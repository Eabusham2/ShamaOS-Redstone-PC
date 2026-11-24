"""Independently read raw bank traces and packed Anvil states without its analyzer.

The two recorded profiles exercise a terminal-level 16x32 bank, not the complete
input receiver or PC. Thirty payload bits are driven at bank boundaries. This
review derives stimulus words from the actual native cases, timing from local
D/lock/Q traces, and saved bits from Minecraft section palette integers.
"""
from __future__ import annotations

import argparse
from bisect import bisect_left, bisect_right
from collections import Counter
import gzip
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import tarfile
import zlib

import nbtlib


EXPECTED_ARCHIVE = "1df7ab65a213d5eec2a4342e0173d4a13fd9aefd3af4fc58e56e345e2a581dfd"


def edges(values, target=None):
    return [i for i in range(1, len(values))
            if values[i] != values[i - 1] and (target is None or values[i] == target)]


def read_archive(path):
    assert sha256(path.read_bytes()).hexdigest() == EXPECTED_ARCHIVE
    with tarfile.open(path, "r:xz") as archive:
        raw = {member.name: archive.extractfile(member).read() for member in archive if member.isfile()}
    manifest = json.loads(raw.pop("manifest.json"))
    assert set(raw) == {"blobs/" + value for value in manifest.values()}
    for name, data in raw.items():
        assert sha256(data).hexdigest() == name.removeprefix("blobs/")
    return {name: raw["blobs/" + digest] for name, digest in manifest.items()}


def stored_bit(files, prefix, position, cache):
    x, y, z = position
    cx, cz = x // 16, z // 16
    key = prefix, cx, cz
    if key not in cache:
        region = files[f"{prefix}/restored-world/region/r.{cx // 32}.{cz // 32}.mca"]
        slot = (cx % 32) + 32 * (cz % 32)
        location = int.from_bytes(region[4 * slot:4 * slot + 4], "big")
        offset = (location >> 8) * 4096
        assert offset >= 8192 and location & 255
        size = int.from_bytes(region[offset:offset + 4], "big")
        assert region[offset + 4] == 2
        root = nbtlib.File.parse(BytesIO(zlib.decompress(region[offset + 5:offset + 4 + size])))
        assert int(root["xPos"]) == cx and int(root["zPos"]) == cz
        cache[key] = {int(section["Y"]): section for section in root["sections"]}
    states = cache[key][y // 16]["block_states"]
    palette = states["palette"]
    index = 256 * (y % 16) + 16 * (z % 16) + x % 16
    palette_index = 0
    if len(palette) > 1:
        bits = max(4, (len(palette) - 1).bit_length())
        per_long = 64 // bits
        value = int(states["data"][index // per_long]) & ((1 << 64) - 1)
        palette_index = (value >> ((index % per_long) * bits)) & ((1 << bits) - 1)
    state = palette[palette_index]
    assert str(state["Name"]) == "minecraft:repeater"
    assert str(state["Properties"]["locked"]) == "true"
    return str(state["Properties"]["powered"]) == "true"


def review(path):
    files = read_archive(path)
    results = []
    decoded = {}
    for phase in (33, 41):
        prefix = f"phase-{phase}"
        load = lambda name: json.loads(files[f"{prefix}/{name}"])
        reference = load("controls.json")
        control = json.loads(gzip.decompress(files[f"{prefix}/controls-result.json.gz"]))
        imported = load("import.json")
        stimulus, at, pending = [], 0, None
        for step in reference["steps"]:
            drive = step.get("drive", {})
            if at and "near" in drive:
                assert pending is None
                pending = [at + 20, sum(int(drive[f"s{r}"]) << r for r in range(16)),
                           int(drive["near"]) | (int(drive["far"]) << 31)]
            if at and "bus1" in drive:
                assert pending is not None and at == pending[0] + phase
                pending[2] |= sum(int(drive[f"bus{b}"]) << b for b in range(1, 31))
                stimulus.append(pending)
                pending = None
            at += step["ticks"]
        assert len(stimulus) == 91 and pending is None
        assert all(b[0] - a[0] == 70 for a, b in zip(stimulus, stimulus[1:]))
        counts = Counter()
        margins = {"setup": 10**9, "hold": 10**9, "aperture": 10**9}
        failures = []
        lower, upper = -10**9, 10**9
        final_words = [0] * 16
        for _, mask, word in stimulus:
            for row in range(16):
                if mask & (1 << row):
                    final_words[row] = word
        for column in range(0, 32, 2):
            name = f"columns-{column:02d}"
            case = load(name + ".json")
            raw = json.loads(gzip.decompress(files[f"{prefix}/{name}-result.json.gz"]))
            assert case["inputs"] == reference["inputs"] and case["steps"] == reference["steps"]
            assert raw["source"] == imported["source"]
            assert raw["imported_stream_sha256"] == imported["stream_sha256"]
            assert raw["scenario_sha256"] == sha256(files[f"{prefix}/{name}.json"]).hexdigest()
            assert raw["ticks"] == at + reference["initial_ticks"]
            assert raw["complete_machine_runtime_verified"] is False
            tr = raw["traces"]
            assert set(tr) == set(case["outputs"])
            for signal in tr.keys() & control["traces"].keys():
                assert tr[signal] == control["traces"][signal]
            first = edges(tr["oscillator"], True)[0]
            for row in range(16):
                row_delay = edges(tr[f"phase{row}"], True)[0] - first
                for bit in (column, column + 1):
                    d, lock, q = (tr[f"{kind}{row}_{bit}"] for kind in ("data", "lock", "bit"))
                    d_edges, q_edges = edges(d), edges(q)
                    opens, closes = edges(lock, False), edges(lock, True)
                    allowed = set()
                    expected = False
                    for index, (epoch, mask, word) in enumerate(stimulus):
                        start = epoch + row_delay
                        opening = opens[bisect_left(opens, start + 35):bisect_right(opens, start + 95)]
                        closing = closes[bisect_left(closes, start + 35):bisect_right(closes, start + 105)]
                        if mask & (1 << row):
                            assert len(opening) == len(closing) == 1
                            left, right = opening[0], closing[0]
                            assert right - left == 35
                            value = bool(word & (1 << bit))
                            after = bisect_right(d_edges, right)
                            previous_edge = d_edges[after - 1] if after else 0
                            next_edge = d_edges[after] if after < len(d_edges) else len(d)
                            measured = {"setup": right - previous_edge, "hold": next_edge - right, "aperture": right - left}
                            for key, number in measured.items():
                                margins[key] = min(margins[key], number)
                            if not all(sample == value for sample in d[left:right + 2]):
                                failures.append({"epoch": index, "row": row, "bit": bit, "hold": measured["hold"]})
                            if 0 < bit < 31:
                                split = bisect_right(d_edges, left)
                                if split:
                                    upper = min(upper, phase + left - d_edges[split - 1] - 1)
                                if split < len(d_edges):
                                    lower = max(lower, phase + 2 - (d_edges[split] - right))
                            transitions = q_edges[bisect_left(q_edges, left):bisect_right(q_edges, right + 1)]
                            assert len(transitions) == int(value != expected)
                            allowed.update(transitions)
                            expected = value
                            counts["writes"] += 1
                        else:
                            assert not opening and not closing
                            counts["inhibited"] += 1
                        assert q[start + 102] == expected
                    assert set(q_edges) == allowed
                    assert any(q[e] for e in q_edges) and any(not q[e] for e in q_edges)
                    counts["q_rises"] += sum(q[e] for e in q_edges)
                    counts["q_falls"] += sum(not q[e] for e in q_edges)
                    actual_saved = stored_bit(files, prefix, case["outputs"][f"bit{row}_{bit}"], decoded)
                    assert actual_saved == bool(final_words[row] & (1 << bit))
                    counts["reopened_bits"] += 1
        assert counts == {"writes": 8000, "inhibited": 38592, "q_rises": 2515, "q_falls": 2249, "reopened_bits": 512}
        assert [lower, upper] == [34, 49]
        if phase == 33:
            assert len(failures) == 13 and {(f["row"], f["bit"], f["hold"]) for f in failures} == {(13, 30, 1)}
            assert margins == {"setup": 52, "hold": 1, "aperture": 35}
        else:
            assert not failures and margins == {"setup": 44, "hold": 2, "aperture": 35}
        results.append({"phase_rt": phase, "measurements": dict(counts), "minimum_rt": margins,
                        "launch_window_rt": [lower, upper], "timing_violations": failures})
    return {"archive_sha256": EXPECTED_ARCHIVE,
            "reviewer_sha256": sha256(Path(__file__).read_bytes()).hexdigest(), "profiles": results,
            "complete_machine_runtime_verified": False,
            "scope": "Independent raw D/lock/Q and direct packed-Anvil readback; 30 terminal sources remain external."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = review(args.archive)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, sort_keys=True))
