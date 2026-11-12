"""The color pass may change supports, never occupied cells or circuit behavior."""
from itertools import product

import pytest

from shamaos.generator import geometry_from_config, load_config, source_provenance, write_placement_stages
from shamaos.hardware.appearance import (
    CONCRETE, CONTROL_COLORS, PALETTE, AppearancePlan, AppearanceRegion, appearance_for_system,
)
from shamaos.hardware.controls import (
    CONTROLLER_NAMES, KEYBOARD_OFFSET, control_actuators, iter_controls, keyboard_buttons,
)
from shamaos.hardware.display import LampPanelSpec, iter_lamp_panel, panel_bounds, panel_ports
from shamaos.hardware.logic import TEMPLATES
from shamaos.hardware.memory import SUPPORT, MemoryBankSpec, bank_ports, iter_memory_bank
from shamaos.layout import default_origins, memory_specs, vram_spec
from shamaos.model import BlockState, Box, Placement, Vec3
from shamaos.worldio import NativeWorldReader, canonical_block_state
from test_mchprs_runtime import (
    assert_compact_console_trace, compact_console_case, run_case, west_driver,
)


def region_for(placements, palette):
    points = [p.pos for p in placements]
    lo = Vec3(*(min(getattr(p, a) for p in points) for a in ("x", "y", "z")))
    hi = Vec3(*(max(getattr(p, a) for p in points) for a in ("x", "y", "z")))
    return AppearanceRegion(palette, Box(lo, hi), palette)


def saved_colored_blocks(tmp_path, blocks, appearance):
    world = tmp_path / "styled-circuit"
    count, _, report = write_placement_stages(world, [("circuit", blocks)], appearance=appearance)
    assert count == len(blocks)
    assert report["status"] == "passed"
    reader = NativeWorldReader(world)
    # Read the actual on-disk Java states before giving any blocks to MCHPRS.
    reopened = [Placement(p.pos, reader.get_block(p.pos), p.component) for p in blocks]
    assert reopened == list(appearance.placements(blocks))
    assert {p.pos for p in reopened} == {p.pos for p in blocks}
    assert [p for p in reopened if not p.block.name.endswith("_concrete")] == [
        p for p in blocks if p.block != SUPPORT and not p.block.name.endswith("_concrete")]
    return reopened


def test_palette_preserves_every_nonstone_state_and_duplicate_supports(tmp_path):
    origin = Vec3(32, 32, 32)
    blocks = list(TEMPLATES["DFF"].emitter(origin, "cpu"))
    support = next(p for p in blocks if p.block == SUPPORT)
    blocks.append(Placement(support.pos, SUPPORT, "shared-route"))
    blocks.extend(Placement(Vec3(80 + i, 32, 32), state, "all-concrete-colors")
                  for i, state in enumerate(CONCRETE.values()))
    blocks.extend(Placement(Vec3(80 + i, 31, 32), BlockState.of(name), "untouched")
                  for i, name in enumerate(("minecraft:bedrock", "minecraft:dirt", "minecraft:grass_block")))
    plan = AppearancePlan((region_for(blocks, "cpu"),))
    styled = list(plan.placements(blocks))
    assert len(styled) == len(blocks)
    for old, new in zip(blocks, styled):
        assert (old.pos, old.component) == (new.pos, new.component)
        assert new.block == plan.support_at(old.pos) if old.block == SUPPORT else new.block == old.block
    for color in CONCRETE.values():
        assert canonical_block_state(color) == color
    # Conflicting support names must not turn a valid shared coordinate into
    # two colors, whichever stage arrives first at the collision-checking writer.
    count, _, report = write_placement_stages(tmp_path / "duplicates", [("first", blocks),
        ("second", list(reversed(blocks)))], appearance=plan)
    assert count == 2 * len(blocks)
    assert report["status"] == "passed"
    assert "src/shamaos/hardware/appearance.py" in source_provenance()["files_sha256"]


def test_component_colors_follow_actual_stacked_heights_and_boundaries():
    geometry = geometry_from_config(load_config("configs/default.toml"))
    origins = default_origins(geometry)
    plan = appearance_for_system(geometry)
    no_console = AppearancePlan(plan.regions)
    cache, ram, flash = memory_specs(geometry)
    for palette, fabric, origin in (("cache", cache, origins.cache), ("ram", ram, origins.ram),
                                    ("flash", flash, origins.flash), ("vram", vram_spec(geometry), origins.vram)):
        for bank_index in (0, fabric.bank_count - 1):
            point = fabric.bank_origin(origin, bank_index)
            support = next(p for p in iter_memory_bank(point, spec=MemoryBankSpec(words=1, word_bits=8))
                           if p.block == SUPPORT)
            assert plan.apply(support).block in {CONCRETE[c] for c in PALETTE[palette]}
    # Every boundary and one point outside each face uses the same precedence
    # as the explicit volume declarations, including negative world coordinates.
    for region in plan.regions:
        for x, y, z in product((region.box.min.x - 1, region.box.min.x, region.box.max.x, region.box.max.x + 1),
                               (region.box.min.y - 1, region.box.min.y, region.box.max.y, region.box.max.y + 1),
                               (region.box.min.z - 1, region.box.min.z, region.box.max.z, region.box.max.z + 1)):
            point = Vec3(x, y, z)
            expected = next((r for r in plan.regions if r.box.min.x <= x <= r.box.max.x
                             and r.box.min.y <= y <= r.box.max.y and r.box.min.z <= z <= r.box.max.z), None)
            state = no_console.support_at(point)
            if expected is None:
                assert state in {CONCRETE[c] for c in PALETTE["routing"]}
            else:
                assert state in {CONCRETE[c] for c in PALETTE[expected.palette]}


def test_compact_console_colors_identify_real_buttons_and_preserve_deck():
    origin = Vec3(96, 32, 5000)
    blocks = list(iter_controls(origin))
    plan = AppearancePlan((region_for(blocks, "input"),), origin)
    states = {p.pos: p.block for p in plan.placements(blocks)}
    for name, button in control_actuators(origin).items():
        backing = button.offset(dz=-1) if name in CONTROLLER_NAMES[:8] else button.offset(dx=1)
        assert states[backing] == CONCRETE[CONTROL_COLORS[name]]
        assert states[button] == next(p.block for p in blocks if p.pos == button)
    for row in keyboard_buttons(origin):
        assert all(states[button.offset(dy=-1)] in CONCRETE.values() for button in row)
    deck = origin.offset(KEYBOARD_OFFSET.x + 1, KEYBOARD_OFFSET.y, KEYBOARD_OFFSET.z + 1)
    assert states[deck] == CONCRETE["gray"]
    assert plan.manifest()["added_blocks"] == plan.manifest()["moved_blocks"] == 0


@pytest.mark.parametrize("color", tuple(CONCRETE))
def test_actual_all_concrete_colors_preserve_powered_support_and_latch(color):
    origin = Vec3(32, 32, 32)
    template = TEMPLATES["DFF"]
    blocks = [Placement(p.pos, CONCRETE[color] if p.block == SUPPORT else p.block, p.component)
              for p in template.emitter(origin, "dff")]
    pins = {name: origin.offset(p.x, p.y, p.z) for name, p in template.pins.items()}
    steps = [
        {"drive": {"C": False, "D": False}, "ticks": 24, "expect": {"Q": False}},
        {"drive": {"D": True}, "ticks": 24, "expect": {"Q": False}, "continuous": True},
        {"drive": {"C": True}, "ticks": 24, "expect": {"Q": True}},
        {"drive": {"D": False}, "ticks": 24, "expect": {"Q": True}, "continuous": True},
        {"drive": {"C": False}, "ticks": 24, "expect": {"Q": True}, "continuous": True},
        {"drive": {"C": True}, "ticks": 24, "expect": {"Q": False}},
    ]
    run_case(blocks, {p: pins[p] for p in ("C", "D")}, {"Q": pins["Q"]}, steps)


def test_actual_saved_colored_console_all_79_actuators(tmp_path):
    origin = Vec3(96, 32, 5000)
    blocks = list(iter_controls(origin))
    plan = AppearancePlan((region_for(blocks, "input"),), origin)
    blocks = saved_colored_blocks(tmp_path, blocks, plan)
    outputs, steps, selected = compact_console_case(origin)
    result = run_case(blocks, {}, outputs, steps, initial_ticks=128, capture_trace=True)
    assert_compact_console_trace(result, selected)


def test_actual_saved_colored_memory_selected_writes_and_retention(tmp_path):
    origin = Vec3(128, 32, 128)
    spec = MemoryBankSpec(words=4, word_bits=8)
    values = [0xA5, 0x5A, 0xFF, 0]
    blocks = list(iter_memory_bank(origin, spec=spec, initial=bytes(values)))
    plan = AppearancePlan((region_for(blocks, "ram"),))
    blocks = saved_colored_blocks(tmp_path, blocks, plan)
    pins = bank_ports(origin, spec)
    inputs = {f"D{i}": west_driver(p) for i, p in enumerate(pins.data_in)}
    inputs |= {f"W{i}": west_driver(p) for i, p in enumerate(pins.write_select)}
    inputs |= {f"R{i}": west_driver(p) for i, p in enumerate(pins.read_select)}
    outputs = {f"Q{i}": p for i, p in enumerate(pins.data_out)}
    outputs |= {f"S{word}:{bit}": origin.offset(bit * spec.bit_pitch + 2, spec.cell_y, 3 + word * spec.word_pitch)
                for word in range(4) for bit in range(8)}
    bits = lambda prefix, value: {f"{prefix}{bit}": bool(value >> bit & 1) for bit in range(8)}
    stored = lambda: {f"S{word}:{bit}": bool(value >> bit & 1)
                      for word, value in enumerate(values) for bit in range(8)}
    steps = [{"ticks": 64, "expect": stored()}]
    for word, value in ((0, 0x18), (3, 0x81), (1, 0), (2, 0x5A)):
        steps += [
            {"drive": {f"R{word}": True}, "ticks": 64, "expect": stored() | bits("Q", values[word])},
            {"drive": {f"R{word}": False}, "ticks": 64, "expect": stored() | bits("Q", 0)},
            {"drive": bits("D", value), "ticks": 64, "expect": stored(), "continuous": True},
        ]
        values[word] = value
        steps += [
            {"drive": {f"W{word}": True}, "ticks": 64, "expect": stored()},
            {"drive": {f"W{word}": False, f"R{word}": True}, "ticks": 64, "expect": stored() | bits("Q", value)},
            {"drive": {f"R{word}": False}, "ticks": 64, "expect": stored() | bits("Q", 0)},
        ]
    run_case(blocks, inputs, outputs, steps)


def test_actual_saved_colored_display_rows_preserve_all_neighbor_pixels(tmp_path):
    origin = Vec3(128, 32, 128)
    spec = LampPanelSpec(width=8, height=4)
    blocks = list(iter_lamp_panel(origin, spec=spec))
    plan = AppearancePlan((AppearanceRegion("display", panel_bounds(origin, spec), "display"),))
    blocks = saved_colored_blocks(tmp_path, blocks, plan)
    pins = panel_ports(origin, spec)
    inputs = {f"D{i}": west_driver(p) for i, p in enumerate(pins.data_in)}
    inputs |= {f"R{i}": west_driver(p) for i, p in enumerate(pins.row_select)}
    outputs = {f"P{row}:{col}": origin.offset(col * spec.pixel_pitch_x + 3, 2, row * spec.pixel_pitch_y)
               for row in range(spec.height) for col in range(spec.width)}
    expected = dict.fromkeys(outputs, False)
    steps = [{"ticks": 64, "expect": expected.copy()}]
    for row, value in ((0, 0xA5), (1, 0x5A), (2, 0xFF), (3, 0x81), (0, 0), (3, 0x18)):
        steps.append({"drive": {f"D{col}": bool(value >> col & 1) for col in range(8)},
                      "ticks": 64, "expect": expected.copy(), "continuous": True})
        expected.update({f"P{row}:{col}": bool(value >> col & 1) for col in range(8)})
        steps += [
            {"drive": {f"R{row}": True}, "ticks": 64, "expect": expected.copy()},
            {"drive": {f"R{row}": False}, "ticks": 64, "expect": expected.copy(), "continuous": True},
        ]
    run_case(blocks, inputs, outputs, steps)
