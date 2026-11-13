"""Execute the saved full cache through real exterior wires and physical banks.

Preparation, all-register clock delivery, and functional save/reopen are
separate resumable phases. Python supplies only the 72 original host inputs.
Every memory response and all 65,536 stored-bit observations come from the
saved native blocks running in the pinned redstone engine.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time

try:
    import resource
except ImportError:
    resource = None

from experiments.memory_controller_runtime import scenarios
from experiments.memory_controller_runtime.probe_controller import analyze_clocks, clock_scenario
from experiments.memory_controller_runtime.save_connected import _sha, _write
from shamaos import release_io
from shamaos.mchprs_import import export_world
from shamaos.mchprs_snapshot import ENGINE_REVISION, TERRAIN_PATCH, restore_snapshot
from shamaos.model import Vec3
from shamaos.worldio import NativeWorldReader


GENERATED = "complete-connected-native-cache-generated-readback-verified"
PREPARED = "complete-connected-cache-native-stream-prepared"
CLOCKS_PASSED = "complete-connected-cache-exterior-clocks-passed"
ROOT = Path(__file__).resolve().parents[2]
RUNTIME_SOURCES = (
    "experiments/memory_controller_runtime/run_connected.py",
    "experiments/memory_controller_runtime/scenarios.py",
    "experiments/memory_controller_runtime/probe_controller.py",
    "experiments/memory_controller_runtime/save_connected.py",
    "src/shamaos/mchprs_import.py", "src/shamaos/mchprs_snapshot.py",
    "src/shamaos/worldio.py", "src/shamaos/release_io.py", "src/shamaos/model.py",
)


def _helper_sources():
    return {name: _sha(ROOT / name) for name in RUNTIME_SOURCES}


def _check_helpers(expected):
    if expected != _helper_sources():
        raise ValueError("native runtime/oracle/import/snapshot helper sources changed")


def _record(path):
    path = Path(path)
    return {"path": str(path.resolve()), "sha256": _sha(path), "bytes": path.stat().st_size}


def _same(record):
    if _record(Path(record["path"])) != record:
        raise ValueError(f"accepted native input changed: {record['path']}")


def _maps(inputs, outputs):
    required = {scenarios.host(name, bit) for name, width in scenarios.HOST_WIDTHS.items()
                for bit in range(width)}
    if set(inputs) != required:
        raise ValueError("connected native runtime must retain exactly the 72 original host inputs")
    if len({tuple(point) for point in inputs.values()}) != 72:
        raise ValueError("connected native host terminals must all be distinct")
    stored = scenarios.storage_expect(scenarios.initial_words()).keys()
    if not stored <= outputs.keys() or sum(name.startswith("storage:") for name in outputs) != 65536:
        raise ValueError("connected native runtime lost physical stored-bit observations")
    if len({tuple(outputs[name]) for name in stored}) != 65536:
        raise ValueError("connected native stored-bit observations alias different physical bits")
    clocks = {name: Vec3(*point) for name, point in outputs.items()
              if name.startswith("register:") and name.endswith(":C")}
    if len(clocks) != 115 or len(set(clocks.values())) != 115:
        raise ValueError("connected native runtime lost one of the 115 original register clocks")
    return clocks


def prepare(build: Path, destination: Path) -> dict:
    """Export every native section once; retain exact maps and input hashes."""
    if destination.exists():
        raise FileExistsError(destination)
    build_report_path = build / "connected-build-report.json"
    report = json.loads(build_report_path.read_text())
    if report.get("status") != GENERATED or report.get("independent_union_agreement") is not True:
        raise ValueError("native execution requires the complete accepted connected world")
    memory = report["physical_memory"]
    if (memory["capacity_bytes"], memory["banks"], memory["rows_per_bank"], memory["stored_bits"]) != (8192, 2, 1024, 65536):
        raise ValueError("native execution requires both complete physical cache banks")
    if report["external_interface"]["nets"] != 78 or report["external_interface"]["host_drive_pads"] != 72:
        raise ValueError("native execution requires the complete connected interface")
    inputs_path, outputs_path = build / "native-inputs.json", build / "native-outputs.json"
    if (_sha(inputs_path) != report["native_input_map_sha256"]
            or _sha(outputs_path) != report["native_output_map_sha256"]):
        raise ValueError("connected native interface maps changed after saved-world validation")
    inputs, outputs = json.loads(inputs_path.read_text()), json.loads(outputs_path.read_text())
    _maps(inputs, outputs)
    world = build / "world"
    inventory = release_io.file_inventory(world)
    if release_io.inventory_digest(inventory) != report["world_inventory_sha256"]:
        raise ValueError("connected native world changed after complete readback")
    if _sha(Path(report["source_input_bundle"]["path"])) != report["source_input_bundle"]["sha256"]:
        raise ValueError("retained connected source/input bundle changed before preparation")
    destination.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    prepared = {"schema": "complete-connected-cache-runtime-input-v1", "status": "exporting-native-world",
        "build_report": _record(build_report_path), "native_world": str(world.resolve()),
        "source_input_bundle": report["source_input_bundle"],
        "world_inventory_sha256": report["world_inventory_sha256"],
        "preparation_source": _record(Path(__file__)),
        "runtime_helper_sources": _helper_sources(),
        "connected_cache_runtime_verified": False, "complete_machine_runtime_verified": False}
    _write(destination / "prepared.json", prepared)
    try:
        shutil.copyfile(inputs_path, destination / "inputs.json")
        shutil.copyfile(outputs_path, destination / "outputs.json")
        stream = destination / "world.jsonl"
        imported = export_world(world, stream)
        if imported["chunks"] != report["native_readback"]["chunks"] or imported["sections"] != imported["chunks"] * 24:
            raise ValueError("runtime export omitted saved connected-cache chunks or height sections")
        _write(destination / "import.json", imported)
        prepared.update(status=PREPARED, inputs=_record(destination / "inputs.json"),
            outputs=_record(destination / "outputs.json"), stream=_record(stream),
            import_report=_record(destination / "import.json"), seconds=time.monotonic() - started)
        _same(prepared["build_report"])
        if _sha(inputs_path) != prepared["inputs"]["sha256"] or _sha(outputs_path) != prepared["outputs"]["sha256"]:
            raise ValueError("native interface maps changed while preparing execution")
        _check_helpers(prepared["runtime_helper_sources"])
        _write(destination / "prepared.json", prepared)
        return prepared
    except Exception as error:
        prepared.update(status="failed", error_type=type(error).__name__, error=str(error),
                        seconds=time.monotonic() - started)
        _write(destination / "prepared.json", prepared)
        raise


def _load(prepared_directory):
    path = prepared_directory / "prepared.json"
    prepared = json.loads(path.read_text())
    if prepared.get("status") != PREPARED:
        raise ValueError("complete connected native preparation has not passed")
    for name in ("build_report", "inputs", "outputs", "stream", "import_report"):
        _same(prepared[name])
    _same(prepared["preparation_source"])
    _check_helpers(prepared["runtime_helper_sources"])
    if _sha(Path(prepared["source_input_bundle"]["path"])) != prepared["source_input_bundle"]["sha256"]:
        raise ValueError("retained connected source/input bundle changed")
    inputs = json.loads(Path(prepared["inputs"]["path"]).read_text())
    outputs = json.loads(Path(prepared["outputs"]["path"]).read_text())
    imported = json.loads(Path(prepared["import_report"]["path"]).read_text())
    if imported["stream_sha256"] != prepared["stream"]["sha256"]:
        raise ValueError("native preparation and complete import disagree")
    return prepared, imported, inputs, outputs, _maps(inputs, outputs)


def execute(runner, stream, scenario, destination, name, maximum_chunks, *,
            memory_limit_mib=3072, wall_seconds=5400, snapshot=False):
    """Bound and measure the real native child; preserve every failure artifact."""
    if resource is None:
        raise RuntimeError("native connected proof requires POSIX process limits and accounting")
    if type(memory_limit_mib) is not int or memory_limit_mib < 512 or type(wall_seconds) is not int or wall_seconds < 1:
        raise ValueError("native execution requires explicit positive memory and runtime bounds")
    stdout_path, stderr_path = destination / f"{name}-runtime.json", destination / f"{name}-progress.jsonl"
    snapshot_path = destination / f"{name}-snapshot.jsonl"
    command = [str(runner), str(stream), str(scenario), str(maximum_chunks)]
    if snapshot:
        if snapshot_path.exists() or snapshot_path.with_suffix(".jsonl.partial").exists():
            raise FileExistsError(snapshot_path)
        command += ["--snapshot", str(snapshot_path)]
    limit = memory_limit_mib << 20

    def limits():
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        resource.setrlimit(resource.RLIMIT_CPU, (wall_seconds, wall_seconds))

    started, timed_out = time.monotonic(), False
    with stdout_path.open("x") as stdout, stderr_path.open("x") as stderr:
        process = subprocess.Popen(command, stdout=stdout, stderr=stderr, preexec_fn=limits,
                                   env=dict(os.environ, SHAMA_NATIVE_PROGRESS="1"))
        _write(destination / f"{name}-process.json", {"pid": process.pid, "command": command,
            "address_space_limit_bytes": limit, "cpu_limit_seconds": wall_seconds,
            "wall_limit_seconds": wall_seconds, "status": "running"})
        while True:
            pid, status, usage = os.wait4(process.pid, os.WNOHANG)
            if pid:
                process.returncode = os.waitstatus_to_exitcode(status)
                break
            if time.monotonic() - started > wall_seconds:
                timed_out = True
                process.send_signal(signal.SIGKILL)
                _, status, usage = os.wait4(process.pid, 0)
                process.returncode = os.waitstatus_to_exitcode(status)
                break
            time.sleep(0.5)
    result = {"exit_code": process.returncode, "wall_seconds": time.monotonic() - started,
              "peak_rss_kib": usage.ru_maxrss, "user_cpu_seconds": usage.ru_utime,
              "system_cpu_seconds": usage.ru_stime, "address_space_limit_bytes": limit,
              "cpu_limit_seconds": wall_seconds, "wall_limit_seconds": wall_seconds,
              "explicit_wall_timeout_reached": timed_out,
              "termination_signal": -process.returncode if process.returncode < 0 else None,
              "stdout": _record(stdout_path), "stderr": _record(stderr_path)}
    if snapshot_path.is_file():
        result["snapshot_file"] = _record(snapshot_path)
    _write(destination / f"{name}-process.json", result)
    return result


def _validate_result(result, imported, scenario_record, expected_steps, expected_ticks, expected_assertions):
    if (result.get("status") != "passed" or result["imported_stream_sha256"] != imported["stream_sha256"]
            or result["scenario_sha256"] != scenario_record["sha256"] or result["source"] != imported["source"]):
        raise ValueError("native runtime result does not bind the complete saved input and scenario")
    if (result["loaded_chunks"] != imported["chunks"] or result["decoded_nonair_blocks"] != imported["nonair"]
            or result["initialized_nonair_blocks"] != imported["nonair"]
            or result["comparator_entities"] != imported["comparators"]):
        raise ValueError("native runtime omitted saved chunks, blocks, initialization or comparators")
    if (result["steps"], result["ticks"], result["assertions"]) != (expected_steps, expected_ticks, expected_assertions):
        raise ValueError("native runtime did not execute every requested step, tick and assertion")
    if result["complete_machine_runtime_verified"] is not False:
        raise ValueError("partition runtime must not become a whole-machine acceptance claim")
    if result["engine_revision"] != ENGINE_REVISION or result["engine_registry_patch_sha256"] != TERRAIN_PATCH:
        raise ValueError("native connected execution requires the exact pinned engine and reviewed terrain registry")


def _evaluate_clock_case(case, imported, drive, clock_points, ready, period, settle_ticks):
    """Recompute acceptance from bound scenarios and complete native traces."""
    _same(case["scenario"])
    _same(case["stdout"])
    scenario, samples = clock_scenario(drive, clock_points, ready,
                                       half_period=period, settle_ticks=settle_ticks)
    if json.loads(Path(case["scenario"]["path"]).read_text()) != scenario:
        raise ValueError("retained exterior-clock scenario differs from the complete requested pulse proof")
    if case["trace_samples"] != samples or case["exit_code"] != 0:
        raise ValueError("retained exterior-clock case is incomplete")
    result = json.loads(Path(case["stdout"]["path"]).read_text())
    captured = sum(step["ticks"] for step in scenario["steps"])
    _validate_result(result, imported, case["scenario"], len(scenario["steps"]),
                     scenario["initial_ticks"] + captured, 116)
    if any(len(trace) != captured or any(type(value) is not bool for value in trace)
           for trace in result["traces"].values()):
        raise ValueError("native clock traces lost samples or contain invalid values")
    checked = analyze_clocks(result, clock_points, period)
    return checked, {key: value for key, value in result.items() if key != "traces"}


def _verify_clock_report(report, prepared_directory, prepared, runner, imported, inputs, outputs, clock_points,
                         requested_half_period):
    if (report.get("status") != CLOCKS_PASSED or report.get("native_clock_gate_verified") is not True
            or report["world_stream"] != prepared["stream"]
            or report["prepared"] != _record(prepared_directory / "prepared.json")
            or report["runner"] != _record(runner)):
        raise ValueError("transactions require matching complete exterior-clock evidence")
    if report.get("runtime_helper_sources") != prepared["runtime_helper_sources"]:
        raise ValueError("clock evidence uses different runtime or oracle helpers")
    _check_helpers(report["runtime_helper_sources"])
    cases = report.get("cases")
    if not isinstance(cases, dict) or set(cases) != {"safe", "too-fast"}:
        raise ValueError("clock evidence must retain both exact safe and too-fast cases")
    safe_period = cases["safe"].get("half_period_ticks")
    settle = cases["safe"].get("settle_ticks")
    if type(safe_period) is not int or safe_period <= 1 or requested_half_period != safe_period:
        raise ValueError("transaction half-period must equal the actually accepted safe clock probe")
    drive = {name: Vec3(*inputs[scenarios.host(name)]) for name in ("clk", "rst")}
    ready = Vec3(*outputs[scenarios.port("req_ready")])
    verified = {}
    for name, period, expected, status in (("safe", safe_period, True, "passed"),
                                         ("too-fast", 1, False, "too-fast-failure-detected")):
        case = cases[name]
        if (case.get("status") != status or case.get("half_period_ticks") != period
                or case.get("settle_ticks") != settle):
            raise ValueError("clock case does not retain the accepted safe/negative timing contract")
        checked, _ = _evaluate_clock_case(case, imported, drive, clock_points, ready, period, settle)
        if checked["clock_gate_passed"] is not expected:
            raise ValueError("retained native clock samples do not prove the required safe/negative outcome")
        verified[name] = checked
    return verified


def clocks(prepared_directory, runner, destination, *, half_period, settle_ticks, **limits):
    prepared, imported, inputs, outputs, clock_points = _load(prepared_directory)
    destination.mkdir(parents=True, exist_ok=False)
    report = {"schema": "complete-connected-cache-exterior-clock-v1", "status": "running",
        "prepared": _record(prepared_directory / "prepared.json"), "runner": _record(runner),
        "helper_source": _record(Path(__file__)), "world_stream": prepared["stream"], "cases": {},
        "runtime_helper_sources": prepared["runtime_helper_sources"],
        "native_clock_gate_verified": False, "connected_cache_runtime_verified": False,
        "complete_machine_runtime_verified": False}
    path = destination / "clock-report.json"
    _write(path, report)
    try:
        drive = {name: Vec3(*inputs[scenarios.host(name)]) for name in ("clk", "rst")}
        ready = Vec3(*outputs[scenarios.port("req_ready")])
        for name, period, expected in (("safe", half_period, True), ("too-fast", 1, False)):
            _check_helpers(report["runtime_helper_sources"])
            _same(report["runner"])
            scenario, samples = clock_scenario(drive, clock_points, ready,
                                               half_period=period, settle_ticks=settle_ticks)
            scenario_path = destination / f"{name}-scenario.json"
            _write(scenario_path, scenario)
            case = {"status": "running", "scenario": _record(scenario_path), "trace_samples": samples,
                    "half_period_ticks": period, "settle_ticks": settle_ticks}
            report["cases"][name] = case
            _write(path, report)
            case.update(execute(runner, Path(prepared["stream"]["path"]), scenario_path, destination,
                                name, imported["chunks"] + 128, **limits))
            if case["exit_code"]:
                case["status"] = "execution-incomplete" if case["exit_code"] < 0 else "runner-rejected"
                raise RuntimeError(f"complete connected {name} clock execution exited {case['exit_code']}")
            checked, actual = _evaluate_clock_case(case, imported, drive, clock_points, ready, period, settle_ticks)
            case.update(checked)
            case["native_runtime"] = actual
            if case["clock_gate_passed"] is not expected:
                raise ValueError(f"complete connected {name} clock gate produced an unexpected outcome")
            case["status"] = "passed" if expected else "too-fast-failure-detected"
            _check_helpers(report["runtime_helper_sources"])
            _write(path, report)
        _same(report["runner"])
        _same(report["prepared"])
        _same(report["world_stream"])
        _same(report["helper_source"])
        _check_helpers(report["runtime_helper_sources"])
        report.update(status=CLOCKS_PASSED, native_clock_gate_verified=True)
        _write(path, report)
        return report
    except Exception as error:
        report.update(status="failed", error_type=type(error).__name__, error=str(error), native_clock_gate_verified=False)
        _write(path, report)
        raise


def _stored_native_bits(world, outputs, words):
    """Inspect saved locked repeaters before a fresh engine can initialize them."""
    reader = NativeWorldReader(world, max_cached_chunks=2)
    count = 0
    for name, expected in scenarios.storage_expect(words).items():
        state = reader.get_block(Vec3(*outputs[name]))
        properties = dict(state.properties)
        if (state.name != "minecraft:repeater" or properties.get("locked") != "true"
                or properties.get("powered") != str(expected).lower()):
            raise ValueError(f"saved native storage differs before fresh execution: {name}")
        count += 1
    if count != 65536:
        raise ValueError("native cache readback lost a stored bit")
    return count


def transactions(prepared_directory, runner, clock_report_path, destination, *,
                 half_period, initial_ticks, smoke=False, **limits):
    prepared, imported, inputs, outputs, clock_points = _load(prepared_directory)
    clock_report = json.loads(clock_report_path.read_text())
    checked_clock_gate = _verify_clock_report(clock_report, prepared_directory, prepared, runner,
        imported, inputs, outputs, clock_points, half_period)
    destination.mkdir(parents=True, exist_ok=False)
    report = {"schema": "complete-connected-cache-transaction-v1", "status": "running",
        "prepared": _record(prepared_directory / "prepared.json"), "runner": _record(runner),
        "clock_gate": _record(clock_report_path), "helper_source": _record(Path(__file__)),
        "runtime_helper_sources": prepared["runtime_helper_sources"],
        "clock_gate_revalidated_from_native_samples": checked_clock_gate,
        "half_period_ticks": half_period, "initial_ticks": initial_ticks, "cases": {},
        "smoke_only": smoke, "connected_cache_runtime_verified": False,
        "native_persistence_verified": False, "complete_machine_runtime_verified": False,
        "scope": "Only original host requests drive the actual full cache; every memory response and stored bit is produced by native circuits"}
    path = destination / "transaction-report.json"
    _write(path, report)
    try:
        cold = list(scenarios.functional_frames())
        if smoke:
            end = next(index for index, frame in enumerate(cold)
                       if frame.label == "initial-0000:return-idle:fall")
            cold = cold[:end + 1]
        cold_summary = scenarios.summarize_frames(cold)
        continuation = list(scenarios.functional_frames(cold_summary["final_words"], resume=True))

        def run_case(name, frames, stream, imported_case, snapshot):
            _check_helpers(report["runtime_helper_sources"])
            _same(report["runner"])
            scenario_path = destination / f"{name}-scenario.json"
            generated = scenarios.write_scenario(scenario_path, frames, inputs, outputs,
                half_period=half_period, initial_ticks=initial_ticks, trace_outputs=None)
            case = {"status": "running", "scenario": _record(scenario_path), "generated": generated,
                "summary": {key: value for key, value in scenarios.summarize_frames(frames).items()
                            if key != "final_words"}}
            report["cases"][name] = case
            _write(path, report)
            case.update(execute(runner, stream, scenario_path, destination, name,
                                imported_case["chunks"] + 128, snapshot=snapshot, **limits))
            if case["exit_code"]:
                case["status"] = "execution-incomplete" if case["exit_code"] < 0 else "runner-rejected"
                raise RuntimeError(f"actual connected {name} transaction execution exited {case['exit_code']}")
            actual = json.loads(Path(case["stdout"]["path"]).read_text())
            _validate_result(actual, imported_case, case["scenario"], len(frames),
                             generated["ticks"], generated["assertions"])
            if actual["trace_capture"]["mode"] != "disabled" or any(actual["traces"].values()):
                raise ValueError("full transaction assertions unexpectedly changed trace selection")
            if snapshot:
                if (actual["pending_ticks_after_scenario"] != 0 or actual["snapshot"]["status"] != "quiescent-snapshot-written"
                        or actual["snapshot"]["sha256"] != case["snapshot_file"]["sha256"]):
                    raise ValueError("actual transaction state has no exact quiescent snapshot")
            case["native_runtime"] = {key: value for key, value in actual.items() if key != "traces"}
            case["status"] = "passed"
            _check_helpers(report["runtime_helper_sources"])
            _same(report["runner"])
            _write(path, report)
            return case

        first = run_case("smoke" if smoke else "cold", cold, Path(prepared["stream"]["path"]), imported, not smoke)
        if smoke:
            for name in ("runner", "prepared", "clock_gate", "helper_source"):
                _same(report[name])
            _check_helpers(report["runtime_helper_sources"])
            report["status"] = "complete-connected-cache-first-read-passed-full-proof-pending"
            _write(path, report)
            return report
        restored_world = destination / "reopened-world"
        report["stage"] = "restore-actual-quiescent-snapshot"
        _write(path, report)
        _check_helpers(report["runtime_helper_sources"])
        restored = restore_snapshot(Path(first["snapshot_file"]["path"]), Path(prepared["native_world"]),
                                    restored_world, expected_sha256=first["snapshot_file"]["sha256"])
        if (restored["parent_imported_stream_sha256"] != prepared["stream"]["sha256"]
                or restored["parent_scenario_sha256"] != first["scenario"]["sha256"]):
            raise ValueError("restored native snapshot is not bound to the actual cold transaction run")
        report["native_restore"] = restored
        report["native_storage_bits_checked_before_fresh_process"] = _stored_native_bits(
            restored_world, outputs, cold_summary["final_words"])
        reopened_stream = destination / "reopened-world.jsonl"
        report["stage"] = "export-every-reopened-native-section"
        _write(path, report)
        _check_helpers(report["runtime_helper_sources"])
        reopened_import = export_world(restored_world, reopened_stream)
        _write(destination / "reopened-import.json", reopened_import)
        report["reopened_import"] = _record(destination / "reopened-import.json")
        report["reopened_stream"] = _record(reopened_stream)
        second = run_case("reopen", continuation, reopened_stream, reopened_import, True)
        total_frames = first["summary"]["frames"] + second["summary"]["frames"]
        total_boundaries = first["summary"]["storage_boundaries"] + second["summary"]["storage_boundaries"]
        total_storage = first["summary"]["full_storage_assertions"] + second["summary"]["full_storage_assertions"]
        if (total_frames, total_boundaries, total_storage) != (649, 53, 3_473_408):
            raise ValueError("complete connected runtime lost part of the independent transaction oracle")
        for name in ("runner", "prepared", "clock_gate", "helper_source"):
            _same(report[name])
        _same(prepared["stream"])
        _check_helpers(report["runtime_helper_sources"])
        report.update(status="complete-connected-cache-native-transactions-and-reopen-passed", stage="passed",
            frames=total_frames, complete_storage_boundaries=total_boundaries,
            complete_storage_assertions=total_storage, connected_cache_runtime_verified=True,
            native_persistence_verified=True)
        _write(path, report)
        return report
    except Exception as error:
        report.update(status="failed", error_type=type(error).__name__, error=str(error),
                      connected_cache_runtime_verified=False, native_persistence_verified=False)
        _write(path, report)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="mode", required=True)
    preparation = commands.add_parser("prepare")
    preparation.add_argument("build", type=Path)
    preparation.add_argument("output", type=Path)
    for name in ("clocks", "transactions"):
        sub = commands.add_parser(name)
        sub.add_argument("prepared", type=Path)
        sub.add_argument("runner", type=Path)
        sub.add_argument("output", type=Path)
        sub.add_argument("--half-period", required=True, type=int)
        sub.add_argument("--memory-limit-mib", type=int, default=3072)
        sub.add_argument("--wall-seconds", type=int, default=5400)
        if name == "clocks":
            sub.add_argument("--settle-ticks", required=True, type=int)
        else:
            sub.add_argument("--initial-ticks", required=True, type=int)
            sub.add_argument("--clock-report", required=True, type=Path)
            sub.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    if args.mode == "prepare":
        result = prepare(args.build, args.output)
    else:
        options = dict(memory_limit_mib=args.memory_limit_mib, wall_seconds=args.wall_seconds)
        if args.mode == "clocks":
            result = clocks(args.prepared, args.runner, args.output,
                            half_period=args.half_period, settle_ticks=args.settle_ticks, **options)
        else:
            result = transactions(args.prepared, args.runner, args.clock_report, args.output,
                                  half_period=args.half_period, initial_ticks=args.initial_ticks,
                                  smoke=args.smoke, **options)
    print(json.dumps({key: value for key, value in result.items()
                      if key in ("status", "native_clock_gate_verified", "connected_cache_runtime_verified",
                                 "native_persistence_verified", "complete_machine_runtime_verified")}, indent=2))
