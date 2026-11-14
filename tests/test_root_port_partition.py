"""Shallower original I/O must preserve complete directed graph and real wires."""
from copy import deepcopy
import json
from pathlib import Path
import zipfile

import pytest

from experiments.partition_placement.port_egress import audit_egress, place_with_egress
from shamaos.hardware.partition_placement import _literal_states, audit_placement
from shamaos.hardware.partition_planner import Graph, PlanningError, audit_plan, build_plan


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def complete_cache_root_ports():
    path = ROOT / "experiments/memory_controller_runtime/evidence/cache-controller-c331.json.gz"
    graph = Graph.read(path, "shama_cache_adapter")
    return graph, build_plan(graph, ports_at_root=True)


def test_complete_cache_keeps_every_sink_clock_and_original_port_at_shallower_depth(complete_cache_root_ports):
    graph, plan = complete_cache_root_ports
    summary = plan["summary"]
    assert audit_plan(graph, plan)["status"] == "passed"
    assert (summary["native_cells"], summary["original_port_bits"], summary["original_sink_uses"],
            summary["clock_leaf_uses"]) == (1144, 183, 2155, 115)
    assert summary["original_signal_endpoints_sha256"] == "916bd6d13d2c3c36de6843a274f33c34ba648c64e6e716f34724d8ccbf4af579"
    root = plan["root_cluster"]
    ports = [cluster for cluster in plan["clusters"] if cluster["port_ids"]]
    assert len(ports) == 3
    assert all(cluster["parent"] == root for cluster in ports)
    assert summary["signal_tree_links"] == 1380
    assert summary["bounded_router_domains"] == 11
    assert max(len(cluster["children"]) for cluster in plan["clusters"]) == 4
    assert build_plan(graph, ports_at_root=True) == plan


def test_default_cache_plan_matches_the_prechange_frozen_complete_plan(complete_cache_root_ports):
    graph, _ = complete_cache_root_ports
    bundle = ROOT / "experiments/partition_placement/evidence/cache-facade-domain4-672-source-bundle.zip"
    with zipfile.ZipFile(bundle) as archive:
        expected = json.loads(archive.read("inputs/plan.json"))
    actual = json.loads(json.dumps(build_plan(graph)))
    # Absolute source paths differ after workspace restoration; reconstructed
    # entities, all links and every reported graph measurement must not.
    for key in ("summary", "budgets", "entities", "clusters", "entity_leaf", "leaf_boundaries",
                "routing_domains", "signals", "constants", "root_cluster"):
        assert actual[key] == expected[key], key


@pytest.mark.parametrize("fault", ["lost-port", "lost-clock", "lost-directed-link"])
def test_shallower_interface_does_not_bypass_independent_conservation(complete_cache_root_ports, fault):
    graph, original = complete_cache_root_ports
    plan = deepcopy(original)
    if fault == "lost-port":
        next(cluster for cluster in plan["clusters"] if cluster["port_ids"])["port_ids"].pop()
    elif fault == "lost-clock":
        record = next(record for record in plan["signals"] if record["clock_leaf_uses"])
        record["sinks"].remove(next(sink for sink in record["sinks"] if sink["pin"] == "C"))
    else:
        next(record for record in plan["signals"] if record["directed_links"])["directed_links"].pop()
    with pytest.raises(PlanningError):
        audit_plan(graph, plan)


@pytest.mark.parametrize("shape", ["ports-only", "cells-only", "many-ports"])
def test_root_interface_handles_degenerate_and_multilevel_port_trees(tmp_path, shape):
    module = {"cells": {}, "ports": {}}
    if shape != "ports-only":
        for index in range(9):
            module["cells"][f"n{index}"] = {"type": "$_NOT_", "connections": {
                "A": [10 + index - 1 if index else "1"], "Y": [10 + index]}}
    if shape != "cells-only":
        bits = list(range(100, 121))
        module["ports"] = {"a": {"direction": "input", "bits": bits},
                           "b": {"direction": "output", "bits": bits}}
    path = tmp_path / "mapped.json"
    path.write_text(json.dumps({"modules": {"edge": module}}))
    graph = Graph.read(path, "edge")
    plan = build_plan(graph, cell_budget=2, boundary_budget=8, domain_budget=8, arity=3, ports_at_root=True)
    assert audit_plan(graph, plan)["status"] == "passed"
    assert all(len(cluster["children"]) <= 3 for cluster in plan["clusters"])
    assert sum(len(cluster["cell_ids"]) + len(cluster["port_ids"]) for cluster in plan["clusters"]) == len(graph.entities)


@pytest.mark.parametrize("invalid", [None, 0, 1, "true"])
def test_root_interface_policy_cannot_be_enabled_by_ambiguous_values(complete_cache_root_ports, invalid):
    graph, _ = complete_cache_root_ports
    with pytest.raises(PlanningError, match="explicit boolean"):
        build_plan(graph, ports_at_root=invalid)


@pytest.fixture(scope="module")
def root_port_pipeline():
    path = ROOT / "experiments/partition_placement/raised_exports/evidence/pipeline-mapped.json"
    graph = Graph.read(path, "pipeline")
    plan = build_plan(graph, cell_budget=2, boundary_budget=8, domain_budget=5, arity=4, ports_at_root=True)
    return graph, plan, place_with_egress(graph, plan)


def test_actual_root_port_geometry_retains_every_pin_and_literal_block(root_port_pipeline):
    graph, plan, placed = root_port_pipeline
    assert audit_placement(graph, plan, placed)["native_cells"] == 3
    facade = audit_egress(graph, placed)
    assert facade["original_port_bits"] == facade["directed_handoffs"] == 6
    assert len(_literal_states(placed.iter_placements())) == placed.unique_blocks
    assert all(cluster["parent"] == plan["root_cluster"]
               for cluster in plan["clusters"] if cluster["port_ids"])


# The shorter core path also passes the old 20-tick stress case. Eight ticks
# preserves all clock edges but exposes missed data transfers in register b.
@pytest.mark.parametrize("half_period,should_pass", [(512, True), (8, False)])
def test_root_ports_actual_exterior_pipeline_and_timing_negative(root_port_pipeline, tmp_path, half_period, should_pass):
    from test_port_egress_placement import test_actual_exterior_io_pipeline_and_too_fast_negative as prove
    prove(root_port_pipeline, half_period, should_pass, tmp_path)


@pytest.mark.parametrize("half_period,should_pass", [(512, True), (8, False)])
def test_root_ports_saved_world_replays_every_original_and_handoff_pin(root_port_pipeline, tmp_path, half_period, should_pass):
    from test_port_egress_saved_world import test_saved_facade_matches_dense_engine_at_every_original_and_handoff_pin as prove
    prove(root_port_pipeline, tmp_path, half_period, should_pass)
