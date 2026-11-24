"""Physical endpoint and scenario tests. The saved fixture has no CPU."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess

import pytest

from shamaos.hardware.controls import control_actuators, iter_controls, keyboard_buttons
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
    positions = set(controls.values()) | {p for row in keyboard_buttons(ORIGIN) for p in row}
    positions.update([controls["POWER"].offset(dx=1), controls["EDITOR"].offset(dx=1),
                      controls["HOME"].offset(dz=-1)])
    with NativeWorldWriter(world, surface_y=-61, spawn_platform=False) as writer:
        items = [p for p in iter_controls(ORIGIN) if p.pos in positions]
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


def test_program_types_every_byte_through_saved_keys_and_checks_execution(endpoints, monkeypatch, tmp_path):
    from firmware_machine import FirmwareMachine
    from shamaos import native_console_scenario as console
    from shamaos.assembler import assemble
    from shamaos.simulator import CPUReference

    baseline = "a" * 64
    world, metadata = endpoints
    power = control_actuators(ORIGIN)["POWER"]

    def empty_targets(source_world, actual_manifest, power_position):
        assert source_world == world and actual_manifest == metadata and power_position == power
        return baseline

    monkeypatch.setattr(console, "require_new_program_files", empty_targets)
    options = BUDGETS | {"action_ticks": 220, "key_ticks": 240, "run_ticks": 270, "shutdown_ticks": 310}
    scenario = console.build_program_scenario(world, metadata, **options)
    assert scenario["shamaos_initial_flash_sha256"] == baseline
    assert scenario["inputs"] == {} and scenario["capture_trace"] is False
    buttons = keyboard_buttons(ORIGIN)
    chars = [(1,3),(0,3),(1,0),(4,4),(2,1),(3,3),(4,4),(3,6),(3,4),(4,5),(0,7),(1,3),(2,3),(4,5)]
    controls = control_actuators(ORIGIN)
    expected = [controls[n] for n in ("POWER", "EDITOR", "DOWN", "A")]
    expected += [buttons[3][1], buttons[4][5], *(buttons[r][c] for r,c in chars), controls["B"]]
    expected += [controls["DOWN"]] * 5 + [controls[n] for n in ("A", "HOME", "POWER")]
    steps = scenario["steps"]
    assert [step["use_blocks"] for step in steps[1:]] == [[[p.x, p.y, p.z]] for p in expected]
    assert all("drive" not in step for step in steps)
    for length, step in enumerate(steps[7:7+len(chars)], 1):
        assert step["ticks"] == 240
        assert _decode(step["expect"], "debug_registers", 32*7, 32) == length
    for selection, step in enumerate(steps[-8:-3], 1):
        assert _decode(step["expect"], "debug_registers", 32*9, 32) == selection
    run, home, off = (step["expect"] for step in steps[-3:])
    assert _decode(run, "debug_app", 0, 4) == 15 and run["halted[0]"]
    assert _decode(run, "debug_registers", 32, 32) == 42
    assert _decode(run, "debug_cache_used", 0, 32) == 12
    assert _decode(home, "debug_app", 0, 4) == 0 and not home["halted[0]"]
    assert _decode(home, "debug_registers", 8*32, 32) == 1
    assert not off["physical_power"] and not steps[0]["expect"]["physical_power"]
    assert all(step["expect"]["physical_power"] for step in steps[1:-1])
    assert _decode(off, "debug_lifecycle", 0, 32) == _decode(off, "debug_cache_used", 0, 32) == 0
    assert assemble(console.PROGRAM_SOURCE.decode()).to_bytes() == console.PROGRAM_BINARY
    # Independently execute the shipped Editor firmware after launch. Decode
    # the scenario's saved controls rather than injecting the expected source.
    editor = FirmwareMachine("editor")
    editor.settle()
    codes = {controls["A"]: 0x14, controls["B"]: 0x15, controls["DOWN"]: 0x11}
    rows = ("ABCDEFGH", "IJKLMNOP", "QRSTUVWX", "YZ012345", "6789 \r")
    keycodes = {buttons[r][c]: ord(ch) for r, chars in enumerate(rows) for c, ch in enumerate(chars)}
    for step in steps[3:-2]:
        p = Vec3(*step["use_blocks"][0])
        if p in codes:
            editor.event(codes[p])
        else:
            editor.event(1, keycodes[p])
        if step["expect"].get("halted[0]"):
            break  # The host firmware harness records program handoff.
        for reg in (7, 8, 9, 11):
            if f"debug_registers[{reg * 32}]" in step["expect"]:
                assert editor.regs[reg] == _decode(step["expect"], "debug_registers", reg*32, 32)
    assert editor.fs.read_file("Z.ASM") == console.PROGRAM_SOURCE
    assert editor.pending_binary == editor.fs.read_file("Z.BIN") == console.PROGRAM_BINARY
    cpu = CPUReference()
    cpu.load_program([int.from_bytes(editor.pending_binary[i:i+4], "little") for i in range(0, 12, 4)])
    cpu.run(3)
    assert cpu.halted and cpu.regs[1] == 42
    result = tmp_path / "program.json"
    args = [str(world.parent), str(result), "--program"]
    for name, ticks in options.items():
        args += ["--" + name.replace("_", "-"), str(ticks)]
    assert main(args) == 0 and json.loads(result.read_text()) == scenario


def test_program_requires_settling_budget_and_actual_keyboard(endpoints, tmp_path):
    from shamaos.native_console_scenario import build_program_scenario
    opts = BUDGETS | {"action_ticks": 210, "key_ticks": 210, "run_ticks": 1000, "shutdown_ticks": 2000}
    with pytest.raises(ValueError, match="119"):
        build_program_scenario(*endpoints, **(opts | {"key_ticks": 100}))
    world, metadata = _candidate(tmp_path, replace_at=(keyboard_buttons(ORIGIN)[4][5], BlockState.of("minecraft:stone")))
    with pytest.raises(WorldWriteError, match="keyboard key"):
        build_program_scenario(world, metadata, **opts)
