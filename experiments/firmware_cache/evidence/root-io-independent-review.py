"""Audit frozen cache root-I/O evidence without emitting the large geometry.

Usage: PYTHONPATH=src python this_file.py --repository /path/to/checkout
The read-only review validates source bindings, raw graph inventory, both typed
manifests, and actual clock-path repeater sums. Large block/chunk totals remain
explicitly identified as retained generator evidence.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path
import resource
import sys
import tempfile
import time
from xml.etree import ElementTree
import zipfile


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    args = parser.parse_args()
    repository = args.repository.resolve()
    sys.path[:0] = [str(repository / "src"), str(repository)]
    resource.setrlimit(resource.RLIMIT_AS, (512 << 20, 512 << 20))
    started = time.monotonic()
    from shamaos.hardware.partition_planner import Graph, audit_plan, build_plan
    from shamaos.hardware.partition_placement import audit_placement, balance_partition_clocks
    from shamaos.hardware.external_router import _trunk
    from experiments.partition_placement.port_egress import audit_egress
    from experiments.partition_placement.port_egress_manifest import read_egress_manifest

    evidence = repository / "experiments/partition_placement/evidence"
    new = repository / "experiments/partition_placement/root_io/evidence"
    comparison = json.loads((new / "comparison.json").read_text())
    configurations = {
        "baseline": (evidence / "cache-facade-domain4-672-source-bundle.zip",
                     json.loads((evidence / "cache-facade-domain4-672-prepared.json").read_text())["source_bundle_sha256"],
                     evidence / "cache-facade-domain4-672-report.json"),
        "candidate": (new / "root-io-source-inputs.zip", comparison["source_inputs_bundle"]["sha256"],
                      new / "domain6-160-report.json"),
    }
    reports, graphs, plans = {}, {}, {}

    def raw_inventory(module, graph):
        drivers, sinks, constants = defaultdict(list), defaultdict(list), []
        expected_cells = {name: cell["type"].upper() for name, cell in module["cells"].items()}
        expected_ports = []

        def terminal(bit, endpoint, driving):
            if type(bit) is int:
                (drivers if driving else sinks)[bit].append(endpoint)
            else:
                assert bit in ("0", "1") and not driving
                constants.append((bit, endpoint))

        for name, cell in module["cells"].items():
            for pin, bits in cell["connections"].items():
                assert len(bits) == 1
                terminal(bits[0], (name, pin, 0), pin in ("Q", "Y"))
        for name, port in module["ports"].items():
            for index, bit in enumerate(port["bits"]):
                entity = f"port:{name}[{index}]"
                expected_ports.append((entity, name, index, port["direction"], bit))
                terminal(bit, (entity, name, index), port["direction"] == "input")
        assert expected_cells == {entity["name"]: entity["cell_type"] for entity in graph.entities if entity["kind"] == "cell"}
        actual_ports = [(entity["name"], entity["port_name"], entity["port_index"], entity["direction"], entity["bit"])
                        for entity in graph.entities if entity["kind"] == "port"]
        assert sorted(expected_ports) == sorted(actual_ports)
        endpoint = lambda point: (graph.entities[point.entity]["name"], point.pin, point.index)
        expected_signals = {}
        for bit, values in sinks.items():
            assert len(drivers[bit]) == 1
            expected_signals[bit] = (drivers[bit][0], sorted(values))
        actual_signals = {bit: (endpoint(signal.source), sorted(map(endpoint, signal.sinks)))
                          for bit, signal in graph.signals.items()}
        assert actual_signals == expected_signals
        assert sorted(constants) == sorted((value, endpoint(point)) for value, point in graph.constants)
        aliases = Counter(port[-1] for port in expected_ports if type(port[-1]) is int)
        return {"cells": len(expected_cells), "port_bits": len(expected_ports),
                "signals": len(expected_signals), "sink_uses": sum(map(len, sinks.values())),
                "constant_terminals": len(constants),
                "port_alias_groups": sum(count > 1 for count in aliases.values()),
                "unused_driver_bits": len(set(drivers) - set(sinks))}

    def physical_clock_arrivals(plan, placed):
        modules = placed.all_modules()
        entities = {record["id"]: record for record in plan["entities"]}
        result = {}

        def path_delays(tile, net):
            source_trunk, source_blocks = tile.endpoint_route(net, net.source)
            endpoints = {sink: tile.endpoint_route(net, sink) for sink in net.sinks}
            trunk = list(_trunk(source_trunk, tuple(point for point, _ in endpoints.values()), component="independent-clock-audit"))

            def delay(blocks):
                states = (tile.overrides.get(block.pos, block.block) for block in blocks)
                return sum(int(dict(state.properties)["delay"]) for state in states if state.name == "minecraft:repeater")

            paths = {}
            for sink, (endpoint_trunk, blocks) in endpoints.items():
                lo, hi = sorted((source_trunk.x, endpoint_trunk.x))
                paths[sink] = delay(source_blocks) + delay(blocks) + delay(block for block in trunk if lo < block.pos.x < hi)
            return paths

        for signal in plan["signals"]:
            clock_sinks = {entities[sink["entity"]]["name"] for sink in signal["sinks"]
                           if entities[sink["entity"]]["kind"] == "cell" and sink["pin"] == "C"}
            if not clock_sinks:
                continue
            links = {link["source"]["terminal_id"]: link for link in signal["boundary_links"]}
            arrivals, visited = {}, set()

            def visit(key, elapsed):
                assert key not in visited, "clock path repeated a module"
                visited.add(key)
                tile = modules[key]
                net = next(net for net in tile.physical.nets if net.bit == signal["bit"])
                for sink, delay in path_delays(tile, net).items():
                    if sink.pin in links:
                        end = links[sink.pin]["sink"]
                        visit(("leaf", end["cluster"]) if end["domain"] is None else ("domain", end["domain"]), elapsed + delay)
                    elif sink.pin == "C" and sink.name in clock_sinks:
                        assert sink.name not in arrivals
                        arrivals[sink.name] = elapsed + delay

            source_entity = signal["source"]["entity"]
            visit(("leaf", plan["entity_leaf"][source_entity]), 0)
            assert set(arrivals) == clock_sinks and len(set(arrivals.values())) == 1
            facade_delays = []
            for tile in placed.all_facade_modules().values():
                for net in tile.physical.nets:
                    if net.bit == source_entity:
                        assert len(net.sinks) == 1
                        facade_delays.extend(path_delays(tile, net).values())
            total = next(iter(arrivals.values()))
            result[str(signal["bit"])] = {"clock_leaves": len(arrivals), "all_original_pin_arrivals": total,
                                           "facade_common_prefix": sum(facade_delays),
                                           "all_exterior_arrivals": total + sum(facade_delays),
                                           "clock_facade_handoffs": len(facade_delays),
                                           "runtime_verified": False}
        return result

    with tempfile.TemporaryDirectory(prefix="root-io-review-") as folder:
        for label, (archive, expected_archive, report_path) in configurations.items():
            assert sha(archive.read_bytes()) == expected_archive
            report = json.loads(report_path.read_text())
            target = Path(folder) / label
            target.mkdir()
            with zipfile.ZipFile(archive) as bundle:
                names = bundle.namelist()
                assert len(names) == len(set(names))
                assert sum(info.file_size for info in bundle.infolist()) < 32 << 20
                for name in names:
                    assert not Path(name).is_absolute() and ".." not in Path(name).parts
                    path = target / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(bundle.read(name))
            for source, expected in report["source_hashes"].items():
                assert sha((target / source).read_bytes()) == expected
                if label == "candidate":
                    assert sha((repository / source).read_bytes()) == expected
            plan_path = target / "inputs/plan.json"
            assert sha(plan_path.read_bytes()) == report["plan_sha256"]
            plan = json.loads(plan_path.read_text())
            mapped = next(path for path in (target / "inputs").glob("*controller*.gz"))
            assert sha(mapped.read_bytes()) == report["mapped_source"]["container_sha256"]
            contents = gzip.decompress(mapped.read_bytes())
            assert sha(contents) == report["mapped_source"]["sha256"]
            graph = Graph.read(mapped, "shama_cache_adapter")
            raw = raw_inventory(json.loads(contents)["modules"]["shama_cache_adapter"], graph)
            assert audit_plan(graph, plan)["status"] == "passed"
            manifest = target / "inputs/balanced.json.gz" if label == "candidate" else evidence / "cache-facade-domain4-672-balanced-manifest.json.gz"
            placed = read_egress_manifest(manifest, report["balanced_manifest"]["sha256"], graph, plan, source_root=target)
            structural, facade = audit_placement(graph, plan, placed), audit_egress(graph, placed)
            snapshot = lambda: {key: (dict(tile.overrides), deepcopy(tile.clock_delays), deepcopy(tile._balanced_positions))
                                for key, tile in placed.all_modules().items()}
            before = snapshot()
            matching = balance_partition_clocks(plan, placed)
            assert before == snapshot()
            assert {str(key): value for key, value in matching.items()} == report["clock_matching"]
            arrivals = physical_clock_arrivals(plan, placed)
            for bit, clocks in arrivals.items():
                assert clocks["all_original_pin_arrivals"] == report["clock_matching"][bit]["balanced_repeater_delay"]
            union = report["literal_union"]
            assert union["emitted_blocks"] == union["merged_records"] == union["literal_unique_blocks"] + union["identical_duplicate_blocks"]
            assert placed.unique_blocks == report["computed_unique_blocks"] == union["literal_unique_blocks"]
            assert report["status"] == "complete-original-io-facade-geometry-passed-runtime-pending"
            reports[label] = {"bundle_sha256": expected_archive, "source_bindings_verified": len(report["source_hashes"]),
                              "mapped_sha256": report["mapped_source"]["sha256"], "plan_sha256": report["plan_sha256"],
                              "manifest_sha256": report["balanced_manifest"]["sha256"],
                              "raw_inventory": raw, "graph_audit": structural, "facade_audit": facade,
                              "clock_overrides_recomputed_without_changes": True, "physical_clock_arrivals": arrivals,
                              "retained_literal_union": {key: union[key] for key in (
                                  "emitted_blocks", "merged_records", "literal_unique_blocks", "identical_duplicate_blocks",
                                  "occupied_chunks", "bounds", "sorted_unique_record_sha256")}}
            graphs[label], plans[label] = graph, plan

    assert reports["baseline"]["mapped_sha256"] == reports["candidate"]["mapped_sha256"]
    assert reports["baseline"]["raw_inventory"] == reports["candidate"]["raw_inventory"]
    assert plans["baseline"]["budgets"] == plans["candidate"]["budgets"]
    assert plans["baseline"]["entities"] == plans["candidate"]["entities"]
    assert plans["baseline"]["constants"] == plans["candidate"]["constants"]
    assert plans["baseline"]["summary"]["original_signal_endpoints_sha256"] == plans["candidate"]["summary"]["original_signal_endpoints_sha256"]
    regenerated = build_plan(graphs["candidate"], ports_at_root=True)
    legacy = build_plan(graphs["candidate"])
    for key in ("summary", "budgets", "entities", "clusters", "entity_leaf", "leaf_boundaries", "routing_domains", "signals", "constants", "root_cluster"):
        assert json.loads(json.dumps(regenerated[key])) == plans["candidate"][key]
        assert json.loads(json.dumps(legacy[key])) == plans["baseline"][key]
    for label in reports:
        for report_key, union_key in (("unique_blocks", "literal_unique_blocks"), ("occupied_chunks", "occupied_chunks")):
            assert comparison[label][report_key] == reports[label]["retained_literal_union"][union_key]

    output = Path(__file__).with_suffix(".json")
    suites = list(ElementTree.parse(Path(__file__).with_suffix(".xml")).getroot().iter("testsuite"))
    totals = {key: sum(int(suite.get(key, "0")) for suite in suites) for key in ("tests", "failures", "errors", "skipped")}
    assert totals["tests"] == 35 and not sum(totals[key] for key in ("failures", "errors", "skipped"))
    review = {"status": "passed-with-explicit-runtime-and-production-gates",
              "scope": "Frozen historical 8 KiB cache controller only; source/graph/typed geometry and clock audit, not a new large literal-union run.",
              "comparison": reports, "small_regressions": totals,
              "small_runtime": {"dense_and_saved_world": True, "positive_clock_half_period": 512,
                                "negative_clock_half_period": 8, "previous_20_tick_negative_is_now_valid": True},
              "default_plan_unchanged": True, "candidate_plan_reproduced": True,
              "source_driver_sink_alias_constant_and_clock_inventory_preserved": True,
              "large_literal_union_independently_reemitted": False,
              "large_controller_runtime_verified": False, "complete_machine_runtime_verified": False,
              "production_integration_at_review": "Root-I/O flag and measured clock reservations not yet selected by production bounded-system builder; a cache-only exact-policy hook is in progress.",
              "outstanding_gates": ["accepted current 4 KiB mapping/geometry rather than extrapolation from historical 8 KiB",
                                    "exact cache policy integrated and recorded in production manifest",
                                    "complete physical bank and external memory interconnection",
                                    "clock/data timing and native saved-world execution of the complete connected cache",
                                    "whole-machine loading, boot and application acceptance"],
              "seconds": time.monotonic() - started, "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
    output.write_text(json.dumps(review, indent=2) + "\n")
    print(json.dumps({"status": review["status"], "small_regressions": totals,
                      "physical_clocks": {key: value["physical_clock_arrivals"] for key, value in reports.items()},
                      "seconds": review["seconds"], "peak_rss_kib": review["peak_rss_kib"]}, indent=2))


if __name__ == "__main__":
    main()
