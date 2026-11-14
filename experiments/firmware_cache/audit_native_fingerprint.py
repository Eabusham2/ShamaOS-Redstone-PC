"""Independently compare the real historical digest and native world throughput.

This loads the candidate source once, compiles the actual historical fingerprint
AST, and changes only that method in an isolated module. It never patches the
production module or any existing world. Timing covers a small explicit fixture;
it cannot establish whole-cache generation or runtime performance.
"""
from __future__ import annotations

import argparse
from array import array
import ast
from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import random
import sqlite3
import statistics
import struct
import subprocess
import sys
import tempfile
import time
import types

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from shamaos.model import BlockState, Placement, Vec3

BASELINE = "184faa9d28d871b77ff4de73c022b620d57536a7"
SEED = 0xCA4E2026


def frozen_methods(candidate_path):
    candidate_raw = candidate_path.read_bytes()
    baseline_raw = subprocess.check_output(
        ["git", "show", f"{BASELINE}:src/shamaos/worldio.py"], cwd=ROOT)
    trees = [ast.parse(raw) for raw in (baseline_raw, candidate_raw)]
    methods = []
    for tree in trees:
        chunk = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "_Chunk")
        method = next(n for n in chunk.body if isinstance(n, ast.FunctionDef) and n.name == "fingerprint")
        methods.append(method)
        chunk.body.remove(method)
    assert ast.dump(trees[0], include_attributes=False) == ast.dump(trees[1], include_attributes=False), \
        "candidate changed behavior outside fingerprint"
    name = "shamaos._independent_fingerprint_audit"
    module = types.ModuleType(name)
    module.__file__ = str(candidate_path)
    module.__package__ = "shamaos"
    sys.modules[name] = module
    exec(compile(candidate_raw, str(candidate_path), "exec"), vars(module))
    namespace = vars(module).copy()
    exec(compile(ast.Module(body=[methods[0]], type_ignores=[]), "historical-fingerprint-AST", "exec"), namespace)
    return module, namespace["fingerprint"], module._Chunk.fingerprint, {
        "baseline_commit": BASELINE,
        "baseline_worldio_sha256": hashlib.sha256(baseline_raw).hexdigest(),
        "candidate_worldio_sha256": hashlib.sha256(candidate_raw).hexdigest(),
        "entire_remaining_module_ast_identical": True,
    }


def block_states():
    return [BlockState.of(name) for name in ("minecraft:air", "minecraft:stone", "minecraft:dirt")] + [
        BlockState.of("minecraft:repeater", facing="east", delay="3", locked="false", powered="false"),
        BlockState.of("minecraft:redstone_lamp", lit="true"),
        BlockState.of("minecraft:redstone_lamp", lit="false"),
        BlockState.of("minecraft:redstone_wire", east="none", north="none", south="none", west="none", power="7"),
        BlockState.of("minecraft:grass_block", snowy="false"),
    ]


def randomized_review(module, baseline, candidate, cases):
    rng = random.Random(SEED)
    states = block_states()
    matched = Counter()
    for case in range(cases):
        chunk = module._Chunk(rng.randrange(-(1 << 31), 1 << 31), rng.randrange(-(1 << 31), 1 << 31))
        for sy in rng.sample(range(-4, 20), rng.randrange(1, 7)):
            palette = [rng.choice(states) for _ in range(rng.randrange(1, 25))]
            palette += [module.AIR, states[1]]
            air_id = palette.index(module.AIR)
            mode = case % 4
            ids = ([air_id] * 4096 if mode == 0 else
                   [rng.randrange(len(palette)) for _ in range(4096)] if mode == 1 else
                   [rng.randrange(len(palette)) if index % (31 if mode == 2 else 7) == 0 else air_id
                    for index in range(4096)])
            ownership = bytearray(512)
            if case % 5 == 1:
                ownership[:] = b"\xff" * 512
            elif case % 5 > 1:
                for _ in range(rng.randrange(1, 512)):
                    cell = rng.randrange(4096)
                    ownership[cell >> 3] |= 1 << (cell & 7)
            chunk.sections[sy] = module._Section(palette, array("H", ids), ownership,
                                                  {state: i for i, state in enumerate(palette)})
        expected = baseline(chunk)
        assert candidate(chunk) == expected
        matched["original"] += 1
        permuted = deepcopy(chunk)
        for section in permuted.sections.values():
            old = section.palette
            section.palette = list(reversed(old)) + [rng.choice(old), states[2]]
            aliases = {state: [i for i, entry in enumerate(section.palette) if entry == state]
                       for state in set(old)}
            section.indices = array("H", (rng.choice(aliases[old[i]]) for i in section.indices))
        assert baseline(permuted) == candidate(permuted) == expected
        matched["palette_permutation_duplicates_unused"] += 1
        for change in ("state", "ownership", "position"):
            changed = deepcopy(chunk)
            section = changed.sections[min(changed.sections)]
            if change == "position":
                changed.x = changed.x + 1 if changed.x < (1 << 31) - 1 else changed.x - 1
            elif change == "ownership":
                section.assigned[0] ^= 1
            else:
                old = section.palette[section.indices[0]]
                section.palette.append(states[1] if old == module.AIR else module.AIR)
                section.indices[0] = len(section.palette) - 1
            altered = baseline(changed)
            assert candidate(changed) == altered and altered[0] != expected[0]
            matched[change + "_mutation"] += 1
    malformed = Counter()
    for size in (1, 3, 25):
        for index in (size, 65535, -size - 1, -1):
            for owned in (False, True):
                chunk = module._Chunk(-1, 1)
                section = module._Section([module.AIR] * size, array("i", [0]) * 4096)
                section.indices[4095] = index
                section.assigned[0] = int(owned)
                chunk.sections[0] = section
                outcomes = []
                for method in (baseline, candidate):
                    try:
                        outcomes.append(("value", method(chunk)))
                    except Exception as exc:
                        outcomes.append(("exception", type(exc).__name__))
                assert outcomes[0] == outcomes[1]
                malformed[outcomes[0][0] + (":" + outcomes[0][1] if outcomes[0][0] == "exception" else "")] += 1
    for length in (0, 4095, 4097):
        chunk = module._Chunk(0, 0, {0: module._Section(indices=array("H", [0]) * length,
                                                       assigned=bytearray([1]) + bytearray(511))})
        outcomes = []
        for method in (baseline, candidate):
            try:
                outcomes.append(("value", method(chunk)))
            except Exception as exc:
                outcomes.append(("exception", type(exc).__name__))
        assert outcomes[0] == outcomes[1]
        malformed["wrong_length:" + outcomes[0][1]] += 1
    return {"seed": SEED, "random_chunks": cases, "matched": dict(matched),
            "malformed_or_negative_index_cases": dict(malformed), "differences": []}


@contextmanager
def using_method(module, method):
    original = module._Chunk.fingerprint
    module._Chunk.fingerprint = method
    try:
        yield
    finally:
        module._Chunk.fingerprint = original


def fixture_placements():
    states = block_states()[1:7]
    result = []
    for cx in range(-4, 4):
        for cz in range(-4, 4):
            for sy in (0, 4, 12, 19):
                for cell in range(0, 4096, 67):
                    point = Vec3(cx * 16 + (cell & 15), sy * 16 + (cell >> 8), cz * 16 + ((cell >> 4) & 15))
                    result.append(Placement(point, states[(cell // 67 + cx + cz + sy) % len(states)], "independent-throughput-fixture"))
            result.append(Placement(Vec3(cx * 16 + 15, 32, cz * 16 + 15), states[0].__class__.of("minecraft:air"), "owned-air"))
    return result


def ledger_rows(path):
    with sqlite3.connect(path / "shamaos/expected.sqlite3") as connection:
        return [(x, z, digest.hex(), count) for x, z, digest, count in
                connection.execute("SELECT x,z,digest,blocks FROM chunks ORDER BY x,z")]


def exact_saved_states(module, path, placements):
    reader = module.NativeWorldReader(path, max_cached_chunks=1)
    expected = {p.pos: module.canonical_block_state(p.block) for p in placements}
    found_owned = {}
    digest = hashlib.sha256()
    cells = 0
    for cx, cz, _, _ in ledger_rows(path):
        digest.update(struct.pack(">ii", cx, cz))
        chunk = reader.chunk(cx, cz)
        for sy, section in sorted(chunk.sections.items()):
            payloads = [json.dumps((state.name, state.properties), separators=(",", ":")).encode()
                        for state in section.palette]
            for cell, palette_id in enumerate(section.indices):
                state = section.palette[palette_id]
                owned = bool(section.assigned[cell >> 3] & (1 << (cell & 7)))
                if state == module.AIR and not owned:
                    continue
                pos = Vec3(cx * 16 + (cell & 15), sy * 16 + (cell >> 8), cz * 16 + ((cell >> 4) & 15))
                if owned:
                    found_owned[pos] = state
                elif pos.y <= -61:
                    expected_ground = BlockState.of("minecraft:bedrock") if pos.y == -64 else \
                        BlockState.of("minecraft:grass_block", snowy="false") if pos.y == -61 else BlockState.of("minecraft:dirt")
                    assert state == expected_ground
                else:
                    raise AssertionError(("unowned nonterrain block", pos, state))
                payload = payloads[palette_id]
                digest.update(struct.pack(">iii?I", pos.x, pos.y, pos.z, owned, len(payload)))
                digest.update(payload)
                cells += 1
    assert found_owned == expected
    assert cells == len(expected) + 64 * 1024
    return {"ordered_state_and_ownership_sha256": digest.hexdigest(), "observed_cells": cells,
            "exact_owned_positions_and_states": len(found_owned), "exact_terrain_cells": 64 * 1024}


def native_corruption_checks(module, path, methods):
    cx = cz = -4
    region = module._Region(path / "region/r.-1.-1.mca")
    index = (cx & 31) + (cz & 31) * 32
    original = region.read(index)
    outcomes = {}
    for fault in ("block_state", "ownership", "protected_terrain", "palette_index"):
        corrupted = deepcopy(original)
        section = next(s for s in corrupted["sections"] if int(s["Y"]) == (-4 if fault == "protected_terrain" else 0))
        if fault == "block_state":
            entry = next(e for e in section["block_states"]["palette"] if str(e["Name"]) == "minecraft:repeater")
            entry["Properties"]["delay"] = module.nbtlib.String("4")
        elif fault in ("ownership", "protected_terrain"):
            values = [int(v) & 255 for v in section["shamaos_written"]]
            values[0] ^= 1
            section["shamaos_written"] = module.nbtlib.ByteArray([v if v < 128 else v - 256 for v in values])
        else:
            states = section["block_states"]
            bits = max(4, (len(states["palette"]) - 1).bit_length())
            words = [int(word) for word in states["data"]]
            words[0] = module._signed_long((words[0] & ((1 << 64) - 1)) | ((1 << bits) - 1))
            states["data"] = module.nbtlib.LongArray(words)
        region.write(index, corrupted)
        region.compact()
        errors = []
        for name, method in methods.items():
            with using_method(module, method):
                try:
                    module.validate_native_world(path)
                except module.WorldWriteError as exc:
                    errors.append((name, str(exc)))
                else:
                    raise AssertionError(("corruption was accepted", fault, name))
        assert errors[0][1] == errors[1][1]
        outcomes[fault] = {"both_rejected": True, "error": errors[0][1]}
    region.write(index, original)
    region.compact()
    for method in methods.values():
        with using_method(module, method):
            assert module.validate_native_world(path)["status"] == "passed"
    return outcomes


def native_benchmark(module, baseline, candidate, trials):
    placements = fixture_placements()
    methods = {"baseline": baseline, "candidate": candidate}
    times = {name: [] for name in methods}
    reference_ledger = reference_states = None
    decoded_result = corruption = None
    with tempfile.TemporaryDirectory(prefix="independent-native-fingerprint-") as temporary:
        for trial in range(trials):
            order = tuple(methods) if trial % 2 == 0 else tuple(reversed(methods))
            for name in order:
                path = Path(temporary) / f"world-{trial}-{name}"
                with using_method(module, methods[name]):
                    start = time.perf_counter()
                    with module.NativeWorldWriter(path, surface_y=-61, spawn_platform=False, max_cached_chunks=8) as writer:
                        writer.set_blocks(placements)
                    wrote = time.perf_counter()
                    report = module.validate_native_world(path)
                    read = time.perf_counter()
                assert report["status"] == "passed" and report["terrain_preserved"]
                assert (report["chunks"], report["assigned_blocks"]) == (64, len(placements))
                rows = ledger_rows(path)
                if reference_ledger is None:
                    reference_ledger = rows
                assert rows == reference_ledger
                times[name].append({"write_save_seconds": wrote - start, "independent_readback_seconds": read - wrote,
                                    "write_and_readback_seconds": read - start})
                if trial == 0:
                    observed = exact_saved_states(module, path, placements)
                    if reference_states is None:
                        reference_states = observed
                    assert observed == reference_states
                if decoded_result is None:
                    chunk = module.NativeWorldReader(path).chunk(-4, -4)
                    expected = baseline(chunk)
                    assert candidate(chunk) == expected
                    samples = {name: [] for name in methods}
                    for repeat in range(3):
                        for method_name, method in methods.items():
                            start = time.perf_counter()
                            for _ in range(32):
                                assert method(chunk) == expected
                            samples[method_name].append((time.perf_counter() - start) / 32)
                    decoded_result = {"decoded_sections": len(chunk.sections), "stored_palette_indices": sum(len(s.indices) for s in chunk.sections.values()),
                                      "assigned_blocks": expected[1], "canonical_fingerprint": expected[0],
                                      "per_fingerprint_seconds": samples,
                                      "median_speedup": statistics.median(samples["baseline"]) / statistics.median(samples["candidate"])}
                    corruption = native_corruption_checks(module, path, methods)
    medians = {name: {phase: statistics.median(sample[phase] for sample in samples) for phase in samples[0]}
               for name, samples in times.items()}
    return {"scope": "64 saved terrain chunks with sparse hardware and owned air; actual create, insert, encode, save and independent disk readback. No whole-cache time claim.",
            "trials_per_method": trials, "order_alternates": True, "chunks": 64, "assigned_placements": len(placements),
            "surface_y": -61, "writer_cache_chunks": 8, "samples_seconds": times, "medians_seconds": medians,
            "median_speedup": {phase: medians["baseline"][phase] / medians["candidate"][phase] for phase in medians["baseline"]},
            "all_saved_ledgers_exactly_equal": True,
            "canonical_ledger_sha256": hashlib.sha256(json.dumps(reference_ledger, separators=(",", ":")).encode()).hexdigest(),
            "exact_decoded_states": reference_states, "decoded_chunk_fingerprint": decoded_result,
            "saved_world_corruption_controls": corruption}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-source", type=Path, default=ROOT / "src/shamaos/worldio.py")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--random-cases", type=int, default=256)
    parser.add_argument("--trials", type=int, default=3)
    args = parser.parse_args()
    assert args.random_cases > 0 and args.trials > 0
    module, baseline, candidate, identity = frozen_methods(args.candidate_source)
    random_result = randomized_review(module, baseline, candidate, args.random_cases)
    print(json.dumps({"randomized": random_result}), flush=True)
    result = {"status": "passed", "identity": identity, "randomized": random_result,
              "native_fixture": native_benchmark(module, baseline, candidate, args.trials),
              "findings": [], "whole_cache_native_run": False,
              "audit_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    assert hashlib.sha256(args.candidate_source.read_bytes()).hexdigest() == identity["candidate_worldio_sha256"]
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"status": result["status"], "native_speedup": result["native_fixture"]["median_speedup"],
                      "decoded_fingerprint_speedup": result["native_fixture"]["decoded_chunk_fingerprint"]["median_speedup"]}), flush=True)


if __name__ == "__main__":
    main()
