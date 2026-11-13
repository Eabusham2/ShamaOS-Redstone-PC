"""Place a complete mapped partition with real original-I/O delivery corridors."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import resource
import signal
import sys
import time
import traceback

if not __debug__:
    raise RuntimeError("physical placement proof requires assertions")
REPOSITORY = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPOSITORY), str(REPOSITORY / "src")]

from experiments.memory_controller_runtime.clock_reserve import reserve_clock_tracks
from experiments.partition_placement.port_egress import audit_egress, place_with_egress
from experiments.partition_placement.port_egress_manifest import write_egress_manifest
from experiments.partition_placement.targeted_clock_reserve import parse_domain_padding, reserve_targeted_clock_tracks
from experiments.partition_placement.raised_exports.streamed_union import literal_union
from shamaos.hardware.partition_placement import audit_placement, balance_partition_clocks
from shamaos.hardware.partition_planner import Graph, audit_plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mapped", type=Path, required=True)
    parser.add_argument("--top", required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--clock-padding", type=int, default=128)
    parser.add_argument("--clock-domain-padding", action="append", default=[], metavar="DOMAIN=ABSOLUTE_BLOCKS")
    parser.add_argument("--maximum-y", type=int, default=285)
    parser.add_argument("--memory-limit-mib", type=int, default=1228)
    parser.add_argument("--wall-seconds", type=int, default=1800)
    parser.add_argument("--literal-union", action="store_true")
    args = parser.parse_args()
    try:
        domain_padding = parse_domain_padding(args.clock_domain_padding, args.clock_padding)
    except ValueError as error:
        parser.error(str(error))
    if args.memory_limit_mib < 128 or args.wall_seconds < 1:
        parser.error("explicit positive wall and at least128MiB memory bounds are required")
    memory_bytes = args.memory_limit_mib * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
    def timeout(*unused):
        raise TimeoutError("complete facade placement exceeded its explicit wall limit")
    signal.signal(signal.SIGALRM, timeout)
    signal.alarm(args.wall_seconds)
    started = time.monotonic()
    names = ["src/shamaos/model.py", "src/shamaos/synthesis.py",
             "src/shamaos/hardware/partition_planner.py", "src/shamaos/hardware/partition_placement.py",
             "src/shamaos/hardware/local_router.py", "src/shamaos/hardware/external_router.py",
             "src/shamaos/hardware/memory.py", "src/shamaos/hardware/routing.py", "src/shamaos/hardware/logic.py",
             "experiments/memory_controller_runtime/clock_reserve.py",
             "experiments/partition_placement/port_egress.py", "experiments/partition_placement/port_egress_manifest.py",
             "experiments/partition_placement/packed_parent_union.py",
             "experiments/partition_placement/targeted_clock_reserve.py",
             "experiments/partition_placement/raised_exports/replay_manifest.py",
             "experiments/partition_placement/raised_exports/streamed_union.py",
             "experiments/partition_placement/run_port_egress.py"]
    bindings = {name: hashlib.sha256((REPOSITORY / name).read_bytes()).hexdigest() for name in names}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    progress_path = args.output.with_name(args.output.stem + "-progress.jsonl")
    if args.output.exists() or progress_path.exists():
        raise ValueError("use a fresh facade proof output path")
    report = {"scope": "Complete partition with actual original-I/O facade; connected memory and runtime remain separate gates",
              "source_hashes": bindings, "clock_padding_blocks": args.clock_padding,
              "clock_domain_padding": domain_padding,
              "memory_limit_mib": args.memory_limit_mib, "wall_limit_seconds": args.wall_seconds,
              "runtime_verified": False, "whole_computer_verified": False}
    try:
        graph = Graph.read(args.mapped, args.top)
        plan_bytes = args.plan.read_bytes()
        plan = json.loads(plan_bytes)
        report.update(mapped_source=graph.source, plan_sha256=hashlib.sha256(plan_bytes).hexdigest(),
                      topology_sha256=plan["summary"]["topology_sha256"])
        audit_plan(graph, plan)
        clock_bits = {bit for bit, value in graph.signals.items() if any(sink.pin == "C" for sink in value.sinks)}
        domains = {record["id"]: record for record in plan["routing_domains"]}
        if set(domain_padding) - set(domains):
            raise ValueError("targeted clock padding refers to an absent complete-plan domain")
        if any(not clock_bits.intersection(segment["net"] for segment in domains[identity]["segments"])
               for identity in domain_padding):
            raise ValueError("targeted clock padding refers to a domain with no requested clock")
        def progress(cluster):
            record = {"cluster": cluster.cluster_id, "elapsed_seconds": time.monotonic() - started,
                      "graph_modules": len(cluster.all_modules()), "facade_modules": len(cluster.all_facade_modules()),
                      "original_ports": len(cluster.ports), "exterior_ports": len(cluster.exposed_ports),
                      "unique_blocks": cluster.unique_blocks, "bounds": asdict(cluster.bounds),
                      "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
            line = json.dumps(record)
            with progress_path.open("a") as handle:
                handle.write(line + "\n")
            print(line, flush=True)
        reservation = (reserve_targeted_clock_tracks(clock_bits, args.clock_padding, domain_padding)
                       if domain_padding else reserve_clock_tracks(clock_bits, args.clock_padding))
        with reservation as applications:
            placed = place_with_egress(graph, plan, maximum_y=args.maximum_y, balance_clocks=False, progress=progress)
        report["clock_reservation_groups"] = list(applications.values()) if applications is not None else []
        report["graph_audit"] = audit_placement(graph, plan, placed)
        report["facade_audit"] = audit_egress(graph, placed)
        report.update(geometry_guards_passed=True, bounds=asdict(placed.bounds), computed_unique_blocks=placed.unique_blocks)
        provenance = {"source_hashes": bindings, "plan_sha256": report["plan_sha256"], "mapped_source": graph.source,
                      "clock_padding_blocks": args.clock_padding, "clock_domain_padding": domain_padding,
                      "clock_reservation_groups": report["clock_reservation_groups"]}
        report["unbalanced_manifest"] = write_egress_manifest(args.output.with_name(args.output.stem + "-unbalanced.json.gz"),
                    graph, plan, placed, provenance=dict(provenance, clock_balance_verified=False))
        report["clock_matching"] = balance_partition_clocks(plan, placed)
        report["balanced_manifest"] = write_egress_manifest(args.output.with_name(args.output.stem + "-balanced.json.gz"),
                    graph, plan, placed, provenance=dict(provenance, clock_balance_verified=True))
        if args.literal_union:
            union = literal_union(placed.iter_placements())
            if union["literal_unique_blocks"] != placed.unique_blocks or union["bounds"] != asdict(placed.bounds):
                raise ValueError("complete facade literal union differs from recursive geometry")
            report["literal_union"] = union
        for name, digest in bindings.items():
            if hashlib.sha256((REPOSITORY / name).read_bytes()).hexdigest() != digest:
                raise ValueError(f"source changed during facade proof: {name}")
        report["status"] = "complete-original-io-facade-geometry-passed-runtime-pending"
    except (Exception, KeyboardInterrupt) as error:
        status = "resource-incomplete" if isinstance(error, (MemoryError, TimeoutError)) else "interrupted" if isinstance(error, KeyboardInterrupt) else "failed"
        report.update(status=status, error_type=type(error).__name__,
                      error=str(error) or type(error).__name__, traceback=traceback.format_exc())
    finally:
        signal.alarm(0)
        report.update(seconds=time.monotonic() - started, peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps({key: report[key] for key in ("status", "seconds", "peak_rss_kib")}), flush=True)
    return 0 if report["status"] == "complete-original-io-facade-geometry-passed-runtime-pending" else 1


if __name__ == "__main__":
    raise SystemExit(main())
