"""Deterministic process accounting at the connected native runner's deadline."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from experiments.memory_controller_runtime import run_connected as runtime


@pytest.mark.parametrize(
    "times, observed_statuses, expected_code, timed_out, expected_sleeps, killed",
    [
        ([10, 10.125], [0], 0, False, [], False),
        ([10, 10.125], [7], 7, False, [], False),
        ([10, 11.125], [0], 0, True, [], False),
        ([10, 11.125], [7], 7, True, [], False),
        ([10, 11], [0], 0, True, [], False),
        ([10, 10.25, 11.125], [None, 0], 0, True, [0.5], False),
        ([10, 10.9, 10.999], [None, 0], 0, False, [0.1], False),
        ([10, 11, 11.125], [None, -9], -9, True, [], True),
        ([10, 11, 11.125], [None, 0], 0, True, [], True),
    ],
    ids=["timely-success", "timely-error", "late-success-already-reaped",
         "late-error-already-reaped", "exact-deadline", "completed-between-polls",
         "sleep-clamped-to-remaining-budget", "running-timeout-killed",
         "natural-success-during-timeout-reap"],
)
def test_execute_deadline_preserves_real_status_and_accounting(
        tmp_path, monkeypatch, times, observed_statuses, expected_code, timed_out,
        expected_sleeps, killed):
    """No child is launched and no actual clock, limit or sleep is used."""
    process = SimpleNamespace(pid=43210, returncode=None)
    usage = SimpleNamespace(ru_maxrss=321, ru_utime=0.2, ru_stime=0.03)
    clock = iter(times)
    remaining = iter(observed_statuses)
    waits, signals, sleeps, launches = [], [], [], []

    def popen(command, **kwargs):
        launches.append((command, kwargs))
        kwargs["stdout"].write('{"synthetic_process_accounting_test":true}\n')
        return process

    def wait4(pid, options):
        assert pid == process.pid
        waits.append(options)
        exit_code = next(remaining)
        return (0, 0, None) if exit_code is None else (pid, exit_code, usage)

    monkeypatch.setattr(runtime, "resource", object())
    monkeypatch.setattr(runtime.subprocess, "Popen", popen)
    monkeypatch.setattr(runtime.signal, "SIGKILL", 9, raising=False)
    monkeypatch.setattr(runtime.os, "WNOHANG", 1, raising=False)
    monkeypatch.setattr(runtime.os, "wait4", wait4, raising=False)
    # Fake wait statuses are decoded explicitly to make these process-control
    # boundary tests portable; production still calls the real POSIX decoder.
    monkeypatch.setattr(runtime.os, "waitstatus_to_exitcode", lambda status: status, raising=False)
    monkeypatch.setattr(runtime.os, "kill", lambda pid, sig: signals.append((pid, sig)))
    monkeypatch.setattr(runtime.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(runtime.time, "sleep", sleeps.append)

    result = runtime.execute(tmp_path / "runner", tmp_path / "stream", tmp_path / "scenario",
                             tmp_path, "boundary", 5, memory_limit_mib=512, wall_seconds=1)

    assert process.returncode == result["exit_code"] == expected_code
    assert result["explicit_wall_timeout_reached"] is timed_out
    assert result["wall_seconds"] == pytest.approx(times[-1] - times[0])
    assert result["wall_limit_seconds"] == result["cpu_limit_seconds"] == 1
    assert result["termination_signal"] == (-expected_code if expected_code < 0 else None)
    assert result["peak_rss_kib"] == usage.ru_maxrss
    assert result["user_cpu_seconds"] == usage.ru_utime
    assert result["system_cpu_seconds"] == usage.ru_stime
    assert result["address_space_limit_bytes"] == 512 << 20
    assert runtime._completed_before_wall_deadline(result) is (not timed_out)
    assert sleeps == pytest.approx(expected_sleeps)
    assert signals == ([(process.pid, runtime.signal.SIGKILL)] if killed else [])
    assert waits == [1] * (len(observed_statuses) - int(killed)) + ([0] if killed else [])
    assert next(clock, None) is None and next(remaining, None) is None
    assert len(launches) == 1
    assert launches[0][1]["env"]["SHAMA_NATIVE_PROGRESS"] == "1"
    assert json.loads((tmp_path / "boundary-process.json").read_text()) == result


@pytest.mark.parametrize("change", [
    {"explicit_wall_timeout_reached": True},
    {"explicit_wall_timeout_reached": 0},
    {"wall_seconds": 1},
    {"wall_seconds": 1.1},
    {"wall_seconds": -1},
    {"wall_seconds": float("nan")},
    {"wall_seconds": float("inf")},
    {"wall_seconds": True},
    {"wall_limit_seconds": 1.5},
    {"wall_limit_seconds": 0},
    {"wall_limit_seconds": True},
])
def test_deadline_acceptance_requires_complete_valid_process_accounting(change):
    record = {"exit_code": 0, "explicit_wall_timeout_reached": False,
              "wall_seconds": 0.1, "wall_limit_seconds": 1}
    assert runtime._completed_before_wall_deadline(record)
    assert not runtime._completed_before_wall_deadline(dict(record, **change))
    for missing in ("explicit_wall_timeout_reached", "wall_seconds", "wall_limit_seconds"):
        assert not runtime._completed_before_wall_deadline({key: value for key, value in record.items()
                                                            if key != missing})
