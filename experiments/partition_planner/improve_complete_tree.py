"""Deterministic whole-tree subtree swaps, isolated from production planning.

For a hypernet, an edge participates exactly when its child subtree contains
some, but not all, of that net's endpoint leaves. This gives an independent
integer cost for all inter-cluster links, including changes above a local swap.
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


def improve(graph, source_plan, *, passes=4, objective="links"):
    planner.audit_plan(graph, source_plan)
    clusters = deepcopy(source_plan["clusters"])
    root = source_plan["root_cluster"]
    counts = [Counter() for _ in clusters]
    populations = {}
    for bit, signal in graph.signals.items():
        leaves = {source_plan["entity_leaf"][signal.source.entity],
                  *(source_plan["entity_leaf"][sink.entity] for sink in signal.sinks)}
        if len(leaves) <= 1:
            continue
        populations[bit] = len(leaves)
        for leaf in leaves:
            counts[leaf][bit] = 1
    by_level = defaultdict(list)
    for cluster in clusters:
        by_level[cluster["level"]].append(cluster["id"])
    for level in sorted(by_level):
        for cid in by_level[level]:
            for child in clusters[cid]["children"]:
                counts[cid].update(counts[child])
    cut = [sum(0 < number < populations[bit] for bit, number in value.items()) for value in counts]
    cut_sets = [{bit for bit, number in value.items() if 0 < number < populations[bit]} for value in counts]
    workload = [cut[cid] + sum(cut[child] for child in cluster["children"])
                if cluster["children"] else 0 for cid, cluster in enumerate(clusters)]
    assert cut[root] == 0
    assert sum(cut) == source_plan["summary"]["signal_tree_links"]
    domain_counts = Counter(domain["cluster_id"] for domain in source_plan["routing_domains"])
    domain_budget = source_plan["budgets"]["routing_terminals_per_domain"]

    def packed_domains(cid, sets=None, children=None):
        if not clusters[cid]["children"]:
            return 0
        sets = sets or {}
        degrees = Counter(sets.get(cid, cut_sets[cid]))
        for child in clusters[cid]["children"] if children is None else children:
            degrees.update(sets.get(child, cut_sets[child]))
        domains = used = 0
        for bit in sorted(degrees):
            terminals = degrees[bit]
            assert 2 <= terminals <= source_plan["budgets"]["children_per_cluster"] + 1
            if not domains or used + terminals > domain_budget:
                domains += 1
                used = 0
            used += terminals
        return domains
    assert all(domain_counts[cid] == packed_domains(cid) for cid in range(len(clusters)))

    def ancestors(cid):
        result = []
        node = clusters[cid]["parent"]
        while node is not None:
            result.append(node)
            node = clusters[node]["parent"]
        return result

    log = []
    for iteration in range(passes):
        started = time.monotonic()
        initial_links, initial_squares = sum(cut), sum(value * value for value in workload)
        changes = 0
        for level in sorted(by_level, reverse=bool(iteration % 2)):
            nodes = by_level[level]
            for index, first in enumerate(nodes):
                for second in nodes[index + 1:]:
                    a_parent, b_parent = clusters[first]["parent"], clusters[second]["parent"]
                    if a_parent is None or a_parent == b_parent:
                        continue
                    a_path, b_path = ancestors(first), ancestors(second)
                    common = next(node for node in a_path if node in b_path)
                    a_path, b_path = a_path[:a_path.index(common)], b_path[:b_path.index(common)]
                    delta_counts = {bit: counts[second][bit] - counts[first][bit]
                                    for bit in counts[first].keys() | counts[second].keys()
                                    if counts[second][bit] != counts[first][bit]}
                    if not delta_counts:
                        continue
                    next_cut = {}
                    for path, sign in ((a_path, 1), (b_path, -1)):
                        for cid in path:
                            adjustment = 0
                            for bit, delta in delta_counts.items():
                                before = counts[cid][bit]
                                after = before + sign * delta
                                assert 0 <= after <= populations[bit]
                                adjustment += int(0 < after < populations[bit]) - int(0 < before < populations[bit])
                            next_cut[cid] = cut[cid] + adjustment
                    delta_links = sum(value - cut[cid] for cid, value in next_cut.items())
                    if delta_links > (0 if objective == "links" else 16):
                        continue
                    affected = set(next_cut) | {clusters[cid]["parent"] for cid in next_cut}
                    affected.discard(None)
                    next_workload = {}
                    for cid in affected:
                        value = next_cut.get(cid, cut[cid]) + sum(next_cut.get(child, cut[child]) for child in clusters[cid]["children"])
                        if cid == a_parent:
                            value += cut[second] - cut[first]
                        elif cid == b_parent:
                            value += cut[first] - cut[second]
                        next_workload[cid] = value
                    delta_squares = sum(value * value - workload[cid] * workload[cid] for cid, value in next_workload.items())
                    next_sets, next_domains = {}, {}
                    if objective == "links":
                        if (delta_links, delta_squares) >= (0, 0):
                            continue
                    else:
                        old_objective = (max(domain_counts.values(), default=0), sum(domain_counts.values()),
                                         sum(cut), sum(value * value for value in workload))
                        unaffected_max = max((value for cid, value in domain_counts.items() if cid not in affected), default=0)
                        lower_domains = {cid: (value + domain_budget - 1) // domain_budget
                                         for cid, value in next_workload.items()}
                        lower_objective = (max(unaffected_max, max(lower_domains.values(), default=0)),
                            old_objective[1] - sum(domain_counts[cid] for cid in affected) + sum(lower_domains.values()),
                            old_objective[2] + delta_links, old_objective[3] + delta_squares)
                        if lower_objective >= old_objective:
                            continue
                    for path, sign in ((a_path, 1), (b_path, -1)):
                        for cid in path:
                            values = cut_sets[cid].copy()
                            for bit, delta in delta_counts.items():
                                value = counts[cid][bit] + sign * delta
                                if 0 < value < populations[bit]:
                                    values.add(bit)
                                else:
                                    values.discard(bit)
                            next_sets[cid] = values
                    for cid in affected:
                        children = clusters[cid]["children"]
                        if cid == a_parent:
                            children = [second if child == first else child for child in children]
                        elif cid == b_parent:
                            children = [first if child == second else child for child in children]
                        next_domains[cid] = packed_domains(cid, next_sets, children)
                    if objective != "links":
                        actual_objective = (max(unaffected_max, max(next_domains.values(), default=0)),
                            old_objective[1] - sum(domain_counts[cid] for cid in affected) + sum(next_domains.values()),
                            old_objective[2] + delta_links, old_objective[3] + delta_squares)
                        if actual_objective >= old_objective:
                            continue
                    for path, sign in ((a_path, 1), (b_path, -1)):
                        for cid in path:
                            for bit, delta in delta_counts.items():
                                counts[cid][bit] += sign * delta
                                if counts[cid][bit] == 0:
                                    del counts[cid][bit]
                    for cid, value in next_cut.items():
                        cut[cid] = value
                    for cid, value in next_workload.items():
                        workload[cid] = value
                    for cid, values in next_sets.items():
                        cut_sets[cid] = values
                    domain_counts.update({cid: value - domain_counts[cid] for cid, value in next_domains.items()})
                    a_children, b_children = clusters[a_parent]["children"], clusters[b_parent]["children"]
                    a_children[a_children.index(first)] = second
                    b_children[b_children.index(second)] = first
                    a_children.sort(); b_children.sort()
                    clusters[first]["parent"], clusters[second]["parent"] = b_parent, a_parent
                    changes += 1
        if objective == "links":
            assert (sum(cut), sum(value * value for value in workload)) <= (initial_links, initial_squares)
        row = {"pass": iteration + 1, "accepted_swaps": changes, "before_links": initial_links,
               "after_links": sum(cut), "maximum_domain_terminal_workload": max(workload),
               "bounded_router_domains": sum(domain_counts.values()),
               "maximum_domains_at_one_cluster": max(domain_counts.values(), default=0),
               "seconds": time.monotonic() - started}
        log.append(row)
        print(json.dumps(row), flush=True)
        if not changes:
            break

    original_make_tree = planner.make_tree
    def retained_tree(*args, **kwargs):
        baseline, entity_leaf, baseline_root = original_make_tree(*args, **kwargs)
        assert entity_leaf == source_plan["entity_leaf"]
        assert baseline_root == root
        for cid, cluster in enumerate(baseline):
            assert cluster["cell_ids"] == clusters[cid]["cell_ids"]
            assert cluster["port_ids"] == clusters[cid]["port_ids"]
        return clusters, entity_leaf, root
    planner.make_tree = retained_tree
    try:
        result = planner.build_plan(graph, cell_budget=source_plan["budgets"]["native_cells_per_leaf"],
            boundary_budget=source_plan["budgets"]["boundary_terminals_per_leaf"],
            domain_budget=source_plan["budgets"]["routing_terminals_per_domain"],
            arity=source_plan["budgets"]["children_per_cluster"])
    finally:
        planner.make_tree = original_make_tree
    audit = planner.audit_plan(graph, result)
    assert result["summary"]["signal_tree_links"] == sum(cut)
    actual_workload = Counter()
    for domain in result["routing_domains"]:
        actual_workload[domain["cluster_id"]] += domain["terminal_count"]
    assert all(actual_workload[cid] == value for cid, value in enumerate(workload))
    actual_domain_counts = Counter(domain["cluster_id"] for domain in result["routing_domains"])
    assert actual_domain_counts == +domain_counts
    return result, {"passes": log, "objective": objective, "independent_audit": audit,
        "independent_cut_cost_matches_all_actual_links": True,
        "independent_terminal_cost_matches_all_domains": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mapped", type=Path)
    parser.add_argument("--top", required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--passes", type=int, default=4)
    parser.add_argument("--objective", choices=["links", "domains"], default="links")
    args = parser.parse_args()
    started = time.monotonic()
    graph = planner.Graph.read(args.mapped, args.top)
    source = json.loads(args.plan.read_text())
    result, report = improve(graph, source, passes=args.passes, objective=args.objective)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, separators=(",", ":")) + "\n")
    report.update(source=graph.source, before=source["summary"], after=result["summary"],
        seconds=time.monotonic() - started,
        source_plan_sha256=hashlib.sha256(args.plan.read_bytes()).hexdigest(),
        result_plan_sha256=hashlib.sha256(args.output.read_bytes()).hexdigest(),
        experiment_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        scope="complete topology and domain workload; no emitted geometry or timing claim")
    args.output.with_suffix(".report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"summary": result["summary"], "seconds": report["seconds"]}), flush=True)
