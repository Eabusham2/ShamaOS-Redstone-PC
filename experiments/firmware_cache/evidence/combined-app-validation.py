"""Retain independent app measurements and execute SHA at its shipped addresses.

Run the pytest command recorded in combined-app-validation.json first, retaining
combined-app-validation.before.json before that run. This script checks those
input hashes, the pytest totals, all app startups and the original Miner layout.
The CPU bench has a unified test bus; physical cache/RAM routing is a separate
SoC validation and is not claimed by this report.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]
from firmware_machine import FirmwareMachine
from test_miner_firmware import MinerMachine
from shamaos.assembler import assemble
from shamaos.machine_spec import CACHE_BYTES
from shamaos.memory_map import APP_BACKING_BYTES

FOLDER = Path(__file__).resolve().parent
PREFIX = "combined-app-validation"
APPS = ("desktop", "editor", "files", "miner", "monitor", "terminal", "calculator", "paint", "settings")
PYTEST_COMMAND = (
    "PYTHONPATH=src:tests python -m pytest -q tests/test_firmware_execution.py "
    "tests/test_firmware_cache_compaction.py tests/test_calculator_firmware.py "
    "tests/test_miner_firmware.py tests/test_miner_rendering.py "
    "tests/test_cpu_rtl.py::test_shipped_software_double_sha_runs_on_cpu_rtl_and_matches_native "
    "tests/test_hardware_assembler.py::test_shipped_source_emits_exact_host_binary "
    "--junitxml=experiments/firmware_cache/evidence/combined-app-validation.xml"
)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    expected_hashes = json.loads((FOLDER / f"{PREFIX}.before.json").read_text())
    actual_hashes = {path: digest(ROOT / path) for path in expected_hashes}
    assert actual_hashes == expected_hashes, "validation inputs changed during the test batch"
    suites = list(ElementTree.parse(FOLDER / f"{PREFIX}.xml").getroot().iter("testsuite"))
    totals = {key: sum(int(suite.get(key, "0")) for suite in suites)
              for key in ("tests", "errors", "failures", "skipped")}
    assert totals["tests"] > 0 and not sum(totals[key] for key in ("errors", "failures", "skipped"))
    tested = {case.get("name") for suite in suites for case in suite.iter("testcase")}
    for name in APPS:
        assert f"test_shipped_source_emits_exact_host_binary[{name}.asm]" in tested

    apps = {}
    for name in APPS:
        path = ROOT / "src/shamaos/firmware" / f"{name}.asm"
        program = assemble(path.read_text())
        machine = MinerMachine() if name == "miner" else FirmwareMachine(name)
        machine.settle()
        assert machine.idle_count == 1 and not machine.halted
        assert not machine.call_stack and not machine.data_stack
        apps[name] = {
            "source_sha256": digest(path),
            "executable_sha256": hashlib.sha256(program.to_bytes()).hexdigest(),
            "executable_bytes": len(program.to_bytes()),
            "cached_bytes": min(CACHE_BYTES, len(program.to_bytes())),
            "ram_tail_bytes": max(0, len(program.to_bytes()) - CACHE_BYTES),
            "startup_instruction_steps": machine.cycle_counter,
            "idle_reached_and_stacks_balanced": True,
        }

    miner = assemble((ROOT / "src/shamaos/firmware/miner.asm").read_text())
    boundaries = {label: miner.labels[label] * 4 for label in (
        ".SOFT_CONSTANTS", ".SOFT_DSHA256", ".SOFT_SHA256", ".SOFT_NEXT_BLOCK",
        ".SOFT_INITIAL_WORDS", ".SOFT_SCHEDULE", ".SOFT_ROUND", ".SOFT_DIGEST_WORDS",
    )}
    assert boundaries[".SOFT_CONSTANTS"] < CACHE_BYTES < len(miner.to_bytes()) <= APP_BACKING_BYTES
    # Replace only the unused app entry with a test bootstrap. The SHA library
    # and all of its absolute call/branch targets retain their shipped addresses.
    bootstrap = assemble("\n".join([
        f"CALLS {miner.labels['.SOFT_CONSTANTS']}",
        "LDIS r1 0x10000", "LDIS r3 0x10300",
        *(f"LDIS r{i} {i * 12345}" for i in range(4, 16)),
        f"CALLS {miner.labels['.SOFT_DSHA256']}",
        "LDIS r3 0x10400", "DSHA256 r3 r1", "HLT",
    ]) + "\n")
    words = [*miner.words]
    words[:len(bootstrap.words)] = bootstrap.words
    assert len(bootstrap.to_bytes()) < boundaries[".SOFT_CONSTANTS"]
    assert tuple(words[miner.labels[".SOFT_CONSTANTS"]:]) == miner.words[miner.labels[".SOFT_CONSTANTS"]:]
    build = ROOT / "build/miner-original-layout-validation"
    build.mkdir(parents=True, exist_ok=True)
    data = bytes((i * 157 + 81) & 255 for i in range(80))
    expected = hashlib.sha256(hashlib.sha256(data).digest()).digest()
    image = ["@00000000", *(f"{word:08x}" for word in words), "@00004000",
             *(f"{int.from_bytes(data[i:i+4], 'little'):08x}" for i in range(0, 80, 4))]
    program_path = build / "miner.hex"
    program_path.write_text("\n".join(image) + "\n")
    bench = (ROOT / "rtl/tb_cpu.sv").read_text()
    insertion = f"""
    integer cached_fetches=0,ram_tail_fetches=0;
    always @(posedge clk) begin
        if(!rst && mem_ready && !mem_we && dut.state==dut.S_FETCH) begin
            if(mem_addr<{CACHE_BYTES}) cached_fetches<=cached_fetches+1;
            else if(mem_addr<{len(miner.to_bytes())}) ram_tail_fetches<=ram_tail_fetches+1;
        end
    end
"""
    assert bench.count("    string program_file;") == 1
    bench = bench.replace("    string program_file;", insertion + "    string program_file;")
    bench = bench.replace('        $display("COUNTERS %0d %0d",dut.cycle_counter,dut.retired_counter);',
                          '        $display("COUNTERS %0d %0d",dut.cycle_counter,dut.retired_counter);\n'
                          '        $display("FETCH_REGIONS %0d %0d",cached_fetches,ram_tail_fetches);')
    bench_path = build / "tb_cpu.sv"
    bench_path.write_text(bench)
    binary = build / "cpu.vvp"
    sources = [ROOT / "rtl" / name for name in (
        "sha256_compress.sv", "sha256_fixed64.sv", "bitcoin_dsha256.sv", "shama_cpu.sv")]
    compiled = subprocess.run(["iverilog", "-g2012", "-I", str(ROOT / "rtl"), "-s", "tb_cpu",
                               "-o", str(binary), *map(str, sources), str(bench_path)],
                              capture_output=True, text=True, timeout=60)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    tail_results = []
    for stall in (0, 3):
        run = subprocess.run(["vvp", str(binary), f"+program={program_path}",
                              "+dump_base=66304", "+dump_words=72", f"+stall={stall}"],
                             capture_output=True, text=True, timeout=60)
        (FOLDER / f"{PREFIX}.tail-stall-{stall}.txt").write_text(run.stdout + run.stderr)
        assert run.returncode == 0, run.stdout + run.stderr
        memory = {int(addr): int(value, 16) for addr, value in re.findall(r"^MEM (\d+) ([0-9a-f]+)$", run.stdout, re.M)}
        regs = {int(reg): int(value, 16) for reg, value in re.findall(r"^REG (\d+) ([0-9a-f]+)$", run.stdout, re.M)}
        assert re.search(r"^FAULT 00$", run.stdout, re.M)
        for base in (0x10300, 0x10400):
            assert b"".join(memory[base + i].to_bytes(4, "little") for i in range(0, 32, 4)) == expected
        assert regs[1] == 0x10000 and all(regs[i] == i * 12345 for i in range(4, 16))
        cached, tail = map(int, re.search(r"^FETCH_REGIONS (\d+) (\d+)$", run.stdout, re.M).groups())
        cycles, retired = map(int, re.search(r"^COUNTERS (\d+) (\d+)$", run.stdout, re.M).groups())
        assert cached > 0 and tail > 0
        tail_results.append({"memory_stall": stall, "prefix_address_opcode_fetches": cached,
                             "tail_address_opcode_fetches": tail, "cycles": cycles, "retired": retired,
                             "software_and_native_digest": expected.hex(), "fault": 0,
                             "preserved_registers_r4_through_r15": True})

    assert {path: digest(ROOT / path) for path in expected_hashes} == expected_hashes
    report = {
        "scope": "Independent combined firmware/assembler/CPU verification. Original-layout SHA uses a unified CPU test bus; physical cache/RAM interconnection is a separate SoC gate.",
        "physical_cache_bytes": CACHE_BYTES, "existing_executable_backing_bytes": APP_BACKING_BYTES,
        "validation_inputs_unchanged": True, "source_sha256": actual_hashes,
        "git_head_at_completion": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "pytest_command": PYTEST_COMMAND,
        "runtime_requirements": "iverilog and vvp on PATH; CPU and assembler RTL checks must not skip.",
        "pytest": totals, "all_nine_in_world_assembler_comparisons_passed": True,
        "apps": apps, "miner_label_byte_addresses": boundaries,
        "original_layout_sha": {"replaced_entry_bytes": len(bootstrap.to_bytes()),
                                 "library_bytes_unchanged": True, "bench_sha256": digest(bench_path),
                                 "cases": tail_results},
    }
    (FOLDER / f"{PREFIX}.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"pytest": totals, "miner_boundaries": boundaries, "original_layout_sha": tail_results}, indent=2))


if __name__ == "__main__":
    main()
