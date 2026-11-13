"""Actual MCHPRS simulation of the generated cells (required release evidence).

Set SHAMA_MCHPRS_RUNNER to the binary built from tests/mchprs_runtime/Cargo.toml.
Normal Python-only development can skip this external runtime; release CI must
build the pinned runner and execute these tests without skips.
"""
import json
import os
from itertools import chain, product
from pathlib import Path
import subprocess

import pytest

from shamaos.hardware.logic import TEMPLATES
from shamaos.hardware.clock import CLOCK_HALF_PERIOD, clock_ports, iter_clock
from shamaos.hardware.controls import (
    BUTTON_FLOOR, CONTROLLER_NAMES, control_actuators, control_bounds,
    control_ports, control_route_access, control_switches, controller_buttons,
    iter_controls, keyboard_buttons,
)
from shamaos.hardware.display import LampPanelSpec, iter_lamp_panel, panel_ports
from shamaos.hardware.debug import debug_ports, iter_debug_panel
from shamaos.hardware.external_router import ExternalNet, _plan_escapes, iter_external_router
from shamaos.hardware.memory import DUST, SUPPORT, MemoryBankSpec, bank_ports, iter_memory_bank, repeater
from shamaos.hardware.memory_backbone import (
    StackedMemoryTileSpec, iter_stacked_memory_tile, iter_stacked_tile_terminal_keepouts,
    stacked_tile_bounds, stacked_tile_ports,
)
from shamaos.hardware.pulse import iter_pulse_stretcher, pulse_ports, rotate_point
from shamaos.model import Placement, Vec3
from shamaos.synthesis import MacroInstance, build_physical_netlist


ORIGIN = Vec3(32, 32, 32)


def coordinates(p):
    return [p.x, p.y, p.z]


def run_case(placements, inputs, outputs, steps, *, initial_ticks=32, capture_trace=False, timeout=60):
    runner = os.environ.get("SHAMA_MCHPRS_RUNNER")
    if not runner or not Path(runner).is_file():
        pytest.skip("actual MCHPRS runner not available; do not claim runtime validation")
    placements=list(placements)
    occupied={}
    for placement in placements:
        previous=occupied.setdefault(placement.pos,placement.block)
        assert previous == placement.block, f"conflicting physical blocks at {placement.pos}: {previous} vs {placement.block}"
    case = {
        "blocks": [{"pos": coordinates(p.pos), "name": p.block.name, "properties": dict(p.block.properties)} for p in placements],
        "inputs": {name: coordinates(p) for name, p in inputs.items()},
        "outputs": {name: coordinates(p) for name, p in outputs.items()},
        "steps": steps, "initial_ticks": initial_ticks, "capture_trace": capture_trace,
    }
    result = subprocess.run([runner], input=json.dumps(case), capture_output=True, text=True, timeout=timeout)
    assert result.returncode == 0, result.stdout + result.stderr
    data = json.loads(result.stdout.strip())
    assert data["status"] == "passed"
    return data


@pytest.mark.parametrize("name", ["BUF", "NOT", "AND", "NAND", "NOR"])
def test_actual_runtime_combinational_truth_tables(name):
    template = TEMPLATES[name]
    pins = {name: ORIGIN.offset(p.x, p.y, p.z) for name, p in template.pins.items()}
    input_names = [pin for pin in pins if pin != "Y"]
    states = list(product((0, 1), repeat=len(input_names)))
    steps = []
    for values in states + list(reversed(states)):
        a, b = values[0], values[-1]
        expected = {"BUF": a, "NOT": 1-a, "AND": a & b, "NAND": 1-(a & b), "NOR": 1-(a | b)}[name]
        steps.append({"drive": dict(zip(input_names, map(bool, values))), "ticks": 24, "expect": {"Y": bool(expected)}})
    run_case(template.emitter(ORIGIN, name), {p: pins[p] for p in input_names}, {"Y": pins["Y"]}, steps)


def test_actual_runtime_dff_edges_holds_and_falling_edge_races():
    template = TEMPLATES["DFF"]
    pins = {name: ORIGIN.offset(p.x, p.y, p.z) for name, p in template.pins.items()}
    steps = [{"drive": {"C": False, "D": False}, "ticks": 24, "expect": {"Q": False}}]
    last = False
    for value in (True, False, True, True, False):
        steps += [
            {"drive": {"D": value}, "ticks": 12, "expect": {"Q": last}, "continuous": True},
            {"drive": {"C": True}, "ticks": 12, "expect": {"Q": value}},
            {"drive": {"D": not value}, "ticks": 12, "expect": {"Q": value}, "continuous": True},
            {"drive": {"C": False, "D": value}, "ticks": 12, "expect": {"Q": value}, "continuous": True},
        ]
        last = value
    run_case(template.emitter(ORIGIN, "DFF"), {p: pins[p] for p in ("C", "D")}, {"Q": pins["Q"]}, steps)


def test_actual_dff_obeys_the_declared_setup_and_hold_contract():
    template=TEMPLATES["DFF"]
    pins={name:ORIGIN.offset(p.x,p.y,p.z) for name,p in template.pins.items()}
    steps=[{"drive":{"C":False,"D":False},"ticks":24,"expect":{"Q":False}}]
    previous=False
    for value in (True,False,True,False):
        steps += [
            {"drive":{"D":value},"ticks":template.setup_ticks,"expect":{"Q":previous}},
            {"drive":{"C":True},"ticks":template.hold_ticks},
            {"drive":{"D":not value},"ticks":template.minimum_clock_half_period-template.hold_ticks,"expect":{"Q":value}},
            {"drive":{"C":False,"D":value},"ticks":template.minimum_clock_half_period,"expect":{"Q":value},"continuous":True},
        ]
        previous=value
    run_case(template.emitter(ORIGIN,"DFF"),{name:pins[name] for name in ("C","D")},{"Q":pins["Q"]},steps)


def high_runs(trace):
    runs, length = [], 0
    for value in [*trace, False]:
        if value:
            length += 1
        elif length:
            runs.append(length)
            length = 0
    return runs


@pytest.mark.parametrize("rotation", range(4))
def test_actual_stone_button_pulse_stretching_in_every_orientation(rotation):
    origin = Vec3(96,32,96)
    button_offset = rotate_point(Vec3(0,1,1),rotation)
    button = origin.offset(button_offset.x,button_offset.y,button_offset.z)
    blocks = list(iter_pulse_stretcher(origin,quarter_turns=rotation))
    blocks += [Placement(button.offset(dy=-1),SUPPORT,"button"),Placement(button,BUTTON_FLOOR,"button")]
    _,output = pulse_ports(origin,quarter_turns=rotation)
    result = run_case(blocks,{}, {"pulse":output},[
        {"use_blocks":[coordinates(button)],"ticks":150,"expect":{"pulse":False}},
    ],capture_trace=True)
    # The engine's real stone button releases at 10 RT. Delayed overlapping
    # copies produce one continuous 94-RT event, with no gaps or self-latching.
    assert high_runs(result["traces"]["pulse"]) == [94]
    assert 94 > 2*CLOCK_HALF_PERIOD + TEMPLATES["DFF"].setup_ticks


def test_actual_clock_has_complete_pulses_at_every_start_and_stop_phase():
    origin = Vec3(64,32,96)
    ports = clock_ports(origin)
    blocks = list(iter_clock(origin))
    for phase in range(2*CLOCK_HALF_PERIOD):
        result = run_case(blocks,{"enable":ports.enable,"hold":ports.hold},{"clock":ports.clock},[
            {"ticks":phase,"expect":{"clock":False},"continuous":True},
            {"drive":{"enable":True},"ticks":350},
            {"drive":{"enable":False},"ticks":200,"expect":{"clock":False}},
        ],capture_trace=True)
        trace = result["traces"]["clock"]
        pulses = high_runs(trace)
        assert len(pulses) >= 3, (phase,pulses)
        assert set(pulses) == {CLOCK_HALF_PERIOD}, (phase,pulses)
        edges = [i for i in range(1,len(trace)) if trace[i] != trace[i-1]]
        assert set(b-a for a,b in zip(edges,edges[1:])) == {CLOCK_HALF_PERIOD}


def test_actual_clock_hold_keeps_cleanup_running_after_power_request_is_removed():
    origin = Vec3(64,32,96)
    ports = clock_ports(origin)
    result = run_case(iter_clock(origin),{"enable":ports.enable,"hold":ports.hold},{"clock":ports.clock},[
        {"drive":{"enable":True},"ticks":350},
        {"drive":{"hold":True},"ticks":140},
        {"drive":{"enable":False},"ticks":350},
        {"drive":{"hold":False},"ticks":200,"expect":{"clock":False}},
    ],capture_trace=True)
    trace = result["traces"]["clock"]
    assert set(high_runs(trace)) == {CLOCK_HALF_PERIOD}
    assert sum(trace[490:840]) == 5*CLOCK_HALF_PERIOD


def test_actual_stretched_physical_button_is_captured_at_every_clock_phase():
    origin = Vec3(64,32,96)
    blocks = list(iter_pulse_stretcher(origin))
    button = origin.offset(0,1,1)
    blocks += [Placement(button.offset(dy=-1),SUPPORT,"button"),Placement(button,BUTTON_FLOOR,"button")]
    dff_origin = origin.offset(32,0,0)
    blocks += list(TEMPLATES["DFF"].emitter(dff_origin,"capture"))
    # A real 29-block route, with two repeaters, joins the pulse output to D.
    for x in range(4,33):
        blocks += [Placement(origin.offset(x,0,2),SUPPORT,"route"),
                   Placement(origin.offset(x,1,2),repeater("east") if x in (12,24) else DUST,"route")]
    blocks += [Placement(origin.offset(32,0,1),SUPPORT,"route"),Placement(origin.offset(32,1,1),DUST,"route")]
    for phase in range(2*CLOCK_HALF_PERIOD):
        pressed_at = 2*CLOCK_HALF_PERIOD + phase
        steps = []
        for tick in range(320):
            step = {"drive":{"clock":bool((tick//CLOCK_HALF_PERIOD)%2)},"ticks":1}
            if tick == pressed_at:
                step["use_blocks"] = [coordinates(button)]
            steps.append(step)
        result = run_case(blocks,{"clock":dff_origin.offset(6,1,0)},
                          {"Q":dff_origin.offset(16,1,0)},steps,capture_trace=True)
        trace = result["traces"]["Q"]
        assert any(trace[pressed_at:pressed_at+120]), phase
        assert not any(trace[:pressed_at]), phase
        assert not trace[-1], phase


def test_actual_controller_buttons_and_maintained_switches_are_independent():
    origin = Vec3(32,32,32)
    pins = control_ports(origin)
    outputs = dict(zip(CONTROLLER_NAMES,pins.controller)) | {
        "RESET":pins.reset,"STEP":pins.step,"POWER":pins.power,"PAUSE":pins.pause,
    }
    steps = []
    for name,button in controller_buttons(origin).items():
        steps += [
            {"use_blocks":[coordinates(button)],"ticks":24,"expect":{out:out == name for out in outputs}},
            {"ticks":128,"expect":dict.fromkeys(outputs,False)},
        ]
    for name,position in control_switches(origin).items():
        switch = coordinates(position)
        steps += [
            {"use_blocks":[switch],"ticks":24,"expect":{out:out == name for out in outputs}},
            {"ticks":128,"expect":{out:out == name for out in outputs},"continuous":True},
            {"use_blocks":[switch],"ticks":24,"expect":dict.fromkeys(outputs,False)},
        ]
    run_case(iter_controls(origin),{},outputs,steps)


def test_actual_keyboard_every_key_decodes_only_its_row_and_column():
    origin = Vec3(32,32,32)
    pins = control_ports(origin)
    outputs = {f"R{i}":p for i,p in enumerate(pins.keyboard_rows)} | {f"C{i}":p for i,p in enumerate(pins.keyboard_cols)}
    buttons = keyboard_buttons(origin)
    # The relocated column circuits settle at 26..40 RT. Sample within one
    # actual input-clock period, then verify the complete pulse and every clock
    # phase below; the old arbitrary 32-RT sample preceded columns four to seven.
    sample_ticks = 2 * CLOCK_HALF_PERIOD
    release_ticks = 128
    steps = [{"ticks":32,"expect":dict.fromkeys(outputs,False)}]
    for row in range(8):
        for col in range(8):
            selected = {f"R{row}",f"C{col}"}
            steps += [
                {"use_blocks":[coordinates(buttons[row][col])],"ticks":sample_ticks,"expect":{out:out in selected for out in outputs}},
                {"ticks":release_ticks,"expect":dict.fromkeys(outputs,False)},
            ]
    result = run_case(iter_controls(origin),{},outputs,steps,capture_trace=True)
    stride = sample_ticks + release_ticks
    setup, hold = TEMPLATES["DFF"].setup_ticks, TEMPLATES["DFF"].hold_ticks
    for row in range(8):
        for col in range(8):
            start = 32 + (row * 8 + col) * stride
            selected = {f"R{row}", f"C{col}"}
            traces = {name: trace[start:start+stride] for name, trace in result["traces"].items()}
            assert all(high_runs(trace) == ([119] if name in selected else [])
                       for name, trace in traces.items()), (row, col)
            both = [a and b for a,b in zip(traces[f"R{row}"], traces[f"C{col}"])]
            for phase in range(sample_ticks):
                assert any(all(both[tick-setup:tick+hold+1])
                           for tick in range(phase, stride, sample_ticks)
                           if tick >= setup and tick + hold < stride), (row, col, phase)


def compact_console_case(origin):
    """Shared literal/saved-world fixture, including every physical actuator."""
    pins=control_ports(origin)
    outputs=dict(zip(CONTROLLER_NAMES,pins.controller)) | {
        "RESET":pins.reset,"STEP":pins.step,"POWER":pins.power,"PAUSE":pins.pause,
    } | {f"R{i}":pin for i,pin in enumerate(pins.keyboard_rows)} | {
        f"C{i}":pin for i,pin in enumerate(pins.keyboard_cols)}
    buttons=keyboard_buttons(origin)
    selected=[({f"R{row}",f"C{col}"},buttons[row][col],119)
              for row in range(8) for col in range(8)]
    selected += [({name},position,94) for name,position in controller_buttons(origin).items()]
    selected += [({"R7","C7"},control_actuators(origin)["SHIFT"],119)]
    steps=[{"use_blocks":[coordinates(position)],"ticks":300,"expect":dict.fromkeys(outputs,False)}
           for _,position,_ in selected]
    for name,position in control_switches(origin).items():
        steps += [
            {"use_blocks":[coordinates(position)],"ticks":100,"expect":{out:out==name for out in outputs}},
            {"use_blocks":[coordinates(position)],"ticks":100,"expect":dict.fromkeys(outputs,False)},
        ]
    return outputs,steps,selected


def assert_compact_console_trace(result,selected):
    minimum=2*CLOCK_HALF_PERIOD+TEMPLATES["DFF"].setup_ticks+TEMPLATES["DFF"].hold_ticks
    overlaps=[]
    for index,(signals,_,width) in enumerate(selected):
        traces={name:trace[index*300:(index+1)*300] for name,trace in result["traces"].items()}
        assert all(high_runs(trace)==([width] if name in signals else []) for name,trace in traces.items()),signals
        if len(signals)==2:
            first,second=sorted(signals)
            overlap=sum(a and b for a,b in zip(traces[first],traces[second]))
            assert overlap>=minimum,(signals,overlap)
            overlaps.append(overlap)
    assert min(overlaps)==101


def test_actual_compact_console_all_79_actuators_preserve_pulses_and_isolation():
    origin=Vec3(96,32,5000)
    outputs,steps,selected=compact_console_case(origin)
    result=run_case(iter_controls(origin),{},outputs,steps,initial_ticks=128,capture_trace=True)
    assert_compact_console_trace(result,selected)


def west_driver(pin):
    # The harness places a power source one block south of its driver anchor.
    # Select an anchor that puts the source WEST of a fabric terminal, leaving
    # the existing N/S bus intact. These coordinates are drivers, not rerouted
    # or substituted circuit blocks.
    return pin.offset(-1,0,-1)


def test_actual_preloaded_memory_retains_reads_and_writes_selected_words():
    origin = Vec3(128,32,128)
    spec = MemoryBankSpec(words=4,word_bits=32)
    initial = [0xA55AC33C,0x12345678,0xFEDCBA98,0x0F0FF0F0]
    pins = bank_ports(origin,spec)
    inputs = {f"D{i}":west_driver(p) for i,p in enumerate(pins.data_in)}
    inputs |= {f"W{i}":west_driver(p) for i,p in enumerate(pins.write_select)}
    inputs |= {f"R{i}":west_driver(p) for i,p in enumerate(pins.read_select)}
    outputs = {f"Q{i}":p for i,p in enumerate(pins.data_out)}
    outputs |= {f"S{word}:{bit}":origin.offset(bit*spec.bit_pitch+2,spec.cell_y,3+word*spec.word_pitch) for word in range(4) for bit in range(32)}
    def bits(prefix,value):
        return {f"{prefix}{bit}":bool((value>>bit)&1) for bit in range(32)}
    def stored(values):
        return {f"S{word}:{bit}":bool((value>>bit)&1) for word,value in enumerate(values) for bit in range(32)}
    steps = [{"ticks":64,"expect":stored(initial),"continuous":True}]
    current = initial[:]
    for word in range(4):
        steps += [
            {"drive":{f"R{word}":True},"ticks":64,"expect":bits("Q",current[word])},
            {"drive":{f"R{word}":False},"ticks":64,"expect":bits("Q",0)},
        ]
    for word,value in ((0,0x5AA53CC3),(3,0xFFFFFFFF),(1,0),(2,0x80000001)):
        steps.append({"drive":bits("D",value),"ticks":64,"expect":stored(current),"continuous":True})
        current[word] = value
        steps += [
            {"drive":{f"W{word}":True},"ticks":64,"expect":stored(current)},
            {"drive":{f"W{word}":False},"ticks":64,"expect":stored(current),"continuous":True},
            {"drive":bits("D",value ^ 0xFFFFFFFF),"ticks":64,"expect":stored(current),"continuous":True},
            {"drive":{f"R{word}":True},"ticks":64,"expect":bits("Q",value)},
            {"drive":{f"R{word}":False},"ticks":64,"expect":bits("Q",0)},
        ]
    image = b"".join(value.to_bytes(4,"little") for value in initial)
    run_case(iter_memory_bank(origin,spec=spec,initial=image),inputs,outputs,steps)


def test_actual_display_pixels_latch_rows_without_neighbor_crosstalk():
    origin = Vec3(128,32,128)
    spec = LampPanelSpec(width=8,height=4)
    pins = panel_ports(origin,spec)
    inputs = {f"D{i}":west_driver(p) for i,p in enumerate(pins.data_in)}
    inputs |= {f"R{i}":west_driver(p) for i,p in enumerate(pins.row_select)}
    outputs = {f"P{row}:{col}":origin.offset(col*spec.pixel_pitch_x+3,2,row*spec.pixel_pitch_y) for row in range(4) for col in range(8)}
    expected = dict.fromkeys(outputs,False)
    steps = [{"ticks":64,"expect":expected.copy()}]
    for row,value in ((0,0xA5),(1,0x5A),(2,0xFF),(3,0x81),(0,0),(3,0x18)):
        steps.append({"drive":{f"D{col}":bool((value>>col)&1) for col in range(8)},"ticks":64,"expect":expected.copy(),"continuous":True})
        expected.update({f"P{row}:{col}":bool((value>>col)&1) for col in range(8)})
        steps += [
            {"drive":{f"R{row}":True},"ticks":64,"expect":expected.copy()},
            {"drive":{f"R{row}":False},"ticks":64,"expect":expected.copy(),"continuous":True},
        ]
    run_case(iter_lamp_panel(origin,spec=spec),inputs,outputs,steps)


def test_actual_external_routes_keep_four_nearby_signals_independent():
    sources=[Vec3(32+4*i,32,5000) for i in range(4)]
    sinks=[Vec3(96+4*i,32,5040) for i in range(4)]
    nets=[ExternalNet(f"bit{i}",sources[i],(sinks[i],)) for i in range(4)]
    steps=[]
    for value in (0,1,2,4,8,15,0):
        steps.append({"drive":{f"A{i}":bool(value & (1<<i)) for i in range(4)},
                      "ticks":1200,"expect":{f"Y{i}":bool(value & (1<<i)) for i in range(4)}})
    run_case(iter_external_router(nets,branch_y=64,trunk_y=68),
             {f"A{i}":west_driver(p) for i,p in enumerate(sources)},
             {f"Y{i}":p for i,p in enumerate(sinks)},steps)


def test_actual_external_route_fans_out_in_both_directions_and_across_heights():
    source=Vec3(96,32,5000)
    sinks=(Vec3(32,40,5064),Vec3(160,24,5040),Vec3(224,32,5080))
    nets=[ExternalNet("fanout",source,sinks)]
    run_case(iter_external_router(nets,branch_y=64,trunk_y=68),{"A":west_driver(source)},
             {f"Y{i}":p for i,p in enumerate(sinks)},[
                 {"drive":{"A":value},"ticks":1300,"expect":{f"Y{i}":value for i in range(3)}}
                 for value in (False,True,False,True)
             ])


def test_actual_wired_or_return_isolates_every_leaf_driver():
    central=Vec3(96,32,5000)
    leaves=(Vec3(32,40,5064),Vec3(160,24,5040),Vec3(224,32,5080))
    blocks=iter_external_router([ExternalNet("read-return",central,leaves,wired_or=True)],branch_y=64,trunk_y=68)
    outputs={"Y":central} | {f"L{i}":pin for i,pin in enumerate(leaves)}
    steps=[]
    for value in (0,1,2,4,3,5,6,7,0):
        states={f"A{i}":bool(value & (1<<i)) for i in range(3)}
        # Observe the un-driven leaf pins as well as the central OR result.
        expected={"Y":bool(value)} | {f"L{i}":states[f"A{i}"] for i in range(3)}
        steps.append({"drive":states,"ticks":1400,"expect":expected})
    run_case(blocks,{f"A{i}":west_driver(pin) for i,pin in enumerate(leaves)},outputs,steps)


def test_actual_clock_export_survives_its_route_and_preserves_pulse_width():
    origin=Vec3(64,32,5000)
    ports=clock_ports(origin)
    destination=Vec3(240,36,5040)
    cell=list(iter_clock(origin))
    blocks=cell+list(iter_external_router([ExternalNet("clock",ports.clock,(destination,))],
                                         branch_y=64,trunk_y=68,obstacles=cell))
    result=run_case(blocks,{"enable":west_driver(ports.enable)},
                    {"local":ports.clock,"remote":destination},[
                        {"ticks":1200,"expect":{"local":False,"remote":False},"continuous":True},
                        {"drive":{"enable":True},"ticks":2000},
                        {"drive":{"enable":False},"ticks":1400,"expect":{"remote":False}},
                    ],initial_ticks=0,capture_trace=True)
    local,remote=result["traces"]["local"],result["traces"]["remote"]
    delay=remote.index(True)-local.index(True)
    assert delay > 0
    assert remote[delay:] == local[:-delay]
    assert set(high_runs(remote)) == {CLOCK_HALF_PERIOD}


@pytest.mark.parametrize("enabled", (False,True),ids=("plain_dff","lowered_dffe"))
def test_actual_placed_and_routed_register_semantics(tmp_path,enabled):
    ports={"clock":{"direction":"input","bits":[2]},"data":{"direction":"input","bits":[3]},
           "q":{"direction":"output","bits":[5]}}
    connections={"C":[2],"D":[3],"Q":[5]}
    if enabled:
        ports["enable"]={"direction":"input","bits":[4]}
        connections["E"]=[4]
    net={"modules":{"register":{"ports":ports,"cells":{"ff":{
        "type":"$_DFFE_PP_" if enabled else "$_DFF_P_","connections":connections,
    }}}}}
    path=tmp_path/"register.json"
    path.write_text(json.dumps(net))
    physical=build_physical_netlist(path,top="register",origin=Vec3(32,32,5000))
    inputs={name:west_driver(pins[0]) for name,pins in physical.input_ports.items()}
    # These intentionally slow stimuli include complete routed propagation.
    # They prove this circuit's semantics, not timing closure for a full SoC.
    settle=6000 if enabled else 2000
    def step(drive,value):
        return {"drive":drive,"ticks":settle,"expect":{"Q":value}}
    steps=[
        step({"clock":False,"data":False,**({"enable":False} if enabled else {})},False),
        step({"data":True,**({"enable":True} if enabled else {})},False),
        step({"clock":True},True),
        step({"data":False,**({"enable":False} if enabled else {})},True),
        step({"clock":False},True),
        step({"clock":True},enabled),
    ]
    if enabled:
        steps += [step({"clock":False,"enable":True},True),step({"clock":True},False)]
    run_case(physical.iter_placements(),inputs,{"Q":physical.output_ports["q"][0]},steps)


@pytest.mark.parametrize("constant_data", (False, True), ids=("routed_data", "constant_macro_input"))
def test_actual_partition_shell_and_global_tiers_preserve_register_semantics(tmp_path, constant_data):
    child_path=tmp_path/"child.json"
    child_path.write_text(json.dumps({"modules":{"child":{
        "ports":{"clock":{"direction":"input","bits":[2]},
                 "data":{"direction":"input","bits":[3]},
                 "q":{"direction":"output","bits":[4]}},
        "cells":{"ff":{"type":"$_DFF_P_","connections":{"C":[2],"D":[3],"Q":[4]}}},
    }}}))
    child=build_physical_netlist(child_path,top="child",origin=Vec3(64,32,6000))
    shell_ports={"clock":{"direction":"input","bits":[2]},"q":{"direction":"output","bits":[4]}}
    if not constant_data:
        shell_ports["data"]={"direction":"input","bits":[3]}
    shell_path=tmp_path/"shell.json"
    shell_path.write_text(json.dumps({"modules":{"shell":{
        "ports":shell_ports,
        "cells":{"u_child":{"type":"child","port_directions":{"clock":"input","data":"input","q":"output"},
                               "connections":{"clock":[2],"data":["1"] if constant_data else [3],"q":[4]}}},
    }}}))
    shell=build_physical_netlist(shell_path,top="shell",origin=Vec3(512,32,6400),
        macro_instances={"u_child":MacroInstance("u_child","child",child)},
        routing_branch_y=300,routing_trunk_y=304)
    blocks=list(child.iter_placements())+list(shell.iter_placements())
    sources={name:Vec3(32+8*i,33,6500) for i,name in enumerate(shell.input_ports)}
    result_pin=Vec3(800,33,6500)
    nets=[ExternalNet(name,sources[name],pins) for name,pins in shell.input_ports.items()]
    nets.append(ExternalNet("result",shell.output_ports["q"][0],(result_pin,)))
    access={pin:("south",) for ports in (shell.input_ports,shell.output_ports) for pins in ports.values() for pin in pins}
    blocks+=list(iter_external_router(nets,obstacles=blocks,access=access))
    steps=[{"drive":{"clock":False,**({} if constant_data else {"data":False})},"ticks":8000,"expect":{"Q":False}}]
    for value in (True,False,True):
        if not constant_data:
            steps.append({"drive":{"data":value},"ticks":8000})
        steps += [
            {"drive":{"clock":True},"ticks":8000,"expect":{"Q":constant_data or value}},
            {"drive":{"clock":False},"ticks":8000,"expect":{"Q":constant_data or value}},
        ]
    run_case(blocks,{name:west_driver(pin) for name,pin in sources.items()},{"Q":result_pin},steps)


def _actual_global_routes_reach_stacked_memory(tmp_path, *, banks, base_y):
    runner=os.environ.get("SHAMA_MCHPRS_RUNNER")
    if not runner or not Path(runner).is_file():
        pytest.skip("actual MCHPRS runner not available; do not claim runtime validation")
    origin=Vec3(512,base_y,6000)
    tile=StackedMemoryTileSpec(MemoryBankSpec(words=4,word_bits=8),banks=banks,layer_pitch=12)
    pins=stacked_tile_ports(origin,tile)
    bounds=stacked_tile_bounds(origin,tile)
    native_inputs=({f"B{i}":pin for i,pin in enumerate(pins.bank_select)}
                   | {f"R{i}":pin for i,pin in enumerate(pins.row_select)}
                   | {f"D{i}":pin for i,pin in enumerate(pins.write_data)}
                   | {"READ":pins.read_enable,"WRITE":pins.write_enable})
    sources={name:Vec3(32+4*i,-55,5400) for i,name in enumerate(native_inputs)}
    outputs={f"Q{i}":Vec3(400+4*i,-55,5400) for i in range(8)}
    nets=[ExternalNet(name,sources[name],(pin,)) for name,pin in native_inputs.items()]
    nets.extend(ExternalNet(f"Q{i}",pin,(outputs[f"Q{i}"],)) for i,pin in enumerate(pins.selected_word))
    access={pin:("north",) if name.startswith("D") else ("west",) for name,pin in native_inputs.items()}
    access.update({pin:("south",) for pin in pins.selected_word})
    endpoint_bounds=dict.fromkeys((*native_inputs.values(),*pins.selected_word),bounds)
    initial=bytes((bank*37+word*13)&255 for bank in range(banks) for word in range(4))
    blocks=chain(iter_stacked_memory_tile(origin,tile=tile,initial=initial),
        iter_external_router(nets,access=access,endpoint_bounds=endpoint_bounds,
            obstacle_bounds=(bounds,),obstacles=iter_stacked_tile_terminal_keepouts(origin,tile),minimum_y=-60))
    bits=lambda value:{f"Q{i}":bool(value>>i&1) for i in range(8)}
    steps=[{"ticks":5000,"expect":bits(0)}]
    for bank in (0,banks//2,banks-1):
        for row in (0,3):
            steps += [
                {"drive":{f"B{bank}":True,f"R{row}":True,"READ":True},"ticks":5000,"expect":bits(initial[bank*4+row])},
                {"drive":{f"B{bank}":False,f"R{row}":False,"READ":False},"ticks":5000,"expect":bits(0)},
            ]
    steps += [
        {"drive":{f"B{banks-1}":True,"R3":True,"READ":True,**{f"D{i}":bool(0xA5>>i&1) for i in range(8)}},"ticks":5000,"expect":bits(initial[-1])},
        {"drive":{"WRITE":True},"ticks":5000,"expect":bits(0xA5)},
        {"drive":{"WRITE":False},"ticks":5000,"expect":bits(0xA5)},
        {"drive":{f"D{i}":bool(0x5A>>i&1) for i in range(8)},"ticks":5000,"expect":bits(0xA5)},
    ]
    # Stream the long global routes rather than retaining Placement objects,
    # a second object graph for JSON, and a huge encoded string together.
    # Every emitted coordinate/state still undergoes conflict and height
    # checks before the unchanged literal block fixture reaches the engine.
    coord=lambda point:[point.x,point.y+64,point.z]
    path=tmp_path/"routed-memory-case.json"
    occupied,states={},{}
    count=0
    with path.open("w") as handle:
        handle.write('{"blocks":[')
        for placement in blocks:
            point=coord(placement.pos)
            assert -60 <= placement.pos.y <= 318
            assert point[0]>=0 and 0<=point[2]<(1<<24)
            key=point[0]<<40 | point[1]<<24 | point[2]
            state=states.setdefault(placement.block,len(states))
            assert occupied.setdefault(key,state)==state,f"conflicting physical blocks at {placement.pos}"
            if count:
                handle.write(",")
            json.dump({"pos":point,"name":placement.block.name,
                       "properties":dict(placement.block.properties)},handle,separators=(",",":"))
            count+=1
        handle.write("]")
        for key,value in {"inputs":{name:coord(west_driver(pin)) for name,pin in sources.items()},
                          "outputs":{name:coord(pin) for name,pin in outputs.items()},
                          "steps":steps,"initial_ticks":128}.items():
            handle.write(","+json.dumps(key)+":")
            json.dump(value,handle,separators=(",",":"))
        handle.write("}")
    unique=len(occupied)
    del occupied
    with path.open() as handle:
        result=subprocess.run([runner],stdin=handle,capture_output=True,text=True,timeout=120)
    assert result.returncode==0,result.stdout[-4000:]+result.stderr[-4000:]
    runtime=json.loads(result.stdout)
    runtime.pop("traces",None)
    assert runtime["status"]=="passed"
    from shamaos.generator import source_provenance
    report={"kind":"routed-stacked-memory-runtime","banks":banks,"base_y":base_y,
            "selected_word_y":pins.selected_word[0].y,"emitted_placements":count,
            "unique_blocks":unique,"runtime":runtime,"source":source_provenance(),
            "complete_machine_runtime_verified":False}
    destination=Path(os.environ.get("SHAMA_VALIDATION_REPORT_DIR",tmp_path))
    destination.mkdir(parents=True,exist_ok=True)
    (destination/f"routed-memory-{banks}-banks-y{base_y}.json").write_text(json.dumps(report,indent=2)+"\n")
    path.unlink()


def test_actual_global_routes_reach_all_faces_of_a_thirty_layer_memory_tile(tmp_path):
    _actual_global_routes_reach_stacked_memory(tmp_path,banks=30,base_y=-56)


def test_actual_global_routes_reach_the_shared_ram_top_return_at_y306(tmp_path):
    # The highest return in the actual cache/VRAM/RAM column lies only one
    # block below its local control ceiling; its south escape must remain
    # routable without crossing another bank or Java's build-height limit.
    _actual_global_routes_reach_stacked_memory(tmp_path,banks=24,base_y=24)


def test_actual_router_honors_forced_west_escapes_for_source_and_sink():
    source,sink=Vec3(96,32,5000),Vec3(160,32,5040)
    obstacles=[Placement(pin.offset(dz=dz),SUPPORT,"keepout") for pin in (source,sink) for dz in (-1,1)]
    escapes=_plan_escapes((source,sink),64,obstacles)
    assert all(escape.escape_x is not None for escape in escapes.values())
    blocks=obstacles+list(iter_external_router([ExternalNet("west",source,(sink,))],
                                              branch_y=64,trunk_y=68,obstacles=obstacles))
    # Drive from the east: the west side now belongs to the actual route.
    run_case(blocks,{"A":source.offset(1,0,-1)},{"Y":sink},[
        {"drive":{"A":value},"ticks":1400,"expect":{"Y":value}} for value in (False,True,False)
    ])


def test_actual_debug_wall_and_routed_inputs_are_independent():
    origin=Vec3(128,32,5000)
    fields=tuple((f"row{row}",4) for row in range(4))
    pins=debug_ports(origin,fields=fields,columns=4)
    cell=list(iter_debug_panel(origin,fields=fields,columns=4))
    inputs={f"{name}:{bit}":pin for name,group in pins.items() for bit,pin in enumerate(group)}
    outputs={name:pin.offset(dz=-3) for name,pin in inputs.items()}
    run_case(cell,{name:west_driver(pin) for name,pin in inputs.items()},outputs,[
        {"drive":{other:other == name for other in inputs},"ticks":12,"expect":{other:other == name for other in outputs}}
        for name in inputs
    ])
    sources={f"A{i}":Vec3(32+i*4,32,5060) for i in range(4)}
    nets=[ExternalNet(f"A{i}",sources[f"A{i}"],(pins[f"row{i}"][i],)) for i in range(4)]
    access={pin:("wall-south",) for group in pins.values() for pin in group}
    blocks=cell+list(iter_external_router(nets,branch_y=64,trunk_y=68,obstacles=cell,access=access))
    run_case(blocks,{name:west_driver(pin) for name,pin in sources.items()},outputs,[
        {"drive":{name:name == f"A{i}" for name in sources},"ticks":1400,
         "expect":{name:name == f"row{i}:{i}" for name in outputs}}
        for i in range(4)
    ])


def test_actual_routed_console_preserves_row_column_overlap_and_all_output_isolation():
    origin=Vec3(2064,32,5000)
    pins=control_ports(origin)
    cell=list(iter_controls(origin))
    sources={f"R{i}":pin for i,pin in enumerate(pins.keyboard_rows)} | {f"C{i}":pin for i,pin in enumerate(pins.keyboard_cols)}
    sources |= dict(zip(CONTROLLER_NAMES,pins.controller)) | {
        "RESET":pins.reset,"STEP":pins.step,"POWER":pins.power,"PAUSE":pins.pause,
    }
    outputs={name:Vec3(2400+i*8,33,5200) for i,name in enumerate(sources)}
    nets=[ExternalNet(name,pin,(outputs[name],)) for name,pin in sources.items()]
    bounds=control_bounds(origin)
    blocks=cell+list(iter_external_router(nets,branch_y=96,trunk_y=100,obstacles=cell,
        access=control_route_access(origin),endpoint_bounds=dict.fromkeys(sources.values(),bounds),
        obstacle_bounds=(bounds,)))
    _,_,all_selected=compact_console_case(origin)
    selected=all_selected
    steps=[{"use_blocks":[coordinates(button)],"ticks":1500,"expect":dict.fromkeys(outputs,False)}
           for _,button,_ in selected]
    for name,position in control_switches(origin).items():
        steps += [
            {"use_blocks":[coordinates(position)],"ticks":1500,"expect":{out:out==name for out in outputs}},
            {"use_blocks":[coordinates(position)],"ticks":1500,"expect":dict.fromkeys(outputs,False)},
        ]
    result=run_case(blocks,{},outputs,steps,capture_trace=True,timeout=120)
    minimum=2*CLOCK_HALF_PERIOD+TEMPLATES["DFF"].setup_ticks+TEMPLATES["DFF"].hold_ticks
    overlaps=[]
    for index,(signals,_,width) in enumerate(selected):
        traces={name:trace[index*1500:(index+1)*1500] for name,trace in result["traces"].items()}
        assert all(high_runs(trace)==([width] if name in signals else []) for name,trace in traces.items()),signals
        if len(signals)==2:
            first,second=sorted(signals)
            overlap=sum(a and b for a,b in zip(traces[first],traces[second]))
            assert overlap>=minimum,(signals,overlap)
            overlaps.append(overlap)
    report_directory=os.environ.get("SHAMA_VALIDATION_REPORT_DIR")
    if report_directory:
        from shamaos.generator import source_provenance
        result.pop("traces",None)
        directory=Path(report_directory)
        directory.mkdir(parents=True,exist_ok=True)
        report={"kind":"routed-console-all-79-actuators","runtime":result,
                "actuators":79,"minimum_row_column_overlap_rt":min(overlaps),
                "required_overlap_rt":minimum,"matrix_pulse_rt":119,"controller_pulse_rt":94,
                "source":source_provenance(),"complete_machine_runtime_verified":False}
        (directory/"routed-console-runtime.json").write_text(json.dumps(report,indent=2)+"\n")


def test_actual_routed_display_uses_clear_front_and_side_approaches():
    origin=Vec3(128,32,5000)
    spec=LampPanelSpec(width=8,height=4)
    pins=panel_ports(origin,spec)
    cell=list(iter_lamp_panel(origin,spec=spec))
    inputs={f"D{i}":pin for i,pin in enumerate(pins.data_in)} | {f"R{i}":pin for i,pin in enumerate(pins.row_select)}
    sources={name:Vec3(32+i*4,32,5200) for i,name in enumerate(inputs)}
    nets=[ExternalNet(name,sources[name],(pin,)) for name,pin in inputs.items()]
    blocks=cell+list(iter_external_router(nets,branch_y=96,trunk_y=100,obstacles=cell))
    outputs={f"P{row}:{col}":origin.offset(col*spec.pixel_pitch_x+3,2,row*spec.pixel_pitch_y) for row in range(4) for col in range(8)}
    expected=dict.fromkeys(outputs,False)
    steps=[]
    for row,value in ((0,0xA5),(1,0x5A),(2,0x81),(3,0x18)):
        steps.append({"drive":{f"D{i}":bool((value>>i)&1) for i in range(8)},"ticks":1800,"expect":expected.copy()})
        expected.update({f"P{row}:{i}":bool((value>>i)&1) for i in range(8)})
        steps += [
            {"drive":{f"R{row}":True},"ticks":1800,"expect":expected.copy()},
            {"drive":{f"R{row}":False},"ticks":1800,"expect":expected.copy()},
        ]
    run_case(blocks,{name:west_driver(pin) for name,pin in sources.items()},outputs,steps)
