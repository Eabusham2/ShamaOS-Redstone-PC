"""Compare the frozen Vec and deque runners on identical small saved worlds.

Run after preparing the native fixtures in tests/test_mchprs_sparse_runtime.py
and the positive/negative connected-pipeline experiment. No fixture is changed.
Both runners receive identical paths, chunk limits and scenarios. Every report
field, complete trace and requested snapshot must match, apart from the four
explicit process-performance fields and snapshot writing duration.

Example:
  python tools/check_pending_queue_equivalence.py --baseline OLD --candidate NEW \
    --fixture-root EXPERIMENTS/world-runtime --output build/queue-comparison \
    --evidence docs/evidence/pending-queue-equivalence-2026-11-13.json
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import resource
import subprocess
import time


REPOSITORY = Path(__file__).resolve().parents[1]
PERFORMANCE_FIELDS = ("import_seconds", "initialization_seconds", "runtime_seconds", "peak_rss_kib")


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def record(path: Path) -> dict:
    return {"path": str(path.resolve()), "bytes": path.stat().st_size, "sha256": sha(path)}


def canonical_sha(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def semantic_report(report: dict) -> dict:
    # Shallow copies preserve all trace arrays, and avoid unnecessary memory.
    result = {key: value for key, value in report.items() if key not in PERFORMANCE_FIELDS}
    if result.get("snapshot") is not None:
        result["snapshot"] = {key: value for key, value in result["snapshot"].items() if key != "seconds"}
    return result


def compare_reports(first: dict, second: dict) -> dict:
    before, after = semantic_report(first), semantic_report(second)
    if before != after:
        keys = sorted(key for key in before.keys() | after.keys() if before.get(key) != after.get(key))
        raise ValueError(f"native runner semantic mismatch in fields: {keys}")
    return {"every_semantic_field_equal": True,
            "trace_outputs": len(before["traces"]),
            "exact_trace_samples": sum(len(values) for values in before["traces"].values()),
            "semantic_report_sha256": canonical_sha(before)}


def check_comparison_rejects_changed_semantics() -> list[str]:
    reference = {"status": "passed", "traces": {"q": [False, True]}, "scheduled_ticks_total": 2,
                 "peak_pending_ticks": 1, "pending_ticks_after_scenario": 0, "snapshot": None}
    failures = []
    for field, replacement in (("traces", {"q": [False, False]}), ("scheduled_ticks_total", 3),
                               ("peak_pending_ticks", 2), ("pending_ticks_after_scenario", 1),
                               ("status", "failed")):
        changed = deepcopy(reference)
        changed[field] = replacement
        try:
            compare_reports(reference, changed)
        except ValueError:
            failures.append(field)
        else:
            raise AssertionError(f"comparison silently accepted changed {field}")
    changed = deepcopy(reference)
    changed.update({field: 123 for field in PERFORMANCE_FIELDS})
    compare_reports(reference, changed)
    return failures


def chunk_count(stream: Path) -> int:
    last = None
    with stream.open() as source:
        for line in source:
            last = line
    complete = json.loads(last)
    if complete["kind"] != "complete" or not 0 < complete["chunks"] <= 128:
        raise ValueError("differential check is limited to complete small fixtures of at most 128 chunks")
    return complete["chunks"]


def run(binary: Path, stream: Path, scenario: Path, directory: Path, label: str,
        limit: int, snapshot: bool) -> tuple[dict, dict]:
    command = [str(binary), str(stream), str(scenario), str(chunk_count(stream) + 16)]
    snapshot_path = directory / "snapshot.jsonl"
    if snapshot:
        if snapshot_path.exists():
            raise ValueError("snapshot output already exists")
        command += ["--snapshot", str(snapshot_path)]

    def enforce_limit():
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        resource.setrlimit(resource.RLIMIT_CPU, (30, 30))

    stdout_path, stderr_path = directory / f"{label}.json", directory / f"{label}.stderr.txt"
    started = time.monotonic()
    with stdout_path.open("x") as stdout, stderr_path.open("x") as stderr:
        process = subprocess.run(command, stdout=stdout, stderr=stderr, timeout=45, preexec_fn=enforce_limit)
    elapsed = time.monotonic() - started
    execution = {"exit_code": process.returncode, "wall_seconds": elapsed,
                 "stdout": record(stdout_path), "stderr": record(stderr_path)}
    if process.returncode:
        raise ValueError(f"{label} failed for {directory.name}; retained stdout/stderr at {directory}")
    report = json.loads(stdout_path.read_text())
    if report["status"] != "passed" or report["complete_machine_runtime_verified"] is not False:
        raise ValueError("fixture must have actual assertions and retain its limited runtime scope")
    if report["imported_stream_sha256"] != sha(stream) or report["scenario_sha256"] != sha(scenario):
        raise ValueError("runtime does not bind the exact supplied inputs")
    execution["performance"] = {key: report[key] for key in PERFORMANCE_FIELDS}
    if report["peak_rss_kib"] * 1024 >= limit:
        raise ValueError("runner exceeded the allowed resident memory")
    if snapshot:
        if report["snapshot"]["sha256"] != sha(snapshot_path):
            raise ValueError("runtime snapshot hash differs from the actual saved bytes")
        preserved = directory / f"{label}-snapshot.jsonl"
        snapshot_path.rename(preserved)
        execution["snapshot"] = record(preserved)
    return report, execution


def pipeline_transfer_checks(report: dict, scenario: dict, reference: dict) -> dict:
    elapsed, checks, mismatches = 0, 0, []
    if len(scenario["steps"]) != len(reference["steps"]):
        raise ValueError("pipeline timing cases no longer describe the same transfer sequence")
    for index, (step, intended) in enumerate(zip(scenario["steps"], reference["steps"])):
        if step.get("drive", {}) != intended.get("drive", {}):
            raise ValueError("pipeline negative control changed data or clock requests")
        elapsed += step["ticks"]
        for name, expected in intended.get("expect", {}).items():
            if name not in ("q0", "q1"):
                continue
            actual = report["traces"][name][elapsed - 1]
            checks += 1
            if actual != expected:
                mismatches.append({"step": index, "output": name, "expected": expected, "actual": actual})
    if checks != 10:
        raise ValueError("pipeline must retain all ten settled transfer observations")
    return {"settled_transfer_checks": checks, "missed_transfers": mismatches}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--fixture-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--memory-mib", type=int, default=200)
    args = parser.parse_args(argv)
    if not 32 <= args.memory_mib <= 200:
        raise ValueError("this check is limited to 32..200 MiB per process")
    args.output.mkdir(parents=True, exist_ok=False)
    evidence = {"schema": "pending-queue-native-differential-v1", "status": "running",
        "created_utc": datetime.now(timezone.utc).isoformat(), "baseline_binary": record(args.baseline),
        "candidate_binary": record(args.candidate), "source_sha256": {}, "cases": [],
        "excluded_process_performance_fields": [*PERFORMANCE_FIELDS, "snapshot.seconds"],
        "address_space_limit_mib": args.memory_mib, "complete_machine_runtime_verified": False,
        "scope": "Identical small saved-world traces and state; no complete-machine timing or speed claim",
        "comparison_negative_cases_rejected": check_comparison_rejects_changed_semantics()}
    for path in [Path(__file__), *(REPOSITORY / "tests/mchprs_sparse_runtime" / name for name in
            ("src/main.rs", "src/pending.rs", "src/snapshot.rs", "Cargo.toml", "Cargo.lock", "native-terrain-registry.patch"))]:
        evidence["source_sha256"][str(path.resolve().relative_to(REPOSITORY))] = sha(path)

    def save():
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")

    fixtures = [(name, args.fixture_root / "fixtures" / name / "world.jsonl",
                 args.fixture_root / "fixtures" / name / "scenario.json", name == "memory")
                for name in ("logic", "memory", "console", "console-distant", "upright")]
    pipeline = args.fixture_root / "connected-pipeline-native-v2"
    fixtures += [(f"pipeline-{period}", pipeline / "world.jsonl", pipeline / f"pipeline-{period}-scenario.json", False)
                 for period in (512, 20)]
    positive = json.loads((pipeline / "pipeline-512-scenario.json").read_text())
    save()
    try:
        for name, stream, scenario, snapshot in fixtures:
            directory = args.output / name
            directory.mkdir()
            case = {"name": name, "world_stream": record(stream), "scenario": record(scenario), "status": "running"}
            evidence["cases"].append(case)
            save()
            before, case["baseline"] = run(args.baseline, stream, scenario, directory, "baseline", args.memory_mib << 20, snapshot)
            after, case["candidate"] = run(args.candidate, stream, scenario, directory, "candidate", args.memory_mib << 20, snapshot)
            case.update(compare_reports(before, after))
            case["assertions"] = before["assertions"]
            case["ticks"] = before["ticks"]
            case["scheduled_ticks_total"] = before["scheduled_ticks_total"]
            case["peak_pending_ticks"] = before["peak_pending_ticks"]
            case["pending_ticks_after_scenario"] = before["pending_ticks_after_scenario"]
            if snapshot:
                if case["baseline"]["snapshot"]["sha256"] != case["candidate"]["snapshot"]["sha256"]:
                    raise ValueError("quiescent snapshot bytes differ")
                case["exact_snapshot_bytes_equal"] = True
                case["snapshot_state"] = {key: before["snapshot"][key] for key in
                    ("chunks", "sections", "nonair", "comparators", "pending_scheduled_ticks")}
            if name.startswith("pipeline-"):
                case["pipeline_transfer_check"] = pipeline_transfer_checks(after, json.loads(scenario.read_text()), positive)
                failed = bool(case["pipeline_transfer_check"]["missed_transfers"])
                if failed != (name == "pipeline-20"):
                    raise ValueError("positive or intentionally too-fast pipeline changed its timing behavior")
            case["status"] = "identical"
            save()
            print(f"{name}: {case['exact_trace_samples']} identical trace samples", flush=True)
            del before, after
        for name, path in (("baseline_binary", args.baseline), ("candidate_binary", args.candidate)):
            if record(path) != evidence[name]:
                raise ValueError("runner binary changed during validation")
        for path, expected in evidence["source_sha256"].items():
            if sha(REPOSITORY / path) != expected:
                raise ValueError("runtime or validation source changed during validation")
        evidence["status"] = "passed"
        evidence["exact_trace_samples"] = sum(case["exact_trace_samples"] for case in evidence["cases"])
        evidence["assertions"] = sum(case["assertions"] for case in evidence["cases"])
        save()
    except Exception as error:
        evidence.update(status="failed", error_type=type(error).__name__, error=str(error))
        save()
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
