"""Exercise the cache validation entry point and its retained evidence contract."""
import json
from pathlib import Path
import sys

import pytest

from experiments.partition_placement.port_egress_manifest import read_egress_manifest
from shamaos.hardware.bounded_facade import place_with_egress
from shamaos.hardware.partition_planner import Graph, build_plan
from tools import validate_connected_cache as validate


def test_cli_leaves_existing_evidence_and_requires_runner_before_creating_output(tmp_path):
    existing = tmp_path / "evidence"
    existing.mkdir()
    marker = existing / "validation-report.json"
    marker.write_text("preserved")
    with pytest.raises(FileExistsError):
        validate.main(["--output", str(existing), "--stop-after", "prepare"])
    assert marker.read_text() == "preserved"
    fresh = tmp_path / "fresh"
    with pytest.raises(ValueError, match="runner"):
        validate.main(["--output", str(fresh), "--stop-after", "clocks"])
    assert not fresh.exists()


def tiny_inputs(root, monkeypatch):
    source = root / "checkout"
    source.mkdir()
    (source / "input").write_text("first")
    monkeypatch.setattr(validate, "ROOT", source)
    monkeypatch.setattr(validate, "source_names", lambda: ("input",))
    monkeypatch.setattr(validate.shutil, "which", lambda name: sys.executable)
    return source


@pytest.mark.parametrize("tree", ["working", "snapshot"])
def test_source_change_and_reversal_during_a_stage_are_detected(tmp_path, monkeypatch, tree):
    source = tiny_inputs(tmp_path, monkeypatch)
    frozen = validate.FrozenInputs(tmp_path / "output", None)
    path = (source if tree == "working" else frozen.snapshot) / "input"
    original = path.read_bytes()
    path.write_text("second")
    path.write_bytes(original)
    with pytest.raises(ValueError, match="source changed"):
        frozen.check()


def test_failure_preserves_the_stage_artifacts_and_scope(tmp_path, monkeypatch):
    tiny_inputs(tmp_path, monkeypatch)
    output = tmp_path / "run"

    def fail_after_snapshot(source, directory, *, capacity_bytes):
        assert source == output / "source"
        assert capacity_bytes == 4096
        directory.mkdir(parents=True)
        (directory / "yosys.log").write_text("diagnostic")
        raise RuntimeError("synthesis interrupted")

    monkeypatch.setattr(validate.current_fixture, "prepare", fail_after_snapshot)
    with pytest.raises(RuntimeError, match="synthesis interrupted"):
        validate.main(["--output", str(output), "--stop-after", "prepare"])
    summary = json.loads((output / "validation-report.json").read_text())
    assert (summary["stage"], summary["status"], summary["error_type"]) == ("prepare", "failed", "RuntimeError")
    assert not summary["stage_reports"]
    assert not any(value for key, value in summary.items() if key.endswith(("_verified", "_eligible")))
    assert (output / "source/build/current/yosys.log").read_text() == "diagnostic"


def test_real_production_facade_is_accepted_and_replayed_by_connected_world_reader(tmp_path):
    source = validate.ROOT / "experiments/partition_placement/raised_exports/evidence/pipeline-mapped.json"
    graph = Graph.read(source, "pipeline")
    plan = build_plan(graph, cell_budget=1, boundary_budget=8, domain_budget=5)
    placed = place_with_egress(graph, plan)
    prepared = tmp_path / "current"
    prepared.mkdir()
    plan_path = prepared / "plan.json"
    plan_path.write_text(json.dumps(plan))
    original = plan_path.read_bytes()
    hashes = {name: validate.sha(validate.ROOT / name) for name in validate.source_names()}
    result = validate.write_controller_facade(graph, plan, placed, prepared, tmp_path / "geometry", hashes)
    assert result["plan_sha256"] == validate.sha(plan_path)
    assert result["status"] == validate.save_connected.PASSED_FACADE
    assert result["native_runtime_verified"] is False
    assert result["graph_audit"]["clock_leaf_uses"] > 0
    manifest = result["balanced_manifest"]
    restored = read_egress_manifest(Path(manifest["path"]), manifest["sha256"], graph, plan)
    assert restored.exposed_ports == placed.exposed_ports
    assert restored.unique_blocks == placed.unique_blocks
    assert plan_path.read_bytes() == original
    altered = plan | {"source": {}}
    with pytest.raises(ValueError, match="differs from its prepared"):
        validate.write_controller_facade(graph, altered, placed, prepared, tmp_path / "other", hashes)
    assert plan_path.read_bytes() == original
    assert not (tmp_path / "other").exists()


def audit_summary():
    return {"schema": "independent-complete-cache-native-audit-v1", "status": validate.AUDIT_PASSED,
            "native_clock_verified": True, "connected_cache_runtime_verified": True,
            "native_persistence_verified": True, "complete_machine_runtime_verified": False,
            "physical_input_review": {"register_clocks": 116, "stored_bits": 32768},
            "transactions": {"frames": 601, "full_storage_checkpoints": 49,
                             "all_bit_assertions": 1605632, "reopened_saved_bits_independently_checked": 32768},
            "clocks": {"safe": {"passed": True, "register_clocks": 116},
                       "too-fast": {"passed": False, "register_clocks": 116}}}


@pytest.mark.parametrize(("keys", "value"), [
    (("status",), "complete-cache-clocks-independently-verified-transactions-pending"),
    (("complete_machine_runtime_verified",), True),
    (("transactions", "all_bit_assertions"), 1605631),
    (("transactions", "reopened_saved_bits_independently_checked"), 32767),
    (("clocks", "too-fast", "passed"), True),
    (("clocks", "safe", "register_clocks"), 115),
])
def test_final_summary_rejects_incomplete_or_mismatched_audit(keys, value):
    summary = audit_summary()
    validate.checked_complete_audit(summary)
    target = summary
    for key in keys[:-1]:
        target = target[key]
    target[keys[-1]] = value
    with pytest.raises(ValueError, match="independent"):
        validate.checked_complete_audit(summary)
