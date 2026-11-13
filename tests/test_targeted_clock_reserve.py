"""Targeted clock space must retain all sibling wires and the real matcher."""
from copy import deepcopy
from pathlib import Path
import hashlib
import json
import zipfile

import pytest

from experiments.memory_controller_runtime.clock_reserve import reserve_clock_tracks
from experiments.partition_placement.clock_capacity import diagnose_clock_capacity
from experiments.partition_placement.port_egress import place_with_egress
from experiments.partition_placement.targeted_clock_reserve import parse_domain_padding, reserve_targeted_clock_tracks
from shamaos.hardware import partition_placement as base
from shamaos.hardware.partition_planner import Graph, build_plan
from test_port_egress_placement import test_actual_exterior_io_pipeline_and_too_fast_negative as _actual_pipeline_case


@pytest.fixture(scope="module")
def clock_graph_plan():
    path = Path(__file__).resolve().parents[1] / "experiments/partition_placement/raised_exports/evidence/pipeline-mapped.json"
    graph = Graph.read(path, "pipeline")
    plan = build_plan(graph, cell_budget=1, boundary_budget=8, domain_budget=5, arity=4)
    target = next(record["id"] for record in plan["routing_domains"] if any(segment["net"] == 2 for segment in record["segments"]))
    return graph, plan, target


def test_empty_override_is_exactly_the_original_clock_reservation(clock_graph_plan):
    graph, plan, _ = clock_graph_plan
    with reserve_clock_tracks({2}, 8):
        old = place_with_egress(graph, plan)
    with reserve_targeted_clock_tracks({2}, 8, {}):
        new = place_with_egress(graph, plan)
    assert base._literal_states(old.iter_placements()) == base._literal_states(new.iter_placements())
    assert old.all_modules() == new.all_modules()
    assert old.all_facade_modules() == new.all_facade_modules()
    assert old.bounds == new.bounds and old.unique_blocks == new.unique_blocks


def test_target_moves_every_sibling_including_facade_and_nothing_else(clock_graph_plan, monkeypatch):
    graph, plan, target = clock_graph_plan
    original = base.plan_bounded_domains
    events = []
    def observe(*args, **kwargs):
        tiles = original(*args, **kwargs)
        events.append((tiles, deepcopy(tiles)))
        return tiles
    monkeypatch.setattr(base, "plan_bounded_domains", observe)
    with reserve_targeted_clock_tracks({2}, 8, {target: 32}) as applications:
        placed = place_with_egress(graph, plan, balance_clocks=False)
    assert base.plan_bounded_domains is observe
    targeted_facade = False
    for tiles, before in events:
        targeted = any(tile.physical.top == f"domain-{target}" for tile in tiles)
        has_clock = any(net.bit == 2 for tile in tiles for net in tile.physical.nets)
        shift = 32 if targeted else 8 if has_clock else 0
        for new, old in zip(tiles, before, strict=True):
            assert new.physical == old.physical
            assert new.escapes == old.escapes and new.tracks == old.tracks
            assert new.branch_y == old.branch_y and new.trunk_y == old.trunk_y
            assert new.overrides == old.overrides and new.clock_delays == old.clock_delays
            assert new.track_base_z == old.track_base_z - shift
            targeted_facade |= targeted and new.physical.top.startswith("original-io-")
    assert targeted_facade
    assert any(record["targeted_domains"] == [target] and record["absolute_padding_blocks"] == 32
               for record in applications.values())
    assert len(base._literal_states(placed.iter_placements())) == placed.unique_blocks
    before = {key: (dict(tile.overrides), deepcopy(tile.clock_delays), deepcopy(tile._balanced_positions))
              for key, tile in placed.all_modules().items()}
    diagnosis = diagnose_clock_capacity(plan, placed)
    assert diagnosis["maximum_shortfall"] == 0
    assert before == {key: (tile.overrides, tile.clock_delays, tile._balanced_positions)
                      for key, tile in placed.all_modules().items()}
    actual = base.balance_partition_clocks(plan, placed)
    assert diagnosis["clocks"]["2"]["ideal_original_pin_to_register_delay"] == actual[2]["balanced_repeater_delay"]
    first = base._literal_states(placed.iter_placements())
    assert base.balance_partition_clocks(plan, placed) == actual
    assert base._literal_states(placed.iter_placements()) == first


@pytest.mark.parametrize("bad", [{True: 640}, {-1: 640}, {4: True}, {4: 3}, {4: 64}, {4: 4100}, {4: 640.0}, []])
def test_invalid_target_rejected_before_changing_any_placer(bad):
    originals = base.compact_tile, base.plan_bounded_domains
    with pytest.raises(ValueError):
        with reserve_targeted_clock_tracks({2}, 128, bad):
            pytest.fail("invalid target entered placement")
    assert (base.compact_tile, base.plan_bounded_domains) == originals


@pytest.mark.parametrize("values", [["4=640", "4=644"], ["04=640"], ["4=0640"], ["4:640"], ["4=True"]])
def test_cli_target_identity_is_exact(values):
    with pytest.raises(ValueError):
        parse_domain_padding(values, 128)
    assert parse_domain_padding(["4=640"], 128) == {4: 640}


def test_missing_target_and_body_failure_restore_placer_hooks(clock_graph_plan):
    graph, plan, target = clock_graph_plan
    originals = base.compact_tile, base.plan_bounded_domains
    with pytest.raises(ValueError, match="did not encounter"):
        with reserve_targeted_clock_tracks({2}, 8, {999: 32}):
            place_with_egress(graph, plan)
    assert (base.compact_tile, base.plan_bounded_domains) == originals
    with pytest.raises(RuntimeError, match="injected"):
        with reserve_targeted_clock_tracks({2}, 8, {target: 32}):
            raise RuntimeError("injected builder failure")
    assert (base.compact_tile, base.plan_bounded_domains) == originals
    with pytest.raises(ValueError, match="integer signal"):
        with reserve_targeted_clock_tracks((0, False), 8, {}):
            pytest.fail("boolean clock alias entered placement")
    assert (base.compact_tile, base.plan_bounded_domains) == originals


@pytest.fixture(scope="module")
def targeted_pipeline(clock_graph_plan):
    graph, plan, target = clock_graph_plan
    with reserve_targeted_clock_tracks({2}, 8, {target: 32}):
        placed = place_with_egress(graph, plan)
    return graph, plan, placed


@pytest.mark.parametrize("half_period,should_pass", [(512, True), (20, False)])
def test_targeted_real_clock_routes_preserve_pipeline_and_reject_fast_input(targeted_pipeline, half_period, should_pass, tmp_path):
    _actual_pipeline_case(targeted_pipeline, half_period, should_pass, tmp_path)


def test_retained_full_shortfall_still_rejects_and_rolls_back_all_matches(tmp_path):
    from experiments.partition_placement.port_egress_manifest import read_egress_manifest
    evidence = Path(__file__).resolve().parents[1] / "experiments/partition_placement/evidence"
    prepared = json.loads((evidence / "cache-facade-packed-prepared.json").read_text())
    report = json.loads((evidence / "cache-facade-packed-unbalanced-report.json").read_text())
    archive = evidence / "cache-facade-packed-source-bundle.zip"
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == prepared["source_bundle_sha256"]
    with zipfile.ZipFile(archive) as bundle:
        assert all(not Path(name).is_absolute() and ".." not in Path(name).parts for name in bundle.namelist())
        bundle.extractall(tmp_path)
    graph = Graph.read(tmp_path / "inputs/cache-controller-c331.json.gz", "shama_cache_adapter")
    plan = json.loads((tmp_path / "inputs/plan.json").read_text())
    placed = read_egress_manifest(evidence / "cache-facade-packed-unbalanced-manifest.json.gz",
        report["unbalanced_manifest"]["sha256"], graph, plan, source_root=tmp_path)
    snapshot = lambda: {key: (dict(tile.overrides), deepcopy(tile.clock_delays), deepcopy(tile._balanced_positions))
                        for key, tile in placed.all_modules().items()}
    before = snapshot()
    for _ in range(2):
        with pytest.raises(ValueError, match="1677 additional delay ticks"):
            base.balance_partition_clocks(plan, placed)
        assert snapshot() == before
