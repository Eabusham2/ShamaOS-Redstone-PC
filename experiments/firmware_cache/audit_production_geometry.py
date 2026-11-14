"""Regenerate the complete current cache through the production layout policy.

Compare every route-defining field with the independently union-checked frozen
controller. No full block union, native world or electrical run is repeated.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import resource
import signal
import sys
import tempfile
import time
import zipfile


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical(value):
    return json.loads(json.dumps(value))


def audit(output):
    from experiments.memory_controller_runtime import current_fixture
    from experiments.partition_placement.port_egress_manifest import read_egress_manifest, _tile_record
    from shamaos.hardware import cache_layout
    from shamaos.hardware.bounded_facade import audit_egress
    from shamaos.hardware.partition_placement import audit_placement

    if output.exists():
        raise FileExistsError(output)
    resource.setrlimit(resource.RLIMIT_AS, (2048 << 20, 2048 << 20))
    resource.setrlimit(resource.RLIMIT_CPU, (1800, 1800))
    signal.alarm(1800)
    started = time.monotonic()
    evidence = ROOT / "experiments/memory_controller_runtime/current_four_kib/evidence"
    receipt = json.loads((evidence / "receipt.json").read_text())
    bundle = ROOT / receipt["bundle"]["path"]
    assert sha(bundle) == receipt["bundle"]["sha256"]
    sources = {name: sha(ROOT / name) for name in (
        "experiments/firmware_cache/audit_production_geometry.py",
        "src/shamaos/hardware/cache_layout.py", "src/shamaos/hardware/bounded_facade.py",
        "src/shamaos/hardware/packed_parent_union.py", "src/shamaos/bounded_system.py")}
    report = {"schema": "current-cache-production-geometry-parity-v1", "status": "running",
        "scope": "Full fresh production controller placement compared field-for-field with retained exact-union geometry; native execution is a separate gate",
        "source_hashes": sources, "receipt_sha256": sha(evidence / "receipt.json"),
        "source_bundle_sha256": sha(bundle), "native_runtime_verified": False,
        "whole_machine_verified": False, "fresh_full_block_union": False}

    def save():
        report.update(elapsed_seconds=time.monotonic() - started,
                      peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(output.suffix + ".partial")
        temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        temporary.replace(output)

    save()
    try:
        with tempfile.TemporaryDirectory(prefix="cache-production-parity-") as folder:
            target = Path(folder)
            with zipfile.ZipFile(bundle) as archive:
                names = archive.namelist()
                assert len(names) == len(set(names)) == receipt["bundle"]["files"]
                assert set(names) == set(receipt["inventory"])
                for name in names:
                    path = target / name
                    assert path.resolve().is_relative_to(target)
                    data = archive.read(name)
                    assert hashlib.sha256(data).hexdigest() == receipt["inventory"][name]
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
            current = target / "inputs/current"
            prepared, graph, _, fabric, _ = current_fixture.load_prepared(current)
            expected_plan = json.loads((current / "plan.json").read_text())
            accepted = json.loads((current / "facade/report.json").read_text())
            for bindings in (prepared["generator_hashes"], accepted["source_hashes"]):
                for name, expected in bindings.items():
                    assert sha(target / name) == sha(ROOT / name) == expected
            for name, expected in prepared["input_hashes"].items():
                assert sha(ROOT / name) == expected
            sources.update({name: sha(ROOT / name) for name in
                            set(prepared["generator_hashes"]) | set(accepted["source_hashes"])})
            expected = read_egress_manifest(current / "facade/report-balanced.json.gz",
                accepted["balanced_manifest"]["sha256"], graph, expected_plan, source_root=target)
            print("Regenerating full production controller", flush=True)
            placement_started = time.monotonic()
            actual_plan, actual = cache_layout.place_compact_cache(graph)
            report["production_placement_seconds"] = time.monotonic() - placement_started
            print("Production placement completed; comparing all geometry fields", flush=True)
            for key in expected_plan:
                if key == "source":
                    identity = lambda value: {k: v for k, v in value.items() if k != "path"}
                    assert identity(actual_plan[key]) == identity(expected_plan[key])
                else:
                    assert canonical(actual_plan[key]) == expected_plan[key], key
            counts = {"clusters": 0, "graph_modules": 0, "facade_modules": 0}
            for name, original, result in (("graph_modules", expected.all_modules(), actual.all_modules()),
                                          ("facade_modules", expected.all_facade_modules(), actual.all_facade_modules())):
                assert original.keys() == result.keys(), name
                for key in original:
                    assert _tile_record(key, original[key]) == _tile_record(key, result[key]), (name, key)
                counts[name] = len(original)

            def compare_clusters(before, after):
                counts["clusters"] += 1
                for field in ("cluster_id", "bounds", "terminals", "ports", "unique_blocks",
                              "shared_contacts", "exposed_ports", "facade_contacts"):
                    assert getattr(before, field) == getattr(after, field), (before.cluster_id, field)
                assert before.modules.keys() == after.modules.keys()
                assert before.facade_modules.keys() == after.facade_modules.keys()
                packing = dict(after.packing)
                packing.pop("cache_layout_policy", None)
                assert canonical(before.packing) == canonical(packing), (before.cluster_id, "packing")
                assert len(before.children) == len(after.children)
                for left, right in zip(before.children, after.children):
                    compare_clusters(left, right)

            compare_clusters(expected, actual)
            graph_audit = audit_placement(graph, actual_plan, actual)
            facade_audit = audit_egress(graph, actual)
            assert graph_audit == accepted["graph_audit"]
            assert facade_audit == accepted["facade_audit"]
            policy = actual.packing["cache_layout_policy"]
            assert policy["clock_reservation_groups"] == accepted["clock_reservation_groups"]
            assert (fabric.total_bytes, fabric.bank_count) == (4096, 1)
            assert actual.unique_blocks == receipt["controller_unique_blocks"]
            assert sources == {name: sha(ROOT / name) for name in sources}
            report.update(status="full-production-controller-geometry-exactly-matches",
                matched=counts, graph_audit=graph_audit, facade_audit=facade_audit,
                policy=policy, bounds=asdict(actual.bounds), capacity_bytes=fabric.total_bytes,
                native_cells=graph.cell_count, original_port_bits=len(actual.ports),
                unique_blocks=actual.unique_blocks,
                retained_exact_union_occupied_chunks=receipt["controller_occupied_chunks"],
                retained_exact_union_sha256=receipt["controller_union_sha256"],
                accepted_manifest_sha256=accepted["balanced_manifest"]["sha256"],
                all_route_defining_fields_equal=True,
                full_block_union_inference="Identical native/route emitter sources and every module, route, override, contact, port and hierarchy field reproduce the retained exact block union; this run compares definitions, not 15.6 million emitted blocks")
            save()
    except BaseException as error:
        report.update(status="failed", error_type=type(error).__name__, error=str(error))
        save()
        raise
    finally:
        signal.alarm(0)
    print(json.dumps({key: report[key] for key in ("status", "matched", "native_cells",
        "unique_blocks", "elapsed_seconds", "peak_rss_kib")}, indent=2), flush=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    audit(args.output)
