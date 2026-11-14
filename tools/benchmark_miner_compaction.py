"""Measure shipped Miner code and reference work against a retained baseline.

This measures assembled executable bytes and reference instruction steps, not
physical Minecraft footprint or hardware/wall-clock execution time.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from shamaos.assembler import _int, assemble, parse_source
from shamaos.isa import SYSCALLS
from shamaos.memory_map import MINER_RECORD
from test_miner_firmware import MinerMachine

BASELINE = "184faa9d28d871b77ff4de73c022b620d57536a7"
SOURCE_PATH = "src/shamaos/firmware/miner.asm"


def measure(source: str) -> dict:
    assembled = assemble(source)
    parsed, definitions = parse_source(source)
    counts = Counter(line.mnemonic.upper() for line in parsed if line.mnemonic)
    controls = sum(value for name, value in counts.items() if name in ("JMP", "CALL") or name.startswith("BR."))
    immediates = 0
    for line in parsed:
        if line.mnemonic and line.mnemonic.upper() in ("LDI", "ADDI", "SUBI", "ANDI", "ORI", "XORI"):
            value = (_int(line.args[1], SYSCALLS | definitions) + 2**31) % 2**32 - 2**31
            immediates += -(2**19) <= value < 2**19

    machine = MinerMachine()
    machine.load_program(assembled.words)
    machine.labels = assembled.labels
    steps = {}
    start = machine.cycle_counter
    machine.settle()
    steps["first_boot_to_idle"] = machine.cycle_counter - start
    for event, label in ((0x11, "live_redraw"), (0x12, "empty_history")):
        start = machine.cycle_counter
        machine.event(event)
        steps[label] = machine.cycle_counter - start
    start = machine.cycle_counter
    machine.type("C")
    steps["compare_command"] = machine.cycle_counter - start
    steps["native_hash_measured"] = machine.word(MINER_RECORD + 112)
    steps["software_hash_measured"] = machine.word(MINER_RECORD + 116)
    assert machine.word(MINER_RECORD + 236) == 1, "native/software digest mismatch"
    binary = assembled.to_bytes()
    return {
        "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "executable_sha256": hashlib.sha256(binary).hexdigest(),
        "executable_bytes": len(binary),
        "control_and_render_prefix_bytes": assembled.labels[".SOFT_CONSTANTS"] * 4,
        "legacy_control_transfers": controls,
        "legacy_signed20_immediates": immediates,
        "projected_bytes_if_eligible_legacy_forms_shorten": len(binary) - 4 * (controls + immediates),
        "reference_instruction_steps": steps,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default=BASELINE)
    parser.add_argument("--output", type=Path, default=ROOT / "build/miner-compaction.json")
    parser.add_argument("--test-report", type=Path, help="Optional pytest JUnit XML to retain the verification totals.")
    args = parser.parse_args()
    baseline = subprocess.check_output(["git", "show", f"{args.baseline}:{SOURCE_PATH}"], cwd=ROOT, text=True)
    report = {
        "scope": "Source compaction; exact executable bytes and model steps. Short-form sizes are projections only.",
        "baseline_commit": args.baseline,
        "source_path": SOURCE_PATH,
        "results": {"baseline": measure(baseline), "candidate": measure((ROOT / SOURCE_PATH).read_text())},
    }
    if args.test_report:
        suites = list(ElementTree.parse(args.test_report).getroot().iter("testsuite"))
        report["verification"] = {
            key: sum(int(suite.get(key, "0")) for suite in suites)
            for key in ("tests", "failures", "errors", "skipped")
        }
        report["verification"]["command"] = (
            "PYTHONPATH=src:tests python -m pytest -q tests/test_miner_firmware.py "
            "tests/test_miner_rendering.py "
            "tests/test_cpu_rtl.py::test_shipped_software_double_sha_runs_on_cpu_rtl_and_matches_native "
            "--junitxml=build/miner-source-compaction-tests.xml"
        )
        report["verification"]["requires"] = "iverilog and vvp on PATH for CPU RTL verification."
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
