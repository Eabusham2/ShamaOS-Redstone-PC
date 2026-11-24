"""Physical endpoint and scenario tests. The saved fixture has no CPU."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess

import pytest

from shamaos.hardware.controls import control_actuators, iter_controller_panel
from shamaos.hardware.debug import debug_ports, iter_debug_panel
from shamaos.mchprs_import import digest, export_world
from shamaos.model import BlockState, Placement, Vec3
from shamaos.native_console_scenario import build_console_scenario, main
from shamaos.os_image import build_default_os_image
from shamaos.worldio import NativeWorldReader, NativeWorldWriter, WorldWriteError


ORIGIN = Vec3(-96, -42, 50)
BUDGETS = {"initial_ticks": 5, "boot_ticks": 7, "editor_ticks": 11, "home_ticks": 13}


def _candidate(path, *, replace_at=None):
    world = path / "world"
    panel = ORIGIN.offset(dz=-224)
    pins = debug_ports(panel)
    metadata = {"physical_sections": [{"name": "input", "kind": "keyboard-controller", "origin": vars(ORIGIN)}],
                "system_build": {"debug_panel": {name: list(map(vars, row)) for name, row in pins.items()}}}
    # Save the real actuator, support and lamp placements, with no circuit
    # connecting them. Such a world must fail the first post-power assertion.
    controls = control_actuators(ORIGIN)
    positions = {controls[name] for name in ("POWER", "EDITOR", "HOME")}
    positions.update([controls["POWER"].offset(dx=1), controls["EDITOR"].offset(dx=1),
                      controls["HOME"].offset(dz=-1)])
    with NativeWorldWriter(world, surface_y=-61, spawn_platform=False) as writer:
        items = [p for p in iter_controller_panel(ORIGIN) if p.pos in positions]
        items.extend(p for p in iter_debug_panel(panel) if p.block.name == "minecraft:redstone_lamp")
        for p in items:
            if replace_at and p.pos == replace_at[0]:
                p = Placement(p.pos, replace_at[1], p.component)
            writer.set_block(p)
    (path / "generated-manifest.json").write_text(json.dumps(metadata))
    return world, metadata


@pytest.fixture(scope="module")
def endpoints(tmp_path_factory):
    return _candidate(tmp_path_factory.mktemp("saved-console-endpoints"))


def _decode(values, field, start, count):
    return sum(int(values[f"{field}[{bit}]"]) << index for index, bit in enumerate(range(start, start + count)))


def test_boot_editor_and_home_require_executing_apps_on_visible_lamps(endpoints):
    world, metadata = endpoints
    scenario = build_console_scenario(world, metadata, **BUDGETS)
    assert scenario["inputs"] == {} and not scenario["capture_trace"]
    assert scenario["initial_ticks"] == 5
    actions = control_actuators(ORIGIN)
    steps = scenario["steps"]
    assert [s["ticks"] for s in steps] == [0, 7, 11, 13]
    assert "use_blocks" not in steps[0]
    assert [s["use_blocks"] for s in steps[1:]] == [
        [[actions[name].x, actions[name].y, actions[name].z]] for name in ("POWER", "EDITOR", "HOME")]
    assert all("drive" not in s for s in steps)
    reader = NativeWorldReader(world)
    outputs = scenario["outputs"]
    assert len(outputs) == len(set(map(tuple, outputs.values()))) == 110
    for name, coords in outputs.items():
        assert reader.get_block(Vec3(*coords)).name == "minecraft:redstone_lamp"
        field, bit = name[:-1].split("[")
        assert Vec3(**metadata["system_build"]["debug_panel"][field][int(bit)]).offset(dz=-3) == Vec3(*coords)
    image = build_default_os_image()
    # Independently obtain executable lengths from the two serialized flash records.
    lengths = [int.from_bytes(image.image[addr:addr+4], "little") for addr in
               (image.slot_flash_offsets[app] for app in ("desktop", "editor"))]
    expected = [(0, 0, 0), (0, lengths[0], 1), (1, lengths[1], 0), (0, lengths[0], 1)]
    for step, (app_id, cache, r8) in zip(steps, expected, strict=True):
        values = step["expect"]
        assert set(values) == set(outputs)
        assert _decode(values, "debug_app", 0, 4) == app_id
        assert _decode(values, "debug_cache_used", 0, 32) == min(cache, 4096)
        assert _decode(values, "debug_registers", 256, 32) == r8
        assert _decode(values, "debug_registers", 352, 32) == int(app_id == 1)
        assert _decode(values, "debug_fault", 0, 8) == 0
        assert not values["input_overflow[0]"] and not values["halted[0]"]


@pytest.mark.parametrize("mutation", ["short", "missing", "reordered", "fractional", "duplicate-section"])
def test_reject_incomplete_or_mislabelled_manifest(endpoints, mutation):
    world, metadata = endpoints
    metadata = deepcopy(metadata)
    pins = metadata["system_build"]["debug_panel"]
    if mutation == "short":
        pins["debug_cache_used"].pop()
    elif mutation == "missing":
        del pins["debug_fault"]
    elif mutation == "reordered":
        pins["debug_app"].reverse()
    elif mutation == "fractional":
        pins["debug_fault"][0]["y"] = 1.5
    else:
        metadata["physical_sections"] *= 2
    with pytest.raises(WorldWriteError):
        build_console_scenario(world, metadata, **BUDGETS)


@pytest.mark.parametrize("name,state", [
    ("POWER", BlockState.of("minecraft:lever", face="wall", facing="west", powered="true")),
    ("EDITOR", BlockState.of("minecraft:lever", face="wall", facing="west", powered="false")),
    ("debug_cache_used", BlockState.of("minecraft:stone")),
])
def test_reject_wrong_saved_blocks(tmp_path, name, state):
    point = (debug_ports(ORIGIN.offset(dz=-224))[name][0].offset(dz=-3)
             if name == "debug_cache_used" else control_actuators(ORIGIN)[name])
    world, metadata = _candidate(tmp_path, replace_at=(point, state))
    with pytest.raises(WorldWriteError, match=name):
        build_console_scenario(world, metadata, **BUDGETS)


@pytest.mark.parametrize("phase,value", [
    ("initial_ticks", 0), ("boot_ticks", True), ("editor_ticks", -5),
    ("home_ticks", 1.5), ("boot_ticks", 2**64-1),
])
def test_requires_explicit_positive_runnable_tick_counts(endpoints, phase, value):
    with pytest.raises(ValueError, match="phase budgets"):
        build_console_scenario(*endpoints, **(BUDGETS | {phase: value}))


def test_cli_emits_only_the_scenario_and_preserves_an_existing_output(endpoints, tmp_path):
    world, metadata = endpoints
    output = tmp_path / "console.json"
    args = [str(world.parent), str(output), "--initial-ticks", "5", "--boot-ticks", "7",
            "--editor-ticks", "11", "--home-ticks", "13"]
    assert main(args) == 0
    assert json.loads(output.read_text()) == build_console_scenario(world, metadata, **BUDGETS)
    output.write_text("existing")
    with pytest.raises(SystemExit, match="1"):
        main(args)
    assert output.read_text() == "existing"
    assert not (world.parent / "acceptance.json").exists()


def test_actual_sparse_engine_rejects_disconnected_console_at_boot(endpoints, tmp_path):
    runner = os.environ.get("SHAMA_MCHPRS_TERRAIN_RUNNER")
    if not runner or not Path(runner).is_file():
        pytest.skip("pinned terrain-aware sparse redstone runner unavailable")
    world, metadata = endpoints
    stream = tmp_path / "world.jsonl"
    export_world(world, stream)
    scenario = build_console_scenario(world, metadata, **BUDGETS)
    scenario_file = tmp_path / "console.json"
    scenario_file.write_text(json.dumps(scenario))
    run = subprocess.run([runner, str(stream), str(scenario_file), "512"], text=True,
                         capture_output=True, timeout=90)
    assert run.returncode != 0
    assert "step=1 tick=7" in run.stderr, run.stderr
    assert '"status":"passed"' not in run.stdout

    # Calibrate the same world/import/runner through the actual POWER lever.
    # Leaving the CPU expectations in place above is what detects the missing CPU.
    power = scenario["steps"][1]["use_blocks"][0]
    probe = {"inputs": {}, "outputs": {"power": power}, "initial_ticks": 5,
             "steps": [{"ticks": 0, "expect": {"power": False}},
                       {"use_blocks": [power], "ticks": 7, "expect": {"power": True}}]}
    scenario_file.write_text(json.dumps(probe))
    run = subprocess.run([runner, str(stream), str(scenario_file), "512"], text=True,
                         capture_output=True, timeout=90)
    assert run.returncode == 0, run.stderr
    report = json.loads(run.stdout)
    assert report["status"] == "passed" and report["assertions"] == 2
    assert report["imported_stream_sha256"] == digest(stream)
    assert report["scenario_sha256"] == digest(scenario_file)
    assert report["complete_machine_runtime_verified"] is False
