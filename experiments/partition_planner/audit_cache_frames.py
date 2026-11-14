"""Independently review complete-cache wiring and request-frame expectations.

The runtime oracle is the real adapter RTL with an explicitly abstract,
level-sensitive two-bank store. This validates the planned transactions, not
native decoder propagation, reset races, physical routing or snapshot replay.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from experiments.memory_controller_runtime import fixture, geometry, scenarios
from shamaos.hardware.memory_backbone import fabric_tile_ports
from shamaos.model import Vec3
from shamaos.system_build import memory_fabric_nets


def check(value, message):
    if not value:
        raise ValueError(message)


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def interconnect_review():
    graph, _, fabric, origin = fixture.graph_and_fabric()
    ports = {(entity["port_name"], entity["port_index"]): entity
             for entity in graph.entities if entity["kind"] == "port"}
    expected = {name: ("input", width) for name, width in scenarios.HOST_WIDTHS.items()}
    expected.update(selected_word=("input", 32), req_ready=("output", 1),
                    req_rdata=("output", 32), bank_select=("output", 2),
                    row_address=("output", 10), read_enable=("output", 1),
                    write_commit=("output", 1), write_data=("output", 32))
    expected_pins = {(name, bit): direction for name, (direction, width) in expected.items()
                     for bit in range(width)}
    check({key: value["direction"] for key, value in ports.items()} == expected_pins,
          "complete original controller pin inventory or direction differs")
    points = {key: Vec3(index * 4, 100, -1000) for index, key in enumerate(sorted(ports))}
    native = fabric_tile_ports(origin, fabric)
    check(len(native) == 1, "cache must preserve the entire single two-bank fabric")
    actual = geometry.memory_nets(points, native[0])
    directions = {direction: {
        "cache_" + name: tuple(points[name, bit] for bit in range(width))
        for name, (kind, width) in expected.items() if kind == direction}
        for direction in ("input", "output")}
    production = tuple(memory_fabric_nets("cache", origin, fabric,
        inputs=directions["input"], outputs=directions["output"]))
    def signature(net):
        return net.source, net.sinks, net.wired_or
    check(tuple(map(signature, actual)) == tuple(map(signature, production)),
          "fixture connection differs from the production fabric ABI")
    check(len(actual) == 78 and sum(net.wired_or for net in actual) == 32,
          "complete interconnect must retain 46 outgoing nets and 32 native returns")
    check(len({point for net in actual for point in (net.source, *net.sinks)}) == 156,
          "controller/fabric endpoints alias across different nets")
    return {"native_cells": graph.cell_count, "original_pins": len(ports),
            "host_driven_pins": sum(scenarios.HOST_WIDTHS.values()),
            "memory_nets": len(actual), "controller_to_fabric": 46,
            "native_return_nets": 32, "distinct_endpoint_positions": 156,
            "production_net_signatures_exact": True,
            "cache_bounds": asdict(fabric.bounds(origin)), "graph_source": graph.source}


def group_bits(values, prefix):
    groups = {}
    for name, enabled in (values or {}).items():
        tag, signal, raw_bit = name.split(":")
        check(tag == prefix and type(enabled) is bool, "invalid scenario scalar")
        bit = int(raw_bit)
        mask, value = groups.get(signal, (0, 0))
        check(not mask & (1 << bit), "duplicate scenario bit")
        groups[signal] = mask | (1 << bit), value | (int(enabled) << bit)
    return groups


def write_bench(directory, frames):
    capacity = frames[0].capacity_bytes
    check(all(frame.capacity_bytes == capacity for frame in frames), "RTL frame review mixes cache capacities")
    words = capacity // 4
    banks = (capacity + 4095) // 4096
    swap_mask = 1024 if banks > 1 else 1
    widths = dict(scenarios.HOST_WIDTHS)
    widths.update(req_ready=1, req_rdata=32, bank_select=banks, row_address=10,
                  read_enable=1, write_commit=1, write_data=32, selected_word=32)
    boundaries = [frame.stored_words for frame in frames if frame.stored_words is not None]
    with (directory / "initial.hex").open("w") as stream:
        stream.writelines(f"{word:08x}\n" for word in scenarios.initial_words(capacity_bytes=capacity))
    with (directory / "expected.hex").open("w") as stream:
        stream.writelines(f"{word:08x}\n" for words in boundaries for word in words)
    code = ["`timescale 1ns/1ps", "module cache_frames;",
        "parameter integer SWAP_READ_BANKS=0, SWAP_WRITE_BANKS=0;",
        "logic clk=0, rst=0, req_valid=0, req_we=0;",
        "logic [31:0] req_addr=0, req_wdata=0; logic [3:0] req_wstrb=0;",
        f"wire req_ready, read_enable, write_commit; wire [{banks - 1}:0] bank_select;",
        "wire [9:0] row_address; wire [31:0] req_rdata, write_data, selected_word;",
        "shama_cache_adapter dut(.*);",
        f"logic [31:0] memory[0:{words - 1}];",
        f"logic [31:0] expected[0:{len(boundaries) * words - 1}];",
        "integer index, commits=0, rises=0, selected;",
        "always @* selected = " + ("(bank_select[1] ? 1024 : 0) + " if banks > 1 else "") + "row_address;",
        f"assign selected_word = read_enable ? memory[selected ^ (SWAP_READ_BANKS ? {swap_mask} : 0)] : 32'd0;",
        "// A level-sensitive abstract store, deliberately not the posedge-only test array.",
        "// #1 samples settled RTL levels; it does not model physical propagation or reset races.",
        f"always #1 if (write_commit === 1'b1) memory[selected ^ (SWAP_WRITE_BANKS ? {swap_mask} : 0)] = write_data;",
        "always @(posedge write_commit) commits=commits+1;",
        "always @(posedge clk) rises=rises+1;",
        "initial begin",
        '$readmemh("initial.hex", memory); $readmemh("expected.hex", expected);']
    boundary = 0
    assertion_count = 0
    for frame_index, frame in enumerate(frames):
        for signal, (mask, value) in group_bits(frame.drive, "host").items():
            width = widths[signal]
            code.append(f"{signal}=({signal} & ~{width}'h{mask:x}) | {width}'h{value:x};")
        if frame.half_periods:
            code.append(f"#{frame.half_periods * 20};")
        for signal, (mask, value) in group_bits(frame.expect, "port").items():
            width = widths[signal]
            code.append(f"if (({signal} & {width}'h{mask:x}) !== {width}'h{value:x}) "
                        f'$fatal(1,"FRAME {frame_index} {frame.label} {signal} expected=%h actual=%h", '
                        f"{width}'h{value:x}, {signal});")
            assertion_count += mask.bit_count()
        if frame.stored_words is not None:
            code.append(f"for(index=0; index<{words}; index=index+1) if(memory[index] !== expected[{boundary * words}+index]) "
                        f'$fatal(1,"STORAGE {frame_index} {frame.label} word=%0d expected=%h actual=%h", '
                        f"index,expected[{boundary * words}+index],memory[index]);")
            boundary += 1
    code += [f'$display("CACHE FRAME ORACLE PASS frames={len(frames)} boundaries={boundary} rises=%0d commits=%0d",rises,commits);',
             "$finish; end endmodule"]
    path = directory / "cache_frames.sv"
    path.write_text("\n".join(code) + "\n")
    return {"frames": len(frames), "boundaries": boundary,
            "word_boundary_assertions": boundary * words,
            "bit_boundary_assertions": boundary * capacity * 8,
            "controller_bit_assertions": assertion_count,
            "testbench_sha256": sha(path)}


def rtl_review(directory, *, source_root=None, capacity_bytes=8192):
    compiler, simulator = shutil.which("iverilog"), shutil.which("vvp")
    check(compiler and simulator, "Icarus Verilog and vvp are required; no skipped acceptance")
    scenarios.check_capacity(capacity_bytes)
    if source_root is None:
        check(capacity_bytes == 8192, "historical RTL can only prove its original 8 KiB capacity")
        source_root = directory / "historical-inputs"
        (source_root / "rtl").mkdir(parents=True, exist_ok=False)
        historical = fixture.historical_inputs()
        for name in ("rtl/shama_memory_adapter.sv", "rtl/shama_boot_params.svh"):
            (source_root / name).write_bytes(historical[name])
    initial = list(scenarios.functional_frames(capacity_bytes=capacity_bytes))
    final = next(frame.stored_words for frame in reversed(initial) if frame.stored_words is not None)
    continuation = list(scenarios.functional_frames(final, resume=True, capacity_bytes=capacity_bytes))
    frames = initial + continuation
    result = write_bench(directory, frames)
    outcomes = []
    for label, parameter, must_pass in (("positive", None, True),
            ("swapped-model-read-bank", "SWAP_READ_BANKS", False),
            ("swapped-model-write-bank", "SWAP_WRITE_BANKS", False)):
        binary = directory / f"{label}.vvp"
        command = [compiler, "-g2012", "-I", str(source_root / "rtl"), "-s", "cache_frames", "-o", str(binary)]
        if parameter:
            command += [f"-Pcache_frames.{parameter}=1"]
        command += [str(source_root / "rtl/shama_memory_adapter.sv"), str(directory / "cache_frames.sv")]
        compiled = subprocess.run(command, capture_output=True, text=True, timeout=30, cwd=directory)
        (directory / f"{label}-compile.log").write_text(compiled.stdout + compiled.stderr)
        check(compiled.returncode == 0, f"{label} did not compile: {compiled.stderr}")
        run = subprocess.run([simulator, str(binary)], capture_output=True, text=True, timeout=30, cwd=directory)
        log = run.stdout + run.stderr
        (directory / f"{label}.log").write_text(log)
        check((run.returncode == 0) == must_pass, f"unexpected {label} result: {log}")
        if must_pass:
            check("CACHE FRAME ORACLE PASS" in log, "positive simulation omitted its completion witness")
        else:
            expected_marker = "FRAME" if parameter == "SWAP_READ_BANKS" else "STORAGE"
            check(expected_marker in log, f"negative fixture failed for an unrelated reason: {log}")
        outcomes.append({"case": label, "exit_code": run.returncode, "output": log.strip()})
    return result | {"capacity_bytes": capacity_bytes,
                     "negative_storage_address_xor": 1024 if capacity_bytes > 4096 else 1,
                     "rtl_source_hashes": {name: sha(source_root / name) for name in ("rtl/shama_memory_adapter.sv", "rtl/shama_boot_params.svh")},
                     "initial_plan": {key: value for key, value in scenarios.summarize_frames(initial).items()
                                      if key not in ("final_words", "boundary_states")},
                     "continuation_frames": len(continuation), "outcomes": outcomes}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    directory = args.output.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    paths = ["experiments/memory_controller_runtime/fixture.py", "experiments/memory_controller_runtime/geometry.py",
             "experiments/memory_controller_runtime/scenarios.py", "rtl/shama_memory_adapter.sv",
             "rtl/shama_boot_params.svh", "src/shamaos/system_build.py",
             "src/shamaos/hardware/memory_backbone.py"]
    before = {name: sha(ROOT / name) for name in paths}
    report = {"schema": "independent-cache-frame-review-v1",
        "scope": "Complete ABI and generated expectation review against actual RTL plus abstract level-sensitive storage; native geometry/clock/reset races and fresh-process world replay remain unverified",
        "source_hashes": before, "auditor_sha256": sha(__file__),
        "interconnect": interconnect_review(), "rtl_oracle": rtl_review(directory)}
    check(before == {name: sha(ROOT / name) for name in paths}, "reviewed sources changed during execution")
    report["status"] = "independent-interface-and-frame-oracle-passed"
    (directory / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
