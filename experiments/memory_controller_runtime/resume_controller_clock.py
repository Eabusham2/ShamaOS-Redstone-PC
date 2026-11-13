"""Run the complete saved controller clock gate without rebuilding its world.

This consumes the exact accepted native stream and original safe scenario. It
does not replace the clock with RTL or omit any controller section. The changed
runner representation must have passed its independent small differential gate.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import resource
import signal
import subprocess
import time

from experiments.memory_controller_runtime.probe_controller import _sha, analyze_clocks, clock_scenario
from shamaos.model import Vec3


STREAM_SHA = "f716c7ca10488300f37f5498be998e603fd700b64fd780506f436167fcaf9ea4"
SAFE_SCENARIO_SHA = "d7cfc346cfb12081c9b8f36789a63684ec490f1ab32aa064a1aec5339059ca57"


def record(path: Path) -> dict:
    return {"path": str(path.resolve()), "bytes": path.stat().st_size, "sha256": _sha(path)}


def run(runner: Path, stream: Path, scenario: Path, output: Path, name: str, maximum_chunks: int) -> dict:
    stdout_path, stderr_path = output / f"{name}-runtime.json", output / f"{name}-progress.jsonl"
    as_limit = 3 << 30
    cpu_limit = 900
    wall_limit = 1200

    def limits():
        resource.setrlimit(resource.RLIMIT_AS, (as_limit, as_limit))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_limit, cpu_limit))

    started = time.monotonic()
    timed_out = False
    with stdout_path.open("x") as stdout, stderr_path.open("x") as stderr:
        process = subprocess.Popen([str(runner), str(stream), str(scenario), str(maximum_chunks)],
            stdout=stdout, stderr=stderr, preexec_fn=limits,
            env=dict(os.environ, SHAMA_NATIVE_PROGRESS="1"))
        while True:
            pid, status, usage = os.wait4(process.pid, os.WNOHANG)
            if pid:
                process.returncode = os.waitstatus_to_exitcode(status)
                break
            if time.monotonic() - started > wall_limit:
                timed_out = True
                process.send_signal(signal.SIGKILL)
                _, status, usage = os.wait4(process.pid, 0)
                process.returncode = os.waitstatus_to_exitcode(status)
                break
            time.sleep(0.5)
    return {"exit_code": process.returncode, "wall_seconds": time.monotonic() - started,
            "peak_rss_kib": usage.ru_maxrss, "user_cpu_seconds": usage.ru_utime,
            "system_cpu_seconds": usage.ru_stime, "address_space_limit_bytes": as_limit,
            "cpu_limit_seconds": cpu_limit, "wall_limit_seconds": wall_limit,
            "explicit_wall_timeout_reached": timed_out, "stdout": record(stdout_path), "stderr": record(stderr_path)}


def main(artifact: Path, runner: Path, output: Path) -> dict:
    stream, original = artifact / "controller-world.jsonl", artifact / "clock-safe-scenario.json"
    if _sha(stream) != STREAM_SHA or _sha(original) != SAFE_SCENARIO_SHA:
        raise ValueError("saved controller stream or original safe scenario differs from accepted evidence")
    imported = json.loads((artifact / "controller-import.json").read_text())
    scenario = json.loads(original.read_text())
    clocks = {name: Vec3(*scenario["outputs"][name]) for name in scenario["trace_outputs"]}
    if len(clocks) != 115 or set(scenario["outputs"]) != {*clocks, "req_ready"}:
        raise ValueError("all 115 saved register clocks must remain observable")
    inputs = {name: Vec3(*point) for name, point in scenario["inputs"].items()}
    ready = Vec3(*scenario["outputs"]["req_ready"])
    settle = scenario["steps"][0]["ticks"]
    safe, _ = clock_scenario(inputs, clocks, ready, half_period=8192, settle_ticks=settle)
    if safe != scenario:
        raise ValueError("safe clock scenario no longer matches the complete original pulse sequence")
    negative, _ = clock_scenario(inputs, clocks, ready, half_period=1, settle_ticks=settle)
    output.mkdir(parents=True, exist_ok=False)
    report = {"schema": "saved-complete-controller-clock-resume-v1", "status": "running",
              "world_stream": record(stream), "runner": record(runner), "helper_source": record(Path(__file__)),
              "native_controller_registers": 115, "cases": {},
              "connected_cache_runtime_verified": False, "complete_machine_runtime_verified": False,
              "scope": "Actual native controller clock delivery only; two-bank cache transactions and whole-machine clock period remain unverified"}
    report_path = output / "clock-resume-report.json"

    def save():
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")

    save()
    try:
        for name, value, half_period, expected in (("safe", scenario, 8192, True), ("too-fast", negative, 1, False)):
            scenario_path = output / f"{name}-scenario.json"
            if name == "safe":
                scenario_path.write_bytes(original.read_bytes())
            else:
                scenario_path.write_text(json.dumps(value, separators=(",", ":")) + "\n")
            samples_per_output = sum(step["ticks"] for step in value["steps"])
            case = {"status": "running", "scenario": record(scenario_path), "trace_samples": samples_per_output * 115}
            report["cases"][name] = case
            save()
            case.update(run(runner, stream, scenario_path, output, name, imported["chunks"] + 16))
            if case["exit_code"]:
                case["status"] = "execution-incomplete" if case["exit_code"] < 0 else "runner-rejected"
                report["status"] = case["status"]
                report["native_clock_gate_verified"] = False
                save()
                return report
            result = json.loads((output / f"{name}-runtime.json").read_text())
            if (result["status"] != "passed" or result["imported_stream_sha256"] != STREAM_SHA
                    or result["scenario_sha256"] != case["scenario"]["sha256"]):
                raise ValueError("runtime result does not bind exact saved blocks and requested pulses")
            if result["loaded_chunks"] != imported["chunks"] or result["decoded_nonair_blocks"] != imported["nonair"]:
                raise ValueError("runtime omitted native controller chunks or blocks")
            if result["initialized_nonair_blocks"] != imported["nonair"]:
                raise ValueError("runtime did not initialize every saved non-air block")
            if result["ticks"] != value["initial_ticks"] + samples_per_output:
                raise ValueError("runtime executed a different tick count")
            if any(len(trace) != samples_per_output or any(type(sample) is not bool for sample in trace)
                   for trace in result["traces"].values()):
                raise ValueError("runtime trace has missing ticks or invalid samples")
            case.update(analyze_clocks(result, clocks, half_period))
            case["native_runtime"] = {key: result[key] for key in result if key != "traces"}
            if case["clock_gate_passed"] is not expected:
                case["status"] = "unexpected-clock-result"
                report["status"] = "clock-gate-failed"
                report["native_clock_gate_verified"] = False
                save()
                return report
            case["status"] = "passed" if expected else "too-fast-failure-detected"
            save()
            del result
        if record(runner) != report["runner"] or _sha(stream) != STREAM_SHA:
            raise ValueError("runner binary or saved stream changed during runtime proof")
        report.update(status="native-controller-clock-gate-passed", native_clock_gate_verified=True)
        save()
    except Exception as error:
        report.update(status="failed", error_type=type(error).__name__, error=str(error), native_clock_gate_verified=False)
        save()
        raise
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("runner", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(main(args.artifact, args.runner, args.output), indent=2))
