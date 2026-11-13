"""Independently review the isolated, same-route flat-prefix precheck.

This is a small differential acceptance check, not whole-machine routing or a
performance benchmark.  It leaves production modules and source files alone.
"""
from __future__ import annotations

if not __debug__:
    raise RuntimeError("Prefix-review acceptance requires normal Python; -O/-OO is unsupported")

import argparse
import ast
from dataclasses import asdict
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import random
import sys
import textwrap
import time

from shamaos.hardware.memory import DUST, SUPPORT
from shamaos.model import Box, Placement, Vec3


def require(value, message):
    if not value:
        raise ValueError(message)


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# Deliberately exclude the original alternative-dependent own-climb predicate:
# q.offset(dy=2) in heads and q.offset(dy=1) in placed.  A later alternative
# moves that climb, so a block above today's flat endpoint is not permanent.
EXPECTED_INSERTION = textwrap.dedent("""
flat_checked = 0
flat_blocked = False
straight_face = len(modes) == 1 and modes[0] in ("north", "south")
if straight_face:
    if flat_blocked:
        continue
    direction = 1 if modes[0] == "south" else -1
    distance = abs(escape_z - p.z)
    for step in range(flat_checked + 1, distance + 1):
        q = p.offset(dz=direction * step)
        if (q in reserved_heads
            or volumes.blocking(q, own=own, endpoint=p, flat_leg_z=wall_lead_z)
            or q in placed
            or (q.offset(dy=-1) in placed and placed[q.offset(dy=-1)] != SUPPORT)
            or any(near in reserved or (near in terminals and near != p)
                   or (near in active and near != p) for near in _nearby_wire_points(q))):
            flat_blocked = True
            break
    if flat_blocked:
        continue
    flat_checked = distance
""")


def source_review(baseline_path, candidate_path):
    original = ast.parse(baseline_path.read_text())
    candidate = ast.parse(candidate_path.read_text())
    core = next(node for node in candidate.body
                if isinstance(node, ast.FunctionDef) and node.name == "_plan_escapes")
    assignments = {node.targets[0].id: node for node in ast.walk(core)
                   if isinstance(node, ast.Assign) and len(node.targets) == 1
                   and isinstance(node.targets[0], ast.Name)
                   and node.targets[0].id in {"flat_checked", "flat_blocked", "straight_face"}
                   and not (isinstance(node.value, ast.Name) and node.value.id == "distance")
                   and not (isinstance(node.value, ast.Constant) and node.value.value is True)}
    conditions = [node for node in ast.walk(core) if isinstance(node, ast.If)
                  and isinstance(node.test, ast.Name) and node.test.id == "straight_face"]
    require(set(assignments) == {"flat_checked", "flat_blocked", "straight_face"}
            and len(conditions) == 1, "unexpected precheck structure")
    inserted = ast.Module(body=[assignments[name] for name in
                               ("flat_checked", "flat_blocked", "straight_face")]
                         + conditions, type_ignores=[])
    require(ast.dump(inserted, include_attributes=False) ==
            ast.dump(ast.parse(EXPECTED_INSERTION), include_attributes=False),
            "unreviewed precheck predicate or state change")

    class RemoveReviewedInsertion(ast.NodeTransformer):
        def visit_Assign(self, node):
            if node in assignments.values():
                return None
            return self.generic_visit(node)

        def visit_If(self, node):
            if node in conditions:
                return None
            return self.generic_visit(node)

    stripped = RemoveReviewedInsertion().visit(candidate)
    require(ast.dump(stripped, include_attributes=False) ==
            ast.dump(original, include_attributes=False),
            "candidate alters original allocation, emission or final checks")
    return {
        "baseline_sha256": hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
        "candidate_sha256": hashlib.sha256(candidate_path.read_bytes()).hexdigest(),
        "only_reviewed_insertion": True,
        "all_original_allocation_emission_and_final_checks_ast_identical": True,
        "proof": [
            "One plain North/South access face has flat-leg distances clearance+6*lane; prefixes are nested.",
            "Every prechecked point is distinct from the endpoint and belongs to every later alternative.",
            "Prior wire/headroom reservations, terminals, component envelopes and placed states do not mutate while searching one endpoint.",
            "Each precheck rejection is an unconditional original rejection on that point; SUPPORT below the wire and the own-envelope access exemption are preserved.",
            "Alternative-dependent own-climb headroom, track turns and emitted diode states are not used to prune future alternatives.",
            "Mixed, East/West and wall faces bypass the precheck; every accepted path still passes all original checks.",
        ],
    }


def cases():
    p = Vec3(0, 1, 0)
    result = []

    def add(name, points=(p,), blocks=(), *, branch_y=9, mode=("north",),
            expect=None, expected_z=None, **kwargs):
        keywords = {"access": {point: mode for point in points}, "minimum_y": 0,
                    "north_lane_limit": 4, **kwargs}
        result.append((name, (list(points), branch_y, list(blocks)), keywords,
                       expect, expected_z))

    add("unobstructed-north", expect="success", expected_z=-6)
    add("unobstructed-south", mode=("south",), expect="success", expected_z=6)
    add("declared-endpoint-state-is-permitted", blocks=(Placement(p, DUST, "endpoint"),),
        expect="success", expected_z=-6)
    add("support-below-flat-wire-is-permitted",
        blocks=(Placement(p.offset(dy=-1, dz=-3), SUPPORT, "support"),),
        expect="success", expected_z=-6)
    add("active-state-below-flat-wire-is-rejected",
        blocks=(Placement(p.offset(dy=-1, dz=-3), DUST, "active"),), expect="failure")
    add("same-position-support-is-rejected",
        blocks=(Placement(p.offset(dz=-3), SUPPORT, "solid"),), expect="failure")
    add("adjacent-active-state-is-rejected",
        blocks=(Placement(p.offset(dx=1, dz=-3), DUST, "active"),), expect="failure")
    add("support-above-flat-wire-is-permitted",
        blocks=(Placement(p.offset(dy=1, dz=-3), SUPPORT, "ceiling"),),
        expect="success", expected_z=-6)
    add("alternative-dependent-climb-obstacle-can-move",
        blocks=(Placement(p.offset(dy=1, dz=-6), SUPPORT, "first-climb-ceiling"),),
        expect="success", expected_z=-12)
    own = Box(Vec3(-2, 0, -3), Vec3(2, 5, 3))
    add("own-envelope-flat-access-is-permitted", endpoint_bounds={p: own},
        obstacle_bounds=(own,), expect="success", expected_z=-6)
    foreign = Box(Vec3(-1, 0, -3), Vec3(1, 2, -2))
    add("foreign-envelope-is-rejected", obstacle_bounds=(foreign,), expect="failure")
    add("blocked-north-can-use-south", mode=("north", "south"),
        blocks=(Placement(p.offset(dz=-3), SUPPORT, "north-solid"),),
        expect="success", expected_z=6)
    add("south-fixed-prefix-is-rejected", mode=("south",),
        blocks=(Placement(p.offset(dz=3), SUPPORT, "south-solid"),), expect="failure")
    add("north-branch-turn-target", track_targets={p: -8}, expect="success", expected_z=-18)
    add("south-branch-turn-target", mode=("south",), track_targets={p: 8},
        expect="success", expected_z=18)
    add("reserved-columns-unchanged", reserved_branch_x=(0, 4, 8, 12), expect="success")
    add("minimum-height-is-rejected", minimum_y=1, expect="failure")
    add("ceiling-is-rejected", maximum_y=5, expect="failure")
    add("invalid-mode-is-rejected", mode=(), expect="failure")
    add("invalid-north-budget-is-rejected", north_lane_limit=True, expect="failure")
    for mode in (("west",), ("east",), ("wall-north",), ("wall-south",)):
        add("bypass-" + mode[0], mode=mode, expect="success")
    for mode in ("north", "south"):
        add("prior-stair-reservations-" + mode,
            points=(Vec3(0, 13, 0), Vec3(4, 11, 0), Vec3(12, 9, 0)), mode=(mode,))
        add("same-plane-terminal-prefix-" + mode,
            points=(Vec3(0, 1, 0), Vec3(0, 1, -4 if mode == "north" else 4)),
            mode=(mode,), expect="failure")

    rng = random.Random(20261113)
    for index in range(36):
        points = [Vec3(12 * i, rng.randrange(1, 10), rng.choice((0, 4, 12)))
                  for i in range(rng.randrange(1, 5))]
        modes = (("north",), ("south",), ("north", "south"), ("west", "north"))
        mode = modes[index % len(modes)]
        blocks = []
        if index % 3:
            anchor = rng.choice(points)
            blocks.append(Placement(anchor.offset(dx=rng.choice((-1, 0, 1)),
                                                 dy=rng.choice((-1, 0, 1)),
                                                 dz=rng.choice((-12, -3, 3, 12))),
                                    rng.choice((DUST, SUPPORT)), "random-obstacle"))
        add(f"seeded-mixed-geometry-{index}", points=points, blocks=blocks,
            branch_y=rng.choice((17, 21, 25)), mode=mode)
    return result


def solve(module, arguments, keywords):
    try:
        result = module._plan_escapes(*arguments, **keywords)
    except ValueError as error:
        return {"error": str(error)}, None
    literal_digest = hashlib.sha256()
    emitted = 0
    for point, escape in sorted(result.items()):
        for source in (False, True):
            for placement in module._endpoint_path(
                    point, escape.escape_z, escape.branch_x, arguments[1],
                    escape.escape_z + 100 * escape.branch_direction,
                    source=source, component="independent-prefix-review",
                    escape_x=escape.escape_x, wall_lead_z=escape.wall_lead_z,
                    branch_direction=escape.branch_direction):
                literal_digest.update(json.dumps(asdict(placement), sort_keys=True,
                                                 separators=(",", ":")).encode())
                literal_digest.update(b"\n")
                emitted += 1
    return {"escapes": [asdict(escape) for _, escape in sorted(result.items())],
            "literal_emissions": emitted, "literal_sha256": literal_digest.hexdigest()}, result


def compare_small_artifacts(before, after):
    baseline = json.loads(gzip.decompress((before / "complete-manifest.json.gz").read_bytes()))
    candidate = json.loads(gzip.decompress((after / "complete-manifest.json.gz").read_bytes()))
    for key in ("modules", "clusters", "shared_contacts"):
        require(baseline[key] == candidate[key], f"small saved geometry changed: {key}")
    report = {"complete_modules_clusters_contacts_identical": True, "dense_cases": {}}
    for period in (512, 20):
        filename = f"pipeline-{period}-dense-case.json"
        previous, actual = (before / filename).read_bytes(), (after / filename).read_bytes()
        require(previous == actual, f"small emitted states changed at half-period {period}")
        report["dense_cases"][str(period)] = {
            "bytes": len(actual), "sha256": hashlib.sha256(actual).hexdigest()}
    report["scope"] = "Read-only exact comparison of retained small geometry and inputs; fresh engine replay is the owner/sibling gate."
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--baseline", type=Path,
                        default=Path(__file__).resolve().parents[2] / "src/shamaos/hardware/external_router.py")
    parser.add_argument("--small-before", type=Path)
    parser.add_argument("--small-after", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    require(bool(args.small_before) == bool(args.small_after), "both small artifact paths are required")
    started = time.monotonic()
    baseline = load_module("shamaos.hardware._independent_prefix_baseline", args.baseline)
    candidate = load_module("shamaos.hardware._independent_prefix_candidate", args.candidate)
    report = {"schema": "shama-independent-prefix-precheck-v1",
              "auditor_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "source_review": source_review(args.baseline, args.candidate), "cases": []}
    emissions = 0
    for name, arguments, keywords, expected_kind, expected_z in cases():
        previous, previous_records = solve(baseline, arguments, keywords)
        actual, actual_records = solve(candidate, arguments, keywords)
        require(actual == previous, f"differential mismatch in {name}: {previous} != {actual}")
        kind = "failure" if "error" in actual else "success"
        if expected_kind is not None:
            require(kind == expected_kind, f"control {name} did not produce expected {expected_kind}: {actual}")
        if expected_z is not None:
            require(actual_records[arguments[0][0]].escape_z == expected_z,
                    f"control {name} selected the wrong alternative")
        emissions += actual.get("literal_emissions", 0)
        report["cases"].append({"name": name, "result": actual})
    report["summary"] = {
        "exact_escape_or_failure_cases": len(report["cases"]),
        "successful_cases": sum("escapes" in case["result"] for case in report["cases"]),
        "rejected_cases": sum("error" in case["result"] for case in report["cases"]),
        "exact_emitted_source_and_sink_state_operations": emissions,
        "seconds": round(time.monotonic() - started, 6),
        "scope": "Small differential/source proof only; retained 896-endpoint equivalence and broader integration remain separate gates. No preparation-speed, chunk or runtime-speed claim.",
    }
    if args.small_before:
        report["retained_small_pipeline"] = compare_small_artifacts(args.small_before, args.small_after)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
