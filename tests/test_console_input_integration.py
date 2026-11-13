"""Actual console waveforms replayed through shama_input at every raw phase.

This verifies the measured native/routed source interface and the real RTL FIFO.
Physical propagation inside the mapped input controller and the complete-machine
clock/data network remain separate acceptance gates.
"""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from shamaos.hardware.clock import CLOCK_HALF_PERIOD
from shamaos.hardware.controls import (
    CONTROLLER_NAMES, control_bounds, control_ports, control_route_access, iter_controls,
)
from shamaos.hardware.external_router import ExternalNet, iter_external_router
from shamaos.hardware.logic import TEMPLATES
from shamaos.model import Vec3
from test_mchprs_runtime import compact_console_case, coordinates, high_runs, run_case


ROOT = Path(__file__).resolve().parents[1]


def expected_keyboard_banks():
    # Public physical key legends, independent of the DUT's keymap function.
    base = [*b"ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", 0x20, 0x0D, 0x08, 0x09,
            *b".,-_/+=:", *b"[]()!?*;", 0x80, 0x81, 0x82, 0x83, 0x1B, 0x7F, 0x20, 0]
    shifted = list(base)
    shifted[:26] = b"abcdefghijklmnopqrstuvwxyz"
    shifted[40:48] = b"\"'<>\\|@`"
    shifted[48:56] = b"{}#$%&^~"
    assert len(base) == len(shifted) == 64
    assert set(range(32, 127)) <= set(base + shifted)
    return base + shifted


@pytest.fixture(scope="module", params=["native", "routed"])
def measured_console(request, tmp_path_factory):
    if not shutil.which("iverilog") or not shutil.which("vvp"):
        pytest.skip("physical-trace/input integration requires Icarus Verilog")
    directory = tmp_path_factory.mktemp(f"console-input-{request.param}")
    routed = request.param == "routed"
    origin = Vec3(2064, 32, 5000) if routed else Vec3(96, 32, 5000)
    pins = control_ports(origin)
    native = list(iter_controls(origin))
    native_outputs, _, selected = compact_console_case(origin)
    assert len(selected) == 77
    stride = 1500 if routed else 300
    if routed:
        sources = {f"R{i}": pin for i, pin in enumerate(pins.keyboard_rows)} | {
            f"C{i}": pin for i, pin in enumerate(pins.keyboard_cols)}
        sources |= dict(zip(CONTROLLER_NAMES, pins.controller)) | {
            "RESET": pins.reset, "STEP": pins.step, "POWER": pins.power, "PAUSE": pins.pause}
        outputs = {name: Vec3(2400 + i * 8, 33, 5200) for i, name in enumerate(sources)}
        nets = [ExternalNet(name, pin, (outputs[name],)) for name, pin in sources.items()]
        bounds = control_bounds(origin)
        blocks = native + list(iter_external_router(nets, branch_y=96, trunk_y=100,
            obstacles=native, access=control_route_access(origin),
            endpoint_bounds=dict.fromkeys(sources.values(), bounds), obstacle_bounds=(bounds,)))
    else:
        blocks, outputs = native, native_outputs
    # Preserve every block and route, translating the complete case together
    # to avoid allocating leading empty chunks in the dense test harness.
    delta = Vec3(*(16 - min(getattr(block.pos, axis) for block in blocks) for axis in ("x", "y", "z")))
    move = lambda point: point.offset(delta.x, delta.y, delta.z)
    placed = [replace(block, pos=move(block.pos)) for block in blocks]
    translated_outputs = {name: move(point) for name, point in outputs.items()}
    steps = [{"use_blocks": [coordinates(move(button))], "ticks": stride,
              "expect": dict.fromkeys(outputs, False)} for _, button, _ in selected]
    result = run_case(placed, {}, translated_outputs, steps, initial_ticks=32 if routed else 128,
                      capture_trace=True, timeout=120)
    traces = result["traces"]
    minimum_overlap = stride
    for index, (signals, _, width) in enumerate(selected):
        segment = {name: trace[index * stride:(index + 1) * stride] for name, trace in traces.items()}
        assert all(high_runs(trace) == ([width] if name in signals else []) for name, trace in segment.items())
        if len(signals) == 2:
            first, second = sorted(signals)
            together = [a and b for a, b in zip(segment[first], segment[second])]
            minimum_overlap = min(minimum_overlap, sum(together))
            setup, hold = TEMPLATES["DFF"].setup_ticks, TEMPLATES["DFF"].hold_ticks
            period = 2 * CLOCK_HALF_PERIOD
            for phase in range(period):
                assert any(all(together[tick - setup:tick + hold + 1])
                           for tick in range(phase, stride, period)
                           if tick >= setup and tick + hold < stride), (request.param, index, phase)
    assert minimum_overlap == (81 if routed else 101)
    trace_file = directory / "measured-input.hex"
    names = [*(f"R{i}" for i in range(8)), *(f"C{i}" for i in range(8)), *CONTROLLER_NAMES]
    with trace_file.open("w") as stream:
        for tick in range(77 * stride):
            sample = sum(int(traces[name][tick]) << bit for bit, name in enumerate(names))
            stream.write(f"{sample:07x}\n")
    key_file = directory / "physical-key-legends.hex"
    key_file.write_text("".join(f"{value:02x}\n" for value in expected_keyboard_banks()))
    return {"kind": request.param, "directory": directory, "trace": trace_file, "keys": key_file,
            "stride": stride, "minimum_overlap": minimum_overlap, "engine_revision": result["revision"],
            "engine_ticks": result["ticks"], "translation": vars(delta), "placements": len(blocks)}


def run_input_rtl(case, period):
    binary = case["directory"] / f"input-period-{period}.vvp"
    compiled = subprocess.run(["iverilog", "-g2012", "-s", "console_trace_input_tb",
        f"-Pconsole_trace_input_tb.RAW_PERIOD={period}", "-o", str(binary),
        str(ROOT / "rtl/shama_input.sv"), str(ROOT / "tests/rtl/console_trace_input_tb.sv")],
        capture_output=True, text=True, timeout=30)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    result = subprocess.run(["vvp", str(binary), f"+TRACE={case['trace']}", f"+KEYS={case['keys']}",
        f"+STRIDE={case['stride']}"], capture_output=True, text=True, timeout=120)
    (case["directory"] / f"input-period-{period}.txt").write_text(result.stdout + result.stderr)
    return result


def test_actual_console_traces_reach_input_rtl_once_at_every_raw_phase(measured_console):
    period = 2 * CLOCK_HALF_PERIOD
    result = run_input_rtl(measured_console, period)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"CONSOLE TRACE INPUT HELD ACK PRIORITY FIFO SHIFT PASS phases={period}" in result.stdout
    report = {"kind": f"{measured_console['kind']}-physical-trace-to-input-rtl", "status": "passed",
        "raw_period_rt": period, "clock_phases": period, "matrix_banks": 2, "matrix_keys_per_bank": 64,
        "controller_buttons": 10, "dedicated_shift": True, "fifo_depth": 16,
        "held_ack_new_arrival": True, "reprioritized_ack_snapshot": True,
        "oldest_identical_record_ack": True, "overflow_and_universal_exit": True,
        "minimum_measured_row_column_overlap_rt": measured_console["minimum_overlap"],
        "engine_revision": measured_console["engine_revision"], "engine_ticks": measured_console["engine_ticks"],
        "geometry_translation": measured_console["translation"], "placement_operations": measured_console["placements"],
        "sample_file_sha256": hashlib.sha256(measured_console["trace"].read_bytes()).hexdigest(),
        "input_rtl_sha256": hashlib.sha256((ROOT / "rtl/shama_input.sv").read_bytes()).hexdigest(),
        "scope": "measured source interface plus actual RTL behavior; mapped input logic and complete global timing unverified"}
    (measured_console["directory"] / "integration-report.json").write_text(json.dumps(report, indent=2) + "\n")
    report_directory = os.environ.get("SHAMA_VALIDATION_REPORT_DIR")
    if report_directory:
        directory = Path(report_directory); directory.mkdir(parents=True, exist_ok=True)
        (directory / f"console-input-{measured_console['kind']}.json").write_text(json.dumps(report, indent=2) + "\n")


def test_unmodified_physical_trace_exposes_too_slow_input_clock(measured_console):
    # Keep the complete actual waveform. A deliberately slower raw clock can
    # miss the row/column overlap; this must fail the same RTL capture oracle.
    result = run_input_rtl(measured_console, 4 * CLOCK_HALF_PERIOD)
    assert result.returncode != 0, "too-slow raw clock did not expose a dropped physical key"
    assert "CONSOLE_CAPTURE_COUNT phase=" in result.stdout, result.stdout + result.stderr
