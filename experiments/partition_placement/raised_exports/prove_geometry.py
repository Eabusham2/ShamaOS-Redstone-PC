"""Reproduce complete ABC subtrees with every native cell and boundary retained.

This diagnostic does not select bounded placement in the production generator.
A failed route is reported and returns a nonzero exit status. No block map or
opaque binary capture is needed to retain its progress and exact source hashes.
"""
from __future__ import annotations

if not __debug__:
    raise RuntimeError("physical proof entrypoints require assertions; do not run with Python -O")


import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import resource
import sys
import time
import traceback

REPOSITORY = Path(__file__).resolve().parents[3]
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
    parser.add_argument("--resume-apron-evidence", type=Path,
                        help="Retained source-bound completed parent emission used only as a starting-apron hint")
    parser.add_argument("--resume-apron-sha256",
                        help="Exact SHA-256 of --resume-apron-evidence; both arguments are required together")
    parser.add_argument("--output", type=Path,
                        default=REPOSITORY / "build/partition-placement/raised-exports/geometry.json")
    args = parser.parse_args()
    if bool(args.resume_apron_evidence) != bool(args.resume_apron_sha256):
        parser.error("--resume-apron-evidence and --resume-apron-sha256 are required together")
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
    report["experimental_geometry"] = json.loads((Path(__file__).parent / "provenance.json").read_bytes())
    report["effective_partition_placement_sha256"] = hashlib.sha256((Path(__file__).parent / "partition_placement.py").read_bytes()).hexdigest()
    report["proof_driver_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    report["streamed_union_sha256"] = hashlib.sha256((Path(__file__).parent / "streamed_union.py").read_bytes()).hexdigest()
    if args.resume_apron_evidence:
        from shamaos.hardware import partition_placement
        from apron_recovery import install_recovery_hint, load_recovery_hint
        hint = load_recovery_hint(args.resume_apron_evidence, args.resume_apron_sha256,
                                  report, context.clusters)
        hint["helper_sha256"] = hashlib.sha256((Path(__file__).parent / "apron_recovery.py").read_bytes()).hexdigest()
        report["apron_recovery"] = hint
        install_recovery_hint(partition_placement, hint)
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
        export_points = [point for tile in placed.modules.values()
                         for ports in (tile.physical.input_ports, tile.physical.output_ports)
                         for pins in ports.values() for point in pins]
        record["own_declared_export_points"] = len(export_points)
        if export_points:
            record["own_export_y_range"] = [min(p.y for p in export_points), max(p.y for p in export_points)]
            record["own_export_z_range"] = [min(p.z for p in export_points), max(p.z for p in export_points)]
            record["own_exports_at_south_envelope"] = all(p.z == placed.bounds.max.z for p in export_points)
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
        from geometry_manifest import write_manifest
        report["complete_geometry_manifest"] = write_manifest(
            args.output.with_name(args.output.stem + "-manifest.json.gz"), placed,
            provenance={"full_plan_topology_sha256": report["topology_sha256"],
                        "mapped_source": report["mapped_source"],
                        "source_hashes": report["source_hashes"],
                        "experimental_geometry": report["experimental_geometry"],
                        "effective_partition_placement_sha256": report["effective_partition_placement_sha256"],
                        "apron_recovery": report.get("apron_recovery")})
        if report["complete_partition"]:
            report["structural_audit"] = audit_placement(graph, plan, placed)
            if args.balance_clocks:
                report["clock_matching"] = balance_partition_clocks(plan, placed)
        if args.literal_union:
            from streamed_union import literal_union
            union = literal_union(placed.iter_placements())
            if union["literal_unique_blocks"] != placed.unique_blocks:
                raise ValueError("streamed block union differs from the complete recursive count")
            report["literal_unique_blocks"] = union["literal_unique_blocks"]
            report["occupied_chunks"] = union["occupied_chunks"]
            report["streamed_literal_union"] = union
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
