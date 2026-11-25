"""Repartition real native cells and I/O together when a parent cannot route them."""
from collections import Counter
from copy import deepcopy
from pathlib import Path
import sys

import pytest

from shamaos.hardware.bounded_facade import place_with_egress
from shamaos.hardware.partition_cuts import maximum_prefixes
from shamaos.hardware.partition_planner import Graph, PlanningError, audit_plan, build_plan
from test_port_egress_saved_world import check_saved_facade


@pytest.fixture(scope="module")
def graph():
    path = Path(__file__).resolve().parents[1] / "experiments/partition_placement/raised_exports/evidence/pipeline-mapped.json"
    return Graph.read(path, "pipeline")


def make_plan(graph, limit=None):
    return build_plan(graph, cell_budget=3, boundary_budget=8, domain_budget=5, arity=4,
                      shared_prefix_budget=limit, facade=True)


def demand(plan):
    # Count real graph domains and both ends of each ancestor's facade net.
    loads = Counter()
    for domain in plan["routing_domains"]:
        loads[domain["cluster_id"]] += domain["terminal_count"]
    for entity in plan["entities"]:
        if entity["kind"] == "port":
            cluster = plan["entity_leaf"][entity["id"]]
            while (cluster := plan["clusters"][cluster]["parent"]) is not None:
                loads[cluster] += 2
    return max(loads.values())


@pytest.mark.parametrize("limit,expected", [(20, 20), (18, 18), (16, 16)])
def test_cut_fits_exact_graph_and_facade_prefix_demand(graph, limit, expected):
    plan = make_plan(graph, limit)
    old = make_plan(graph)
    assert old["summary"]["original_signal_endpoints_sha256"] == plan["summary"]["original_signal_endpoints_sha256"]
    assert plan["entities"] == old["entities"] and plan["constants"] == old["constants"]
    assert audit_plan(graph, plan)
    assert demand(plan) == maximum_prefixes(graph, (plan["clusters"], plan["entity_leaf"], plan["root_cluster"]), facade=True) == expected
    assert make_plan(graph, limit) == plan
    assert (old == plan) == (limit == 20)
    if limit < 20:
        assert any(c["port_ids"] and c["cell_ids"] for c in plan["clusters"])


def test_cut_still_rejects_impossible_budget_and_missing_native_dependency(graph, monkeypatch):
    with pytest.raises(PlanningError, match="no candidate hierarchy"):
        make_plan(graph, 15)
    monkeypatch.setitem(sys.modules, "pymetis", None)
    assert make_plan(graph, 20) == make_plan(graph)
    with pytest.raises(PlanningError, match="install pymetis"):
        make_plan(graph, 18)


@pytest.mark.parametrize("fault", ["constant", "clock", "port"])
def test_original_graph_audit_rejects_corruption_in_a_cut_plan(graph, fault):
    plan = deepcopy(make_plan(graph, 18))
    if fault == "constant":
        plan["constants"].pop()
    elif fault == "clock":
        clock = next(s for s in plan["signals"] if any(sink["pin"] == "C" for sink in s["sinks"]))
        clock["sinks"].pop()
    else:
        next(c for c in plan["clusters"] if c["port_ids"])["port_ids"].pop()
    with pytest.raises(PlanningError):
        audit_plan(graph, plan)


@pytest.mark.parametrize("half_period,success", [(512, True), (8, False)])
def test_cut_mixed_io_and_cell_leaves_drive_saved_pipeline(graph, tmp_path, half_period, success):
    plan = make_plan(graph, 18)
    placed = place_with_egress(graph, plan, local_data_tracks=True)
    check_saved_facade((graph, plan, placed), tmp_path, half_period, success)
