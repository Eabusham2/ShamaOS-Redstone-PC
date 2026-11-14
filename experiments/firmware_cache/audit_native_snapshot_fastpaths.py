"""Compare the actual prior AST and time small, checksum-bound native restores.

The timed fixture is a static synthetic quiescent stream made from saved native
states. Actual-engine write/save/reopen behavior is covered separately by the
existing MCHPRS snapshot regression suite. No complete-machine claim is made.
"""
from array import array
import argparse
import ast
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import platform
import random
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests"), str(Path(__file__).resolve().parent)]
from shamaos import mchprs_snapshot as snapshot
from shamaos import worldio
from shamaos.mchprs_import import digest, export_world
from shamaos.model import BlockState, Placement, Vec3
from audit_native_fingerprint import fixture_placements
from test_native_snapshot_fastpaths import GOLDEN_NBT, chunk_case, nbt_bytes

BASELINE = "f7ae2e413df787732dbd976b939bb92330f4c155"
SEED = 0x5EC71026


def baseline_function(module, filename, name):
    relative = "src/shamaos/" + filename
    prior = subprocess.check_output(["git", "show", f"{BASELINE}:{relative}"], cwd=ROOT)
    current = (ROOT / relative).read_bytes()
    trees = [ast.parse(raw) for raw in (prior, current)]
    methods = []
    for tree in trees:
        method = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
        methods.append(method)
        tree.body.remove(method)
    assert ast.dump(trees[0], include_attributes=False) == ast.dump(trees[1], include_attributes=False)
    namespace = vars(module).copy()
    exec(compile(ast.Module(body=[methods[0]], type_ignores=[]), f"historical-{name}-AST", "exec"), namespace)
    return namespace[name], {"baseline_sha256": hashlib.sha256(prior).hexdigest(),
                             "candidate_sha256": hashlib.sha256(current).hexdigest(),
                             "remaining_module_ast_identical": True}


def section_identity(result):
    section, count = result
    return section.palette, section.indices, section.assigned, section.lookup, count


def outcome(method, value, normalize=lambda result: result):
    try:
        return "value", normalize(method(value))
    except Exception as exc:
        return "exception", type(exc).__name__, str(exc)


def differential(prior_section, prior_nbt):
    rng = random.Random(SEED)
    states = [worldio.AIR, BlockState.of("minecraft:stone"), BlockState.of("minecraft:dirt"),
              worldio.canonical_block_state(BlockState.of("minecraft:comparator", powered="true")),
              worldio.canonical_block_state(BlockState.of("minecraft:comparator", powered="false")),
              worldio.canonical_block_state(BlockState.of("minecraft:repeater", powered="true"))]
    uniform = packed = chunks = negatives = 0
    for case in range(128):
        state = rng.choice(states)
        record = {"kind": "uniform_section", "state": {"name": state.name, "properties": dict(state.properties)},
                  "nonair": 0 if state == worldio.AIR else 4096}
        assert section_identity(prior_section(record)) == section_identity(snapshot._section(record))
        uniform += 1
        palette = rng.sample(states, rng.randrange(1, len(states) + 1))
        ids = array("H", (rng.randrange(len(palette)) for _ in range(4096)))
        record = {"kind": "section", "palette": [{"name": s.name, "properties": dict(s.properties)} for s in palette],
                  "bits": 4, "data": [word & ((1 << 64) - 1) for word in worldio._pack_indices(ids, 4)],
                  "nonair": sum(palette[i] != worldio.AIR for i in ids)}
        assert section_identity(prior_section(record)) == section_identity(snapshot._section(record))
        packed += 1
        chunk = worldio._Chunk(rng.randrange(-100, 101), rng.randrange(-100, 101))
        for sy in rng.sample(range(-4, 20), rng.randrange(1, 5)):
            section = worldio._Section(palette=list(states))
            if case % 3:
                for cell in range(0, 4096, 31):
                    section.indices[cell] = rng.randrange(len(states))
            section.assigned[:] = bytes(rng.getrandbits(8) for _ in range(512))
            chunk.sections[sy] = section
        old = nbt_bytes(prior_nbt(chunk))
        new_root = worldio._chunk_nbt(chunk)
        assert nbt_bytes(new_root) == old
        assert worldio._decode_chunk(new_root, chunk.x, chunk.z).fingerprint() == chunk.fingerprint()
        chunks += 1
    for name, expected_hash in GOLDEN_NBT.items():
        assert hashlib.sha256(nbt_bytes(prior_nbt(chunk_case(name)))).hexdigest() == expected_hash
    for state, count in (({"name": "minecraft:air", "properties": {}}, n) for n in (1, -1, 4096, 4097, True, "0")):
        record = {"kind": "uniform_section", "state": state, "nonair": count}
        old = outcome(prior_section, record, section_identity)
        assert old[0] == "exception" and old == outcome(snapshot._section, record, section_identity)
        negatives += 1
    for state in ({"name": "minecraft:command_block", "properties": {}},
                  {"name": "minecraft:comparator", "properties": {}},
                  {"name": "minecraft:comparator", "properties": {"powered": True}},
                  {"name": "minecraft:air", "properties": {"unknown": "true"}},
                  {"name": 1, "properties": {}}, {"name": "minecraft:stone"}):
        record = {"kind": "uniform_section", "state": state, "nonair": 4096}
        old = outcome(prior_section, record, section_identity)
        assert old[0] == "exception" and old == outcome(snapshot._section, record, section_identity)
        negatives += 1
    for index in (1, 65535):
        chunk = worldio._Chunk(0, 0, {0: worldio._Section()})
        chunk.sections[0].indices[4095] = index
        old = outcome(prior_nbt, chunk, nbt_bytes)
        assert old[0] == "exception" and old == outcome(worldio._chunk_nbt, chunk, nbt_bytes)
        negatives += 1
    chunk = worldio._Chunk(0, 0, {0: worldio._Section(palette=[BlockState.of("minecraft:comparator")])})
    old = outcome(prior_nbt, chunk, nbt_bytes)
    assert old[0] == "exception" and old == outcome(worldio._chunk_nbt, chunk, nbt_bytes)
    negatives += 1
    return {"seed": SEED, "uniform_sections": uniform, "packed_sections": packed,
            "random_chunk_nbt_bytes_and_ownership": chunks, "historical_nbt_goldens": len(GOLDEN_NBT),
            "negative_exception_types_and_messages": negatives, "differences": []}


@contextmanager
def using_methods(section, nbt):
    original = snapshot._section, snapshot._chunk_nbt, worldio._chunk_nbt
    snapshot._section, snapshot._chunk_nbt, worldio._chunk_nbt = section, nbt, nbt
    try:
        yield
    finally:
        snapshot._section, snapshot._chunk_nbt, worldio._chunk_nbt = original


def world_identity(path):
    rows = []
    ownership = comparators = 0
    for file in sorted((path / "region").glob("*.mca")):
        rx, rz = map(int, file.name.split(".")[1:3])
        region = worldio._Region(file)
        for index in region.locations():
            root = region.read(index)
            cx, cz = rx * 32 + (index & 31), rz * 32 + (index >> 5)
            chunk = worldio._decode_chunk(root, cx, cz)
            ownership += chunk.fingerprint()[1]
            comparators += len(root["block_entities"])
            rows.append((cx, cz, hashlib.sha256(nbt_bytes(root)).hexdigest()))
    return {"chunks": len(rows), "comparators": comparators, "assigned_cells": ownership,
            "ordered_full_nbt_sha256": hashlib.sha256(json.dumps(sorted(rows), separators=(",", ":")).encode()).hexdigest()}


def benchmark(prior_section, prior_nbt, trials):
    methods = {"baseline": (prior_section, prior_nbt), "candidate": (snapshot._section, worldio._chunk_nbt)}
    samples = {name: [] for name in methods}
    reference = reference_report = None
    with tempfile.TemporaryDirectory(prefix="native-snapshot-fastpaths-") as temporary:
        directory = Path(temporary)
        source = directory / "source-world"
        placements = fixture_placements()
        for cx in range(-4, 4):
            for cz in range(-4, 4):
                for dy, powered in ((0, "true"), (1, "false")):
                    state = worldio.canonical_block_state(BlockState.of("minecraft:comparator", powered=powered))
                    placements.append(Placement(Vec3(cx * 16 + 8, 100 + dy, cz * 16 + 8), state, "snapshot-fixture"))
        with worldio.NativeWorldWriter(source, surface_y=-61, spawn_platform=False, max_cached_chunks=8) as writer:
            writer.set_blocks(placements)
        stream = directory / "source.jsonl"
        manifest = export_world(source, stream)
        records = [json.loads(line) for line in stream.read_text().splitlines()]
        records[0] |= {"kind": "snapshot_header", "schema": "shama-mchprs-quiescent-snapshot-v1",
                       "engine_revision": snapshot.ENGINE_REVISION, "engine_registry_patch_sha256": snapshot.TERRAIN_PATCH,
                       "imported_stream_sha256": digest(stream), "scenario_sha256": "0" * 64, "pending_scheduled_ticks": 0}
        records[-1] |= {"kind": "snapshot_complete", "pending_scheduled_ticks": 0}
        for record in records:
            if record["kind"] == "comparator" and record["output_strength"]:
                record["output_strength"] = 12  # Preserve analog strength through the full native restore.
        saved = directory / "synthetic-quiescent.jsonl"
        saved.write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in records))
        checksum = digest(saved)
        source_files = [source / "level.dat", *sorted((source / "region").glob("*.mca"))]
        source_hashes = {str(path.relative_to(source)): digest(path) for path in source_files}
        for trial in range(trials):
            order = tuple(methods) if trial % 2 == 0 else tuple(reversed(methods))
            for name in order:
                destination = directory / f"restored-{trial}-{name}"
                with using_methods(*methods[name]):
                    started = time.perf_counter()
                    report = snapshot.restore_snapshot(saved, source, destination, expected_sha256=checksum)
                    elapsed = time.perf_counter() - started
                samples[name].append(elapsed)
                actual = world_identity(destination)
                if reference is None:
                    reference, reference_report = actual, report
                assert actual == reference and report == reference_report
                assert report["original_chunk_inventory_preserved"] and report["pending_scheduled_ticks"] == 0
                assert report["complete_machine_runtime_verified"] is False
                assert actual["assigned_cells"] == 0  # Snapshot format has no native ownership field.
                assert actual["comparators"] == 128
                for file in (destination / "region").glob("*.mca"):
                    region = worldio._Region(file)
                    for index in region.locations():
                        assert sorted(int(e["OutputSignal"]) for e in region.read(index)["block_entities"]) == [0, 12]
        assert digest(saved) == checksum
        assert {str(path.relative_to(source)): digest(path) for path in source_files} == source_hashes
        medians = {name: statistics.median(values) for name, values in samples.items()}
        return {"scope": "Complete restoration, native encoding, region write/compact, independent saved readback and atomic publication of a 64-chunk synthetic snapshot; no engine execution or whole-cache timing.",
                "trials_per_method": trials, "order_alternates": True, "os_caches_flushed": False,
                "chunks": manifest["chunks"], "sections": manifest["sections"], "uniform_sections": manifest["uniform_sections"],
                "nonair": manifest["nonair"], "comparators": manifest["comparators"], "stream_bytes": saved.stat().st_size,
                "restore_seconds": samples, "median_restore_seconds": medians,
                "median_speedup": medians["baseline"] / medians["candidate"],
                "all_six_native_outputs_and_reports_identical": trials == 3,
                "canonical_native_output": reference, "source_world_and_snapshot_unchanged": True,
                "analog_strength_preserved": 12}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--trials", type=int, default=3)
    args = parser.parse_args()
    assert args.trials > 0
    prior_section, section_identity_hashes = baseline_function(snapshot, "mchprs_snapshot.py", "_section")
    prior_nbt, nbt_identity_hashes = baseline_function(worldio, "worldio.py", "_chunk_nbt")
    report = {"status": "passed", "baseline_commit": BASELINE,
              "identity": {"mchprs_snapshot.py": section_identity_hashes, "worldio.py": nbt_identity_hashes},
              "environment": {"python": sys.version, "platform": platform.platform(), "nbtlib": worldio.nbtlib.__version__},
              "differential": differential(prior_section, prior_nbt),
              "native_restoration": benchmark(prior_section, prior_nbt, args.trials),
              "whole_cache_or_warm_runtime_verified_by_this_benchmark": False,
              "audit_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    for filename, identity in report["identity"].items():
        assert digest(ROOT / "src/shamaos" / filename) == identity["candidate_sha256"]
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
