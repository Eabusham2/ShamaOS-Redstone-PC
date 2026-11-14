"""Review the frozen one-bank controller and production policy without a union rerun.

The raw mapped graph, complete plans, both manifests and their real source
bindings are checked. Repeater delays are recomputed from retained routes.
Large block/chunk totals remain explicitly retained generator evidence.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import asdict
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
    assert expected_cells == {e["name"]: e["cell_type"] for e in graph.entities if e["kind"] == "cell"}
    assert sorted(expected_ports) == sorted((e["name"], e["port_name"], e["port_index"], e["direction"], e["bit"])
                                           for e in graph.entities if e["kind"] == "port")
    endpoint = lambda point: (graph.entities[point.entity]["name"], point.pin, point.index)
    expected_signals = {}
    for bit, values in sinks.items():
        assert len(drivers[bit]) == 1
        expected_signals[bit] = (drivers[bit][0], sorted(values))
    assert expected_signals == {bit: (endpoint(signal.source), sorted(map(endpoint, signal.sinks)))
                                for bit, signal in graph.signals.items()}
    assert sorted(constants) == sorted((value, endpoint(point)) for value, point in graph.constants)
    aliases = Counter(port[-1] for port in expected_ports if type(port[-1]) is int)
    return {"cells": len(expected_cells), "port_bits": len(expected_ports), "signals": len(expected_signals),
            "sink_uses": sum(map(len, sinks.values())), "constant_terminals": len(constants),
            "port_alias_groups": sum(count > 1 for count in aliases.values()),
            "unused_driver_bits": len(set(drivers) - set(sinks))}


def physical_clock_arrivals(plan, placed):
    from shamaos.hardware.external_router import _trunk
    modules = placed.all_modules()
    entities = {record["id"]: record for record in plan["entities"]}
    result = {}

    def path_delays(tile, net):
        source_trunk, source_blocks = tile.endpoint_route(net, net.source)
        def delay(blocks):
            states = (tile.overrides.get(block.pos, block.block) for block in blocks)
            return sum(int(dict(state.properties)["delay"]) for state in states if state.name == "minecraft:repeater")
        source_delay = delay(source_blocks)
        del source_blocks
        endpoints = {}
        for sink in net.sinks:
            point, blocks = tile.endpoint_route(net, sink)
            endpoints[sink] = (point, delay(blocks))
        del blocks
        trunk = list(_trunk(source_trunk, tuple(point for point, _ in endpoints.values()), component="review-clock"))
        paths = {}
        for sink, (end_trunk, sink_delay) in endpoints.items():
            lo, hi = sorted((source_trunk.x, end_trunk.x))
            paths[sink] = source_delay + sink_delay + delay(block for block in trunk if lo < block.pos.x < hi)
        return paths

    for signal in plan["signals"]:
        clocks = {entities[sink["entity"]]["name"] for sink in signal["sinks"]
                  if entities[sink["entity"]]["kind"] == "cell" and sink["pin"] == "C"}
        if not clocks:
            continue
        links = {link["source"]["terminal_id"]: link for link in signal["boundary_links"]}
        arrivals, visited = {}, set()

        def visit(key, elapsed):
            assert key not in visited
            visited.add(key)
            tile = modules[key]
            net = next(net for net in tile.physical.nets if net.bit == signal["bit"])
            for sink, delay in path_delays(tile, net).items():
                if sink.pin in links:
                    end = links[sink.pin]["sink"]
                    visit(("leaf", end["cluster"]) if end["domain"] is None else ("domain", end["domain"]), elapsed + delay)
                elif sink.pin == "C" and sink.name in clocks:
                    assert sink.name not in arrivals
                    arrivals[sink.name] = elapsed + delay

        source_entity = signal["source"]["entity"]
        visit(("leaf", plan["entity_leaf"][source_entity]), 0)
        assert set(arrivals) == clocks and len(set(arrivals.values())) == 1
        prefixes = []
        for tile in placed.all_facade_modules().values():
            for net in tile.physical.nets:
                if net.bit == source_entity:
                    assert len(net.sinks) == 1
                    prefixes.extend(path_delays(tile, net).values())
        original_delay = next(iter(arrivals.values()))
        result[str(signal["bit"])] = {"clock_leaves": len(arrivals), "all_original_pin_arrivals": original_delay,
                                      "facade_common_prefix": sum(prefixes), "all_exterior_arrivals": original_delay + sum(prefixes),
                                      "clock_facade_handoffs": len(prefixes), "runtime_verified": False}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--junit", type=Path)
    args = parser.parse_args()
    repository, integration_root = args.repository.resolve(), args.root.resolve()
    sys.path[:0] = [str(repository / "src"), str(repository)]
    resource.setrlimit(resource.RLIMIT_AS, (768 << 20, 768 << 20))
    started = time.monotonic()
    from experiments.memory_controller_runtime import current_fixture
    from experiments.partition_planner.audit_cache_frames import rtl_review
    from experiments.partition_placement.port_egress_manifest import read_egress_manifest
    from experiments.partition_placement.port_egress import audit_egress as retained_audit_egress
    from shamaos.hardware import cache_layout
    from shamaos.hardware.bounded_facade import EgressCluster, audit_egress
    from shamaos.hardware.logic import template_for
    from shamaos.hardware.partition_placement import audit_placement, balance_partition_clocks
    from shamaos.hardware.partition_planner import audit_plan, build_plan
    from shamaos.bounded_system import _verify_planned_clock_matching

    evidence = repository / "experiments/memory_controller_runtime/current_four_kib/evidence"
    receipt_path = evidence / "receipt.json"
    receipt = json.loads(receipt_path.read_text())
    archive_path = repository / receipt["bundle"]["path"]
    archive_data = archive_path.read_bytes()
    assert sha(archive_data) == receipt["bundle"]["sha256"] and len(archive_data) == receipt["bundle"]["bytes"]
    review_files = ["src/shamaos/hardware/cache_layout.py", "src/shamaos/bounded_system.py",
                    "src/shamaos/hardware/bounded_facade.py", "src/shamaos/hardware/packed_parent_union.py",
                    "tests/test_cache_layout_policy.py", "tests/test_current_cache_fixture.py",
                    "tests/test_bounded_system_backend.py"]
    production_hashes = {name: sha((repository / name).read_bytes()) for name in review_files}
    assert production_hashes == {name: sha((integration_root / name).read_bytes()) for name in review_files}
    source_matches = {}
    with tempfile.TemporaryDirectory(prefix="current-cache-review-") as folder:
        target = Path(folder)
        with zipfile.ZipFile(archive_path) as archive:
            names = archive.namelist()
            assert len(names) == len(set(names)) == receipt["bundle"]["files"] == 51
            assert set(names) == set(receipt["inventory"])
            assert sum(info.file_size for info in archive.infolist()) < 16 << 20
            for name in names:
                assert not Path(name).is_absolute() and ".." not in Path(name).parts
                data = archive.read(name)
                assert sha(data) == receipt["inventory"][name]
                path = target / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)

        current = target / "inputs/current"
        prepared, graph, _, fabric, _ = current_fixture.load_prepared(current)
        assert (evidence / "prepared.json").read_bytes() == (current / "prepared.json").read_bytes()
        accepted = json.loads((current / "facade/report.json").read_text())
        assert (evidence / "controller-geometry.json").read_bytes() == (current / "facade/report.json").read_bytes()
        for name, expected in prepared["input_hashes"].items():
            assert sha((integration_root / name).read_bytes()) == expected
            source_matches[name] = expected
        for source_set in (prepared["generator_hashes"], accepted["source_hashes"]):
            for name, expected in source_set.items():
                assert sha((target / name).read_bytes()) == expected
                assert sha((repository / name).read_bytes()) == expected

        raw = json.loads((current / "mapped-raw.json").read_text())["modules"]
        mapped = json.loads((current / "mapped.json").read_text())["modules"]
        assert set(raw) == set(mapped) == {"shama_cache_adapter"}
        before, after = raw["shama_cache_adapter"], mapped["shama_cache_adapter"]
        scope = {name: cell for name, cell in before["cells"].items() if cell["type"] == "$scopeinfo"}
        assert len(scope) == prepared["metadata_removal"]["pinless_scope_records"] == 1
        assert all(not cell.get("connections") and not cell.get("port_directions") for cell in scope.values())
        assert before["ports"] == after["ports"] and before["netnames"] == after["netnames"]
        native = {name: cell for name, cell in before["cells"].items() if name not in scope}
        assert native.keys() == after["cells"].keys()
        annotations = 0
        for name, cell in native.items():
            actual = dict(after["cells"][name])
            if "port_directions" not in cell and "port_directions" in actual:
                expected = {pin: "output" if pin in ("Y", "Q") else "input" for pin in template_for(cell["type"]).pins}
                assert actual.pop("port_directions") == expected
                annotations += 1
            assert actual == cell
        assert annotations == prepared["metadata_removal"]["validated_library_pin_annotations_added"] == 115
        inventory = raw_inventory(after, graph)
        assert inventory["cells"] == receipt["native_cells"] == 1178
        assert inventory["port_bits"] == receipt["original_port_bits"] == 182
        plan = json.loads((current / "plan.json").read_text())
        assert sha((current / "plan.json").read_bytes()) == prepared["plan_sha256"] == accepted["plan_sha256"]
        assert audit_plan(graph, plan) == prepared["plan_audit"]
        assert plan["summary"]["clock_leaf_uses"] == receipt["register_clock_pins"] == 115
        policy = cache_layout.cache_policy(graph)
        rebuilt = build_plan(graph, cell_budget=policy["native_cells_per_leaf"], boundary_budget=policy["boundary_terminals_per_leaf"],
                             domain_budget=policy["routing_terminals_per_domain"], arity=policy["children_per_cluster"],
                             ports_at_root=policy["ports_at_root"])
        canonical = lambda value: json.loads(json.dumps(value))
        for key in plan:
            if key == "source":
                assert {k: v for k, v in rebuilt[key].items() if k != "path"} == {k: v for k, v in plan[key].items() if k != "path"}
            else:
                assert canonical(rebuilt[key]) == plan[key], key
        assert policy["clock_padding_blocks"] == accepted["clock_padding_blocks"] == 128
        assert {str(k): v for k, v in policy["clock_domain_padding_blocks"].items()} == accepted["clock_domain_padding"] == {"6": 160}
        assert not policy["native_runtime_verified"]

        print("Frozen sources, full raw inventory and production plan passed", flush=True)
        manifests = {}
        for label in ("unbalanced", "balanced"):
            path = current / f"facade/report-{label}.json.gz"
            manifests[label] = read_egress_manifest(path, accepted[f"{label}_manifest"]["sha256"], graph, plan, source_root=target)
        structural = audit_placement(graph, plan, manifests["balanced"])
        exterior = retained_audit_egress(graph, manifests["balanced"])
        # The retained experiment and promoted production facade deliberately
        # have distinct Python classes. Rewrap the same fields recursively to
        # exercise production's strict class audit without changing geometry.
        def production_type(cluster):
            fields = dict(vars(cluster))
            fields["children"] = tuple(production_type(child) for child in cluster.children)
            result = EgressCluster(**fields)
            assert {k: v for k, v in vars(result).items() if k != "children"} == {k: v for k, v in vars(cluster).items() if k != "children"}
            return result
        production_facade = production_type(manifests["balanced"])
        assert audit_egress(graph, production_facade) == exterior
        assert structural == accepted["graph_audit"] and exterior == accepted["facade_audit"]
        print("Both saved manifests and production facade audit passed", flush=True)
        matched = balance_partition_clocks(plan, manifests["unbalanced"])
        assert canonical(matched) == accepted["clock_matching"]
        assert manifests["unbalanced"].all_modules() == manifests["balanced"].all_modules()
        assert manifests["unbalanced"].all_facade_modules() == manifests["balanced"].all_facade_modules()
        before_match = deepcopy(manifests["balanced"].all_modules())
        production_matching = _verify_planned_clock_matching(plan, production_facade)
        assert canonical(production_matching) == accepted["clock_matching"]
        assert before_match == manifests["balanced"].all_modules()
        print("Unbalanced and production clock matcher replay passed", flush=True)
        arrivals = physical_clock_arrivals(plan, manifests["balanced"])
        for bit, record in arrivals.items():
            assert record["all_original_pin_arrivals"] == accepted["clock_matching"][bit]["balanced_repeater_delay"]

        # The promoted full facade has one import substitution; its support
        # union implementation is byte-identical to the retained experiment.
        original = (target / "experiments/partition_placement/port_egress.py").read_text()
        promoted = (repository / "src/shamaos/hardware/bounded_facade.py").read_text()
        assert original.replace("from experiments.partition_placement.packed_parent_union import PackedParentUnion",
                                "from .packed_parent_union import PackedParentUnion") == promoted
        assert (target / "experiments/partition_placement/packed_parent_union.py").read_bytes() == (repository / "src/shamaos/hardware/packed_parent_union.py").read_bytes()

        # Re-run the retained 601-frame RTL schedule, including both intended
        # failures, using the exact copied current RTL/header. No synthesis or
        # large geometry generation is performed.
        print("Every original/exterior clock-path delay recomputed", flush=True)
        rtl_directory = target / "independent-rtl"
        rtl_directory.mkdir()
        oracle = rtl_review(rtl_directory, source_root=current / "source", capacity_bytes=4096)
        for key in oracle:
            if key != "outcomes":
                assert oracle[key] == prepared["rtl_oracle"][key], key
        assert [row["exit_code"] for row in oracle["outcomes"]] == [0, 1, 1]
        print("Retained RTL schedule and both negative cases passed", flush=True)
        union = accepted["literal_union"]
        assert union["emitted_blocks"] == union["merged_records"] == union["literal_unique_blocks"] + union["identical_duplicate_blocks"]
        assert manifests["balanced"].unique_blocks == accepted["computed_unique_blocks"] == union["literal_unique_blocks"] == receipt["controller_unique_blocks"]
        assert asdict(manifests["balanced"].bounds) == accepted["bounds"] == union["bounds"]
        assert union["occupied_chunks"] == receipt["controller_occupied_chunks"]
        assert union["sorted_unique_record_sha256"] == receipt["controller_union_sha256"]
        assert (fabric.total_bytes, fabric.bank_count) == (4096, 1)
        assert not receipt["connected_cache_runtime_verified"] and not receipt["whole_machine_verified"]

    assert production_hashes == {name: sha((repository / name).read_bytes()) for name in review_files}
    assert source_matches == {name: sha((integration_root / name).read_bytes()) for name in source_matches}
    report = {"schema": "independent-current-cache-review-v1", "status": "source-graph-plan-manifest-clock-and-rtl-review-passed",
              "scope": "Independent source-bound inventory and metadata replay plus focused RTL/guard tests. Retained full geometry union was not regenerated; physical connected-cache runtime and whole-world acceptance remain pending.",
              "receipt_sha256": sha(receipt_path.read_bytes()), "bundle_sha256": sha(archive_data), "bundle_files_verified": 51,
              "root_elaboration_inputs_exact": source_matches, "reviewed_production_and_test_sha256": production_hashes,
              "root_production_and_test_sources_exact": True,
              "all_raw_native_cells_ports_and_named_nets_retained": True, "pinless_scope_records_removed": len(scope),
              "native_pin_direction_annotations_verified": annotations, "raw_inventory": inventory,
              "complete_saved_plan_reproduced_by_production_policy": True, "plan_sha256": prepared["plan_sha256"],
              "policy": policy, "accepted_clock_reservation_groups": accepted["clock_reservation_groups"],
              "graph_audit": structural, "facade_audit": exterior, "balanced_manifest_sha256": accepted["balanced_manifest"]["sha256"],
              "unbalanced_manifest_sha256": accepted["unbalanced_manifest"]["sha256"],
              "saved_unbalanced_rematches_to_exact_saved_balanced_modules": True,
              "production_matcher_replay_matches_without_mutation": True,
              "production_facade_audit_accepts_exact_fields_after_class_conversion": True,
              "physical_clock_arrivals": arrivals, "promoted_facade_exact_except_import": True,
              "promoted_parent_union_byte_identical": True, "independently_replayed_rtl_oracle": oracle,
              "retained_literal_union": {key: union[key] for key in ("emitted_blocks", "merged_records", "literal_unique_blocks",
                  "identical_duplicate_blocks", "occupied_chunks", "bounds", "sorted_unique_record_sha256")},
              "large_geometry_reemitted": False, "concrete_findings": [],
              "connected_cache_runtime_verified": False, "whole_machine_verified": False,
              "elapsed_seconds": time.monotonic() - started, "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
    if args.junit:
        xml = ElementTree.parse(args.junit).getroot()
        suites = list(xml.iter("testsuite"))
        report["focused_tests"] = {name: sum(int(suite.attrib.get(name, 0)) for suite in suites)
                                   for name in ("tests", "failures", "errors", "skipped")}
        assert not any(report["focused_tests"][name] for name in ("failures", "errors", "skipped"))
        report["focused_tests_junit_sha256"] = sha(args.junit.read_bytes())
    destination = Path(__file__).with_suffix(".json")
    destination.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in ("status", "raw_inventory", "physical_clock_arrivals", "elapsed_seconds", "peak_rss_kib")}, indent=2))


if __name__ == "__main__":
    main()
