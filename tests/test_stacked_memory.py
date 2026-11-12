"""Experimental layered tile: actual blocks, local decode and bank isolation."""
import pytest

from shamaos.hardware.memory import MemoryBankSpec
from shamaos.hardware.memory_backbone import (
    StackedMemoryTileSpec, iter_stacked_memory_tile, stacked_tile_ports,
)
from shamaos.model import Vec3
from test_mchprs_runtime import run_case, west_driver


def test_stacked_tile_preserves_complete_bank_capacity_and_height():
    assert StackedMemoryTileSpec(banks=16).bytes == 16 * 4096
    with pytest.raises(ValueError, match="build height"):
        next(iter_stacked_memory_tile(Vec3(256, 80, 128), tile=StackedMemoryTileSpec(banks=16)))
    with pytest.raises(ValueError, match="four-block"):
        StackedMemoryTileSpec(layer_pitch=15)


def test_two_layer_memory_has_no_conflicting_placements():
    origin = Vec3(256, 32, 128)
    tile = StackedMemoryTileSpec(bank=MemoryBankSpec(words=4, word_bits=8), banks=2)
    seen = {}
    for placement in iter_stacked_memory_tile(origin, tile=tile):
        previous = seen.setdefault(placement.pos, placement.block)
        assert previous == placement.block, (placement.pos, previous, placement.block)


def test_actual_two_layer_local_decoder_preload_read_write_and_isolation():
    origin = Vec3(256, 32, 128)
    spec = MemoryBankSpec(words=4, word_bits=8)
    tile = StackedMemoryTileSpec(bank=spec, banks=2)
    pins = stacked_tile_ports(origin, tile)
    inputs = {f"D{i}": west_driver(p) for i, p in enumerate(pins.write_data)}
    inputs |= {f"ROW{i}": west_driver(p) for i, p in enumerate(pins.row_select)}
    inputs |= {f"BANK{i}": west_driver(p) for i, p in enumerate(pins.bank_select)}
    inputs |= {"READ": west_driver(pins.read_enable), "WRITE": west_driver(pins.write_enable)}
    outputs = {f"Q{bank}:{i}": p for bank, points in enumerate(pins.bank_read_data) for i, p in enumerate(points)}
    outputs |= {f"S{bank}:{word}:{bit}": origin.offset(bit * spec.bit_pitch + 2, spec.cell_y + bank * tile.layer_pitch, 3 + word * spec.word_pitch)
                for bank in range(2) for word in range(4) for bit in range(8)}
    current = [[0xA5, 0x12, 0x80, 0xFF], [0x5A, 0xED, 0x01, 0]]
    initial = bytes(value for bank in current for value in bank)

    def stored():
        return {f"S{bank}:{word}:{bit}": bool(value >> bit & 1)
                for bank, words in enumerate(current) for word, value in enumerate(words) for bit in range(8)}

    def read_value(bank, value):
        return {f"Q{other}:{bit}": bool(value >> bit & 1) if other == bank else False
                for other in range(2) for bit in range(8)}

    steps = [{"ticks": 128, "expect": stored()}]
    for bank in range(2):
        for word in range(4):
            steps += [
                {"drive": {f"BANK{bank}": True, f"ROW{word}": True, "READ": True}, "ticks": 128, "expect": stored() | read_value(bank, current[bank][word])},
                {"drive": {f"BANK{bank}": False, f"ROW{word}": False, "READ": False}, "ticks": 128, "expect": stored() | read_value(bank, 0)},
            ]
    for bank, word, value in ((0, 1, 0x69), (1, 3, 0x96), (0, 0, 0), (1, 2, 0xFF)):
        steps.append({"drive": {f"D{i}": bool(value >> i & 1) for i in range(8)}, "ticks": 128, "expect": stored(), "continuous": True})
        current[bank][word] = value
        steps += [
            {"drive": {f"BANK{bank}": True, f"ROW{word}": True, "WRITE": True}, "ticks": 128, "expect": stored()},
            {"drive": {"WRITE": False, "READ": True}, "ticks": 128, "expect": stored() | read_value(bank, value)},
            {"drive": {f"BANK{bank}": False, f"ROW{word}": False, "READ": False}, "ticks": 128, "expect": stored()},
        ]
    run_case(iter_stacked_memory_tile(origin, tile=tile, initial=initial), inputs, outputs, steps, initial_ticks=128)
