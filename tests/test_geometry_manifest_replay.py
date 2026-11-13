"""A retained complete manifest must recover geometry without route search."""
from copy import deepcopy
import builtins
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from experiments.partition_placement.raised_exports.geometry_manifest import write_manifest
from shamaos.hardware.partition_placement import PlacedCluster, _literal_states, audit_placement
from shamaos.hardware.partition_planner import Graph, build_plan
from shamaos.model import BlockState, Placement
from test_partition_placement import partition_pipeline_case

DIRECTORY = Path(__file__).resolve().parents[1] / "experiments/partition_placement/raised_exports"
SPEC = importlib.util.spec_from_file_location("retained_geometry_replay", DIRECTORY / "replay_manifest.py")
REPLAY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REPLAY)


@pytest.fixture
def manifest():
    path = DIRECTORY / "evidence/pipeline-manifest.json.gz"
    return REPLAY.read_manifest(path, hashlib.sha256(path.read_bytes()).hexdigest())


def _digest(value):
    return hashlib.sha256(json.dumps(value, separators=(",", ":")).encode()).hexdigest()


@pytest.mark.parametrize("extended_at", ["root", "descendant"])
def test_ordinary_manifest_rejects_additional_implementation_at_any_depth(manifest, tmp_path, extended_at):
    placed = REPLAY.restore_manifest(manifest)
    additional_position = placed.bounds.min.offset(dx=-32)

    class ExtraRouteCluster(PlacedCluster):
        def iter_placements(self):
            yield from super().iter_placements()
            yield Placement(additional_position, BlockState.of("minecraft:stone"), "extension-only-route")

    original_count = sum(1 for _ in placed.iter_placements())
    if extended_at == "root":
        placed = ExtraRouteCluster(**vars(placed))
    else:
        child, *others = placed.children
        placed.children = (ExtraRouteCluster(**vars(child)), *others)
    # The extra implementation really emits a block which the ordinary
    # module-only format would omit, rather than only changing a class label.
    assert sum(1 for _ in placed.iter_placements()) == original_count + 1
    path = tmp_path / "would-drop-extra-route.json.gz"
    with pytest.raises(ValueError, match="extended cluster"):
        write_manifest(path, placed, provenance={"scope": "test-only"})
    assert not path.exists()


def test_replayed_complete_pipeline_matches_independent_literal_and_port_oracle(manifest, monkeypatch):
    # A recovery must use its actual retained decisions, not perform a new
    # allocator run which happens to have the same final block count.
    from shamaos.hardware import external_router, local_router
    def no_search(*args, **kwargs):
        pytest.fail("manifest replay attempted a fresh route allocation")
    monkeypatch.setattr(external_router, "_plan_escapes", no_search)
    monkeypatch.setattr(local_router, "_plan_escapes", no_search)
    placed = REPLAY.restore_manifest(manifest)
    graph = Graph.read(DIRECTORY / "evidence/pipeline-mapped.json", "pipeline")
    plan = build_plan(graph, cell_budget=1, boundary_budget=8, domain_budget=5, arity=4)
    audit = audit_placement(graph, plan, placed)
    assert (audit["native_cells"], audit["original_port_bits"], audit["shared_contacts"],
            audit["clock_leaf_uses"], audit["constant_terminals"]) == (3, 6, 18, 2, 3)
    blocks = list(placed.iter_placements())
    states = _literal_states(iter(blocks))
    union = [[p.x, p.y, p.z, state.name, state.properties] for p, state in sorted(states.items())]
    emissions = sorted((p.pos.x, p.pos.y, p.pos.z, p.block.name, p.block.properties) for p in blocks)
    oracle = json.loads((DIRECTORY / "evidence/pipeline-literal-replay-oracle.json").read_bytes())
    for case in oracle["cases"]:
        assert len(states) == case["unique_blocks"] == 10340
        assert len(blocks) == case["emitted_operations"] == 10532
        assert _digest(union) == case["state_union_sha256"]
        assert _digest(emissions) == case["state_operation_multiset_sha256"]
        _, inputs, outputs, _, _, delta = partition_pipeline_case(
            graph, plan, placed, case["half_period"], verify_outputs=case["half_period"] == 512)
        ports = {group: {name: [point.x - delta.x, point.y - delta.y, point.z - delta.z]
                         for name, point in values.items()}
                 for group, values in (("inputs", inputs), ("outputs", outputs))}
        assert ports == case["ports"]
    assert len({(point.x // 16, point.z // 16) for point in states}) == 74


@pytest.mark.parametrize("fault", ["duplicate-cluster", "duplicate-module", "missing-module",
                                    "duplicate-owner", "missing-escape", "extra-cluster",
                                    "wrong-contact", "wrong-face", "index-alias", "boolean-coordinate"])
def test_replay_rejects_lost_or_ambiguous_inventory(manifest, fault):
    document = deepcopy(manifest)
    root = document["clusters"][0]
    if fault == "duplicate-cluster":
        document["clusters"].append(deepcopy(root))
    elif fault == "duplicate-module":
        document["modules"].append(deepcopy(document["modules"][0]))
    elif fault == "missing-module":
        # The unused/constant-only leaf cannot disappear just because it has no
        # routed signal whose endpoint count would otherwise expose the loss.
        document["modules"] = [m for m in document["modules"] if m["key"] != ["leaf", 8]]
    elif fault == "duplicate-owner":
        root["own_module_keys"].append(["leaf", 0])
    elif fault == "missing-escape":
        next(m for m in document["modules"] if m["escapes"])["escapes"].pop()
    elif fault == "extra-cluster":
        extra = deepcopy(document["clusters"][-1])
        extra["cluster_id"] = 99999
        document["clusters"].append(extra)
    elif fault == "wrong-contact":
        next(iter(document["shared_contacts"].values()))["position"]["x"] += 1
    elif fault == "wrong-face":
        next(iter(document["shared_contacts"].values()))["child_access"] = "south"
    elif fault == "index-alias":
        tile = next(m for m in document["modules"] if m["tracks"])
        bit = next(iter(tile["tracks"]))
        tile["tracks"]["0" + bit] = tile["tracks"][bit]
    else:
        root["bounds"]["min"]["x"] = False
    with pytest.raises(ValueError):
        REPLAY.restore_manifest(document)


def test_replay_preserves_ordered_duplicate_property_pairs(manifest):
    tile = next(m for m in manifest["modules"] if m["overrides"])
    pairs = [["z", "one"], ["a", "two"], ["z", "three"]]
    tile["overrides"][0]["state"]["properties"] = pairs
    placed = REPLAY.restore_manifest(manifest)
    key = tuple(tile["key"])
    point = REPLAY._vector(tile["overrides"][0]["point"])
    assert placed.all_modules()[key].overrides[point].properties == tuple(map(tuple, pairs))


def test_manifest_digest_and_duplicate_json_keys_are_enforced(tmp_path):
    path = tmp_path / "geometry.json.gz"
    path.write_bytes(gzip.compress(b'{"schema":"x","schema":"y"}'))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="duplicate JSON key"):
        REPLAY.read_manifest(path, digest)
    with pytest.raises(ValueError, match="digest"):
        REPLAY.read_manifest(path, "0" * 64)


def test_replayed_union_requires_exact_original_source_bindings(manifest):
    with pytest.raises(ValueError, match="lacks source bindings"):
        REPLAY.validate_sources(manifest)
    name = "src/shamaos/hardware/local_router.py"
    manifest["provenance"]["source_hashes"] = {
        name: hashlib.sha256((DIRECTORY.parents[2] / name).read_bytes()).hexdigest()
        for name in REPLAY.REQUIRED_SOURCE_BINDINGS}
    REPLAY.validate_sources(manifest)
    manifest["provenance"]["source_hashes"][name] = "0" * 64
    with pytest.raises(ValueError, match="source binding changed"):
        REPLAY.validate_sources(manifest)


def test_manifest_restoration_does_not_require_posix_process_measurement(manifest, monkeypatch):
    original = builtins.__import__
    def missing_resource(name, *args, **kwargs):
        if name == "resource":
            raise ImportError("injected non-POSIX platform")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", missing_resource)
    assert len(REPLAY.restore_manifest(manifest).all_modules()) == 16
    assert REPLAY._peak_rss() == {
        "peak_rss_kib": None, "peak_rss_measurement": "unavailable: platform has no resource module"}


def test_mac_process_measurement_converts_bytes_to_kib(monkeypatch):
    original = builtins.__import__
    def mac_resource(name, *args, **kwargs):
        if name == "resource":
            return SimpleNamespace(RUSAGE_SELF=0, getrusage=lambda _: SimpleNamespace(ru_maxrss=4097))
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", mac_resource)
    monkeypatch.setattr(REPLAY.sys, "platform", "darwin")
    assert REPLAY._peak_rss()["peak_rss_kib"] == 5
