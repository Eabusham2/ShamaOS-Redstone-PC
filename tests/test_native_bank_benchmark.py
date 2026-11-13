"""Benchmark provenance/platform guards; these are not redstone runtime proofs."""
from __future__ import annotations

import builtins
import hashlib
import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "tools/benchmark_mchprs_native_bank.py"
SPEC = importlib.util.spec_from_file_location("native_bank_benchmark", SCRIPT)
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)


@pytest.fixture
def cached(tmp_path):
    # Small arbitrary bytes deliberately exercise only cache identity checks.
    # The complete saved 4 KiB bank has separate actual-engine evidence.
    world = tmp_path / "world"
    region = world / "region"
    region.mkdir(parents=True)
    (world / "level.dat").write_bytes(b"level bytes")
    (region / "r.0.0.mca").write_bytes(b"region bytes")
    initial = tmp_path / "initial-4k.bin"
    initial.write_bytes(bytes(range(256)) * 16)
    initial_sha = hashlib.sha256(initial.read_bytes()).hexdigest()
    sources = {"generator.py": "current-generator-sha256"}
    prepared = {
        "cache_version": 1, "physical_bytes": 4096, "stored_bits": 32768,
        "physical_placements": 1_991_552, "initial_image_sha256": initial_sha,
        "generator_source_files_sha256": sources,
        "native_validation": {"status": "passed"},
        "native_world_files": benchmark.world_inventory(world),
    }
    return prepared, world, initial, initial_sha, sources


def test_unchanged_cache_is_accepted(cached):
    benchmark.validate_prepared_cache(*cached)


@pytest.mark.parametrize("field", (
    "cache_version", "physical_bytes", "stored_bits", "physical_placements",
    "initial_image_sha256", "generator_source_files_sha256", "native_world_files",
    "native_validation",
))
def test_missing_cache_provenance_is_rejected(cached, field):
    prepared, *arguments = cached
    del prepared[field]
    with pytest.raises(ValueError, match="stale prepared cache"):
        benchmark.validate_prepared_cache(prepared, *arguments)


def test_changed_generator_is_rejected(cached):
    prepared, world, initial, initial_sha, sources = cached
    changed = sources | {"generator.py": "different-generator-sha256"}
    with pytest.raises(ValueError, match="generator_source_files_sha256"):
        benchmark.validate_prepared_cache(prepared, world, initial, initial_sha, changed)


@pytest.mark.parametrize("change", ("content", "missing"))
def test_changed_initial_image_is_rejected(cached, change):
    prepared, world, initial, initial_sha, sources = cached
    if change == "content":
        initial.write_bytes(b"changed" + initial.read_bytes()[7:])
    else:
        initial.unlink()
    with pytest.raises(ValueError, match="initial image"):
        benchmark.validate_prepared_cache(*cached)


@pytest.mark.parametrize("relative", ("level.dat", "region/r.0.0.mca"))
def test_changed_native_content_is_rejected(cached, relative):
    prepared, world, *_ = cached
    target = world / relative
    target.write_bytes(b"X" + target.read_bytes()[1:])  # Same byte size still changes identity.
    with pytest.raises(ValueError, match="native world file inventory or content changed"):
        benchmark.validate_prepared_cache(*cached)


@pytest.mark.parametrize("change", ("add", "remove", "rename"))
def test_changed_region_inventory_is_rejected(cached, change):
    prepared, world, *_ = cached
    existing = world / "region/r.0.0.mca"
    added = world / "region/r.1.0.mca"
    if change == "add":
        added.write_bytes(existing.read_bytes())
    elif change == "rename":
        existing.rename(added)
    else:
        existing.unlink()
    with pytest.raises(ValueError, match="native world"):
        benchmark.validate_prepared_cache(*cached)


def test_failed_native_validation_is_rejected(cached):
    cached[0]["native_validation"]["status"] = "failed"
    with pytest.raises(ValueError, match="native validation did not pass"):
        benchmark.validate_prepared_cache(*cached)


@pytest.mark.parametrize("change", (None, "level", "region", "added-region"))
def test_exported_source_is_bound_to_validated_bank(cached, change):
    prepared = cached[0]
    files = prepared["native_world_files"]
    source = {"level_dat_sha256": files["level.dat"]["sha256"],
              "regions": [{"name": "r.0.0.mca", **files["region/r.0.0.mca"]}]}
    if change == "level":
        source["level_dat_sha256"] = "changed-level"
    elif change == "region":
        source["regions"][0]["sha256"] = "changed-region"
    elif change == "added-region":
        source["regions"].append(source["regions"][0] | {"name": "r.1.0.mca"})
    if change is None:
        benchmark.validate_exported_source(prepared, {"source": source})
    else:
        with pytest.raises(ValueError, match="exported native source changed"):
            benchmark.validate_exported_source(prepared, {"source": source})


@pytest.mark.parametrize("platform,value,expected", (
    ("linux", 2049, 2049), ("linux2", 2049, 2049),
    ("darwin", 2048, 2), ("darwin", 2049, 3), ("darwin", 0, 0),
))
def test_rss_units_are_normalized(platform, value, expected):
    assert benchmark.normalize_max_rss_kib(value, platform) == expected


def test_unknown_rss_platform_is_rejected():
    with pytest.raises(ValueError, match="unsupported ru_maxrss platform"):
        benchmark.normalize_max_rss_kib(1024, "win32")


def test_windows_gets_platform_error_before_resource_import(monkeypatch, tmp_path):
    original = builtins.__import__

    def without_resource(name, *args, **kwargs):
        if name == "resource":
            pytest.fail("unsupported platforms must be rejected before importing resource")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_resource)
    monkeypatch.setattr(benchmark.sys, "platform", "win32")
    with pytest.raises(ValueError, match="requires Linux or macOS and POSIX wait4"):
        benchmark.main(tmp_path / "unused", tmp_path / "runner")
    assert not (tmp_path / "unused").exists()


def test_python_optimized_mode_is_rejected():
    result = subprocess.run([sys.executable, "-O", str(SCRIPT), "--help"], text=True,
                            capture_output=True, timeout=10)
    assert result.returncode != 0
    assert "refuses Python -O" in result.stderr
    assert "passed" not in result.stdout


def test_fingerprint_binds_exact_loaded_generator_checkout(monkeypatch):
    sources = benchmark.generator_fingerprint()
    assert len(sources) == 6
    assert sources["tools/benchmark_mchprs_native_bank.py"] == benchmark.file_sha256(SCRIPT)
    original = sys.modules["shamaos.hardware.memory"]
    monkeypatch.setattr(original, "__file__", str(SCRIPT.parent / "different-memory.py"))
    with pytest.raises(ValueError, match="source mismatch"):
        benchmark.generator_fingerprint()
