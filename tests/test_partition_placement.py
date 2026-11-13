"""Connected placement proofs; the complete production system is a later gate."""
from dataclasses import replace
import json
from pathlib import Path

import pytest

from shamaos.hardware.logic import template_for
from shamaos.hardware.external_router import _plan_escapes, _wire_z
from shamaos.hardware.memory import SUPPORT
from shamaos.hardware.routing import iter_stair
from shamaos.hardware.partition_planner import Graph, build_plan
from shamaos.hardware.partition_placement import (
    PlacementError, _check_domain_isolation, _literal_states, audit_placement, balance_partition_clocks,
    place_partition,
)
from shamaos.model import Placement, Vec3
from test_mchprs_runtime import high_runs, run_case, west_driver


@pytest.fixture(scope="module", params=[16, 5], ids=["single-domain", "multiple-domains"])
def connected_pipeline(tmp_path_factory, request):
    directory = tmp_path_factory.mktemp("complete-partition-pipeline")
    module = {"ports": {
        "clk": {"direction": "input", "bits": [2]},
        "d": {"direction": "input", "bits": [3]},
        "q": {"direction": "output", "bits": [5, 6]},
        "one": {"direction": "output", "bits": ["1"]},
        "zero": {"direction": "output", "bits": ["0"]},
    }, "cells": {
        "enabled": {"type": "$_AND_", "connections": {"A": [3], "B": ["1"], "Y": [4]}},
        "a": {"type": "$_DFF_P_", "connections": {"C": [2], "D": [4], "Q": [5]}},
        "b": {"type": "$_DFF_P_", "connections": {"C": [2], "D": [5], "Q": [6]}},
    }}
    path = directory / "mapped.json"
    path.write_text(json.dumps({"modules": {"pipeline": module}}))
    graph = Graph.read(path, "pipeline")
    plan = build_plan(graph, cell_budget=1, boundary_budget=8, domain_budget=request.param, arity=4)
    placed = place_partition(graph, plan)
    return graph, plan, placed


def test_two_level_partition_preserves_all_native_pins_contacts_constants_and_ports(connected_pipeline):
    graph, plan, placed = connected_pipeline
    assert plan["summary"]["hierarchy_depth"] == 2
    assert audit_placement(graph, plan, placed)["shared_contacts"] == 18
    assert placed.bounds.min.y == -60
    assert placed.bounds.max.y <= 319
    states = _literal_states(placed.iter_placements())
    assert len(states) == placed.unique_blocks
    assert len(placed.all_modules()) == plan["summary"]["leaf_clusters"] + plan["summary"]["bounded_router_domains"]
    assert balance_partition_clocks(plan, placed)[2]["clock_leaves"] == 2
    # All original port bits remain physical, including unconnected constants.
    assert len(placed.ports) == 6


def test_sibling_domains_keep_bounded_inventory_with_separate_columns_and_track_bands(connected_pipeline):
    _, plan, placed = connected_pipeline
    pending = [placed]
    while pending:
        cluster = pending.pop()
        tiles = [tile for key, tile in cluster.modules.items() if key[0] == "domain"]
        assert len({(tile.branch_y, tile.trunk_y) for tile in tiles}) <= 1
        used = set()
        last_band = None
        for tile in tiles:
            assert sum(1 + len(net.sinks) for net in tile.physical.nets) <= plan["budgets"]["routing_terminals_per_domain"]
            columns = {escape.branch_x for escape in tile.escapes.values()}
            assert not any(abs(column - old) < 3 for column in columns for old in used)
            used.update(columns)
            maximum_track = tile.track_base_z + 4 * max(tile.tracks.values(), default=0)
            if last_band is not None:
                assert maximum_track <= last_band - 8
            last_band = tile.track_base_z
        pending.extend(cluster.children)


def test_insufficient_vertical_room_moves_complete_child_groups_horizontally(connected_pipeline):
    graph, plan, _ = connected_pipeline
    placed = place_partition(graph, plan, maximum_y=63)
    assert placed.bounds.min.y == -60
    assert placed.bounds.max.y <= 63
    assert placed.packing["horizontal_stacks"] > 1
    assert audit_placement(graph, plan, placed)["native_cells"] == graph.cell_count
    assert len(_literal_states(placed.iter_placements())) == placed.unique_blocks


def test_existing_high_grass_is_preserved_or_rejected_without_clipping(connected_pipeline):
    graph, plan, _ = connected_pipeline
    placed = place_partition(graph, plan, surface_y=63)
    assert placed.bounds.min.y == 64
    assert placed.bounds.max.y <= 319
    assert all(64 <= point.y <= 319 for point in _literal_states(placed.iter_placements()))
    with pytest.raises(PlacementError, match="build height"):
        place_partition(graph, plan, surface_y=300)


@pytest.mark.parametrize("fault", ["pin", "constant", "contact", "face", "zero-leaf", "duplicate-net", "extra-terminal", "duplicate-module", "contact-parent", "actual-face"])
def test_physical_conservation_audit_rejects_changed_wiring(connected_pipeline, fault):
    from copy import deepcopy
    graph, plan, original = connected_pipeline
    placed = deepcopy(original)
    if fault == "pin":
        tile = next(tile for tile in placed.all_modules().values() if "a" in tile.physical.cell_types)
        net = next(net for net in tile.physical.nets if any(sink.name == "a" and sink.pin == "D" for sink in net.sinks))
        broken = replace(net, sinks=tuple(replace(sink, pos=sink.pos.offset(dx=1)) if sink.name == "a" and sink.pin == "D" else sink for sink in net.sinks))
        tile.physical = replace(tile.physical, nets=tuple(broken if entry is net else entry for entry in tile.physical.nets))
    elif fault == "constant":
        tile = next(tile for tile in placed.all_modules().values() if tile.physical.constant_high)
        tile.physical = replace(tile.physical, constant_high=())
    elif fault == "contact":
        link = next(iter(placed.shared_contacts.values()))
        placed.terminals[link["sink_terminal"]] = link["position"].offset(dx=1)
    elif fault == "face":
        next(iter(placed.shared_contacts.values()))["parent_access"] = "north"
    elif fault in ("zero-leaf", "duplicate-module"):
        pending = [placed]
        while pending:
            cluster = pending.pop()
            if ("leaf", 8) in cluster.modules:
                tile = cluster.modules[("leaf", 8)]
                if fault == "zero-leaf":
                    del cluster.modules[("leaf", 8)]
                else:
                    placed.modules[("leaf", 8)] = tile
                break
            pending.extend(cluster.children)
    elif fault == "duplicate-net":
        tile = next(tile for tile in placed.all_modules().values() if tile.physical.nets)
        tile.physical = replace(tile.physical, nets=(*tile.physical.nets, tile.physical.nets[0]))
    elif fault == "extra-terminal":
        placed.terminals["invented"] = Vec3(10000, 0, 10000)
    elif fault == "contact-parent":
        next(iter(placed.shared_contacts.values()))["parent_cluster"] = -100
    else:
        link = next(iter(placed.shared_contacts.values()))
        child = link["child_cluster"]
        tile = placed.all_modules()[("leaf", child)]
        point = link["position"]
        tile.escapes[point] = replace(tile.escapes[point], escape_z=point.z + 6)
    with pytest.raises(PlacementError):
        audit_placement(graph, plan, placed)


def partition_pipeline_case(graph, plan, placed, half_period, *, verify_outputs=True):
    # Dense TestWorld requires nonnegative coordinates; this exact translation
    # is reversible for saved/reopened native-world replay.
    delta = Vec3(32 - placed.bounds.min.x, 32 - placed.bounds.min.y, 32 - placed.bounds.min.z)
    translated = placed.translated(delta)
    entities = {entity["id"]: entity for entity in graph.entities}
    inputs, outputs = {}, {}
    for entity_id, point in translated.ports.items():
        entity = entities[entity_id]
        name = entity["port_name"]
        if name == "q":
            name += str(entity["port_index"])
        (inputs if entity["direction"] == "input" else outputs)[name] = west_driver(point) if entity["direction"] == "input" else point
    for tile in translated.all_modules().values():
        for name, base in tile.physical.cell_origins.items():
            if template_for(tile.physical.cell_types[name]).cell_type == "DFF":
                for pin, offset in template_for("DFF").pins.items():
                    outputs[f"{name}:{pin}"] = base.offset(offset.x, offset.y, offset.z)
    for link, contact in translated.shared_contacts.items():
        outputs[f"handoff:{link}"] = contact["position"]
    expected, steps, state = [], [], [False, False]
    for data, cycles in ((False, 2), (True, 4), (False, 4), (True, 3), (False, 3)):
        steps.append({"drive": {"clk": False, "d": data}, "ticks": 1024,
                      "expect": {"one": True, "zero": False}, "continuous": True})
        for _ in range(cycles):
            state = [data, state[0]]
            expected.append(tuple(state))
            steps.extend([{"drive": {"clk": True}, "ticks": half_period,
                           "expect": {"one": True, "zero": False}, "continuous": True},
                          {"drive": {"clk": False}, "ticks": half_period,
                           "expect": {"one": True, "zero": False}, "continuous": True}])
        final = {"one": True, "zero": False}
        if verify_outputs:
            final.update(q0=state[0], q1=state[1])
        steps.append({"ticks": 1024, "expect": final})
    return translated, inputs, outputs, steps, expected, delta


def analyze_pipeline_trace(result, expected, half_period):
    clocks = [result["traces"][f"{name}:C"] for name in ("a", "b")]
    assert clocks[0] == clocks[1], "actual register clock pins have scheduled-engine skew"
    edges = [index for index, value in enumerate(clocks[0]) if value and (not index or not clocks[0][index - 1])]
    assert len(edges) == len(expected), "a parent-child handoff lost or invented a clock edge"
    assert set(high_runs(clocks[0])) == {half_period}
    failures, setup, hold = [], [], []
    for edge, wanted in zip(edges, expected):
        for index, name in enumerate(("a", "b")):
            q = result["traces"][f"{name}:Q"]
            d = result["traces"][f"{name}:D"]
            if q[edge + 12] != wanted[index]:
                failures.append({"edge": edge, "register": name, "expected": wanted[index], "actual": q[edge + 12]})
            setup.append(next((delta for delta in range(1, edge + 1) if d[edge - delta] != d[edge]), edge))
            hold.append(next((delta for delta in range(1, len(d) - edge) if d[edge + delta] != d[edge]), len(d) - edge))
    return {"clock_skew_ticks": 0, "clock_edges": len(edges), "half_period_redstone_ticks": half_period,
            "minimum_setup_ticks": min(setup), "minimum_hold_ticks": min(hold),
            "pipeline_failures": failures, "actual_ticks": result["ticks"]}


@pytest.mark.parametrize("half_period, should_pass", [(512, True), (20, False)])
def test_actual_connected_two_level_pipeline_transfers_and_too_fast_negative(connected_pipeline, half_period, should_pass, tmp_path):
    graph, plan, placed = connected_pipeline
    translated, inputs, outputs, steps, expected, delta = partition_pipeline_case(
        graph, plan, placed, half_period, verify_outputs=should_pass)
    result = run_case(translated.iter_placements(), inputs, outputs, steps,
                      initial_ticks=1024, capture_trace=True)
    report = analyze_pipeline_trace(result, expected, half_period)
    report.update(native_to_dense_translation=vars(delta), topology_sha256=plan["summary"]["topology_sha256"])
    (tmp_path / "connected-partition-runtime.json").write_text(json.dumps(report, indent=2))
    if should_pass:
        assert not report["pipeline_failures"], report
        assert report["minimum_setup_ticks"] >= template_for("DFF").setup_ticks
        assert report["minimum_hold_ticks"] >= template_for("DFF").hold_ticks
    else:
        assert report["pipeline_failures"], "too-fast negative failed to expose a lost transfer"


@pytest.mark.parametrize("pitch", [3, 2])
def test_actual_crossing_wire_headroom_preserves_independence_or_exposes_a_short(pitch):
    start = Vec3(32, 32, 32)
    finish = start.offset(4, 4, 0)
    lower = list(iter_stair(start, finish, axis="x", signal_forward=True, component="lower"))
    source, sink = start.offset(0, pitch, -8), start.offset(0, pitch, 8)
    upper = list(_wire_z(source, sink, signal_forward=True, component="upper"))
    if pitch == 3:
        # Support two above the lower rising wire leaves the needed air gap.
        _check_domain_isolation(_literal_states(lower), _literal_states(upper))
    else:
        with pytest.raises(PlacementError, match="adjacent active wires|climbing headroom"):
            _check_domain_isolation(_literal_states(lower), _literal_states(upper))
    steps = []
    for a, b in ((False, False), (True, False), (False, True), (True, True), (False, False)):
        steps.append({"drive": {"lower": a, "upper": b}, "ticks": 32,
                      "expect": {"lower": a if pitch == 3 else b, "upper": b}})
    # At the deliberately invalid pitch, the upper circuit controls the lower
    # output. The negative proves a real short, not merely a conservative gate.
    run_case([*lower, *upper], {"lower": west_driver(start), "upper": west_driver(source)},
             {"lower": finish, "upper": sink}, steps)


def test_actual_solid_block_directly_above_rising_wire_blocks_the_signal():
    start = Vec3(32, 32, 32)
    finish = start.offset(4, 4, 0)
    lower = list(iter_stair(start, finish, axis="x", signal_forward=True, component="lower"))
    blocker = Placement(start.offset(dy=1), SUPPORT, "blocked-headroom")
    with pytest.raises(PlacementError, match="climbing headroom"):
        _check_domain_isolation(_literal_states(lower), _literal_states([blocker]))
    run_case([*lower, blocker], {"lower": west_driver(start)}, {"lower": finish},
             [{"drive": {"lower": value}, "ticks": 32, "expect": {"lower": False}}
              for value in (False, True, False, True)])


def test_explicit_north_budget_crosses_a_measured_wall_without_changing_the_default():
    pin = Vec3(0, 16, 0)
    # All first 128 North lanes collide with this solid wall during the climb.
    # The 129th lane is physically clear; a larger budget must keep the same
    # collision/headroom rules and the unchanged default must still reject it.
    wall = [Placement(Vec3(5, 21, z), SUPPORT, "wall") for z in range(-768, -5)]
    with pytest.raises(ValueError, match="cannot isolate"):
        _plan_escapes([pin], 30, wall, access={pin: ("north",)}, minimum_y=0)
    escapes = _plan_escapes([pin], 30, wall, access={pin: ("north",)}, minimum_y=0,
                            north_lane_limit=129)
    assert escapes[pin].escape_z == -774
    # Extending North search must not silently extend other access modes.
    south_wall = [Placement(Vec3(5, 21, z), SUPPORT, "south-wall") for z in range(6, 769)]
    with pytest.raises(ValueError, match="cannot isolate"):
        _plan_escapes([pin], 30, south_wall, access={pin: ("south",)}, minimum_y=0,
                      north_lane_limit=512)


@pytest.mark.parametrize("budget", [0, -1, True, 1.5])
def test_north_budget_rejects_invalid_values_before_allocating_geometry(budget):
    with pytest.raises(ValueError, match="positive integer"):
        _plan_escapes([Vec3(0, 16, 0)], 30, north_lane_limit=budget)
