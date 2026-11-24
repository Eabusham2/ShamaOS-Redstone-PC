"""Visible title assertions, real kernel/GPU presentations and native lamp fixtures."""
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from shamaos import native_console_scenario as console, native_title_crop as title
from shamaos.generator import build_plan, load_config, manifest
from shamaos.hardware.display import LAMP_OFF
from shamaos.mchprs_import import digest, export_world
from shamaos.model import BlockState, Placement, Vec3
from shamaos.worldio import NativeWorldWriter, WorldWriteError
from test_native_console_scenario import BUDGETS, _candidate

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def presents(tmp_path_factory):
    if not shutil.which("iverilog") or not shutil.which("vvp"):
        pytest.skip("Icarus Verilog required for serial GPU title presentations")
    output = tmp_path_factory.mktemp("kernel-title-rtl") / "render"
    sources = ["shama_kernel_accel.sv", "shama_gpu.sv", "shama_font5x7.sv", "tb_kernel_title_crop.sv"]
    subprocess.run(["iverilog", "-g2012", "-I" + str(ROOT / "rtl"), "-s", "tb_kernel_title_crop",
                    "-o", str(output), *(str(ROOT / "rtl" / s) for s in sources)],
                   check=True, capture_output=True, text=True, timeout=30)
    run = subprocess.run(["vvp", str(output)], check=True, capture_output=True, text=True, timeout=120)
    assert "KERNEL TITLE CROP PASS" in run.stdout
    frames = [{} for _ in range(5)]
    for line in run.stdout.splitlines():
        if not line.startswith("TITLE "):
            continue
        _, phase, y, bits = line.split()
        assert int(y) in title.CROP_Y and len(bits) == len(title.CROP_X) and set(bits) <= {"0", "1"}
        for x, bit in zip(title.CROP_X, bits, strict=True):
            name = f"title[{x},{y}]"
            assert name not in frames[int(phase)]
            frames[int(phase)][name] = bit == "1"
    return frames


@pytest.fixture(scope="module")
def production_section():
    config = load_config(ROOT / "configs/default.toml")
    sections = manifest(config, build_plan(config))["physical_sections"]
    return next(section for section in sections if section["name"] == "display")


def write_crop(world, section, pixels, *, omit=None, stale=None):
    origin = Vec3(**section["origin"])
    with NativeWorldWriter(world, spawn_platform=False, surface_y=-61) as writer:
        for y in range(3, 12):
            for x in range(3, 197):
                if (x, y) == omit:
                    continue
                # Independent top-down mapping for the saved upright fixture.
                point = origin.offset(x * 3, (179 - y) * 2, 0)
                on = pixels[f"title[{x},{y}]"] or (x, y) == stale
                lamp = BlockState.of("minecraft:redstone_lamp", lit="true") if on else LAMP_OFF
                writer.set_block(Placement(point, lamp, "title-fixture"))
                if on:
                    writer.set_block(Placement(point.offset(dz=1), BlockState.of("minecraft:redstone_block"), "fixture-power"))


@pytest.fixture(scope="module")
def cold_candidate(tmp_path_factory, production_section):
    path = tmp_path_factory.mktemp("cold-console-title")
    world, metadata = _candidate(path)
    metadata["physical_sections"].append(production_section)
    (path / "generated-manifest.json").write_text(json.dumps(metadata))
    write_crop(world, production_section, title.title_pixels(""))
    return world, metadata


def test_title_crop_matches_every_pixel_from_four_real_kernel_gpu_presentations(presents):
    assert len(title.CROP_X) * len(title.CROP_Y) == 1746
    assert presents == [title.title_pixels(text) for text in
                        ("", "DESKTOP > EDITOR", "EDITOR > NEW TEXT", "EDITOR > NEW PROGRAM", "DESKTOP > EDITOR")]
    assert [sum(frame.values()) for frame in presents] == [0, 208, 206, 274, 208]
    assert presents[1] != presents[2] and presents[2] != presents[3]
    for frame in presents:
        for x in title.CROP_X:
            assert not frame[f"title[{x},3]"] and not frame[f"title[{x},11]"]
        for y in title.CROP_Y:
            assert not frame[f"title[3,{y}]"] and not frame[f"title[196,{y}]"]


@pytest.mark.parametrize("program", [False, True])
def test_cli_keeps_all_original_actions_and_adds_only_the_four_visible_checks(cold_candidate, tmp_path, monkeypatch, program):
    world, metadata = cold_candidate
    phases = BUDGETS.copy()
    args = [str(world.parent), str(tmp_path / "scenario.json"), "--titles"]
    if program:
        # This fixture contains console/display endpoints only; the independent
        # physical-flash reader and baseline refusals are covered separately.
        monkeypatch.setattr(console, "require_new_program_files", lambda *unused: "0" * 64)
        phases.update(key_ticks=240, action_ticks=240, run_ticks=700, shutdown_ticks=280)
        args.append("--program")
    for key, value in phases.items():
        args.extend(["--" + key.replace("_", "-"), str(value)])
    assert console.main(args) == 0
    scenario = json.loads((tmp_path / "scenario.json").read_text())
    build = console.build_program_scenario if program else console.build_console_scenario
    original = build(world, metadata, **phases)
    assert scenario["inputs"] == {} and scenario["capture_trace"] is False
    new_outputs = {name: value for name, value in scenario["outputs"].items() if name not in original["outputs"]}
    assert len(new_outputs) == len(set(map(tuple, new_outputs.values()))) == 1746
    origin = Vec3(**next(section["origin"] for section in metadata["physical_sections"] if section["name"] == "display"))
    for x in title.CROP_X:
        for y in title.CROP_Y:
            assert new_outputs[f"title[{x},{y}]"] == [origin.x+3*x, origin.y+2*(179-y), origin.z]
    home = len(original["steps"]) - (2 if program else 1)
    known = {0: "", 1: "DESKTOP > EDITOR", 2: "EDITOR > NEW TEXT", home: "DESKTOP > EDITOR"}
    for index, (step, prior) in enumerate(zip(scenario["steps"], original["steps"], strict=True)):
        expected = prior["expect"] | (title.title_pixels(known[index]) if index in known else {})
        assert step == dict(prior, expect=expected)


@pytest.mark.parametrize("defect", ["missing-lamp", "prelit-guard", "horizontal", "duplicate", "wrong-height"])
def test_display_source_rejects_missing_stale_or_misdeclared_lamps(tmp_path, production_section, defect):
    section = deepcopy(production_section)
    sections = [section]
    if defect in ("missing-lamp", "prelit-guard"):
        write_crop(tmp_path, section, title.title_pixels(""), omit=(3, 3) if defect == "missing-lamp" else None,
                   stale=(3, 3) if defect == "prelit-guard" else None)
    elif defect == "horizontal":
        section["orientation"] = "horizontal"
    elif defect == "duplicate":
        sections.append(section)
    else:
        section["physical_height"] += 1
    with pytest.raises(WorldWriteError, match="saved monitor lamp|upright 320x180"):
        title._saved_title_lamps(tmp_path, {"physical_sections": sections})


@pytest.mark.parametrize("frame,next_title,extra_light", [
    (1, "", False), (1, "EDITOR > NEW TEXT", False),
    (3, "EDITOR > NEW TEXT", False), (2, "EDITOR > NEW TEXT", True),
], ids=["pre-power-not-blank", "stale-desktop", "wrong-editor-selection", "lit-guard"])
def test_native_engine_rejects_incorrect_visible_frame(
        tmp_path, presents, production_section, cold_candidate, frame, next_title, extra_light):
    engine = os.getenv("SHAMA_MCHPRS_TERRAIN_RUNNER")
    if not engine or not Path(engine).is_file():
        pytest.skip("pinned terrain-aware sparse redstone runner required")
    # Materialize the actual RTL presentation as a static lamp fixture. The
    # runner must sample its real blocks. This fixture has no CPU or GPU.
    world = tmp_path / "frame"
    observed = presents[frame].copy()
    if extra_light:
        observed["title[3,3]"] = True
    write_crop(world, production_section, observed)
    stream, scenario_file = tmp_path / "saved.jsonl", tmp_path / "scenario.json"
    export_world(world, stream)
    scenario = {"inputs": {}, "outputs": title._saved_title_lamps(*cold_candidate), "initial_ticks": 2,
                "capture_trace": False, "steps": [{"ticks": 1, "expect": observed}]}
    def run():
        scenario_file.write_text(json.dumps(scenario))
        return subprocess.run([engine, str(stream), str(scenario_file), "1024"], text=True,
                              capture_output=True, timeout=90)
    positive = run()
    assert positive.returncode == 0, positive.stderr
    report = json.loads(positive.stdout)
    assert report["status"] == "passed" and report["assertions"] == 1746
    assert report["scenario_sha256"] == digest(scenario_file) and report["imported_stream_sha256"] == digest(stream)
    assert report["complete_machine_runtime_verified"] is False
    scenario["steps"].append({"ticks": 1, "expect": title.title_pixels(next_title)})
    negative = run()
    assert negative.returncode and "step=1" in negative.stderr and "title[" in negative.stderr, negative.stderr
