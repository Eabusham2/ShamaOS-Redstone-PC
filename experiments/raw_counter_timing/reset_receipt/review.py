"""Independent review of a bounded reset-receipt experiment, not a system gate."""
from bisect import bisect_left, bisect_right
import argparse
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile


ARCHIVE = Path(__file__).with_name("bridge-evidence.tar.xz")
ARCHIVE_SHA256 = "87de71a9bb031a6731a7914cf94015075002ae1c5a12226503c64f51f27565aa"
RUNNER_SHA256 = "c8032089622501cb57f9edbb0ea7e3be24e3a23fd0b2b80653b0ed4f07045610"


def transitions(values):
    return [i for i in range(1, len(values)) if values[i] != values[i - 1]]


def rising(values):
    return [i for i in transitions(values) if values[i]]


def review(directory):
    files = {name: directory / name for name in
             ("matrix.json", "matrix-result.json", "bridge.json", "world.jsonl")}
    original = {name: sha256(path.read_bytes()).hexdigest() for name, path in files.items()}
    case = json.loads(files["matrix.json"].read_bytes())
    native = json.loads(files["matrix-result.json"].read_bytes())
    traces = native["traces"]
    ticks = case["initial_ticks"] + sum(step["ticks"] for step in case["steps"])
    assert native["ticks"] == ticks
    assert native["status"] == "passed" and native["pending_ticks_after_scenario"] == 0
    assert native["scenario_sha256"] == original["matrix.json"]
    assert native["imported_stream_sha256"] == original["world.jsonl"]
    assert set(traces) == set(case["outputs"])
    assert all(len(trace) == ticks and all(type(bit) is bool for bit in trace)
               for trace in traces.values())
    assert set(case["inputs"]) == {"raw", "local_reset", "cleared_valid", "cleared_ticket"}
    # Reconstruct all physical stimulus contacts from the submitted sequence.
    # Internal request, issue, release, reset and register pins must not be driven.
    levels = dict.fromkeys(case["inputs"], False)
    cursor = case["initial_ticks"]
    for step in case["steps"]:
        assert set(step.get("drive", {})) <= set(levels)
        levels.update(step.get("drive", {}))
        end = cursor + step["ticks"]
        for name, value in levels.items():
            assert all(bit == value for bit in traces["port_" + name][cursor:end])
        cursor = end
    transactions = []
    starts = rising(traces["port_local_reset"])
    assert len(starts) == 70
    all_ticket_changes = transitions(traces["request_ticket_Q"])
    assert len(all_ticket_changes) == 70
    all_reset_changes = transitions(traces["reset"])
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else ticks
        ticket_events = [t for t in all_ticket_changes if start <= t < end]
        assert len(ticket_events) == 1
        ticket = ticket_events[0]
        new_value = traces["request_ticket_Q"][ticket]
        assert new_value != traces["request_ticket_Q"][start - 1]
        reset_edges = [t for t in all_reset_changes if start <= t < end]
        assert len(reset_edges) == 2
        asserted, released = reset_edges
        assert traces["reset"][asserted] and not traces["reset"][released]
        assert all(traces["reset"][asserted:released])
        # Both an explicitly stale response and a later correct response must
        # actually arrive. Compare the real physical input contacts, not labels.
        stale = [t for t in range(ticket, released)
                 if traces["port_cleared_valid"][t]
                 and traces["port_cleared_ticket"][t] != new_value]
        assert stale and all(traces["reset"][t] for t in stale)
        matching = next(t for t in range(ticket, released + 1)
                        if traces["port_cleared_valid"][t]
                        and traces["port_cleared_ticket"][t] == new_value)
        assert released > matching
        assert all(traces["released_Q"][released:end])
        transactions.append({"input_rise": start, "ticket_change": ticket,
                             "reset_rise": asserted, "matching_response": matching,
                             "reset_fall": released, "stale_response_ticks": len(stale)})
    registers = {}
    violations = []
    capture_mismatches = []
    for name in ("issue", "request_ticket", "released"):
        clocks = rising(traces[name + "_C"])
        changes = transitions(traces[name + "_D"])
        setup, hold = ticks, ticks
        for edge in clocks:
            before = bisect_right(changes, edge)
            after = bisect_left(changes, edge)
            s = edge - changes[before - 1] if before else edge
            h = changes[after] - edge if after < len(changes) else ticks - edge
            setup, hold = min(setup, s), min(hold, h)
            if s < 8 or h < 2:
                violations.append({"register": name, "edge": edge, "setup": s, "hold": h})
            # Every observed event must capture real D at the template's Q delay.
            assert edge + 7 < ticks
            if traces[name + "_Q"][edge + 7] != traces[name + "_D"][edge]:
                capture_mismatches.append({"register": name, "edge": edge,
                                           "D": traces[name + "_D"][edge],
                                           "Q": traces[name + "_Q"][edge + 7]})
        registers[name] = {"edges": len(clocks), "minimum_setup": setup, "minimum_hold": hold}
    assert original == {name: sha256(path.read_bytes()).hexdigest() for name, path in files.items()}
    return {"scope": "Physical 19/18-cell reset-receipt bridge only; counter return, Step and whole-machine execution remain separate",
            "source_files": {name: {"path": str(path), "sha256": original[name]}
                             for name, path in files.items()},
            "ticks": ticks, "transactions": transactions, "registers": registers,
            "aperture_violations": violations, "capture_mismatches": capture_mismatches,
            "native_pending_events": 0,
            "complete_machine_runtime_verified": False}


def review_retained(archive=ARCHIVE, *, runner=None):
    archive = Path(archive)
    if sha256(archive.read_bytes()).hexdigest() != ARCHIVE_SHA256:
        raise ValueError("reset-receipt evidence changed")
    if runner is not None:
        runner = Path(runner).resolve()
        if sha256(runner.read_bytes()).hexdigest() != RUNNER_SHA256:
            raise ValueError("reset-receipt replay requires its pinned terrain engine")
    with tarfile.open(archive, "r:xz") as tar:
        members = tar.getmembers()
        assert len({m.name for m in members}) == len(members)
        assert all(m.isfile() and not Path(m.name).is_absolute()
                   and ".." not in Path(m.name).parts for m in members)
        contents = {m.name: tar.extractfile(m).read() for m in members}
    inventory = json.loads(contents.pop("inventory.json"))["sha256"]
    assert inventory == {name: sha256(data).hexdigest() for name, data in contents.items()}
    with tempfile.TemporaryDirectory(prefix="reset-receipt-review-") as temporary:
        root = Path(temporary)
        for name, data in contents.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        for group in ("observed", "issued"):
            if runner is not None:
                directory = root / group
                replayed = json.loads(subprocess.check_output([
                    str(runner), str(directory / "world.jsonl"),
                    str(directory / "matrix.json"), "1200"]))
                original = json.loads((directory / "matrix-result.json").read_bytes())
                for key in ("traces", "status", "ticks", "steps", "assertions",
                            "scenario_sha256", "imported_stream_sha256",
                            "engine_revision", "engine_registry_patch_sha256",
                            "initialized_nonair_blocks", "pending_ticks_after_scenario"):
                    assert replayed[key] == original[key], (group, key)
        positive = review(root / "observed")
        negative = review(root / "issued")
        for group, result in (("observed", positive), ("issued", negative)):
            for name, record in result["source_files"].items():
                record["path"] = group + "/" + name
    assert not positive["aperture_violations"]
    assert not positive["capture_mismatches"]
    assert len(negative["aperture_violations"]) == 9
    assert len(negative["capture_mismatches"]) == 2
    result = {"schema": "independent-reset-receipt-review-v1", "positive": positive,
              "retained_negative": negative,
              "fresh_native_cases": 2 if runner is not None else 0,
              "status": "bounded-bridge-positive-and-nine-aperture-negative-cases-reproduced"}
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runner", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = review_retained(runner=args.runner)
    if args.output:
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    positive, negative = result["positive"], result["retained_negative"]
    print(json.dumps({"positive_transactions": len(positive["transactions"]),
                      "positive_registers": positive["registers"],
                      "negative_violations": len(negative["aperture_violations"]),
                      "negative_capture_mismatches": negative["capture_mismatches"],
                      "fresh_native_cases": result["fresh_native_cases"]}))
