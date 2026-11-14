"""A smaller physical cache requires fresh mapping and complete-bit oracles."""
from dataclasses import replace
import json
from pathlib import Path
import re
import shutil

import pytest

from experiments.memory_controller_runtime import current_fixture, geometry, run_connected, scenarios
from shamaos.hardware.memory_backbone import fabric_tile_ports
from shamaos.hardware.partition_planner import Graph
from shamaos.model import Vec3
from shamaos.system_build import memory_fabric_nets


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def four_kib_current(tmp_path_factory):
    if any(not shutil.which(tool) for tool in ("yosys", "iverilog", "vvp")):
        pytest.skip("current cache provenance requires actual synthesis and RTL simulation tools")
    directory = tmp_path_factory.mktemp("current-cache")
    source = directory / "source"
    for name in current_fixture.INPUTS:
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / name).read_bytes())
    config = source / "configs/default.toml"
    text, count = re.subn(r"(?m)^cache_bytes\s*=\s*\d+\s*$", "cache_bytes = 4096", config.read_text())
    assert count == 1
    config.write_text(text)
    header = source / "rtl/shama_boot_params.svh"
    text = header.read_text()
    for name, value in (("SHAMA_CACHE_BYTES", 4096), ("SHAMA_CACHE_BANKS", 1)):
        text, count = re.subn(rf"(?m)^(`define\s+{name}\s+).*$", rf"\g<1>32'h{value:08x}", text)
        assert count == 1
    header.write_text(text)
    prepared = directory / "prepared"
    report = current_fixture.prepare(source, prepared, capacity_bytes=4096)
    return prepared, report


def test_fresh_single_bank_mapping_and_actual_rtl_frames_retain_all_storage(four_kib_current):
    directory, report = four_kib_current
    loaded, graph, _, fabric, _ = current_fixture.load_prepared(directory)
    assert loaded == report
    assert (fabric.total_bytes, fabric.bank_count) == (4096, 1)
    assert report["interface"]["original_port_bits"] == 182
    assert report["interface"]["memory_nets"] == 77
    assert report["plan_audit"]["status"] == "passed"
    assert report["metadata_removal"]["all_native_cells_ports_nets_unchanged"]
    assert report["rtl_oracle"]["frames"] == 601
    assert report["rtl_oracle"]["boundaries"] == 49
    assert report["rtl_oracle"]["bit_boundary_assertions"] == 1_605_632
    assert [row["exit_code"] for row in report["rtl_oracle"]["outcomes"]] == [0, 1, 1]
    assert not report["native_runtime_verified"]
    assert len(graph.entities) == report["plan_summary"]["native_cells"] + 182


def test_single_bank_real_memory_contract_matches_production_connections(four_kib_current):
    directory, _ = four_kib_current
    _, graph, _, fabric, origin = current_fixture.load_prepared(directory)
    ports = {(entity["port_name"], entity["port_index"]): Vec3(entity["id"] * 4, 0, -64)
             for entity in graph.entities if entity["kind"] == "port"}
    pins = fabric_tile_ports(origin, fabric)[0]
    actual = geometry.memory_nets(ports, pins, bank_count=1)
    widths = {"bank_select": 1, "row_address": 10, "write_data": 32, "read_enable": 1, "write_commit": 1}
    expected = tuple(memory_fabric_nets("cache", origin, fabric,
        inputs={"cache_selected_word": tuple(ports["selected_word", bit] for bit in range(32))},
        outputs={f"cache_{name}": tuple(ports[name, bit] for bit in range(width)) for name, width in widths.items()}))
    signature = lambda nets: [(net.source, net.sinks, net.wired_or) for net in nets]
    assert signature(actual) == signature(expected)
    assert len(actual) == len(geometry.cache_route_contract(origin, fabric, capacity_bytes=4096).access) == 77
    assert sum(net.wired_or for net in actual) == 32
    with pytest.raises(ValueError, match="exact installed banks"):
        geometry.cache_route_contract(origin, fabric)  # Historical default still demands 8 KiB.


@pytest.mark.parametrize("name", ["source/rtl/shama_memory_adapter.sv", "source/rtl/shama_boot_params.svh",
                                  "source/configs/default.toml", "mapped.json", "plan.json", "initial.bin"])
def test_current_preparation_rejects_changed_sources_graph_plan_or_memory(four_kib_current, tmp_path, name):
    original, _ = four_kib_current
    clone = tmp_path / "changed"
    shutil.copytree(original, clone)
    path = clone / name
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="changed|differs"):
        current_fixture.load_prepared(clone)


def test_scope_metadata_with_even_one_signal_pin_cannot_be_removed(tmp_path):
    path = tmp_path / "invalid-metadata.json"
    path.write_text(json.dumps({"modules": {current_fixture.TOP: {"cells": {
        "scope": {"type": "$scopeinfo", "connections": {"Y": [2]}, "port_directions": {"Y": "output"}}}}}}))
    with pytest.raises(ValueError, match="physical pins or signal connections"):
        current_fixture.retain_native_mapping(path, tmp_path / "mapped.json", yosys="never-run", liberty=tmp_path / "unused.lib")


def test_native_four_kib_maps_reject_any_missing_or_aliased_stored_bit():
    inputs = {scenarios.host(name, bit): [index * 4, 0, 0]
              for index, (name, bit) in enumerate((name, bit) for name, width in scenarios.HOST_WIDTHS.items() for bit in range(width))}
    words = scenarios.initial_words(capacity_bytes=4096)
    outputs = {name: [index * 4, 10, 0] for index, name in enumerate(scenarios.storage_expect(words, capacity_bytes=4096))}
    outputs.update({f"register:cell{index}:C": [index * 4, 20, 0] for index in range(115)})
    assert len(run_connected._maps(inputs, outputs, capacity_bytes=4096)) == 115
    missing = dict(outputs)
    del missing["storage:0:1023:31"]
    with pytest.raises(ValueError, match="stored-bit observations"):
        run_connected._maps(inputs, missing, capacity_bytes=4096)
    alias = dict(outputs)
    alias["storage:0:1023:31"] = alias["storage:0:0:0"]
    with pytest.raises(ValueError, match="alias"):
        run_connected._maps(inputs, alias, capacity_bytes=4096)
    with pytest.raises(ValueError, match="stored-bit observations"):
        run_connected._maps(inputs, outputs)  # 8 KiB proof cannot accept half its bits.


def test_new_schedule_never_drives_returns_and_rejects_mixed_capacity():
    frames = list(scenarios.functional_frames(capacity_bytes=4096))
    summary = scenarios.summarize_frames(frames)
    assert summary["storage_bits_per_boundary"] == 32768
    assert all(len(frame.stored_words) == 1024 for frame in frames if frame.stored_words is not None)
    assert all(not name.startswith("host:selected_word") for frame in frames for name in (frame.drive or {}))
    assert all("port:bank_select:1" not in (frame.expect or {}) for frame in frames)
    frames.append(scenarios.Frame("different-capacity", capacity_bytes=8192))
    with pytest.raises(ValueError, match="mixes different physical capacities"):
        scenarios.summarize_frames(frames)


@pytest.mark.parametrize("capacity", [4096, 8192])
def test_full_native_acceptance_counts_match_the_retained_capacity_oracle(capacity):
    first = scenarios.summarize_frames(list(scenarios.functional_frames(capacity_bytes=capacity)))
    second = scenarios.summarize_frames(list(scenarios.functional_frames(first["final_words"],
                                                        resume=True, capacity_bytes=capacity)))
    observed = tuple(first[key] + second[key] for key in ("frames", "storage_boundaries", "full_storage_assertions"))
    assert run_connected._transaction_inventory(capacity) == observed


def test_unreviewed_capacity_cannot_inherit_another_native_oracle():
    with pytest.raises(ValueError, match="exact-capacity RTL oracle"):
        run_connected._transaction_inventory(2048)


@pytest.mark.parametrize("capacity", [4096, 8192])
def test_short_native_probe_includes_reset_boundary_reads_write_and_readback(capacity):
    frames = run_connected._smoke_frames(capacity)
    summary = scenarios.summarize_frames(frames)
    labels = [frame.label for frame in frames]
    assert labels[0] == "initial-host-reset"
    assert labels[-1] == "clear-all-host-pads"
    assert "cold-reset:storage" in labels
    assert labels.index("fresh-process-write:commit:rise") < labels.index("fresh-process-read:response:rise")
    assert labels.index("reopened-low:response:rise") < labels.index("reopened-high:response:rise")
    assert summary["frames"] < 70
    assert summary["storage_bits_per_boundary"] == capacity * 8
    assert summary["final_words"][min(4096, capacity - 4) // 4] == 0xC33CA55A
