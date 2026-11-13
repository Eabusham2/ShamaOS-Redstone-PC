"""Compare all 896 retained escape decisions and their emitted directional states.

The parent launches one measured child at a time, retains phase/heartbeat data,
and enforces a wall budget. This is a prefix proof, not a complete parent-world
or whole-computer runtime proof.
"""
from __future__ import annotations

if not __debug__:
    raise RuntimeError("physical proof entrypoints require assertions; do not run with Python -O")

import argparse
from dataclasses import asdict
import gzip
import hashlib
import importlib.util
from itertools import chain
import json
import math
from pathlib import Path
import struct
import subprocess
import sys
import time

DIRECTORY = Path(__file__).resolve().parent
REPOSITORY = DIRECTORY.parents[2]
sys.path.insert(0, str(REPOSITORY / "src"))
BINDING_FIELDS = (
    "fixture_sha256", "input_provenance", "shared_source_hashes", "loader_sha256",
    "comparison_driver_sha256", "endpoint_count", "north_lane_limit", "allocated_prefixes",
    "maximum_north_run_blocks", "common_track_z", "branch_y", "trunk_y",
    "branch_to_trunk_transition_included", "endpoint_directions", "emitted_state_operations",
    "selected_escapes_sha256", "all_directional_emissions_sha256",
)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def write_json(path, value):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def worker(args):
    try:
        import resource
    except ImportError as error:
        raise RuntimeError("measured prefix workers require the POSIX resource module") from error
    provenance = json.loads((DIRECTORY / "provenance.json").read_bytes())
    algorithm = args.worker
    source = DIRECTORY / ("baseline_external_router.py" if algorithm == "baseline"
                          else "candidate_external_router.py")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if digest != provenance[algorithm + "_sha256"]:
        raise RuntimeError("frozen allocator no longer matches its declared source hash")
    allocator = load("shamaos.hardware._prefix_comparison_" + algorithm, source)
    loader_path = DIRECTORY.parent / "replay_prefix.py"
    loader_digest = hashlib.sha256(loader_path.read_bytes()).hexdigest()
    loader = load("retained_prefix_input", loader_path)
    fixture = REPOSITORY / provenance["fixture"]
    fixture_digest = hashlib.sha256(fixture.read_bytes()).hexdigest()
    if fixture_digest != provenance["fixture_sha256"]:
        raise RuntimeError("retained prefix input no longer matches its declared digest")
    case, arguments, keywords = loader.load_call(fixture)
    path = args.output / (algorithm + ".json")
    report = {"algorithm": algorithm, "phase": "allocating", "status": "in-progress",
              "scope": "All retained endpoint prefixes; complete multi-net trunks and runtime are separate gates",
              "fixture_sha256": fixture_digest, "loader_sha256": loader_digest,
              "comparison_driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "allocator_sha256": digest, "input_provenance": case["provenance"],
              "endpoint_count": len(arguments[0]), "north_lane_limit": provenance["north_lane_limit"],
              "shared_source_hashes": {str(p.relative_to(REPOSITORY)): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in (REPOSITORY / "src/shamaos/hardware/routing.py",
                            REPOSITORY / "src/shamaos/hardware/memory.py",
                            REPOSITORY / "src/shamaos/model.py")}}
    write_json(path, report)
    started = time.monotonic()
    escapes = allocator._plan_escapes(*arguments, **keywords, north_lane_limit=provenance["north_lane_limit"])
    report["allocation_seconds"] = time.monotonic() - started
    if set(escapes) != set(arguments[0]) or len(escapes) != 896:
        raise RuntimeError("allocator did not preserve every retained endpoint")
    records = [{"point": asdict(point), "escape": asdict(escape)}
               for point, escape in sorted(escapes.items())]
    payload = json.dumps(records, sort_keys=True, separators=(",", ":")).encode()
    record_path = args.output / (algorithm + "-escapes.json.gz")
    record_path.write_bytes(gzip.compress(payload, mtime=0))
    report.update(phase="emitting", selected_escapes_sha256=hashlib.sha256(payload).hexdigest(),
                  allocated_prefixes=len(escapes),
                  maximum_north_run_blocks=max((point.z - escape.escape_z for point, escape in escapes.items()
                      if keywords["access"].get(point) == ("north",)), default=0))
    write_json(path, report)
    # Exercise the exact selected paths in both electrical directions. All use
    # a common admissible track north of every landing; no net/trunk assignment
    # is inferred from this prefix-only fixture.
    branch_y = arguments[1]
    trunk_y = branch_y + 4
    track_z = min(escape.escape_z for escape in escapes.values()) - 16
    state_bytes = {}
    route_records = []
    overall = hashlib.sha256()
    emitted = 0
    emission_started = time.monotonic()
    for point, escape in sorted(escapes.items()):
        for forward in (True, False):
            state_hash = hashlib.sha256()
            count = 0
            from shamaos.model import Vec3
            branch = Vec3(escape.branch_x, branch_y, track_z + 4)
            trunk = Vec3(escape.branch_x, trunk_y, track_z)
            lower = allocator._endpoint_path(point, escape.escape_z, escape.branch_x,
                branch_y, branch.z, source=forward, component="prefix-equivalence",
                escape_x=escape.escape_x, wall_lead_z=escape.wall_lead_z,
                branch_direction=escape.branch_direction)
            transition = allocator.iter_stair(branch if forward else trunk,
                trunk if forward else branch, axis="z", signal_forward=True,
                component="prefix-equivalence")
            blocks = chain(lower, transition)
            for block in blocks:
                encoded_state = state_bytes.get(block.block)
                if encoded_state is None:
                    encoded_state = json.dumps([block.block.name, block.block.properties],
                                               separators=(",", ":")).encode()
                    state_bytes[block.block] = encoded_state
                encoded = struct.pack(">iiiI", block.pos.x, block.pos.y, block.pos.z, len(encoded_state)) + encoded_state
                state_hash.update(encoded)
                count += 1
            item = {"point": asdict(point), "source_direction": forward,
                    "emitted_operations": count, "state_operations_sha256": state_hash.hexdigest()}
            route_records.append(item)
            overall.update(json.dumps(item, sort_keys=True, separators=(",", ":")).encode() + b"\n")
            emitted += count
    route_payload = json.dumps(route_records, sort_keys=True, separators=(",", ":")).encode()
    (args.output / (algorithm + "-emitted-paths.json.gz")).write_bytes(gzip.compress(route_payload, mtime=0))
    peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        peak_rss = (peak_rss + 1023) // 1024
    report.update(phase="complete", status="prefix-and-directional-state-emission-passed",
                  emission_seconds=time.monotonic() - emission_started,
                  endpoint_directions=len(route_records), emitted_state_operations=emitted,
                  all_directional_emissions_sha256=overall.hexdigest(), common_track_z=track_z,
                  branch_to_trunk_transition_included=True, branch_y=branch_y, trunk_y=trunk_y,
                  peak_rss_kib=peak_rss,
                  total_seconds=time.monotonic() - started)
    write_json(path, report)


def current_rss(pid):
    try:
        line = next(line for line in Path(f"/proc/{pid}/status").read_text().splitlines()
                    if line.startswith("VmRSS:"))
        return int(line.split()[1])
    except (OSError, StopIteration, ValueError):
        return None


def run_one(args, algorithm):
    started = time.monotonic()
    command = [sys.executable, str(Path(__file__).resolve()), "--worker", algorithm,
               "--output", str(args.output), "--wall-seconds", str(args.wall_seconds)]
    with (args.output / (algorithm + ".log")).open("w") as output:
        process = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT)
        peak = 0
        heartbeat_path = args.output / (algorithm + "-heartbeat.json")
        heartbeat = {"algorithm": algorithm, "pid": process.pid, "elapsed_seconds": 0,
                     "current_rss_kib": None, "observed_peak_rss_kib": 0,
                     "wall_budget_seconds": args.wall_seconds}
        try:
            while process.poll() is None:
                elapsed = time.monotonic() - started
                rss = current_rss(process.pid)
                peak = max(peak, rss or 0)
                heartbeat = {"algorithm": algorithm, "pid": process.pid,
                             "elapsed_seconds": elapsed, "current_rss_kib": rss,
                             "observed_peak_rss_kib": peak, "wall_budget_seconds": args.wall_seconds}
                write_json(heartbeat_path, heartbeat)
                if elapsed >= args.wall_seconds:
                    raise TimeoutError(f"{algorithm} exceeded its explicit wall budget; no equivalence pass")
                try:
                    process.wait(timeout=min(10, max(0.1, args.wall_seconds - elapsed)))
                except subprocess.TimeoutExpired:
                    pass
        except BaseException as error:
            heartbeat.update(status="budget-aborted" if isinstance(error, TimeoutError) else "interrupted",
                             error=str(error), elapsed_seconds=time.monotonic() - started)
            raise
        finally:
            # A failed heartbeat write, cancellation or parent exception must
            # not orphan a worker after its caller has released the large slot.
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            heartbeat["exit_code"] = process.returncode
            try:
                write_json(heartbeat_path, heartbeat)
            except OSError:
                pass
        heartbeat.update(status="completed" if process.returncode == 0 else "failed",
                         exit_code=process.returncode, elapsed_seconds=time.monotonic() - started)
        write_json(heartbeat_path, heartbeat)
        if process.returncode:
            raise RuntimeError(f"{algorithm} exited {process.returncode}; inspect its retained log")
    print(json.dumps(json.loads((args.output / (algorithm + ".json")).read_bytes())), flush=True)


def validate_bindings(reports, provenance):
    if len(reports) != 2 or [report.get("algorithm") for report in reports] != ["baseline", "candidate"]:
        raise RuntimeError("comparison must retain exactly the two declared allocator workers")
    for report in reports:
        if (report.get("status"), report.get("phase")) != ("prefix-and-directional-state-emission-passed", "complete"):
            raise RuntimeError("an incomplete worker cannot publish an equivalence pass")
        if report.get("allocator_sha256") != provenance[report["algorithm"] + "_sha256"]:
            raise RuntimeError("worker allocator binding differs from the declared frozen snapshot")
    for field in BINDING_FIELDS:
        if field not in reports[0] or field not in reports[1] or reports[0][field] != reports[1][field]:
            raise RuntimeError(f"workers differ in required binding {field}; no equivalence pass")
    before = reports[0]
    if before["fixture_sha256"] != provenance["fixture_sha256"] or before["north_lane_limit"] != provenance["north_lane_limit"]:
        raise RuntimeError("comparison input or lane budget differs from its declared provenance")
    if (before["endpoint_count"], before["allocated_prefixes"], before["endpoint_directions"]) != (896, 896, 1792):
        raise RuntimeError("comparison lost or invented an endpoint or electrical direction")
    if before["branch_to_trunk_transition_included"] is not True or before["emitted_state_operations"] <= 0:
        raise RuntimeError("comparison did not emit complete directional paths")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=REPOSITORY / "build/partition-placement/prefix-precheck")
    parser.add_argument("--wall-seconds", type=float, default=900)
    parser.add_argument("--worker", choices=("baseline", "candidate"), help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not math.isfinite(args.wall_seconds) or args.wall_seconds <= 0:
        parser.error("wall budget must be positive")
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.worker:
        worker(args)
        return
    if any((args.output / name).exists() for name in ("baseline.json", "candidate.json", "comparison.json")):
        parser.error("use a fresh output directory; earlier proof records are retained")
    for algorithm in ("baseline", "candidate"):
        run_one(args, algorithm)
    reports = [json.loads((args.output / (algorithm + ".json")).read_bytes())
               for algorithm in ("baseline", "candidate")]
    provenance = json.loads((DIRECTORY / "provenance.json").read_bytes())
    validate_bindings(reports, provenance)
    for suffix in ("escapes", "emitted-paths"):
        before, after = [gzip.decompress((args.output / (algorithm + "-" + suffix + ".json.gz")).read_bytes())
                         for algorithm in ("baseline", "candidate")]
        if before != after:
            raise RuntimeError(f"prefix precheck changed exact {suffix}; no equivalence pass")
    summary = {"status": "exact-896-endpoint-equivalence-passed",
               "scope": "All retained selected prefixes and both electrical-direction state emissions; no complete parent/net-trunk or Minecraft runtime claim",
               "endpoint_count": reports[0]["endpoint_count"],
               "source_and_sink_paths": reports[0]["endpoint_directions"],
               "emitted_state_operations": reports[0]["emitted_state_operations"],
               "selected_escapes_sha256": reports[0]["selected_escapes_sha256"],
               "all_directional_emissions_sha256": reports[0]["all_directional_emissions_sha256"],
               "allocation_seconds": {r["algorithm"]: r["allocation_seconds"] for r in reports},
               "fixture_sha256": reports[0]["fixture_sha256"],
               "loader_sha256": reports[0]["loader_sha256"],
               "comparison_driver_sha256": reports[0]["comparison_driver_sha256"],
               "shared_source_hashes": reports[0]["shared_source_hashes"],
               "compared_binding_fields": list(BINDING_FIELDS),
               "source_hashes": {r["algorithm"]: r["allocator_sha256"] for r in reports}}
    write_json(args.output / "comparison.json", summary)
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
