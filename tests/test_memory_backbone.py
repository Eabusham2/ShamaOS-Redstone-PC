from itertools import islice

from shamaos.hardware.memory import MemoryBankSpec, MemoryFabricSpec
from shamaos.hardware.memory_backbone import backbone_ports, iter_memory_backbone
from shamaos.model import Vec3


def test_memory_backbone_ports_match_fabric_geometry():
    bank = MemoryBankSpec(words=4, word_bits=8, bit_pitch=8, word_pitch=6)
    fabric = MemoryFabricSpec(
        total_bytes=8,
        bank=bank,
        banks_per_row=2,
        bank_gap_x=192,
        bank_gap_z=128,
    )
    origin = Vec3(1000, 64, 1000)
    ports = backbone_ports(origin, fabric)

    assert len(ports.bank_select) == 2
    assert len(ports.row_select) == 4
    assert len(ports.write_data) == 8
    assert len(ports.selected_word) == 8


def test_memory_backbone_small_fabric_emits_real_redstone():
    bank = MemoryBankSpec(words=4, word_bits=8, bit_pitch=8, word_pitch=6)
    fabric = MemoryFabricSpec(
        total_bytes=8,
        bank=bank,
        banks_per_row=2,
        bank_gap_x=192,
        bank_gap_z=128,
    )
    placements = list(
        iter_memory_backbone(
            Vec3(1000, 64, 1000),
            fabric=fabric,
            component="test-backbone",
        )
    )
    assert placements
    names = {p.block.name for p in placements}
    assert "minecraft:redstone_wire" in names
    assert "minecraft:repeater" in names
    assert any("torch" in name for name in names)


def test_default_scale_backbone_is_streaming():
    bank = MemoryBankSpec(words=1024, word_bits=32)
    fabric = MemoryFabricSpec(
        total_bytes=4 << 20,
        bank=bank,
        banks_per_row=16,
    )
    gen = iter_memory_backbone(Vec3(0, 64, 0), fabric=fabric)
    assert len(list(islice(gen, 128))) == 128
