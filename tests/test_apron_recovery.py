"""Recovery hints choose a measured start; they cannot replace geometry checks."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

DIRECTORY = Path(__file__).resolve().parents[1] / "experiments/partition_placement/raised_exports"
SPEC = importlib.util.spec_from_file_location("apron_recovery", DIRECTORY / "apron_recovery.py")
RECOVERY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RECOVERY)


@pytest.fixture
def retained():
    path = DIRECTORY / "evidence/parent221-interrupted.json"
    evidence = json.loads(path.read_bytes())
    report = {"topology_sha256": evidence["full_graph_topology_sha256"],
              "mapped_source": evidence["mapped_source"],
              "effective_partition_placement_sha256": evidence["effective_candidate_sha256"],
              "source_hashes": {name: hashlib.sha256((DIRECTORY.parents[2] / name).read_bytes()).hexdigest()
                                for name in evidence["source_bindings"]},
              "cluster_id": 221}
    clusters = {221: {"children": [177], "parent": None},
                177: {"children": [0], "parent": 221},
                0: {"children": [], "parent": 177}}
    return path, evidence, report, clusters


def test_retained_hint_requires_current_sources_and_preserves_incomplete_scope(retained):
    path, evidence, report, clusters = retained
    result = RECOVERY.load_recovery_hint(path, hashlib.sha256(path.read_bytes()).hexdigest(), report, clusters)
    assert result["cluster_id"] == 221
    assert result["horizontal_layout"] == "row"
    assert result["initial_apron"] == 1874
    assert "final union must run again" in result["scope"]
    assert evidence["literal_union_verified"] is False


@pytest.mark.parametrize("fault", ["status", "topology", "mapped", "candidate", "source-inventory",
                                    "source-hash", "leaf", "boolean-id", "layout", "apron",
                                    "boolean-apron", "unexposed", "outside"])
def test_invalid_or_unbound_recovery_cannot_start(retained, tmp_path, fault):
    _, evidence, report, clusters = retained
    evidence = deepcopy(evidence)
    progress = evidence["computed_parent_progress"]
    if fault == "status":
        evidence["status"] = "running"
    elif fault == "topology":
        evidence["full_graph_topology_sha256"] = "0" * 64
    elif fault == "mapped":
        evidence["mapped_source"]["sha256"] = "0" * 64
    elif fault == "candidate":
        evidence["effective_candidate_sha256"] = "0" * 64
    elif fault == "source-inventory":
        evidence["source_bindings"].pop(next(iter(evidence["source_bindings"])))
    elif fault == "source-hash":
        evidence["source_bindings"][next(iter(evidence["source_bindings"]))]["current_sha256"] = "0" * 64
    elif fault == "leaf":
        progress["cluster_id"] = 0
    elif fault == "boolean-id":
        progress["cluster_id"] = False
    elif fault == "layout":
        progress["packing"]["horizontal_layout"] = "invented"
    elif fault == "apron":
        progress["packing"]["export_apron"] = 23
    elif fault == "boolean-apron":
        progress["packing"]["export_apron"] = True
    elif fault == "unexposed":
        progress["own_exports_at_south_envelope"] = False
    else:
        report["cluster_id"] = 177
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(evidence))
    with pytest.raises(ValueError):
        RECOVERY.load_recovery_hint(path, hashlib.sha256(path.read_bytes()).hexdigest(), report, clusters)


def test_digest_and_duplicate_json_keys_are_rejected(retained, tmp_path):
    path, _, report, clusters = retained
    with pytest.raises(ValueError, match="digest"):
        RECOVERY.load_recovery_hint(path, "0" * 64, report, clusters)
    path = tmp_path / "duplicate.json"
    path.write_text('{"status":"a","status":"b"}')
    with pytest.raises(ValueError, match="duplicate"):
        RECOVERY.load_recovery_hint(path, hashlib.sha256(path.read_bytes()).hexdigest(), report, clusters)


def _result(front, maximum, *, second=None):
    points = [SimpleNamespace(z=front)]
    if second is not None:
        points.append(SimpleNamespace(z=second))
    physical = SimpleNamespace(input_ports={"p": tuple(points)}, output_ports={})
    return SimpleNamespace(modules={0: SimpleNamespace(physical=physical)},
                           bounds=SimpleNamespace(max=SimpleNamespace(z=maximum)), packing={})


def _module(attempt):
    return SimpleNamespace(_connect_parent=lambda *args: "unmodified", _connect_parent_attempt=attempt,
                           PlacementError=ValueError)


def test_hint_reruns_attempt_and_monotonic_export_checks():
    calls = []
    def attempt(*args, export_apron):
        calls.append(export_apron)
        return _result(export_apron, export_apron + 10 if len(calls) == 1 else export_apron)
    module = _module(attempt)
    RECOVERY.install_recovery_hint(module, {"cluster_id": 3, "horizontal_layout": "row", "initial_apron": 50})
    assert module._connect_parent(None, 4, (), 319, {"horizontal_layout": "row"}) == "unmodified"
    assert module._connect_parent(None, 3, (), 319, {"horizontal_layout": "compact"}) == "unmodified"
    result = module._connect_parent(None, 3, (), 319, {"horizontal_layout": "row"})
    assert calls == [50, 64]
    assert result.packing["export_apron"] == 64
    assert result.packing["recovered_initial_export_apron"] == 50
    assert result.packing["export_apron_attempts"] == [{"apron": 50, "export_z": 50, "actual_maximum_z": 60}]


@pytest.mark.parametrize("fault", ["route", "mixed-face", "unbounded"])
def test_all_route_and_interface_rejections_still_propagate(fault):
    calls = []
    def attempt(*args, export_apron):
        calls.append(export_apron)
        if fault == "route":
            raise ValueError("actual wire collision")
        return _result(export_apron, export_apron + 10,
                       second=export_apron + 1 if fault == "mixed-face" else None)
    module = _module(attempt)
    RECOVERY.install_recovery_hint(module, {"cluster_id": 3, "horizontal_layout": "row", "initial_apron": 50})
    with pytest.raises(ValueError):
        module._connect_parent(None, 3, (), 319, {"horizontal_layout": "row"})
    assert len(calls) == (8 if fault == "unbounded" else 1)


def test_default_start_matches_original_guarded_small_hierarchy():
    from shamaos.hardware.partition_planner import Graph, build_plan
    spec = importlib.util.spec_from_file_location("shamaos.hardware.apron_recovery_candidate", DIRECTORY / "partition_placement.py")
    candidate = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[spec.name] = candidate
    try:
        spec.loader.exec_module(candidate)
        graph = Graph.read(DIRECTORY / "evidence/pipeline-mapped.json", "pipeline")
        plan = build_plan(graph, cell_budget=1, boundary_budget=8, domain_budget=5, arity=4)
        baseline = candidate.place_partition(graph, plan)
        RECOVERY.install_recovery_hint(candidate, {"cluster_id": plan["root_cluster"],
            "horizontal_layout": baseline.packing["horizontal_layout"], "initial_apron": 24})
        replayed = candidate.place_partition(graph, plan)
        assert candidate._literal_states(baseline.iter_placements()) == candidate._literal_states(replayed.iter_placements())
        assert candidate.audit_placement(graph, plan, replayed)["shared_contacts"] == 18
    finally:
        sys.modules.pop(spec.name, None)
