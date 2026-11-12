import copy
import json

from shamaos.cli import main
from shamaos.generator import (
    _bank_chunk_footprint, _capacity_claims, generation_preflight,
    load_config, memory_bank_profile, validate_manifest_capacity,
)
from shamaos.hardware.memory import MemoryBankSpec, iter_memory_bank
from shamaos.model import Vec3


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
    assert report["storage_bits"] == 42_336_256
    assert report["storage_banks"] == 1292
    assert report["sample_bank"]["bytes"] == 4096
    assert report["sample_bank"]["placements"] == 1_991_552
    assert report["storage_placements"] == report["sample_bank"]["placements"] * 1292
    assert 5_000_000 < report["storage_chunks"] < 6_500_000
    assert report["minimum_storage_region_bytes"] >= report["storage_chunks"] * 4096
    assert report["minimum_storage_region_bytes"] > 19 * (1 << 30)
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
