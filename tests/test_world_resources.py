import copy
import json

from shamaos.cli import main
from shamaos.generator import (
    _bank_chunk_footprint, _capacity_claims, generation_preflight,
    load_config, memory_bank_profile, validate_manifest_capacity,
)
from shamaos.hardware.memory import MemoryBankSpec, iter_memory_bank
from shamaos.model import Vec3
from shamaos.worldio import NativeWorldWriter


def test_chunk_cost_matches_actual_emitter_across_bank_alignments():
    spec = MemoryBankSpec(words=8, word_bits=32)
    for x, z in ((0, 0), (8, 0), (0, 8), (8, 8), (1, 1), (7, 9), (9, 7), (15, 15)):
        actual = {(p.pos.x // 16, p.pos.z // 16) for p in iter_memory_bank(Vec3(x, 64, z), spec=spec)}
        assert _bank_chunk_footprint(spec, x, z) == actual
    placements = list(iter_memory_bank(Vec3(0, 64, 0), spec=spec))
    report = memory_bank_profile(spec)
    assert report["placements"] == len(placements)
    assert report["chunks_at_aligned_origin"] == len({(p.pos.x // 16, p.pos.z // 16) for p in placements})
    assert sum(report["block_types"].values()) == len(placements)


def test_literal_capacity_validator_rejects_scaled_spec_and_false_bank_counts(tmp_path, capsys):
    claims = _capacity_claims(load_config("configs/default.toml"))
    assert validate_manifest_capacity(claims)["status"] == "passed"
    reduced = copy.deepcopy(claims)
    reduced["logical_ram_bytes"] //= 16
    reduced["physical_ram_bytes"] //= 16
    reduced["memory_banks"]["ram"] //= 16
    assert validate_manifest_capacity(reduced)["status"] == "failed"
    false_counts = copy.deepcopy(claims)
    false_counts["memory_banks"]["flash"] -= 1
    assert validate_manifest_capacity(false_counts)["status"] == "failed"
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(reduced))
    assert main(["validate-manifest", "--manifest", str(path)]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["complete_world_runtime_verified"] is False


def test_full_storage_cost_is_reported_without_generating_billions_of_blocks(tmp_path):
    report = generation_preflight(load_config("configs/default.toml"), world_path=tmp_path)
    assert report["capacity_contract"]["status"] == "passed"
    assert report["storage_bits"] == 5_865_472
    assert report["storage_banks"] == 179
    assert report["sample_bank"]["bytes"] == 4096
    assert report["sample_bank"]["placements"] == 1_991_552
    assert report["storage_placements"] == report["sample_bank"]["placements"] * 179
    assert report["storage_chunks"] == 39_750
    assert report["storage_regions"] == 126
    assert report["production_packing"]["actual_max_layers"] == 30
    assert report["production_packing"]["surface_y"] == -61
    assert report["production_packing"]["minimum_build_y"] == -60
    assert report["production_packing"]["storage_base_y"] == -56
    assert report["minimum_storage_region_bytes"] >= report["storage_chunks"] * 4096
    assert report["minimum_storage_region_bytes"] == 163_848_192
    scenarios = {item["layers"]: item for item in report["stacking_scenarios"]}
    assert scenarios[1]["storage_chunks"] == 793_786
    assert scenarios[30]["storage_chunks"] == report["storage_chunks"]
    assert scenarios[30]["production_enabled"]
    assert all(item["physical_storage_placements"] == report["storage_placements"] for item in scenarios.values())
    assert {name: detail["banks"] for name, detail in report["storage_fabrics"].items()} == {"cache": 2, "ram": 48, "flash": 125, "vram": 4}
    assert report["storage_fabrics"]["ram"]["banks_in_final_tile"] == 18
    assert report["storage_fabrics"]["flash"]["banks_in_final_tile"] == 5
    regular = report["regular_fabrics"]
    assert regular["storage_and_local_control_union_chunks"] == 60_805
    assert regular["regular_fabric_union_chunks"] == 67_235
    assert regular["local_memory_control_and_return_placements"] == 85_626_782
    assert regular["regular_fabric_envelope"]["envelope_chunks"] == 252_560
    assert report["complete_world_runtime_verified"] is False
    assert "unverified" in report["full_world_loading_strategy"]
    assert "interconnect" in report["estimate_excludes"]
    assert not (tmp_path / "level.dat").exists()


def test_disabling_physical_capacity_does_not_make_full_preflight_pass(tmp_path):
    config = load_config("configs/default.toml")
    config["generator"]["physical_flash"] = False
    report = generation_preflight(config, world_path=tmp_path)
    assert report["capacity_contract"]["status"] == "failed"
    assert any("physical_flash_bytes" in error for error in report["capacity_contract"]["errors"])


def test_preflight_uses_existing_world_grass_and_reduces_layers_without_capacity_loss(tmp_path):
    world = tmp_path / "high-grass"
    with NativeWorldWriter(world, surface_y=63, spawn=Vec3(-16, 65, -16)):
        pass
    config = load_config("configs/default.toml")
    report = generation_preflight(config, world_path=world)
    assert report["production_packing"]["surface_y"] == 63
    assert report["production_packing"]["minimum_build_y"] == 64
    assert report["production_packing"]["storage_base_y"] == 68
    assert report["production_packing"]["actual_max_layers"] == 20
    assert report["storage_bits"] == 5_865_472
    assert report["storage_banks"] == 179
    assert report["capacity_contract"]["status"] == "passed"
    assert config["world"]["surface_y"] == -61
