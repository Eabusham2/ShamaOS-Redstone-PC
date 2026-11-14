from dataclasses import replace
import pytest

from shamaos.layout import MachineGeometry, default_origins, memory_specs, plan_machine, shared_memory_tile, vram_spec
from shamaos.machine_spec import RAM_BYTES, FLASH_BYTES
from shamaos.model import Box, BuildPlan, ComponentPlan, Vec3


def test_default_floorplan_has_no_overlaps():
    plan = plan_machine(MachineGeometry(
        origin=Vec3(0, 8, 0),
        ram_bytes=RAM_BYTES,
        cache_bytes=8 << 10,
        flash_bytes=FLASH_BYTES,
        display_width=320,
        display_height=180,
    ))
    names = {c.name for c in plan.components}
    assert {"cpu","cache","ram","flash","gpu","input","display"} <= names


@pytest.mark.parametrize("surface,base,layers", [(-61, -56, 30), (63, 68, 20)])
def test_auto_stacking_preserves_surface_and_routing_canopy(surface, base, layers):
    geometry = MachineGeometry(Vec3(0, surface + 5, 0), RAM_BYTES, 8 << 10, FLASH_BYTES, 320, 180, surface_y=surface)
    assert geometry.storage_origin_y == base
    assert geometry.resolved_memory_layers == layers
    origins = default_origins(geometry)
    fabrics = (*memory_specs(geometry), vram_spec(geometry))
    for fabric, origin in zip(fabrics, (origins.cache, origins.ram, origins.flash, origins.vram)):
        bounds = fabric.bounds(origin)
        assert bounds.min.y >= surface + 1
        assert bounds.max.y <= 307
    assert [fabric.bank_count for fabric in fabrics] == [2, 24, 50, 4]
    assert [fabric.layers for fabric in fabrics] == [2, min(24, layers), layers, 4]
    if surface == -61:
        assert shared_memory_tile(geometry) == {"cache": (0, -170), "vram": (28, -154), "ram": (80, -138)}
        assert origins.cache.x == origins.ram.x == origins.vram.x
        assert (origins.cache.y, origins.vram.y, origins.ram.y) == (-56, -28, 24)
        assert fabrics[1].bounds(origins.ram).max.y == 307
    else:
        assert shared_memory_tile(geometry) == {}
        assert len({origins.cache.x, origins.ram.x, origins.vram.x}) == 3
    with pytest.raises(ValueError, match="above-ground"):
        replace(geometry, memory_layers=layers + 1)
    with pytest.raises(ValueError, match="below the grass"):
        replace(geometry, memory_base_y=surface + 4)
    plan_machine(geometry)


def test_component_fragments_allow_empty_headroom_but_reject_real_overlap():
    envelope = Box(Vec3(0, 0, 0), Vec3(10, 10, 10))
    column = Box(Vec3(0, 0, 0), Vec3(1, 10, 10))
    base = Box(Vec3(0, 0, 0), Vec3(10, 1, 10))
    plan = BuildPlan()
    plan.add(ComponentPlan("lower", envelope, "physical-memory", collision_boxes=(column, base)))
    plan.add(ComponentPlan("upper", Box(Vec3(3, 3, 0), Vec3(10, 10, 10)), "physical-memory"))
    with pytest.raises(ValueError, match="component overlap"):
        plan.add(ComponentPlan("crossing", Box(Vec3(1, 3, 0), Vec3(2, 4, 1)), "physical-memory"))
    with pytest.raises(ValueError, match="leave envelope"):
        BuildPlan().add(ComponentPlan("invalid", base, "physical-memory", collision_boxes=(column,)))


@pytest.mark.parametrize("layers", [8, 16, 24])
def test_shared_tile_respects_requested_total_layer_limit(layers):
    geometry = MachineGeometry(Vec3(0, -56, 0), RAM_BYTES, 8 << 10, FLASH_BYTES, 320, 180, memory_layers=layers)
    assert shared_memory_tile(geometry) == {}
    origins = default_origins(geometry)
    assert len({origins.cache.x, origins.ram.x, origins.vram.x}) == 3
    assert all(fabric.layers <= layers for fabric in (*memory_specs(geometry), vram_spec(geometry)))
    plan_machine(geometry)


def test_legacy_one_hot_geometry_never_uses_shared_fabric_placement():
    from shamaos.generator import geometry_from_config, load_config
    from shamaos.hardware.memory_backbone import fabric_tile_ports

    geometry = MachineGeometry(Vec3(0, -56, 0), RAM_BYTES, 8 << 10, FLASH_BYTES, 320, 180, memory_binary_rows=False)
    assert shared_memory_tile(geometry) == {}
    origins = default_origins(geometry)
    assert len({origins.cache.x, origins.ram.x, origins.vram.x}) == 3
    cache = memory_specs(geometry)[0]
    ports = fabric_tile_ports(origins.cache, cache)[0]
    assert len(ports.row_select) == 1024 and not ports.row_address
    plan_machine(geometry)
    config = load_config("configs/default.toml")
    config["memory"]["row_decoder"] = "one-hot"
    with pytest.raises(ValueError, match="requires the binary"):
        geometry_from_config(config)


@pytest.mark.parametrize("surface,layers,shared", [(-61, "auto", True), (-61, 24, False), (63, "auto", False)])
def test_production_sections_manifest_and_capacity_match_shared_placement(surface, layers, shared):
    from shamaos.generator import geometry_from_config, load_config, manifest, physical_sections, _storage_layout_cost

    config = load_config("configs/default.toml")
    config["world"]["surface_y"] = surface
    config["world"]["origin_y"] = surface + 5
    config["memory"]["stack_layers"] = layers
    if surface == 63:
        # This case checks independent memory tiles on a higher grass
        # surface; the full upright display has its own height rejection.
        config["display"].update(orientation="horizontal", pixel_pitch_x=5, pixel_pitch_y=4)
    geometry = geometry_from_config(config)
    origins = default_origins(geometry)
    exported = manifest(config, plan_machine(geometry))
    packing = exported["memory_packing"]
    assert bool(packing["shared_fabrics"]) is shared
    assert packing["horizontal_tiles"] == _storage_layout_cost(geometry)["horizontal_memory_tiles"]
    sections = [section for section in physical_sections(config) if section.metadata.get("kind") == "stacked-memory-tile"]
    assert sum(section.metadata["banks"] for section in sections) == 79
    ram_sections = [section for section in sections if section.name.startswith("ram-")]
    assert sum(section.metadata["bytes"] for section in ram_sections) == RAM_BYTES
    assert ram_sections[0].metadata["origin"] == vars(origins.ram)
    assert exported["memory_banks"] == {"cache": 1, "ram": 24, "flash": 50, "vram": 4, "bytes_per_bank": 4096,
                                        "final_bank_bytes": {"cache": 4096, "ram": 4096, "flash": 4096, "vram": 4096}}
