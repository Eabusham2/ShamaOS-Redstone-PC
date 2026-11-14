"""Recover a stopped connected-world write from its exact committed union.

The old attempt is read-only. Saved chunks are reused in a new destination only
after comparison with complete expected chunks reconstructed from the accepted
union. The unchanged writer appends missing chunks, then independent native
readback and every host/output pad check run before runtime becomes eligible.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
from itertools import groupby
import json
import os
from pathlib import Path
import re
import shutil
import signal
import sqlite3
import time

try:
    import resource
except ImportError:
    resource = None

from experiments.memory_controller_runtime import current_fixture, interconnect
from experiments.memory_controller_runtime.save_connected import (
    ROOT, _native_maps, _sha, _validate_native_driver_pads, _write,
)
from experiments.partition_placement.port_egress_manifest import read_egress_manifest
from shamaos import bounded_build, release_io
from shamaos.model import BlockState, Placement, Vec3
from shamaos.worldio import (
    NativeWorldReader, NativeWorldWriter, WorldWriteError, _Region, _Section, _new_chunk,
    canonical_block_state, validate_native_world,
)


def _rows(connection):
    return connection.execute("SELECT position,state FROM blocks ORDER BY position")


def _disjoint_paths(source, destination):
    source, destination = source.resolve(), destination.resolve()
    if source.is_relative_to(destination) or destination.is_relative_to(source):
        raise ValueError("recovery source and destination must be disjoint directories")
    return source, destination


def _bound_preparation(report):
    """Bind live preparation and every regenerated external net to acceptance."""
    preparation_path = Path(report["current_preparation"]["path"])
    if _sha(preparation_path) != report["current_preparation"]["sha256"]:
        raise ValueError("original accepted current preparation changed")
    directory = preparation_path.parent
    if _sha(directory / "plan.json") != report["plan_sha256"]:
        raise ValueError("original accepted complete plan changed")
    prepared, graph, _, fabric, origin = current_fixture.load_prepared(directory)
    identity = lambda value: {name: item for name, item in value.items() if name != "path"}
    if identity(graph.source) != identity(report["mapped_source"]):
        raise ValueError("current mapped graph differs from original connected acceptance")
    if _sha(directory / "initial.bin") != report["physical_memory"]["initial_sha256"]:
        raise ValueError("original complete physical storage image changed")
    manifest = Path(report["facade_manifest"]["path"])
    if not manifest.is_absolute():
        manifest = ROOT / manifest
    plan = json.loads((directory / "plan.json").read_text())
    placed = read_egress_manifest(manifest, report["facade_manifest"]["sha256"], graph, plan)
    connection = interconnect.connect_cache(graph, placed, fabric, origin, capacity_bytes=prepared["capacity_bytes"])
    nets = (json.dumps([asdict(net) for net in connection.nets], indent=2, sort_keys=True) + "\n").encode()
    if hashlib.sha256(nets).hexdigest() != report["external_nets_sha256"]:
        raise ValueError("regenerated complete external nets differ from original acceptance")
    memory = report["physical_memory"]
    if (asdict(connection.bank_origin) != memory["bank_origin"]
            or (prepared["capacity_bytes"], fabric.bank_count, fabric.bank.words, fabric.bank.word_bits)
            != (memory["capacity_bytes"], memory["banks"], memory["rows_per_bank"], memory["word_bits"])):
        raise ValueError("regenerated physical bank layout differs from original acceptance")
    return prepared, graph, fabric, placed, connection


def verify_union(connection, record):
    """Recompute exact records, palette validity, count, bounds and chunks."""
    if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise ValueError("retained union database is damaged")
    palette = [BlockState(value["name"], tuple(tuple(pair) for pair in value["properties"]))
               for value in record["palette"]]
    try:
        if not palette or any(canonical_block_state(state) != state for state in palette):
            raise ValueError("retained union palette is noncanonical")
    except WorldWriteError as error:
        raise ValueError("retained union palette is unsupported") from error
    digest = hashlib.sha256()
    count = chunks = 0
    previous_chunk = None
    low = high = None
    for key, index in _rows(connection):
        if type(key) is not int or type(index) is not int or not 0 <= index < len(palette):
            raise ValueError("retained union has an invalid record")
        point = bounded_build._coordinate(key)
        if bounded_build._coordinate_key(point) != key or not -60 <= point.y <= 319:
            raise ValueError("retained union leaves its above-grass coordinate envelope")
        digest.update(bounded_build._RECORD.pack(key, index))
        xyz = (point.x, point.y, point.z)
        low = xyz if low is None else tuple(min(a, b) for a, b in zip(low, xyz))
        high = xyz if high is None else tuple(max(a, b) for a, b in zip(high, xyz))
        count += 1
        if key >> 17 != previous_chunk:
            chunks += 1
            previous_chunk = key >> 17
    bounds = {"min": dict(zip("xyz", low or ())), "max": dict(zip("xyz", high or ()))}
    actual = dict(unique_blocks=count, occupied_chunks=chunks, bounds=bounds,
                  sorted_unique_record_sha256=digest.hexdigest())
    if any(record.get(key) != value for key, value in actual.items()):
        raise ValueError("retained union differs from its complete accepted records")
    return palette, actual


def recover_chunks(world, destination, connection, palette, progress=None):
    """Copy a stopped owned save and rebuild its ledger from independent inputs."""
    world, destination = _disjoint_paths(world, destination)
    if destination.exists():
        raise FileExistsError(destination)
    if not (world / "shamaos/world.json").is_file():
        raise ValueError("recovery requires an owned native world")
    with (world / "session.lock").open("r+b") as source_lock:
        try:
            source_lock.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(source_lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.lockf(source_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB, 0, 0, os.SEEK_SET)
        except OSError as error:
            raise ValueError("the original writer still owns this world") from error
        saved = set()
        for path in (world / "region").glob("*.mca"):
            match = re.fullmatch(r"r\.(-?\d+)\.(-?\d+)\.mca", path.name)
            if match is None:
                raise ValueError("invalid original region filename")
            rx, rz = map(int, match.groups())
            for index in _Region(path).locations():
                point = Vec3((rx * 32 + (index & 31)) * 16, -60,
                             (rz * 32 + (index >> 5)) * 16)
                saved.add(bounded_build._coordinate_key(point) >> 17)
        shutil.copytree(world, destination, ignore=shutil.ignore_patterns(
            "expected.sqlite3*", "session.lock", "readback.json", "fixture.json", "runtime_case.json"))
        reader = NativeWorldReader(destination, max_cached_chunks=1)
        if reader.surface_y != -61:
            raise ValueError("recovery must preserve the exact superflat grass level")
        ledger = sqlite3.connect(destination / "shamaos/expected.sqlite3")
        ledger.execute("CREATE TABLE chunks (x INTEGER NOT NULL,z INTEGER NOT NULL,digest BLOB NOT NULL,blocks INTEGER NOT NULL,PRIMARY KEY(x,z)) WITHOUT ROWID")
        verified = set()
        blocks = 0
        try:
            for chunk_key, rows in groupby(_rows(connection), key=lambda row: row[0] >> 17):
                if chunk_key not in saved:
                    continue
                point = bounded_build._coordinate(chunk_key << 17)
                expected = _new_chunk(point.x // 16, point.z // 16, reader.surface_y)
                for key, index in rows:
                    position = bounded_build._coordinate(key)
                    section = expected.sections.get(position.y // 16)
                    if section is None:
                        section = _Section()
                        expected.sections[position.y // 16] = section
                    local = ((position.y & 15) << 8) | ((position.z & 15) << 4) | (position.x & 15)
                    section.put(local, palette[index], position, "accepted-complete-union")
                fingerprint = expected.fingerprint()
                actual = reader.chunk(expected.x, expected.z).fingerprint()
                if actual != fingerprint:
                    raise ValueError(f"saved chunk differs from the complete union: {expected.x},{expected.z}")
                ledger.execute("INSERT INTO chunks VALUES (?,?,?,?)", (
                    expected.x, expected.z, bytes.fromhex(fingerprint[0]), fingerprint[1]))
                verified.add(chunk_key)
                blocks += fingerprint[1]
                if progress and len(verified) % 10000 == 0:
                    progress(dict(verified_reused_chunks=len(verified), verified_reused_blocks=blocks))
            if saved != verified:
                raise ValueError("saved world contains chunks outside the accepted complete union")
            ledger.commit()
        finally:
            ledger.close()
    return verified, {"verified_reused_chunks": len(verified), "verified_reused_blocks": blocks,
                      "source_world_unchanged": True, "comparison": "complete expected and decoded native chunk fingerprints"}


def resume(build: Path, destination: Path):
    if resource is None:
        raise RuntimeError("complete connected recovery requires POSIX process accounting")
    build, destination = _disjoint_paths(build, destination)
    if destination.exists():
        raise FileExistsError(destination)
    original_path = build / "connected-build-report.json"
    original_bytes = original_path.read_bytes()
    report = json.loads(original_bytes)
    if (report.get("independent_union_agreement") is not True
            or report.get("stage") not in ("write-complete-connected-native-world", "independent-connected-native-readback")
            or report.get("status") not in ("running", "failed")):
        raise ValueError("recovery requires a stopped fully union-verified write/readback")
    bundle = Path(report["source_input_bundle"]["path"])
    expected_sources = report["source_hashes"]

    def frozen():
        if original_path.read_bytes() != original_bytes or _sha(bundle) != report["source_input_bundle"]["sha256"]:
            raise ValueError("original connected report or retained source bundle changed")
        if expected_sources != {name: _sha(ROOT / name) for name in expected_sources}:
            raise ValueError("connected generator sources changed before or during recovery")
        _bound_preparation(report)

    frozen()
    prepared, graph, fabric, placed, connection = _bound_preparation(report)
    database = build / "connected-union.sqlite3"
    union = sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)
    started = time.monotonic()
    destination.mkdir(parents=True)
    (destination / "recovery-origin-report.json").write_bytes(original_bytes)
    shutil.copyfile(bundle, destination / "connected-source-inputs.zip")
    shutil.copyfile(__file__, destination / "recovery-source.py")
    report["source_input_bundle"] = {**report["source_input_bundle"], "path": str((destination / "connected-source-inputs.zip").resolve())}
    report.update(status="running", stage="revalidate-retained-complete-union", recovery={
        "original_build": str(build.resolve()), "original_report_sha256": hashlib.sha256(original_bytes).hexdigest(),
        "helper_source_sha256": _sha(Path(__file__)), "union_database_sha256": _sha(database),
        "source_world_unchanged": True})
    report.pop("error", None)
    report.pop("error_type", None)

    def save(update=None):
        if update:
            report["recovery"].update(update)
        report.update(seconds=time.monotonic() - started, peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        _write(destination / "connected-build-report.json", report)
        print(json.dumps({key: report[key] for key in ("status", "stage", "seconds", "peak_rss_kib", "recovery")}), flush=True)

    try:
        save()
        palette, checked = verify_union(union, report["sqlite_union"])
        if report["independent_sort_union"]["palette"] != report["sqlite_union"]["palette"]:
            raise ValueError("the independent sort union has a different block palette")
        if any(report["independent_sort_union"].get(key) != value for key, value in checked.items()
               if key != "unique_blocks"):
            raise ValueError("the independent sort union disagrees with recovery records")
        if report["independent_sort_union"]["literal_unique_blocks"] != checked["unique_blocks"]:
            raise ValueError("the independent sort union lost complete blocks")
        report["recovery"]["revalidated_union"] = checked
        report["stage"] = "validate-and-recover-saved-chunks"
        save()
        reused, recovered = recover_chunks(build / "world", destination / "world", union, palette, save)
        report["recovery"].update(recovered)
        report["stage"] = "append-missing-connected-native-chunks"
        save()
        count = 0
        def placements():
            nonlocal count
            for key, index in _rows(union):
                if key >> 17 in reused:
                    continue
                yield Placement(bounded_build._coordinate(key), palette[index], "complete-bounded-partition")
                count += 1
                if count % 1000000 == 0:
                    save({"new_blocks_written": count})
        with NativeWorldWriter(destination / "world", spawn_platform=False, surface_y=-61,
                               max_cached_chunks=2) as writer:
            writer.set_blocks(placements())
        if count + recovered["verified_reused_blocks"] != checked["unique_blocks"]:
            raise ValueError("reused and appended blocks do not cover the complete union")
        report["recovery"]["new_blocks_written"] = count
        report["stage"] = "independent-connected-native-readback"
        save()
        readback = validate_native_world(destination / "world")
        if (readback["assigned_blocks"], readback["chunks"], readback["regions"]) != (
                checked["unique_blocks"], checked["occupied_chunks"], report["sqlite_union"]["occupied_regions"]):
            raise ValueError("recovered native world differs from the complete accepted union")
        inputs, outputs = _native_maps(graph, placed, connection, fabric, capacity_bytes=prepared["capacity_bytes"])
        reader = NativeWorldReader(destination / "world")
        report["native_host_pad_validation"] = _validate_native_driver_pads(reader, inputs)
        if any(reader.get_block(Vec3(*point)).name == "minecraft:air" for point in outputs.values()):
            raise ValueError("a physical output is absent from the recovered native world")
        for name, value in (("native-inputs.json", inputs), ("native-outputs.json", outputs)):
            _write(destination / name, value)
        report["native_input_map_sha256"] = _sha(destination / "native-inputs.json")
        report["native_output_map_sha256"] = _sha(destination / "native-outputs.json")
        report["native_readback"] = readback
        inventory = release_io.file_inventory(destination / "world")
        _write(destination / "connected-world-inventory.json", inventory)
        report["world_inventory_sha256"] = release_io.inventory_digest(inventory)
        frozen()
        if _sha(database) != report["recovery"]["union_database_sha256"] or _sha(Path(__file__)) != report["recovery"]["helper_source_sha256"]:
            raise ValueError("recovery union or helper changed during execution")
        report.update(status="complete-connected-native-cache-generated-readback-verified", stage="native-runtime-pending")
        save()
        return report
    except Exception as error:
        report.update(status="failed", error_type=type(error).__name__, error=str(error))
        save()
        raise
    finally:
        union.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("build", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--memory-limit-mib", type=int, default=2048)
    parser.add_argument("--wall-seconds", type=int, default=5400)
    args = parser.parse_args()
    if resource is None or args.memory_limit_mib < 512 or args.wall_seconds < 1:
        parser.error("explicit positive bounds are required")
    resource.setrlimit(resource.RLIMIT_AS, (args.memory_limit_mib << 20,) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (args.wall_seconds,) * 2)
    def timeout(*unused):
        raise TimeoutError("connected native recovery exceeded its explicit wall limit")
    signal.signal(signal.SIGALRM, timeout)
    signal.alarm(args.wall_seconds)
    resume(args.build, args.output)
