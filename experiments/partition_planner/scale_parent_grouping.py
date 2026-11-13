"""Bounded-neighbor parent grouping for complete large mapped graphs.

The fanout/candidate limits affect heuristic search only. The unchanged planner
and independent oracle retain/check every original signal, sink and clock leaf.
"""
from __future__ import annotations

if not __debug__:
    raise RuntimeError("Partition experiment acceptance requires assertions; optimized Python (-O/-OO) is unsupported")

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import time

import shamaos.hardware.partition_planner as planner
from audit_summary_topology import audit_summary


def bounded_neighbor_tree(graph, original_make_tree, *, neighbor_fanout=32,
                          candidate_limit=64, swap_passes=1, group_neighbors=8):
    levels = []

    def make_tree(groups, cell_count, entity_count, *, arity, port_budget):
        baseline, entity_leaf, _ = original_make_tree(groups, cell_count, entity_count,
            arity=arity, port_budget=port_budget)
        clusters = [dict(cluster, parent=None) for cluster in baseline if not cluster["children"]]
        node_leaves = {cluster["id"]: {cluster["id"]} for cluster in clusters}
        terminals = {}
        for bit, signal in graph.signals.items():
            leaves = {entity_leaf[signal.source.entity], *(entity_leaf[sink.entity] for sink in signal.sinks)}
            if len(leaves) > 1:
                terminals[bit] = leaves
        active = list(range(len(clusters)))
        while len(active) > 1:
            started = time.monotonic()
            leaf_owner = {leaf: cid for cid in active for leaf in node_leaves[cid]}
            projected = {bit: {leaf_owner[leaf] for leaf in leaves} for bit, leaves in terminals.items()}
            projected = {bit: owners for bit, owners in projected.items() if len(owners) > 1}
            populations = {bit: len(owners) for bit, owners in projected.items()}
            incidents = {cid: set() for cid in active}
            for bit, owners in projected.items():
                for cid in owners:
                    incidents[cid].add(bit)
            low_fanout = {bit: owners for bit, owners in projected.items() if len(owners) <= neighbor_fanout}
            weights = {bit: 1048576 // (len(owners) - 1) for bit, owners in low_fanout.items()}
            preferred = iter(sorted(active, key=lambda cid: (-len(incidents[cid]), cid)))
            available, chosen = set(active), []
            scored_candidates = 0
            while available:
                first = next(cid for cid in preferred if cid in available)
                available.remove(first)
                group, counts = [first], Counter(incidents[first])
                while available and len(group) < arity:
                    votes = Counter()
                    for bit in counts:
                        if bit in low_fanout:
                            weight = weights[bit] * (1 + int(counts[bit] + 1 == populations[bit]))
                            for candidate in low_fanout[bit] & available:
                                votes[candidate] += weight
                    shortlist = sorted(votes, key=lambda cid: (-votes[cid], cid))[:candidate_limit]
                    if not shortlist:
                        shortlist = [min(available)]
                    def exact_gain(candidate):
                        shared = incidents[candidate] & counts.keys()
                        return (sum(1 + int(counts[bit] + 1 == populations[bit]) for bit in shared),
                                votes[candidate], -candidate)
                    candidate = max(shortlist, key=exact_gain)
                    scored_candidates += len(shortlist)
                    available.remove(candidate); group.append(candidate); counts.update(incidents[candidate])
                chosen.append(sorted(group))

            def cut(counts):
                return sum(0 < value < populations[bit] for bit, value in counts.items())
            counts = [Counter(bit for cid in group for bit in incidents[cid]) for group in chosen]
            emitted = [int(len(group) > 1) for group in chosen]
            costs = [cut(value) * factor for value, factor in zip(counts, emitted)]
            child_costs = [sum(len(incidents[cid]) for cid in group) * factor for group, factor in zip(chosen, emitted)]
            before, changes, tested_swaps = sum(costs), 0, 0
            for iteration in range(swap_passes):
                group_of = {cid: index for index, group in enumerate(chosen) for cid in group}
                group_votes = defaultdict(Counter)
                for bit, owners in low_fanout.items():
                    groups_for_bit = sorted({group_of[cid] for cid in owners})
                    for index, left in enumerate(groups_for_bit):
                        for right in groups_for_bit[index + 1:]:
                            group_votes[left][right] += weights[bit]
                            group_votes[right][left] += weights[bit]
                pairs = set()
                for left, votes in group_votes.items():
                    for right in sorted(votes, key=lambda cid: (-votes[cid], cid))[:group_neighbors]:
                        pairs.add((min(left, right), max(left, right)))
                changed = False
                for left, right in sorted(pairs):
                    for li in range(len(chosen[left])):
                        for ri in range(len(chosen[right])):
                            a, b = chosen[left][li], chosen[right][ri]
                            delta_left = delta_right = 0
                            for bit in incidents[a] | incidents[b]:
                                present_a, present_b = int(bit in incidents[a]), int(bit in incidents[b])
                                old_left, old_right = counts[left][bit], counts[right][bit]
                                new_left, new_right = old_left - present_a + present_b, old_right - present_b + present_a
                                delta_left += int(0 < new_left < populations[bit]) - int(0 < old_left < populations[bit])
                                delta_right += int(0 < new_right < populations[bit]) - int(0 < old_right < populations[bit])
                            delta_left *= emitted[left]; delta_right *= emitted[right]
                            moved = len(incidents[b]) - len(incidents[a])
                            old_loads = (costs[left] + child_costs[left], costs[right] + child_costs[right])
                            new_loads = (old_loads[0] + delta_left + moved * emitted[left],
                                         old_loads[1] + delta_right - moved * emitted[right])
                            delta = (delta_left + delta_right,
                                     sum(value * value for value in new_loads) - sum(value * value for value in old_loads))
                            tested_swaps += 1
                            if delta >= (0, 0):
                                continue
                            chosen[left][li], chosen[right][ri] = b, a
                            counts[left].subtract(incidents[a]); counts[left].update(incidents[b])
                            counts[right].subtract(incidents[b]); counts[right].update(incidents[a])
                            costs[left] += delta_left; costs[right] += delta_right
                            child_costs[left] += moved * emitted[left]; child_costs[right] -= moved * emitted[right]
                            changes += 1; changed = True
                if not changed:
                    break
            assert costs == [cut(value) * factor for value, factor in zip(counts, emitted)]
            assert sum(costs) <= before
            parents = []
            for group in chosen:
                group.sort()
                if len(group) == 1:
                    parents.extend(group)
                    continue
                cid = len(clusters)
                clusters.append({"id": cid, "parent": None, "children": group,
                    "level": 1 + max(clusters[child]["level"] for child in group), "cell_ids": [], "port_ids": []})
                node_leaves[cid] = set().union(*(node_leaves[child] for child in group))
                for child in group:
                    clusters[child]["parent"] = cid
                parents.append(cid)
            row = {"active_clusters": len(active), "parent_groups": len(chosen),
                "projected_crossing_signals": len(projected), "candidate_search_signals": len(low_fanout),
                "scored_candidates": scored_candidates, "tested_local_swaps": tested_swaps,
                "accepted_local_swaps": changes, "parent_cut_before_swaps": before,
                "parent_cut_after_swaps": sum(costs), "seconds": time.monotonic() - started}
            levels.append(row); print(json.dumps(row), flush=True)
            active = parents
        return clusters, entity_leaf, active[0]
    return make_tree, levels


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mapped", type=Path)
    parser.add_argument("--top", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--swap-passes", type=int, default=1)
    parser.add_argument("--neighbor-fanout", type=int, default=32)
    parser.add_argument("--candidate-limit", type=int, default=64)
    parser.add_argument("--group-neighbors", type=int, default=8)
    parser.add_argument("--retain", action="store_true")
    args = parser.parse_args()
    assert args.swap_passes >= 0 and min(args.neighbor_fanout, args.candidate_limit, args.group_neighbors) >= 1
    started = time.monotonic()
    graph = planner.Graph.read(args.mapped, args.top)
    print(json.dumps({"loaded_cells": graph.cell_count, "seconds": time.monotonic() - started}), flush=True)
    original_make_tree = planner.make_tree
    planner.make_tree, levels = bounded_neighbor_tree(graph, original_make_tree,
        neighbor_fanout=args.neighbor_fanout, candidate_limit=args.candidate_limit,
        swap_passes=args.swap_passes, group_neighbors=args.group_neighbors)
    try:
        plan = planner.build_plan(graph, retain_signals=args.retain)
    finally:
        planner.make_tree = original_make_tree
    planning_seconds = time.monotonic() - started
    print(json.dumps({"planned_summary": plan["summary"], "seconds": planning_seconds}), flush=True)
    audit = planner.audit_plan(graph, plan) if args.retain else audit_summary(graph, plan)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(plan, separators=(",", ":")) + "\n")
    report = {"source": graph.source, "summary": plan["summary"], "levels": levels,
        "heuristic_limits": {"neighbor_fanout": args.neighbor_fanout, "candidate_limit": args.candidate_limit,
                             "group_neighbors": args.group_neighbors, "swap_passes": args.swap_passes},
        "independent_audit": audit, "planning_seconds": planning_seconds,
        "total_seconds": time.monotonic() - started,
        "plan_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
        "experiment_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "scope": "complete logical topology comparison; no geometry, chunk or speed claim"}
    args.output.with_suffix(".report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2), flush=True)
