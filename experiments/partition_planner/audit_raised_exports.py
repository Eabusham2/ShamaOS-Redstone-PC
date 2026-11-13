"""Read-only independent audit of a raised-export placement candidate.

Reconstructs the small complete geometry and audits retained actual-engine
traces. Fresh native-world replay is an independent sibling-agent gate.
"""
from __future__ import annotations

if not __debug__:
    raise RuntimeError("The independent placement acceptance requires assertions; -O/-OO is unsupported")

import argparse
import ast
from collections import Counter
from copy import deepcopy
from dataclasses import asdict
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

from shamaos.hardware.partition_planner import Graph
from shamaos.model import BlockState, Box, Vec3


def require(value, message):
    if not value:
        raise RuntimeError(message)


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def source_review(baseline_path, candidate_path):
    baseline = ast.parse(baseline_path.read_text())
    candidate = ast.parse(candidate_path.read_text())
    measured = any(isinstance(node, ast.FunctionDef) and node.name == "_connect_parent_attempt"
                   for node in candidate.body)
    original = next(node for node in baseline.body
                    if isinstance(node, ast.FunctionDef) and node.name == "_connect_parent")
    core = next(node for node in candidate.body if isinstance(node, ast.FunctionDef)
                and node.name == ("_connect_parent_attempt" if measured else "_connect_parent"))
    original_front = next(node.value for node in ast.walk(original)
                          if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
                          and node.targets[0].id == "front_z")
    original_export = next(node.value.args[1] for node in ast.walk(original)
                           if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Subscript)
                           and isinstance(node.targets[0].value, ast.Name)
                           and node.targets[0].value.id == "external_points")
    replaced = []
    for node in ast.walk(core):
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "front_z":
            replaced.append("front_z")
            node.value = deepcopy(original_front)
        if (isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Subscript)
                and isinstance(node.targets[0].value, ast.Name) and node.targets[0].value.id == "external_points"):
            replaced.append("export_y")
            node.value.args[1] = deepcopy(original_export)
    require(sorted(replaced) == ["export_y", "front_z"], "unexpected geometry edits")
    if measured:
        candidate.body = [node for node in candidate.body
                          if not (isinstance(node, ast.FunctionDef) and node.name == "_connect_parent")]
        require([arg.arg for arg in core.args.kwonlyargs] == ["export_apron"], "unexpected core parameters")
        core.name = "_connect_parent"
        core.args.kwonlyargs = []
        core.args.kw_defaults = []
    require(ast.dump(candidate, include_attributes=False) == ast.dump(baseline, include_attributes=False),
            "candidate changes more than the reviewed geometry expressions and retry wrapper")
    return {"core_geometry_edits_only": True, "all_other_module_code_ast_identical": True,
            "measured_retry_wrapper": measured,
            "baseline_sha256": hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
            "candidate_sha256": hashlib.sha256(candidate_path.read_bytes()).hexdigest()}


def wrapper_controls(module):
    if not hasattr(module, "_connect_parent_attempt"):
        return {"applicable": False}
    original = module._connect_parent_attempt
    checks = []

    def placed(fronts, maximum, packing):
        physical = SimpleNamespace(input_ports={str(i): (Vec3(0, 0, z),) for i, z in enumerate(fronts)},
                                   output_ports={})
        return SimpleNamespace(modules={0: SimpleNamespace(physical=physical)} if fronts else {},
                               bounds=Box(Vec3(0, 0, 0), Vec3(0, 0, maximum)), packing=packing)

    try:
        calls = []
        def eventually_exposed(context, cluster, children, maximum_y, packing, *, export_apron):
            calls.append(export_apron)
            return placed([100], 164, packing) if len(calls) == 1 else placed([200], 200, packing)
        module._connect_parent_attempt = eventually_exposed
        result = module._connect_parent(None, 0, (), 319, {})
        require(calls == [24, 92] and result.packing["export_apron"] == 92, "wrong measured apron advancement")
        checks.append("measured 64-block burial advances apron24 to92 and accepts exposed second attempt")

        calls = []
        def forever_buried(context, cluster, children, maximum_y, packing, *, export_apron):
            calls.append(export_apron)
            return placed([100], 101, packing)
        module._connect_parent_attempt = forever_buried
        try:
            module._connect_parent(None, 0, (), 319, {})
        except module.PlacementError:
            require(calls == list(range(24, 64, 5)), "retry limit or monotonic growth changed")
        else:
            raise RuntimeError("accepted permanently buried ports")
        checks.append("eight unsuccessful measurements fail without publishing a partial result")

        module._connect_parent_attempt = lambda c, i, children, y, packing, **kw: placed([100, 101], 102, packing)
        try:
            module._connect_parent(None, 0, (), 319, {})
        except module.PlacementError:
            checks.append("mixed external export planes rejected")
        else:
            raise RuntimeError("mixed export planes accepted")

        calls = []
        def intrusion(*args, **kwargs):
            calls.append(kwargs["export_apron"])
            raise module.PlacementError("injected original intrusion guard")
        module._connect_parent_attempt = intrusion
        try:
            module._connect_parent(None, 0, (), 319, {})
        except module.PlacementError as error:
            require(str(error) == "injected original intrusion guard" and calls == [24],
                    "wrapper bypassed an original geometry rejection")
        else:
            raise RuntimeError("original guard ignored")
        checks.append("original geometry errors propagate without relaxing or bypassing the guard")
    finally:
        module._connect_parent_attempt = original
    return {"applicable": True, "checks": checks}


def point(value):
    return tuple(value[axis] for axis in ("x", "y", "z"))


def record(placement):
    return ((placement.pos.x, placement.pos.y, placement.pos.z),
            placement.block.name, placement.block.properties)


def audit_traces(case, retained, runtime, half_period):
    traces = retained["traces"]
    require(retained["status"] == "passed", "retained engine failed")
    require(traces["a:C"] == traces["b:C"], "clock leaves skew in retained runtime")
    edges = [tick for tick, value in enumerate(traces["a:C"])
             if value and (not tick or not traces["a:C"][tick - 1])]
    fall = [tick for tick, value in enumerate(traces["a:C"])
            if tick and not value and traces["a:C"][tick - 1]]
    state, clock, data, expected = [False, False], False, False, []
    for step in case["steps"]:
        drive = step.get("drive", {})
        data = drive.get("d", data)
        next_clock = drive.get("clk", clock)
        if next_clock and not clock:
            state = [data, state[0]]
            expected.append(state[:])
        clock = next_clock
    require(len(edges) == len(fall) == len(expected) == 16, "lost/invented clock edge")
    require(all(end - start == half_period for start, end in zip(edges, fall)), "clock width changed")
    require(expected == runtime["expected_transfers"], "retained expected transfer list changed")
    require(all(traces["one"]) and not any(traces["zero"]), "constant output changed")
    failures, setup, hold = [], [], []
    for edge, wanted in zip(edges, expected):
        for index, name in enumerate(("a", "b")):
            q, d = traces[f"{name}:Q"], traces[f"{name}:D"]
            if q[edge + 12] != wanted[index]:
                failures.append({"edge": edge, "register": name,
                                 "expected": wanted[index], "actual": q[edge + 12]})
            setup.append(next((delta - 1 for delta in range(1, edge + 1) if d[edge - delta] != d[edge]), edge))
            hold.append(next((delta - 1 for delta in range(1, len(d) - edge) if d[edge + delta] != d[edge]),
                             len(d) - edge - 1))
    require(failures == runtime["pipeline_failures"], "independent transfer oracle differs")
    require(len(failures) == (0 if half_period == 512 else 8), "timing control changed")
    require(min(setup) >= 8 and min(hold) >= 2, "fixture local margins changed")
    return {"status": "passing waveform" if not failures else "expected invalid timing detected",
            "half_period_rt": half_period, "engine_revision": retained["revision"],
            "clock_edges_per_leaf": len(edges), "clock_pulse_width_rt": half_period,
            "leaf_clock_skew_rt": 0, "incorrect_transfers": len(failures),
            "minimum_fully_stable_setup_rt": min(setup), "minimum_fully_stable_hold_rt": min(hold),
            "oracle_expected_values_derived_from_input_steps": True,
            "retained_actual_trace_audited": True, "fresh_engine_rerun_in_this_audit": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("artifacts", type=Path)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    review = source_review(args.repository / "src/shamaos/hardware/partition_placement.py", args.candidate)
    candidate = load_module("shamaos.hardware._independent_raised_exports", args.candidate)
    oracle = load_module("independent_manifest_oracle", args.repository / "experiments/partition_planner/audit_placement_manifest.py")
    graph = Graph.read(args.artifacts / "mapped.json", "pipeline")
    plan = json.loads((args.artifacts / "plan.json").read_text())
    manifest = json.loads((args.artifacts / "placement.json").read_text())
    inventory = oracle.audit(graph, plan, manifest)
    placed = candidate.place_partition(graph, plan)
    modules = placed.all_modules()
    raw_placements = list(placed.iter_placements())
    unique = {}
    for value in raw_placements:
        key = (value.pos.x, value.pos.y, value.pos.z)
        require(unique.setdefault(key, value.block) == value.block, "conflicting literal states")
    require(len(unique) == manifest["unique_blocks"] == placed.unique_blocks, "literal count differs")
    chunks = len({(x // 16, z // 16) for x, y, z in unique})
    require(chunks == manifest["occupied_chunks"], "literal chunk union differs")
    require(asdict(placed.bounds) == manifest["bounds"], "physical bounds differ")
    require({name: asdict(p) for name, p in placed.terminals.items()} == manifest["terminals"], "terminal positions differ")
    require({str(entity): asdict(p) for entity, p in placed.ports.items()} == manifest["original_ports"], "original port positions differ")
    face_uses = 0
    for signal in plan["signals"]:
        for link in signal["boundary_links"]:
            contact = placed.shared_contacts[link["id"]]
            for end in (link["source"], link["sink"]):
                key = ("leaf", end["cluster"]) if end["domain"] is None else ("domain", end["domain"])
                tile = modules[key]
                pin = contact["position"]
                escape = tile.escapes[pin]
                child = end["cluster"] == contact["child_cluster"]
                dz, face = (-1, "north") if child else (1, "south")
                require(escape.escape_x is None and escape.wall_lead_z is None
                        and (escape.escape_z - pin.z) * dz > 0, "actual contact escape face changed")
                net = next(n for n in tile.physical.nets if n.bit == signal["bit"])
                is_source = net.source.pin == end["terminal_id"]
                signal_direction = face if is_source else {"north": "south", "south": "north"}[face]
                # Java repeater block-state facing is its input direction.
                facing = {"north": "south", "south": "north"}[signal_direction]
                neighbor = unique[pin.x, pin.y, pin.z + dz]
                require(neighbor.name == "minecraft:repeater" and dict(neighbor.properties)["facing"] == facing,
                        f"literal first contact diode mismatch: {link['id']} {key} {pin} {neighbor} wanted {facing}")
                face_uses += 1
    observations, artifact_hashes = [], {}
    for period in (512, 20):
        path = args.artifacts / f"pipeline-{period}-dense-case.json"
        case = json.loads(path.read_text())
        runtime = json.loads((args.artifacts / f"pipeline-{period}-runtime.json").read_text())
        retained = json.loads(gzip.decompress((args.artifacts / f"pipeline-{period}-dense-result.json.gz").read_bytes()))
        require(hashlib.sha256(path.read_bytes()).hexdigest() == runtime["dense_case_sha256"], "runtime case hash differs")
        delta = Vec3(**runtime["native_to_dense_translation"])
        fresh = Counter(record(value) for value in placed.translated(delta).iter_placements())
        recorded = Counter((tuple(value["pos"]), value["name"], tuple(sorted(value["properties"].items())))
                           for value in case["blocks"])
        require(fresh == recorded, "recorded engine case omitted, duplicated or changed emitted state")
        observations.append(audit_traces(case, retained, runtime, period))
    for path in sorted(args.artifacts.iterdir()):
        if path.is_file():
            artifact_hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    report = {"status": "small complete candidate independently audited",
              "scope": "Complete retained small fixture and candidate source; larger subtree evidence is a separate gate",
              "source_review": review, "wrapper_controls": wrapper_controls(candidate),
              "inventory": inventory, "literal_emission_operations": len(raw_placements),
              "literal_unique_blocks": len(unique), "occupied_chunks": chunks,
              "bounds": asdict(placed.bounds), "actual_contact_faces_and_diodes_checked": face_uses,
              "all_recorded_engine_placements_equal_regenerated_states": True,
              "runtime_observations": observations, "artifact_sha256": artifact_hashes,
              "auditor_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
