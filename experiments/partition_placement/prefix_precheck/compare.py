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
import resource
import struct
import subprocess
import sys
import time

DIRECTORY = Path(__file__).resolve().parent
REPOSITORY = DIRECTORY.parents[2]
sys.path.insert(0, str(REPOSITORY / "src"))


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
    provenance = json.loads((DIRECTORY / "provenance.json").read_bytes())
    algorithm = args.worker
    source = DIRECTORY / ("baseline_external_router.py" if algorithm == "baseline"
                          else "candidate_external_router.py")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if digest != provenance[algorithm + "_sha256"]:
        raise RuntimeError("frozen allocator no longer matches its declared source hash")
    allocator = load("shamaos.hardware._prefix_comparison_" + algorithm, source)
    loader = load("retained_prefix_input", DIRECTORY.parent / "replay_prefix.py")
    fixture = REPOSITORY / provenance["fixture"]
    case, arguments, keywords = loader.load_call(fixture)
    path = args.output / (algorithm + ".json")
    report = {"algorithm": algorithm, "phase": "allocating", "status": "in-progress",
              "scope": "All retained endpoint prefixes; complete multi-net trunks and runtime are separate gates",
              "fixture_sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
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
    report.update(phase="complete", status="prefix-and-directional-state-emission-passed",
                  emission_seconds=time.monotonic() - emission_started,
                  endpoint_directions=len(route_records), emitted_state_operations=emitted,
                  all_directional_emissions_sha256=overall.hexdigest(), common_track_z=track_z,
                  branch_to_trunk_transition_included=True, branch_y=branch_y, trunk_y=trunk_y,
                  peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
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
        while process.poll() is None:
            elapsed = time.monotonic() - started
            rss = current_rss(process.pid)
            peak = max(peak, rss or 0)
            heartbeat = {"algorithm": algorithm, "pid": process.pid,
                         "elapsed_seconds": elapsed, "current_rss_kib": rss,
                         "observed_peak_rss_kib": peak, "wall_budget_seconds": args.wall_seconds}
            write_json(heartbeat_path, heartbeat)
            if elapsed >= args.wall_seconds:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                heartbeat.update(status="budget-aborted", exit_code=process.returncode)
                write_json(heartbeat_path, heartbeat)
                raise RuntimeError(f"{algorithm} exceeded its explicit wall budget; no equivalence pass")
            try:
                process.wait(timeout=min(10, max(0.1, args.wall_seconds - elapsed)))
            except subprocess.TimeoutExpired:
                pass
        heartbeat.update(status="completed" if process.returncode == 0 else "failed",
                         exit_code=process.returncode, elapsed_seconds=time.monotonic() - started)
        write_json(heartbeat_path, heartbeat)
        if process.returncode:
            raise RuntimeError(f"{algorithm} exited{process.returncode}; inspect its retained log")
    print(json.dumps(json.loads((args.output / (algorithm + ".json")).read_bytes())), flush=True)


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
               "source_hashes": {r["algorithm"]: r["allocator_sha256"] for r in reports}}
    write_json(args.output / "comparison.json", summary)
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
