"""Run an isolated bounded-router column search with all native geometry."""
from __future__ import annotations

if not __debug__:
    raise RuntimeError("Column-search acceptance requires assertions; -O/-OO is unsupported")

from dataclasses import asdict
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

from shamaos.hardware import external_router, local_router
from shamaos.hardware.partition_planner import Graph
from shamaos.model import Vec3
from test_partition_placement import partition_pipeline_case, analyze_pipeline_trace
from test_mchprs_runtime import run_case


DIRECTORY = Path(__file__).resolve().parent
ARTIFACTS = DIRECTORY.parents[1] / "partition-placement/raised-exports-measured/pipeline"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main():
    output = DIRECTORY / "unit-step-pipeline"
    output.mkdir(exist_ok=True)
    unit = load("shamaos.hardware._unit_step_column_router", DIRECTORY / "external_router_unit.py")
    p = Vec3(2, 1, 20)
    kwargs = dict(access={p: ("north",)}, reserved_branch_x=(0, 8))
    old = external_router._plan_escapes([p], 5, (), **kwargs)
    new = unit._plan_escapes([p], 5, (), **kwargs)
    assert (old[p].branch_x, new[p].branch_x) == (14, 3)
    assert unit._plan_escapes([p], 5, (), **kwargs) == new
    for planner in (external_router._plan_escapes, unit._plan_escapes):
        try:
            planner([p], 5, (), minimum_y=1, **kwargs)
        except ValueError:
            pass
        else:
            raise RuntimeError("minimum-height rejection disappeared")
    graph = Graph.read(ARTIFACTS / "mapped.json", "pipeline")
    plan = json.loads((ARTIFACTS / "plan.json").read_text())
    placement = load("shamaos.hardware._unit_step_placement", DIRECTORY / "partition_placement_snapshot.py")
    baseline = placement.place_partition(graph, plan)
    baseline_states = placement._literal_states(baseline.iter_placements())
    # Process-local change only: production/global routing entrypoints and
    # the other agent's active process continue using their original binding.
    local_router._plan_escapes = unit._plan_escapes
    placed = placement.place_partition(graph, plan)
    states = placement._literal_states(placed.iter_placements())
    summary = {"scope": "complete retained small fixture only; larger layouts and global timing remain unverified",
               "provenance": json.loads((DIRECTORY / "provenance.json").read_text()),
               "actual_gap_example": {"reserved_columns": [0, 8], "initial_candidate": 2,
                                      "old_selected_column": old[p].branch_x, "new_selected_column": new[p].branch_x},
               "baseline": {"unique_blocks": len(baseline_states),
                            "occupied_chunks": len({(p.x // 16, p.z // 16) for p in baseline_states}),
                            "bounds": asdict(baseline.bounds)},
               "candidate": {"unique_blocks": len(states),
                             "occupied_chunks": len({(p.x // 16, p.z // 16) for p in states}),
                             "bounds": asdict(placed.bounds)},
               "structural_audit": placement.audit_placement(graph, plan, placed),
               "runtime": []}
    for period in (512, 20):
        moved, inputs, outputs, steps, expected, delta = partition_pipeline_case(
            graph, plan, placed, period, verify_outputs=period == 512)
        result = run_case(moved.iter_placements(), inputs, outputs, steps, initial_ticks=1024, capture_trace=True)
        report = analyze_pipeline_trace(result, expected, period)
        assert bool(report["pipeline_failures"]) == (period == 20), report
        report.update(native_to_dense_translation=asdict(delta), engine_revision=result["revision"])
        (output / f"pipeline-{period}-runtime.json").write_text(json.dumps(report, indent=2) + "\n")
        (output / f"pipeline-{period}-trace.json.gz").write_bytes(gzip.compress(json.dumps(result).encode(), mtime=0))
        summary["runtime"].append(report)
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
