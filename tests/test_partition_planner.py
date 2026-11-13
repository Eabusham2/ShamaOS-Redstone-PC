from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path

import pytest

from shamaos.hardware.partition_planner import Graph, PlanningError, audit_plan, build_plan


ARTIFACTS = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def complete_display():
    graph = Graph.read(ARTIFACTS / "display_bridge_aig.json.gz", "shama_display_bridge")
    plan = build_plan(graph)
    return graph, plan


def test_complete_real_partition_preserves_every_cell_sink_clock_constant_and_port(complete_display):
    graph, plan = complete_display
    assert graph.cell_count == 13314
    assert len(graph.entities) == 13314 + 351
    assert plan["summary"]["clock_leaf_uses"] == 649
    assert plan["summary"]["constant_terminals"] == 649
    assert plan["summary"]["original_sink_uses"] == 23328
    assert plan["summary"]["maximum_leaf_boundary"] <= 128
    assert plan["summary"]["maximum_domain_terminals"] <= 256
    assert plan["summary"]["maximum_router_fanout"] <= 4
    assert plan["summary"]["maximum_domains_at_one_cluster"] > 1
    assert audit_plan(graph, plan)["status"] == "passed"
    assert not plan["verification"]["physical_geometry_emitted"]
    assert not plan["verification"]["timing_closed"]
    second = build_plan(graph)
    assert second == plan


def test_complete_production_abc_partition_including_library_dff_directions():
    graph = Graph.read(ARTIFACTS / "display_bridge_abc.json.gz", "shama_display_bridge")
    plan = build_plan(graph)
    assert graph.cell_count == 10910
    assert plan["summary"]["original_port_bits"] == 351
    assert plan["summary"]["clock_leaf_uses"] == 649
    assert plan["summary"]["signals"] == 10931
    assert plan["summary"]["original_sink_uses"] == 20944
    assert audit_plan(graph, plan)["status"] == "passed"


@pytest.fixture(scope="module")
def production_display():
    graph = Graph.read(ARTIFACTS / "display_bridge_abc.json.gz", "shama_display_bridge")
    return graph, build_plan(graph)


@pytest.mark.parametrize("fault", ["disconnected-edge", "invented-domain-net", "segment-count", "clock-count", "total-clock-count"])
def test_audit_covers_every_edge_domain_and_reported_clock_leaf(production_display, fault):
    graph, original = production_display
    plan = deepcopy(original)
    if fault == "disconnected-edge":
        # Reproduce the independent review's false acceptance exactly: ABC
        # bit353 is local, but an unrelated valid hierarchy edge is appended.
        record = next(record for record in plan["signals"] if record["bit"] == 353)
        assert not record["directed_links"]
        source, sink = 177, 1
        assert plan["clusters"][sink]["parent"] == source
        identity = f"net353:cluster{source}>cluster{sink}"
        record["directed_links"] = [(source, sink)]
        record["boundary_links"] = [{
            "id": identity,
            "source": {"cluster": source, "domain": None, "terminal_id": identity + ":out"},
            "sink": {"cluster": sink, "domain": None, "terminal_id": identity + ":in"},
        }]
    elif fault == "invented-domain-net":
        domain = next(domain for domain in plan["routing_domains"]
                      if domain["segments"] and domain["terminal_count"] + 1 + len(domain["segments"][0]["outgoing_clusters"]) <= 256)
        segment = deepcopy(domain["segments"][0])
        segment["net"] = max(graph.signals) + 1000
        domain["segments"].append(segment)
        domain["terminal_count"] += 1 + len(segment["outgoing_clusters"])
        domain["segment_count"] += 1
    elif fault == "segment-count":
        plan["routing_domains"][0]["segment_count"] += 1
    elif fault == "clock-count":
        next(record for record in plan["signals"] if record["clock_leaf_uses"])["clock_leaf_uses"] -= 1
    else:
        plan["summary"]["clock_leaf_uses"] -= 1
    with pytest.raises(PlanningError):
        audit_plan(graph, plan)


def test_audit_rejects_a_router_domain_that_carries_no_original_connection(production_display):
    graph, original = production_display
    plan = deepcopy(original)
    plan["routing_domains"].append({
        "id": len(plan["routing_domains"]), "cluster_id": plan["root_cluster"],
        "terminal_count": 0, "segment_count": 0, "segments": [],
    })
    plan["summary"]["bounded_router_domains"] += 1
    with pytest.raises(PlanningError, match="does not participate"):
        audit_plan(graph, plan)


@pytest.mark.parametrize("fault", ["native_cells", "signal_tree_links", "maximum_router_fanout",
                                  "topology_sha256", "original_signal_endpoints_sha256", "cluster-depth"])
def test_audit_recomputes_evidence_counts_and_hashes_instead_of_trusting_the_report(production_display, fault):
    graph, original = production_display
    plan = deepcopy(original)
    if fault == "cluster-depth":
        plan["clusters"][plan["root_cluster"]]["level"] -= 1
        plan["summary"]["hierarchy_depth"] -= 1
    elif fault.endswith("sha256"):
        plan["summary"][fault] = "0" * 64
    else:
        plan["summary"][fault] -= 1
    with pytest.raises(PlanningError, match="misreports"):
        audit_plan(graph, plan)


def test_compressed_complete_fixtures_match_their_recorded_mapping_provenance():
    provenance = json.loads((ARTIFACTS / "display_bridge_mapping_provenance.json").read_text())
    for name, expected in provenance["fixtures"].items():
        compressed = (ARTIFACTS / name).read_bytes()
        mapped = gzip.decompress(compressed)
        assert hashlib.sha256(compressed).hexdigest() == expected["gzip_sha256"]
        assert hashlib.sha256(mapped).hexdigest() == expected["mapped_json_sha256"]


def test_plan_mutation_cannot_change_the_original_graph_used_by_the_auditor(complete_display):
    graph, _ = complete_display
    plan = build_plan(graph)
    original_name = graph.entities[0]["name"]
    plan["entities"][0]["name"] = "different-cell"
    assert graph.entities[0]["name"] == original_name
    with pytest.raises(PlanningError, match="original cell"):
        audit_plan(graph, plan)
    summary = build_plan(graph, retain_signals=False)
    assert not summary["verification"]["all_signal_sink_paths_retained"]
    with pytest.raises(PlanningError, match="summary-only"):
        audit_plan(graph, summary)


@pytest.mark.parametrize("invalid", [True, 64.5, float("inf")])
def test_noninteger_or_infinite_budgets_cannot_disable_the_bounded_contract(complete_display, invalid):
    graph, _ = complete_display
    with pytest.raises(PlanningError, match="finite integers"):
        build_plan(graph, cell_budget=invalid)


@pytest.mark.parametrize("fault", ["cell", "clock-leaf", "constant", "domain-budget", "link", "link-terminal", "domain-segment", "leaf-attachment"])
def test_independent_audit_detects_structural_loss_and_unrouted_boundaries(complete_display, fault):
    graph, original = complete_display
    plan = deepcopy(original)
    if fault == "cell":
        next(cluster for cluster in plan["clusters"] if cluster["cell_ids"])["cell_ids"].pop()
    elif fault == "clock-leaf":
        signal = next(record for record in plan["signals"] if record["clock_leaf_uses"])
        clock = next(endpoint for endpoint in signal["sinks"] if endpoint["pin"] == "C")
        signal["sinks"].remove(clock)
    elif fault == "constant":
        plan["constants"].pop()
    elif fault == "domain-budget":
        plan["routing_domains"][0]["terminal_count"] = 100000
    elif fault == "link":
        next(record for record in plan["signals"] if record["directed_links"])["directed_links"].pop()
    elif fault == "link-terminal":
        next(record for record in plan["signals"] if record["boundary_links"])["boundary_links"][0]["sink"]["terminal_id"] = "wrong-terminal"
    elif fault == "domain-segment":
        domain = next(domain for domain in plan["routing_domains"] if domain["segments"])
        removed = domain["segments"].pop()
        domain["terminal_count"] -= 1 + len(removed["outgoing_clusters"])
        record = next(record for record in plan["signals"] if record["bit"] == removed["net"])
        record["domain_segments"] = [segment for segment in record["domain_segments"] if segment[1] != domain["id"]]
    else:
        plan["entity_leaf"][0] = plan["root_cluster"]
    with pytest.raises(PlanningError):
        audit_plan(graph, plan)


def test_high_fanout_clock_has_explicit_bounded_branches_and_no_missing_registers(tmp_path):
    count = 129
    module = {
        "ports": {"clock": {"direction": "input", "bits": [2]},
                  "q": {"direction": "output", "bits": list(range(10, 10 + count))}},
        "cells": {f"register{index:03}": {
            "type": "$_DFF_P_", "port_directions": {"C": "input", "D": "input", "Q": "output"},
            "connections": {"C": [2], "D": ["1"], "Q": [10 + index]},
        } for index in range(count)},
    }
    path = tmp_path / "fanout.json"
    path.write_text(json.dumps({"modules": {"clock_fanout": module}}))
    graph = Graph.read(path, "clock_fanout")
    plan = build_plan(graph, cell_budget=8, boundary_budget=16, domain_budget=16)
    assert audit_plan(graph, plan)["status"] == "passed"
    assert plan["summary"]["clock_leaf_uses"] == count
    assert plan["summary"]["constant_terminals"] == count
    assert plan["summary"]["explicit_fanout_branches"] > 1
    assert plan["summary"]["maximum_router_fanout"] <= 4
    assert all(domain["terminal_count"] <= 16 for domain in plan["routing_domains"])
    with pytest.raises(PlanningError, match="alone needs"):
        build_plan(graph, cell_budget=8, boundary_budget=1)
    with pytest.raises(PlanningError, match="complete bounded routing branch"):
        build_plan(graph, domain_budget=4)


def test_unknown_flash_or_clock_constant_is_not_silently_grounded(tmp_path):
    path = tmp_path / "unknown.json"
    path.write_text(json.dumps({"modules": {"bad": {"ports": {}, "cells": {
        "register": {"type": "$_DFF_P_", "port_directions": {"C": "input", "D": "input", "Q": "output"},
                     "connections": {"C": ["x"], "D": ["0"], "Q": [2]}}
    }}}}))
    with pytest.raises(PlanningError, match="cannot silently ground"):
        Graph.read(path, "bad")
    source = json.loads(path.read_text())
    del source["modules"]["bad"]["cells"]["register"]["connections"]["C"]
    path.write_text(json.dumps(source))
    with pytest.raises(PlanningError, match="malformed|missing a physical pin"):
        Graph.read(path, "bad")
