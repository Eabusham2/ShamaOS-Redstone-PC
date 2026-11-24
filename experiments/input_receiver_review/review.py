"""Independent check of the frozen, default-height physical input receiver.

Only the archive reader and bit-vector decoder live here. The producer's test,
trace codec, timing grader, RTL classifier and result labels are not imported.
The default command checks every retained tick; --native also repeats one
complete control sequence and the physically skewed negative in MCHPRS.
"""
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import tarfile
import tempfile
import zlib

import numpy as np

from shamaos.hardware.controls import control_actuators, keyboard_buttons
from shamaos.mchprs_import import export_world
from shamaos.model import Vec3
from shamaos.worldio import NativeWorldReader

HERE = Path(__file__).resolve().parent
ARCHIVE_SHA = "5bbc016e4f02ef8dda45624ee95a4581957daac312b505144c2fb0efe60f92b0"
RUNNER_SHA = "c8032089622501cb57f9edbb0ea7e3be24e3a23fd0b2b80653b0ed4f07045610"
ENGINE = "f9e10d2572116bf5dcb9a36a305f28e40641a61a"
ENGINE_PATCH = "8ebd0e097d07e93a6b2b158e08ad19c2660b0b7e2521c6b6d34a4583a10bfbdc"
CONTROLS = ("UP", "DOWN", "LEFT", "RIGHT", "A", "B", "HOME", "EXIT", "EDITOR", "FILES")
LANES = tuple(f"R{i}" for i in range(8)) + tuple(f"C{i}" for i in range(8)) + CONTROLS


def sha(data):
    return hashlib.sha256(data).hexdigest()


def load_archive(path):
    assert sha(path.read_bytes()) == ARCHIVE_SHA, "unreviewed archive bytes"
    files = {}
    with tarfile.open(path, "r:gz") as archive:
        for member in archive:
            name = PurePosixPath(member.name)
            assert member.isfile() and not name.is_absolute() and ".." not in name.parts
            assert member.name not in files and member.size <= 20_000_000
            files[member.name] = archive.extractfile(member).read()
    index = json.loads(files["SHA256.json"])
    assert set(index) == set(files) - {"SHA256.json"}
    assert len(index) == 604
    for name, digest in index.items():
        assert sha(files[name]) == digest, name
    return files


def decode_trace(data):
    doc = json.loads(gzip.decompress(data))
    count = doc["size"]
    assert type(count) is int and count > 0
    vectors, serialized = [], []
    for string in doc["series"]:
        packed = zlib.decompress(base64.b64decode(string, validate=True))
        assert len(packed) == (count + 7) // 8
        bits = np.unpackbits(np.frombuffer(packed, dtype=np.uint8), bitorder="little")[:count].astype(bool)
        vectors.append(bits)
        serialized.append(json.dumps(bits.tolist(), separators=(",", ":")).encode())
    trace, parts = {}, []
    for name, index in doc["names"]:
        assert name not in trace and type(index) is int and 0 <= index < len(vectors)
        trace[name] = vectors[index]
        parts.append(json.dumps(name).encode() + b":" + serialized[index])
    raw = b"{" + b",".join(parts) + b"}"
    assert sha(raw) == doc["raw_sha256"], "reconstructed native trace changed"
    # The archived gzip digest/header describe its original transport. The
    # canonical decoded JSON above is the waveform identity. Recompressing it
    # on another zlib version need not reproduce the old deflate bitstream.
    return trace, doc["raw_sha256"]


def rising(signal):
    return np.flatnonzero(signal[1:] & ~signal[:-1]) + 1


def clock_check(trace):
    clock = trace["R0_C"]
    for lane in LANES:
        if lane + "_C" in trace:
            assert np.array_equal(trace[lane + "_C"], clock), f"clock skew: {lane}"
    if "sample_clock" in trace:
        assert np.array_equal(trace["sample_clock"], clock), "export clock skew"
    edges = rising(clock)
    assert len(edges) > 20 and edges[0] == 330
    assert np.all(np.diff(edges) == 70)
    assert not clock[:edges[0]].any() and not clock[edges[-1] + 35:].any()
    for edge in edges:
        assert clock[edge:edge + 35].all() and not clock[edge + 35:edge + 70].any()
    source = rising(trace["oscillator"])
    assert np.array_equal(source + 72, edges), "source clock lost or duplicated"
    return edges


def register_check(trace, edges):
    checks = 0
    for lane in LANES:
        if lane + "_D" not in trace:
            continue
        d, q = trace[lane + "_D"], trace[lane + "_Q"]
        for edge in edges:
            aperture = d[edge - 8:edge + 3]
            if np.all(aperture == d[edge]):
                assert np.all(q[edge + 7:edge + 35] == d[edge]), (lane, int(edge), "D/Q")
                checks += 1
            # Arbitrary button phase may change D during the setup aperture.
            # It still must settle once, remain latched through the low phase,
            # and cannot invent an extra registered pulse.
            changes = np.flatnonzero(q[edge:edge + 70] != q[edge - 1:edge + 69])
            assert len(changes) <= 1 and np.all(changes < 35), (lane, int(edge), "unstable Q")
    return checks


def gesture_map():
    origin = Vec3(900, -56, -8)
    actions = {tuple((p.x, p.y, p.z)): {name} for name, p in control_actuators(origin).items()}
    for r, row in enumerate(keyboard_buttons(origin)):
        for c, point in enumerate(row):
            actions[(point.x, point.y, point.z)] = {f"R{r}", f"C{c}"}
    for position, names in list(actions.items()):
        if names == {"SHIFT"}:
            actions[position] = {"R7", "C7"}
    return actions


def gesture_check(case, trace, edges):
    actions = gesture_map()
    gestures = 0
    time = 0
    for step in case["steps"]:
        selected = set()
        for position in step.get("use_blocks", []):
            selected |= actions[tuple(position)]
        stop = time + step["ticks"]
        if selected & {"POWER", "PAUSE"}:
            time = stop
            continue
        assert selected
        gestures += 1
        active = [name for name in LANES if name in selected]
        for name in LANES:
            if name not in trace:
                continue
            expected = name in selected
            pulse = trace[name][time:stop]
            segments = int(pulse[0]) + len(rising(pulse))
            assert segments == int(expected), (time, selected, name, "raw pulse count")
            q = trace[name + "_Q"][time:stop]
            assert int(q[0]) + len(rising(q)) == int(expected), (time, selected, name, "Q pulse count")
            if not expected:
                assert not trace[name + "_D"][time:stop].any()
        if active:
            eligible = [e for e in edges if time <= e < stop and all(trace[n + "_D"][e - 8:e + 3].all() for n in active)]
            assert eligible, (time, selected, "no simultaneous full setup/hold")
            for edge in eligible:
                assert all(trace[n + "_Q"][edge + 7:edge + 35].all() for n in active)
        time = stop
    assert time == len(trace["R0_C"])
    return gestures


def endpoint_check(case, trace):
    time = count = 0
    for step in case["steps"]:
        assert step["ticks"] > 0 and not step.get("drive")
        time += step["ticks"]
        for name, expected in step.get("expect", {}).items():
            assert type(expected) is bool
            assert bool(trace[name][time - 1]) == expected, (time, name, "retained endpoint assertion")
            count += 1
    return count


def source_check(files, prefix):
    lines = files[prefix + "/receiver.jsonl"].splitlines()
    header, complete = json.loads(lines[0]), json.loads(lines[-1])
    assert header["kind"] == "header" and header["surface_y"] == -61
    assert header["schema"] == "shama-native-anvil-sections-v2"
    assert complete["kind"] == "complete"
    source = header["source"]
    assert source["data_version"] == 3700 and source["saved_native_pending_ticks"] == 0
    world = prefix + "/world/"
    assert source["level_dat_sha256"] == sha(files[world + "level.dat"])
    regions = [{"name": name.removeprefix(world + "region/"), "bytes": len(data), "sha256": sha(data)}
               for name, data in sorted(files.items()) if name.startswith(world + "region/") and name.endswith(".mca")]
    assert source["regions"] == regions and len(regions) == 4
    return source, complete


def engine_check(engine, case, files, prefix, scenario_bytes):
    source, complete = source_check(files, prefix)
    assert engine["status"] == "passed" and engine["engine_revision"] == ENGINE
    assert engine["engine_registry_patch_sha256"] == ENGINE_PATCH
    assert engine["scenario_sha256"] == sha(scenario_bytes)
    assert engine["imported_stream_sha256"] == sha(files[prefix + "/receiver.jsonl"])
    assert engine["source"] == source
    assert engine["complete_machine_runtime_verified"] is False and engine["pending_ticks_after_scenario"] == 0
    assert engine["ticks"] == case["initial_ticks"] + sum(step["ticks"] for step in case["steps"])
    assert engine["steps"] == len(case["steps"])
    assert engine["assertions"] == sum(len(step.get("expect", {})) for step in case["steps"])
    assert engine["loaded_chunks"] == complete["chunks"]
    assert engine["decoded_nonair_blocks"] == engine["initialized_nonair_blocks"] == complete["nonair"]
    assert engine["comparator_entities"] == complete["comparators"]


def edge_word_check(trace, common):
    checked = 0
    for name in CONTROLS:
        sample_edges = rising(trace[name + "_edge_C"])
        previous_edges = rising(trace[name + "_previous_C"])
        assert len(sample_edges) == len(previous_edges) == len(common)
        assert np.all((sample_edges - common >= 30) & (sample_edges - common <= 36))
        assert np.all((previous_edges - common >= 57) & (previous_edges - common <= 59))
        previous = False
        for i, edge in enumerate(common[:-1]):
            value = bool(trace[name + "_Q"][edge + 34])
            want = value and not previous
            pc, ec = previous_edges[i], sample_edges[i]
            assert np.all(trace[name + "_previous_D"][pc - 8:pc + 3] == value)
            assert bool(trace[name + "_previous_Q"][pc + 7]) == value
            assert np.all(trace[name + "_edge_D"][ec - 8:ec + 3] == want)
            assert bool(trace[name + "_edge_Q"][ec + 7]) == want
            following = common[i + 1]
            assert np.all(trace[name + "_edge_Q"][following - 8:following + 3] == want)
            checked += 1
            previous = value
    return checked


def world_check(files, directory):
    for name, data in files.items():
        prefix = "default-height/world/"
        if name.startswith(prefix) and (name.endswith(".mca") or name.endswith("level.dat")):
            target = directory / name.removeprefix(prefix)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    reader = NativeWorldReader(directory)
    assert reader.surface_y == -61
    chunks = set()
    terrain = circuit = 0
    for block in reader.iter_blocks():
        p = block.pos
        chunks.add((p.x // 16, p.z // 16))
        if p.y <= -61:
            assert block.block.name == {-64: "minecraft:bedrock", -63: "minecraft:dirt", -62: "minecraft:dirt", -61: "minecraft:grass_block"}[p.y]
            terrain += 1
        else:
            circuit += 1
    assert len(chunks) == 156 and terrain == 156 * 1024 and circuit == 35495
    stream = directory.parent / "independent-export.jsonl"
    export_world(directory, stream)
    old = files["default-height/receiver.jsonl"].splitlines()
    new = stream.read_bytes().splitlines()
    a, b = json.loads(old[0]), json.loads(new[0])
    # Only the local extraction directory differs. Every other header field,
    # all 3,744 section records, entities and final counts must match exactly.
    a["source"].pop("native_world")
    b["source"].pop("native_world")
    assert a == b and old[1:] == new[1:]
    return {"chunks": len(chunks), "circuit_blocks": circuit, "terrain_blocks": terrain,
            "fresh_anvil_export_body_matches_native_input": True,
            "export_body_sha256": sha(b"\n".join(new[1:]) + b"\n")}


def check(files):
    folders = sorted(name.rsplit("/", 1)[0] for name in files if name.startswith("default-height/") and name.endswith("/trace.json.gz"))
    assert set(folders) == {*(f"default-height/phase-{i:02d}" for i in range(70)),
                            *(f"default-height/edge-{i:02d}" for i in range(70)), "default-height/both-legends"}
    totals = {"scenarios": 0, "native_ticks": 0, "retained_native_assertions": 0, "gestures": 0, "qualified_register_apertures": 0, "edge_word_apertures": 0}
    for number, folder in enumerate(folders):
        case = json.loads(files[folder + "/scenario.json"])
        engine = json.loads(files[folder + "/engine.json"])
        assert case["inputs"] == {}, "external harness drives are not a saved console proof"
        engine_check(engine, case, files, "default-height", files[folder + "/scenario.json"])
        trace, _ = decode_trace(files[folder + "/trace.json.gz"])
        assert set(trace) == set(case["outputs"])
        expected_lanes = CONTROLS if "/edge-" in folder else LANES
        assert {*(name + pin for name in expected_lanes for pin in ("", "_D", "_C", "_Q")),
                *(name + pin for name in CONTROLS for pin in ("_edge_D", "_edge_C", "_edge_Q", "_previous_D", "_previous_C", "_previous_Q")),
                "R0_C", "RESET", "POWER", "oscillator"} <= set(trace)
        assert len(trace["R0_C"]) + case["initial_ticks"] == engine["ticks"]
        assert endpoint_check(case, trace) == engine["assertions"]
        common = clock_check(trace)
        totals["qualified_register_apertures"] += register_check(trace, common)
        totals["edge_word_apertures"] += edge_word_check(trace, common)
        totals["gestures"] += gesture_check(case, trace, common)
        totals["scenarios"] += 1
        totals["native_ticks"] += engine["ticks"]
        totals["retained_native_assertions"] += engine["assertions"]
        if number % 20 == 0:
            print(f"checked {number + 1}/141 native traces", flush=True)
    negative, _ = decode_trace(files["negative-initial-true-height/trace.json.gz"])
    try:
        clock_check(negative)
    except AssertionError as error:
        assert "clock skew" in str(error), error
        totals["retained_skew_negative"] = str(error)
    else:
        raise AssertionError("real 1RT clock skew was not detected")
    assert totals["native_ticks"] == 3_203_540 and totals["retained_native_assertions"] == 27_181
    assert totals["gestures"] == 6_793
    return totals


def native_repeat(files, directory):
    runner = Path(os.environ["SHAMA_MCHPRS_TERRAIN_RUNNER"])
    assert sha(runner.read_bytes()) == RUNNER_SHA
    receipts = {}
    for label, folder, stream in (("positive", "default-height/phase-69", "default-height/receiver.jsonl"),
                                  ("negative", "negative-initial-true-height", "negative-initial-true-height/receiver.jsonl")):
        target = directory / label
        target.mkdir()
        world, scenario = target / "world.jsonl", target / "scenario.json"
        world.write_bytes(files[stream])
        scenario.write_bytes(files[folder + "/scenario.json"])
        with (target / "native.json").open("wb") as stdout, (target / "stderr.txt").open("wb") as stderr:
            result = subprocess.run([str(runner), str(world), str(scenario), "2000000"], stdout=stdout, stderr=stderr, timeout=180)
        assert result.returncode == 0, (target / "stderr.txt").read_text()
        fresh = json.loads((target / "native.json").read_bytes())
        frozen, trace_sha = decode_trace(files[folder + "/trace.json.gz"])
        tr = fresh.pop("traces")
        prefix = "default-height" if label == "positive" else folder
        case = json.loads(scenario.read_bytes())
        engine_check(fresh, case, files, prefix, scenario.read_bytes())
        assert endpoint_check(case, tr) == fresh["assertions"]
        assert set(tr) == set(frozen) and all(np.array_equal(tr[n], frozen[n]) for n in tr), label
        assert fresh["pending_ticks_after_scenario"] == 0 and fresh["complete_machine_runtime_verified"] is False
        receipts[label] = {"native_ticks": fresh["ticks"], "assertions": fresh["assertions"], "native_json_sha256": sha((target / "native.json").read_bytes()),
                           "retained_raw_trace_sha256": trace_sha, "all_declared_traces_identical": True}
        (target / "metadata.json").write_text(json.dumps(fresh, indent=2) + "\n")
    return receipts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=HERE / "evidence/default-height-input-receiver.tar.gz")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--native", action="store_true")
    args = parser.parse_args()
    files = load_archive(args.archive)
    result = {"archive_sha256": ARCHIVE_SHA, "producer_commit": "176cef9afb17684220fc007744dc1a7680cd9d35",
              "scope": "Complete saved console and physical input registers, at default height. The classifier, CDC return, packet memory, CPU and whole computer are not covered."}
    result.update(check(files))
    with tempfile.TemporaryDirectory(prefix="input-independent-") as temporary:
        target = Path(temporary)
        result["saved_world"] = world_check(files, target / "world")
        if args.native:
            result["fresh_native"] = native_repeat(files, target)
    result["status"] = "passed"
    result["complete_machine_runtime_verified"] = False
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
