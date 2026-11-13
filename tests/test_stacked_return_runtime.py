"""Full-depth and maximum-height read-return settling, using literal blocks.

The two bounded fixtures separately cover complete 1,024-row, 32-bit banks
and a 30-bank column. Their stated settling intervals are local test phases,
not a claimed timing proof for the complete computer or its global clock.
"""
from itertools import chain
import json
import os
from pathlib import Path
import subprocess

import pytest

from shamaos.generator import _memory_sections, source_provenance
from shamaos.hardware.memory import MemoryBankSpec
from shamaos.hardware.memory_backbone import (
    StackedMemoryFabricSpec, fabric_tile_ports, iter_memory_backbone,
)
from shamaos.model import Vec3
from test_mchprs_runtime import west_driver


def _return_case(*, words, banks):
    origin = Vec3(512, -56, 256)
    bank = MemoryBankSpec(words=words, word_bits=32)
    fabric = StackedMemoryFabricSpec(
        banks * bank.bytes, bank, banks_per_row=1, layers=banks,
        layer_pitch=12, binary_rows=True,
    )
    pins, = fabric_tile_ports(origin, fabric)
    values = [[((0xA55AC33C ^ row * 0x9E3779B9) ^ (0xFFFFFFFF if index % 2 else 0)) & 0xFFFFFFFF
               for row in range(words)] for index in range(banks)]
    initial = b"".join(value.to_bytes(4, "little") for rows in values for value in rows)
    sections = tuple(_memory_sections(name="return", origin=origin, fabric=fabric, initial=initial))
    assert sum(section.metadata["bytes"] for section in sections) == banks * words * 4
    inputs = {f"A{bit}": point.offset(dz=-2) for bit, point in enumerate(pins.row_address)}
    inputs |= {f"B{index}": point.offset(dz=-2) for index, point in enumerate(pins.bank_select)}
    inputs |= {f"D{bit}": point.offset(dz=-2) for bit, point in enumerate(pins.write_data)}
    inputs |= {"READ": west_driver(pins.read_enable), "WRITE": pins.write_enable.offset(dz=-2)}
    outputs = {f"OUT{bit}": point for bit, point in enumerate(pins.selected_word)}
    outputs |= {f"S{index}:{row}:{bit}": fabric.bank_origin(origin, index).offset(
                    bit * bank.bit_pitch + 2, bank.cell_y, 3 + row * bank.word_pitch)
                for index in range(banks) for row in range(words) for bit in range(32)}

    def stored():
        return {f"S{index}:{row}:{bit}": bool(value >> bit & 1)
                for index, rows in enumerate(values) for row, value in enumerate(rows)
                for bit in range(32)}

    def selected(value):
        return {f"OUT{bit}": bool(value >> bit & 1) for bit in range(32)}

    def address(row):
        return {f"A{bit}": bool(row >> bit & 1) for bit in range(len(pins.row_address))}

    phase = 4096 if words == 1024 else 512
    retained = stored()
    steps = [{"ticks": phase, "expect": retained | selected(0)}]
    # Keep READ asserted and switch adjacent banks carrying complementary
    # words. No stale OR result may remain at the next stated sample boundary.
    for row in sorted({0, words // 2, words - 1}):
        steps.append({"drive": address(row), "ticks": phase, "expect": retained | selected(0)})
        previous = None
        for index in (*range(banks), *reversed(range(banks))):
            drive = {"READ": True, f"B{index}": True}
            if previous is not None and previous != index:
                drive[f"B{previous}"] = False
            expectation = retained | selected(values[index][row])
            steps.append({"drive": drive, "ticks": phase, "expect": expectation})
            steps.append({"ticks": 16, "expect": expectation, "continuous": True})
            previous = index
        steps.append({"drive": {"READ": False, f"B{previous}": False},
                      "ticks": phase, "expect": retained | selected(0)})
        steps.append({"ticks": 16, "expect": retained | selected(0), "continuous": True})
    # A changed read return must also preserve writes and every unselected bit.
    for index, row, value in ((0, 0, 0), (banks - 1, words - 1, 0xFFFFFFFF)):
        steps.append({"drive": address(row) | {f"D{bit}": bool(value >> bit & 1) for bit in range(32)},
                      "ticks": phase, "expect": retained | selected(0)})
        values[index][row] = value
        retained = stored()
        steps.extend([
            {"drive": {f"B{index}": True, "WRITE": True}, "ticks": phase,
             "expect": retained | selected(0)},
            {"drive": {"WRITE": False, "READ": True}, "ticks": phase,
             "expect": retained | selected(value)},
            {"drive": {"READ": False, f"B{index}": False}, "ticks": phase,
             "expect": retained | selected(0)},
            {"ticks": 16, "expect": retained | selected(0), "continuous": True},
        ])
    blocks = chain(chain.from_iterable(section.factory() for section in sections),
                   iter_memory_backbone(origin, fabric=fabric))
    return blocks, inputs, outputs, steps, phase, fabric


@pytest.mark.parametrize("words,banks", [(2, 30), (1024, 2)])
def test_actual_return_opposite_bank_transitions_clear_write_and_retention(words, banks, tmp_path):
    runner = os.environ.get("SHAMA_MCHPRS_RUNNER")
    if not runner or not Path(runner).is_file():
        pytest.skip("actual MCHPRS runner unavailable; stacked read-return timing is unverified")
    blocks, inputs, outputs, steps, phase, fabric = _return_case(words=words, banks=banks)
    coord = lambda point: [point.x, point.y + 64, point.z]
    path = tmp_path / "return-case.json"
    count, states, occupied = 0, {}, {}
    with path.open("w") as handle:
        handle.write('{"blocks":[')
        for block in blocks:
            point = coord(block.pos)
            # Packed integer positions keep full-depth collision detection
            # bounded without retaining millions of Placement objects.
            key = point[0] << 40 | point[1] << 24 | point[2]
            state = states.setdefault(block.block, len(states))
            assert occupied.setdefault(key, state) == state, f"physical conflict at {block.pos}"
            if count:
                handle.write(",")
            handle.write(json.dumps({"pos": point, "name": block.block.name,
                                     "properties": dict(block.block.properties)}, separators=(",", ":")))
            count += 1
        handle.write("]")
        for key, value in {"inputs": {name: coord(point) for name, point in inputs.items()},
                           "outputs": {name: coord(point) for name, point in outputs.items()},
                           "steps": steps, "initial_ticks": phase}.items():
            handle.write("," + json.dumps(key) + ":")
            json.dump(value, handle, separators=(",", ":"))
        handle.write("}")
    unique = len(occupied)
    del occupied
    with path.open() as handle:
        result = subprocess.run([runner], stdin=handle, capture_output=True, text=True, timeout=600)
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
    runtime = json.loads(result.stdout)
    runtime.pop("traces", None)
    assert runtime["status"] == "passed"
    report = {
        "kind": "stacked-read-return-settling-runtime", "words_per_bank": words,
        "banks": banks, "word_bits": 32, "physical_bytes": fabric.total_bytes,
        "emitted_placements": count, "unique_blocks": unique,
        "stored_bits_checked_after_each_step": words * banks * 32,
        "settling_ticks_per_phase": phase, "stable_ticks_after_sample": 16,
        "transitions": "adjacent complementary bank words, forward and reverse, while READ remains asserted",
        "runtime": runtime, "source": source_provenance(),
        "complete_machine_runtime_verified": False,
        "timing_scope": "bounded local fixture phases; not the production oscillator or global routing",
    }
    destination = Path(os.environ.get("SHAMA_VALIDATION_REPORT_DIR", tmp_path))
    destination.mkdir(parents=True, exist_ok=True)
    (destination / f"return-{banks}-banks-{words}-rows-runtime.json").write_text(json.dumps(report, indent=2) + "\n")
