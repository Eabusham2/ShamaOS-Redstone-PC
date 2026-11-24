"""Recompute registered-bank writes from real lever uses and raw cell signals.

This independent reader does not call the fixture's producer or timing grader.
Its Anvil decoder is shared with the earlier independent terminal-bank review.
"""
from __future__ import annotations

import argparse
from bisect import bisect_left, bisect_right
from collections import Counter
import gzip
from hashlib import sha256
import json
from pathlib import Path
import tarfile

from experiments.input_storage_bus.review_independent import edges, stored_bit

ARCHIVE_SHA = "359a0d44918784c5858dc0b76e47e090074a157eef71287476476fc8c78525f5"


def review(path):
    assert sha256(path.read_bytes()).hexdigest() == ARCHIVE_SHA
    with tarfile.open(path, "r:xz") as archive:
        blobs = {m.name: archive.extractfile(m).read() for m in archive if m.isfile()}
    manifest = json.loads(blobs.pop("manifest.json"))
    assert set(blobs) == {"blobs/" + h for h in manifest.values()}
    assert all(sha256(data).hexdigest() == name[6:] for name, data in blobs.items())
    files = {name: blobs["blobs/" + digest] for name, digest in manifest.items()}
    results = {}
    for prefix in ("positive", "delayed"):
        load = lambda name: json.loads(files[prefix + "/" + name])
        main_name = "controls" if prefix == "positive" else "delayed-strobes"
        case = load(main_name + ".json")
        raw = json.loads(gzip.decompress(files[prefix + "/" + main_name + "-result.json.gz"]))
        assert case["inputs"] == {} and not any("drive" in s for s in case["steps"])
        controls = {tuple(p): name for name, p in json.loads(files["positive/actuators.json"]).items()}
        assert case["steps"] == json.loads(files["positive/controls.json"])["steps"]
        assert len(controls) == 34 and len(set(controls.values())) == 34
        reference = raw["traces"]
        rises = edges(reference["oscillator"], True)
        assert all(b-a == 70 for a, b in zip(rises, rises[1:]))
        state = dict.fromkeys(controls.values(), False)
        at, token, events = 0, 0, []
        for step in case["steps"]:
            names = [controls[tuple(p)] for p in step.get("use_blocks", [])]
            assert len(names) == len(set(names))
            for name in names:
                state[name] = not state[name]
            if "enable" not in names:
                assert state["enable"]
                token = ((token << 1) & 0xffff) | int(state["seed"])
                word = sum(int(state[f"word{bit}"]) << bit for bit in range(32))
                epoch = rises[bisect_left(rises, at)]
                events.append((epoch, token, word))
            at += step["ticks"]
        assert len(events) == 80 and at == len(reference["oscillator"])
        assert raw["ticks"] == at + case["initial_ticks"]
        assert not state["enable"] and sum(bool(mask) for _, mask, _ in events) == 64
        final = [0] * 16
        for _, mask, word in events:
            for row in range(16):
                if mask & (1 << row):
                    final[row] = word
        counts, faults, margins = Counter(), Counter(), {k: 10**9 for k in ("setup", "hold", "aperture")}
        launch_delays = {}
        chunks = {}
        # Reuse the independent packed palette decoder, retaining exact bytes.
        aliases = {name.replace("/restored/", "/restored-world/"): data for name, data in files.items()}
        pairs = [(b, b+1) for b in range(0, 32, 2)] if prefix == "positive" else [(0, 31)]
        for pair in pairs:
            name = f"lanes-{pair[0]:02d}" if prefix == "positive" else main_name
            scenario = load(name + ".json")
            body = json.loads(gzip.decompress(files[prefix + "/" + name + "-result.json.gz"]))
            assert scenario["steps"] == case["steps"] and scenario["inputs"] == {}
            assert body["scenario_sha256"] == sha256(files[prefix + "/" + name + ".json"]).hexdigest()
            assert body["imported_stream_sha256"] == load("import.json")["stream_sha256"]
            waves = body["traces"]
            assert all(waves[k] == reference[k] for k in waves.keys() & reference.keys())
            for bit in pair:
                clock, d, q = (waves[f"launch{bit}_{pin}"] for pin in "CDQ")
                local = edges(clock, True)
                shift = local[0] - rises[0]
                assert local == [t+shift for t in rises]
                assert edges(clock, False) == [t+35 for t in local]
                changes, prior = [], False
                for epoch, _, word in events:
                    value = bool(word & (1 << bit))
                    edge = epoch+shift
                    assert all(sample == value for sample in d[edge-8:edge+3])
                    if value != prior:
                        changes.append(epoch)
                    prior = value
                assert edges(q) == [t+shift+7 for t in changes]
                terminal = waves[f"terminal{bit}"]
                delays = {t-epoch for t, epoch in zip(edges(terminal), changes)}
                assert len(edges(terminal)) == len(changes) and len(delays) == 1
                launch_delays[bit] = delays.pop()
            for row in range(16):
                phase = edges(reference[f"phase{row}"], True)[0] - rises[0]
                for bit in pair:
                    d, lock, q = (waves[f"{pin}{row}_{bit}"] for pin in ("data", "lock", "bit"))
                    openings, closings, de, qe = edges(lock, False), edges(lock, True), edges(d), edges(q)
                    expected, permitted = False, set()
                    for epoch, mask, word in events:
                        start = epoch+phase
                        left = openings[bisect_left(openings, start+35):bisect_right(openings, start+95)]
                        right = closings[bisect_left(closings, start+35):bisect_right(closings, start+105)]
                        if mask & (1 << row):
                            assert len(left) == len(right) == 1 and right[0]-left[0] == 35
                            left, right = left[0], right[0]
                            value = bool(word & (1 << bit))
                            split = bisect_right(de, right)
                            before = de[split-1] if split else 0
                            after = de[split] if split < len(de) else len(d)
                            for key, measured in (("setup", right-before), ("hold", after-right), ("aperture", right-left)):
                                margins[key] = min(margins[key], measured)
                            faults["unstable_data"] += not all(v == value for v in d[left:right+2])
                            flips = qe[bisect_left(qe, left):bisect_right(qe, right+1)]
                            faults["incorrect_Q"] += len(flips) != int(value != expected) or q[right+1] != value
                            permitted.update(flips)
                            expected = value
                            counts["writes"] += 1
                        else:
                            assert not left and not right
                            counts["retained"] += 1
                        faults["retention"] += q[start+102] != expected
                    faults["stray_Q"] += len(set(qe)-permitted)
                    counts["q_rises"] += sum(q[t] for t in qe)
                    counts["q_falls"] += sum(not q[t] for t in qe)
                    if prefix == "positive":
                        assert stored_bit(aliases, prefix, scenario["outputs"][f"bit{row}_{bit}"], chunks) == bool(final[row] & (1 << bit))
                        counts["reopened_bits"] += 1
        if prefix == "positive":
            assert not any(faults.values()) and counts == {"writes":2048,"retained":38912,"q_rises":895,"q_falls":647,"reopened_bits":512}
            assert margins == {"setup":39,"hold":2,"aperture":35}
            assert all(launch_delays[bit] == 45 for bit in range(1,31))
        else:
            assert faults["unstable_data"] == 96 and faults["incorrect_Q"] == 86 and faults["retention"] == 1381
        results[prefix] = {"counts": dict(counts), "faults": dict(faults), "minimum_rt": margins,
                           "register_terminal_arrivals_rt": launch_delays}
    return {"archive_sha256":ARCHIVE_SHA,"reviewer_sha256":sha256(Path(__file__).read_bytes()).hexdigest(),
            "profiles":results,"complete_machine_runtime_verified":False,
            "scope":"Independent real-lever stimulus, launch registers, raw storage timing and direct packed Anvil decode; event admission/CDC remain open."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    report = review(args.archive)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True)+"\n")
    print(json.dumps(report, sort_keys=True))
