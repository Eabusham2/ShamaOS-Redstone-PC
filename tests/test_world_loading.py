"""Check commands against independent chunk sets, including real installations."""
import json
import random
import re
import sqlite3

import nbtlib
import pytest

from shamaos.cli import main
from shamaos.installer import install_world
from shamaos.model import BlockState, Placement, Vec3
from shamaos.release_io import file_inventory
from shamaos.world_loading import loading_plan, saved_world_chunks, write_loading_bundle
from shamaos.worldio import NativeWorldWriter, WorldWriteError


def decode_commands(world, plan, operation="load"):
    path = world / plan["data_pack"] / "data" / plan["namespace"] / "functions" / operation
    action = "add" if operation == "load" else "remove"
    chunks = []
    for function in sorted(path.glob("*.mcfunction")):
        for line in function.read_text().splitlines():
            if line.startswith("schedule "):
                continue
            match = re.fullmatch(r"execute in minecraft:overworld run forceload " + action + r" (-?\d+) (-?\d+) (-?\d+) (-?\d+)", line)
            assert match is not None, line
            x0, z0, x1, z1 = (int(value) // 16 for value in match.groups())
            assert (x1 - x0 + 1) * (z1 - z0 + 1) <= 256
            chunks.extend((x, z) for z in range(z0, z1 + 1) for x in range(x0, x1 + 1))
    assert len(chunks) == len(set(chunks))
    return set(chunks)


def small_world(path, coordinates):
    with NativeWorldWriter(path, surface_y=-61, spawn=Vec3(0, -59, 0), spawn_platform=False) as writer:
        for x, z in coordinates:
            writer.set_block(Placement(Vec3(x * 16, -60, z * 16), BlockState.of("minecraft:stone"), "test"))
        writer.validate()
    return path


@pytest.mark.parametrize("chunks", [
    {(x, z) for x in range(-32, 32) for z in range(-32, 32)},
    {(x, z) for x in range(-17, 18) for z in range(-3, 4) if (x + z) % 3},
    {(-1, -1), (-32, 32), (0, 0), (1, 0), (2, 0), (4000, -4000)},
    {(x, 0) for x in range(513)},
    {(0, z) for z in range(513)},
])
def test_generated_commands_cover_only_input_chunks(tmp_path, chunks):
    plan = write_loading_bundle(tmp_path / "world", chunks)
    assert decode_commands(tmp_path / "world", plan) == chunks
    assert decode_commands(tmp_path / "world", plan, "unload") == chunks
    assert plan["requested_chunks"] == len(chunks)
    assert plan["extra_requested_chunks"] == 0
    assert plan["actual_server_loading_verified"] is False
    assert plan["complete_machine_runtime_verified"] is False
    assert not list((tmp_path / "world" / "datapacks").rglob("tags"))


def test_packing_remains_exact_and_deterministic_with_holes_and_random_order(tmp_path):
    rng = random.Random(8821)
    for case in range(12):
        chunks = {(x, z) for x in range(-25, 26) for z in range(-15, 16) if rng.randrange(4)}
        shuffled = list(chunks)
        rng.shuffle(shuffled)
        assert loading_plan(shuffled) == loading_plan(reversed(shuffled))
        world = tmp_path / str(case)
        plan = write_loading_bundle(world, shuffled)
        assert decode_commands(world, plan) == chunks


@pytest.mark.parametrize("chunks", [[], [(True, 0)], [(1.0, 0)], [(0,)], [(0, 0), (0, 0)], [(1875001, 0)]])
def test_invalid_inventory_never_writes_a_data_pack(tmp_path, chunks):
    with pytest.raises((WorldWriteError, ValueError)):
        write_loading_bundle(tmp_path / "world", chunks)
    assert not (tmp_path / "world").exists()


def test_batched_helpers_cancel_both_directions_and_preserve_modified_files(tmp_path):
    world = tmp_path / "world"
    chunks = {(x * 2, 0) for x in range(270)}
    plan = write_loading_bundle(world, chunks)
    assert plan["batches"] == 3
    before = file_inventory(world)
    assert write_loading_bundle(world, chunks) == plan
    assert file_inventory(world) == before
    functions = world / plan["data_pack"] / "data" / plan["namespace"] / "functions"
    for operation in ("load", "unload"):
        assert (functions / f"{operation}.mcfunction").read_text().splitlines()[0] == f"function {plan['namespace']}:stop"
        for index in (0, 1):
            assert f"schedule function {plan['namespace']}:{operation}/{index + 1:06d} 1t replace" in (
                functions / operation / f"{index:06d}.mcfunction").read_text()
        assert "schedule" not in (functions / operation / "000002.mcfunction").read_text()
    stop = (functions / "stop.mcfunction").read_text().splitlines()
    assert len(stop) == 6 and len(set(stop)) == 6
    edited = functions / "stop.mcfunction"
    edited.write_text("# player customization\n")
    with pytest.raises(WorldWriteError, match="different data pack"):
        write_loading_bundle(world, chunks)
    assert edited.read_text() == "# player customization\n"


def test_cli_binds_saved_world_and_rejects_ledger_omissions(tmp_path, capsys):
    coordinates = {(-1, -1), (0, 0), (4, 4)}
    world = small_world(tmp_path / "world", coordinates)
    assert saved_world_chunks(world) == coordinates
    assert main(["world-loading", "--world", str(world)]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert decode_commands(world, plan) == coordinates
    with sqlite3.connect(world / "shamaos" / "expected.sqlite3") as db:
        db.execute("DELETE FROM chunks WHERE x=4 AND z=4")
    with pytest.raises(WorldWriteError, match="differ"):
        saved_world_chunks(world)


def test_installer_recomputes_only_the_relocated_machine_set(tmp_path):
    source = small_world(tmp_path / "source", {(0, 0), (9, 9)})
    template_chunks = {(-2, 3), (0, 4), (2, 3)}
    template = small_world(tmp_path / "template", template_chunks)
    original_plan = write_loading_bundle(template, template_chunks)
    before = file_inventory(source)
    output = tmp_path / "installed"
    result = install_world(template, source, output, console={"x": 0, "y": -59, "z": 0},
        source_commit="a" * 40, setup_steps=["Console {x} {z}"], offset_chunks=(400, -600))
    expected = {(x + 400, z - 600) for x, z in template_chunks}
    assert decode_commands(output, result["loading_plan"]) == expected
    assert result["loading_plan"]["chunk_inventory_sha256"] != original_plan["chunk_inventory_sha256"]
    assert result["loading_plan"]["load_command"] in (output / "START-HERE.txt").read_text()
    assert file_inventory(source) == before
    assert not (output / original_plan["data_pack"]).exists()


def force_chunks(world, chunks):
    (world / "data").mkdir(exist_ok=True)
    packed = []
    for x, z in chunks:
        value = (x & 0xffffffff) | ((z & 0xffffffff) << 32)
        packed.append(value - (1 << 64) if value >= (1 << 63) else value)
    nbtlib.File({"data": nbtlib.Compound({"Forced": nbtlib.LongArray(packed)}),
                 "DataVersion": nbtlib.Int(3700)}).save(world / "data" / "chunks.dat", gzipped=True)


def test_preserve_preexisting_forced_chunks_including_negative_coordinates(tmp_path):
    world = small_world(tmp_path / "world", {(-4, -6), (2, 3), (5, 7)})
    force_chunks(world, {(-4, -6), (900, 901)})
    original = (world / "data" / "chunks.dat").read_bytes()
    chunks = saved_world_chunks(world)
    plan = write_loading_bundle(world, chunks)
    assert decode_commands(world, plan, "unload") == chunks - {(-4, -6)}
    assert plan["preserved_existing_forced_chunks"] == [(-4, -6)]
    assert (world / "data" / "chunks.dat").read_bytes() == original
    # A subsequent load has forced the whole machine. Regeneration must retain
    # the original ownership baseline, rather than making unload a no-op.
    force_chunks(world, chunks | {(900, 901)})
    assert write_loading_bundle(world, chunks) == plan


def test_unload_is_empty_when_every_machine_chunk_was_previously_forced(tmp_path):
    world = small_world(tmp_path / "world", {(-1, 4)})
    force_chunks(world, {(-1, 4)})
    plan = write_loading_bundle(world, {(-1, 4)})
    assert decode_commands(world, plan, "unload") == set()
    assert plan["unload_requested_chunks"] == 0


@pytest.mark.parametrize("target", ["datapacks", "shamaos", "shamaos/loading-plan.json", "shamaos/LOADING.txt"])
def test_linked_output_is_rejected_before_any_mutation(tmp_path, target):
    world = tmp_path / "world"
    outside = tmp_path / "outside"
    outside.mkdir()
    path = world / target
    path.parent.mkdir(parents=True, exist_ok=True)
    linked = outside if "." not in path.name else outside / "keep.txt"
    if linked != outside:
        linked.write_text("keep this content\n")
    path.symlink_to(linked, target_is_directory=linked.is_dir())
    before = file_inventory(outside)
    with pytest.raises(WorldWriteError, match="symbolic-link"):
        write_loading_bundle(world, {(1, 1)})
    assert file_inventory(outside) == before


def test_conflicting_notes_and_interrupted_pack_do_not_destroy_user_files(tmp_path, monkeypatch):
    world = tmp_path / "world"
    notes = world / "shamaos" / "LOADING.txt"
    notes.parent.mkdir(parents=True)
    notes.write_text("unrelated user instructions\n")
    with pytest.raises(WorldWriteError, match="instructions differ"):
        write_loading_bundle(world, {(1, 1)})
    assert notes.read_text() == "unrelated user instructions\n"
    assert not (world / "datapacks").exists()
    other = tmp_path / "other"
    from pathlib import Path
    original = Path.write_text
    def fail_midway(path, *args, **kwargs):
        if path.name == "stop.mcfunction":
            raise OSError("interrupted test write")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "write_text", fail_midway)
    with pytest.raises(OSError, match="interrupted test write"):
        write_loading_bundle(other, {(2, 2)})
    assert not list((other / "datapacks").iterdir())
    monkeypatch.setattr(Path, "write_text", original)
    assert write_loading_bundle(other, {(2, 2)})["cover_verified"] is True


def test_installer_retains_existing_ticket_at_a_previously_unsaved_destination(tmp_path):
    source = small_world(tmp_path / "source", {(0, 0)})
    force_chunks(source, {(100, -100)})
    template = small_world(tmp_path / "template", {(0, 0), (1, 0)})
    force_chunks(template, {(0, 0), (1, 0)})
    before = file_inventory(source)
    output = tmp_path / "output"
    result = install_world(template, source, output, console={"x": 0, "y": -59, "z": 0},
        source_commit="a" * 40, setup_steps=[], offset_chunks=(100, -100))
    assert decode_commands(output, result["loading_plan"], "unload") == {(101, -100)}
    assert file_inventory(source) == before
    assert (output / "data" / "chunks.dat").read_bytes() == (source / "data" / "chunks.dat").read_bytes()


def test_report_directory_cannot_escape_world_through_parent_components(tmp_path):
    world = tmp_path / "world"
    with pytest.raises(WorldWriteError, match="inside the selected world"):
        write_loading_bundle(world, {(1, 1)}, report_directory=world / ".." / "outside")
    assert not world.exists() and not (tmp_path / "outside").exists()
