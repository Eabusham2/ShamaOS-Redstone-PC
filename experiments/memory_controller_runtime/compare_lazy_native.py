"""Compare exact lazy section storage with the frozen native runner.

The only additional excluded field is the measured allocated palette storage.
Its difference must be exactly the original 2,052 bytes per imported uniform
section. Every circuit observation, imported count, scheduled event, complete
trace, quiescent snapshot and deliberately too-fast transfer is still compared.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from tools import check_pending_queue_equivalence as common


HERE = Path(__file__).resolve().parent


def uniform_stream(source: Path, destination: Path) -> dict:
    """Use v2's exact single-state representation for existing v1 fixtures."""
    converted = 0
    records = 0
    with source.open() as before, destination.open("x") as after:
        for line in before:
            record = json.loads(line)
            if record["kind"] == "header":
                if records or record["schema"] != "shama-native-anvil-sections-v1":
                    raise ValueError("comparison fixture must have one original v1 header")
                record["schema"] = "shama-native-anvil-sections-v2"
            elif record["kind"] == "section" and len(record["palette"]) == 1:
                if record["bits"] != 4 or len(record["data"]) != 256 or any(record["data"]):
                    raise ValueError("single-state fixture section contains an invalid palette index")
                record = {"kind": "uniform_section", "chunk": record["chunk"], "y": record["y"],
                          "state": record["palette"][0], "nonair": record["nonair"]}
                converted += 1
            after.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
            records += 1
    if not converted:
        raise ValueError("storage comparison must actually exercise compact uniform sections")
    return {"source": common.record(source), "converted_uniform_sections": converted,
            "unchanged_record_count": records, "every_single_state_index_verified": True}


def compare_reports(before: dict, after: dict) -> dict:
    old_bytes = before["paletted_storage_bytes_before_updates"]
    new_bytes = after["paletted_storage_bytes_before_updates"]
    expected = before["uniform_sections"] * (256 * 8 + 4)
    if old_bytes - new_bytes != expected:
        raise ValueError("lazy storage change does not equal all exact uniform-section arrays")
    # This field measures representation, rather than imported state. Verify
    # the exact allocation difference above before using the common comparator.
    normalized = dict(after, paletted_storage_bytes_before_updates=old_bytes)
    result = common.compare_reports(before, normalized)
    result.update(baseline_palette_bytes=old_bytes, candidate_palette_bytes=new_bytes,
                  uniform_array_bytes_avoided=expected)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--fixture-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=False)
    sources = [Path(__file__), Path(common.__file__), *sorted((HERE / "lazy_native_runner").rglob("*.rs")),
               HERE / "lazy_native_runner/Cargo.toml", HERE / "lazy_native_runner/Cargo.lock",
               common.REPOSITORY / "tests/mchprs_sparse_runtime/native-terrain-registry.patch"]
    evidence = {
        "schema": "exact-lazy-native-sections-differential-v1", "status": "running",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "baseline_binary": common.record(args.baseline), "candidate_binary": common.record(args.candidate),
        "source_sha256": {str(path.relative_to(common.REPOSITORY)): common.sha(path) for path in sources},
        "address_space_limit_mib": 200, "cpu_seconds_per_process": 30, "wall_seconds_per_process": 45,
        "excluded_process_performance_fields": [*common.PERFORMANCE_FIELDS, "snapshot.seconds"],
        "storage_representation_check": "Exactly 2,052 fewer allocated palette bytes per uniform section; every other field compared",
        "comparison_negative_cases_rejected": common.check_comparison_rejects_changed_semantics(),
        "cases": [], "complete_machine_runtime_verified": False,
        "scope": "Exact native World storage comparison on saved fixtures; complete cache clocks and connected memory remain separate gates",
    }

    def save():
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")

    fixtures = [(name, args.fixture_root / "fixtures" / name / "world.jsonl",
                 args.fixture_root / "fixtures" / name / "scenario.json", name == "memory")
                for name in ("logic", "memory", "console", "console-distant", "upright")]
    pipeline = args.fixture_root / "connected-pipeline-native-v2"
    fixtures.extend((f"pipeline-{period}", pipeline / "world.jsonl",
                     pipeline / f"pipeline-{period}-scenario.json", False) for period in (512, 20))
    positive = json.loads((pipeline / "pipeline-512-scenario.json").read_text())
    save()
    try:
        for encoding in ("legacy-v1", "uniform-v2"):
            for name, original_stream, scenario, snapshot in fixtures:
                directory = args.output / f"{encoding}-{name}"
                directory.mkdir()
                case = {"name": name, "encoding": encoding, "scenario": common.record(scenario), "status": "running"}
                if encoding == "uniform-v2":
                    stream = directory / "world.jsonl"
                    case["stream_conversion"] = uniform_stream(original_stream, stream)
                else:
                    stream = original_stream
                case["world_stream"] = common.record(stream)
                evidence["cases"].append(case)
                save()
                before, case["baseline"] = common.run(args.baseline, stream, scenario, directory, "baseline", 200 << 20, snapshot)
                after, case["candidate"] = common.run(args.candidate, stream, scenario, directory, "candidate", 200 << 20, snapshot)
                case.update(compare_reports(before, after))
                case.update({key: before[key] for key in ("assertions", "ticks", "scheduled_ticks_total", "peak_pending_ticks", "pending_ticks_after_scenario")})
                if snapshot:
                    if case["baseline"]["snapshot"]["sha256"] != case["candidate"]["snapshot"]["sha256"]:
                        raise ValueError("native snapshot bytes changed")
                    case["exact_snapshot_bytes_equal"] = True
                if name.startswith("pipeline-"):
                    case["pipeline_transfer_check"] = common.pipeline_transfer_checks(after, json.loads(scenario.read_text()), positive)
                    if bool(case["pipeline_transfer_check"]["missed_transfers"]) != (name == "pipeline-20"):
                        raise ValueError("positive or intentionally too-fast native transfer changed")
                case["status"] = "identical"
                save()
                print(f"{encoding}/{name}: {case['exact_trace_samples']} identical samples; {case['uniform_array_bytes_avoided']} uniform bytes avoided", flush=True)
                del before, after
        for key, path in (("baseline_binary", args.baseline), ("candidate_binary", args.candidate)):
            if common.record(path) != evidence[key]:
                raise ValueError("runner binary changed during comparison")
        for relative, expected in evidence["source_sha256"].items():
            if common.sha(common.REPOSITORY / relative) != expected:
                raise ValueError("comparison source changed during execution")
        evidence.update(status="passed", exact_trace_samples=sum(case["exact_trace_samples"] for case in evidence["cases"]),
                        assertions=sum(case["assertions"] for case in evidence["cases"]))
        save()
    except Exception as error:
        evidence.update(status="failed", error_type=type(error).__name__, error=str(error))
        save()
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
