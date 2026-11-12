"""Release failures are tested separately from small archive/install fixtures."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import zipfile

import nbtlib as n
import pytest

from shamaos.generator import build_plan, load_config, manifest
from shamaos.hardware.memory import comparator, repeater
from shamaos.installer import extract_ready_world, install_world
from shamaos.model import BlockState, Placement, Vec3
from shamaos.release import GATES, validate_candidate, validate_ci
from shamaos.release_acceptance import verify_full_world_acceptance
from shamaos.release_io import extract_zip, file_inventory, inventory_digest, materialize_asset, split_asset, write_zip
from shamaos.schematic import export_litematic, verify_litematic
from shamaos.worldio import NativeWorldReader, NativeWorldWriter, WorldWriteError, _Region


COMMIT = "a" * 40


def ci_report():
    names = ["world-backend", "rtl", "behavioral-and-redstone", "full-physical-synthesis"]
    names += [f"test ({os}, {python})" for os in ("ubuntu", "windows") for python in ("3.11", "3.12")]
    names += [f"full-physical-partitions ({i})" for i in range(12)]
    return {"head_sha": COMMIT, "conclusion": "success", "status": "completed",
            "path": ".github/workflows/ci.yml", "jobs": [{"name": x, "conclusion": "success"} for x in names]}


def tiny_world(path, *, surface=-61):
    with NativeWorldWriter(path, surface_y=surface, spawn=Vec3(0, surface + 2, 0), spawn_platform=False) as writer:
        writer.set_blocks([
            Placement(Vec3(-1, surface + 1, -1), BlockState.of("minecraft:stone"), "test-only"),
            Placement(Vec3(0, surface + 2, 0), comparator("east", powered=True), "test-only"),
            Placement(Vec3(17, surface + 3, 31), repeater("south", locked=True, delay=4), "test-only"),
        ])
        writer.validate()
    return path


def test_litematic_reopens_exact_negative_coordinates_and_states(tmp_path):
    world = tiny_world(tmp_path / "world")
    path = tmp_path / "test-fixture.litematic"
    report = export_litematic(world, path, name="TEST FIXTURE - NOT THE COMPUTER")
    assert report["assigned_blocks"] == report["non_air_blocks"] == 3
    root = n.load(path)
    assert int(root["Version"]) == 6
    assert int(root["MinecraftDataVersion"]) == 3700
    assert len(root["Regions"]) == 3
    found = {}
    for region in root["Regions"].values():
        local = [int(region["Position"][axis]) for axis in "xyz"]
        size = [int(region["Size"][axis]) for axis in "xyz"]
        # This fixture intentionally has one block per region; its palette
        # entry and relative coordinates can be checked without exporter code.
        assert size == [1, 1, 1]
        assert int(region["BlockStates"][0]) == 1
        state = region["BlockStatePalette"][1]
        position = tuple(report["origin"][i] + local[i] for i in range(3))
        found[position] = str(state["Name"])
        if str(state["Name"]) == "minecraft:comparator":
            assert region["TileEntities"][0]["OutputSignal"] == 15
    assert found == {(-1, -60, -1): "minecraft:stone", (0, -59, 0): "minecraft:comparator",
                     (17, -58, 31): "minecraft:repeater"}
    # Re-export is reproducible; gzip timestamps/filenames are not host dependent.
    other = tmp_path / "again.litematic"
    export_litematic(world, other, name="TEST FIXTURE - NOT THE COMPUTER")
    assert path.read_bytes() == other.read_bytes()
    with pytest.raises(WorldWriteError, match="differs"):
        verify_litematic(path, "0" * 64, report["regions"])


def test_schematic_continuous_palette_packing_crosses_long_boundaries(tmp_path):
    world = tmp_path / "world"
    colors = ("white", "orange", "magenta", "light_blue", "yellow", "lime", "pink", "gray",
              "light_gray", "cyan", "purple", "blue", "brown", "green", "red", "black")
    with NativeWorldWriter(world, spawn_platform=False, surface_y=-61) as writer:
        for index in range(81):
            writer.set_block(Placement(Vec3(index % 9, -60, index // 9),
                BlockState.of(f"minecraft:{colors[index % 16]}_concrete"), "test-only"))
    path = tmp_path / "palette.litematic"
    export_litematic(world, path, name="test-only")
    root = n.load(path)
    region = next(iter(root["Regions"].values()))
    longs = [int(v) & ((1 << 64) - 1) for v in region["BlockStates"]]
    bits = "".join(f"{word:064b}"[::-1] for word in longs)
    # Independent string-bit decoder across 5-bit palette values and 64-bit words.
    for index in range(81):
        value = int(bits[index * 5:index * 5 + 5][::-1], 2)
        assert region["BlockStatePalette"][value]["Name"] == f"minecraft:{colors[index % 16]}_concrete"


def test_archive_round_trip_split_join_and_missing_parts(tmp_path):
    files = tmp_path / "files"
    files.mkdir()
    (files / "hello.txt").write_text("hello\n", encoding="utf-8")
    archive = tmp_path / "world.zip"
    write_zip(archive, {"hello.txt": files / "hello.txt"})
    expected = file_inventory(files)
    original = archive.read_bytes()
    descriptor = split_asset(archive, part_bytes=25)
    joined = materialize_asset(tmp_path, descriptor, tmp_path / "joined.zip")
    assert joined.read_bytes() == original
    extract_zip(joined, tmp_path / "unpacked", inventory=expected)
    assert file_inventory(tmp_path / "unpacked") == expected
    (tmp_path / descriptor["parts"][1]["name"]).unlink()
    with pytest.raises(WorldWriteError, match="missing or incomplete"):
        materialize_asset(tmp_path, descriptor, tmp_path / "failed.zip")
    assert not (tmp_path / "failed.zip").exists()


@pytest.mark.parametrize("name", ["../outside", "/absolute", "C:/drive", "a\\b", "normal/../../outside"])
def test_archive_rejects_unsafe_paths_without_leaving_output(tmp_path, name):
    path = tmp_path / "bad.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(name, "bad")
    with pytest.raises(WorldWriteError, match="Unsafe"):
        extract_zip(path, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_archive_rejects_case_duplicate_and_corrupt_expected_hash(tmp_path):
    path = tmp_path / "bad.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("a", "one")
        archive.writestr("A", "two")
    with pytest.raises(WorldWriteError, match="Duplicate"):
        extract_zip(path, tmp_path / "out")
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("a", "one")
    with pytest.raises(WorldWriteError, match="check failed"):
        extract_zip(path, tmp_path / "out", inventory={"a": {"bytes": 3, "sha256": "0" * 64}})


def test_installer_preserves_original_and_existing_chunks_and_relocates_comparator(tmp_path):
    source = tiny_world(tmp_path / "Original")
    template = tiny_world(tmp_path / "TEMPLATE-TEST-ONLY")
    (source / "playerdata").mkdir()
    (source / "playerdata" / "player.dat").write_bytes(b"existing player inventory")
    before = file_inventory(source)
    old_record = _Region(source / "region" / "r.0.0.mca").record(0)
    output = tmp_path / "New"
    report = install_world(template, source, output, console={"x": 0, "y": -59, "z": 0},
        source_commit=COMMIT, setup_steps=["Console /tp @s {x} {y} {z}"])
    assert file_inventory(source) == before
    assert (output / "playerdata" / "player.dat").read_bytes() == b"existing player inventory"
    assert _Region(output / "region" / "r.0.0.mca").record(0) == old_record
    offset = report["offset_blocks"][0]
    assert offset >= 512 and offset % 16 == 0
    reader = NativeWorldReader(output)
    assert reader.get_block(Vec3(offset, -59, 0)) == comparator("east", powered=True)
    assert reader.get_block(Vec3(offset, -61, 0)).name == "minecraft:grass_block"
    assert reader.get_block(Vec3(offset, -64, 0)).name == "minecraft:bedrock"
    assert reader.get_block(Vec3(0, -59, 0)) == comparator("east", powered=True)
    assert report["copied_files_verified"] == len(before)
    assert report["installed_chunks"] == 3


def test_installer_rejects_overlap_height_and_existing_output_without_mutation(tmp_path):
    source = tiny_world(tmp_path / "original")
    template = tiny_world(tmp_path / "template")
    before = file_inventory(source)
    kwargs = {"console": {"x": 0, "y": -59, "z": 0}, "source_commit": COMMIT, "setup_steps": ["Start"]}
    with pytest.raises(WorldWriteError, match="already has saved chunks"):
        install_world(template, source, tmp_path / "out", offset_chunks=(0, 0), **kwargs)
    high = tiny_world(tmp_path / "high", surface=0)
    with pytest.raises(WorldWriteError, match="different ground height"):
        install_world(template, high, tmp_path / "out", **kwargs)
    with pytest.raises(WorldWriteError, match="already exists"):
        install_world(template, source, source, **kwargs)
    assert file_inventory(source) == before
    assert not (tmp_path / "out").exists()


def test_installer_lock_rejects_world_in_another_process(tmp_path):
    source = tiny_world(tmp_path / "original")
    template = tiny_world(tmp_path / "template")
    code = """from pathlib import Path
from shamaos.installer import install_world
from shamaos.worldio import WorldWriteError
import sys
try:
 install_world(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), console={'x':0,'y':-59,'z':0}, source_commit='a'*40, setup_steps=['start'])
except WorldWriteError as exc:
 print(exc)
 sys.exit(3)
"""
    with NativeWorldWriter(source, spawn_platform=False, surface_y=-61):
        result = subprocess.run([sys.executable, "-c", code, str(template), str(source), str(tmp_path / "out")],
                                capture_output=True, text=True)
    assert result.returncode == 3
    assert "Close Minecraft" in result.stdout
    assert not (tmp_path / "out").exists()


def test_pending_ticks_survive_installer_translation_and_schematic_export(tmp_path):
    source = tiny_world(tmp_path / "original")
    template = tiny_world(tmp_path / "template")
    region = _Region(template / "region" / "r.0.0.mca")
    chunk = region.read(0)
    tick = n.Compound({"i": n.String("minecraft:comparator"), "x": n.Int(0), "y": n.Int(-59),
                       "z": n.Int(0), "t": n.Int(7), "p": n.Int(-1)})
    chunk["block_ticks"] = n.List[n.Compound]([tick])
    chunk["fluid_ticks"] = n.List[n.Compound]([n.Compound({**tick, "i": n.String("minecraft:water")})])
    region.write(0, chunk)
    region.compact()
    result = install_world(template, source, tmp_path / "out", console={"x": 0, "y": -59, "z": 0},
        source_commit=COMMIT, setup_steps=["start"], offset_chunks=(64, -64))
    translated = _Region(tmp_path / "out" / "region" / "r.2.-2.mca").read(0)
    for field in ("block_ticks", "fluid_ticks"):
        assert translated[field][0]["x"] == 1024
        assert translated[field][0]["z"] == -1024
        assert translated[field][0]["t"] == 7
        assert translated[field][0]["p"] == -1
    assert result["offset_blocks"] == [1024, 0, -1024]
    path = tmp_path / "pending.litematic"
    export_litematic(template, path, name="test-only")
    section = next(region for region in n.load(path)["Regions"].values() if region["PendingBlockTicks"])
    assert section["PendingBlockTicks"][0]["Time"] == 7
    assert section["PendingBlockTicks"][0]["Priority"] == -1
    assert section["PendingBlockTicks"][0]["x"] == section["PendingBlockTicks"][0]["z"] == 0
    assert section["PendingFluidTicks"][0]["Fluid"] == "minecraft:water"


@pytest.mark.parametrize("folder", ["entities", "poi", "data", "DIM-1", "dimensions"])
def test_installer_rejects_unhandled_coordinate_sidecars(tmp_path, folder):
    source = tiny_world(tmp_path / "original")
    template = tiny_world(tmp_path / "template")
    (template / folder).mkdir()
    (template / folder / "unknown.dat").write_bytes(b"coordinates")
    with pytest.raises(WorldWriteError, match="cannot safely relocate"):
        install_world(template, source, tmp_path / "out", console={"x": 0, "y": -59, "z": 0},
            source_commit=COMMIT, setup_steps=["start"])
    assert not (tmp_path / "out").exists()


def test_ready_world_extraction_is_verified_and_atomic(tmp_path, monkeypatch):
    template = tiny_world(tmp_path / "template")
    expected = file_inventory(template)
    extract_ready_world(template, tmp_path / "ready", expected)
    assert file_inventory(tmp_path / "ready") == expected
    bad = deepcopy(expected)
    bad["level.dat"]["sha256"] = "0" * 64
    with pytest.raises(WorldWriteError, match="file check"):
        extract_ready_world(template, tmp_path / "bad", bad)
    assert not (tmp_path / "bad").exists()
    assert not list(tmp_path.glob(".bad-unpack-*"))


def test_failed_install_removes_staging_and_never_changes_original(tmp_path, monkeypatch):
    source = tiny_world(tmp_path / "original")
    template = tiny_world(tmp_path / "template")
    before = file_inventory(source)
    def fail_write(*args):
        raise OSError("simulated disk failure")
    monkeypatch.setattr(_Region, "write", fail_write)
    with pytest.raises(OSError, match="simulated disk failure"):
        install_world(template, source, tmp_path / "out", console={"x": 0, "y": -59, "z": 0},
            source_commit=COMMIT, setup_steps=["start"])
    assert file_inventory(source) == before
    assert not (tmp_path / "out").exists()
    assert not list(tmp_path.glob(".out-install-*"))


def test_ci_gate_requires_exact_commit_and_all_real_jobs():
    report = ci_report()
    validate_ci(report, COMMIT)
    for modify in (lambda r: r.update(head_sha="b" * 40), lambda r: r["jobs"].pop(),
                   lambda r: r["jobs"][0].update(conclusion="skipped")):
        bad = deepcopy(report)
        modify(bad)
        with pytest.raises(WorldWriteError):
            validate_ci(bad, COMMIT)


def labeled_candidate(tmp_path):
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    world = tiny_world(candidate / "world")
    config = load_config("configs/default.toml")
    generated = manifest(config, build_plan(config))
    generated.update(source={"base_revision": COMMIT, "working_tree_dirty": False},
        physical_generation_status="generated-topology-readback-verified",
        stage_counts={"synthesized-soc": 1, "system-infrastructure": 1, "system-interconnect": 1})
    (candidate / "generated-manifest.json").write_text(json.dumps(generated))
    (candidate / "evidence").mkdir()
    evidence = candidate / "evidence" / "test-only.json"
    evidence.write_text('{"description":"FAKE LABELS FOR NEGATIVE TEST ONLY"}')
    acceptance = {"format": "shamaos-full-world-acceptance-v1", "scope": "complete-machine", "status": "passed",
        "source_commit": COMMIT, "world_inventory_sha256": inventory_digest(file_inventory(world)),
        "observed_capacity_bytes": {"ram": generated["logical_ram_bytes"], "cache": generated["cache_bytes"],
                                   "vram": generated["vram_bytes"], "flash": generated["flash_bytes"]},
        "gates": {name: {"status": "passed", "scope": "complete-machine", "evidence": [{"path": "evidence/test-only.json",
                    "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest()}]} for name in GATES},
        "runtime": {"name": "test-only", "version": "test", "clock": "test", "loading": "test",
                    "setup_steps": ["test"], "render_distance": 1, "simulation_distance": 1},
        "installer_setup_steps": ["test"], "console": {"x": 0, "y": -59, "z": 0}}
    (candidate / "acceptance.json").write_text(json.dumps(acceptance))
    return candidate, acceptance


def test_fixture_cannot_be_released_even_with_copied_full_capacity_labels(tmp_path):
    candidate, _ = labeled_candidate(tmp_path)
    with pytest.raises(WorldWriteError, match="too small"):
        validate_candidate(candidate, COMMIT, ci_report())


def test_candidate_fails_missing_runtime_gate_changed_world_and_missing_evidence(tmp_path):
    candidate, acceptance = labeled_candidate(tmp_path)
    for name in GATES:
        bad = deepcopy(acceptance)
        bad["gates"][name]["status"] = "not-run"
        (candidate / "acceptance.json").write_text(json.dumps(bad))
        with pytest.raises(WorldWriteError, match=f"gate remains open: {name}"):
            validate_candidate(candidate, COMMIT, ci_report())
    (candidate / "acceptance.json").write_text(json.dumps(acceptance))
    (candidate / "world" / "changed.txt").write_text("changed")
    with pytest.raises(WorldWriteError, match="accepted runtime snapshot"):
        validate_candidate(candidate, COMMIT, ci_report())
    (candidate / "evidence" / "test-only.json").unlink()
    with pytest.raises(WorldWriteError, match="Missing or changed"):
        validate_candidate(candidate, COMMIT, ci_report())


def test_invented_pass_records_cannot_certify_complete_world_runtime(tmp_path):
    candidate, acceptance = labeled_candidate(tmp_path)
    digest = acceptance["world_inventory_sha256"]
    with pytest.raises(WorldWriteError, match="not a source/world-bound"):
        verify_full_world_acceptance(candidate, acceptance, commit=COMMIT, world_digest=digest)
    # Even correctly shaped, source-bound claims cannot invent a trusted runner.
    acceptance["runner"] = "invented-full-world-runner"
    report = {"format": "shamaos-full-world-trace-v1", "scope": "complete-machine",
              "source_commit": COMMIT, "world_inventory_sha256": digest,
              "runner": acceptance["runner"], "gates": list(GATES), "status": "passed"}
    (candidate / "evidence" / "test-only.json").write_text(json.dumps(report))
    with pytest.raises(WorldWriteError, match="no supported full-world acceptance validator exists"):
        verify_full_world_acceptance(candidate, acceptance, commit=COMMIT, world_digest=digest)


def test_only_package_writes_all_artifacts_after_mocked_acceptance(tmp_path, monkeypatch):
    """Exercise packaging plumbing, explicitly NOT a runtime/release proof."""
    import shamaos.release as release_module
    from shamaos.schematic import machine_bounds
    candidate, acceptance = labeled_candidate(tmp_path)
    root = Path(__file__).resolve().parents[1]
    checked = {"acceptance": acceptance, "manifest": {}, "readback": {}, "evidence": {},
               "world_files": file_inventory(candidate / "world"), "bounds": machine_bounds(candidate / "world")}
    # No production flag can do this: only this test replaces the real gate.
    monkeypatch.setattr(release_module, "validate_candidate", lambda *args: checked)
    monkeypatch.setattr(release_module, "_source_files", lambda *args: {"ShamaOS-source/README.md": root / "README.md"})
    ci_dir = tmp_path / "ci"
    ci_dir.mkdir()
    with zipfile.ZipFile(ci_dir / "full-soc-test-fixture.zip", "w") as archive:
        archive.writestr("TEST-ONLY.txt", "Unit test evidence, not the complete computer")
    destination = tmp_path / "test-only-release"
    result = release_module.package_release(candidate, destination, commit=COMMIT, tag="TEST-ONLY",
        ci=ci_report(), ci_artifacts=ci_dir, root=root)
    names = {p.name for p in destination.iterdir()}
    assert {"ShamaOS-world.zip", "ShamaOS.litematic", "ShamaOS-installer.zip", "ShamaOS-source-and-tools.zip",
            "ShamaOS-validation.zip", "README.txt", "SHA256SUMS.txt", "release-manifest.json",
            "full-soc-test-fixture.zip"} <= names
    for descriptor in result["assets"].values():
        assert hashlib.sha256((destination / descriptor["name"]).read_bytes()).hexdigest() == descriptor["sha256"]
    with zipfile.ZipFile(destination / "ShamaOS-installer.zip") as archive:
        assert {"Install-ShamaOS.py", "Start-Windows.cmd", "Start-Mac-Linux.command", "README.txt",
                "src/shamaos/installer.py", "release-manifest.json"} <= set(archive.namelist())
    assert "THIS RELEASE'S TESTED SETTINGS" in (destination / "README.txt").read_text()


def test_asset_upload_preserves_unknown_assets_and_checks_collisions_first(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("release_ci_tools", Path(__file__).resolve().parents[1] / "tools/release_ci.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    (tmp_path / "release-manifest.json").write_text(json.dumps({"source_commit": COMMIT}))
    (tmp_path / "new.zip").write_bytes(b"new artifact")
    monkeypatch.setattr(module, "api", lambda path: {"sha": COMMIT} if "/commits/" in path else {"id": 9})
    monkeypatch.setattr(module, "paginated", lambda *args: iter([{"id": 1, "name": "preexisting-extra.zip"}]))
    calls = []
    monkeypatch.setattr(module.subprocess, "run", lambda args, **kwargs: calls.append(args))
    module.append_assets("owner/repo", "test", tmp_path)
    assert all(args[:3] == ["gh", "release", "upload"] for args in calls)
    assert not any("delete" in args or "--clobber" in args for args in calls)
    assert not any("preexisting-extra.zip" in args for args in calls)
    calls.clear()
    monkeypatch.setattr(module, "api", lambda path: {"sha": "b" * 40} if "/commits/" in path else {"id": 9})
    with pytest.raises(WorldWriteError, match="tag moved"):
        module.append_assets("owner/repo", "test", tmp_path)
    assert not calls
