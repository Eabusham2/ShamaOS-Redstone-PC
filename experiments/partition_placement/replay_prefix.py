"""Replay the exact retained parent221 endpoint allocation without opaque state.

This proves only prefix allocation. Native subtree geometry was measured in the
separate parent reports; complete wire isolation and runtime remain later gates.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import sys
import time

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY / "src"))

from shamaos.hardware import external_router
from shamaos.model import BlockState, Box, Placement, Vec3


def load_call(path: Path):
    data = path.read_bytes()
    payload = gzip.decompress(data) if data.startswith(b"\x1f\x8b") else data
    case = json.loads(payload)
    if case.get("schema") != "shama-bounded-prefix-v1":
        raise ValueError("unsupported bounded-prefix fixture schema")
    vector = lambda point: Vec3(*point)
    box = lambda value: Box(vector(value[0]), vector(value[1]))
    blocks = [Placement(vector(block["position"]),
                        BlockState.of(block["name"], **block["properties"]), block["component"])
              for block in case["obstacles"]]
    arguments = ([vector(point) for point in case["points"]], case["branch_y"], blocks)
    keywords = {
        "access": {vector(item["position"]): tuple(item["directions"]) for item in case["access"]},
        "minimum_y": case["minimum_y"], "maximum_y": case["maximum_y"],
        "endpoint_bounds": {vector(item["position"]): box(item["bounds"]) for item in case["endpoint_bounds"]},
        "obstacle_bounds": [box(item) for item in case["obstacle_bounds"]],
        "track_targets": None if case["track_targets"] is None else {vector(point): z for point, z in case["track_targets"]},
        "transition_run": case["transition_run"],
        "reserved_branch_x": tuple(case["reserved_branch_x"]),
    }
    return case, arguments, keywords


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path,
                        default=Path(__file__).parent / "evidence/parent-221-row-prefix-input.json.gz")
    parser.add_argument("--north-lanes", type=int, default=128)
    parser.add_argument("--output", type=Path,
                        default=REPOSITORY / "build/partition-placement/prefix-replay.json")
    args = parser.parse_args()
    if args.north_lanes < 1:
        parser.error("--north-lanes must be a positive integer")
    case, arguments, keywords = load_call(args.fixture)
    report = {
        "scope": case["scope"], "fixture_provenance": case["provenance"],
        "fixture_sha256": hashlib.sha256(args.fixture.read_bytes()).hexdigest(),
        "router_source_sha256": hashlib.sha256(Path(external_router.__file__).read_bytes()).hexdigest(),
        "endpoint_count": len(arguments[0]), "native_terminal_blocks": len(arguments[2]),
        "north_lane_limit": args.north_lanes, "full_wire_isolation_verified": False,
        "runtime_verified": False,
    }
    started = time.monotonic()
    try:
        escapes = external_router._plan_escapes(*arguments, **keywords, north_lane_limit=args.north_lanes)
        report.update(status="prefix-allocation-passed", allocated_prefixes=len(escapes),
            maximum_north_run_blocks=max((point.z - escape.escape_z for point, escape in escapes.items()
                if keywords["access"].get(point) == ("north",)), default=0))
    except ValueError as error:
        report.update(status="failed", error=str(error))
    report["seconds"] = time.monotonic() - started
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 1 if report["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
