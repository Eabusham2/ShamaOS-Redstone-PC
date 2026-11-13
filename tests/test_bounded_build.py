"""Complete bounded netlists reach real native saves, never full-release claims."""
from copy import deepcopy
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest

from shamaos import bounded_build
from shamaos.bounded_build import BoundedBuildError, build_bounded_partition, emit_bounded_partition
from shamaos.hardware.partition_planner import Graph, PlanningError, build_plan
from shamaos.hardware.partition_placement import PlacementError, place_partition
from shamaos.mchprs_import import export_world, digest
from shamaos.model import BlockState, Placement, Vec3
from shamaos.release_acceptance import _FULL_WORLD_VALIDATORS
from shamaos.release_io import file_inventory, inventory_digest
from shamaos.worldio import NativeWorldReader, validate_native_world


@pytest.fixture(scope="module")
def mapped_pipeline(tmp_path_factory):
    path = tmp_path_factory.mktemp("bounded-build-source") / "pipeline.json.gz"
    module = {"ports": {
        "clk": {"direction": "input", "bits": [2]},
        "d": {"direction": "input", "bits": [3]},
        "q": {"direction": "output", "bits": [5, 6]},
        "one": {"direction": "output", "bits": ["1"]},
        "zero": {"direction": "output", "bits": ["0"]},
    }, "cells": {
        "enabled": {"type": "$_AND_", "connections": {"A": [3], "B": ["1"], "Y": [4]}},
        "a": {"type": "$_DFF_P_", "connections": {"C": [2], "D": [4], "Q": [5]}},
        "b": {"type": "$_DFF_P_", "connections": {"C": [2], "D": [5], "Q": [6]}},
    }}
    raw = json.dumps({"modules": {"pipeline": module}}).encode()
    path.write_bytes(gzip.compress(raw, mtime=0))
    return path, hashlib.sha256(raw).hexdigest()


@pytest.fixture(scope="module")
def prepared_pipeline(mapped_pipeline):
    graph = Graph.read(mapped_pipeline[0], "pipeline")
    plan = build_plan(graph, cell_budget=1, boundary_budget=8, domain_budget=16, arity=4)
    return graph, plan, place_partition(graph, plan, maximum_y=285)


@pytest.fixture(scope="module")
def saved_pipeline(mapped_pipeline, tmp_path_factory):
    directory = tmp_path_factory.mktemp("bounded-build-parent") / "pipeline"
    report = build_bounded_partition(mapped_pipeline[0], "pipeline", directory,
        expected_mapped_sha256=mapped_pipeline[1], cell_budget=1, boundary_budget=8, domain_budget=16)
    return directory, report


def test_complete_graph_to_fresh_native_world_retains_all_cells_ports_and_actual_states(saved_pipeline, prepared_pipeline):
    directory, report = saved_pipeline
    graph, plan, placed = prepared_pipeline
    assert report["status"] == "generated-and-readback-verified"
    assert report["placement_audit"]["native_cells"] == 3
    assert report["placement_audit"]["original_port_bits"] == 6
    assert report["placement_audit"]["clock_leaf_uses"] == 2
    assert report["plan_audit"]["status"] == "passed"
    assert json.loads((directory / "plan.json").read_text())["summary"] == plan["summary"]
    native = NativeWorldReader(directory / "world")
    # Compare the entire emitted circuit to saved blocks, including duplicate
    # terminals and matched repeater delays, not just aggregate counts.
    emitted = 0
    for placement in placed.iter_placements():
        assert native.get_block(placement.pos) == placement.block
        emitted += 1
    assert report["literal_union"]["emitted_placements"] == emitted
    assert report["literal_union"]["unique_blocks"] == placed.unique_blocks
    actual = validate_native_world(directory / "world")
    assert actual == report["world_validation"]
    assert actual["assigned_blocks"] == placed.unique_blocks
    assert actual["chunks"] == report["literal_union"]["occupied_chunks"]
    assert actual["terrain_preserved"] is True
    assert report["world_inventory_sha256"] == inventory_digest(file_inventory(directory / "world"))
    assert report["source_hashes"]["bounded_build.py"] == hashlib.sha256(Path(bounded_build.__file__).read_bytes()).hexdigest()
    assert json.loads((directory / "build-report.json").read_text()) == report


def test_development_world_never_certifies_runtime_or_registers_release_validator(saved_pipeline):
    directory, report = saved_pipeline
    assert report["scope"] == "complete-mapped-partition-development"
    for field in ("complete_machine_runtime_verified", "partition_runtime_verified", "complete_machine_release_eligible",
                  "electrical_route_isolation_verified", "complete_route_validation_verified"):
        assert report[field] is False
    assert not (directory / "acceptance.json").exists()
    assert not _FULL_WORLD_VALIDATORS
    ports = json.loads((directory / "ports.json").read_text())
    assert ports["externally_routable_verified"] is False
    assert set(ports["ports"]) == {"clk", "d", "q", "one", "zero"}
    assert len(ports["ports"]["q"]["pins"]) == 2


def test_saved_complete_pipeline_executes_in_fresh_actual_engine(saved_pipeline, tmp_path):
    runner = os.environ.get("SHAMA_MCHPRS_TERRAIN_RUNNER")
    if not runner or not Path(runner).is_file():
        pytest.skip("SHAMA_MCHPRS_TERRAIN_RUNNER unavailable; complete-partition runtime remains unverified")
    directory, _ = saved_pipeline
    ports = json.loads((directory / "ports.json").read_text())["ports"]
    xyz = lambda name, bit=0: [ports[name]["pins"][bit][axis] for axis in "xyz"]
    # Place temporary drive sources west of the original terminals, leaving
    # their N/S internal connections untouched, as in the physical fixture.
    anchor = lambda name: [xyz(name)[0] - 1, xyz(name)[1], xyz(name)[2] - 1]
    scenario = {"initial_ticks": 2048, "capture_trace": False,
                "inputs": {name: anchor(name) for name in ("clk", "d")},
                "outputs": {"q0": xyz("q"), "q1": xyz("q", 1), "one": xyz("one"), "zero": xyz("zero")},
                "steps": []}
    state = [False, False]
    for data in [False, True, True, False, True, False, False]:
        scenario["steps"].append({"drive": {"clk": False, "d": data}, "ticks": 1024})
        state = [data, state[0]]
        scenario["steps"].append({"drive": {"clk": True}, "ticks": 1024,
            "expect": {"q0": state[0], "q1": state[1], "one": True, "zero": False}})
    stream, case = tmp_path / "native.jsonl", tmp_path / "scenario.json"
    export_world(directory / "world", stream)
    case.write_text(json.dumps(scenario))
    process = subprocess.run([runner, str(stream), str(case), "1000"], capture_output=True, text=True, timeout=60)
    assert process.returncode == 0, process.stdout[-3000:] + process.stderr[-3000:]
    native = json.loads(process.stdout)
    assert native["status"] == "passed"
    assert native["assertions"] == 28
    assert native["imported_stream_sha256"] == digest(stream)
    assert native["scenario_sha256"] == digest(case)
    assert native["complete_machine_runtime_verified"] is False


def test_prepared_complete_placement_can_be_emitted_without_rerouting(mapped_pipeline, prepared_pipeline, tmp_path, monkeypatch):
    graph, plan, placed = deepcopy(prepared_pipeline)
    def no_new_search(*args, **kwargs):
        raise AssertionError("already accepted complete geometry must not route again")
    monkeypatch.setattr(bounded_build.partition_placement, "place_partition", no_new_search)
    report = emit_bounded_partition(mapped_pipeline[0], "pipeline", plan, placed, tmp_path / "resumed",
                                    expected_mapped_sha256=mapped_pipeline[1])
    assert report["placement_audit"]["native_cells"] == graph.cell_count
    assert report["world_validation"]["assigned_blocks"] == placed.unique_blocks
    # A restored object's graph and literal states passing does not replay the
    # allocator's adjacency/headroom checks or establish electrical continuity.
    assert report["electrical_route_isolation_verified"] is False
    assert report["complete_route_validation_verified"] is False
    assert "separate validation" in report["route_validation_scope"]


@pytest.mark.parametrize("fault", ["subtree", "missing-port", "false-count", "summary-plan"])
def test_partial_or_misreported_geometry_cannot_be_written(mapped_pipeline, prepared_pipeline, tmp_path, fault):
    _, plan, placed = deepcopy(prepared_pipeline)
    if fault == "subtree":
        placed = placed.children[0]
    elif fault == "missing-port":
        placed.ports.pop(next(iter(placed.ports)))
    elif fault == "false-count":
        placed.unique_blocks += 1
    else:
        plan["summary"]["all_signal_records_retained"] = False
    output = tmp_path / fault
    with pytest.raises((BoundedBuildError, PlacementError, PlanningError)):
        emit_bounded_partition(mapped_pipeline[0], "pipeline", plan, placed, output,
                               expected_mapped_sha256=mapped_pipeline[1])
    failure = json.loads((output / "build-report.json").read_text())
    assert failure["status"] == "failed"
    assert failure["complete_machine_release_eligible"] is False
    assert not (output / "world").exists()


def test_literal_collision_is_rejected_before_any_native_world(mapped_pipeline, prepared_pipeline, tmp_path, monkeypatch):
    _, plan, placed = deepcopy(prepared_pipeline)
    original = type(placed).iter_placements
    first = next(original(placed))
    def with_collision(self):
        yield from original(self)
        if self is placed:
            yield Placement(first.pos, BlockState.of("minecraft:redstone_block"), "conflicting-test")
    monkeypatch.setattr(type(placed), "iter_placements", with_collision)
    with pytest.raises(BoundedBuildError, match="conflicting"):
        emit_bounded_partition(mapped_pipeline[0], "pipeline", plan, placed, tmp_path / "bad",
                               expected_mapped_sha256=mapped_pipeline[1])
    assert not (tmp_path / "bad/world").exists()


@pytest.mark.parametrize("expected", ["0" * 64, "not-a-digest", "A" * 64])
def test_wrong_or_malformed_source_hash_fails_with_diagnostics(mapped_pipeline, tmp_path, expected):
    output = tmp_path / "rejected"
    with pytest.raises(BoundedBuildError, match="SHA-256"):
        build_bounded_partition(mapped_pipeline[0], "pipeline", output, expected_mapped_sha256=expected)
    assert json.loads((output / "build-report.json").read_text())["status"] == "failed"
    assert not (output / "world").exists()


def test_existing_destination_is_untouched(mapped_pipeline, tmp_path):
    output = tmp_path / "existing"
    output.mkdir()
    original = output / "build-report.json"
    original.write_text("preserve this")
    with pytest.raises(FileExistsError):
        build_bounded_partition(mapped_pipeline[0], "pipeline", output,
                                expected_mapped_sha256=mapped_pipeline[1])
    assert original.read_text() == "preserve this"
    assert list(output.iterdir()) == [original]


def test_changed_mapping_during_emission_cannot_gain_pass_status(mapped_pipeline, prepared_pipeline, tmp_path, monkeypatch):
    mapped = tmp_path / "mapping.json.gz"
    mapped.write_bytes(mapped_pipeline[0].read_bytes())
    _, plan, placed = deepcopy(prepared_pipeline)
    original = bounded_build.NativeWorldWriter.__exit__
    def finish_and_modify(self, *args):
        result = original(self, *args)
        content = json.loads(gzip.decompress(mapped.read_bytes()))
        content["creator"] = "source changed"
        mapped.write_bytes(gzip.compress(json.dumps(content).encode(), mtime=0))
        return result
    monkeypatch.setattr(bounded_build.NativeWorldWriter, "__exit__", finish_and_modify)
    output = tmp_path / "changed"
    with pytest.raises(BoundedBuildError, match="differs from"):
        emit_bounded_partition(mapped, "pipeline", plan, placed, output,
                               expected_mapped_sha256=mapped_pipeline[1])
    assert json.loads((output / "build-report.json").read_text())["status"] == "failed"
    assert not (output / "acceptance.json").exists()


@pytest.mark.parametrize("point", [Vec3(-29_999_984, -64, -29_999_984), Vec3(29_999_983, 319, 29_999_983),
                                   Vec3(-17, -60, 15), Vec3(0, 0, -1), Vec3(16, 63, -16)])
def test_literal_sort_key_roundtrips_java_coordinate_boundaries(point):
    assert bounded_build._coordinate(bounded_build._coordinate_key(point)) == point


@pytest.mark.parametrize("surface,maximum", [(-64, 285), (-61, 320), (True, 285), (317, 319), (-61, -61)])
def test_invalid_build_height_contract_fails_before_placement(mapped_pipeline, tmp_path, surface, maximum):
    with pytest.raises(BoundedBuildError, match="envelope"):
        build_bounded_partition(mapped_pipeline[0], "pipeline", tmp_path / "bad",
            expected_mapped_sha256=mapped_pipeline[1], surface_y=surface, maximum_y=maximum)
    assert not (tmp_path / "bad/world").exists()


def test_cli_returns_failure_without_replacing_existing_output(mapped_pipeline, saved_pipeline, capsys):
    assert bounded_build.main(["--mapped", str(mapped_pipeline[0]), "--top", "pipeline", "--sha256",
                              mapped_pipeline[1], "--output", str(saved_pipeline[0])]) == 1
    assert "failed" in capsys.readouterr().out


def test_cli_native_readback_failure_returns_error_and_preserves_failed_report(mapped_pipeline, tmp_path, monkeypatch, capsys):
    original = bounded_build.validate_native_world

    def corrupt_saved_region_before_readback(world):
        # This corrupts an actual generated Anvil file. The real independent
        # reader must reject it; neither the writer nor a fabricated pass
        # record is used as the acceptance oracle.
        region = next((Path(world) / "region").glob("*.mca"))
        region.write_bytes(b"truncated native region")
        return original(world)

    monkeypatch.setattr(bounded_build, "validate_native_world", corrupt_saved_region_before_readback)
    output = tmp_path / "corrupt-native-save"
    result = bounded_build.main(["--mapped", str(mapped_pipeline[0]), "--top", "pipeline", "--sha256",
        mapped_pipeline[1], "--output", str(output), "--cell-budget", "1", "--boundary-budget", "8",
        "--domain-budget", "16"])
    assert result == 1
    text = capsys.readouterr().out
    assert "Bounded partition build failed:" in text
    assert "generated-and-readback-verified" not in text
    report = json.loads((output / "build-report.json").read_text())
    assert report["status"] == "failed"
    assert report["stage"] == "independent-native-readback"
    assert report["error_type"] == "WorldWriteError"
    assert report["complete_machine_release_eligible"] is False
    assert "world_validation" not in report
    assert not (output / "acceptance.json").exists()
