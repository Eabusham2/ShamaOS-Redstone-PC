"""Exact console geometry and above-grass compact regular-fabric placement."""
import json
import os
from pathlib import Path

import pytest

from shamaos.generator import geometry_from_config, load_config, source_provenance, write_placement_stages
from shamaos.hardware.clock import iter_clock
from shamaos.hardware.controls import control_bounds, control_interaction_descriptor, iter_controls
from shamaos.hardware.debug import iter_debug_panel
from shamaos.layout import default_origins, plan_machine
from shamaos.model import Placement, Vec3
from shamaos.worldio import NativeWorldReader


def test_compact_floorplan_preserves_surface_console_and_routing_corridors():
    geometry = geometry_from_config(load_config("configs/default.toml"))
    plan = plan_machine(geometry)
    boxes = {component.name: component.box for component in plan.components}
    assert boxes["cache"].min.x - boxes["display"].max.x - 1 == 800
    assert boxes["debug-panel"].max.z == default_origins(geometry).input.z - 224
    assert boxes["input"].max.z < boxes["display"].min.z
    for name in ("cache", "ram", "flash", "vram", "input", "display", "debug-panel", "clock"):
        assert boxes[name].min.y >= geometry.minimum_build_y
        assert boxes[name].max.y <= 319
    console = next(component for component in plan.components if component.name == "input")
    descriptor = console.metadata["player_interaction"]
    assert descriptor["activation_span_xz"] == [16, 16]
    assert descriptor["activation_count"] == 79


def test_saved_compact_console_debug_clock_preserve_every_block_and_grass(tmp_path):
    geometry = geometry_from_config(load_config("configs/default.toml"))
    origins = default_origins(geometry)
    stages = [("console", list(iter_controls(origins.input))),
              ("debug", list(iter_debug_panel(origins.input.offset(dz=-224)))),
              ("clock", list(iter_clock(origins.input.offset(180))))]
    console_box = control_bounds(origins.input)
    literal = stages[0][1]
    assert Vec3(*(min(getattr(p.pos, axis) for p in literal) for axis in ("x", "y", "z"))) == console_box.min
    assert Vec3(*(max(getattr(p.pos, axis) for p in literal) for axis in ("x", "y", "z"))) == console_box.max
    descriptor = control_interaction_descriptor(origins.input)
    assert descriptor["stairs_lowest_support_y"] == geometry.minimum_build_y
    world = tmp_path / "compact-console"
    _, _, validation = write_placement_stages(world, stages, surface_y=geometry.surface_y,
                                              spawn=Vec3(880, geometry.surface_y + 2, 160))
    assert validation["status"] == "passed"
    reader = NativeWorldReader(world)
    for _, blocks in stages:
        for placement in blocks:
            assert placement.pos.y > geometry.surface_y
            assert reader.get_block(placement.pos) == placement.block
    for position in (console_box.min, console_box.max, origins.input.offset(60, 0, 52)):
        assert reader.get_block(Vec3(position.x, geometry.surface_y, position.z)).name == "minecraft:grass_block"


def test_saved_compact_console_every_actuator_in_actual_engine(tmp_path):
    runner = os.environ.get("SHAMA_MCHPRS_RUNNER")
    if not runner or not Path(runner).is_file():
        pytest.skip("actual MCHPRS runner not available; saved console runtime is unverified")
    from test_mchprs_runtime import assert_compact_console_trace, compact_console_case, run_case

    geometry = geometry_from_config(load_config("configs/default.toml"))
    origin = default_origins(geometry).input
    blocks = list(iter_controls(origin))
    report_directory = os.environ.get("SHAMA_VALIDATION_REPORT_DIR")
    world = Path(report_directory) / "compact-console-world" if report_directory else tmp_path / "console-world"
    _, _, verification = write_placement_stages(world, [("console", blocks)], surface_y=geometry.surface_y,
                                                spawn=Vec3(880, geometry.surface_y + 2, 160))
    reader = NativeWorldReader(world)
    # A uniform translation is required only by the engine's small TestWorld.
    # Every simulated circuit state comes from reopened region files.
    translation = Vec3(0, 64, 256)
    moved_origin = origin.offset(translation.x, translation.y, translation.z)
    reopened = [Placement(position.offset(translation.x, translation.y, translation.z), reader.get_block(position), "reopened-console")
                for position in sorted({block.pos for block in blocks})]
    outputs, steps, selected = compact_console_case(moved_origin)
    runtime = run_case(reopened, {}, outputs, steps, initial_ticks=128, capture_trace=True)
    assert_compact_console_trace(runtime, selected)
    runtime.pop("traces", None)
    report = {"kind": "saved-compact-console-runtime", "complete_machine_runtime_verified": False,
              "world_validation": verification, "runtime": runtime, "unique_circuit_blocks": len(reopened),
              "surface_y": geometry.surface_y, "minimum_circuit_y": min(block.pos.y for block in blocks),
              "runtime_translation": vars(translation), "interaction": control_interaction_descriptor(origin),
              "source": source_provenance()}
    (world / "shamaos" / "console-runtime.json").write_text(json.dumps(report, indent=2) + "\n")
