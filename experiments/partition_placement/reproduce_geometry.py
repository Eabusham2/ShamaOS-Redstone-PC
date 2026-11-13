"""Reproduce complete ABC subtrees with every native cell and boundary retained.

This diagnostic does not select bounded placement in the production generator.
A failed route is reported and returns a nonzero exit status. No block map or
opaque binary capture is needed to retain its progress and exact source hashes.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import resource
import sys
import time
import traceback

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY / "src"))

from shamaos.hardware.partition_planner import Graph, audit_plan, build_plan
from shamaos.hardware.partition_placement import (
    _Context, _leaf, _literal_states, _parent, audit_placement,
    balance_partition_clocks,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mapped", type=Path,
                        default=REPOSITORY / "tests/fixtures/display_bridge_abc.json.gz")
    parser.add_argument("--plan", type=Path, help="Optional complete, independently audited alternative plan")
    parser.add_argument("--cluster", type=int, help="Omit to attempt the entire mapped display bridge")
    parser.add_argument("--balance-clocks", action="store_true",
                        help="Attempt complete-bridge clock delay matching after geometry; not runtime proof")
    parser.add_argument("--literal-union", action="store_true",
                        help="Materialize the final block union for an independent count; uses more memory")
    parser.add_argument("--parent-north-lanes", type=int,
                        help="Diagnostic bounded-parent override;128 reproduces the retained earlier limit")
    parser.add_argument("--output", type=Path,
                        default=REPOSITORY / "build/partition-placement/geometry.json")
    args = parser.parse_args()
    if args.parent_north_lanes is not None and args.parent_north_lanes < 1:
        parser.error("--parent-north-lanes must be a positive integer")
    if args.parent_north_lanes is not None:
        # Explicit diagnostic policy only; the source/default global router is
        # untouched, and reports retain this override with their source hashes.
        from shamaos.hardware import partition_placement
        plan_domains = partition_placement.plan_bounded_domains

        def diagnostic_domains(*arguments, **keywords):
            keywords["north_lane_limit"] = args.parent_north_lanes
            return plan_domains(*arguments, **keywords)

        partition_placement.plan_bounded_domains = diagnostic_domains
    graph = Graph.read(args.mapped, "shama_display_bridge")
    plan = json.loads(args.plan.read_bytes()) if args.plan else build_plan(graph)
    audit_plan(graph, plan)
    context = _Context(plan, -61, 319)
    cluster_id = args.cluster if args.cluster is not None else plan["root_cluster"]
    if cluster_id not in context.clusters:
        parser.error(f"cluster {cluster_id} is absent from the complete graph")
    maximum_y = 319
    ancestor = context.clusters[cluster_id]["parent"]
    while ancestor is not None:
        maximum_y -= context.route_height(ancestor)
        ancestor = context.clusters[ancestor]["parent"]
    sources = ["src/shamaos/hardware/partition_placement.py",
               "src/shamaos/hardware/partition_planner.py",
               "src/shamaos/hardware/local_router.py",
               "src/shamaos/hardware/external_router.py",
               "src/shamaos/hardware/logic.py",
               "src/shamaos/hardware/routing.py",
               "src/shamaos/hardware/memory.py", "src/shamaos/synthesis.py"]
    report = {
        "scope": "Complete selected subtree geometry; whole-computer routing and runtime are separate gates",
        "topology_sha256": plan["summary"]["topology_sha256"],
        "mapped_source": graph.source,
        "source_hashes": {name: hashlib.sha256((REPOSITORY / name).read_bytes()).hexdigest() for name in sources},
        "cluster_id": cluster_id, "complete_partition": cluster_id == plan["root_cluster"],
        "runtime_verified": False, "whole_computer_verified": False,
        "diagnostic_parent_north_lane_limit": args.parent_north_lanes,
    }
    records = []
    started = time.monotonic()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    progress = args.output.with_name(args.output.stem + "-progress.json")

    def visit(selected: int, limit: int):
        begin = time.monotonic()
        cluster = context.clusters[selected]
        if cluster["children"]:
            children = tuple(visit(child, limit - context.route_height(selected)) for child in cluster["children"])
            placed = _parent(context, selected, children, limit)
        else:
            placed = _leaf(context, selected)
            if placed.bounds.max.y > limit:
                raise ValueError("reserved ancestor planes leave insufficient above-grass leaf height")
        modules = placed.all_modules()
        record = {
            "cluster_id": selected, "leaf": not cluster["children"],
            "native_cells": sum(len(tile.physical.cell_types) for tile in modules.values()),
            "computed_unique_blocks": placed.unique_blocks, "bounds": asdict(placed.bounds),
            "modules": len(modules), "shared_contacts": len(placed.shared_contacts),
            "packing": placed.packing, "seconds": time.monotonic() - begin,
        }
        records.append(record)
        progress.write_text(json.dumps(records, indent=2) + "\n")
        if cluster["children"] or len(records) % 20 == 0:
            print(json.dumps(record), flush=True)
        return placed

    try:
        placed = visit(cluster_id, maximum_y)
        modules = placed.all_modules()
        report.update(
            status="connected-subtree-geometry-passed-runtime-unverified",
            computed_unique_blocks=placed.unique_blocks, bounds=asdict(placed.bounds),
            modules=len(modules), shared_contacts=len(placed.shared_contacts),
            native_cells=sum(len(tile.physical.cell_types) for tile in modules.values()),
        )
        if report["complete_partition"]:
            report["structural_audit"] = audit_placement(graph, plan, placed)
            if args.balance_clocks:
                report["clock_matching"] = balance_partition_clocks(plan, placed)
        if args.literal_union:
            states = _literal_states(placed.iter_placements())
            if len(states) != placed.unique_blocks:
                raise ValueError("materialized block union differs from the complete recursive count")
            report["literal_unique_blocks"] = len(states)
            report["occupied_chunks"] = len({(point.x // 16, point.z // 16) for point in states})
    except (ValueError, KeyError) as error:
        traceback.print_exc()
        report.update(status="failed", error=str(error), completed_clusters=len(records))
    report["seconds"] = time.monotonic() - started
    report["peak_rss_kib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2), flush=True)
    return 1 if report["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
