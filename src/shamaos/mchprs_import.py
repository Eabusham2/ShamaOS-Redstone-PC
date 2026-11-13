"""Stream every saved native Anvil section to the pinned-engine prototype.

This imports saved circuit/terrain states. Nonempty native tick queues are
explicitly unsupported; they are never silently dropped. The runner schedules
and processes real MCHPRS ticks after initializing the imported saved circuit.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

from .worldio import AIR, NativeWorldReader, WorldWriteError, _Region, _decode_chunk, _pack_indices


_EMPTY_PACKED_SECTION = (0,) * 256


def digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _snapshot(path: Path) -> tuple[tuple[int, ...], str]:
    def signature():
        stat = path.stat()
        return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns
    before = signature()
    checksum = digest(path)
    if signature() != before:
        raise WorldWriteError(f"native source changed while hashing: {path}")
    return before, checksum


def export_world(world: Path, destination: Path, *, compact_sections: bool = True) -> dict:
    world, destination = Path(world), Path(destination)
    region_files = sorted((world / "region").glob("*.mca"))
    if not region_files:
        raise WorldWriteError("no saved Anvil regions; cannot attest an empty import")
    snapshot = {path: _snapshot(path) for path in [world / "level.dat", *region_files]}
    reader = NativeWorldReader(world, max_cached_chunks=1)
    source = {
        "native_world": str(world.resolve()),
        "data_version": 3700,
        "level_dat_sha256": snapshot[world / "level.dat"][1],
        "regions": [{"name": path.name, "bytes": snapshot[path][0][2], "sha256": snapshot[path][1]}
                    for path in region_files],
        "scope": "every saved chunk and section; not an assertion of complete-machine capacity",
        "saved_native_pending_ticks": 0,
    }
    counts = dict(chunks=0, sections=0, nonair=0, comparators=0)
    uniform_sections = 0
    schema = "shama-native-anvil-sections-v2" if compact_sections else "shama-native-anvil-sections-v1"
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".partial")
    with temporary.open("w") as handle:
        def emit(record):
            handle.write(json.dumps(record, separators=(",", ":")) + "\n")

        emit(dict(kind="header", schema=schema,
                  surface_y=reader.surface_y, source=source))
        for path in region_files:
            match = re.fullmatch(r"r\.(-?\d+)\.(-?\d+)\.mca", path.name)
            if match is None:
                raise WorldWriteError(f"invalid region filename: {path.name}")
            rx, rz = map(int, match.groups())
            region = _Region(path)
            for index in region.locations():
                cx, cz = rx * 32 + (index & 31), rz * 32 + (index >> 5)
                root = region.read(index)
                assert root is not None
                if len(root.get("block_ticks", ())) or len(root.get("fluid_ticks", ())):
                    raise WorldWriteError("nonempty native tick queue needs an explicit tick conversion; refusing to discard it")
                chunk = _decode_chunk(root, cx, cz)
                for sy in range(-4, 20):
                    section = chunk.sections.get(sy)
                    uniform = AIR if section is None else None
                    if compact_sections and section is not None and section.indices.count(section.indices[0]) == 4096:
                        uniform = section.palette[section.indices[0]]
                    if compact_sections and uniform is not None:
                        nonair = 0 if uniform == AIR else 4096
                        emit(dict(kind="uniform_section", chunk=[cx, cz], y=sy,
                                  state={"name": uniform.name, "properties": dict(uniform.properties)},
                                  nonair=nonair))
                        uniform_sections += 1
                    else:
                        if section is None:
                            # Preserve the exact v1 byte representation without
                            # allocating/counting/repacking 4,096 air indices.
                            palette, bits, nonair, data = [AIR], 4, 0, _EMPTY_PACKED_SECTION
                        else:
                            palette, indices = section.palette, section.indices
                            bits = max(4, (len(palette) - 1).bit_length())
                            nonair = sum(palette[value] != AIR for value in indices)
                            data = [value & ((1 << 64) - 1) for value in _pack_indices(indices, bits)]
                        emit(dict(kind="section", chunk=[cx, cz], y=sy, bits=bits,
                                  palette=[{"name": state.name, "properties": dict(state.properties)} for state in palette],
                                  data=data, nonair=nonair))
                    counts["sections"] += 1
                    counts["nonair"] += nonair
                for entity in root["block_entities"]:
                    # _decode_chunk independently validates complete comparator
                    # coverage, identity, coordinates and saved output strength.
                    emit(dict(kind="comparator", pos=[int(entity[key]) for key in ("x", "y", "z")],
                              output_strength=int(entity["OutputSignal"])))
                    counts["comparators"] += 1
                counts["chunks"] += 1
        # A successful completion marker attests one stable source snapshot.
        # Replaced/modified regions, changed level settings, or a changed chunk
        # file inventory leave an explicitly incomplete .partial stream.
        if sorted((world / "region").glob("*.mca")) != region_files:
            raise WorldWriteError("native source region inventory changed during export")
        try:
            for path, original in snapshot.items():
                if _snapshot(path) != original:
                    raise WorldWriteError(f"native source changed during export: {path}")
        except OSError as exc:
            raise WorldWriteError(f"native source changed during export: {exc}") from exc
        emit(dict(kind="complete", **counts))
    temporary.replace(destination)
    return {**counts, "stream_bytes": destination.stat().st_size,
            "stream_sha256": digest(destination), "source": source,
            "schema": schema, "uniform_sections": uniform_sections}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("world", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--legacy-sections", action="store_true", help="write v1 packed arrays, with identical empty-section bytes")
    args = parser.parse_args()
    print(json.dumps(export_world(args.world, args.destination, compact_sections=not args.legacy_sections), indent=2))
