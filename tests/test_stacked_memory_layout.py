"""Capacity, complete placement bounds and controller-to-tile wiring."""
from itertools import chain

import pytest

from shamaos.generator import _memory_sections
from shamaos.hardware.memory import MemoryBankSpec
from shamaos.hardware.memory_backbone import (
    StackedMemoryFabricSpec, backbone_ports, fabric_tile_ports,
    iter_memory_backbone, iter_stacked_memory_tile, iter_stacked_tile_terminal_keepouts, stacked_tile_bounds,
)
from shamaos.model import Vec3
from shamaos.system_build import memory_fabric_nets


@pytest.mark.parametrize("banks,layers,columns", [(4, 32, 1), (8, 32, 1), (7, 3, 2), (35, 32, 1)])
def test_complete_tile_envelopes_include_return_tail_and_short_final_group(banks, layers, columns):
    spec = MemoryBankSpec(words=2, word_bits=8)
    fabric = StackedMemoryFabricSpec(banks * spec.bytes, spec, columns, layers=layers)
    origin = Vec3(256, -60, 128)
    box = fabric.bounds(origin)
    occupied = {}
    tile_boxes = []
    for group in range(fabric.group_count):
        base, tile = fabric.group_origin(origin, group), fabric.tile_spec(group)
        tile_box = stacked_tile_bounds(base, tile)
        assert all(not tile_box.intersects(other) for other in tile_boxes)
        tile_boxes.append(tile_box)
        for placement in iter_stacked_memory_tile(base, tile=tile):
            point = placement.pos
            assert box.min.x <= point.x <= box.max.x
            assert box.min.y <= point.y <= box.max.y
            assert box.min.z <= point.z <= box.max.z
            previous = occupied.setdefault(point, placement.block)
            assert previous == placement.block, (point, previous, placement.block)
        for keepout in iter_stacked_tile_terminal_keepouts(base, tile):
            assert occupied[keepout.pos] == keepout.block, (keepout.pos, occupied[keepout.pos], keepout.block)
    assert sum(fabric.tile_spec(group).banks for group in range(fabric.group_count)) == banks
    assert len({fabric.bank_origin(origin, index) for index in range(banks)}) == banks
    with pytest.raises(IndexError):
        fabric.bank_origin(origin, banks)


def test_preloaded_tile_sections_and_backbone_preserve_bank_order_without_padding():
    spec = MemoryBankSpec(words=2, word_bits=8)
    fabric = StackedMemoryFabricSpec(7 * spec.bytes, spec, 2, layers=3)
    origin = Vec3(256, 64, 128)
    initial = bytes((bank * 29 + row * 83) & 255 for bank in range(7) for row in range(2))
    sections = list(_memory_sections(name="test", origin=origin, fabric=fabric, initial=initial))
    assert [section.metadata["banks"] for section in sections] == [3, 3, 1]
    assert [section.metadata["first_bank_index"] for section in sections] == [0, 3, 6]
    assert sum(section.metadata["bytes"] for section in sections) == len(initial)
    occupied = {}
    for placement in chain(chain.from_iterable(section.factory() for section in sections),
                           iter_memory_backbone(origin, fabric=fabric)):
        previous = occupied.setdefault(placement.pos, placement.block)
        assert previous == placement.block
    for bank in range(7):
        base = fabric.bank_origin(origin, bank)
        for row in range(2):
            for bit in range(8):
                cell = occupied[base.offset(bit * spec.bit_pitch + 2, spec.cell_y, 3 + row * spec.word_pitch)]
                assert cell.name == "minecraft:repeater"
                assert dict(cell.properties)["powered"] == str(bool(initial[bank * 2 + row] >> bit & 1)).lower()


def test_controller_nets_broadcast_every_tile_and_merge_all_selected_words():
    spec = MemoryBankSpec(words=2, word_bits=8)
    fabric = StackedMemoryFabricSpec(7 * spec.bytes, spec, 2, layers=3)
    origin = Vec3(256, 64, 128)
    fields = {"bank_select": fabric.bank_count, "row_select": spec.words,
              "write_data": spec.word_bits, "read_enable": 1, "write_commit": 1}
    outputs = {f"ram_{field}": tuple(Vec3(16 + bit * 6, 80, -32 - field_id * 8) for bit in range(width))
               for field_id, (field, width) in enumerate(fields.items())}
    inputs = {"ram_selected_word": tuple(Vec3(16 + bit * 6, 80, -96) for bit in range(8))}
    nets = {net.name: net for net in memory_fabric_nets("ram", origin, fabric, inputs=inputs, outputs=outputs)}
    tiles = fabric_tile_ports(origin, fabric)
    assert [len(tile.bank_select) for tile in tiles] == [3, 3, 1]
    selectors = tuple(pin for tile in tiles for pin in tile.bank_select)
    for index, pin in enumerate(selectors):
        assert nets[f"ram_bank_select[{index}]"] .sinks == (pin,)
        assert nets[f"ram_bank_select[{index}]"] .source == outputs["ram_bank_select"][index]
    for field, width in (("row_select", 2), ("write_data", 8)):
        for bit in range(width):
            net = nets[f"ram_{field}[{bit}]"]
            assert not net.wired_or
            assert net.sinks == tuple(getattr(tile, field)[bit] for tile in tiles)
    for bit in range(8):
        net = nets[f"ram_selected_word[{bit}]"]
        assert net.wired_or
        assert net.source == inputs["ram_selected_word"][bit]
        assert net.sinks == tuple(tile.selected_word[bit] for tile in tiles)
    with pytest.raises(ValueError, match="multi-tile"):
        backbone_ports(origin, fabric)
