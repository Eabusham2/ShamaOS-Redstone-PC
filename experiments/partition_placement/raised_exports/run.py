"""Exercise the isolated raised-export candidate; production defaults stay intact."""
if not __debug__:
    raise RuntimeError("physical proof entrypoints require assertions; do not run with Python -O")

from pathlib import Path
import argparse
import hashlib
import importlib.util
import json
import math
import os
import resource
import runpy
import subprocess
import sys
import time

DIRECTORY = Path(__file__).resolve().parent
REPOSITORY = DIRECTORY.parents[2]
sys.path[:0] = [str(REPOSITORY / "src"), str(REPOSITORY / "tests"), str(DIRECTORY)]
if len(sys.argv) < 2 or sys.argv[1] not in ("geometry", "pipeline", "tests"):
    raise SystemExit("usage: run.py {geometry,pipeline,tests} [proof arguments]")
mode = sys.argv.pop(1)
geometry_worker = "--geometry-worker" in sys.argv
if geometry_worker:
    sys.argv.remove("--geometry-worker")
if mode == "geometry" and not geometry_worker and not any(arg in ("-h", "--help") for arg in sys.argv):
    budget_parser = argparse.ArgumentParser(add_help=False)
    budget_parser.add_argument("--wall-seconds", type=float, default=2400)
    budget, arguments = budget_parser.parse_known_args(sys.argv[1:])
    if not math.isfinite(budget.wall_seconds) or budget.wall_seconds <= 0:
        raise SystemExit("--wall-seconds must be positive and finite")
    if "--output" in arguments:
        report_path = Path(arguments[arguments.index("--output") + 1]).resolve()
    else:
        report_path = REPOSITORY / "build/partition-placement/raised-exports/geometry.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    heartbeat_path = report_path.with_name(report_path.stem + "-heartbeat.json")
    command = [sys.executable, str(Path(__file__).resolve()), "geometry", "--geometry-worker", *arguments]
    process = subprocess.Popen(command)
    started = time.monotonic()
    peak = 0
    heartbeat = {"status": "running", "pid": process.pid, "command": command,
                 "wall_budget_seconds": budget.wall_seconds,
                 "candidate_sha256": hashlib.sha256((DIRECTORY / "partition_placement.py").read_bytes()).hexdigest(),
                 "launcher_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}

    def save_heartbeat():
        temporary = heartbeat_path.with_name(heartbeat_path.name + ".tmp")
        temporary.write_text(json.dumps(heartbeat, indent=2) + "\n")
        temporary.replace(heartbeat_path)

    try:
        while process.poll() is None:
            current = None
            try:
                line = next(line for line in Path(f"/proc/{process.pid}/status").read_text().splitlines()
                            if line.startswith("VmRSS:"))
                current = int(line.split()[1])
            except (OSError, StopIteration, ValueError):
                pass
            elapsed = time.monotonic() - started
            peak = max(peak, current or 0)
            heartbeat.update(elapsed_seconds=elapsed, current_rss_kib=current, observed_peak_rss_kib=peak)
            save_heartbeat()
            if elapsed >= budget.wall_seconds:
                raise TimeoutError("geometry proof exceeded its explicit wall budget")
            try:
                process.wait(timeout=min(10, max(0.1, budget.wall_seconds - elapsed)))
            except subprocess.TimeoutExpired:
                pass
    except BaseException as error:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        heartbeat.update(status="budget-aborted" if isinstance(error, TimeoutError) else "interrupted",
                         error=str(error), exit_code=process.returncode,
                         elapsed_seconds=time.monotonic() - started,
                         scope="Incomplete proof; preserved progress/manifest must not be read as a final union or runtime pass")
        save_heartbeat()
        raise
    heartbeat.update(status="completed" if process.returncode == 0 else "failed",
                     exit_code=process.returncode, elapsed_seconds=time.monotonic() - started)
    save_heartbeat()
    raise SystemExit(0 if process.returncode == 0 else 1)

name = "shamaos.hardware.partition_placement"
spec = importlib.util.spec_from_file_location(name, DIRECTORY / "partition_placement.py")
module = importlib.util.module_from_spec(spec)
sys.modules[name] = module
spec.loader.exec_module(module)
provenance = json.loads((DIRECTORY / "provenance.json").read_bytes())
if hashlib.sha256((DIRECTORY / "partition_placement.py").read_bytes()).hexdigest() != provenance["candidate_sha256"]:
    raise RuntimeError("candidate snapshot no longer matches its declared provenance")


def memory():
    current = None
    try:
        line = next(line for line in Path("/proc/self/status").read_text().splitlines()
                    if line.startswith("VmRSS:"))
        current = int(line.split()[1])
    except (OSError, StopIteration, ValueError):
        pass
    return {"current_rss_kib": current,
            "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}


attempt = module._connect_parent_attempt


def observed_attempt(context, cluster_id, children, maximum_y, packing, *, export_apron):
    started = time.monotonic()
    base = {"cluster_id": cluster_id, "layout": packing["horizontal_layout"],
            "export_apron": export_apron}
    print(json.dumps(dict(base, event="apron-start", **memory())), flush=True)
    try:
        result = attempt(context, cluster_id, children, maximum_y, packing, export_apron=export_apron)
    except Exception as error:
        print(json.dumps(dict(base, event="apron-error", error=str(error),
                              seconds=time.monotonic() - started, **memory())), flush=True)
        raise
    print(json.dumps(dict(base, event="apron-emitted", maximum_z=result.bounds.max.z,
                          seconds=time.monotonic() - started, **memory())), flush=True)
    return result


module._connect_parent_attempt = observed_attempt
if mode == "tests":
    os.chdir(REPOSITORY)
    import pytest
    raise SystemExit(pytest.main(["-q", "tests/test_partition_placement.py", *sys.argv[1:]]))
sys.argv[0] = str(DIRECTORY / ("prove_geometry.py" if mode == "geometry" else "prove_pipeline.py"))
runpy.run_path(sys.argv[0], run_name="__main__")
