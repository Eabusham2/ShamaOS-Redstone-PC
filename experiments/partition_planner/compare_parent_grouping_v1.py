"""Isolated deterministic parent grouping experiment; production defaults stay fixed.

Leaves, native ownership and all original net endpoints remain unchanged. Only
which active clusters share a bounded parent changes. Every retained candidate
passes the production independent structural auditor before its metrics are used.
"""
from __future__ import annotations

if not __debug__:
    raise RuntimeError("Partition experiment acceptance requires assertions; optimized Python (-O/-OO) is unsupported")

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import time

import shamaos.hardware.partition_planner as planner


def grouping_tree(graph, original_make_tree, *, seed, weighted=False, swaps=False):
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
            weights = {bit: (1048576 // (population - 1) if weighted else 1)
                       for bit, population in populations.items()}

            def cut(counts):
                return sum(0 < value < populations[bit] for bit, value in counts.items())

            if seed == "consecutive":
                chosen = [active[start:start + arity] for start in range(0, len(active), arity)]
            else:
                available = set(active)
                chosen = []
                while available:
                    first = (min(available) if seed == "first" else
                             min(available, key=lambda cid: (-len(incidents[cid]), cid)))
                    group = [first]
                    available.remove(first)
                    counts = Counter(incidents[first])
                    while available and len(group) < arity:
                        def gain(candidate):
                            shared = incidents[candidate] & counts.keys()
                            raw = sum(1 + int(counts[bit] + 1 == populations[bit]) for bit in shared)
                            score = sum(weights[bit] * (1 + int(counts[bit] + 1 == populations[bit])) for bit in shared)
                            return score, raw, -candidate
                        candidate = max(available, key=gain)
                        group.append(candidate)
                        available.remove(candidate)
                        counts.update(incidents[candidate])
                    chosen.append(sorted(group))

            counts = [Counter(bit for cid in group for bit in incidents[cid]) for group in chosen]
            costs = [cut(value) for value in counts]
            child_costs = [sum(len(incidents[cid]) for cid in group) for group in chosen]
            before = sum(costs)
            changes = 0
            if swaps:
                # Strictly decrease (total parent cut, squared parent terminal
                # workload). The latter balances equal-cut choices. Both are
                # integer potentials, so accepted swaps cannot cycle.
                for iteration in range(4):
                    changed = False
                    for left in range(len(chosen)):
                        for right in range(left + 1, len(chosen)):
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
                                    old_loads = (costs[left] + child_costs[left], costs[right] + child_costs[right])
                                    moved = len(incidents[b]) - len(incidents[a])
                                    new_loads = (old_loads[0] + delta_left + moved, old_loads[1] + delta_right - moved)
                                    delta = (delta_left + delta_right,
                                             sum(value * value for value in new_loads) - sum(value * value for value in old_loads))
                                    if delta >= (0, 0):
                                        continue
                                    chosen[left][li], chosen[right][ri] = b, a
                                    counts[left].subtract(incidents[a]); counts[left].update(incidents[b])
                                    counts[right].subtract(incidents[b]); counts[right].update(incidents[a])
                                    costs[left] += delta_left; costs[right] += delta_right
                                    child_costs[left] += moved; child_costs[right] -= moved
                                    changes += 1
                                    changed = True
                    if not changed:
                        break
            assert costs == [cut(value) for value in counts]
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
            levels.append({"active_clusters": len(active), "parent_groups": len(chosen),
                "parent_cut_before_swaps": before, "parent_cut_after_swaps": sum(costs),
                "accepted_swaps": changes, "seconds": time.monotonic() - started})
            active = parents
        return clusters, entity_leaf, active[0]
    return make_tree, levels


def run(mapped, top, output, strategies):
    output.mkdir(parents=True, exist_ok=True)
    graph = planner.Graph.read(mapped, top)
    original_make_tree = planner.make_tree
    reports = []
    for name, seed, weighted, swaps in strategies:
        started = time.monotonic()
        if name == "baseline":
            levels = []
        else:
            planner.make_tree, levels = grouping_tree(graph, original_make_tree, seed=seed, weighted=weighted, swaps=swaps)
        try:
            plan = planner.build_plan(graph)
        finally:
            planner.make_tree = original_make_tree
        audit = planner.audit_plan(graph, plan)
        elapsed = time.monotonic() - started
        path = output / f"{name}-plan.json"
        path.write_text(json.dumps(plan, separators=(",", ":")) + "\n")
        report = {"strategy": name, "seconds": elapsed, "summary": plan["summary"],
            "independent_audit": audit, "levels": levels, "plan_file": str(path),
            "plan_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        reports.append(report)
        (output / "comparison.json").write_text(json.dumps({
            "source": graph.source, "planner_source_sha256": hashlib.sha256(Path(planner.__file__).read_bytes()).hexdigest(),
            "experiment_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "scope": "complete graph topology only; geometry, clock timing and footprint unmeasured",
            "candidates": reports}, indent=2) + "\n")
        print(json.dumps({"strategy": name, "seconds": elapsed,
            **{key: plan["summary"][key] for key in ("signal_tree_links", "bounded_router_domains", "maximum_domains_at_one_cluster", "hierarchy_depth")},
            "audit": audit["status"]}), flush=True)
    return reports


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mapped", type=Path)
    parser.add_argument("--top", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--only")
    args = parser.parse_args()
    strategies = [
        ("baseline", "consecutive", False, False),
        ("consecutive-swaps", "consecutive", False, True),
        ("greedy-degree", "degree", False, False),
        ("greedy-first", "first", False, False),
        ("greedy-weighted-degree", "degree", True, False),
        ("greedy-degree-swaps", "degree", False, True),
        ("greedy-first-swaps", "first", False, True),
        ("greedy-weighted-degree-swaps", "degree", True, True),
    ]
    run(args.mapped, args.top, args.output,
        [strategy for strategy in strategies if args.only is None or strategy[0] in args.only.split(",")])
