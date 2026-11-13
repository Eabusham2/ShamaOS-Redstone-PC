"""Exact native Anvil imports execute in one all-loaded actual-engine world."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import shutil
import subprocess

import nbtlib
import pytest

from shamaos.generator import create_runtime_fixture_world, write_placement_stages
from shamaos.hardware.appearance import AppearancePlan
from shamaos.hardware.controls import iter_controls
from shamaos.hardware.display import LampPanelSpec, iter_lamp_panel, panel_pixel, panel_ports, panel_row_inverted
from shamaos.mchprs_import import digest, export_world
from shamaos.memory_fixture import create_stacked_runtime_fixture_world
from shamaos.model import BlockState, Placement, Vec3
from shamaos.worldio import NativeWorldReader, NativeWorldWriter, WorldWriteError, _Region
from test_appearance import region_for
from test_mchprs_runtime import assert_compact_console_trace, compact_console_case
from test_upright_display import _panel_steps


def _runner(variable):
    path = os.environ.get(variable)
    if not path or not Path(path).is_file():
        pytest.skip(f"{variable} unavailable; native all-loaded runtime is unverified")
    return path


def _coords(point):
    return [point.x, point.y, point.z]


def _scenario(case, y_translation=0):
    result = {key: copy.deepcopy(value) for key, value in case.items() if key != "blocks"}
    result["capture_trace"] = True
    for field in ("inputs", "outputs"):
        result[field] = {name: [p[0], p[1] - y_translation, p[2]] for name, p in result.get(field, {}).items()}
    for step in result["steps"]:
        step["use_blocks"] = [[p[0], p[1] - y_translation, p[2]] for p in step.get("use_blocks", ())]
    return result


def _run_sparse(runner, stream, scenario, directory, limit=100000, *, succeeds=True, status="passed"):
    scenario_path = directory / "scenario.json"
    scenario_path.write_text(json.dumps(scenario, separators=(",", ":")))
    result = subprocess.run([runner, str(stream), str(scenario_path), str(limit)], text=True,
                            capture_output=True, timeout=60)
    if not succeeds:
        assert result.returncode != 0
        assert '"status":"passed"' not in result.stdout
        return result.stderr
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
    report = json.loads(result.stdout)
    assert report["status"] == status
    assert report["imported_stream_sha256"] == digest(stream)
    assert report["scenario_sha256"] == digest(scenario_path)
    assert report["complete_machine_runtime_verified"] is False
    assert report["decoded_nonair_blocks"] == report["initialized_nonair_blocks"]
    return report


def _dense_case(world, scenario):
    blocks = list(NativeWorldReader(world).iter_blocks())
    dx = max(0, 32 - min(p.pos.x for p in blocks))
    dz = max(0, 32 - min(p.pos.z for p in blocks))
    convert = lambda p: [p[0] + dx, p[1] + 64, p[2] + dz]
    case = copy.deepcopy(scenario)
    case["blocks"] = [{"pos": convert(_coords(p.pos)), "name": p.block.name,
                       "properties": dict(p.block.properties)} for p in blocks]
    for field in ("inputs", "outputs"):
        case[field] = {name: convert(p) for name, p in case.get(field, {}).items()}
    for step in case["steps"]:
        step["use_blocks"] = [convert(p) for p in step.get("use_blocks", ())]
    return case


def _compare_dense(world, scenario, sparse, *, runner=None):
    selected = _runner("SHAMA_MCHPRS_RUNNER") if runner is None else runner
    result = subprocess.run([selected], input=json.dumps(_dense_case(world, scenario)),
                            text=True, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
    reference = json.loads(result.stdout)
    assert sparse["traces"] == reference["traces"]
    return sum(map(len, sparse["traces"].values()))


def _record(directory, name, report):
    path = Path(os.environ.get("SHAMA_VALIDATION_REPORT_DIR", directory))
    path.mkdir(parents=True, exist_ok=True)
    compact = {key: value for key, value in report.items() if key != "traces"}
    (path / name).write_text(json.dumps(compact, indent=2) + "\n")


@pytest.fixture(scope="module")
def saved_logic(tmp_path_factory):
    directory = tmp_path_factory.mktemp("sparse-native-logic")
    world = directory / "world"
    create_runtime_fixture_world(world)
    scenario = _scenario(json.loads((world / "shamaos/runtime_case.json").read_text()))
    stream = directory / "world.jsonl"
    manifest = export_world(world, stream)
    return world, stream, scenario, manifest


@pytest.fixture(scope="module")
def saved_memory(tmp_path_factory):
    directory = tmp_path_factory.mktemp("sparse-native-memory")
    world = directory / "world"
    fixture = create_stacked_runtime_fixture_world(world)
    scenario = _scenario(json.loads((world / "shamaos/runtime_case.json").read_text()),
                         fixture["runtime_coordinate_translation_y"])
    stream = directory / "world.jsonl"
    manifest = export_world(world, stream)
    return world, stream, scenario, manifest


def test_native_export_covers_every_saved_section_terrain_and_comparator(saved_memory):
    world, stream, _, manifest = saved_memory
    records = [json.loads(line) for line in stream.read_text().splitlines()]
    assert records[0]["surface_y"] == -61
    assert records[0]["source"]["saved_native_pending_ticks"] == 0
    sections = [record for record in records if record["kind"] in ("section", "uniform_section")]
    chunks = {tuple(record["chunk"]) for record in sections}
    assert len(sections) == len(chunks) * 24 == manifest["sections"]
    for chunk in chunks:
        assert {record["y"] for record in sections if tuple(record["chunk"]) == chunk} == set(range(-4, 20))
    assert manifest["nonair"] == sum(1 for _ in NativeWorldReader(world).iter_blocks())
    assert manifest["comparators"] == len([record for record in records if record["kind"] == "comparator"]) == 192
    states = {entry["name"] for section in sections
              for entry in (section["palette"] if section["kind"] == "section" else [section["state"]])}
    assert {"minecraft:bedrock", "minecraft:dirt", "minecraft:grass_block"} <= states
    assert records[-1] == {"kind": "complete", **{key: manifest[key] for key in ("chunks", "sections", "nonair", "comparators")}}


def test_native_export_rejects_saved_pending_ticks_instead_of_dropping_them(saved_logic, tmp_path):
    world = tmp_path / "world"
    shutil.copytree(saved_logic[0], world)
    region = _Region(next((world / "region").glob("*.mca")))
    slot = next(region.locations())
    root = region.read(slot)
    root["block_ticks"] = nbtlib.List[nbtlib.Compound]([nbtlib.Compound({"i": nbtlib.String("minecraft:repeater")})])
    region.write(slot, root)
    destination = tmp_path / "world.jsonl"
    with pytest.raises(WorldWriteError, match="refusing to discard"):
        export_world(world, destination)
    assert not destination.exists()


def test_native_export_rejects_source_changes_during_read(saved_logic, tmp_path, monkeypatch):
    world = tmp_path / "world"
    shutil.copytree(saved_logic[0], world)
    original = _Region.read
    changed = False

    def read_then_change_source(region, slot):
        nonlocal changed
        result = original(region, slot)
        if not changed:
            changed = True
            path = world / "level.dat"
            path.write_bytes(path.read_bytes() + b"source-changed-during-export")
        return result

    monkeypatch.setattr(_Region, "read", read_then_change_source)
    destination = tmp_path / "world.jsonl"
    with pytest.raises(WorldWriteError, match="source changed during export"):
        export_world(world, destination)
    assert not destination.exists()


def test_actual_sparse_saved_logic_matches_every_dense_tick(saved_logic, tmp_path):
    world, stream, scenario, manifest = saved_logic
    result = _run_sparse(_runner("SHAMA_MCHPRS_SPARSE_RUNNER"), stream, scenario, tmp_path)
    assert result["loaded_chunks"] == manifest["chunks"]
    assert _compare_dense(world, scenario, result) == 128
    _record(tmp_path, "sparse-native-logic.json", result)


def test_uniform_encoding_preserves_every_imported_state_and_legacy_tick_trace(saved_logic, tmp_path):
    world, compact_stream, scenario, _ = saved_logic
    legacy_stream = tmp_path / "legacy.jsonl"
    legacy_manifest = export_world(world, legacy_stream, compact_sections=False)
    legacy_records = [json.loads(line) for line in legacy_stream.read_text().splitlines()]
    compact_records = [json.loads(line) for line in compact_stream.read_text().splitlines()]
    assert len(legacy_records) == len(compact_records)
    assert legacy_records[0]["schema"] == "shama-native-anvil-sections-v1"
    assert compact_records[0]["schema"] == "shama-native-anvil-sections-v2"
    assert legacy_records[0]["source"] == compact_records[0]["source"]
    for old, new in zip(legacy_records[1:], compact_records[1:]):
        if new["kind"] != "uniform_section":
            assert old == new
            continue
        assert old["kind"] == "section"
        assert (old["chunk"], old["y"], old["nonair"]) == (new["chunk"], new["y"], new["nonair"])
        bits = old["bits"]
        per_long = 64 // bits
        for index in range(4096):
            palette_id = (old["data"][index // per_long] >> ((index % per_long) * bits)) & ((1 << bits) - 1)
            assert old["palette"][palette_id] == new["state"]
    assert compact_stream.stat().st_size < legacy_manifest["stream_bytes"] // 2
    runner = _runner("SHAMA_MCHPRS_SPARSE_RUNNER")
    old = _run_sparse(runner, legacy_stream, scenario, tmp_path)
    new = _run_sparse(runner, compact_stream, scenario, tmp_path)
    assert old["traces"] == new["traces"]
    assert old["uniform_sections"] == 0 and new["uniform_sections"] > 0


def test_actual_uniform_nonair_section_preserves_all_solid_blocks(tmp_path):
    world = tmp_path / "world"
    with NativeWorldWriter(world, spawn=Vec3(0, 32, 0), spawn_platform=False) as writer:
        for x in range(16, 32):
            for y in range(16):
                for z in range(16, 32):
                    writer.set_block(Placement(Vec3(x, y, z), BlockState.of("minecraft:stone"), "uniform-solid-section"))
        assert writer.validate()["status"] == "passed"
    stream = tmp_path / "world.jsonl"
    manifest = export_world(world, stream)
    assert manifest["uniform_sections"] == manifest["sections"] == 24
    assert manifest["nonair"] == 4096
    scenario = dict(outputs={"solid": [20, 8, 20]}, inputs={}, initial_ticks=1,
                    steps=[{"ticks": 1, "expect": {"solid": False}}], capture_trace=True)
    result = _run_sparse(_runner("SHAMA_MCHPRS_SPARSE_RUNNER"), stream, scenario, tmp_path)
    assert result["decoded_nonair_blocks"] == 4096
    assert result["uniform_sections"] == 24


def test_actual_sparse_native_console_all_controls_and_distant_negative_coordinates(tmp_path):
    runner = _runner("SHAMA_MCHPRS_SPARSE_RUNNER")
    origin = Vec3(96, 32, 96)
    blocks = list(iter_controls(origin))
    world = tmp_path / "world"
    write_placement_stages(world, [("console", blocks)], spawn=Vec3(64, 32, 64),
                           appearance=AppearancePlan((region_for(blocks, "input"),), origin))
    outputs, steps, selected = compact_console_case(origin)
    scenario = dict(inputs={}, outputs={name: _coords(p) for name, p in outputs.items()},
                    initial_ticks=128, steps=steps, capture_trace=True)
    stream = tmp_path / "world.jsonl"
    manifest = export_world(world, stream)
    result = _run_sparse(runner, stream, scenario, tmp_path)
    assert_compact_console_trace(result, selected)
    assert _compare_dense(world, scenario, result) == 705000

    distant = tmp_path / "distant-world"
    dx, dz = -1_000_000, 1_000_000
    with NativeWorldWriter(distant, spawn=Vec3(64 + dx, 32, 64 + dz), spawn_platform=False) as writer:
        for p in NativeWorldReader(world).iter_blocks():
            writer.set_block(Placement(p.pos.offset(dx, 0, dz), p.block, "exact-relocation"))
        assert writer.validate()["status"] == "passed"
    relocated = copy.deepcopy(scenario)
    convert = lambda p: [p[0] + dx, p[1], p[2] + dz]
    relocated["outputs"] = {name: convert(p) for name, p in scenario["outputs"].items()}
    for step in relocated["steps"]:
        step["use_blocks"] = [convert(p) for p in step.get("use_blocks", ())]
    distant_stream = tmp_path / "distant-world.jsonl"
    distant_manifest = export_world(distant, distant_stream)
    far = _run_sparse(runner, distant_stream, relocated, tmp_path)
    assert far["traces"] == result["traces"]
    assert far["loaded_chunks"] == manifest["chunks"] == distant_manifest["chunks"] == 42
    assert far["decoded_nonair_blocks"] == result["decoded_nonair_blocks"]
    assert far["scheduled_ticks_total"] == result["scheduled_ticks_total"]
    _record(tmp_path, "sparse-native-console.json", result)
    _record(tmp_path, "sparse-native-console-distant.json", far)


def test_actual_sparse_upright_screen_preserves_all_lamps_every_tick(tmp_path):
    origin = Vec3(489, -54, 512)
    spec = LampPanelSpec(8, 4, 3, 2, "upright")
    world = tmp_path / "world"
    write_placement_stages(world, [("upright-panel", iter_lamp_panel(origin, spec=spec))], spawn=Vec3(464, -58, 480))
    updates = []
    for row, value in [(0, 0xA5), (1, 0x5A), (2, 0xFF), (3, 0), (0, 0), (3, 0xA5), (1, 0), (2, 0x5A)]:
        logical = [bool(value >> bit & 1) for bit in range(8)]
        physical = value ^ (255 if panel_row_inverted(spec, row) else 0)
        updates.append(((row, physical), (row, logical)))
    pins = panel_ports(origin, spec)
    scenario = dict(inputs={f"D{i}": _coords(p) for i, p in enumerate(pins.data_in)} |
                           {f"R{i}": _coords(p) for i, p in enumerate(pins.row_select)},
                    outputs={f"P{row}:{col}": _coords(panel_pixel(origin, spec, row, col)) for row in range(4) for col in range(8)},
                    initial_ticks=1024, steps=_panel_steps(spec, updates), capture_trace=True)
    stream = tmp_path / "world.jsonl"
    export_world(world, stream)
    result = _run_sparse(_runner("SHAMA_MCHPRS_SPARSE_RUNNER"), stream, scenario, tmp_path)
    assert _compare_dense(world, scenario, result) == 819200
    _record(tmp_path, "sparse-native-upright.json", result)


def test_actual_terrain_sparse_world_preserves_stacked_memory_and_native_entities(saved_memory, tmp_path):
    _, stream, scenario, manifest = saved_memory
    result = _run_sparse(_runner("SHAMA_MCHPRS_TERRAIN_RUNNER"), stream, scenario, tmp_path)
    assert result["loaded_chunks"] == manifest["chunks"] == 50
    assert result["decoded_nonair_blocks"] == manifest["nonair"] == 72047
    assert result["comparator_entities"] == 192
    assert result["steps"] == 25 and result["ticks"] == 6656
    assert result["engine_registry_patch_sha256"] == "8ebd0e097d07e93a6b2b158e08ad19c2660b0b7e2521c6b6d34a4583a10bfbdc"
    _record(tmp_path, "sparse-native-terrain-memory.json", result)


def test_unmodified_engine_explicitly_rejects_unsupported_superflat_terrain(saved_memory, tmp_path):
    error = _run_sparse(_runner("SHAMA_MCHPRS_SPARSE_RUNNER"), saved_memory[1], saved_memory[2], tmp_path, succeeds=False)
    assert "refusing to omit or replace" in error


def test_terrain_registry_preserves_all_existing_states_and_properties():
    registries = []
    for variable in ("SHAMA_MCHPRS_SPARSE_RUNNER", "SHAMA_MCHPRS_TERRAIN_RUNNER"):
        result = subprocess.run([_runner(variable), "--registry"], text=True, capture_output=True, check=True)
        registries.append({state["id"]: state for state in json.loads(result.stdout)["states"]})
    old, new = registries
    assert len(old) == 3327
    assert set(new) - set(old) == {8, 9, 10, 79}
    assert {key: new[key] for key in old} == old
    assert new[8]["properties"] == {"snowy": "true"}
    assert new[9]["properties"] == {"snowy": "false"}
    assert all(new[index]["solid"] and new[index]["cube"] and not new[index]["transparent"] for index in (8, 9, 10, 79))


@pytest.mark.parametrize("corruption", (
    "truncated", "duplicate", "trailing", "count", "palette", "unknown", "property", "missing_section",
    "uniform_count", "uniform_legacy_schema", "duplicate_palette", "palette_bits",
))
def test_sparse_import_rejects_incomplete_or_unrepresentable_source(saved_logic, tmp_path, corruption):
    _, stream, scenario, _ = saved_logic
    records = [json.loads(line) for line in stream.read_text().splitlines()]
    packed = next(record for record in records if record["kind"] == "section")
    uniform = next(record for record in records if record["kind"] == "uniform_section")
    if corruption == "truncated":
        records.pop()
    elif corruption == "duplicate":
        records.insert(2, copy.deepcopy(records[1]))
    elif corruption == "trailing":
        records.append(copy.deepcopy(records[1]))
    elif corruption == "count":
        records[-1]["nonair"] += 1
    elif corruption == "palette":
        packed["data"][0] = (1 << packed["bits"]) - 1
    elif corruption == "unknown":
        uniform["state"]["name"] = "minecraft:not_a_block"
    elif corruption == "property":
        uniform["state"]["properties"]["fictional"] = "true"
    elif corruption == "missing_section":
        index = next(i for i, record in enumerate(records) if record["kind"] == "uniform_section" and record["nonair"] == 0)
        records.pop(index)
        records[-1]["sections"] -= 1
    elif corruption == "uniform_count":
        uniform["nonair"] = 1
    elif corruption == "uniform_legacy_schema":
        records[0]["schema"] = "shama-native-anvil-sections-v1"
    elif corruption == "duplicate_palette":
        packed["palette"].append(copy.deepcopy(packed["palette"][0]))
    elif corruption == "palette_bits":
        packed["bits"] = 8
    broken = tmp_path / "broken.jsonl"
    broken.write_text("".join(json.dumps(record) + "\n" for record in records))
    _run_sparse(_runner("SHAMA_MCHPRS_SPARSE_RUNNER"), broken, scenario, tmp_path, succeeds=False)


def test_sparse_memory_limit_and_wrong_output_fail_instead_of_partial_success(saved_logic, tmp_path):
    _, stream, scenario, _ = saved_logic
    runner = _runner("SHAMA_MCHPRS_SPARSE_RUNNER")
    assert "explicit loaded-chunk memory limit" in _run_sparse(runner, stream, scenario, tmp_path, limit=2, succeeds=False)
    wrong = copy.deepcopy(scenario)
    name = next(iter(wrong["steps"][0]["expect"]))
    wrong["steps"][0]["expect"][name] = not wrong["steps"][0]["expect"][name]
    assert "output=" in _run_sparse(runner, stream, wrong, tmp_path, succeeds=False)


@pytest.mark.parametrize("invalid", (
    "output_height", "output_border", "input_height", "use_height", "absent_output",
    "air_output", "air_actuator", "solid_actuator", "overwrite_pad", "duplicate_pad",
    "no_outputs", "no_steps", "unobserved", "no_ticks",
))
def test_sparse_scenario_rejects_invalid_coordinates_pads_and_empty_evidence(saved_logic, tmp_path, invalid):
    _, stream, original, _ = saved_logic
    scenario = copy.deepcopy(original)
    name = next(iter(scenario["outputs"]))
    output = scenario["outputs"][name]
    if invalid == "output_height":
        scenario["outputs"][name][1] = 320
    elif invalid == "output_border":
        scenario["outputs"][name][0] = 30_000_000
    elif invalid == "input_height":
        scenario["inputs"]["bad"] = [0, -65, 0]
    elif invalid == "use_height":
        scenario["steps"][0]["use_blocks"] = [[0, 320, 0]]
    elif invalid == "absent_output":
        scenario["outputs"][name] = [900_000, 64, 900_000]
    elif invalid == "air_output":
        scenario["outputs"][name] = [output[0], output[1] + 10, output[2]]
    elif invalid == "air_actuator":
        scenario["steps"][0]["use_blocks"] = [[output[0], output[1] + 10, output[2]]]
    elif invalid == "solid_actuator":
        scenario["steps"][0]["use_blocks"] = [[-16, 63, -16]]
    elif invalid == "overwrite_pad":
        scenario["inputs"]["bad"] = [output[0], output[1], output[2] - 1]
    elif invalid == "duplicate_pad":
        scenario["inputs"] = {"one": [512, 64, 512], "two": [512, 64, 512]}
    elif invalid == "no_outputs":
        scenario["outputs"] = {}
    elif invalid == "no_steps":
        scenario["steps"] = []
    elif invalid == "unobserved":
        scenario["capture_trace"] = False
        for step in scenario["steps"]:
            step["expect"] = {}
    elif invalid == "no_ticks":
        scenario["initial_ticks"] = 0
        for step in scenario["steps"]:
            step["ticks"] = 0
    _run_sparse(_runner("SHAMA_MCHPRS_SPARSE_RUNNER"), stream, scenario, tmp_path, succeeds=False)


def test_trace_only_execution_is_distinguished_from_asserted_success(saved_logic, tmp_path):
    _, stream, original, _ = saved_logic
    scenario = copy.deepcopy(original)
    for step in scenario["steps"]:
        step["expect"] = {}
    result = _run_sparse(_runner("SHAMA_MCHPRS_SPARSE_RUNNER"), stream, scenario, tmp_path,
                         status="executed-trace-only")
    assert result["assertions"] == 0
    assert sum(map(len, result["traces"].values())) == 128
