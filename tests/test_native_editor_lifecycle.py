"""Bounded saved-world and firmware checks for the lifecycle runner. No complete PC is simulated here."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from shamaos import native_editor_lifecycle as editor
from shamaos import native_lifecycle_topology as topology
from shamaos.flashfs import ShamaFS
from shamaos.generator import build_plan, load_config, manifest
from shamaos.hardware.clock import system_clock_origin
from shamaos.hardware.controls import control_actuators, keyboard_buttons
from shamaos.hardware.display import LampPanelSpec, panel_pixel
from shamaos.hardware.memory import repeater
from shamaos.mchprs_import import digest
from shamaos.model import BlockState, Placement, Vec3
from shamaos.native_title_crop import CROP_X, CROP_Y
from shamaos.os_image import build_default_os_image
from shamaos.release_acceptance import _FULL_WORLD_VALIDATORS
from shamaos.worldio import NativeWorldReader, NativeWorldWriter, WorldWriteError, resolve_world_surface, validate_native_world
from tools import run_native_editor_lifecycle as lifecycle


def fixture_world(world):
    origin = Vec3(16, -55, 32)
    support = Placement(origin, BlockState.of("minecraft:stone"), "support")
    lever = Placement(origin.offset(dy=1), BlockState.of("minecraft:lever", face="floor", facing="north", powered="false"), "lever")
    with NativeWorldWriter(world, surface_y=-61, spawn_platform=False) as writer:
        writer.set_blocks((support, lever))
    return support, lever


def test_source_coverage_rejects_missing_extra_and_wrong_native_states(tmp_path):
    world = tmp_path / "world"
    support, lever = fixture_world(world)
    native = validate_native_world(world)
    result = topology.check_placements(world, [("fixture", [support, support, lever])], native, tmp_path)
    assert result["counts"] == {"fixture": 3}
    assert result["literal_union"]["literal_unique_blocks"] == 2
    with pytest.raises(WorldWriteError, match="assignments or chunks"):
        topology.check_placements(world, [("fixture", [support])], native, tmp_path)
    for bad in [Placement(lever.pos, support.block, "wrong"), Placement(lever.pos.offset(dy=1), lever.block, "missing")]:
        with pytest.raises(WorldWriteError, match="Missing or different"):
            topology.check_placements(world, [("fixture", [support, bad])], native, tmp_path)


@pytest.fixture
def spec():
    cfg = resolve_world_surface(load_config(lifecycle.ROOT / "configs/default.toml"), None)
    cfg["generator"]["logic_backend"] = "bounded"
    return cfg, manifest(cfg, build_plan(cfg))


def test_plans_wrong_capacity_and_stale_sources_cannot_start_a_runtime(spec, monkeypatch):
    cfg, generated = spec
    commit = "a" * 40
    generated["source"].update(working_tree_dirty=False, base_revision=commit)
    monkeypatch.setattr(topology, "source_provenance", lambda: generated["source"])
    with pytest.raises(WorldWriteError, match="actual generated"):
        topology.check_manifest(generated, cfg, commit)
    with pytest.raises(WorldWriteError, match="capacity"):
        topology.check_manifest(generated | {"cache_bytes": 8192}, cfg, commit)
    with pytest.raises(WorldWriteError, match="source"):
        topology.check_manifest(generated, cfg, "b"*40)
    assert _FULL_WORLD_VALIDATORS == {}


def test_forged_full_labels_cannot_promote_small_native_world(spec, tmp_path):
    _, generated = spec
    world = tmp_path / "world"
    fixture_world(world)
    generated.update(physical_generation_status="generated-topology-readback-verified",
                     world_validation=validate_native_world(world))
    with pytest.raises(WorldWriteError, match="cannot contain"):
        topology.check_topology(world, {"world": {"surface_y": -61}}, generated, tmp_path / "audit")
    assert not (tmp_path / "audit").exists()


def test_saved_flash_checks_restored_types_bytes_and_pristine_source():
    initial = build_default_os_image().image
    before = hashlib.sha256(initial).hexdigest()
    fs = ShamaFS.deserialize(initial)
    for name, (kind, content) in editor.FILES.items():
        fs.create_file(name, content, kind)
    flash = fs.serialize()
    assert editor.check_saved_files(flash, before)["files"]["X.TXT"] == "414243"
    with pytest.raises(WorldWriteError, match="initial image"):
        editor.check_saved_files(flash, "0"*64)
    with pytest.raises(WorldWriteError, match="directory"):
        editor.check_saved_files(initial, before)
    fs.write_file("X.TXT", b"ABD")
    with pytest.raises(WorldWriteError, match="Incorrect saved X.TXT"):
        editor.check_saved_files(fs.serialize(), before)


def test_editor_and_reopen_use_separate_actual_controls_and_ram_bits(tmp_path, monkeypatch):
    from test_native_console_scenario import _candidate, BUDGETS, ORIGIN
    from shamaos import native_console_scenario as console
    world, meta = _candidate(tmp_path)
    panel = LampPanelSpec(pixel_pitch_x=3, pixel_pitch_y=2, orientation="upright")
    display = Vec3(2000, -54, 0)
    meta["physical_sections"].extend([
        {"name": "display", "kind": "latched-lamp-panel", "origin": vars(display), "width": panel.width,
         "height": panel.height, "physical_height": panel.physical_height, "physical_width": panel.physical_width,
         "physical_depth": panel.depth, "orientation": panel.orientation},
        {"name":"ram-tile-000", "first_bank_index":0, "banks":8, "layer_pitch":12,
         "origin":vars(Vec3(4000, -53, 0))}])
    meta["system_build"]["clock_origin"] = vars(system_clock_origin(ORIGIN))
    dark = BlockState.of("minecraft:redstone_lamp", lit="false")
    with NativeWorldWriter(world, surface_y=-61) as writer:
        for y in CROP_Y:
            for x in CROP_X:
                writer.set_block(Placement(panel_pixel(display, panel, row=y, col=x), dark, "crop"))
        for address in (editor.EDIT_BUF, editor.EDIT_CTX+20, editor.EDIT_NAME):
            bank = editor.bank_at(meta, "ram", address//4096)
            for bit in range(32):
                pos = bank.offset(2+bit*editor.BANK.bit_pitch, editor.BANK.cell_y, 3+address%4096//4*editor.BANK.word_pitch)
                writer.set_block(Placement(pos, repeater("east", locked=True), "RAM probe"))
    # This disconnected fixture has no flash. Full-world entry reads every generated placement,
    # the actual initial flash, and the actual restored flash; only test scenario construction here.
    monkeypatch.setattr(console, "require_new_program_files", lambda *args: "a"*64)
    options = BUDGETS | {"key_ticks":151, "action_ticks":189, "run_ticks":301, "shutdown_ticks":303,"shutdown_ack_ticks":151}
    run, reopen = editor.build_lifecycle_scenarios(world, meta, **options)
    buttons, keys = control_actuators(ORIGIN), keyboard_buttons(ORIGIN)
    names = [s["use_blocks"][0] for s in run["steps"] if "use_blocks" in s]
    points = lambda xs: [list((p.x,p.y,p.z)) for p in xs]
    assert names[:12] == points([buttons["POWER"], buttons["EDITOR"], buttons["A"],keys[2][7], keys[4][5],
                                 keys[0][0],keys[0][1],keys[0][2],buttons["HOME"], buttons["EDITOR"],
                                 buttons["DOWN"],buttons["A"]])
    assert [s["use_blocks"][0] for s in reopen["steps"] if "use_blocks" in s] == points(
        [buttons["POWER"],buttons["FILES"],keys[2][7],buttons["LEFT"],buttons["HOME"],buttons["POWER"]])
    assert all(scenario["inputs"] == {} and all("drive" not in step for step in scenario["steps"])
               for scenario in (run, reopen))
    buf = reopen["steps"][4]["expect"]
    assert bytes(sum(buf[f"text_bytes[{i*8 + b}]"] << b for b in range(8)) for i in range(3)) == b"ABC"
    assert reopen["steps"][0]["expect"]["physical_power"] is False
    assert all(f"text_bytes[{i}]" in reopen["outputs"] for i in range(32))
    assert run["steps"][-1]["expect"]["clock_hold"] is False
    assert reopen["steps"][-2]["expect"] == {"physical_power": False, "shutdown_ready": True}
    assert reopen["steps"][-2]["ticks"] + reopen["steps"][-1]["ticks"] == options["shutdown_ticks"]


    # Independently calibrate the observer values against the shipped firmware.
    # This instruction-level fixture stands apart from native-world evidence.
    from firmware_machine import FirmwareMachine
    machine = FirmwareMachine("editor")
    machine.settle()
    event = {tuple(points([buttons["A"]])[0]): (0x14, 0)}
    for ch, p in zip("X\rABC", [keys[2][7], keys[4][5], *keys[0][:3]], strict=True):
        event[tuple(points([p])[0])] = (1, ord(ch))
    for step in run["steps"][3:9]:
        machine.event(*event[tuple(step["use_blocks"][0])])
        for label, expected in step["expect"].items():
            prefix, _, subscript = label.partition("[")
            if prefix == "debug_registers":
                bit = int(subscript[:-1])
                value = bool(machine.regs[bit//32] & (1 << (bit%32)))
            elif prefix in ("text_bytes", "editor_dirty", "edit_name"):
                address = {"text_bytes": editor.EDIT_BUF, "editor_dirty": editor.EDIT_CTX+20,
                           "edit_name": editor.EDIT_NAME}[prefix]
                bit = int(subscript[:-1])
                value = bool(machine.memory[address + bit//8] & (1 << (bit%8)))
            else:
                continue
            assert value == expected, label

    fs = ShamaFS.deserialize(build_default_os_image().image)
    fs.create_file("X.TXT", b"ABC", editor.FILES["X.TXT"][0])
    files = FirmwareMachine("files", fs)
    files.settle()
    files.event(1, ord("X"))
    assert files.cstring(editor.EDIT_NAME) == "X.TXT"
    files.event(0x12)
    assert files.pending_app == 1
    files.load_app("editor")
    files.settle()
    assert (files.regs[7], files.regs[8], files.regs[11]) == (3, 3, 1)
    assert bytes(files.memory[editor.EDIT_BUF:editor.EDIT_BUF+3]) == b"ABC"


def test_actual_quiescent_save_reopen_and_snapshot_substitution_rejection(tmp_path):
    runner = Path(os.environ.get("SHAMA_MCHPRS_TERRAIN_RUNNER", "/missing"))
    if not runner.is_file():
        pytest.skip("actual pinned sparse terrain engine unavailable")
    world = tmp_path / "fixture"
    _, lever = fixture_world(world)
    pos = [lever.pos.x, lever.pos.y, lever.pos.z]
    def scenario(start):
        return {"inputs": {}, "outputs": {"lever": pos}, "initial_ticks": 16,
                "steps": [{"ticks": 0, "expect":{"lever": start}},
                          {"use_blocks":[pos], "ticks": 24, "expect":{"lever": not start}}]}
    if digest(runner) == lifecycle.REVIEWED_RUNNER_SHA256:
        assert lifecycle.reviewed_runner(runner) == runner.resolve()
    work = (tmp_path / "first").resolve()
    saved, first = lifecycle.execute_saved(world, scenario(False), work, runner, digest(runner), 30)
    assert dict(NativeWorldReader(saved).get_block(lever.pos).properties)["powered"] == "true"
    restored, second = lifecycle.execute_saved(saved, scenario(True), tmp_path / "second", runner, digest(runner), 30)
    assert dict(NativeWorldReader(restored).get_block(lever.pos).properties)["powered"] == "false"
    assert first["stream_sha256"] != second["stream_sha256"]
    raw, imported = [json.loads((work / p).read_bytes()) for p in ("runtime.json","import.json")]
    for update in ({"pending_ticks_after_scenario":1}, {"scenario_sha256":"b"*64},
                   {"kind":"caller-declared-pass"},{"loaded_chunks":False}):
        with pytest.raises(WorldWriteError, match="Native result"):
            lifecycle.check_result(raw | update, imported, scenario(False), first["scenario_sha256"],
                                   work / "snapshot.jsonl", digest(runner))
    wrong = deepcopy(raw)
    foreign = tmp_path / "second/snapshot.jsonl"
    wrong["snapshot"].update(path=str(foreign), bytes=foreign.stat().st_size, sha256=digest(foreign))
    with pytest.raises(WorldWriteError, match="Snapshot header"):
        lifecycle.check_result(wrong, imported, scenario(False), first["scenario_sha256"], foreign, digest(runner))
    with pytest.raises(subprocess.CalledProcessError):
        lifecycle.execute_saved(world, scenario(True), tmp_path / "negative", runner, digest(runner), 30)
    assert not (tmp_path / "negative/restored-world").exists()
    assert not (tmp_path / "acceptance.json").exists()


def test_untrusted_executable_and_unsupported_required_flow_cannot_supply_pass(tmp_path):
    caller = tmp_path / "caller"
    caller.write_text('{"status":"passed"}')
    with pytest.raises(WorldWriteError, match="Unrecognized native runner"):
        lifecycle.reviewed_runner(caller)
    with pytest.raises(SystemExit) as err:
        lifecycle.main(["candidate", str(tmp_path/"output"), "--runner", str(caller),
                        "--require-flow", "mining-pool-validation"])
    assert err.value.code == 2
    assert not (tmp_path/"output").exists()
    assert not _FULL_WORLD_VALIDATORS


@pytest.mark.parametrize("changed", ["scenario", "world"])
def test_native_execution_rejects_byte_identical_edit_and_restore(tmp_path, monkeypatch, changed):
    runner = Path(os.environ.get("SHAMA_MCHPRS_TERRAIN_RUNNER", "/missing"))
    if not runner.is_file():
        pytest.skip("actual terrain runner unavailable")
    world = tmp_path / "fixture"
    _, lever = fixture_world(world)
    pos = [lever.pos.x, lever.pos.y, lever.pos.z]
    scenario = {"inputs": {}, "outputs": {"lever": pos}, "initial_ticks": 8,
                "steps": [{"ticks": 0, "expect":{"lever": False}}]}
    execute = subprocess.run
    def mutate_after_native(command, **kwargs):
        result = execute(command, **kwargs)
        path = Path(command[2]) if changed == "scenario" else world / "level.dat"
        original = path.read_bytes()
        stamp = path.stat()
        path.write_bytes(b"temporarily different")
        path.write_bytes(original)
        os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        assert path.read_bytes() == original
        return result
    monkeypatch.setattr(lifecycle.subprocess, "run", mutate_after_native)
    with pytest.raises(WorldWriteError, match="changed during"):
        lifecycle.execute_saved(world, scenario, tmp_path / "proof", runner, digest(runner), 30)
    assert not (tmp_path / "proof/restored-world").exists()


def test_tracked_source_rejects_reverted_input_even_when_git_clean(tmp_path):
    repo = tmp_path / "repo"
    subprocess.run(["git","init","-q",str(repo)], check=True)
    source = repo / "physical.sv"
    source.write_text("original")
    subprocess.run(["git","add","physical.sv"], cwd=repo, check=True)
    stamps = lifecycle.tracked_stamps(repo)
    before = source.stat()
    source.write_text("temporary")
    if sys.platform == "win32":
        # Windows Python interprets st_ctime as creation time. Exercise file
        # identity there; native restore itself requires atomic Linux/macOS APIs.
        replacement = repo / "replacement"
        replacement.write_text("original")
        replacement.replace(source)
    else:
        source.write_text("original")
    os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert not subprocess.check_output(["git","diff"], cwd=repo)
    with pytest.raises(WorldWriteError, match="changed during"):
        lifecycle.require_stable(lifecycle.tracked_stamps(repo), stamps, "Source mapping")


def test_initial_native_comparator_rejects_non_generated_analog_level(tmp_path):
    import nbtlib
    from shamaos.hardware.memory import comparator
    from shamaos.worldio import _Region

    world = tmp_path / "comparator"
    point = Vec3(1, -55, 1)
    with NativeWorldWriter(world, surface_y=-61, spawn_platform=False) as writer:
        writer.set_block(Placement(point, comparator("east", powered=True), "fixture"))
    readback = validate_native_world(world)
    assert topology.audit_native_contents(world, -61) == (1024, 1)
    region = _Region(world / "region/r.0.0.mca")
    root = region.read(0)
    entities = root["block_entities"]
    assert len(entities) == 1 and int(entities[0]["OutputSignal"]) == 15
    entities[0]["OutputSignal"] = nbtlib.Int(7)
    region.write(0, root)
    assert validate_native_world(world)["assigned_blocks"] == readback["assigned_blocks"]
    with pytest.raises(WorldWriteError, match="comparator"):
        topology.audit_native_contents(world, -61)
