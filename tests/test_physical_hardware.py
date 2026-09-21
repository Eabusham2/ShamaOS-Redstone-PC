from itertools import islice

from shamaos.hardware.display import LampPanelSpec, iter_lamp_panel, panel_ports
from shamaos.hardware.memory import MemoryBankSpec, iter_memory_bank
from shamaos.model import Vec3


def test_small_memory_bank_contains_locked_preloaded_bits():
    spec = MemoryBankSpec(words=2, word_bits=8, bit_pitch=8, word_pitch=6)
    blocks = list(iter_memory_bank(Vec3(0, 10, 0), spec=spec, initial=b"\x01\x80"))
    repeaters = [p for p in blocks if p.block.name == "minecraft:repeater"]
    assert repeaters
    assert any(dict(p.block.properties).get("locked") == "true" for p in repeaters)
    assert any(dict(p.block.properties).get("powered") == "true" for p in repeaters)


def test_small_panel_has_one_lamp_per_pixel():
    spec = LampPanelSpec(width=8, height=4)
    blocks = list(iter_lamp_panel(Vec3(0, 0, 0), spec=spec, backing=False))
    lamps = [p for p in blocks if p.block.name == "minecraft:redstone_lamp"]
    assert len(lamps) == 32


def test_large_generators_are_streaming_iterators():
    gen = iter_memory_bank(
        Vec3(0, 0, 0),
        spec=MemoryBankSpec(words=1024, word_bits=32),
    )
    first = list(islice(gen, 100))
    assert len(first) == 100


def test_locked_panel_geometry_is_horizontal_and_fully_addressable():
    spec = LampPanelSpec(width=320, height=180, pixel_pitch_x=5, pixel_pitch_y=5)
    origin = Vec3(0, 64, 0)
    ports = panel_ports(origin, spec)

    assert len(ports.data_in) == 320
    assert len(ports.row_select) == 180
    assert all(p.y == 65 for p in ports.data_in)
    assert all(p.y == 62 for p in ports.row_select)
    assert spec.physical_width == 1600
    assert spec.physical_height == 900
    assert spec.depth == 6


def test_small_horizontal_panel_stays_thin_in_y():
    spec = LampPanelSpec(width=8, height=4)
    blocks = list(iter_lamp_panel(Vec3(0, 64, 0), spec=spec, backing=False))
    ys = [p.pos.y for p in blocks]
    assert max(ys) - min(ys) <= 6
