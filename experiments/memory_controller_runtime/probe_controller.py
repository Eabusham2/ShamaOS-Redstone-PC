"""Save the complete cache controller and measure all actual register clocks.

This is a controller-only clock gate.  Neither memory responses nor a complete
cache transaction are simulated by this helper.  Physical memory connections,
functional transactions and persistence remain separate acceptance gates.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

from experiments.memory_controller_runtime import fixture
from shamaos.bounded_build import emit_bounded_partition
from shamaos.hardware.logic import template_for
from shamaos.mchprs_import import export_world
from shamaos.worldio import NativeWorldReader


def _sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _save(path, document):
    with Path(path).open("x") as stream:
        json.dump(document, stream, separators=(",", ":"))
        stream.write("\n")


def controller_clock_maps(graph, placed):
    ports = {(entity["port_name"], entity["port_index"]): placed.ports[entity["id"]]
             for entity in graph.entities if entity["kind"] == "port"}
    inputs = {name: ports[name, 0] for name in ("clk", "rst")}
    clocks = {}
    for tile in placed.all_modules().values():
        for name, origin in tile.physical.cell_origins.items():
            cell = template_for(tile.physical.cell_types[name])
            if cell.cell_type != "DFF":
                continue
            if name in clocks:
                raise ValueError("register clock occurs in multiple modules")
            offset = cell.pins["C"]
            clocks[name] = origin.offset(offset.x, offset.y, offset.z)
    if len(clocks) != 115:
        raise ValueError("controller probe must retain all 115 register clocks")
    return inputs, clocks, ports["req_ready", 0]


def clock_scenario(inputs, clocks, ready, *, half_period, settle_ticks):
    if type(half_period) is not int or half_period < 1 or type(settle_ticks) is not int or settle_ticks < 1:
        raise ValueError("positive integer tick durations are required")
    coord = lambda point: [point.x, point.y, point.z]
    steps = [{"drive": {"clk": False, "rst": True}, "ticks": settle_ticks}]
    for _ in range(3):
        steps.extend(({"drive": {"clk": True}, "ticks": half_period},
                      {"drive": {"clk": False}, "ticks": half_period}))
    steps.append({"ticks": settle_ticks, "expect": {**{name: False for name in clocks}, "req_ready": False}})
    samples = sum(step["ticks"] for step in steps) * len(clocks)
    if samples > 20_000_000:
        raise ValueError("controller clock probe exceeds its retained trace budget")
    return {"inputs": {name: coord(point) for name, point in inputs.items()},
            "outputs": {**{name: coord(point) for name, point in clocks.items()}, "req_ready": coord(ready)},
            "initial_ticks": settle_ticks, "steps": steps, "capture_trace": True,
            "trace_outputs": sorted(clocks)}, samples


def _runs(values):
    runs, start = [], None
    for tick, value in enumerate([*values, False]):
        if value and start is None:
            start = tick
        elif not value and start is not None:
            runs.append((start, tick - start))
            start = None
    return runs


def analyze_clocks(result, clock_names, half_period):
    traces = result["traces"]
    if set(traces) != set(clock_names):
        raise ValueError("runner trace inventory differs from all 115 register clocks")
    entries = {name: _runs(traces[name]) for name in sorted(clock_names)}
    first = entries[next(iter(entries))]
    matched = all(value == first for value in entries.values())
    spaced = len(first) == 3 and all(start == first[0][0] + 2 * index * half_period
                                   for index, (start, _) in enumerate(first))
    complete = spaced and all(width == half_period for _, width in first)
    return {"register_clocks": len(entries), "all_arrivals_and_widths_identical": matched,
            "all_three_requested_pulses_preserved": complete, "reference_pulses": first,
            "requested_pulse_spacing_preserved": spaced,
            "actual_half_period_ticks": half_period,
            "clock_gate_passed": matched and complete,
            "scope": "Physical controller clock pulses only; no memory transaction or whole-machine timing claim"}


def main(prepared: Path, output: Path, runner: Path, *, half_period=8192):
    _, graph, plan, placed, _, _ = fixture.load_controller(prepared)
    inputs, clocks, ready = controller_clock_maps(graph, placed)
    build = emit_bounded_partition(fixture.HERE / "evidence/cache-controller-c331.json.gz", fixture.TOP,
                                  plan, placed, output, expected_mapped_sha256=fixture.MAPPED_SHA,
                                  surface_y=-61, maximum_y=285)
    world = output / "world"
    reader = NativeWorldReader(world)
    pads = {}
    for name, point in inputs.items():
        # The native runner places a redstone block one block SOUTH of the
        # supplied input location.  The original DUST terminal is untouched.
        pad = point.offset(dz=1)
        if reader.get_block(point).name != "minecraft:redstone_wire":
            raise ValueError(f"original {name} input terminal is not saved dust")
        if reader.get_block(pad).name != "minecraft:air":
            raise ValueError(f"native {name} driver pad is obstructed")
        pads[name] = [pad.x, pad.y, pad.z]
    if len(set(map(tuple, pads.values()))) != 2:
        raise ValueError("clock/reset driver pads overlap")
    stream = output / "controller-world.jsonl"
    imported = export_world(world, stream)
    _save(output / "controller-import.json", imported)
    delay = max(record["balanced_repeater_delay"] for record in build["clock_route_matching"].values())
    settle = 4 * delay + 1024
    report = {"schema": "complete-cache-controller-clock-probe-v1", "status": "running",
              "mapped_sha256": fixture.MAPPED_SHA, "native_controller_cells": graph.cell_count,
              "native_controller_registers": len(clocks), "world_stream_sha256": imported["stream_sha256"],
              "runner_sha256": _sha(runner), "probe_source_sha256": _sha(Path(__file__)),
              "driver_pads": pads, "structural_clock_delay_ticks": delay,
              "connected_cache_runtime_verified": False, "complete_machine_runtime_verified": False,
              "cases": {}}
    for name, period, expected in (("safe", half_period, True), ("too-fast", 1, False)):
        scenario, samples = clock_scenario(inputs, clocks, ready, half_period=period, settle_ticks=settle)
        scenario_path = output / f"clock-{name}-scenario.json"
        _save(scenario_path, scenario)
        result_path = output / f"clock-{name}-runtime.json"
        started = time.monotonic()
        with result_path.open("x") as stdout, (output / f"clock-{name}-stderr.txt").open("x") as stderr:
            process = subprocess.Popen([str(runner), str(stream), str(scenario_path), str(imported["chunks"] + 16)],
                                       stdout=stdout, stderr=stderr)
            _, status, usage = os.wait4(process.pid, 0)
            process.returncode = os.waitstatus_to_exitcode(status)
        case = {"exit_code": process.returncode, "wall_seconds": time.monotonic() - started,
                "peak_rss_kib": usage.ru_maxrss, "trace_samples": samples,
                "scenario_sha256": _sha(scenario_path), "runtime_sha256": _sha(result_path)}
        if process.returncode:
            case["status"] = "runner-failed"
            report["cases"][name] = case
            report["status"] = "failed"
            _save(output / "controller-clock-probe-report.json", report)
            raise ValueError(f"actual controller {name} clock probe failed in native runner")
        result = json.loads(result_path.read_text())
        if result["status"] != "passed" or result["imported_stream_sha256"] != imported["stream_sha256"]:
            raise ValueError("native probe result does not bind the saved controller world")
        case.update(analyze_clocks(result, clocks, period))
        if case["clock_gate_passed"] is not expected:
            case["status"] = "unexpected-clock-result"
            report["cases"][name] = case
            report["status"] = "failed"
            _save(output / "controller-clock-probe-report.json", report)
            raise ValueError(f"actual {name} clock pulses did not satisfy their acceptance gate")
        case["status"] = "passed" if expected else "timing-failure-detected"
        report["cases"][name] = case
        del result
    report["status"] = "controller-clock-probes-passed-memory-integration-pending"
    _save(output / "controller-clock-probe-report.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prepared", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("runner", type=Path)
    parser.add_argument("--half-period", type=int, default=8192)
    options = parser.parse_args()
    print(json.dumps(main(options.prepared, options.output, options.runner,
                          half_period=options.half_period), indent=2))
