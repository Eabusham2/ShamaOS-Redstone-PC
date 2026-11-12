"""The literal 4 KiB bank and its full 6,144-block data/read buses."""
import json
import os
from pathlib import Path
import subprocess
import time

import pytest

from shamaos.generator import source_provenance
from shamaos.hardware.memory import MemoryBankSpec, bank_ports
from shamaos.hardware.memory_streaming import iter_memory_bank_ordered
from shamaos.model import Vec3


def run_full_bank_case(work_path: Path, runner: str) -> dict:
    """Stream the case to disk so Python never holds two million Placements."""
    work_path.mkdir(parents=True, exist_ok=True)
    case_path = work_path / "case.json"
    origin = Vec3(32, 16, 32)
    bank = MemoryBankSpec(words=1024, word_bits=32)
    pins = bank_ports(origin, bank)
    values = [((word + 1) * 0x9E3779B9 ^ 0xA53C96F0) & 0xFFFFFFFF for word in range(bank.words)]
    image = b"".join(value.to_bytes(4, "little") for value in values)
    coord = lambda point: [point.x, point.y, point.z]
    driver = lambda point: coord(point.offset(-1, 0, -1))
    inputs = {f"D{bit}": driver(point) for bit, point in enumerate(pins.data_in)}
    inputs |= {f"W{word}": driver(pins.write_select[word]) for word in (0, 512, 1023)}
    inputs |= {f"R{word}": driver(pins.read_select[word]) for word in (0, 512, 1023)}
    outputs = {f"Q{bit}": coord(point) for bit, point in enumerate(pins.data_out)}
    outputs |= {f"S{word}:{bit}": coord(origin.offset(bit * bank.bit_pitch + 2, bank.cell_y, 3 + word * bank.word_pitch))
                for word in range(bank.words) for bit in range(32)}

    def stored():
        return {f"S{word}:{bit}": bool(value >> bit & 1) for word, value in enumerate(values) for bit in range(32)}

    def selected(value):
        return {f"Q{bit}": bool(value >> bit & 1) for bit in range(32)}

    steps = [{"ticks": 4096, "expect": stored() | selected(0)}]
    for word, value in ((0, 0), (512, 0xFFFFFFFF), (1023, 0x6A95F00F)):
        steps.extend([
            {"drive": {f"R{word}": True}, "ticks": 4096, "expect": stored() | selected(values[word])},
            {"drive": {f"R{word}": False}, "ticks": 4096, "expect": stored() | selected(0)},
            {"drive": {f"D{bit}": bool(value >> bit & 1) for bit in range(32)},
             "ticks": 4096, "expect": stored()},
        ])
        values[word] = value
        steps.extend([
            {"drive": {f"W{word}": True}, "ticks": 4096, "expect": stored()},
            {"drive": {f"W{word}": False, f"R{word}": True}, "ticks": 4096, "expect": stored() | selected(value)},
            {"drive": {f"R{word}": False}, "ticks": 4096, "expect": stored() | selected(0)},
        ])
    payload = {"inputs": inputs, "outputs": outputs, "initial_ticks": 4096, "steps": steps}
    started = time.perf_counter()
    count = 0
    with case_path.open("w", encoding="utf-8") as handle:
        handle.write('{"blocks":[')
        for placement in iter_memory_bank_ordered(origin, spec=bank, initial=image):
            if count:
                handle.write(",")
            handle.write(json.dumps({"pos": coord(placement.pos), "name": placement.block.name,
                                     "properties": dict(placement.block.properties)}, separators=(",", ":")))
            count += 1
        handle.write("]")
        for key, value in payload.items():
            handle.write("," + json.dumps(key) + ":")
            json.dump(value, handle, separators=(",", ":"))
        handle.write("}")
    prepared = time.perf_counter()
    with case_path.open("r", encoding="utf-8") as handle:
        result = subprocess.run([runner], stdin=handle, capture_output=True, text=True, timeout=600)
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
    runtime = json.loads(result.stdout)
    runtime.pop("traces", None)
    assert runtime["status"] == "passed"
    assert count == 1_991_552
    report = {
        "kind": "literal-4KiB-memory-bank-runtime", "physical_placements": count,
        "stored_bits": 32768, "stored_bits_checked_after_each_step": 32768,
        "words": 1024, "word_bits": 32, "addresses_exercised": [0, 512, 1023],
        "settling_ticks_per_step": 4096, "runtime": runtime,
        "case_json_bytes": case_path.stat().st_size,
        "case_generation_seconds": prepared - started,
        "runtime_seconds": time.perf_counter() - prepared,
        "source": source_provenance(), "complete_machine_runtime_verified": False,
    }
    (work_path / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    report_directory = os.environ.get("SHAMA_VALIDATION_REPORT_DIR")
    if report_directory:
        destination = Path(report_directory)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "full-bank-runtime.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def test_actual_full_1024_by_32_bank_preload_first_middle_last_and_retention(tmp_path):
    runner = os.environ.get("SHAMA_MCHPRS_RUNNER")
    if not runner or not Path(runner).is_file():
        pytest.skip("actual MCHPRS runner not available; full bank runtime is unverified")
    report = run_full_bank_case(tmp_path, runner)
    assert report["runtime"]["steps"] == 19
    assert report["runtime"]["ticks"] == 81920
