"""Behavioral export/readback tests for the dependency-light Anvil backend."""
from pathlib import Path
import struct
import subprocess
import sys

import nbtlib
import pytest

from shamaos.hardware.memory import comparator, redstone_wire, repeater
from shamaos.model import BlockState, Placement, Vec3
from shamaos.worldio import (
    AIR, NativeWorldReader, NativeWorldWriter, WorldWriteError,
    _Region, canonical_block_state, validate_native_world,
)


STONE = BlockState.of("minecraft:stone")


def put(pos, state=STONE, component="test"):
    return Placement(pos, state, component)


def one_chunk(tmp_path):
    world = tmp_path / "world"
    with NativeWorldWriter(world, spawn_platform=False) as writer:
        writer.set_block(put(Vec3(0, 64, 0)))
    return world


def rewrite_chunk(world, change):
    region = _Region(world / "region" / "r.0.0.mca")
    root = region.read(0)
    assert root is not None
    change(root)
    region.write(0, root)
    region.compact()


def test_create_world_without_an_existing_save_and_read_actual_regions(tmp_path):
    world = tmp_path / "new-world"
    with NativeWorldWriter(world, name="Ready to open") as writer:
        report = writer.validate()
    level = nbtlib.load(world / "level.dat")["Data"]
    assert level["LevelName"] == "Ready to open"
    assert int(level["DataVersion"]) == 3700
    assert level["Version"]["Name"] == "1.20.4"
    assert level["WorldGenSettings"]["dimensions"]["minecraft:overworld"]["generator"]["type"] == "minecraft:flat"
    assert int(level["GameType"]) == 1
    reader = NativeWorldReader(world)
    assert reader.get_block(Vec3(-16, 63, -16)) == STONE
    assert reader.get_block(Vec3(-16, 64, -16)) == AIR
    assert report["assigned_blocks"] == 81
    assert report["status"] == "passed"
    assert len(list(reader.iter_blocks())) == 81
    for region in (world / "region").glob("*.mca"):
        assert region.stat().st_size % 4096 == 0


def test_exact_properties_and_negative_far_away_chunk_region_boundaries(tmp_path):
    world = tmp_path / "world"
    states = [
        repeater("east", powered=True, locked=True, delay=4),
        comparator("south", powered=True),
        redstone_wire(north="up", east="none", south="side", west="none", power=11),
        BlockState.of("minecraft:redstone_lamp", lit="true"),
        BlockState.of("minecraft:lever", face="ceiling", facing="west", powered="true"),
        BlockState.of("minecraft:stone_button", face="wall", facing="south", powered="false"),
        BlockState.of("minecraft:redstone_wall_torch", facing="east", lit="false"),
    ]
    points = [
        Vec3(-1, -64, -1), Vec3(-16, -1, -16), Vec3(-17, 319, -17),
        Vec3(15, 15, 15), Vec3(16, 16, 16), Vec3(511, 31, 511),
        Vec3(512, 32, 512), Vec3(-512, 64, -512), Vec3(-513, 65, -513),
        Vec3(500_123, 64, -400_017), Vec3(-29_999_984, 64, 29_999_983),
    ]
    placements = [put(point, states[index % len(states)]) for index, point in enumerate(points)]
    with NativeWorldWriter(world, spawn_platform=False, max_cached_chunks=1) as writer:
        writer.set_blocks(placements)
        report = writer.validate()
    reader = NativeWorldReader(world, max_cached_chunks=1)
    for placement in placements:
        assert reader.get_block(placement.pos) == placement.block
    assert report["assigned_blocks"] == len(placements)
    actual = {p.pos: p.block for p in reader.iter_blocks()}
    assert actual == {p.pos: p.block for p in placements}


def test_five_bit_palette_packing_at_long_boundaries(tmp_path):
    world = tmp_path / "world"
    states = [redstone_wire(power=i) for i in range(16)] + [STONE, BlockState.of("minecraft:redstone_lamp", lit="true")]
    with NativeWorldWriter(world, spawn_platform=False) as writer:
        for index in range(4096):
            writer.set_block(put(Vec3(index & 15, 64 + (index >> 8), (index >> 4) & 15), states[index % len(states)]))
        writer.validate()
    root = _Region(world / "region" / "r.0.0.mca").read(0)
    encoded = next(section for section in root["sections"] if int(section["Y"]) == 4)["block_states"]
    assert len(encoded["palette"]) == 18
    assert len(encoded["data"]) == (4096 + 11) // 12
    # Decode the on-disk longs independently from NativeWorldReader. Five-bit
    # entries leave four high padding bits in every 64-bit word.
    palette = [BlockState.of(str(entry["Name"]), **{k: str(v) for k, v in entry.get("Properties", {}).items()}) for entry in encoded["palette"]]
    for index in range(4096):
        word = int(encoded["data"][index // 12]) & ((1 << 64) - 1)
        actual = palette[(word >> ((index % 12) * 5)) & 31]
        assert actual == states[index % len(states)]
    assert all((int(word) & ((1 << 64) - 1)) >> 60 == 0 for word in encoded["data"])


def test_identical_overlap_allowed_but_conflicts_survive_eviction_save_reopen(tmp_path):
    world = tmp_path / "world"
    point = Vec3(0, 64, 0)
    with NativeWorldWriter(world, spawn_platform=False, max_cached_chunks=1) as writer:
        writer.set_block(put(point))
        writer.set_block(put(point, component="shared-terminal"))
        assert writer.identical_overlaps == 1
        writer.set_block(put(Vec3(512, 64, 0)))
        with pytest.raises(WorldWriteError, match="conflicting placements"):
            writer.set_block(put(point, BlockState.of("minecraft:redstone_block"), "crossing-route"))
        writer.save()
        with pytest.raises(WorldWriteError, match="implicit overwrite"):
            writer.set_block(put(point, AIR))
    with NativeWorldWriter(world, spawn_platform=False) as writer:
        with pytest.raises(WorldWriteError, match="conflicting placements"):
            writer.set_block(put(point, BlockState.of("minecraft:black_concrete")))
        assert writer.validate()["assigned_blocks"] == 2
    assert NativeWorldReader(world).get_block(point) == STONE


def test_explicit_air_assignment_is_not_lost_across_save(tmp_path):
    world = tmp_path / "world"
    point = Vec3(0, 64, 0)
    with NativeWorldWriter(world, spawn_platform=False) as writer:
        writer.set_block(put(point, AIR))
        assert writer.validate()["assigned_blocks"] == 1
    with NativeWorldWriter(world, spawn_platform=False) as writer:
        with pytest.raises(WorldWriteError, match="conflicting placements"):
            writer.set_block(put(point))
    reader = NativeWorldReader(world)
    assert list(reader.iter_blocks()) == []
    assert [p.pos for p in reader.iter_blocks(include_assigned_air=True)] == [point]


@pytest.mark.parametrize("position", [
    Vec3(0, -65, 0), Vec3(0, 320, 0), Vec3(-29_999_985, 64, 0),
    Vec3(29_999_984, 64, 0), Vec3(0, 64, -29_999_985), Vec3(0, 64, 29_999_984),
    Vec3(0.5, 64, 0), Vec3(False, 64, 0),
])
def test_rejects_invalid_java_coordinates(tmp_path, position):
    with NativeWorldWriter(tmp_path / "world", spawn_platform=False) as writer:
        with pytest.raises(WorldWriteError):
            writer.set_block(put(position))


@pytest.mark.parametrize("state", [
    BlockState.of("minecraft:not_a_real_block"), BlockState.of("mod:repeater"),
    BlockState.of("minecraft:repeater", delay="5"),
    BlockState.of("minecraft:comparator", powered="yes"),
    BlockState.of("minecraft:stone", facing="north"),
    BlockState.of("minecraft:redstone_wire", north="left"),
    BlockState.of("minecraft:redstone_wall_torch", facing="up"),
])
def test_rejects_unknown_blocks_or_invalid_functional_states(tmp_path, state):
    with NativeWorldWriter(tmp_path / "world", spawn_platform=False) as writer:
        with pytest.raises(WorldWriteError):
            writer.set_block(put(Vec3(0, 64, 0), state))


def test_missing_properties_have_explicit_java_defaults():
    state = canonical_block_state(BlockState.of("repeater"))
    assert dict(state.properties) == {"delay": "1", "facing": "north", "locked": "false", "powered": "false"}


def test_saved_state_corruption_is_detected_instead_of_validating_cache(tmp_path):
    world = one_chunk(tmp_path)

    def corrupt(root):
        section = next(section for section in root["sections"] if int(section["Y"]) == 4)
        for entry in section["block_states"]["palette"]:
            if entry["Name"] == "minecraft:stone":
                entry["Name"] = nbtlib.String("minecraft:black_concrete")

    rewrite_chunk(world, corrupt)
    assert NativeWorldReader(world).get_block(Vec3(0, 64, 0)).name == "minecraft:black_concrete"
    with pytest.raises(WorldWriteError, match="readback mismatch"):
        validate_native_world(world)
    with pytest.raises(WorldWriteError, match="readback mismatch"):
        NativeWorldWriter(world)


def test_rejects_wrong_chunk_coordinates(tmp_path):
    world = one_chunk(tmp_path)
    rewrite_chunk(world, lambda root: root.__setitem__("xPos", nbtlib.Int(1)))
    with pytest.raises(WorldWriteError, match="coordinates disagree"):
        validate_native_world(world)


def test_rejects_overlapping_region_sectors(tmp_path):
    world = one_chunk(tmp_path)
    with NativeWorldWriter(world, spawn_platform=False) as writer:
        writer.set_block(put(Vec3(16, 64, 0)))
    path = world / "region" / "r.0.0.mca"
    data = bytearray(path.read_bytes())
    data[4:8] = data[0:4]
    path.write_bytes(data)
    with pytest.raises(WorldWriteError, match="overlapping Anvil"):
        validate_native_world(world)


@pytest.mark.parametrize("corruption", ["truncated", "header", "payload", "compression", "compressed-data"])
def test_rejects_corrupt_anvil_region_records(tmp_path, corruption):
    world = one_chunk(tmp_path)
    path = world / "region" / "r.0.0.mca"
    data = bytearray(path.read_bytes())
    sector = struct.unpack_from(">I", data)[0] >> 8
    start = sector * 4096
    if corruption == "truncated":
        data.pop()
    elif corruption == "header":
        struct.pack_into(">I", data, 0, (1 << 8) | 1)
    elif corruption == "payload":
        struct.pack_into(">I", data, start, 4096)
    elif corruption == "compression":
        data[start + 4] = 127
    else:
        data[start + 5] ^= 255
    path.write_bytes(data)
    with pytest.raises(WorldWriteError):
        validate_native_world(world)


def test_comparator_block_entities_preserve_output_and_are_validated(tmp_path):
    world = tmp_path / "world"
    with NativeWorldWriter(world, spawn_platform=False) as writer:
        writer.set_block(put(Vec3(1, 64, 2), comparator("east", powered=True)))
        writer.validate()
    region = _Region(world / "region" / "r.0.0.mca")
    root = region.read(0)
    entity = root["block_entities"][0]
    assert (int(entity["x"]), int(entity["y"]), int(entity["z"]), int(entity["OutputSignal"])) == (1, 64, 2, 15)
    rewrite_chunk(world, lambda root: root["block_entities"][0].__setitem__("OutputSignal", nbtlib.Int(0)))
    with pytest.raises(WorldWriteError, match="comparator block entity"):
        validate_native_world(world)


def test_refuses_unowned_or_nonempty_destinations(tmp_path):
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "notes.txt").write_text("keep me")
    with pytest.raises(WorldWriteError, match="must be empty"):
        NativeWorldWriter(occupied)
    assert (occupied / "notes.txt").read_text() == "keep me"
    world = one_chunk(tmp_path)
    (world / "shamaos" / "world.json").unlink()
    with pytest.raises(WorldWriteError, match="unowned save"):
        NativeWorldWriter(world)


def test_compaction_bounds_file_growth_on_repeated_saves(tmp_path):
    world = tmp_path / "world"
    with NativeWorldWriter(world, spawn_platform=False) as writer:
        for x in range(16):
            writer.set_block(put(Vec3(x, 64, 0)))
            writer.save()
        assert writer.validate()["assigned_blocks"] == 16
    assert (world / "region" / "r.0.0.mca").stat().st_size == 3 * 4096


def test_mutation_invalidates_previous_success_reports(tmp_path):
    world = tmp_path / "world"
    with NativeWorldWriter(world, spawn_platform=False) as writer:
        writer.set_block(put(Vec3(0, 64, 0)))
        writer.validate()
        assert (world / "shamaos" / "readback.json").is_file()
        writer.set_block(put(Vec3(1, 64, 0)))
        assert not (world / "shamaos" / "readback.json").exists()
        writer.validate()
        writer.set_block(put(Vec3(1, 64, 0)))  # Identical overlap changes nothing.
        assert (world / "shamaos" / "readback.json").is_file()
    with NativeWorldWriter(world, spawn_platform=False) as writer:
        writer.set_block(put(Vec3(2, 64, 0)))
    assert not (world / "shamaos" / "readback.json").exists()
    assert (world / "session.lock").read_bytes() == "☃".encode()


def test_disk_reserve_stops_before_saving_chunks(tmp_path, monkeypatch):
    world = tmp_path / "world"
    with NativeWorldWriter(world, spawn_platform=False) as writer:
        writer.set_block(put(Vec3(0, 64, 0)))
        with monkeypatch.context() as patch:
            from collections import namedtuple
            usage = namedtuple("usage", "total used free")
            patch.setattr("shamaos.worldio.shutil.disk_usage", lambda _path: usage(100, 99, 1))
            with pytest.raises(WorldWriteError, match="64 MiB reserve"):
                writer.save()
            assert list((world / "region").glob("*.mca")) == []
        assert writer.validate()["assigned_blocks"] == 1


def test_session_lock_excludes_other_processes_and_releases_after_close(tmp_path):
    world = one_chunk(tmp_path)
    attempt = """
import sys
from shamaos.worldio import NativeWorldWriter, WorldWriteError
try:
    with NativeWorldWriter(sys.argv[1], spawn_platform=False):
        print('opened')
except WorldWriteError as exc:
    if 'locked by another writer or Minecraft' not in str(exc):
        raise
    print('locked')
"""
    def probe():
        result = subprocess.run(
            [sys.executable, "-c", attempt, str(world)],
            capture_output=True, text=True, timeout=20, check=True,
        )
        return result.stdout.strip()

    with NativeWorldWriter(world, spawn_platform=False) as writer:
        # POSIX record locks belong to the process; closing a separately
        # opened descriptor for this inode would release the test's lock.
        writer._lock.seek(0)
        before = writer._lock.read()
        assert probe() == "locked"
        writer._lock.seek(0)
        assert writer._lock.read() == before
        assert writer.validate()["assigned_blocks"] == 1
    assert probe() == "opened"
    assert validate_native_world(world)["assigned_blocks"] == 1
