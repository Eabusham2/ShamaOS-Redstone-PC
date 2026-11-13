"""Compare the deadline-fixed wrapper with a retained small native execution.

Usage: python check.py CHECKOUT BASELINE_REVIEW_JSON OUTPUT_DIRECTORY
The baseline report supplies exact runner, stream, scenario, output and snapshot
identities. This comparison never proves the complete connected cache works.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys


checkout, baseline_path, destination = (Path(value).resolve() for value in sys.argv[1:])
sys.path[:0] = [str(checkout / "src"), str(checkout)]
from experiments.memory_controller_runtime import run_connected as runtime


destination.mkdir(parents=True, exist_ok=False)
baseline = json.loads(baseline_path.read_text())
for key in ("runner", "stream", "scenario"):
    runtime._same(baseline[key])
for key in ("stdout", "stderr", "snapshot_file"):
    runtime._same(baseline["process"][key])
source = runtime._record(Path(runtime.__file__))
helpers = runtime._helper_sources()
runner, stream, scenario = (Path(baseline[key]["path"]) for key in ("runner", "stream", "scenario"))
scenario_document = json.loads(scenario.read_text())
with stream.open() as handle:
    header = json.loads(next(handle))
    for line in handle:
        footer = json.loads(line)
assert header["kind"] == "header" and footer["kind"] == "complete"
imported = {**{key: value for key, value in footer.items() if key != "kind"},
            "source": header["source"], "stream_sha256": runtime._sha(stream)}
process = runtime.execute(runner, stream, scenario, destination, "small-native", imported["chunks"] + 128,
                          memory_limit_mib=512, wall_seconds=60, snapshot=True)
assert process["exit_code"] == 0 and runtime._completed_before_wall_deadline(process), process
actual = json.loads(Path(process["stdout"]["path"]).read_text())
old = json.loads(Path(baseline["process"]["stdout"]["path"]).read_text())
expected_assertions = sum(len(step.get("expect", {})) for step in scenario_document["steps"])
expected_ticks = scenario_document["initial_ticks"] + sum(step["ticks"] for step in scenario_document["steps"])
runtime._validate_result(actual, imported, runtime._record(scenario), len(scenario_document["steps"]),
                         expected_ticks, expected_assertions)


def semantic(result):
    result = deepcopy(result)
    for key in ("import_seconds", "initialization_seconds", "runtime_seconds", "peak_rss_kib"):
        result.pop(key)
    for key in ("seconds", "path"):
        result["snapshot"].pop(key)
    return result


assert semantic(actual) == semantic(old)
assert process["snapshot_file"]["sha256"] == baseline["process"]["snapshot_file"]["sha256"]
runtime._same(source)
runtime._check_helpers(helpers)
review = {
    "status": "passed",
    "scope": "Real small native fixture through the corrected process deadline monitor; all native semantics and exact snapshot bytes compared",
    "source": source, "runtime_helper_sources": helpers,
    "historical_wrapper_source": baseline["source"],
    "baseline_review": runtime._record(baseline_path),
    "harness": runtime._record(Path(__file__)),
    "runner": runtime._record(runner), "stream": runtime._record(stream), "scenario": runtime._record(scenario),
    "process": process,
    "all_semantic_fields_and_traces_equal": True,
    "trace_samples_compared": sum(len(values) for values in actual["traces"].values()),
    "declared_assertions": expected_assertions, "snapshot_bytes_identical": True,
    "ignored_fields": ["import_seconds", "initialization_seconds", "runtime_seconds", "peak_rss_kib",
                       "snapshot.seconds", "snapshot.path"],
    "connected_cache_runtime_verified": False, "complete_machine_runtime_verified": False,
}
(destination / "review.json").write_text(json.dumps(review, indent=2) + "\n")
print(json.dumps({key: review[key] for key in ("status", "trace_samples_compared", "declared_assertions",
                                              "snapshot_bytes_identical")}, indent=2))
