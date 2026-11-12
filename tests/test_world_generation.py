import json
import os
from pathlib import Path
import subprocess

import pytest

from shamaos.cli import main
from shamaos.generator import create_runtime_fixture_world, write_placement_stages
from shamaos.model import BlockState, Placement, Vec3
from shamaos.worldio import NativeWorldReader, WorldWriteError, validate_native_world


def test_stage_writer_creates_world_and_validate_flag_runs_disk_readback(tmp_path):
    world = tmp_path / "world"
    placements = [Placement(Vec3(32 + x, 64, 32), BlockState.of("minecraft:stone"), "stage") for x in range(40)]
    count, stages, report = write_placement_stages(world, [("first", placements[:20]), ("second", placements[20:])], save_every=7)
    assert count == 40
    assert stages == {"first": 20, "second": 20}
    assert report["method"] == "independent-anvil-block-state-readback"
    assert report["assigned_blocks"] == 121
    assert NativeWorldReader(world).get_block(placements[-1].pos) == placements[-1].block


def test_disabled_readback_is_reported_as_not_run(tmp_path):
    world = tmp_path / "world"
    _, _, report = write_placement_stages(world, [], validate_after_write=False)
    assert report["status"] == "not-run"
    assert not (world / "shamaos" / "readback.json").exists()
    assert validate_native_world(world)["status"] == "passed"


def test_cross_stage_conflicts_fail_and_preserve_saved_first_stage(tmp_path):
    world = tmp_path / "world"
    point = Vec3(0, 64, 0)
    with pytest.raises(WorldWriteError, match="second-stage"):
        write_placement_stages(world, [
            ("one", [Placement(point, BlockState.of("minecraft:stone"), "first-stage")]),
            ("two", [Placement(point, BlockState.of("minecraft:redstone_wire"), "second-stage")]),
        ])
    assert NativeWorldReader(world).get_block(point).name == "minecraft:stone"
    assert not (world / "shamaos" / "readback.json").exists()


def test_cli_creates_and_validates_world_without_optional_amulet(tmp_path, capsys):
    path = tmp_path / "cli-world"
    assert main(["create-world", "--world", str(path), "--name", "CLI fixture"]) == 0
    assert "verified 81" in capsys.readouterr().out
    assert main(["validate-world", "--world", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "passed"


def test_playable_fixture_case_comes_from_exact_saved_blocks(tmp_path):
    world = tmp_path / "fixture"
    report = create_runtime_fixture_world(world)
    assert report["kind"] == "small-runtime-fixture"
    assert report["complete_shamaos_machine"] is False
    assert report["world_validation"]["status"] == "passed"
    case = json.loads((world / "shamaos" / "runtime_case.json").read_text())
    reader = NativeWorldReader(world)
    for block in case["blocks"]:
        assert reader.get_block(Vec3(*block["pos"])) == BlockState.of(block["name"], **block["properties"])


def test_saved_world_fixture_runs_in_actual_mchprs(tmp_path):
    runner = os.environ.get("SHAMA_MCHPRS_RUNNER")
    if not runner or not Path(runner).is_file():
        pytest.skip("actual MCHPRS runner unavailable; saved-world runtime gate remains unexecuted")
    world = tmp_path / "runtime-world"
    assert main(["fixture-world", "--world", str(world), "--runtime-runner", runner]) == 0
    report = json.loads((world / "shamaos" / "fixture.json").read_text())
    assert report["runtime_validation"]["status"] == "passed"
    assert report["runtime_validation"]["runtime"] == "MCHPRS redstone"
    assert report["runtime_validation"]["steps"] == 3


def test_full_generation_checks_mapped_routes_before_creating_world(tmp_path, monkeypatch):
    """A fitting storage estimate cannot hide an unaffordable mapped router."""
    from types import SimpleNamespace
    from shamaos import generator, system_build
    from shamaos.hardware.external_router import ExternalNet

    config = generator.load_config(Path(__file__).resolve().parents[1] / "configs/default.toml")
    config["generator"]["build_dir"] = str(tmp_path / "build")
    world = tmp_path / "must-not-exist"
    physical = SimpleNamespace(
        top="test", cell_types={}, cell_origins={}, input_ports={}, output_ports={},
        constant_high=(), nets=(), routing_branch_y=286, routing_trunk_y=290,
        track_pitch=4,
    )
    nets = tuple(
        ExternalNet(str(i), Vec3(i * 8, 64, 0), (Vec3(i * 8 + 4, 64, 0),))
        for i in range(1000)
    )
    prepared = SimpleNamespace(
        logic=SimpleNamespace(partitions=(), shell=physical), nets=nets,
        routing=SimpleNamespace(access={}, endpoint_bounds={}),
    )
    storage = {
        "capacity_contract": {"status": "passed"}, "storage_chunks": 10,
        "minimum_storage_region_bytes": 40960, "available_disk_bytes": 1 << 20,
        "storage_minimum_fits_available_disk": True,
    }
    monkeypatch.setattr(generator, "generation_preflight", lambda *a, **kw: storage)
    monkeypatch.setattr(system_build, "prepare_system", lambda *a, **kw: prepared)
    with pytest.raises(WorldWriteError, match="mapped logic/routing Anvil lower bound"):
        generator.write_available_placements(world, config, generator.build_plan(config))
    assert not world.exists()
    report = json.loads((tmp_path / "build" / "mapped-resource-preflight.json").read_text())
    assert report["generation_gate"] == "blocked-by-proven-disk-lower-bound"
    assert report["whole_machine_exact_chunks"] is None
