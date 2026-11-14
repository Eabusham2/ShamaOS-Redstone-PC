"""Interrupted native saves may reuse only chunks proven against exact inputs."""
import copy
from dataclasses import asdict
import hashlib
import json
from types import SimpleNamespace

import pytest

from experiments.memory_controller_runtime import resume_connected
from experiments.memory_controller_runtime.resume_connected import _bound_preparation, _disjoint_paths, recover_chunks, verify_union
from shamaos import bounded_build, release_io
from shamaos.model import BlockState, Box, Placement, Vec3
from shamaos.hardware.external_router import ExternalNet
from shamaos.worldio import NativeWorldWriter, validate_native_world


STONE = BlockState.of("minecraft:stone")
DIRT = BlockState.of("minecraft:dirt")


def example(tmp_path, *, partial=False, extra=False, corrupted=False):
    points = [Placement(Vec3(0, -60, 0), STONE, "first"),
              Placement(Vec3(32, -60, 0), STONE, "second")]
    if partial:
        points.append(Placement(Vec3(1, -60, 0), STONE, "first-tail"))
    union = bounded_build._LiteralUnion(tmp_path / "union.sqlite3")
    record = union.collect(iter(points), surface_y=-61, maximum_y=319,
        bounds=Box(Vec3(0, -60, 0), Vec3(32, -60, 0)), expected_unique=len(points))
    source = tmp_path / "stopped"
    with NativeWorldWriter(source, spawn=Vec3(-16, -59, -16),
                           surface_y=-61, spawn_platform=False) as writer:
        writer.set_block(Placement(points[0].pos, DIRT if corrupted else STONE, "saved-first"))
        if extra:
            writer.set_block(Placement(Vec3(64, -60, 0), STONE, "not-in-union"))
    return points, union, record, source


def test_recovered_chunks_then_missing_append_match_complete_union(tmp_path):
    points, union, record, source = example(tmp_path)
    try:
        before = release_io.file_inventory(source)
        palette, checked = verify_union(union.connection, record)
        destination = tmp_path / "recovered"
        reused, recovered = recover_chunks(source, destination, union.connection, palette)
        assert not (destination / "session.lock").exists()
        assert checked["unique_blocks"] == 2
        assert recovered["verified_reused_chunks"] == recovered["verified_reused_blocks"] == 1
        with NativeWorldWriter(destination, spawn_platform=False, surface_y=-61) as writer:
            writer.set_blocks(p for p in points if bounded_build._coordinate_key(p.pos) >> 17 not in reused)
        actual = validate_native_world(destination)
        assert actual["assigned_blocks"] == 2 and actual["chunks"] == 2
        assert release_io.file_inventory(source) == before
    finally:
        union.close()


@pytest.mark.parametrize("fault", ["digest", "count", "bounds", "palette"])
def test_changed_union_evidence_is_rejected_before_reuse(tmp_path, fault):
    _, union, record, _ = example(tmp_path)
    try:
        bad = copy.deepcopy(record)
        if fault == "digest":
            bad["sorted_unique_record_sha256"] = "0" * 64
        elif fault == "count":
            bad["unique_blocks"] += 1
        elif fault == "bounds":
            bad["bounds"]["max"]["x"] += 1
        else:
            bad["palette"][0]["name"] = "minecraft:invented"
        with pytest.raises(ValueError):
            verify_union(union.connection, bad)
    finally:
        union.close()


@pytest.mark.parametrize("fault", ["partial", "extra", "corrupted"])
def test_saved_chunk_must_match_all_expected_states_and_ownership(tmp_path, fault):
    _, union, record, source = example(tmp_path, **{fault: True})
    try:
        before = release_io.file_inventory(source)
        palette, _ = verify_union(union.connection, record)
        with pytest.raises(ValueError, match="saved (chunk differs|world contains chunks)"):
            recover_chunks(source, tmp_path / "recovery-rejected", union.connection, palette)
        assert release_io.file_inventory(source) == before
    finally:
        union.close()


def test_recovery_refuses_existing_destination(tmp_path):
    _, union, record, source = example(tmp_path)
    try:
        palette, _ = verify_union(union.connection, record)
        target = tmp_path / "already-exists"
        target.mkdir()
        with pytest.raises(FileExistsError):
            recover_chunks(source, target, union.connection, palette)
    finally:
        union.close()


@pytest.mark.parametrize("kind", ["same", "nested", "ancestor"])
def test_recovery_rejects_related_directories_before_copying(tmp_path, kind):
    source = tmp_path / "source"
    source.mkdir()
    destination = {"same": source, "nested": source / "recovered", "ancestor": tmp_path}[kind]
    before = sorted(str(path) for path in tmp_path.rglob("*"))
    with pytest.raises(ValueError, match="disjoint"):
        _disjoint_paths(source, destination)
    with pytest.raises(ValueError, match="disjoint"):
        recover_chunks(source, destination, None, None)
    assert sorted(str(path) for path in tmp_path.rglob("*")) == before


def test_recovery_paths_resolve_symlinks_before_ancestry_check(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(source, target_is_directory=True)
    except OSError:
        # Junction/symlink creation can require Windows developer privileges;
        # the platform's ordinary relative-path check still runs above.
        assert _disjoint_paths(source, tmp_path / "independent")
        return
    with pytest.raises(ValueError, match="disjoint"):
        _disjoint_paths(source, alias / "recovered")


@pytest.mark.parametrize("fault", ["preparation", "plan"])
def test_exact_original_preparation_and_plan_hashes_checked_before_loading(tmp_path, monkeypatch, fault):
    (tmp_path / "prepared.json").write_text("{}\n")
    (tmp_path / "plan.json").write_text("{}\n")
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    report = {"current_preparation": {"path": str(tmp_path / "prepared.json"),
                                     "sha256": digest(tmp_path / "prepared.json")},
              "plan_sha256": digest(tmp_path / "plan.json")}
    changed = tmp_path / ("prepared.json" if fault == "preparation" else "plan.json")
    changed.write_text(changed.read_text() + "\n")
    monkeypatch.setattr(resume_connected.current_fixture, "load_prepared",
                        lambda _: pytest.fail("unbound preparation reached its internal loader"))
    with pytest.raises(ValueError, match="original accepted"):
        _bound_preparation(report)


@pytest.mark.parametrize("fault", [None, "mapping", "image", "nets", "bank"])
def test_preparation_binding_rejects_different_original_graph_image_nets_and_bank(tmp_path, monkeypatch, fault):
    for name, data in (("prepared.json", b"{}\n"), ("plan.json", b"{}\n"), ("initial.bin", bytes(4096))):
        (tmp_path / name).write_bytes(data)
    sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    graph = SimpleNamespace(source={"path": "mapped.json", "sha256": "1" * 64})
    fabric = SimpleNamespace(bank_count=1, bank=SimpleNamespace(words=1024, word_bits=32))
    origin = Vec3(100, -60, 0)
    connection = SimpleNamespace(bank_origin=origin, nets=(ExternalNet("test", Vec3(0, 0, 0), (Vec3(4, 0, 0),)),))
    encoded = (json.dumps([asdict(net) for net in connection.nets], indent=2, sort_keys=True) + "\n").encode()
    report = {"current_preparation": {"path": str(tmp_path / "prepared.json"), "sha256": sha(tmp_path / "prepared.json")},
              "plan_sha256": sha(tmp_path / "plan.json"), "mapped_source": dict(graph.source),
              "facade_manifest": {"path": "bound-manifest.json.gz", "sha256": "2" * 64},
              "external_nets_sha256": hashlib.sha256(encoded).hexdigest(),
              "physical_memory": {"initial_sha256": sha(tmp_path / "initial.bin"), "bank_origin": asdict(origin),
                  "capacity_bytes": 4096, "banks": 1, "rows_per_bank": 1024, "word_bits": 32}}
    monkeypatch.setattr(resume_connected.current_fixture, "load_prepared",
                        lambda _: ({"capacity_bytes": 4096}, graph, None, fabric, origin))
    monkeypatch.setattr(resume_connected, "read_egress_manifest", lambda *args: "complete-bound-layout")
    monkeypatch.setattr(resume_connected.interconnect, "connect_cache", lambda *args, **kwargs: connection)
    if fault == "mapping":
        report["mapped_source"]["sha256"] = "0" * 64
    elif fault == "image":
        report["physical_memory"]["initial_sha256"] = "0" * 64
    elif fault == "nets":
        report["external_nets_sha256"] = "0" * 64
    elif fault == "bank":
        report["physical_memory"]["bank_origin"]["x"] += 1
    if fault is None:
        assert _bound_preparation(report)[1] is graph
    else:
        with pytest.raises(ValueError, match="(original|acceptance)"):
            _bound_preparation(report)
