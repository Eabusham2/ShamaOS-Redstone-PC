"""Exact partial-bank capacity, real decoding, retention and saved-world proof."""
from itertools import chain
import json
import os
from pathlib import Path
import subprocess

import pytest

from shamaos.generator import _memory_sections, source_provenance, write_placement_stages
from shamaos.hardware.memory import MemoryBankSpec, MemoryFabricSpec
from shamaos.hardware.memory_backbone import StackedMemoryFabricSpec, fabric_tile_ports, iter_memory_backbone
from shamaos.model import Placement, Vec3
from test_mchprs_runtime import run_case, west_driver


def partial_case(*, words, tail_words, banks, layers, word_bits=32):
    origin = Vec3(512, -56, 256)
    bank = MemoryBankSpec(words=words, word_bits=word_bits)
    fabric = StackedMemoryFabricSpec((banks - 1) * bank.bytes + tail_words * word_bits // 8,
                                    bank, banks_per_row=4, layers=layers, binary_rows=True)
    mask = (1 << word_bits) - 1
    values = [[((index + 1) * 0x9E3779B9 ^ row * 0x10204081) & mask
               for row in range(fabric.bank_spec(index).words)] for index in range(banks)]
    initial = b"".join(value.to_bytes(word_bits // 8, "little") for rows in values for value in rows)
    sections = tuple(_memory_sections(name="partial", origin=origin, fabric=fabric, initial=initial))
    assert sum(section.metadata["bytes"] for section in sections) == len(initial) == fabric.total_bytes
    assert sum(section.metadata["banks"] for section in sections) == banks
    ports = fabric_tile_ports(origin, fabric)
    inputs, outputs = {}, {}
    address_bits = max(1, (words - 1).bit_length())
    for group, tile in enumerate(ports):
        assert len(tile.row_address) == address_bits and not tile.row_select
        for field, points in (("A", tile.row_address), ("B", tile.bank_select), ("D", tile.write_data)):
            inputs.update({f"{group}:{field}{bit}": point.offset(dz=-2) for bit, point in enumerate(points)})
        inputs[f"{group}:READ"] = west_driver(tile.read_enable)
        inputs[f"{group}:WRITE"] = tile.write_enable.offset(dz=-2)
        outputs.update({f"{group}:OUT{bit}": point for bit, point in enumerate(tile.selected_word)})
    for index in range(banks):
        base = fabric.bank_origin(origin, index)
        outputs.update({f"S{index}:{row}:{bit}": base.offset(bit * bank.bit_pitch + 2, bank.cell_y, 3 + row * bank.word_pitch)
                        for row in range(fabric.bank_spec(index).words) for bit in range(word_bits)})

    def stored():
        return {f"S{index}:{row}:{bit}": bool(value >> bit & 1)
                for index, rows in enumerate(values) for row, value in enumerate(rows) for bit in range(word_bits)}

    def read(group=None, value=0):
        return {f"{index}:OUT{bit}": bool(value >> bit & 1) if index == group else False
                for index in range(len(ports)) for bit in range(word_bits)}

    def address(row):
        return {f"{index}:A{bit}": bool(row >> bit & 1) for index in range(len(ports)) for bit in range(address_bits)}

    def enable(field, value):
        return {f"{index}:{field}": value for index in range(len(ports))}

    ticks = 4096 if words > 8 else 512
    steps = [{"ticks": ticks, "expect": stored() | read()}]
    for index, rows in enumerate(values):
        group, layer = divmod(index, layers)
        tested_rows = range(len(rows)) if words <= 8 else sorted({0, len(rows) // 2, len(rows) - 1})
        for row in tested_rows:
            steps.extend([
                {"drive": address(row), "ticks": ticks, "expect": stored() | read()},
                {"drive": {f"{group}:B{layer}": True} | enable("READ", True),
                 "ticks": ticks, "expect": stored() | read(group, rows[row])},
                {"drive": {f"{group}:B{layer}": False} | enable("READ", False),
                 "ticks": ticks, "expect": stored() | read()},
            ])
    for index, row, value in ((0, 0, 0), (banks - 1, tail_words - 1, mask), (banks - 1, 0, mask ^ 0x69)):
        group, layer = divmod(index, layers)
        steps.append({"drive": address(row) | {f"{tile}:D{bit}": bool(value >> bit & 1)
                                               for tile in range(len(ports)) for bit in range(word_bits)},
                      "ticks": ticks, "expect": stored() | read()})
        values[index][row] = value
        steps.extend([
            {"drive": {f"{group}:B{layer}": True} | enable("WRITE", True), "ticks": ticks, "expect": stored() | read()},
            {"drive": enable("WRITE", False) | enable("READ", True), "ticks": ticks, "expect": stored() | read(group, value)},
            {"drive": {f"{group}:B{layer}": False} | enable("READ", False), "ticks": ticks, "expect": stored() | read()},
        ])
    # A selected short bank has no physical cells in the missing suffix.
    # Keep its upper address bit active, so the suffix cannot wrap to row0.
    group, layer = divmod(banks - 1, layers)
    invalid_rows = range(tail_words, words) if words <= 8 else (tail_words, words - 1)
    for row in invalid_rows:
        steps.extend([
            {"drive": address(row), "ticks": ticks, "expect": stored() | read()},
            {"drive": {f"{group}:B{layer}": True} | enable("READ", True), "ticks": ticks, "expect": stored() | read()},
            {"drive": enable("WRITE", True), "ticks": ticks, "expect": stored() | read()},
            {"drive": {f"{group}:B{layer}": False} | enable("READ", False) | enable("WRITE", False),
             "ticks": ticks, "expect": stored() | read()},
        ])
    blocks = chain(chain.from_iterable(section.factory() for section in sections), iter_memory_backbone(origin, fabric=fabric))
    return blocks, inputs, outputs, steps, fabric


@pytest.mark.parametrize("banks,layers,saved", [(3, 3, False), (3, 2, False), (1, 1, True)])
def test_partial_bank_has_exact_cells_and_cannot_alias_missing_rows(banks, layers, saved, tmp_path):
    blocks, inputs, outputs, steps, fabric = partial_case(words=8, tail_words=4, banks=banks, layers=layers)
    blocks = list(blocks)
    occupied = {block.pos for block in blocks}
    last_origin = fabric.bank_origin(Vec3(512, -56, 256), banks - 1)
    assert all(last_origin.offset(bit * fabric.bank.bit_pitch + 2, fabric.bank.cell_y, 3 + row * fabric.bank.word_pitch) not in occupied
               for row in range(4, 8) for bit in range(32)), "missing tail cells must not be padded into the world"
    if saved:
        from shamaos.worldio import NativeWorldReader
        destination = Path(os.environ.get("SHAMA_VALIDATION_REPORT_DIR", tmp_path)) / "partial-memory-world"
        _, _, validation = write_placement_stages(destination, [("partial-memory", blocks)], surface_y=-61,
                                                  spawn=Vec3(64, -59, 64))
        reader = NativeWorldReader(destination)
        positions = sorted({block.pos for block in blocks})
        blocks = [Placement(point, reader.get_block(point), "reopened-partial-memory") for point in positions]
        assert reader.get_block(Vec3(512, -61, 256)).name == "minecraft:grass_block"
        assert validation["status"] == "passed"
    shift = lambda point: point.offset(dy=64)
    runtime = run_case((Placement(shift(block.pos), block.block, block.component) for block in blocks),
                       {name: shift(point) for name, point in inputs.items()},
                       {name: shift(point) for name, point in outputs.items()}, steps, initial_ticks=512)
    assert runtime["status"] == "passed"
    assert fabric.bank_count == banks
    assert fabric.bank_spec(banks - 1).bytes == 16


def test_exact_250_kib_flash_shape_and_invalid_partial_word():
    fabric = MemoryFabricSpec(250 * 1024)
    assert fabric.bank_count == 63
    assert [fabric.bank_spec(index).words for index in range(63)] == [1024] * 62 + [512]
    assert sum(fabric.bank_spec(index).bytes for index in range(63)) == 256000
    with pytest.raises(ValueError, match="whole number of words"):
        _ = MemoryFabricSpec(256001).bank_count
    with pytest.raises(IndexError):
        fabric.bank_spec(63)
    with pytest.raises(ValueError, match="exact fabric capacity"):
        tuple(_memory_sections(name="oversize", origin=Vec3(0, 8, 0), fabric=fabric, initial=bytes(256001)))


def test_actual_full_length_partial_bank_return_and_high_row_isolation(tmp_path):
    runner = os.environ.get("SHAMA_MCHPRS_RUNNER")
    if not runner or not Path(runner).is_file():
        pytest.skip("actual MCHPRS runner unavailable; partial bank return is unverified")
    # Full 6,144-block buses and 3,072-block tail extension; the separate
    # 32-bit fixtures exercise every data lane at short and half-bank length.
    blocks, inputs, outputs, steps, fabric = partial_case(words=1024, tail_words=512, banks=2, layers=2, word_bits=8)
    coord = lambda point: [point.x, point.y + 64, point.z]
    path = tmp_path / "partial-full-length.json"
    count, seen, states = 0, {}, {}
    with path.open("w") as handle:
        handle.write('{"blocks":[')
        for block in blocks:
            point = coord(block.pos)
            key = point[0] << 40 | point[1] << 24 | point[2]
            state = states.setdefault(block.block, len(states))
            assert seen.setdefault(key, state) == state, f"physical conflict at {block.pos}"
            if count:
                handle.write(",")
            handle.write(json.dumps({"pos": point, "name": block.block.name, "properties": dict(block.block.properties)}, separators=(",", ":")))
            count += 1
        handle.write("]")
        for key, value in {"inputs": {name: coord(point) for name, point in inputs.items()},
                           "outputs": {name: coord(point) for name, point in outputs.items()},
                           "steps": steps, "initial_ticks": 4096}.items():
            handle.write("," + json.dumps(key) + ":")
            json.dump(value, handle, separators=(",", ":"))
        handle.write("}")
    unique = len(seen)
    del seen
    with path.open() as handle:
        result = subprocess.run([runner], stdin=handle, capture_output=True, text=True, timeout=600)
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
    runtime = json.loads(result.stdout)
    runtime.pop("traces", None)
    assert runtime["status"] == "passed"
    report = {"kind": "full-length-partial-bank-stack-runtime", "bank_words": [1024, 512], "word_bits": 8,
              "physical_bytes": fabric.total_bytes, "emitted_placements": count, "unique_blocks": unique,
              "tail_return_extension_blocks": 3072, "stored_bits_checked_after_each_step": 12288,
              "runtime": runtime, "source": source_provenance(), "complete_machine_runtime_verified": False}
    destination = Path(os.environ.get("SHAMA_VALIDATION_REPORT_DIR", tmp_path))
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "partial-full-length-runtime.json").write_text(json.dumps(report, indent=2) + "\n")
