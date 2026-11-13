"""Preparation guards for a complete native-cache proof, without claiming one."""
from dataclasses import replace
import gzip
import hashlib
import json
from pathlib import Path

import pytest

from experiments.memory_controller_runtime import fixture
from experiments.memory_controller_runtime.clock_reserve import reserve_clock_tracks
from experiments.memory_controller_runtime.geometry import cache_route_contract, memory_nets
from experiments.memory_controller_runtime.scenarios import (
    CAPACITY, HOST_WIDTHS, Frame, bus, functional_frames, host, initial_words,
    merge_bytes, port, requests, storage_expect, summarize_frames, trace_budget, write_scenario,
)
from shamaos.hardware.memory_backbone import fabric_tile_ports
from shamaos.hardware.partition_planner import audit_plan, build_plan
from shamaos.hardware import partition_placement
from shamaos.hardware.partition_placement import _Context, _leaf, _literal_states
from shamaos.model import Vec3
from shamaos.system_build import memory_fabric_nets


@pytest.fixture(scope="module")
def complete_cache():
    return fixture.graph_and_fabric()


@pytest.fixture(scope="module")
def frames():
    return list(functional_frames())


def test_pinned_complete_graph_and_physical_capacity_are_retained(complete_cache):
    graph, geometry, fabric, origin = complete_cache
    plan = build_plan(graph)
    audit_plan(graph, plan)
    summary = plan["summary"]
    assert graph.source["sha256"] == fixture.MAPPED_SHA
    mapped = fixture.HERE / "evidence/cache-controller-c331.json.gz"
    assert hashlib.sha256(gzip.decompress(mapped.read_bytes())).hexdigest() == fixture.MAPPED_SHA
    assert (summary["native_cells"], summary["original_port_bits"], summary["clock_leaf_uses"]) == (1144, 183, 115)
    assert (summary["leaf_clusters"], summary["bounded_router_domains"], summary["signal_tree_links"]) == (21, 12, 1484)
    assert summary["topology_sha256"] == "aabd37d3322b1eac9571b541e9558a9b835afeb44ece93987081a21185950e97"
    assert summary["all_signal_records_retained"]
    assert (fabric.total_bytes, fabric.bank_count, fabric.bank.words, fabric.bank.word_bits) == (8192, 2, 1024, 32)
    assert (fabric.layers, fabric.layer_pitch, fabric.decoder_x_offset) == (2, 12, -170)
    assert geometry.surface_y == -61
    assert fabric.bounds(origin).min.y == -60


def test_every_memory_connection_matches_production_direction_and_bank_order(complete_cache):
    graph, _, fabric, origin = complete_cache
    # Stand-in terminal coordinates test the ABI only; no geometry is accepted.
    ports = {(entity["port_name"], entity["port_index"]): Vec3(10000 + entity["id"] * 4, 0, -64)
             for entity in graph.entities if entity["kind"] == "port"}
    pins = fabric_tile_ports(origin, fabric)[0]
    actual = memory_nets(ports, pins)
    widths = {"bank_select": 2, "row_address": 10, "write_data": 32,
              "read_enable": 1, "write_commit": 1}
    expected = tuple(memory_fabric_nets("cache", origin, fabric,
        inputs={"cache_selected_word": tuple(ports["selected_word", bit] for bit in range(32))},
        outputs={f"cache_{name}": tuple(ports[name, bit] for bit in range(width)) for name, width in widths.items()}))
    signature = lambda values: {(net.source, net.sinks, net.wired_or) for net in values}
    assert signature(actual) == signature(expected)
    assert len(actual) == 78
    assert sum(net.wired_or for net in actual) == 32
    assert len({point for net in actual for point in (net.source, *net.sinks)}) == 156
    for net in actual:
        if net.name.startswith("cache:selected_word:"):
            bit = int(net.name.rsplit(":", 1)[1])
            assert net.source == ports["selected_word", bit]
            assert net.sinks == (pins.selected_word[bit],)
            assert net.wired_or
    with pytest.raises(ValueError, match="bank_select width"):
        memory_nets(ports, replace(pins, bank_select=pins.bank_select[:1]))


def test_cache_access_contract_retains_actual_decoder_and_return_faces(complete_cache):
    _, _, fabric, origin = complete_cache
    contract = cache_route_contract(origin, fabric)
    pins = fabric_tile_ports(origin, fabric)[0]
    assert len(contract.access) == len(contract.endpoint_bounds) == 78
    assert contract.minimum_y == -60
    assert all(point.y - 1 >= contract.minimum_y for point in contract.access)
    assert all(contract.access[point] == ("north",) for point in (*pins.bank_select, *pins.row_address, *pins.write_data, pins.write_enable))
    assert contract.access[pins.read_enable] == ("west",)
    assert all(contract.access[point] == ("south",) for point in pins.selected_word)
    assert {placement.pos for placement in contract.obstacles}.issuperset(pins.selected_word)
    assert pins.row_address == tuple(origin.offset(-162, bit * 12, -8) for bit in range(10))
    assert len({contract.endpoint_bounds[point] for point in pins.row_address}) == 10


def test_request_schedule_keeps_host_only_drives_and_complete_boundary_inventory(frames):
    required = {host(name, bit) for name, width in HOST_WIDTHS.items() for bit in range(width)}
    assert len(required) == 72
    assert all(set(frame.drive or {}) <= required for frame in frames)
    assert not any("selected_word" in name for frame in frames for name in (frame.drive or {}))
    summary = summarize_frames(frames)
    assert (summary["frames"], summary["clock_rising_edges"], summary["storage_boundaries"]) == (595, 248, 47)
    assert summary["full_storage_assertions"] == 3_080_192
    assert all(len(frame.stored_words) == 2048 for frame in frames if frame.stored_words is not None)
    all_bits = storage_expect(initial_words())
    assert len(all_bits) == 65536
    assert all_bits["storage:0:0:0"] == bool(initial_words()[0] & 1)
    assert all_bits["storage:1:1023:31"] == bool(initial_words()[-1] & (1 << 31))
    final = frames[-1]
    assert final.label == "clear-all-host-pads"
    assert set(final.drive) == required and not any(final.drive.values())
    assert final.stored_words is not None
    with pytest.raises(ValueError, match="original host"):
        host("selected_word")


def test_invalid_addresses_preserve_both_banks_and_lane_masks_use_aligned_words(frames):
    previous = initial_words()
    for frame in frames:
        if frame.stored_words is None:
            continue
        if frame.label.startswith("reject-"):
            assert frame.stored_words == previous
            assert all(not frame.expect[port("req_rdata", bit)] for bit in range(32))
            assert not frame.expect[port("write_commit")]
        previous = frame.stored_words
    assert {operation["strobes"] for operation in requests() if operation["name"].startswith("lane-mask-")} == set(range(16))
    for mask in range(16):
        old, new = bytes.fromhex("01234567"), bytes.fromhex("89abcdef")
        expected = bytes(new[lane] if mask & (1 << lane) else old[lane] for lane in range(4))
        assert merge_bytes(int.from_bytes(old, "little"), int.from_bytes(new, "little"), mask) == int.from_bytes(expected, "little")
    assert [operation["address"] for operation in requests() if operation["name"].startswith("boundary-")] == [4095, 4096, 4094, 4096]


def test_reset_before_read_sample_and_after_native_commit_have_distinct_oracles(frames):
    by_name = {frame.label: frame for frame in frames}
    before = by_name["reject-ffffffff-write:storage"].stored_words
    early = by_name["reset-read-settle:reset:storage"].stored_words
    committed = by_name["reset-completed-commit:intended-write-complete"].stored_words
    late = by_name["reset-completed-commit:reset:storage"].stored_words
    assert before == early
    assert committed == late
    assert committed[-1] == 0x6A95F00F
    assert committed[:-1] == early[:-1] and committed[-1] != early[-1]
    for prefix in ("reset-read-settle", "reset-completed-commit"):
        assert prefix + ":reset:assert" not in by_name
        first = by_name[prefix + ":reset:0:rise"]
        assert first.drive == {host("clk"): True, host("rst"): True}
        assert not first.expect[port("read_enable")] and not first.expect[port("write_commit")]


def test_requests_hold_valid_across_response_and_next_idle(frames):
    active = False
    for frame in frames:
        if frame.label == "initial-0000:request-setup":
            active = True
        if frame.label.startswith("reset-read-settle:reset:"):
            break
        if active and host("req_valid") in (frame.drive or {}):
            assert frame.drive[host("req_valid")]
    by_name = {frame.label: frame for frame in frames}
    assert by_name["initial-0000:response:rise"].expect[port("req_ready")]
    assert not by_name["initial-0000:return-idle:rise"].expect[port("req_ready")]


def test_trace_budget_excludes_initial_ticks_and_keeps_full_observation_counts(frames):
    budget = trace_budget(frames, half_period=100, initial_ticks=999, traced_outputs=4)
    assert budget["traced_ticks_per_output"] == 55_500
    assert budget["total_trace_samples"] == 222_000
    assert budget["total_execution_ticks"] == 56_499
    assert budget["minimum_bool_vector_payload_bytes"] == 222_000
    with pytest.raises(ValueError, match="trace budget exceeded"):
        trace_budget(frames, half_period=100, initial_ticks=0, traced_outputs=115, max_trace_samples=100_000)


def _maps():
    inputs = {host(name, bit): [0, 0, 4 * index]
              for index, (name, bit) in enumerate((name, bit) for name, width in HOST_WIDTHS.items() for bit in range(width))}
    outputs = {name: [4 * index, 4, 0] for index, name in enumerate(storage_expect(initial_words()))}
    outputs[port("req_ready")] = [0, 8, 0]
    return inputs, outputs


@pytest.mark.parametrize("capture", [True, False])
def test_streamed_scenario_keeps_every_storage_assertion_with_selective_or_disabled_trace(tmp_path, capture, monkeypatch):
    inputs, outputs = _maps()
    frames = [Frame("hold", 3, expect={port("req_ready"): False}),
              Frame("every-bit", 0, stored_words=initial_words())]
    path = tmp_path / "scenario.json"
    # Hashing must not reread the complete large JSON into another bytes object.
    monkeypatch.setattr(Path, "read_bytes", lambda *_: pytest.fail("scenario hashing allocated a full duplicate file"))
    report = write_scenario(path, frames, inputs, outputs, half_period=7, initial_ticks=11,
                            trace_outputs=[port("req_ready")] if capture else None)
    document = json.loads(path.read_text())
    assert report["assertions"] == 65537
    assert report["full_storage_boundaries"] == 1
    assert report["trace_budget"]["total_trace_samples"] == (21 if capture else 0)
    assert document["capture_trace"] is capture
    assert ("trace_outputs" in document) is capture
    assert document["steps"][1]["expect"] == storage_expect(initial_words())
    assert document["steps"][1]["ticks"] == 0
    assert document["steps"][0]["ticks"] == 21
    with path.open("rb") as stream:
        assert report["scenario_sha256"] == hashlib.file_digest(stream, "sha256").hexdigest()


@pytest.mark.parametrize("fault", ["missing-input", "memory-drive", "missing-storage", "empty-trace", "unknown-trace", "budget", "no-observation"])
def test_bad_scenario_rejects_before_creating_an_output(tmp_path, fault):
    inputs, outputs = _maps()
    frames = [Frame("boundary", 1, stored_words=initial_words())]
    traces = [port("req_ready")]
    options = {}
    if fault == "missing-input":
        inputs.pop(host("clk"))
    elif fault == "memory-drive":
        frames = [Frame("bad-drive", 1, drive={"host:selected_word:0": True}, stored_words=initial_words())]
    elif fault == "missing-storage":
        outputs.pop("storage:1:1023:31")
    elif fault == "empty-trace":
        traces = []
    elif fault == "unknown-trace":
        traces = ["invented"]
    elif fault == "budget":
        options["max_trace_samples"] = 1
    else:
        frames, traces = [Frame("nothing", 1)], None
    path = tmp_path / "bad.json"
    with pytest.raises(ValueError):
        write_scenario(path, frames, inputs, outputs, half_period=7, initial_ticks=0, trace_outputs=traces, **options)
    assert not path.exists()


@pytest.mark.parametrize("artifact", ["plan.json", "initial.bin", "request-plan.json", "continuation-plan.json", "source-inventory"])
def test_prepared_cache_rejects_stale_or_incomplete_bindings(tmp_path, artifact):
    directory = tmp_path / "prepared"
    report = fixture.prepare(directory)
    assert not report["geometry_verified"] and not report["connected_cache_runtime_verified"]
    assert fixture.verify_prepared(directory)["plan_sha256"] == report["plan_sha256"]
    if artifact == "source-inventory":
        report["source_hashes"].pop(fixture.SOURCE_PATHS[-1])
        (directory / "prepared.json").write_text(json.dumps(report))
    else:
        with (directory / artifact).open("ab") as stream:
            stream.write(b"\nchanged")
    with pytest.raises(ValueError, match="changed"):
        fixture.verify_prepared(directory)


@pytest.mark.parametrize("bad", [True, -1, 1.5])
def test_noninteger_or_negative_timing_and_invalid_storage_values_are_rejected(bad):
    with pytest.raises(ValueError):
        Frame("bad", bad)
    with pytest.raises(ValueError):
        trace_budget([Frame("one")], half_period=bad, initial_ticks=0, traced_outputs=1)
    words = list(initial_words())
    words[0] = bad
    with pytest.raises(ValueError):
        storage_expect(words)


@pytest.fixture(scope="module")
def reserved_cache_leaf(complete_cache):
    graph, _, _, _ = complete_cache
    plan = build_plan(graph)
    cell = next(entity for entity in graph.entities
                if entity.get("name") == "$auto$ff.cc:266:slice$68013")
    cluster = plan["entity_leaf"][cell["id"]]
    context = _Context(plan, -61, 285)
    original = _leaf(context, cluster)
    tile = next(iter(original.modules.values()))
    with pytest.raises(ValueError, match="private branch capacity"):
        tile.balance_clock_routes()
    compact, domains = partition_placement.compact_tile, partition_placement.plan_bounded_domains
    with reserve_clock_tracks({2}, 64):
        extended = _leaf(context, cluster)
    assert partition_placement.compact_tile is compact
    assert partition_placement.plan_bounded_domains is domains
    fixed = next(iter(extended.modules.values()))
    assert fixed.physical == tile.physical
    assert fixed.escapes == tile.escapes and fixed.tracks == tile.tracks
    assert fixed.track_base_z == tile.track_base_z - 64
    fixed.balance_clock_routes()
    assert 2 in fixed.clock_delays
    assert len(_literal_states(extended.iter_placements())) == extended.unique_blocks
    return extended


def test_clock_reserve_provides_real_capacity_without_changing_any_native_endpoint(reserved_cache_leaf):
    assert reserved_cache_leaf.bounds.min.y == -60
    assert reserved_cache_leaf.bounds.max.y <= 285


@pytest.mark.parametrize("bad", [True, -4, 3, 516, 1.5])
def test_invalid_clock_reserve_is_rejected_before_changing_placer(bad):
    compact = partition_placement.compact_tile
    with pytest.raises(ValueError, match="multiple of four"):
        with reserve_clock_tracks({2}, bad):
            pytest.fail("invalid clock padding entered placement")
    assert partition_placement.compact_tile is compact


def test_clock_reserve_restores_placer_after_failure():
    compact, domains = partition_placement.compact_tile, partition_placement.plan_bounded_domains
    with pytest.raises(RuntimeError, match="injected"):
        with reserve_clock_tracks({2}, 64):
            raise RuntimeError("injected")
    assert partition_placement.compact_tile is compact
    assert partition_placement.plan_bounded_domains is domains


def test_actual_clock_pulses_match_on_every_register_of_the_previously_failing_leaf(reserved_cache_leaf):
    from test_mchprs_runtime import high_runs, run_case
    from shamaos.hardware.logic import template_for
    delta = Vec3(32 - reserved_cache_leaf.bounds.min.x,
                 32 - reserved_cache_leaf.bounds.min.y,
                 32 - reserved_cache_leaf.bounds.min.z)
    placed = reserved_cache_leaf.translated(delta)
    tile = next(iter(placed.modules.values()))
    clock = next(net for net in tile.physical.nets if net.bit == 2)
    clocks = {sink.name: sink.pos for sink in clock.sinks
              if sink.pin == "C" and sink.name in tile.physical.cell_types
              and template_for(tile.physical.cell_types[sink.name]).cell_type == "DFF"}
    assert len(clocks) == 7
    half_period = 256
    steps = [{"drive": {"clock": False}, "ticks": 512}]
    for _ in range(3):
        steps.extend(({"drive": {"clock": True}, "ticks": half_period},
                      {"drive": {"clock": False}, "ticks": half_period}))
    steps.append({"ticks": 512, "expect": {name: False for name in clocks}})
    result = run_case(placed.iter_placements(), {"clock": clock.source.pos}, clocks,
                      steps, initial_ticks=512, capture_trace=True)
    traces = list(result["traces"].values())
    assert all(trace == traces[0] for trace in traces)
    assert high_runs(traces[0]) == [half_period] * 3


def test_complete_clock_analyzer_rejects_missing_pulses_wrong_width_and_skew():
    from experiments.memory_controller_runtime.probe_controller import analyze_clocks
    pulse = [False, True, True, False] * 3
    good = {"traces": {"a": pulse, "b": pulse}}
    assert analyze_clocks(good, ("a", "b"), 2)["clock_gate_passed"]
    for traces in ({"a": [False] * 12, "b": [False] * 12},
                   {"a": pulse, "b": [False] + pulse[:-1]},
                   {"a": [False, True, False, False] * 3,
                    "b": [False, True, False, False] * 3}):
        assert not analyze_clocks({"traces": traces}, ("a", "b"), 2)["clock_gate_passed"]
    with pytest.raises(ValueError, match="inventory"):
        analyze_clocks({"traces": {"a": pulse}}, ("a", "b"), 2)
