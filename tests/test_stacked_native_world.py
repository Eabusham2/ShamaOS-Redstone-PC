"""Exercise stacked circuitry reconstructed from actual saved Anvil chunks."""
import json
import os
from pathlib import Path

from shamaos.memory_fixture import create_stacked_runtime_fixture_world
from shamaos.model import Vec3
from shamaos.worldio import NativeWorldReader


def test_saved_stacked_fixture_readback_runtime_and_intact_grass(tmp_path):
    runner = os.environ.get("SHAMA_MCHPRS_RUNNER")
    world = tmp_path / "stacked-memory"
    report = create_stacked_runtime_fixture_world(world, runtime_runner=runner)
    assert report["world_validation"]["status"] == "passed"
    assert report["physical_storage_bits"] == 3 * 2 * 32
    assert report["lowest_circuit_y"] == -60
    assert report["highest_circuit_y"] == -25
    assert report["complete_shamaos_machine"] is False
    assert report["source"]["files_sha256"]["src/shamaos/hardware/memory_backbone.py"]
    reader = NativeWorldReader(world)
    assert reader.get_block(Vec3(256, -61, 128)).name == "minecraft:grass_block"
    assert reader.get_block(Vec3(256, -62, 128)).name == "minecraft:dirt"
    assert reader.get_block(Vec3(256, -64, 128)).name == "minecraft:bedrock"
    case = json.loads((world / "shamaos" / "runtime_case.json").read_text())
    shift = report["runtime_coordinate_translation_y"]
    for saved in case["blocks"]:
        x, y, z = saved["pos"]
        state = reader.get_block(Vec3(x, y - shift, z))
        assert saved["name"] == state.name
        assert saved["properties"] == dict(state.properties)
    assert len(case["blocks"]) == report["unique_circuit_blocks"]
    if runner and Path(runner).is_file():
        assert report["runtime_validation"]["status"] == "passed"
        assert report["runtime_validation"]["steps"] == 25
    else:
        assert report["runtime_validation"]["status"] == "not-run"
