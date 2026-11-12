"""Experimental layered tile: actual blocks, local decode and bank isolation."""
import pytest

from shamaos.hardware.memory import MemoryBankSpec
from shamaos.hardware.memory_backbone import (
    StackedMemoryTileSpec, iter_stacked_memory_tile, stacked_tile_ports,
)
from shamaos.model import Placement, Vec3
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
    outputs |= {f"OUT{i}": point for i, point in enumerate(pins.selected_word)}
    outputs |= {f"S{bank}:{word}:{bit}": origin.offset(bit * spec.bit_pitch + 2, spec.cell_y + bank * tile.layer_pitch, 3 + word * spec.word_pitch)
                for bank in range(2) for word in range(4) for bit in range(8)}
    current = [[0xA5, 0x12, 0x80, 0xFF], [0x5A, 0xED, 0x01, 0]]
    initial = bytes(value for bank in current for value in bank)

    def stored():
        return {f"S{bank}:{word}:{bit}": bool(value >> bit & 1)
                for bank, words in enumerate(current) for word, value in enumerate(words) for bit in range(8)}

    def read_value(bank, value):
        return ({f"Q{other}:{bit}": bool(value >> bit & 1) if other == bank else False
                for other in range(2) for bit in range(8)} |
                {f"OUT{bit}": bool(value >> bit & 1) for bit in range(8)})

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


@pytest.mark.parametrize("banks,pitch,base_y,word_bits", [(3, 12, 32, 32), (16, 16, 64, 8), (24, 16, -60, 8), (32, 12, -60, 8), (32, 12, -60, 32), (30, 12, -56, 32)])
def test_actual_shared_return_first_middle_last_bank_and_maximum_height(banks, pitch, base_y, word_bits):
    origin = Vec3(256, base_y, 128)
    spec = MemoryBankSpec(words=2, word_bits=word_bits)
    tile = StackedMemoryTileSpec(bank=spec, banks=banks, layer_pitch=pitch)
    pins = stacked_tile_ports(origin, tile)
    # TestWorld uses positive coordinates; translating the entire valid Java
    # -64..319 fixture preserves every relative physical connection.
    dy = 64 if base_y < 0 else 0
    shift = lambda p: p.offset(dy=dy)
    inputs = {f"D{i}": west_driver(shift(p)) for i, p in enumerate(pins.write_data)}
    inputs |= {f"ROW{i}": west_driver(shift(p)) for i, p in enumerate(pins.row_select)}
    inputs |= {f"BANK{i}": west_driver(shift(p)) for i, p in enumerate(pins.bank_select)}
    inputs |= {"READ": west_driver(shift(pins.read_enable)), "WRITE": west_driver(shift(pins.write_enable))}
    outputs = {f"OUT{i}": shift(p) for i, p in enumerate(pins.selected_word)}
    outputs |= {f"S{bank}:{word}:{bit}": shift(origin.offset(bit * spec.bit_pitch + 2, spec.cell_y + bank * pitch, 3 + word * spec.word_pitch))
                for bank in range(banks) for word in range(2) for bit in range(word_bits)}
    mask = (1 << word_bits) - 1
    current = [[((bank + 1) * 0x9E3779B9 ^ (word + 1) * 0xA55AC33C) & mask for word in range(2)] for bank in range(banks)]
    initial = b"".join(value.to_bytes(word_bits // 8, "little") for words in current for value in words)

    def stored():
        return {f"S{bank}:{word}:{bit}": bool(value >> bit & 1)
                for bank, words in enumerate(current) for word, value in enumerate(words) for bit in range(word_bits)}

    def value_bits(value):
        return {f"OUT{bit}": bool(value >> bit & 1) for bit in range(word_bits)}

    steps = [{"ticks": 512, "expect": stored() | value_bits(0)}]
    for bank in (0, banks // 2, banks - 1):
        for word in range(2):
            steps += [
                {"drive": {f"BANK{bank}": True, f"ROW{word}": True, "READ": True}, "ticks": 512, "expect": stored() | value_bits(current[bank][word])},
                {"drive": {f"BANK{bank}": False, f"ROW{word}": False, "READ": False}, "ticks": 512, "expect": stored() | value_bits(0)},
            ]
        value = (0xDEADBEEF ^ bank * 0x10204081) & mask
        steps.append({"drive": {f"D{i}": bool(value >> i & 1) for i in range(word_bits)}, "ticks": 512, "expect": stored(), "continuous": True})
        current[bank][1] = value
        steps += [
            {"drive": {f"BANK{bank}": True, "ROW1": True, "WRITE": True}, "ticks": 512, "expect": stored()},
            {"drive": {"WRITE": False, "READ": True}, "ticks": 512, "expect": stored() | value_bits(value)},
            {"drive": {f"BANK{bank}": False, "ROW1": False, "READ": False}, "ticks": 512, "expect": stored() | value_bits(0)},
        ]
    placements = list(iter_stacked_memory_tile(origin, tile=tile, initial=initial))
    assert min(p.pos.y for p in placements) >= -64
    assert max(p.pos.y for p in placements) <= 319
    run_case((Placement(shift(p.pos), p.block, p.component) for p in placements), inputs, outputs, steps, initial_ticks=512)
