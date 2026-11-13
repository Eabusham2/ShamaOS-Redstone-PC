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


@pytest.fixture(scope="module")
def small_connected_facade():
    """Small real graph for binding guards, never a complete-cache surrogate."""
    from experiments.partition_placement.port_egress import place_with_egress
    from shamaos.hardware.partition_planner import Graph
    mapped = fixture.ROOT / "experiments/partition_placement/raised_exports/evidence/pipeline-mapped.json"
    graph = Graph.read(mapped, "pipeline")
    plan = build_plan(graph, cell_budget=1, boundary_budget=8, domain_budget=5, arity=4)
    return graph, plan, place_with_egress(graph, plan)


def _connected_facade_inputs(tmp_path, small_connected_facade):
    from experiments.memory_controller_runtime import save_connected
    from experiments.partition_placement.port_egress_manifest import write_egress_manifest
    graph, plan, placed = small_connected_facade
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan))
    plan_sha = save_connected._sha(plan_path)
    manifest_path = tmp_path / "facade.json.gz"
    record = write_egress_manifest(manifest_path, graph, plan, placed,
        provenance={"plan_sha256": plan_sha, "clock_balance_verified": True})
    document = json.loads(gzip.decompress(manifest_path.read_bytes()))
    report = {"status": save_connected.PASSED_FACADE, "balanced_manifest": record,
              "mapped_source": graph.source, "plan_sha256": plan_sha,
              "source_hashes": document["original_geometry"]["provenance"]["source_hashes"]}
    path = tmp_path / "facade-report.json"
    path.write_text(json.dumps(report))
    return graph, path, plan_path, manifest_path, report, document


def test_connected_emitter_accepts_only_bound_balanced_facade(tmp_path, small_connected_facade):
    from experiments.memory_controller_runtime.save_connected import _accepted_facade
    graph, path, plan, manifest, report, _ = _connected_facade_inputs(tmp_path, small_connected_facade)
    actual, record, actual_manifest = _accepted_facade(path, plan, graph)
    assert actual == report
    assert record == report["balanced_manifest"] and actual_manifest == manifest


@pytest.mark.parametrize("fault", ["source", "plan", "unbalanced", "report-source", "mapped", "manifest"])
def test_connected_emitter_rejects_changed_sources_plan_or_geometry(tmp_path, small_connected_facade, fault):
    from experiments.memory_controller_runtime.save_connected import _accepted_facade, _sha
    graph, path, plan, manifest, report, document = _connected_facade_inputs(tmp_path, small_connected_facade)
    provenance = document["original_geometry"]["provenance"]
    if fault == "source":
        name = next(iter(provenance["source_hashes"]))
        provenance["source_hashes"][name] = "0" * 64
        report["source_hashes"][name] = "0" * 64
    elif fault == "plan":
        plan.write_text(plan.read_text() + "\nchanged")
    elif fault == "unbalanced":
        provenance["clock_balance_verified"] = False
    elif fault == "report-source":
        report["source_hashes"] = dict(report["source_hashes"], **{"invented.py": "0" * 64})
    elif fault == "mapped":
        report["mapped_source"] = dict(report["mapped_source"], sha256="0" * 64)
    if fault in ("source", "unbalanced"):
        # Correctly rehashing a tampered document cannot make its provenance valid.
        manifest.write_bytes(gzip.compress(json.dumps(document).encode(), mtime=0))
        report["balanced_manifest"]["sha256"] = _sha(manifest)
    if fault == "manifest":
        manifest.write_bytes(manifest.read_bytes() + b"changed")
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError):
        _accepted_facade(path, plan, graph)


def test_connected_emitter_never_replaces_existing_output(tmp_path, monkeypatch):
    from experiments.memory_controller_runtime import save_connected
    destination = tmp_path / "existing-world"
    destination.mkdir()
    sentinel = destination / "user-data"
    sentinel.write_bytes(b"preserved")
    monkeypatch.setattr(fixture, "graph_and_fabric", lambda: pytest.fail("existing destination was not rejected first"))
    with pytest.raises(FileExistsError):
        save_connected.save_connected(tmp_path / "unread", tmp_path / "unread-plan", destination)
    assert sentinel.read_bytes() == b"preserved"
    assert list(destination.iterdir()) == [sentinel]


def test_connected_interconnect_rejects_reduced_physical_capacity_before_routing(complete_cache):
    from types import SimpleNamespace
    from experiments.memory_controller_runtime.interconnect import connect_cache, iter_complete_banks
    graph, _, fabric, origin = complete_cache
    reduced = SimpleNamespace(total_bytes=4096, bank_count=1, bank=fabric.bank,
                              layers=1, layer_pitch=fabric.layer_pitch, decoder_x_offset=fabric.decoder_x_offset)
    with pytest.raises(ValueError, match="both complete"):
        connect_cache(graph, None, reduced, origin)
    with pytest.raises(ValueError, match="exactly 8 KiB"):
        list(iter_complete_banks(None, fabric, bytes(4096)))


@pytest.mark.parametrize("fault", [None, "missing-terminal", "occupied-pad", "unrelated-neighbor", "aliased-pad"])
def test_connected_host_pad_checks_saved_native_blocks(tmp_path, fault):
    from experiments.memory_controller_runtime.save_connected import _validate_native_driver_pads
    from shamaos.hardware.memory import DUST, SUPPORT
    from shamaos.model import Placement
    from shamaos.worldio import NativeWorldReader, NativeWorldWriter
    terminal = Vec3(0, 0, 0)
    blocks = [Placement(terminal.offset(dy=-1), SUPPORT, "test-terminal")]
    if fault != "missing-terminal":
        blocks.append(Placement(terminal, DUST, "test-terminal"))
    if fault == "occupied-pad":
        blocks.append(Placement(terminal.offset(dz=1), SUPPORT, "bad-driver-pad"))
    if fault == "unrelated-neighbor":
        blocks.append(Placement(terminal.offset(dz=2), SUPPORT, "bad-neighbor"))
    world = tmp_path / "native"
    with NativeWorldWriter(world, surface_y=-61, spawn_platform=False) as writer:
        writer.set_blocks(blocks)
    inputs = {"host:clk:0": [0, 0, 0]}
    if fault == "aliased-pad":
        inputs["host:rst:0"] = [0, 0, 0]
    reader = NativeWorldReader(world)
    if fault is None:
        result = _validate_native_driver_pads(reader, inputs)
        assert result["host_pads"] == 1
        assert result["each_pad_directly_touches_only_its_intended_terminal"]
    else:
        with pytest.raises(ValueError, match="host"):
            _validate_native_driver_pads(reader, inputs)


def test_connected_union_methods_preserve_exact_small_native_states(tmp_path):
    """Exercise both collectors and the actual encoder independently of placement."""
    from dataclasses import asdict
    from experiments.partition_placement.raised_exports.streamed_union import literal_union
    from shamaos.bounded_build import _LiteralUnion
    from shamaos.hardware.memory import MemoryBankSpec, iter_memory_bank
    from shamaos.model import Box
    from shamaos.worldio import NativeWorldReader, NativeWorldWriter, validate_native_world
    origin = Vec3(16, -56, 16)
    bank = MemoryBankSpec(words=4, word_bits=32)
    values = (0, 0xFFFFFFFF, 0x55AA55AA, 0xA596C33C)
    initial = b"".join(word.to_bytes(4, "little") for word in values)
    placements = lambda: iter_memory_bank(origin, spec=bank, initial=initial)
    first = literal_union(placements(), run_records=257, merge_fan_in=3, temporary_parent=tmp_path)
    bounds = Box(Vec3(**first["bounds"]["min"]), Vec3(**first["bounds"]["max"]))
    union = _LiteralUnion(tmp_path / "union.sqlite3")
    try:
        second = union.collect(placements(), surface_y=-61, maximum_y=319,
                               bounds=bounds, expected_unique=first["literal_unique_blocks"])
        assert first["sorted_unique_record_sha256"] == second["sorted_unique_record_sha256"]
        assert first["bounds"] == second["bounds"] == asdict(bounds)
        assert json.loads(json.dumps(first["palette"])) == json.loads(json.dumps(second["palette"]))
        world = tmp_path / "world"
        with NativeWorldWriter(world, surface_y=-61, spawn_platform=False) as writer:
            writer.set_blocks(union.placements())
        result = validate_native_world(world)
        assert result["assigned_blocks"] == first["literal_unique_blocks"]
        assert result["chunks"] == first["occupied_chunks"]
        reader = NativeWorldReader(world)
        for row, word in enumerate(values):
            for bit in range(32):
                state = reader.get_block(origin.offset(bit * bank.bit_pitch + 2, bank.cell_y,
                                                     3 + row * bank.word_pitch))
                assert state.name == "minecraft:repeater"
                assert dict(state.properties)["powered"] == str(bool(word >> bit & 1)).lower()
                assert dict(state.properties)["locked"] == "true"
    finally:
        union.close()


@pytest.fixture
def runtime_clock_evidence(tmp_path):
    """Synthetic traces test the evidence gate; they never prove physical timing."""
    from experiments.memory_controller_runtime import run_connected as runtime
    from experiments.memory_controller_runtime.probe_controller import clock_scenario
    runner = tmp_path / "runner-identity"
    runner.write_bytes(b"identity only; never executed")
    prepared_directory = tmp_path / "prepared"
    prepared_directory.mkdir()
    (prepared_directory / "prepared.json").write_text('{"synthetic_guard_test":true}\n')
    prepared = {"stream": {"sha256": "1" * 64}, "runtime_helper_sources": runtime._helper_sources()}
    imported = {"stream_sha256": "1" * 64, "chunks": 1, "nonair": 116,
                "comparators": 0, "source": {"synthetic_guard_test": True}}
    inputs = {host("clk"): [0, 0, 0], host("rst"): [4, 0, 0]}
    outputs = {port("req_ready"): [8, 0, 0]}
    clock_points = {f"register:test-{index}:C": Vec3(index * 4, 4, 0) for index in range(115)}
    drive = {"clk": Vec3(0, 0, 0), "rst": Vec3(4, 0, 0)}
    report = {"status": runtime.CLOCKS_PASSED, "native_clock_gate_verified": True,
        "world_stream": prepared["stream"], "runtime_helper_sources": prepared["runtime_helper_sources"],
        "prepared": runtime._record(prepared_directory / "prepared.json"),
        "runner": runtime._record(runner), "cases": {}}
    for name, period in (("safe", 2), ("too-fast", 1)):
        scenario, samples = clock_scenario(drive, clock_points, Vec3(8, 0, 0), half_period=period, settle_ticks=1)
        scenario_path, result_path = tmp_path / f"{name}-scenario.json", tmp_path / f"{name}-result.json"
        scenario_path.write_text(json.dumps(scenario))
        captured = sum(step["ticks"] for step in scenario["steps"])
        trace = ([False] + [True, True, False, False] * 3 + [False]
                 if name == "safe" else [False, True, True, True, False, False, False, False])
        assert len(trace) == captured
        result = {"status": "passed", "imported_stream_sha256": "1" * 64,
            "scenario_sha256": runtime._sha(scenario_path), "source": imported["source"],
            "loaded_chunks": 1, "decoded_nonair_blocks": 116, "initialized_nonair_blocks": 116,
            "comparator_entities": 0, "steps": len(scenario["steps"]),
            "ticks": 1 + captured, "assertions": 116, "complete_machine_runtime_verified": False,
            "engine_revision": runtime.ENGINE_REVISION, "engine_registry_patch_sha256": runtime.TERRAIN_PATCH,
            "traces": {name: trace for name in clock_points}}
        result_path.write_text(json.dumps(result))
        report["cases"][name] = {"status": "passed" if name == "safe" else "too-fast-failure-detected",
            "half_period_ticks": period, "settle_ticks": 1, "exit_code": 0,
            "wall_seconds": 0.1, "wall_limit_seconds": 60, "explicit_wall_timeout_reached": False,
            "trace_samples": samples, "scenario": runtime._record(scenario_path), "stdout": runtime._record(result_path)}
    arguments = (report, prepared_directory, prepared, runner, imported, inputs, outputs, clock_points)
    return runtime, arguments


def test_native_runtime_clock_gate_rechecks_both_bound_sample_sets(runtime_clock_evidence):
    runtime, arguments = runtime_clock_evidence
    checked = runtime._verify_clock_report(*arguments, 2)
    assert checked["safe"]["clock_gate_passed"]
    assert not checked["too-fast"]["clock_gate_passed"]
    assert checked["safe"]["register_clocks"] == 115


@pytest.mark.parametrize("name", ["safe", "too-fast"])
@pytest.mark.parametrize("process", [
    {"explicit_wall_timeout_reached": True, "wall_seconds": 60.1},
    {"explicit_wall_timeout_reached": False, "wall_seconds": 60},
])
def test_native_runtime_clock_gate_rejects_late_successful_exit(runtime_clock_evidence, name, process):
    runtime, arguments = runtime_clock_evidence
    case = arguments[0]["cases"][name]
    assert case["exit_code"] == 0
    case.update(process)
    with pytest.raises(ValueError, match="wall deadline"):
        runtime._verify_clock_report(*arguments, 2)


@pytest.mark.parametrize("mode", ["clocks", "transactions"])
@pytest.mark.parametrize("process", [
    {"explicit_wall_timeout_reached": True, "wall_seconds": 1.1, "wall_limit_seconds": 1},
    {"explicit_wall_timeout_reached": False, "wall_seconds": 1, "wall_limit_seconds": 1},
])
def test_native_runtime_callers_preserve_and_reject_late_success(
        runtime_clock_evidence, monkeypatch, tmp_path, mode, process):
    """Late native success cannot become either a clock or transaction pass."""
    runtime, arguments = runtime_clock_evidence
    clock_report, prepared_directory, prepared, runner, imported, inputs, outputs, points = arguments
    # The small synthetic fixture checks the caller gate, never physical data.
    stream = tmp_path / "unexecuted-world.jsonl"
    stream.write_text('{"synthetic_guard_test":true}\n')
    prepared["stream"] = runtime._record(stream)
    clock_report["world_stream"] = prepared["stream"]
    monkeypatch.setattr(runtime, "_load", lambda _: (prepared, imported, inputs, outputs, points))
    calls = []

    def late_success(*args, **kwargs):
        calls.append((args, kwargs))
        # Deliberately not a native JSON result: any attempted parsing would
        # fail differently, proving the process guard runs before acceptance.
        stdout = args[3] / "late-result.json"
        stdout.write_text("late result must never be evaluated")
        return dict(process, exit_code=0, stdout=runtime._record(stdout))

    monkeypatch.setattr(runtime, "execute", late_success)
    destination = tmp_path / f"late-{mode}"
    if mode == "clocks":
        action = lambda: runtime.clocks(prepared_directory, runner, destination,
                                        half_period=2, settle_ticks=1)
        report_name, case_name = "clock-report.json", "safe"
    else:
        # The retained clock report is actually revalidated before entering
        # this transaction caller. Only the large scenario writer is replaced.
        clock_report_path = tmp_path / "clock-report.json"
        clock_report_path.write_text(json.dumps(clock_report))

        def write_scenario(path, frames, *_args, **_kwargs):
            path.write_text('{"synthetic_process_gate_only":true}\n')
            return {"ticks": len(frames), "assertions": 0}

        monkeypatch.setattr(runtime.scenarios, "write_scenario", write_scenario)
        action = lambda: runtime.transactions(prepared_directory, runner, clock_report_path,
            destination, half_period=2, initial_ticks=1, smoke=True)
        report_name, case_name = "transaction-report.json", "smoke"
    with pytest.raises(TimeoutError, match="wall deadline"):
        action()
    saved = json.loads((destination / report_name).read_text())
    assert saved["status"] == "failed" and saved["error_type"] == "TimeoutError"
    assert saved["connected_cache_runtime_verified"] is False
    assert saved["complete_machine_runtime_verified"] is False
    assert saved.get("native_clock_gate_verified", False) is False
    assert saved.get("native_persistence_verified", False) is False
    assert set(saved["cases"]) == {case_name}
    assert saved["cases"][case_name]["status"] == "execution-incomplete"
    assert saved["cases"][case_name]["exit_code"] == 0
    assert {key: saved["cases"][case_name][key] for key in process} == process
    assert len(calls) == 1


@pytest.mark.parametrize("fault", ["no-cases", "missing-negative", "invented-case", "faster-period", "slower-period",
    "different-scenario", "changed-safe-trace", "false-negative", "lost-clock", "other-stream", "other-helper"])
def test_native_runtime_clock_gate_rejects_incomplete_or_false_evidence(runtime_clock_evidence, fault):
    runtime, arguments = runtime_clock_evidence
    report = arguments[0]
    period = 2
    if fault == "no-cases":
        report["cases"] = {}
    elif fault == "missing-negative":
        del report["cases"]["too-fast"]
    elif fault == "invented-case":
        report["cases"]["invented"] = report["cases"]["safe"]
    elif fault == "faster-period":
        period = 1
    elif fault == "slower-period":
        period = 3
    elif fault == "other-helper":
        report["runtime_helper_sources"] = dict(report["runtime_helper_sources"], **{"invented.py": "0" * 64})
    else:
        name = "too-fast" if fault == "false-negative" else "safe"
        case = report["cases"][name]
        key = "scenario" if fault == "different-scenario" else "stdout"
        path = Path(case[key]["path"])
        document = json.loads(path.read_text())
        if fault == "different-scenario":
            document["steps"][1]["drive"]["clk"] = False
        elif fault == "other-stream":
            document["imported_stream_sha256"] = "0" * 64
        elif fault == "lost-clock":
            del document["traces"][next(iter(document["traces"]))]
        elif fault == "changed-safe-trace":
            name = next(iter(document["traces"]))
            document["traces"][name] = [False] * len(document["traces"][name])
        else:
            document["traces"] = {name: [False, True, False, True, False, True, False, False]
                                  for name in document["traces"]}
        path.write_text(json.dumps(document))
        # Rehashing a false scenario or trace cannot restore its acceptance.
        case[key] = runtime._record(path)
    with pytest.raises(ValueError):
        runtime._verify_clock_report(*arguments, period)


@pytest.mark.parametrize("fault", ["missing", "changed", "extra"])
def test_native_runtime_helper_inventory_is_exact(fault):
    from experiments.memory_controller_runtime import run_connected as runtime
    expected = runtime._helper_sources()
    if fault == "missing":
        expected.pop(runtime.RUNTIME_SOURCES[-1])
    elif fault == "changed":
        expected[runtime.RUNTIME_SOURCES[-1]] = "0" * 64
    else:
        expected["invented.py"] = "0" * 64
    with pytest.raises(ValueError, match="helper sources changed"):
        runtime._check_helpers(expected)
