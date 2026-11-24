"""Exercise the real preparation and its independent oracle without writing the large world."""
import json
import os
from pathlib import Path
import shutil

import pytest

from experiments.memory_controller_runtime import audit_native_connected as audit
from experiments.memory_controller_runtime import current_fixture
from shamaos.hardware.cache_layout import cache_policy
from shamaos.hardware.partition_planner import build_plan
from tools import validate_connected_cache as validation


def test_prepared_cache_can_feed_production_plan_and_independent_evidence_audit(tmp_path):
    if any(shutil.which(tool) is None for tool in ("yosys", "iverilog", "vvp")):
        pytest.skip("Yosys and Icarus Verilog are required for the real preparation")
    output = tmp_path / "cache-validation"
    assert validation.main(["--output", str(output), "--stop-after", "prepare"]) == 0

    summary = audit.read_json(output / "validation-report.json")
    assert summary["status"] == "stopped-after-prepare-full-validation-pending"
    assert set(summary["stage_reports"]) == {"prepare"}
    assert all(value is False for key, value in summary.items() if key.endswith(("_verified", "_eligible")))
    frozen = audit.read_json(audit.check_file(summary["source_inventory"]))
    prepared = audit.check_file(summary["stage_reports"]["prepare"]).parent
    assert prepared.parent.parent == Path(frozen["snapshot_root"]) == output / "source"
    assert (prepared / "source/rtl/shama_memory_adapter.sv").read_bytes() == (
        prepared.parent.parent / "rtl/shama_memory_adapter.sv").read_bytes()

    report, graph, _, _, _ = current_fixture.load_prepared(prepared)
    assert current_fixture.validate_interface(graph, 4096) == report["interface"]
    assert report["interface"]["host_bits"] == 72 and report["interface"]["memory_nets"] == 77
    policy = cache_policy(graph)
    assert policy["name"] == "one-bank-cache-root-io-guarded-v2"
    # The next stage re-plans with production cache-layout parameters and
    # compares the complete JSON, including the physical-mapping path.
    plan = build_plan(graph, cell_budget=policy["native_cells_per_leaf"],
                      boundary_budget=policy["boundary_terminals_per_leaf"],
                      domain_budget=policy["routing_terminals_per_domain"],
                      arity=policy["children_per_cluster"], ports_at_root=policy["ports_at_root"])
    assert json.loads(json.dumps(plan)) == audit.read_json(prepared / "plan.json")

    cold, reopened, final = audit.bound_transaction_frames()
    states = [f.stored_words for f in (*cold, *reopened) if f.stored_words is not None]
    assert (len(cold), len(reopened), len(states), len(final)) == (547, 54, 49, 1024)
    receipts = {name: audit.check_file(record) for name, record in summary["rtl_evidence"].items()}
    checked = audit.read_json(audit.check_file(summary["independent_rtl_oracle"]))
    assert audit.check_file(checked["fresh_expected"]) == receipts["expected.hex"]
    assert checked["full_storage_checkpoints"] == 49 and checked["checked_words"] == 49 * 1024
    expected = [int(value, 16) for value in receipts["expected.hex"].read_text().splitlines()]
    assert expected == [word for state in states for word in state]
    assert len(expected) * 32 == report["rtl_oracle"]["bit_boundary_assertions"] == 1_605_632
    assert {"positive.log", "swapped-model-read-bank.log", "swapped-model-write-bank.log"} <= receipts.keys()
    assert not (output / "independent-audit.json").exists()
    expected[100] ^= 1
    receipts["expected.hex"].write_text("".join(f"{value:08x}\n" for value in expected))
    with pytest.raises(ValueError, match="differs from the independent"):
        validation.check_fresh_rtl_oracle(prepared)


@pytest.mark.parametrize("variable,succeeds", [
    ("SHAMA_MCHPRS_TERRAIN_RUNNER", True), ("SHAMA_MCHPRS_SPARSE_RUNNER", False)])
def test_real_early_probe_proves_power_toggles_and_rejects_an_unpatched_engine(tmp_path, variable, succeeds):
    runner = os.environ.get(variable)
    if not runner or not Path(runner).is_file():
        pytest.skip(f"{variable} unavailable")
    source = tmp_path / "source-inventory.json"
    source.write_text('{"test_scope": "actual lever-and-lamp probe"}')
    probe = tmp_path / "probe"
    args = (probe, Path(runner), audit.file_record(source))
    limits = {"memory_limit_mib": 512, "wall_seconds": 30}
    if succeeds:
        receipt = validation.check_native_runner(*args, **limits)
        assert audit.check_file(receipt) == probe / "runner-probe.json"
    else:
        with pytest.raises(ValueError, match="complete successfully"):
            validation.check_native_runner(*args, **limits)
    report = audit.read_json(probe / "runner-probe.json")
    assert report["status"] == ("passed" if succeeds else "failed")
    assert report["readback"]["terrain_preserved"]
    assert audit.check_file(report["runner"]) == Path(runner).resolve()
    assert audit.check_file(report["source_inventory"]) == source
    assert report["connected_cache_runtime_verified"] is report["complete_machine_runtime_verified"] is False
    scenario = audit.read_json(audit.check_file(report["scenario"]))
    assert scenario["inputs"] == {}
    assert [set(step["expect"].values()) for step in scenario["steps"]] == [{False}, {True}, {False}]
    process = audit.read_json(audit.check_file(report["process"]))
    assert (process["exit_code"] == 0) is succeeds
    if succeeds:
        result = audit.read_json(audit.check_file(process["stdout"]))
        assert result["assertions"] == report["assertions"] == 6
        assert result["engine_revision"] == report["engine_revision"] == audit.ENGINE_REVISION
        assert result["engine_registry_patch_sha256"] == report["terrain_patch_sha256"] == audit.TERRAIN_PATCH
    else:
        failure = audit.check_file(process["stderr"]).read_text()
        assert "registry" in failure or "does not implement" in failure


def test_wrong_executable_stops_the_workflow_before_mapping_or_placement(tmp_path):
    if os.name != "posix" or any(shutil.which(tool) is None for tool in ("yosys", "iverilog", "vvp", "true")):
        pytest.skip("real POSIX tool preflight requires its toolchain")
    output = tmp_path / "fail-before-synthesis"
    with pytest.raises(ValueError):
        validation.main(["--output", str(output), "--runner", shutil.which("true")])
    progress = audit.read_json(output / "validation-report.json")
    assert progress["status"] == "failed" and progress["stage"] == "native-runner-probe"
    assert not progress["stage_reports"]
    assert (output / "native-runner-probe/runner-probe.json").is_file()
    assert not (output / "source/build/current").exists()
