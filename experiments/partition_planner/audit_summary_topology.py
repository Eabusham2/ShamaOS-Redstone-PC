"""Independent all-net reconstruction for an intentionally unretained summary.

The edge oracle uses subtree endpoint membership, not signal_links(). It checks
every native cell/port/constant, recomputes every original signal's complete
directed topology and deterministic domain assignment, and compares the saved
hashes and inventories. A passing summary still lacks retained physical routing
records and must not be passed to the production retained-plan auditor/emitter.
"""
from __future__ import annotations

if not __debug__:
    raise RuntimeError("Partition experiment acceptance requires assertions; optimized Python (-O/-OO) is unsupported")

from collections import Counter, defaultdict, deque
import argparse
import hashlib
import json
from pathlib import Path
import time

from shamaos.hardware.logic import template_for
from shamaos.hardware.partition_planner import Graph


def audit_summary(graph, plan):
    assert plan["summary"]["all_signal_records_retained"] is False
    assert plan["signals"] == []
    assert plan["entities"] == graph.entities
    assert plan["constants"] == [{"value": value, "endpoint": endpoint.record()} for value, endpoint in graph.constants]
    for field in ("sha256", "top", "lowering_source_sha256", "raw_cells", "lowered_cells"):
        assert plan["source"][field] == graph.source[field]
    budgets = plan["budgets"]
    assert all(type(value) is int for value in budgets.values())
    assert budgets["native_cells_per_leaf"] >= 1 and budgets["boundary_terminals_per_leaf"] >= 1
    assert budgets["children_per_cluster"] >= 2
    assert budgets["routing_terminals_per_domain"] >= budgets["children_per_cluster"] + 1
    clusters = plan["clusters"]
    root = plan["root_cluster"]
    assert 0 <= root < len(clusters)
    parents = [cluster["parent"] for cluster in clusters]
    children_used = Counter(child for cluster in clusters for child in cluster["children"])
    owned, entity_leaf = Counter(), [-1] * len(graph.entities)
    for cid, cluster in enumerate(clusters):
        assert cluster["id"] == cid
        assert len(cluster["children"]) <= budgets["children_per_cluster"]
        assert len(cluster["cell_ids"]) <= budgets["native_cells_per_leaf"]
        assert all(0 <= child < len(clusters) and parents[child] == cid and children_used[child] == 1
                   for child in cluster["children"])
        assert all(0 <= entity < graph.cell_count for entity in cluster["cell_ids"])
        assert all(graph.cell_count <= entity < len(graph.entities) for entity in cluster["port_ids"])
        members = cluster["cell_ids"] + cluster["port_ids"]
        assert bool(members) != bool(cluster["children"])
        for entity in members:
            owned[entity] += 1
            entity_leaf[entity] = cid
    assert owned == Counter(range(len(graph.entities)))
    assert entity_leaf == plan["entity_leaf"]
    assert parents[root] is None and children_used[root] == 0
    pending, visited, order = [root], set(), []
    while pending:
        cid = pending.pop()
        assert cid not in visited
        visited.add(cid); order.append(cid)
        pending.extend(clusters[cid]["children"])
    assert visited == set(range(len(clusters)))
    levels = {}
    for cid in reversed(order):
        levels[cid] = 1 + max(levels[child] for child in clusters[cid]["children"]) if clusters[cid]["children"] else 0
        assert clusters[cid]["level"] == levels[cid]
    ancestors = {}
    for cluster in clusters:
        if cluster["children"]:
            continue
        path, cid = [], cluster["id"]
        while cid is not None:
            path.append(cid); cid = parents[cid]
        ancestors[cluster["id"]] = tuple(path)

    domains, last_domain, domains_per_cluster = [], {}, Counter()
    incoming, outgoing = defaultdict(set), defaultdict(set)
    topology_hash, endpoint_hash = hashlib.sha256(), hashlib.sha256()
    total_sinks = total_clocks = total_links = fanout_branches = maximum_branch = 0
    clock_entities = {entity["id"] for entity in graph.entities if entity["kind"] == "cell"
                      and template_for(entity["cell_type"]).cell_type == "DFF"}
    for bit, signal in graph.signals.items():
        source_leaf = entity_leaf[signal.source.entity]
        sink_leaves = {entity_leaf[sink.entity] for sink in signal.sinks}
        leaves = sink_leaves | {source_leaf}
        membership = Counter(cid for leaf in leaves for cid in ancestors[leaf])
        edges = [(cid, parents[cid]) for cid, count in membership.items() if 0 < count < len(leaves)]
        neighbors = defaultdict(set)
        for first, second in edges:
            assert second is not None
            neighbors[first].add(second); neighbors[second].add(first)
        links, predecessor, visited = [], {}, {source_leaf}
        queue = deque([source_leaf])
        while queue:
            cid = queue.popleft()
            for other in sorted(neighbors[cid]):
                if other in visited:
                    continue
                predecessor[other] = cid
                links.append((cid, other)); visited.add(other); queue.append(other)
        assert len(links) == len(edges) and leaves <= visited
        branches = defaultdict(list)
        for first, second in links:
            branches[first].append(second)
        segments = []
        for cid in sorted(node for node in neighbors if clusters[node]["children"]):
            outputs = sorted(branches[cid])
            assert cid in predecessor and outputs and len(outputs) <= budgets["children_per_cluster"]
            terminals = len(outputs) + 1
            previous = last_domain.get(cid)
            if previous is None or domains[previous]["terminal_count"] + terminals > budgets["routing_terminals_per_domain"]:
                did = len(domains)
                domains.append({"id": did, "cluster_id": cid, "terminal_count": 0, "segment_count": 0, "segments": []})
                last_domain[cid] = did; domains_per_cluster[cid] += 1
            did = last_domain[cid]
            domains[did]["terminal_count"] += terminals
            domains[did]["segment_count"] += 1
            segments.append((cid, did, predecessor[cid], outputs))
            maximum_branch = max(maximum_branch, len(outputs))
            fanout_branches += int(len(outputs) > 1)
        clock_uses = sum(sink.entity in clock_entities and sink.pin == "C" for sink in signal.sinks)
        source, sinks = signal.source.record(), [sink.record() for sink in signal.sinks]
        record = {"bit": bit, "source": source, "sinks": sinks, "clock_leaf_uses": clock_uses,
                  "directed_links": links, "domain_segments": segments}
        topology_hash.update(json.dumps(record, separators=(",", ":"), sort_keys=True).encode())
        endpoint_hash.update(json.dumps([bit, source, sinks], separators=(",", ":"), sort_keys=True).encode())
        total_links += len(links); total_clocks += clock_uses; total_sinks += len(sinks)
        if sink_leaves - {source_leaf}:
            outgoing[source_leaf].add(bit)
        for leaf in sink_leaves - {source_leaf}:
            incoming[leaf].add(bit)
    assert domains == plan["routing_domains"]
    boundaries = [{"cluster_id": cid, "incoming_nets": sorted(incoming[cid]), "outgoing_nets": sorted(outgoing[cid]),
                   "terminal_count": len(incoming[cid]) + len(outgoing[cid])}
                  for cid, cluster in enumerate(clusters) if not cluster["children"]]
    assert boundaries == plan["leaf_boundaries"]
    assert all(row["terminal_count"] <= budgets["boundary_terminals_per_leaf"] for row in boundaries)
    summary = {"native_cells": graph.cell_count, "original_port_bits": len(graph.entities) - graph.cell_count,
        "signals": len(graph.signals), "original_sink_uses": total_sinks, "clock_leaf_uses": total_clocks,
        "constant_terminals": len(graph.constants), "leaf_clusters": len(boundaries),
        "internal_clusters": len(clusters) - len(boundaries), "hierarchy_depth": levels[root],
        "bounded_router_domains": len(domains), "signal_tree_links": total_links,
        "explicit_fanout_branches": fanout_branches, "maximum_router_fanout": maximum_branch,
        "maximum_leaf_boundary": max((row["terminal_count"] for row in boundaries), default=0),
        "maximum_domain_terminals": max((domain["terminal_count"] for domain in domains), default=0),
        "maximum_domains_at_one_cluster": max(domains_per_cluster.values(), default=0),
        "topology_sha256": topology_hash.hexdigest(), "original_signal_endpoints_sha256": endpoint_hash.hexdigest(),
        "all_signal_records_retained": False}
    assert summary == plan["summary"], {key: (summary[key], plan["summary"].get(key)) for key in summary if summary[key] != plan["summary"].get(key)}
    return {"status": "all-original-net-hashes-reconstructed", "signals": len(graph.signals),
            "native_cells": graph.cell_count, "clock_leaf_uses": total_clocks,
            "complete_summary_topology_hash_matched": True, "complete_original_endpoint_hash_matched": True,
            "physical_routing_records_retained": False, "geometry_or_runtime_verified": False,
            "scope": "independent complete logical reconstruction of summary hash; not a retained-plan or physical-world acceptance"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mapped", type=Path)
    parser.add_argument("--top", required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    started = time.monotonic()
    graph = Graph.read(args.mapped, args.top)
    print(json.dumps({"loaded_cells": graph.cell_count, "seconds": time.monotonic() - started}), flush=True)
    plan = json.loads(args.plan.read_text())
    report = audit_summary(graph, plan)
    report.update(source=graph.source, summary=plan["summary"], seconds=time.monotonic() - started,
        summary_plan_sha256=hashlib.sha256(args.plan.read_bytes()).hexdigest(),
        auditor_source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2), flush=True)
