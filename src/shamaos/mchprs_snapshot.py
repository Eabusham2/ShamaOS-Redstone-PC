"""Convert an explicitly quiescent actual-engine snapshot into a fresh native save.

This experimental path preserves every section and actual comparator strength.
It never serializes a nonempty engine tick queue or overwrites the source world.
"""
from __future__ import annotations

from array import array
from contextlib import ExitStack
import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

import nbtlib

from .mchprs_import import digest
from .model import BlockState, Vec3
from .worldio import (
    AIR, JAVA_DATA_VERSION, NativeWorldReader, WorldWriteError, _Chunk, _Region,
    _Section, _chunk_nbt, _decode_chunk, _unpack_indices, canonical_block_state,
    validate_position,
)


ENGINE_REVISION = "f9e10d2572116bf5dcb9a36a305f28e40641a61a"
TERRAIN_PATCH = "8ebd0e097d07e93a6b2b158e08ad19c2660b0b7e2521c6b6d34a4583a10bfbdc"


def _integer(value, name, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise WorldWriteError(f"snapshot {name} must be an integer in {minimum}..{maximum}")
    return value


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise WorldWriteError(f"snapshot JSON repeats field {key}")
        result[key] = value
    return result


def _state(value):
    if not isinstance(value, dict) or set(value) != {"name", "properties"}:
        raise WorldWriteError("snapshot state requires exact name/properties fields")
    name, properties = value["name"], value["properties"]
    if not isinstance(name, str) or not isinstance(properties, dict):
        raise WorldWriteError("snapshot block identity/properties have invalid types")
    if any(not isinstance(k, str) or not isinstance(v, str) for k, v in properties.items()):
        raise WorldWriteError("snapshot block properties must be strings")
    state = BlockState.of(name, **properties)
    if canonical_block_state(state) != state:
        raise WorldWriteError("snapshot block is not an exact supported native state")
    return state


def _section(record):
    if record["kind"] == "uniform_section":
        palette = [_state(record["state"])]
        indices = array("H", [0]) * 4096
        nonair = 0 if palette[0] == AIR else 4096
    else:
        palette = list(map(_state, record["palette"]))
        if not 1 <= len(palette) <= 4096 or len(set(palette)) != len(palette):
            raise WorldWriteError("snapshot has an empty, duplicate or oversized palette")
        bits = _integer(record["bits"], "bits", 4, 12)
        if bits != max(4, (len(palette) - 1).bit_length()):
            raise WorldWriteError("snapshot palette width does not match its palette")
        data = [_integer(word, "packed word", 0, (1 << 64) - 1) for word in record["data"]]
        indices = _unpack_indices(data, 4096, bits, len(palette))
        nonair = sum(palette[index] != AIR for index in indices)
    if _integer(record["nonair"], "nonair", 0, 4096) != nonair:
        raise WorldWriteError("snapshot non-air count does not match actual section states")
    return _Section(palette=palette, indices=indices, lookup={state: i for i, state in enumerate(palette)}), nonair


def _entity_records(root):
    return sorted(([int(entity[k]) for k in ("x", "y", "z")] + [int(entity["OutputSignal"])])
                  for entity in root["block_entities"])


def _entity_fingerprint(root):
    # Keep only a digest after each chunk is written. A large machine must not
    # retain every comparator coordinate/strength from all chunks at once.
    return hashlib.sha256(json.dumps(_entity_records(root), separators=(",", ":")).encode()).hexdigest()


def _cleanup_owned_directory(path: Path, identity: tuple[int, int]) -> None:
    try:
        current = path.lstat()
    except FileNotFoundError:
        return
    if (current.st_dev, current.st_ino) == identity:
        shutil.rmtree(path)


def _publish_no_replace(source: Path, destination: Path) -> None:
    """Atomically publish a directory without ever replacing another entry.

    Linux renameat2(RENAME_NOREPLACE) is the actual publication gate. There is
    deliberately no exists()+rename fallback when the OS/filesystem lacks it.
    """
    if not sys.platform.startswith("linux"):
        raise WorldWriteError("atomic native snapshot publication requires Linux renameat2(RENAME_NOREPLACE)")
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, "renameat2", None)
    if rename is None:
        raise WorldWriteError("atomic no-replace rename is unavailable; refusing unsafe publication")
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    # Linux UAPI: AT_FDCWD=-100; RENAME_NOREPLACE=(1 << 0).
    if rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1) != 0:
        error = ctypes.get_errno()
        if error in (errno.EEXIST, errno.ENOTEMPTY):
            raise WorldWriteError("snapshot destination appeared during restore; refusing to replace it")
        if error in (errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP):
            raise WorldWriteError("filesystem does not support atomic no-replace publication")
        raise OSError(error, os.strerror(error), str(destination))


def _source_chunk_inventory(region_files: list[Path]) -> set[tuple[int, int]]:
    chunks = set()
    for path in region_files:
        match = re.fullmatch(r"r\.(-?\d+)\.(-?\d+)\.mca", path.name)
        if match is None:
            raise WorldWriteError(f"invalid snapshot source region filename: {path.name}")
        rx, rz = map(int, match.groups())
        for index in _Region(path).locations():
            chunks.add((rx * 32 + (index & 31), rz * 32 + (index >> 5)))
    if not chunks:
        raise WorldWriteError("snapshot source has no saved native chunks")
    return chunks


def _restore_snapshot(snapshot: Path, source_world: Path, destination: Path, *, expected_sha256: str,
                      cleanup: ExitStack) -> dict:
    snapshot, source_world, destination = Path(snapshot), Path(source_world), Path(destination)
    if os.path.lexists(destination):
        raise WorldWriteError("snapshot restore needs a fresh destination outside the source world")
    source_world, destination = source_world.resolve(), destination.resolve()
    if destination.exists() or destination.is_relative_to(source_world):
        raise WorldWriteError("snapshot restore needs a fresh destination outside the source world")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise WorldWriteError("snapshot restore requires the actual runner's SHA-256 digest")
    stream_hash = hashlib.sha256()
    original_stat = snapshot.stat()
    counts = dict(chunks=0, sections=0, nonair=0, comparators=0)
    completed = False
    current = None
    section_ids = set()
    entities = {}
    expected_chunks = {}
    regions = {}
    temporary = None
    header = None
    source_chunks = set()

    def flush_chunk():
        nonlocal current, entities, section_ids
        if current is None:
            return
        if section_ids != set(range(-4, 20)):
            raise WorldWriteError("snapshot chunk is missing a native height section")
        root = _chunk_nbt(current)
        required = {(int(e["x"]), int(e["y"]), int(e["z"])) for e in root["block_entities"]}
        if required != set(entities):
            raise WorldWriteError("snapshot comparator entities do not exactly cover comparator blocks")
        root["block_entities"] = nbtlib.List[nbtlib.Compound]([
            nbtlib.Compound({"id": nbtlib.String("minecraft:comparator"), "x": nbtlib.Int(pos[0]),
                "y": nbtlib.Int(pos[1]), "z": nbtlib.Int(pos[2]), "OutputSignal": nbtlib.Int(strength),
                "keepPacked": nbtlib.Byte(0)})
            for pos, strength in sorted(entities.items())
        ])
        decoded = _decode_chunk(root, current.x, current.z)
        if decoded.fingerprint() != current.fingerprint():
            raise WorldWriteError("snapshot-to-NBT conversion changed actual section states")
        key = (current.x, current.z)
        if key in expected_chunks:
            raise WorldWriteError("snapshot repeats a native chunk")
        expected_chunks[key] = (current.fingerprint(), _entity_fingerprint(root))
        region_key = (current.x // 32, current.z // 32)
        if region_key not in regions:
            regions[region_key] = _Region(temporary / "region" / f"r.{region_key[0]}.{region_key[1]}.mca")
        regions[region_key].write((current.x & 31) + (current.z & 31) * 32, root)
        counts["chunks"] += 1
        current, entities, section_ids = None, {}, set()

    with snapshot.open("rb") as handle:
        while raw := handle.readline(16 << 20):
            if not raw.endswith(b"\n"):
                raise WorldWriteError("snapshot record is oversized or truncated")
            stream_hash.update(raw)
            try:
                record = json.loads(raw, object_pairs_hook=_unique_object)
            except (ValueError, UnicodeError) as error:
                raise WorldWriteError(f"invalid snapshot JSON: {error}") from error
            if not isinstance(record, dict):
                raise WorldWriteError("snapshot records must be objects")
            kind = record.get("kind")
            if completed:
                raise WorldWriteError("snapshot has records after completion")
            if header is None:
                if kind != "snapshot_header" or record.get("schema") != "shama-mchprs-quiescent-snapshot-v1":
                    raise WorldWriteError("unsupported or missing snapshot header")
                if record.get("engine_revision") != ENGINE_REVISION or record.get("engine_registry_patch_sha256") not in (None, TERRAIN_PATCH):
                    raise WorldWriteError("snapshot engine provenance is not supported")
                for field in ("imported_stream_sha256", "scenario_sha256"):
                    if not isinstance(record.get(field), str) or not re.fullmatch(r"[0-9a-f]{64}", record[field]):
                        raise WorldWriteError(f"snapshot has invalid {field} provenance")
                if _integer(record.get("pending_scheduled_ticks"), "pending ticks", 0, 0) != 0:
                    raise WorldWriteError("snapshot has pending scheduled ticks")
                source = record["source"]
                if source["data_version"] != JAVA_DATA_VERSION:
                    raise WorldWriteError("snapshot source data version differs from native output")
                reader = NativeWorldReader(source_world, max_cached_chunks=1)
                if reader.surface_y is not None and record.get("engine_registry_patch_sha256") != TERRAIN_PATCH:
                    raise WorldWriteError("native terrain snapshot requires the exact terrain registry provenance")
                if record["surface_y"] != reader.surface_y or digest(source_world / "level.dat") != source["level_dat_sha256"]:
                    raise WorldWriteError("snapshot source level or terrain configuration changed")
                source_regions = sorted((source_world / "region").glob("*.mca"))
                actual_regions = [{"name": p.name, "bytes": p.stat().st_size, "sha256": digest(p)}
                                  for p in source_regions]
                if actual_regions != source["regions"]:
                    raise WorldWriteError("snapshot source region inventory or content changed")
                source_chunks = _source_chunk_inventory(source_regions)
                destination.parent.mkdir(parents=True, exist_ok=True)
                temporary = Path(tempfile.mkdtemp(prefix=destination.name + ".partial-", dir=destination.parent))
                identity = temporary.stat()
                cleanup.callback(_cleanup_owned_directory, temporary, (identity.st_dev, identity.st_ino))
                (temporary / "region").mkdir()
                shutil.copyfile(source_world / "level.dat", temporary / "level.dat")
                if digest(temporary / "level.dat") != source["level_dat_sha256"]:
                    raise WorldWriteError("snapshot source level changed during restore")
                header = record
            elif kind in ("section", "uniform_section"):
                coords = record["chunk"]
                if not isinstance(coords, list) or len(coords) != 2:
                    raise WorldWriteError("snapshot chunk requires X/Z coordinates")
                cx, cz = (_integer(v, "chunk coordinate", -1_874_999, 1_874_998) for v in coords)
                validate_position(Vec3(cx * 16, -64, cz * 16))
                validate_position(Vec3(cx * 16 + 15, 319, cz * 16 + 15))
                if current is None or (current.x, current.z) != (cx, cz):
                    flush_chunk()
                    current = _Chunk(cx, cz)
                sy = _integer(record["y"], "section Y", -4, 19)
                if sy in section_ids or entities:
                    raise WorldWriteError("snapshot repeats or reorders native sections")
                section_ids.add(sy)
                section, nonair = _section(record)
                current.sections[sy] = section
                counts["sections"] += 1
                counts["nonair"] += nonair
            elif kind == "comparator":
                if current is None or section_ids != set(range(-4, 20)):
                    raise WorldWriteError("snapshot comparator precedes its complete native chunk")
                raw_pos = record["pos"]
                if not isinstance(raw_pos, list) or len(raw_pos) != 3:
                    raise WorldWriteError("snapshot comparator needs three coordinates")
                position = tuple(_integer(v, "comparator coordinate", -29_999_984, 29_999_983) for v in raw_pos)
                validate_position(Vec3(*position))
                if (position[0] // 16, position[2] // 16) != (current.x, current.z) or position in entities:
                    raise WorldWriteError("snapshot comparator has duplicate or out-of-chunk coordinates")
                entities[position] = _integer(record["output_strength"], "comparator strength", 0, 15)
                counts["comparators"] += 1
            elif kind == "snapshot_complete":
                flush_chunk()
                if not counts["chunks"] or any(_integer(record.get(key), key, 0, (1 << 64) - 1) != value for key, value in counts.items()):
                    raise WorldWriteError("snapshot completion counts disagree with decoded states")
                if _integer(record.get("pending_scheduled_ticks"), "pending ticks", 0, 0) != 0:
                    raise WorldWriteError("snapshot has pending scheduled ticks")
                completed = True
            else:
                raise WorldWriteError(f"unsupported snapshot record {kind}")
    if not completed:
        raise WorldWriteError("truncated snapshot: missing completion marker")
    missing = source_chunks.difference(expected_chunks)
    if missing:
        raise WorldWriteError(f"snapshot omits {len(missing)} original saved native chunks")
    if stream_hash.hexdigest() != expected_sha256:
        raise WorldWriteError("snapshot bytes differ from the actual runner's reported SHA-256")
    after = snapshot.stat()
    if (original_stat.st_dev, original_stat.st_ino, original_stat.st_size, original_stat.st_mtime_ns, original_stat.st_ctime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise WorldWriteError("snapshot source changed during restore")
    for region in regions.values():
        region.compact()
    for (cx, cz), (fingerprint, comparator_states_sha256) in expected_chunks.items():
        reopened = _Region(temporary / "region" / f"r.{cx // 32}.{cz // 32}.mca")
        root = reopened.read((cx & 31) + (cz & 31) * 32)
        if root is None or _decode_chunk(root, cx, cz).fingerprint() != fingerprint or _entity_fingerprint(root) != comparator_states_sha256:
            raise WorldWriteError("independent native snapshot readback changed a block or comparator entity")
        if root["block_ticks"] or root["fluid_ticks"]:
            raise WorldWriteError("quiescent restored world unexpectedly has pending native ticks")
    report = {"status": "quiescent-snapshot-native-readback-passed", **counts,
              "snapshot_sha256": expected_sha256, "engine_revision": header["engine_revision"],
              "engine_registry_patch_sha256": header["engine_registry_patch_sha256"],
              "parent_imported_stream_sha256": header["imported_stream_sha256"],
              "parent_scenario_sha256": header["scenario_sha256"],
              "original_source_chunks": len(source_chunks),
              "additional_runtime_chunks": len(expected_chunks) - len(source_chunks),
              "original_chunk_inventory_preserved": True,
              "original_chunk_inventory_sha256": hashlib.sha256(json.dumps(sorted(source_chunks), separators=(",", ":")).encode()).hexdigest(),
              "pending_scheduled_ticks": 0, "complete_machine_runtime_verified": False}
    (temporary / "shamaos").mkdir()
    (temporary / "shamaos/quiescent-snapshot.json").write_text(json.dumps(report, indent=2) + "\n")
    _publish_no_replace(temporary, destination)
    return report


def restore_snapshot(snapshot: Path, source_world: Path, destination: Path, *, expected_sha256: str) -> dict:
    """Publish only a checksum-bound, independently read-back native save."""
    try:
        with ExitStack() as cleanup:
            return _restore_snapshot(snapshot, source_world, destination, expected_sha256=expected_sha256, cleanup=cleanup)
    except WorldWriteError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError, IndexError) as error:
        raise WorldWriteError(f"malformed quiescent snapshot: {error}") from error


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--source-world", type=Path, required=True, help="unchanged native world originally imported by the runner")
    parser.add_argument("--output", type=Path, required=True, help="fresh directory for the reopened native save")
    parser.add_argument("--sha256", required=True, help="snapshot digest reported by the actual runner")
    args = parser.parse_args()
    print(json.dumps(restore_snapshot(args.snapshot, args.source_world, args.output, expected_sha256=args.sha256), indent=2))
