"""Real mapped hierarchy boundaries and timing; not full-machine acceptance."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import random

import pytest

from shamaos.hardware.local_router import audit_hierarchy, connected_order, stack_tiles
from shamaos.hardware.logic import template_for
from shamaos.hardware.netlist_cut import mapped_cell_cut
from shamaos.model import Vec3
from shamaos.synthesis import build_physical_netlist
from test_mchprs_runtime import high_runs, run_case, west_driver


FIXTURE = Path(__file__).parent / "fixtures/asm_state_cut.json"


def scalar_cell(kind, **connections):
    return {
        "type": kind,
        "connections": {pin: [bit] for pin, bit in connections.items()},
        "port_directions": {pin: "output" if pin in ("Y", "Q") else "input" for pin in connections},
    }


def test_mapped_cut_retains_all_boundary_consumers_clocks_bus_indices_and_constants():
    module = {
        "ports": {"bus": {"direction": "input", "bits": [2, 3]},
                  "clock": {"direction": "input", "bits": [4]},
                  "result": {"direction": "output", "bits": [7, 6]}},
        "cells": {
            "gate": scalar_cell("$_AND_", A=2, B="1", Y=5),
            "register": scalar_cell("$_DFF_P_", C=4, D=5, Q=6),
            "outside": scalar_cell("$_AND_", A=6, B=5, Y=7),
        },
    }
    before = deepcopy(module)
    cut = mapped_cell_cut(module, ("gate", "register"), name="cut")
    retained = cut["modules"]["cut"]
    assert retained["cells"] == {name: module["cells"][name] for name in ("gate", "register")}
    assert retained["ports"] == {
        "i2": {"direction": "input", "bits": [2]},
        "i4": {"direction": "input", "bits": [4]},
        "o5": {"direction": "output", "bits": [5]},
        "o6": {"direction": "output", "bits": [6]},
    }
    boundary = cut["cut_boundary"]
    assert boundary["incoming"]["4"]["selected_consumers"] == [{"cell": "register", "pin": "C", "index": 0}]
    assert boundary["incoming"]["2"]["parent_ports"] == [{"port": "bus", "index": 0, "direction": "input"}]
    assert boundary["outgoing"]["5"]["outside_consumers"] == [{"cell": "outside", "pin": "B", "index": 0}]
    assert boundary["outgoing"]["6"]["outside_consumers"] == [
        {"cell": "outside", "pin": "A", "index": 0},
        {"port": "result", "index": 1, "direction": "output"},
    ]
    assert module == before
    module["cells"]["gate"]["connections"]["B"] = ["x"]
    with pytest.raises(ValueError, match="unknown constants"):
        mapped_cell_cut(module, ("gate",), name="bad")


@pytest.fixture(scope="module")
def state_cut():
    raw = json.loads(FIXTURE.read_text())
    physical = build_physical_netlist(FIXTURE, top="asm_state_pair", origin=Vec3(128, -60, 1000), minimum_build_y=-60)
    order = connected_order(physical)
    registers = [name for name in order if template_for(physical.cell_types[name]).cell_type == "DFF"]
    assert len(registers) == 2
    # Both original registers would otherwise land in the last affinity tile.
    # Put them on different tiers to exercise the parent clock distribution.
    position = order.index(registers[0])
    order[0], order[position] = order[position], order[0]
    groups = [order[index:index + 16] for index in range(0, len(order), 16)]
    return raw, physical, groups


@pytest.fixture(scope="module")
def state_hierarchy(state_cut):
    _, physical, groups = state_cut
    return stack_tiles(physical, groups, max_cells=16)


def test_real_partition_conserves_all_cells_terminals_and_clock_leaves_above_grass(state_cut, state_hierarchy):
    raw, physical, _ = state_cut
    report = audit_hierarchy(physical, state_hierarchy)
    assert len(raw["modules"]["asm_state_pair"]["cells"]) == 40
    assert report["cells"] == 47
    assert report["original_port_bits"] == 37
    assert report["clock_leaf_uses"] == 2
    assert [len(child.physical.cell_types) for child in state_hierarchy.children] == [16, 16, 15]
    assert sum(len(child.clock_delays) for child in state_hierarchy.children) == 2
    placements = list(state_hierarchy.iter_placements())
    occupied = {}
    for placement in placements:
        assert -60 <= placement.pos.y <= 319
        assert occupied.setdefault(placement.pos, placement.block) == placement.block
    # The old fixed parent strip at Z=1120 exhausted its legal escape lanes.
    # Larger boundaries now get measured, explicitly bounded clearance.
    assert min(pin.z for pins in state_hierarchy.parent.physical.input_ports.values() for pin in pins) > 1120


@pytest.mark.parametrize("fault", ["clock-leaf", "cell", "port-width", "driver"])
def test_hierarchy_audit_rejects_dropped_or_rewired_original_identity(state_cut, state_hierarchy, fault):
    _, physical, _ = state_cut
    damaged = deepcopy(state_hierarchy)
    if fault == "clock-leaf":
        child = next(child for child in damaged.children if child.clock_delays)
        child.physical.nets = tuple(replace(net, sinks=tuple(sink for sink in net.sinks if sink.pin != "C"))
                                    for net in child.physical.nets)
    elif fault == "cell":
        child = damaged.children[0]
        del child.physical.cell_types[next(iter(child.physical.cell_types))]
    elif fault == "port-width":
        del damaged.parent.physical.input_ports[next(iter(damaged.parent.physical.input_ports))]
    else:
        parent = damaged.parent.physical
        external = next(net for net in parent.nets if net.source.name.startswith("port:"))
        parent.nets = tuple(replace(net, source=replace(net.source, pin="wrong-input")) if net == external else net
                            for net in parent.nets)
    with pytest.raises(ValueError, match="hierarchy"):
        audit_hierarchy(physical, damaged)


def test_parent_hierarchy_budget_rejects_unbounded_interfaces(state_cut):
    _, physical, groups = state_cut
    with pytest.raises(ValueError, match="boundary terminal budget"):
        stack_tiles(physical, groups, max_cells=16, max_boundary_terminals=8)


def test_crossing_locally_generated_clock_requires_a_common_clock_tree(tmp_path):
    module = {
        "ports": {"clk": {"direction": "input", "bits": [2]},
                  "data": {"direction": "input", "bits": [3, 4]},
                  "q": {"direction": "output", "bits": [13, 14]}},
        "cells": {
            "divider": scalar_cell("$_DFF_P_", C=2, D=3, Q=11),
            "near": scalar_cell("$_DFF_P_", C=11, D=3, Q=13),
            "far": scalar_cell("$_DFF_P_", C=11, D=4, Q=14),
        },
    }
    path = tmp_path / "generated-clock.json"
    path.write_text(json.dumps({"modules": {"clocked": module}}))
    physical = build_physical_netlist(path, top="clocked")
    with pytest.raises(ValueError, match="global clock closure"):
        stack_tiles(physical, (("divider", "near"), ("far",)))


def state_cut_trace(raw, hierarchy, half_period):
    module = raw["modules"]["asm_state_pair"]
    cells = module["cells"]
    by_bit = {bit: cell for cell in cells.values() for pin, bits in cell["connections"].items()
              if cell["port_directions"][pin] == "output" for bit in bits}
    registers = {name: cell for name, cell in cells.items() if "DFF" in cell["type"]}
    assert {cell["type"] for cell in registers.values()} == {"$_SDFF_PN0_"}
    state = {cell["connections"]["Q"][0]: False for cell in registers.values()}
    root = hierarchy.parent.physical
    inputs = {name: west_driver(pins[0]) for name, pins in root.input_ports.items()}
    outputs = {name: pins[0] for name, pins in root.output_ports.items()}
    physical_registers = {}
    for child in hierarchy.children:
        for name, origin in child.physical.cell_origins.items():
            template = template_for(child.physical.cell_types[name])
            if template.cell_type == "DFF":
                original_name = name.removesuffix("$dff")
                assert original_name in registers
                physical_registers[name] = registers[original_name]
                for pin, offset in template.pins.items():
                    outputs[f"{name}:{pin}"] = origin.offset(offset.x, offset.y, offset.z)
    assert len(physical_registers) == len(registers)
    input_bits = [port["bits"][0] for port in module["ports"].values()
                  if port["direction"] == "input" and port["bits"][0] != 2]
    rng = random.Random(0x5348414D)
    vectors = [dict.fromkeys(input_bits, value) for value in (False, True)]
    vectors.extend({bit: bool(rng.getrandbits(1)) for bit in input_bits} for _ in range(30))
    steps, expected, boundary_checks = [], [], []
    elapsed = 0
    for vector in vectors:
        steps.append({"drive": {"i2": False, **{f"i{bit}": value for bit, value in vector.items()}},
                      "ticks": half_period})

        def evaluate(bit, memo):
            if bit in ("0", "1"):
                return bit == "1"
            if bit in vector:
                return vector[bit]
            if bit in state:
                return state[bit]
            if bit not in memo:
                cell = by_bit[bit]
                a = evaluate(cell["connections"]["A"][0], memo)
                if cell["type"] == "$_NOT_":
                    memo[bit] = not a
                else:
                    assert cell["type"] == "$_AND_"
                    memo[bit] = a and evaluate(cell["connections"]["B"][0], memo)
            return memo[bit]

        for _ in range(3):
            # Independent oracle uses the original mapped SDFF semantics,
            # not the physical generator's lowered reset-mux implementation.
            state = {cell["connections"]["Q"][0]:
                     evaluate(cell["connections"]["D"][0], {}) if evaluate(cell["connections"]["R"][0], {}) else False
                     for cell in registers.values()}
            expected.append(dict(state))
            steps.extend([{"drive": {"i2": True}, "ticks": half_period},
                          {"drive": {"i2": False}, "ticks": half_period}])
        steps.append({"ticks": 1200})
        elapsed += 7 * half_period + 1200
        boundary_checks.append((elapsed - 1, {
            name: evaluate(port["bits"][0], {}) for name, port in module["ports"].items()
            if port["direction"] == "output"
        }))
    # The harness's finite plot uses nonnegative coordinates. Translate every
    # physical block and terminal equally; the original above-grass bounds are
    # independently checked above and no circuit geometry is substituted.
    blocks = [replace(placement, pos=placement.pos.offset(dy=64)) for placement in hierarchy.iter_placements()]
    result = run_case(blocks, {name: point.offset(dy=64) for name, point in inputs.items()},
                      {name: point.offset(dy=64) for name, point in outputs.items()},
                      steps, initial_ticks=256, capture_trace=True)
    clocks = {name: result["traces"][f"{name}:C"] for name in physical_registers}
    edges = {name: [index for index, value in enumerate(trace) if value and (not index or not trace[index - 1])]
             for name, trace in clocks.items()}
    failures, setups, holds = [], [], []
    for name, cell in physical_registers.items():
        assert len(edges[name]) == len(expected)
        assert set(high_runs(clocks[name])) == {half_period}
        q, data = result["traces"][f"{name}:Q"], result["traces"][f"{name}:D"]
        for cycle, (edge, wanted) in enumerate(zip(edges[name], expected)):
            if q[edge + 12] != wanted[cell["connections"]["Q"][0]]:
                failures.append({"cycle": cycle, "register": name, "clock_edge": edge})
            setups.append(next((delta for delta in range(1, edge + 1) if data[edge - delta] != data[edge]), edge))
            holds.append(next((delta for delta in range(1, len(data) - edge) if data[edge + delta] != data[edge]), len(data) - edge))
    skews = [max(ticks) - min(ticks) for ticks in zip(*edges.values())]
    boundary_failures = [{"tick": tick, "output": name} for tick, values in boundary_checks
                         for name, wanted in values.items() if result["traces"][name][tick] != wanted]
    return hierarchy.report() | {
        "provenance": raw["proof_source"], "half_period_redstone_ticks": half_period,
        "maximum_observed_clock_skew_ticks": max(skews), "minimum_observed_setup_ticks": min(setups),
        "minimum_observed_hold_ticks": min(holds), "clock_edges_per_register": len(expected),
        "actual_ticks": result["ticks"], "failed_register_transfers": failures,
        "boundary_output_samples": sum(len(values) for _, values in boundary_checks),
        "failed_boundary_outputs": boundary_failures,
        "runtime_coordinate_translation_y": 64,
    }


@pytest.mark.parametrize("half_period,should_pass", [(400, True), (100, False)])
def test_actual_mapped_three_tier_register_transfers_and_timing_negative_control(
    state_cut, state_hierarchy, tmp_path, half_period, should_pass,
):
    raw, _, _ = state_cut
    report = state_cut_trace(raw, state_hierarchy, half_period)
    (tmp_path / f"mapped-state-cut-{half_period}.json").write_text(json.dumps(report, indent=2) + "\n")
    assert report["maximum_observed_clock_skew_ticks"] == 0
    if should_pass:
        assert not report["failed_register_transfers"], report
        assert not report["failed_boundary_outputs"], report
        assert report["minimum_observed_setup_ticks"] >= template_for("DFF").setup_ticks
        assert report["minimum_observed_hold_ticks"] >= template_for("DFF").hold_ticks
    else:
        # Even stable local D and matching clocks cannot prove that this
        # cycle's intended input data has completed the longer boundary route.
        assert report["failed_register_transfers"], report
