"""A route comparison must bind both workers and leave no orphaned process."""
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

DIRECTORY = Path(__file__).resolve().parents[1] / "experiments/partition_placement/prefix_precheck"
SPEC = importlib.util.spec_from_file_location("prefix_comparison_harness", DIRECTORY / "compare.py")
HARNESS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HARNESS)


@pytest.fixture
def worker_reports():
    reports = [json.loads((DIRECTORY / "evidence/complete-896" / f"{name}.json").read_bytes())
               for name in ("baseline", "candidate")]
    # These are synthetic reports for the new harness contract. Do not rewrite
    # the original run: its loader digest was measured only in a post-run audit.
    for report in reports:
        report["loader_sha256"] = hashlib.sha256((DIRECTORY.parent / "replay_prefix.py").read_bytes()).hexdigest()
        report["comparison_driver_sha256"] = hashlib.sha256((DIRECTORY / "compare.py").read_bytes()).hexdigest()
    return reports


def test_comparison_accepts_complete_equal_bindings(worker_reports):
    provenance = json.loads((DIRECTORY / "provenance.json").read_bytes())
    HARNESS.validate_bindings(worker_reports, provenance)


@pytest.mark.parametrize("field", HARNESS.BINDING_FIELDS)
def test_comparison_rejects_each_missing_worker_binding(worker_reports, field):
    del worker_reports[1][field]
    with pytest.raises(RuntimeError, match="binding"):
        HARNESS.validate_bindings(worker_reports, json.loads((DIRECTORY / "provenance.json").read_bytes()))


@pytest.mark.parametrize("fault", ["same-wrong-budget", "same-wrong-input", "incomplete",
                                    "wrong-allocator", "wrong-algorithm", "wrong-inventory"])
def test_equal_routes_cannot_hide_wrong_run_identity(worker_reports, fault):
    if fault == "same-wrong-budget":
        for report in worker_reports:
            report["north_lane_limit"] += 1
    elif fault == "same-wrong-input":
        for report in worker_reports:
            report["fixture_sha256"] = "0" * 64
    elif fault == "incomplete":
        worker_reports[1]["phase"] = "emitting"
    elif fault == "wrong-allocator":
        worker_reports[1]["allocator_sha256"] = worker_reports[0]["allocator_sha256"]
    elif fault == "wrong-algorithm":
        worker_reports[1]["algorithm"] = "baseline"
    else:
        for report in worker_reports:
            report["endpoint_directions"] = 1791
    with pytest.raises(RuntimeError):
        HARNESS.validate_bindings(worker_reports, json.loads((DIRECTORY / "provenance.json").read_bytes()))


@pytest.mark.parametrize("error", [OSError("injected heartbeat write failure"), KeyboardInterrupt()])
def test_parent_exception_stops_its_exact_worker(tmp_path, monkeypatch, error):
    processes = []
    spawn = HARNESS.subprocess.Popen
    def capture(*args, **kwargs):
        process = spawn(*args, **kwargs)
        processes.append(process)
        return process
    def fail_write(*args, **kwargs):
        raise error
    monkeypatch.setattr(HARNESS.subprocess, "Popen", capture)
    monkeypatch.setattr(HARNESS, "write_json", fail_write)
    with pytest.raises(type(error)):
        HARNESS.run_one(SimpleNamespace(output=tmp_path, wall_seconds=900), "baseline")
    assert len(processes) == 1
    assert processes[0].poll() is not None


def test_timeout_retains_budget_abort_and_cannot_publish_comparison(tmp_path):
    with pytest.raises(TimeoutError, match="wall budget"):
        HARNESS.run_one(SimpleNamespace(output=tmp_path, wall_seconds=0.000001), "baseline")
    heartbeat = json.loads((tmp_path / "baseline-heartbeat.json").read_bytes())
    assert heartbeat["status"] == "budget-aborted"
    assert heartbeat["exit_code"] is not None
    assert not (tmp_path / "comparison.json").exists()
