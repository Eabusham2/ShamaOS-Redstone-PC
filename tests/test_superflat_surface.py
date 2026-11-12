"""Grass, dirt and bedrock remain intact beneath every generated component."""
import nbtlib
import pytest

from shamaos.cli import main
from shamaos.generator import geometry_from_config, load_config
from shamaos.layout import memory_specs
from shamaos.machine_spec import RAM_BYTES, CACHE_BYTES, FLASH_BYTES
from shamaos.model import BlockState, Placement, Vec3
from shamaos.worldio import (
    NativeWorldReader, NativeWorldWriter, WorldWriteError, _Region,
    resolve_world_surface, superflat_surface_y, validate_native_world,
)


STONE = BlockState.of("minecraft:stone")
GRASS = BlockState.of("minecraft:grass_block", snowy="false")
DIRT = BlockState.of("minecraft:dirt")
BEDROCK = BlockState.of("minecraft:bedrock")


@pytest.mark.parametrize("surface", [-61, 63])
def test_saved_ground_is_complete_and_all_placements_stay_above_it(tmp_path, surface):
    world = tmp_path / "flat"
    with NativeWorldWriter(world, surface_y=surface,
                           spawn=Vec3(-16, surface + 2, -16), max_cached_chunks=1) as writer:
        writer.set_block(Placement(Vec3(16, surface + 1, 16), STONE, "lowest-support"))
        writer.set_block(Placement(Vec3(32, 319, 32), STONE, "highest-wire"))
        for y in (-64, -63, surface):
            with pytest.raises(WorldWriteError, match="protected grass/ground"):
                writer.set_block(Placement(Vec3(16, y, 16), STONE, "invalid-underground"))
        report = writer.validate()
    assert report["terrain_preserved"] is True
    assert report["minimum_build_y"] == surface + 1
    assert report["assigned_blocks"] == 83  # Ground is not counted as circuitry.
    assert superflat_surface_y(world) == surface
    reader = NativeWorldReader(world)
    for x, z in ((-16, -16), (16, 16), (32, 32), (1000, -1000)):
        assert reader.get_block(Vec3(x, -64, z)) == BEDROCK
        assert reader.get_block(Vec3(x, -63, z)) == DIRT
        assert reader.get_block(Vec3(x, surface - 1, z)) == DIRT
        assert reader.get_block(Vec3(x, surface, z)) == GRASS
    assert reader.get_block(Vec3(16, surface + 1, 16)) == STONE
    with NativeWorldWriter(world, spawn_platform=False) as resumed:
        with pytest.raises(WorldWriteError, match="protected grass/ground"):
            resumed.set_block(Placement(Vec3(32, surface, 32), GRASS, "same-state-still-protected"))
    with pytest.raises(WorldWriteError, match="differs"):
        NativeWorldWriter(world, spawn_platform=False, surface_y=surface + 1)
    assert validate_native_world(world)["assigned_blocks"] == 83


def test_existing_higher_flat_preset_relocates_and_reduces_stack_without_losing_capacity(tmp_path):
    world = tmp_path / "high-flat"
    with NativeWorldWriter(world, surface_y=63, spawn=Vec3(-16, 65, -16)):
        pass
    original = load_config("configs/default.toml")
    resolved = resolve_world_surface(original, world)
    assert original["world"]["surface_y"] == -61
    assert resolved["world"]["surface_y"] == 63
    assert resolved["world"]["origin_y"] == 68
    assert resolved["world"]["minimum_build_y"] == 64
    assert resolve_world_surface(resolved, world) == resolved
    g = geometry_from_config(resolved)
    cache, ram, flash = memory_specs(g)
    assert (cache.total_bytes, ram.total_bytes, flash.total_bytes) == (CACHE_BYTES, RAM_BYTES, FLASH_BYTES)
    assert (cache.layers, ram.layers, flash.layers) == (2, 20, 20)
    assert g.storage_origin_y == 68


def test_cli_creates_a_grass_world_and_keeps_spawn_platform_above_surface(tmp_path, capsys):
    world = tmp_path / "created"
    assert main(["create-world", "--world", str(world)]) == 0
    assert "above intact ground" in capsys.readouterr().out
    assert superflat_surface_y(world) == -61
    level = nbtlib.load(world / "level.dat")["Data"]
    layers = level["WorldGenSettings"]["dimensions"]["minecraft:overworld"]["generator"]["settings"]["layers"]
    assert [(int(layer["height"]), str(layer["block"])) for layer in layers] == [
        (1, "minecraft:bedrock"), (2, "minecraft:dirt"), (1, "minecraft:grass_block"),
    ]
    assert int(level["SpawnY"]) == -59
    assert NativeWorldReader(world).get_block(Vec3(-16, -61, -16)) == GRASS


def test_full_build_surface_detection_rejects_void_fixture_without_changing_it(tmp_path):
    world = tmp_path / "fixture"
    with NativeWorldWriter(world):
        pass
    before = (world / "level.dat").read_bytes()
    with pytest.raises(WorldWriteError, match="grass"):
        resolve_world_surface(load_config("configs/default.toml"), world)
    assert (world / "level.dat").read_bytes() == before
    assert validate_native_world(world)["status"] == "passed"


def test_readback_detects_ground_corruption_even_when_terrain_is_not_a_placement(tmp_path):
    world = tmp_path / "flat"
    with NativeWorldWriter(world, surface_y=-61, spawn=Vec3(0, -59, 0)) as writer:
        writer.validate()
    region = _Region(world / "region" / "r.0.0.mca")
    chunk = region.read(0)
    section = next(section for section in chunk["sections"] if int(section["Y"]) == -4)
    palette = section["block_states"]["palette"]
    grass = next(entry for entry in palette if str(entry["Name"]) == "minecraft:grass_block")
    grass["Name"] = nbtlib.String("minecraft:barrier")
    grass.pop("Properties")
    region.write(0, chunk)
    region.compact()
    with pytest.raises(WorldWriteError, match="readback mismatch"):
        validate_native_world(world)
