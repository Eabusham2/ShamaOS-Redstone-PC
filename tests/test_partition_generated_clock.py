"""Native generated-clock proofs; these do not establish complete-machine timing."""
from collections import defaultdict
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil

import pytest

from shamaos.hardware.local_router import LocalTile
from shamaos.hardware.logic import template_for
from shamaos.hardware.partition_planner import Graph, build_plan
from shamaos.hardware.partition_placement import (
    _literal_states, audit_placement, balance_partition_clocks, place_partition,
)
from shamaos.model import Vec3
from shamaos.synthesis import synthesize_json
from test_mchprs_runtime import high_runs, run_case, west_driver


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tests/fixtures/generated_clock.sv"


@pytest.fixture(scope="module")
def generated_clock(tmp_path_factory):
    yosys = shutil.which("yosys") or shutil.which("yowasp-yosys")
    if not yosys:
        pytest.skip("real Yosys is required for the generated-clock proof")
    directory = tmp_path_factory.mktemp("native-generated-clock")
    mapped = synthesize_json([SOURCE], top="generated_clock",
                             output_json=directory / "mapped.json", yosys=yosys)
    graph = Graph.read(mapped, "generated_clock")
    # A small domain budget exercises real clock padding across multiple
    # parent domains; every source cell, feedback sink and port is retained.
    plan = build_plan(graph, cell_budget=1, boundary_budget=8, domain_budget=5, arity=2)
    placed = place_partition(graph, plan)
    entities = {entity["id"]: entity for entity in graph.entities}
    ports = {(entity["port_name"], entity["port_index"]): entity["bit"]
             for entity in graph.entities if entity["kind"] == "port"}
    roles = {role: entities[graph.signals[ports[port]].source.entity]["name"]
             for role, port in (("divider", ("divided", 0)), ("a", ("q", 0)), ("b", ("q", 1)))}
    return {"graph": graph, "plan": plan, "placed": placed, "roles": roles,
            "bits": ports, "entities": entities, "directory": directory}


def native_pins(placed, roles):
    result = {}
    for tile in placed.all_modules().values():
        for name, origin in tile.physical.cell_origins.items():
            if name not in roles.values():
                continue
            role = next(role for role, cell in roles.items() if cell == name)
            assert template_for(tile.physical.cell_types[name]).cell_type == "DFF"
            for pin, offset in template_for("DFF").pins.items():
                result[f"{role}:{pin}"] = origin.offset(offset.x, offset.y, offset.z)
    assert len(result) == 9
    return result


def timing_state(placed):
    return {key: (deepcopy(tile.overrides), deepcopy(tile.clock_delays),
                  deepcopy(tile._balanced_positions))
            for key, tile in placed.all_modules().items()}


def test_generated_clock_preserves_its_native_source_all_sinks_and_real_padding(generated_clock):
    case = generated_clock
    graph, plan, placed = case["graph"], case["plan"], case["placed"]
    bit = case["bits"]["divided", 0]
    signal = graph.signals[bit]
    assert signal.source.pin == "Q"
    assert case["entities"][signal.source.entity]["name"] == case["roles"]["divider"]
    assert graph.cell_count == 4
    assert plan["summary"]["hierarchy_depth"] >= 3
    assert plan["summary"]["maximum_domains_at_one_cluster"] > 1
    assert audit_placement(graph, plan, placed)["clock_leaf_uses"] == 3
    pins = native_pins(placed, case["roles"])
    edges, sources = defaultdict(set), []
    for tile in placed.all_modules().values():
        for net in tile.physical.nets:
            if net.bit != bit:
                continue
            edges[net.source.pos].update(sink.pos for sink in net.sinks)
            if net.source.name in tile.physical.cell_types:
                sources.append((net.source.name, net.source.pin, net.source.pos))
    assert sources == [(case["roles"]["divider"], "Q", pins["divider:Q"])]
    reached, pending = set(), [pins["divider:Q"]]
    while pending:
        point = pending.pop()
        if point not in reached:
            reached.add(point)
            pending.extend(edges.get(point, ()))
    expected = []
    for sink in signal.sinks:
        entity = case["entities"][sink.entity]
        if entity["kind"] == "port":
            expected.append(placed.ports[sink.entity])
        else:
            tile = placed.all_modules()[("leaf", plan["entity_leaf"][sink.entity])]
            offset = template_for(entity["cell_type"]).pins[sink.pin]
            origin = tile.physical.cell_origins[entity["name"]]
            expected.append(origin.offset(offset.x, offset.y, offset.z))
    # Includes the NOT feedback input and original divided output port, as
    # well as both downstream clock pins; no source replacement is allowed.
    assert len(expected) == 4 and set(expected) <= reached
    assert all(record["position"] in reached for record in placed.shared_contacts.values()
               if record["bit"] == bit)
    assert pins["a:C"] in reached and pins["b:C"] in reached
    original = timing_state(placed)
    states = _literal_states(placed.iter_placements())
    reports = balance_partition_clocks(plan, placed)
    assert timing_state(placed) == original
    assert reports[bit]["clock_leaves"] == 2
    assert reports[case["bits"]["raw_clk", 0]]["clock_leaves"] == 1
    assert sum(len(tile._balanced_positions.get(bit, ()))
               for tile in placed.all_modules().values()) > 0
    assert _literal_states(placed.iter_placements()) == states
    assert len(states) == placed.unique_blocks


def generated_clock_case(case, half_period, *, remove_matching=False):
    placed = deepcopy(case["placed"]) if remove_matching else case["placed"]
    bit = case["bits"]["divided", 0]
    removed = []
    if remove_matching:
        for key, tile in placed.all_modules().items():
            for point in sorted(tile._balanced_positions.get(bit, ())):
                state = tile.overrides.pop(point)
                removed.append({"module": list(key), "position": vars(point),
                                "state": {"name": state.name, "properties": dict(state.properties)}})
        assert removed, "the negative control must remove an actual emitted delay modification"
    # The single reversible translation fits the native -64..319 height in
    # the dense engine. Every native cell, route and handoff moves together.
    delta = Vec3(16 - placed.bounds.min.x, 4 - placed.bounds.min.y, 16 - placed.bounds.min.z)
    translated = placed.translated(delta)
    inputs, outputs = {}, native_pins(translated, case["roles"])
    for entity_id, point in translated.ports.items():
        entity = case["entities"][entity_id]
        name = entity["port_name"] + (str(entity["port_index"]) if entity["port_name"] == "q" else "")
        outputs[f"port:{name}"] = point
        if entity["direction"] == "input":
            inputs[name] = west_driver(point)
    assert set(inputs) == {"raw_clk", "data"}, "the derived clock must come only from the native divider"
    for link, contact in translated.shared_contacts.items():
        if contact["bit"] == bit:
            outputs[f"handoff:{link}"] = contact["position"]
    steps, raw_expected, derived_expected, checkpoints = [], [], [], []
    state, divided, ticks = [False, False], False, 0
    for data, cycles in ((False, 4), (True, 8), (False, 8), (True, 6), (False, 6)):
        steps.append({"drive": {"raw_clk": False, "data": data}, "ticks": 1024})
        ticks += 1024
        for _ in range(cycles):
            divided = not divided
            raw_expected.append(divided)
            if divided:
                state = [data, state[0]]
                derived_expected.append(state[:])
            steps.extend([{"drive": {"raw_clk": True}, "ticks": half_period},
                          {"drive": {"raw_clk": False}, "ticks": half_period}])
            ticks += 2 * half_period
        steps.append({"ticks": 1024})
        ticks += 1024
        checkpoints.append({"tick": ticks - 1, "q0": state[0], "q1": state[1], "divided": False})
    result = run_case(translated.iter_placements(), inputs, outputs, steps,
                      initial_ticks=1024, capture_trace=True)
    return result, raw_expected, derived_expected, checkpoints, removed, delta


def rising_edges(trace):
    return [index for index, value in enumerate(trace) if value and (not index or not trace[index - 1])]


def falling_edges(trace):
    return [index for index, value in enumerate(trace) if index and not value and trace[index - 1]]


def clock_trace_report(trace):
    rise, fall = rising_edges(trace), falling_edges(trace)
    return {"rising_edges": rise, "falling_edges": fall,
            "complete_pulse_widths": [end - start for start, end in zip(rise, fall)],
            "observed_high_runs": high_runs(trace), "ends_high": bool(trace[-1])}


def analyze_generated_clock(case, result, raw_expected, derived_expected, checkpoints, half_period):
    traces = result["traces"]
    names = {"raw": "divider:C", "generated": "divider:Q", "a": "a:C", "b": "b:C"}
    clocks = {role: clock_trace_report(traces[name]) for role, name in names.items()}
    failures, margins = {}, {}
    for role, expected in (("divider", raw_expected), ("a", [q[0] for q in derived_expected]),
                           ("b", [q[1] for q in derived_expected])):
        d, q = traces[f"{role}:D"], traces[f"{role}:Q"]
        edges = rising_edges(traces[f"{role}:C"])
        failures[role] = [{"edge": edge, "event_index": index, "expected": wanted, "actual": q[edge + 12]}
                          for index, (edge, wanted) in enumerate(zip(edges, expected)) if q[edge + 12] != wanted]
        # Count fully stable observed ticks, not the distance to the previous
        # differing sample; a transition sample cannot be claimed as setup.
        setup = [next((delta - 1 for delta in range(1, edge + 1) if d[edge - delta] != d[edge]), edge)
                 for edge in edges]
        hold = [next((delta - 1 for delta in range(1, len(d) - edge) if d[edge + delta] != d[edge]),
                     len(d) - edge - 1) for edge in edges]
        margins[role] = {"minimum_setup_rt": min(setup, default=None), "minimum_hold_rt": min(hold, default=None)}
    delays = {role: {polarity: sorted(set(end - start for start, end in zip(
        clocks["generated"][polarity], clocks[role][polarity])))
                    for polarity in ("rising_edges", "falling_edges")} for role in ("a", "b")}
    skew = [abs(a - b) for a, b in zip(clocks["a"]["rising_edges"], clocks["b"]["rising_edges"])]
    port_failures = [{"tick": point["tick"], "port": name, "expected": point[name],
                      "actual": traces[f"port:{name}"][point["tick"]]}
                     for point in checkpoints for name in ("q0", "q1", "divided")
                     if traces[f"port:{name}"][point["tick"]] != point[name]]
    errors = []
    expected_counts = {"raw": len(raw_expected), "generated": len(derived_expected),
                       "a": len(derived_expected), "b": len(derived_expected)}
    for role, count in expected_counts.items():
        expected_width = half_period if role == "raw" else 2 * half_period
        if len(clocks[role]["rising_edges"]) != count:
            errors.append(f"{role}:wrong-edge-count")
        if clocks[role]["complete_pulse_widths"] != [expected_width] * count or clocks[role]["ends_high"]:
            errors.append(f"{role}:wrong-pulse-width-or-unfinished-pulse")
    if traces["a:C"] != traces["b:C"]:
        errors.append("generated-clock:leaf-skew")
    planned = balance_partition_clocks(case["plan"], case["placed"])[case["bits"]["divided", 0]]["balanced_repeater_delay"]
    if any(values != [planned] for group in delays.values() for values in group.values()):
        errors.append("generated-clock:source-to-leaf-delay-differs")
    for role in ("divider", "a", "b"):
        if failures[role]:
            errors.append(f"{role}:wrong-register-transfer")
        if (margins[role]["minimum_setup_rt"] is None or margins[role]["minimum_setup_rt"] < template_for("DFF").setup_ticks
                or margins[role]["minimum_hold_rt"] is None or margins[role]["minimum_hold_rt"] < template_for("DFF").hold_ticks):
            errors.append(f"{role}:setup-or-hold-violation")
    if port_failures:
        errors.append("original-output-port:wrong-settled-value")
    return {"engine_revision": result["revision"], "actual_ticks": result["ticks"],
            "raw_half_period_rt": half_period, "expected_raw_edges": len(raw_expected),
            "expected_generated_edges": len(derived_expected), "clock_traces": clocks,
            "source_to_leaf_delay_rt": delays, "planned_source_to_leaf_delay_rt": planned,
            "leaf_traces_equal": traces["a:C"] == traces["b:C"], "maximum_leaf_skew_rt": max(skew, default=None),
            "stable_data_margins": margins, "register_transfer_failures": failures,
            "settled_port_failures": port_failures, "acceptance_errors": errors}


@pytest.mark.parametrize("mode,half_period", [("matched", 512), ("too-fast", 20), ("missing-padding", 512)])
def test_actual_native_generated_clock_across_parents(generated_clock, tmp_path, mode, half_period):
    case = generated_clock
    result, raw, derived, checkpoints, removed, delta = generated_clock_case(
        case, half_period, remove_matching=mode == "missing-padding")
    report = analyze_generated_clock(case, result, raw, derived, checkpoints, half_period)
    report.update(mode=mode, status="passed" if mode == "matched" else "expected timing failure detected",
                  source_sha256=hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
                  mapped_source=case["graph"].source, topology_sha256=case["plan"]["summary"]["topology_sha256"],
                  native_unique_blocks=case["placed"].unique_blocks, removed_matching=removed,
                  native_to_dense_translation=vars(delta),
                  scope="This synthesized divider and pipeline only; complete-machine timing remains unverified")
    if mode == "matched":
        assert not report["acceptance_errors"], report
    elif mode == "too-fast":
        assert "generated:wrong-edge-count" in report["acceptance_errors"], report
        assert report["register_transfer_failures"]["divider"], report
    else:
        assert "generated-clock:leaf-skew" in report["acceptance_errors"], report
        assert "generated-clock:source-to-leaf-delay-differs" in report["acceptance_errors"], report
        assert report["maximum_leaf_skew_rt"] > 0
        # Data may still transfer correctly; that must not hide unequal clock
        # arrival. The original metadata deliberately still claims matching.
        assert not any(report["register_transfer_failures"].values())
    (tmp_path / f"generated-clock-{mode}.json").write_text(json.dumps(report, indent=2) + "\n")
    if directory := os.environ.get("SHAMA_VALIDATION_REPORT_DIR"):
        Path(directory).mkdir(parents=True, exist_ok=True)
        (Path(directory) / f"generated-clock-{mode}.json").write_text(json.dumps(report, indent=2) + "\n")


def test_generated_clock_capacity_failure_rolls_back_all_prior_clock_edits(generated_clock, monkeypatch):
    case = generated_clock
    plan = build_plan(case["graph"], cell_budget=1, boundary_budget=8, domain_budget=5, arity=3)
    placed = place_partition(case["graph"], plan, balance_clocks=False)
    original = timing_state(placed)
    calls = []
    balance = LocalTile.balance_net

    def record_call(tile, bit, tails):
        calls.append(bit)
        return balance(tile, bit, tails)

    monkeypatch.setattr(LocalTile, "balance_net", record_call)
    with pytest.raises(ValueError, match="private branch capacity"):
        balance_partition_clocks(plan, placed)
    assert case["bits"]["raw_clk", 0] in calls and case["bits"]["divided", 0] in calls
    assert timing_state(placed) == original
