"""Independent exact old-guard/new-collector comparison; small cases only."""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import importlib.util
import inspect
from itertools import product
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from shamaos.hardware import partition_placement as old
from shamaos.hardware.memory import DUST, SUPPORT, redstone_wire, repeater
from shamaos.hardware.partition_planner import Graph, build_plan
from shamaos.hardware.routing import iter_stair
from shamaos.hardware.external_router import _wire_z
from shamaos.model import BlockState, Box, Placement, Vec3
from experiments.partition_placement.packed_parent_union import PackedParentUnion
import experiments.partition_placement.packed_parent_union as candidate


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def literal_digest(states):
    return digest([[p.x, p.y, p.z, state.name, state.properties] for p, state in sorted(states.items())])


def reference(domains, boxes, shared, minimum_y, maximum_y):
    states = {}
    for domain in domains:
        current = old._literal_states(iter(domain))
        old._check_domain_isolation(states, current)
        for point, state in current.items():
            previous = states.setdefault(point, state)
            if previous != state:
                raise old.PlacementError('parent routing domains collide')
    duplicates = 0
    for point, state in states.items():
        if any(box.min.x <= point.x <= box.max.x and box.min.y <= point.y <= box.max.y
               and box.min.z <= point.z <= box.max.z for box in boxes):
            if shared.get(point) != state:
                raise old.PlacementError('parent route intrudes into a child envelope')
            duplicates += 1
    bounds = old._box([*(point for box in boxes for point in (box.min, box.max)), *states])
    if bounds.min.y < minimum_y or bounds.max.y > maximum_y:
        raise old.PlacementError('parent exceeds above-grass height')
    return states, duplicates, bounds


def item(point, state=SUPPORT):
    return Placement(point, state, 'independent-union-audit')


stats = {'cases': 0, 'accepted': 0, 'rejected': 0, 'emitted_operations': 0, 'by_group': {}, 'fixture_parents': []}
outcomes = []


def check(name, domains, *, boxes=(), shared=None, minimum_y=-64, maximum_y=319):
    shared = shared or {}
    before = None
    try:
        before = reference(domains, boxes, shared, minimum_y, maximum_y)
    except old.PlacementError:
        pass
    collector = PackedParentUnion(child_boxes=boxes, expected_shared=shared)
    after = None
    try:
        for domain in domains:
            collector.add_domain(iter(domain))
        result = collector.finish(minimum_y=minimum_y, maximum_y=maximum_y)
        after = dict(collector.iter_states()), result.child_duplicates, result.bounds
        assert result.unique_blocks == len(after[0]), name
        assert result.domains == len(domains), name
        assert result.emitted_operations == sum(map(len, domains)), name
    except old.PlacementError:
        for operation in (lambda: collector.add_domain(()),
                          lambda: collector.finish(minimum_y=minimum_y, maximum_y=maximum_y),
                          lambda: list(collector.iter_states())):
            try:
                operation()
            except old.PlacementError as error:
                assert 'rejected parent union' in str(error), (name, str(error))
            else:
                raise AssertionError(f'{name}: rejected collector remained usable')
    assert (before is None) == (after is None), (name, before is None, after is None)
    if before is not None:
        assert before == after, name
        state_digest = literal_digest(before[0])
    else:
        state_digest = None
    stats['cases'] += 1
    stats['accepted' if before is not None else 'rejected'] += 1
    stats['emitted_operations'] += sum(map(len, domains))
    group = name.split('/')[0]
    stats['by_group'][group] = stats['by_group'].get(group, 0) + 1
    outcomes.append([name, before is not None, state_digest])
    return before


point = Vec3(8, 40, 8)
states = (SUPPORT, DUST, redstone_wire(north='up'), repeater('south', delay=2))
offsets = list(product((-1, 0, 1), repeat=3)) + [(2, 0, 0), (-2, 0, 0), (0, 2, 0), (0, -2, 0), (0, 0, 2), (0, 0, -2)]
for offset in offsets:
    for first, second in product(range(len(states)), repeat=2):
        check(f'adjacency/{offset}/{first}/{second}', [[item(point, states[first])], [item(point.offset(*offset), states[second])]])
check('duplicates/within-equal', [[item(point, DUST)] * 21])
check('duplicates/within-conflicting', [[item(point, DUST), item(point, SUPPORT)]])
check('duplicates/shared-support-three-domains', [[item(point)] * 3, [item(point)] * 5, [item(point)] * 7])
check('duplicates/within-active-neighbors', [[item(point, DUST), item(point.offset(dx=1), DUST)]])
check('duplicates/within-climb-headroom', [[item(point, states[2]), item(point.offset(dy=1))]])
for sequence in ((('north', 'up'), ('north', 'side')), (('north', 'side'), ('north', 'up'))):
    unusual = BlockState('minecraft:redstone_wire', sequence)
    for reverse in (False, True):
        domains = [[item(point, unusual)], [item(point.offset(dy=1))]]
        if reverse:
            domains.reverse()
        check(f'properties/last-value/{sequence}/{reverse}', domains)
ordered = BlockState('minecraft:redstone_wire', (('power', '0'), ('north', 'side')))
reversed_state = BlockState(ordered.name, tuple(reversed(ordered.properties)))
check('properties/order-remains-exact', [[item(point, ordered), item(point, reversed_state)]])
check('empty/no-envelope', [])
box = Box(Vec3(-4, -60, -4), Vec3(4, -50, 4))
check('empty/child-only', [], boxes=[box])
check('empty/empty-domains', [[], []], boxes=[box])
for contact in (box.min, box.max, Vec3(0, -55, -4), Vec3(4, -55, 0), Vec3(0, -55, 0)):
    for state in (SUPPORT, DUST):
        for declared in (False, True):
            check(f'envelope/{contact}/{state.name}/{declared}', [[item(contact, state)]], boxes=[box],
                  shared={contact: state} if declared else {})
check('envelope/overlapping-count-once', [[item(Vec3(0, -55, 0))]], boxes=[box, box], shared={Vec3(0, -55, 0): SUPPORT})
check('envelope/shared-but-outside-not-counted', [[item(point)]], boxes=[box], shared={point: SUPPORT})
check('envelope/wrong-declared-state', [[item(Vec3(0, -55, 0), DUST)]], boxes=[box], shared={Vec3(0, -55, 0): SUPPORT})
check('height/block-below', [[item(Vec3(0, -61, 0))]], minimum_y=-60)
check('height/block-above', [[item(Vec3(0, 286, 0))]], maximum_y=285)
check('height/child-below', [], boxes=[box], minimum_y=-59)
for origin in (Vec3(-29_999_984, -64, 29_999_983), Vec3(29_999_983, 319, -29_999_984),
               Vec3(-(1 << 25), -64, 0), Vec3((1 << 25)-1, 447, 0),
               Vec3(1 << 25, 448, -(1 << 25)-1), Vec3(-(1 << 48), -(1 << 20), 1 << 48)):
    for offset in offsets:
        check(f'codec/{origin}/{offset}', [[item(origin, DUST)], [item(origin.offset(*offset), SUPPORT)]],
              minimum_y=-(1 << 30), maximum_y=1 << 30)
start = Vec3(32, 32, 32)
lower = list(iter_stair(start, start.offset(4, 4, 0), axis='x', signal_forward=True, component='lower'))
for pitch in (2, 3):
    upper = list(_wire_z(start.offset(0, pitch, -8), start.offset(0, pitch, 8), signal_forward=True, component='upper'))
    for reverse in (False, True):
        domains = [lower, upper]
        if reverse:
            domains.reverse()
        check(f'stairs/pitch{pitch}/{reverse}', domains)
check('stairs/blocker', [lower, [item(start.offset(dy=1))]])

snapshot = Path(__file__).resolve().with_name('baseline_egress.py')
spec = importlib.util.spec_from_file_location('independent_frozen_egress', snapshot)
module = importlib.util.module_from_spec(spec);sys.modules[spec.name] = module;spec.loader.exec_module(module)
graph = Graph.read(ROOT/'experiments/partition_placement/raised_exports/evidence/pipeline-mapped.json', 'pipeline')
for name, budget, builder in (('ordinary-single', 16, old.place_partition), ('ordinary-multiple', 5, old.place_partition),
                              ('facade', 5, module.place_with_egress)):
    plan = build_plan(graph, cell_budget=1, boundary_budget=8, domain_budget=budget, arity=4)
    placed = builder(graph, plan)
    pending = [placed]
    while pending:
        cluster = pending.pop();pending.extend(cluster.children)
        if not cluster.children:
            continue
        tiles = [*cluster.modules.values(), *getattr(cluster, 'facade_modules', {}).values()]
        contacts = [record for record in cluster.shared_contacts.values() if record['parent_cluster'] == cluster.cluster_id]
        contacts += [record for record in getattr(cluster, 'facade_contacts', {}).values() if record['parent_cluster'] == cluster.cluster_id]
        shared = {record['position']: DUST for record in contacts}
        shared.update({p.offset(dy=-1): SUPPORT for p in tuple(shared)})
        result = check(f'physical/{name}/cluster{cluster.cluster_id}', [list(tile.iter_placements()) for tile in tiles],
                       boxes=[child.bounds for child in cluster.children], shared=shared, minimum_y=-60)
        assert result is not None
        parent_states, duplicates, bounds = result
        assert sum(child.unique_blocks for child in cluster.children) + len(parent_states) - duplicates == cluster.unique_blocks
        assert bounds == cluster.bounds
        stats['fixture_parents'].append({'fixture': name, 'cluster': cluster.cluster_id, 'domains': len(tiles),
            'unique_parent_states': len(parent_states), 'child_duplicates': duplicates, 'bounds': asdict(bounds),
            'literal_sha256': literal_digest(parent_states)})

report = {'status': 'passed', 'scope': 'Exact old/new literal guard outcomes and small physical parent inventories; no full-cache completion or timing claim',
          'candidate_source_sha256': hashlib.sha256(Path(candidate.__file__).read_bytes()).hexdigest(),
          'baseline_egress_sha256': hashlib.sha256(snapshot.read_bytes()).hexdigest(),
          'reference_guard_source_sha256': hashlib.sha256((inspect.getsource(old._literal_states) + inspect.getsource(old._check_domain_isolation) + inspect.getsource(old._wire_headroom)).encode()).hexdigest(),
          'harness_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), 'outcomes_sha256': digest(outcomes), **stats}
path = ROOT/'build/packed-parent-union-independent.json';path.write_text(json.dumps(report, indent=2, sort_keys=True)+'\n')
print(json.dumps({key: report[key] for key in ('status', 'cases', 'accepted', 'rejected', 'emitted_operations', 'candidate_source_sha256')}, indent=2))
