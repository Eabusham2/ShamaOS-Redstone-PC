"""The compact host inventory must preserve the existing literal route guards."""
from itertools import product
from pathlib import Path

import pytest

from experiments.partition_placement.packed_parent_union import (
    PackedParentUnion, coordinate_from_key, coordinate_key,
)
from shamaos.hardware import partition_placement as reference
from shamaos.hardware.memory import DUST, SUPPORT
from shamaos.hardware.partition_planner import Graph, build_plan
from shamaos.model import BlockState, Box, Placement, Vec3
from test_port_egress_placement import facade_pipeline


def _blocks(*items):
    return [Placement(point, state, "test-domain") for point, state in items]


def _old_union(domains, boxes=(), shared=None):
    states = {}
    for domain in domains:
        current = reference._literal_states(iter(domain))
        reference._check_domain_isolation(states, current)
        states.update(current)
    duplicate_count = 0
    for point, state in states.items():
        if any(box.min.x <= point.x <= box.max.x and box.min.y <= point.y <= box.max.y
               and box.min.z <= point.z <= box.max.z for box in boxes):
            if (shared or {}).get(point) != state:
                raise reference.PlacementError("reference child-envelope entry")
            duplicate_count += 1
    bounds = reference._box([*(point for box in boxes for point in (box.min, box.max)), *states])
    return states, duplicate_count, bounds


def test_codec_is_injective_at_every_packing_boundary_and_large_integer():
    axes = (-2**90, -2**25 - 1, -2**25, -1, 0, 2**25 - 1, 2**25, 2**90)
    heights = (-2**90, -65, -64, -63, 319, 447, 448, 2**90)
    points = [Vec3(*point) for point in product(axes, heights, axes)]
    keys = [coordinate_key(point.x, point.y, point.z) for point in points]
    assert len(set(keys)) == len(points)
    assert [coordinate_from_key(key) for key in keys] == points


@pytest.mark.parametrize("single_domain", [False, True])
@pytest.mark.parametrize("kind", ["equal-support", "equal-active", "adjacent-active", "headroom", "conflicting-state"])
def test_existing_domain_acceptance_is_retained(single_domain, kind):
    point = Vec3(-3, 8, 7)
    rising = BlockState.of("minecraft:redstone_wire", south="up")
    pairs = {
        "equal-support": ((point, SUPPORT), (point, SUPPORT)),
        "equal-active": ((point, DUST), (point, DUST)),
        "adjacent-active": ((point, DUST), (point.offset(dx=1, dy=1), DUST)),
        "headroom": ((point, rising), (point.offset(dy=1), SUPPORT)),
        "conflicting-state": ((point, SUPPORT), (point, DUST)),
    }
    first, second = pairs[kind]
    domains = [_blocks(first, second)] if single_domain else [_blocks(first), _blocks(second)]
    try:
        expected, duplicates, bounds = _old_union(domains)
    except reference.PlacementError:
        union = PackedParentUnion()
        with pytest.raises(reference.PlacementError):
            for domain in domains:
                union.add_domain(domain)
        with pytest.raises(reference.PlacementError, match="cannot be reused"):
            union.add_domain([])
        return
    union = PackedParentUnion()
    for domain in domains:
        union.add_domain(domain)
    summary = union.finish(minimum_y=-64, maximum_y=319)
    assert dict(union.iter_states()) == expected
    assert (summary.child_duplicates, summary.bounds) == (duplicates, bounds)


def test_current_domain_retains_prior_owner_for_repeated_support_headroom():
    point = Vec3(7, 14, -3)
    rising = BlockState.of("minecraft:redstone_wire", east="up")
    first = _blocks((point.offset(dy=1), SUPPORT))
    second = _blocks((point.offset(dy=1), SUPPORT), (point, rising))
    with pytest.raises(reference.PlacementError, match="headroom"):
        _old_union([first, second])
    union = PackedParentUnion()
    union.add_domain(first)
    with pytest.raises(reference.PlacementError, match="headroom"):
        union.add_domain(second)


def test_child_overlap_and_repeated_emissions_count_each_shared_position_once():
    point = Vec3(10, -59, 9)
    support = point.offset(dy=-1)
    boxes = (Box(Vec3(0, -60, 0), point), Box(support, Vec3(20, -58, 20)))
    shared = {point: DUST, support: SUPPORT}
    domains = [_blocks((point, DUST), (support, SUPPORT)) * 5000,
               _blocks((support, SUPPORT)) * 5000]
    expected, duplicates, bounds = _old_union(domains, boxes, shared)
    union = PackedParentUnion(boxes, shared)
    for domain in domains:
        union.add_domain(iter(domain))
    summary = union.finish(minimum_y=-60, maximum_y=319)
    assert summary.unique_blocks == 2
    assert summary.child_duplicates == duplicates == 2
    assert summary.bounds == bounds
    assert summary.emitted_operations == 15000
    assert dict(union.iter_states()) == expected


def test_rejected_iterator_cannot_leave_a_reusable_partial_union():
    def failed_domain():
        yield Placement(Vec3(1, 1, 1), SUPPORT, "partial")
        raise RuntimeError("interrupted emission")
    union = PackedParentUnion()
    with pytest.raises(RuntimeError, match="interrupted emission"):
        union.add_domain(failed_domain())
    with pytest.raises(reference.PlacementError, match="cannot be reused"):
        list(union.iter_states())
    with pytest.raises(reference.PlacementError, match="cannot be reused"):
        union.finish(minimum_y=-64, maximum_y=319)


def test_every_completed_facade_parent_has_identical_states_bounds_and_counts(facade_pipeline):
    _, _, placed = facade_pipeline
    pending, checked = [placed], 0
    while pending:
        cluster = pending.pop()
        pending.extend(cluster.children)
        if not cluster.children:
            continue
        boxes = tuple(child.bounds for child in cluster.children)
        records = [record for record in (*cluster.shared_contacts.values(), *cluster.facade_contacts.values())
                   if record["parent_cluster"] == cluster.cluster_id]
        shared = {record["position"]: DUST for record in records}
        shared.update({point.offset(dy=-1): SUPPORT for point in tuple(shared)})
        tiles = (*cluster.modules.values(), *cluster.facade_modules.values())
        expected, duplicates, bounds = _old_union([list(tile.iter_placements()) for tile in tiles], boxes, shared)
        union = PackedParentUnion(boxes, shared)
        for tile in tiles:
            union.add_domain(tile.iter_placements())
        summary = union.finish(minimum_y=-60, maximum_y=319)
        assert dict(union.iter_states()) == expected
        assert (summary.unique_blocks, summary.child_duplicates, summary.bounds) == (len(expected), duplicates, bounds)
        assert bounds == cluster.bounds
        assert sum(child.unique_blocks for child in cluster.children) + summary.unique_blocks - duplicates == cluster.unique_blocks
        checked += 1
    assert checked >= 2


@pytest.mark.parametrize("domain_budget", [5, 16])
def test_complete_generation_matches_retained_original_guard_builder(domain_budget):
    from experiments.partition_placement.parent_union.baseline_egress import place_with_egress as original_builder
    from experiments.partition_placement.port_egress import place_with_egress
    path = Path(__file__).resolve().parents[1] / "experiments/partition_placement/raised_exports/evidence/pipeline-mapped.json"
    graph = Graph.read(path, "pipeline")
    plan = build_plan(graph, cell_budget=1, boundary_budget=8, domain_budget=domain_budget, arity=4)
    before, after = original_builder(graph, plan), place_with_egress(graph, plan)
    assert reference._literal_states(before.iter_placements()) == reference._literal_states(after.iter_placements())
    assert before.all_modules() == after.all_modules()
    assert before.all_facade_modules() == after.all_facade_modules()
    pending = [(before, after)]
    while pending:
        original, candidate = pending.pop()
        for name in ("cluster_id", "bounds", "terminals", "ports", "unique_blocks", "shared_contacts",
                     "packing", "exposed_ports", "facade_contacts"):
            assert getattr(original, name) == getattr(candidate, name)
        assert len(original.children) == len(candidate.children)
        pending.extend(zip(original.children, candidate.children, strict=True))
