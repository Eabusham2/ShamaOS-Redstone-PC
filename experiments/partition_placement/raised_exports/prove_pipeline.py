"""Retain complete native/dense fixtures and every scheduled pipeline sample."""
if not __debug__:
    raise RuntimeError("physical proof entrypoints require assertions; do not run with Python -O")


from dataclasses import asdict
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess

from shamaos.hardware.partition_planner import Graph, build_plan
from shamaos.hardware.partition_placement import (
    _literal_states, audit_placement, balance_partition_clocks, place_partition,
)
from test_partition_placement import partition_pipeline_case, analyze_pipeline_trace

parser = argparse.ArgumentParser()
parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[3] / "build/partition-placement/raised-exports/pipeline")
parser.add_argument("--mapped", type=Path, default=Path(__file__).parent / "evidence/pipeline-mapped.json")
parser.add_argument("--domain-budget", type=int, default=5)
parser.add_argument("--runner", default=os.environ.get("SHAMA_MCHPRS_RUNNER"))
args = parser.parse_args()
root = args.output
root.mkdir(parents=True, exist_ok=True)
(root / "mapped.json").write_bytes(args.mapped.read_bytes())
runner = args.runner
if not runner:
    parser.error("set SHAMA_MCHPRS_RUNNER or pass --runner with the actual pinned redstone engine")
graph = Graph.read(root / "mapped.json", "pipeline")
plan = build_plan(graph, cell_budget=1, boundary_budget=8, domain_budget=args.domain_budget, arity=4)
(root / "plan.json").write_text(json.dumps(plan, indent=2) + "\n")
placed = place_partition(graph, plan)
states = _literal_states(placed.iter_placements())
manifest = {
    "status": "connected-literal-geometry;runtime-evidence-in-separate-reports",
    "topology_sha256": plan["summary"]["topology_sha256"],
    "structural_audit": audit_placement(graph, plan, placed),
    "clock_matching": balance_partition_clocks(plan, placed),
    "bounds": asdict(placed.bounds), "unique_blocks": len(states),
    "occupied_chunks": len({(point.x // 16, point.z // 16) for point in states}),
    "original_ports": {str(entity): asdict(point) for entity, point in placed.ports.items()},
    "terminals": {name: asdict(point) for name, point in placed.terminals.items()},
    "shared_contacts": {name: {**record, "position": asdict(record["position"])}
                        for name, record in placed.shared_contacts.items()},
    "modules": [{"key": list(key), "top": tile.physical.top,
                 "cell_origins": {name: asdict(point) for name, point in tile.physical.cell_origins.items()},
                 "cell_types": tile.physical.cell_types,
                 "input_ports": {name: [asdict(point) for point in pins] for name, pins in tile.physical.input_ports.items()},
                 "output_ports": {name: [asdict(point) for point in pins] for name, pins in tile.physical.output_ports.items()},
                 "nets": [asdict(net) for net in tile.physical.nets],
                 "constant_high": [asdict(point) for point in tile.physical.constant_high],
                 "north_constant_high": [asdict(point) for point in tile.physical.north_constant_high],
                 "clock_delays": tile.clock_delays}
                for key, tile in placed.all_modules().items()],
}
(root / "placement.json").write_text(json.dumps(manifest, indent=2) + "\n")
summaries = []
coord = lambda point: [point.x, point.y, point.z]
for period in (512, 20):
    moved, inputs, outputs, steps, expected, delta = partition_pipeline_case(
        graph, plan, placed, period, verify_outputs=period == 512)
    case = {"blocks": [{"pos": coord(p.pos), "name": p.block.name, "properties": dict(p.block.properties)}
                       for p in moved.iter_placements()],
            "inputs": {name: coord(point) for name, point in inputs.items()},
            "outputs": {name: coord(point) for name, point in outputs.items()},
            "steps": steps, "initial_ticks": 1024, "capture_trace": True}
    path = root / f"pipeline-{period}-dense-case.json"
    path.write_text(json.dumps(case, separators=(",", ":")) + "\n")
    result = subprocess.run([runner], input=path.read_bytes(), capture_output=True, check=True)
    data = json.loads(result.stdout)
    report = analyze_pipeline_trace(data, expected, period)
    assert bool(report["pipeline_failures"]) == (period == 20)
    report.update(native_to_dense_translation=asdict(delta), expected_transfers=expected,
                  topology_sha256=plan["summary"]["topology_sha256"],
                  dense_case_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                  scope="all literal native cells and all 18 parent-child handoffs; two-level proof only")
    (root / f"pipeline-{period}-runtime.json").write_text(json.dumps(report, indent=2) + "\n")
    (root / f"pipeline-{period}-dense-result.json.gz").write_bytes(gzip.compress(result.stdout, mtime=0))
    summaries.append(report)
print(json.dumps(summaries, indent=2))
