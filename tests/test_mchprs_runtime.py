"""Actual MCHPRS simulation of the generated cells (required release evidence).

Set SHAMA_MCHPRS_RUNNER to the binary built from tests/mchprs_runtime/Cargo.toml.
Normal Python-only development can skip this external runtime; release CI must
build the pinned runner and execute these tests without skips.
"""
import json
import os
from itertools import product
from pathlib import Path
import subprocess

import pytest

from shamaos.hardware.logic import TEMPLATES
from shamaos.hardware.clock import CLOCK_HALF_PERIOD, clock_ports, iter_clock
from shamaos.hardware.controls import (
    BUTTON_FLOOR, CONTROLLER_NAMES, control_ports, controller_buttons,
    iter_controls, keyboard_buttons,
)
from shamaos.hardware.display import LampPanelSpec, iter_lamp_panel, panel_ports
from shamaos.hardware.external_router import ExternalNet, iter_external_router
from shamaos.hardware.memory import DUST, SUPPORT, MemoryBankSpec, bank_ports, iter_memory_bank, repeater
from shamaos.hardware.pulse import iter_pulse_stretcher, pulse_ports, rotate_point
from shamaos.model import Placement, Vec3
from shamaos.synthesis import build_physical_netlist


ORIGIN = Vec3(32, 32, 32)


def coordinates(p):
    return [p.x, p.y, p.z]


def run_case(placements, inputs, outputs, steps, *, initial_ticks=32, capture_trace=False):
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
    result = subprocess.run([runner], input=json.dumps(case), capture_output=True, text=True, timeout=60)
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
    for name,x in (("POWER",90),("PAUSE",100)):
        switch = coordinates(origin.offset(x,1,46))
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
    steps = [{"ticks":32,"expect":dict.fromkeys(outputs,False)}]
    for row in range(8):
        for col in range(8):
            selected = {f"R{row}",f"C{col}"}
            steps += [
                {"use_blocks":[coordinates(buttons[row][col])],"ticks":32,"expect":{out:out in selected for out in outputs}},
                {"ticks":128,"expect":dict.fromkeys(outputs,False)},
            ]
    run_case(iter_controls(origin),{},outputs,steps)


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
    outputs = {f"P{row}:{col}":origin.offset(col*5+3,2,row*5) for row in range(4) for col in range(8)}
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


def test_actual_clock_export_survives_its_route_and_preserves_pulse_width():
    origin=Vec3(64,32,5000)
    ports=clock_ports(origin)
    destination=Vec3(240,36,5040)
    cell=list(iter_clock(origin))
    blocks=cell+list(iter_external_router([ExternalNet("clock",ports.clock,(destination,))],
                                         branch_y=64,trunk_y=68,obstacles=cell))
    result=run_case(blocks,{"enable":west_driver(ports.enable)},
                    {"local":ports.clock,"remote":destination},[
                        {"drive":{"enable":True},"ticks":2000},
                        {"drive":{"enable":False},"ticks":1400,"expect":{"remote":False}},
                    ],capture_trace=True)
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
