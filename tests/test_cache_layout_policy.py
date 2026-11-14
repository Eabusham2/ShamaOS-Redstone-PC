"""Only the measured one-bank cache selects the compact production layout."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from shamaos.hardware import cache_layout, partition_placement
from shamaos.hardware.bounded_facade import place_with_egress
from shamaos.hardware.partition_planner import Graph, audit_plan, build_plan


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "experiments/memory_controller_runtime/current_four_kib/evidence"


@pytest.fixture(scope="module")
def current_evidence(tmp_path_factory):
    directory = tmp_path_factory.mktemp("frozen-current-cache")
    receipt = json.loads((EVIDENCE / "receipt.json").read_text())
    bundle = ROOT / receipt["bundle"]["path"]
    assert hashlib.sha256(bundle.read_bytes()).hexdigest() == receipt["bundle"]["sha256"]
    with zipfile.ZipFile(bundle) as archive:
        assert len(archive.namelist()) == len(set(archive.namelist())) == receipt["bundle"]["files"]
        assert set(archive.namelist()) == set(receipt["inventory"])
        for name, digest in receipt["inventory"].items():
            path = directory / name
            assert path.resolve().is_relative_to(directory)
            data = archive.read(name)
            assert hashlib.sha256(data).hexdigest() == digest
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    graph = Graph.read(directory / "inputs/current/mapped.json", "shama_cache_adapter")
    plan = json.loads((directory / "inputs/current/plan.json").read_text())
    return directory, graph, plan, receipt


def test_retained_current_cache_has_every_bound_source_plan_cell_and_clock(current_evidence):
    directory, graph, plan, receipt = current_evidence
    prepared = json.loads((directory / "inputs/current/prepared.json").read_text())
    accepted = json.loads((directory / "inputs/current/facade/report.json").read_text())
    sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    for record in (prepared["generator_hashes"], accepted["source_hashes"]):
        assert all(sha(directory / name) == digest for name, digest in record.items())
    assert all(sha(directory / "inputs/current/source" / name) == digest
               for name, digest in prepared["input_hashes"].items())
    assert sha(directory / "inputs/current/plan.json") == prepared["plan_sha256"] == accepted["plan_sha256"]
    assert audit_plan(graph, plan)["status"] == "passed"
    assert (graph.cell_count, receipt["original_port_bits"], receipt["register_clock_pins"]) == (1178, 182, 115)
    assert (receipt["controller_unique_blocks"], receipt["controller_occupied_chunks"]) == (15645176, 67206)
    assert not receipt["connected_cache_runtime_verified"]
    assert not receipt["whole_machine_verified"]


def test_cache_policy_reproduces_exact_current_frozen_plan(current_evidence):
    _, graph, expected, _ = current_evidence
    policy = cache_layout.cache_policy(graph)
    actual = build_plan(graph, cell_budget=policy["native_cells_per_leaf"],
        boundary_budget=policy["boundary_terminals_per_leaf"],
        domain_budget=policy["routing_terminals_per_domain"],
        arity=policy["children_per_cluster"], ports_at_root=policy["ports_at_root"])
    for name in ("summary", "budgets", "entities", "clusters", "entity_leaf", "leaf_boundaries",
                 "routing_domains", "signals", "constants", "root_cluster"):
        assert json.loads(json.dumps(actual[name])) == expected[name]
    assert policy["clock_domain_padding_blocks"] == {6: 160}
    assert policy["routing_terminals_per_domain"] == 256
    assert not policy["native_runtime_verified"]


@pytest.mark.parametrize("change", ["other-top", "two-banks", "missing-pin", "wrong-direction"])
def test_cache_policy_cannot_silently_select_another_partition_or_partial_interface(current_evidence, change):
    _, original, _, _ = current_evidence
    graph = deepcopy(original)
    if change == "other-top":
        graph.source["top"] = "shama_ram_adapter"
    elif change == "two-banks":
        pin = next(e for e in graph.entities if e.get("port_name") == "bank_select")
        graph.entities.append(dict(pin, port_index=1))
    elif change == "missing-pin":
        graph.entities.remove(next(e for e in graph.entities if e.get("port_name") == "req_we"))
    else:
        next(e for e in graph.entities if e.get("port_name") == "req_we")["direction"] = "output"
    if change in ("other-top", "two-banks"):
        assert cache_layout.cache_policy(graph) is None
    else:
        with pytest.raises(ValueError, match="complete one-bank controller interface"):
            cache_layout.cache_policy(graph)


@pytest.fixture(scope="module")
def small_plan():
    graph = Graph.read(ROOT / "experiments/partition_placement/raised_exports/evidence/pipeline-mapped.json", "pipeline")
    plan = build_plan(graph, cell_budget=2, boundary_budget=8, domain_budget=5, arity=4, ports_at_root=True)
    clocks = {record["bit"] for record in plan["signals"] if any(sink["pin"] == "C" for sink in record["sinks"])}
    target = next(domain["id"] for domain in plan["routing_domains"]
                  if any(segment["net"] in clocks for segment in domain["segments"]))
    return graph, plan, clocks, target


def test_production_clock_reservation_emits_exact_accepted_experimental_geometry(small_plan):
    from experiments.partition_placement.port_egress import place_with_egress as experimental_place
    from experiments.partition_placement.targeted_clock_reserve import reserve_targeted_clock_tracks
    graph, plan, clocks, target = small_plan
    with reserve_targeted_clock_tracks(clocks, 128, {target: 160}) as previous_groups:
        previous = experimental_place(graph, plan, balance_clocks=False)
    previous_clock = partition_placement.balance_partition_clocks(plan, previous)
    with cache_layout.reserve_clock_space(clocks, 128, {target: 160}) as groups:
        actual = place_with_egress(graph, plan, balance_clocks=False)
    actual_clock = partition_placement.balance_partition_clocks(plan, actual)
    assert previous_groups == groups
    assert previous_clock == actual_clock
    assert actual.bounds == previous.bounds
    assert partition_placement._literal_states(actual.iter_placements()) == partition_placement._literal_states(previous.iter_placements())
    for key, tile in actual.all_modules().items():
        other = previous.all_modules()[key]
        assert tile.overrides == other.overrides
        assert tile._balanced_positions == other._balanced_positions


@pytest.mark.parametrize("fault", ["absent", "not-clock", "prebalanced", "during-placement"])
def test_clock_reservation_rejects_invalid_targets_and_restores_core_callbacks(monkeypatch, fault):
    clock = SimpleNamespace(bit=2)
    tile = SimpleNamespace(physical=SimpleNamespace(top="domain-6", nets=[clock]),
                           overrides={}, clock_delays={}, _balanced_positions={}, track_base_z=1000)
    if fault == "not-clock": tile.physical.nets = []
    if fault == "prebalanced": tile.overrides = {"already": "matched"}
    def domains(*args, **kwargs):
        if fault == "during-placement": raise RuntimeError("original placement rejected geometry")
        return [tile]
    monkeypatch.setattr(partition_placement, "plan_bounded_domains", domains)
    original = partition_placement.compact_tile
    error = RuntimeError if fault == "during-placement" else ValueError
    with pytest.raises(error):
        with cache_layout.reserve_clock_space({2}, 128, {6: 160}):
            if fault != "absent": partition_placement.plan_bounded_domains()
    assert partition_placement.compact_tile is original
    assert partition_placement.plan_bounded_domains is domains


@pytest.mark.parametrize("target", [10000, "not-a-clock"])
def test_complete_cache_policy_rejects_changed_domain_identity_before_geometry(current_evidence, monkeypatch, target):
    _, graph, plan, _ = current_evidence
    policy = cache_layout.cache_policy(graph)
    if target == "not-a-clock":
        clocks = {r["bit"] for r in plan["signals"] if any(s["pin"] == "C" for s in r["sinks"])}
        target = next(d["id"] for d in plan["routing_domains"] if all(s["net"] not in clocks for s in d["segments"]))
    policy["clock_domain_padding_blocks"] = {target: 160}
    monkeypatch.setattr(cache_layout, "cache_policy", lambda _: policy)
    def forbidden(*args, **kwargs): raise AssertionError("invalid target reached physical placement")
    monkeypatch.setattr(cache_layout, "place_with_egress", forbidden)
    with pytest.raises(ValueError, match="real clock-bearing domain"):
        cache_layout.place_compact_cache(graph)


def test_production_cache_hook_keeps_real_matcher_rejection(current_evidence, monkeypatch):
    _, graph, _, _ = current_evidence
    sentinel = SimpleNamespace(packing={})
    from contextlib import contextmanager
    @contextmanager
    def reservation(*args): yield {}
    monkeypatch.setattr(cache_layout, "reserve_clock_space", reservation)
    monkeypatch.setattr(cache_layout, "place_with_egress", lambda *a, **k: sentinel)
    def insufficient(*args): raise partition_placement.PlacementError("actual clock route lacks matching space")
    monkeypatch.setattr(partition_placement, "balance_partition_clocks", insufficient)
    with pytest.raises(partition_placement.PlacementError, match="lacks matching space"):
        cache_layout.place_compact_cache(graph)
    assert "cache_layout_policy" not in sentinel.packing


@pytest.mark.parametrize("name,enabled,domain_budget,selected", [("u_cache", 1, 128, "compact"),
    ("u_cache", 1, 100, "compact"), ("u_cache", 0, 128, "generic"), ("u_other", 1, 128, "generic")])
def test_bounded_backend_selects_compaction_only_for_the_named_enabled_cache(
        current_evidence, monkeypatch, name, enabled, domain_budget, selected):
    from shamaos import bounded_system
    _, graph, _, _ = current_evidence
    monkeypatch.setattr(bounded_system.Graph, "read", lambda *args: graph)
    def compact(graph, **kwargs):
        assert cache_layout.cache_policy(graph)["routing_terminals_per_domain"] == 256
        raise RuntimeError("compact selected")
    def generic(*args, **kwargs): raise RuntimeError("generic selected")
    monkeypatch.setattr(cache_layout, "place_compact_cache", compact)
    monkeypatch.setattr(bounded_system, "build_plan", generic)
    with pytest.raises(RuntimeError, match=selected + " selected"):
        bounded_system.prepare_from_artifacts([(name, "shama_cache_adapter", Path("not-read"))],
            Path("not-read-shell"), options={"cache_compaction": enabled, "domain_budget": domain_budget})


@pytest.mark.parametrize("setting,value", [("cell_budget", 66), ("boundary_budget", 64), ("arity", 3)])
def test_custom_cell_boundary_or_arity_requires_explicit_generic_cache_policy(
        current_evidence, monkeypatch, setting, value):
    from shamaos import bounded_system
    _, graph, _, _ = current_evidence
    monkeypatch.setattr(bounded_system.Graph, "read", lambda *args: graph)
    def forbidden(*args, **kwargs): raise AssertionError("unsupported compact policy reached placement")
    monkeypatch.setattr(cache_layout, "place_compact_cache", forbidden)
    with pytest.raises(ValueError, match="cell/boundary/arity 64/128/4"):
        bounded_system.prepare_from_artifacts([("u_cache", "shama_cache_adapter", Path("not-read"))],
            Path("not-read-shell"), options={setting: value})
