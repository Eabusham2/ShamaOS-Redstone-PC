"""Bounded complete-graph partition tree; emits no Minecraft blocks.

Leaf boundaries, parent routing domains and every distribution branch are
bounded independently. The input graph is never truncated to make it fit.
Physical placement, route geometry and clock/data timing remain separate gates.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
import hashlib
import gzip
import json
from pathlib import Path
import time

from .logic import template_for
from .. import synthesis
from ..synthesis import SynthesisError, _expand_sequential_cells


class PlanningError(ValueError):
    pass


def load_mapped_module(path: str | Path, top: str) -> tuple[dict, dict]:
    """Read plain/gzipped Yosys JSON and apply the production sequential lowering.

    The metadata hashes the exact decompressed mapped artifact and its
    container separately. Placement uses this same loader so expanded cell
    identities agree with the planner. No graph is truncated on a read error.
    """
    path = Path(path)
    container = path.read_bytes()
    container_digest = hashlib.sha256(container).hexdigest()
    compressed = container.startswith(b"\x1f\x8b")
    try:
        contents = gzip.decompress(container) if compressed else container
        module = json.loads(contents)["modules"][top]
    except (OSError, EOFError, ValueError, KeyError, TypeError) as exc:
        raise PlanningError(f"invalid mapped artifact or missing top module {top!r}") from exc
    digest = hashlib.sha256(contents).hexdigest()
    raw_cells = len(module.get("cells", {}))
    try:
        _expand_sequential_cells(module)
    except SynthesisError as exc:
        raise PlanningError(f"invalid mapped sequential cell: {exc}") from exc
    return module, {
        "path": str(path.resolve()), "sha256": digest, "container_sha256": container_digest,
        "encoding": "gzip" if compressed else "plain", "top": top, "raw_cells": raw_cells,
        "lowered_cells": len(module.get("cells", {})),
        "lowering_source_sha256": hashlib.sha256(Path(synthesis.__file__).read_bytes()).hexdigest(),
    }


@dataclass(frozen=True, slots=True, order=True)
class Endpoint:
    entity: int
    pin: str
    index: int = 0

    def record(self):
        return {"entity": self.entity, "pin": self.pin, "index": self.index}


@dataclass(slots=True)
class Signal:
    bit: int
    source: Endpoint
    sinks: tuple[Endpoint, ...]


@dataclass
class Graph:
    entities: list[dict]
    cell_count: int
    signals: dict[int, Signal]
    constants: list[tuple[str, Endpoint]]
    source: dict

    @classmethod
    def read(cls, path, top):
        module, source = load_mapped_module(path, top)
        entities, drivers, consumers, constants = [], defaultdict(list), defaultdict(list), []

        def terminal(bit, endpoint, direction):
            if isinstance(bit, int):
                (drivers if direction == "output" else consumers)[bit].append(endpoint)
            elif bit in ("0", "1"):
                if direction == "input":
                    constants.append((bit, endpoint))
                else:
                    raise PlanningError(f"constant-valued driving terminal: {endpoint}")
            else:
                raise PlanningError(f"unresolved constant {bit!r} at {endpoint}; cannot silently ground it")

        for name, cell in sorted(module.get("cells", {}).items()):
            kind = str(cell["type"]).upper()
            template = template_for(kind)
            if set(cell["connections"]) != set(template.pins):
                raise PlanningError(f"native cell {name} is missing a physical pin or has an unknown pin")
            entity = len(entities)
            entities.append({"id": entity, "name": name, "kind": "cell", "cell_type": kind})
            for pin, bits in cell["connections"].items():
                expected_direction = "output" if pin in ("Q", "Y") else "input"
                direction = cell.get("port_directions", {}).get(pin, expected_direction)
                if len(bits) != 1 or pin not in template.pins or direction != expected_direction:
                    raise PlanningError(f"unmapped or ambiguous native pin {name}:{pin}")
                terminal(bits[0], Endpoint(entity, pin), direction)
        cell_count = len(entities)
        for name, port in sorted(module.get("ports", {}).items()):
            if port["direction"] not in ("input", "output"):
                raise PlanningError(f"unsupported bidirectional module port {name}")
            for index, bit in enumerate(port["bits"]):
                entity = len(entities)
                entities.append({"id": entity, "name": f"port:{name}[{index}]", "kind": "port",
                                 "port_name": name, "port_index": index, "direction": port["direction"], "bit": bit})
                terminal(bit, Endpoint(entity, name, index), "output" if port["direction"] == "input" else "input")
        signals = {}
        for bit, sinks in sorted(consumers.items()):
            if len(drivers[bit]) != 1:
                raise PlanningError(f"signal {bit} has {len(drivers[bit])} drivers for {len(sinks)} sinks")
            signals[bit] = Signal(bit, drivers[bit][0], tuple(sorted(sinks)))
        return cls(entities, cell_count, signals, constants, source)


def affinity_order(graph: Graph, *, fanout_limit=16):
    """Linear graph walk; fanout filtering affects order only, never topology."""
    adjacent = [set() for _ in range(graph.cell_count)]
    for signal in graph.signals.values():
        source = signal.source.entity
        if source >= graph.cell_count or len(signal.sinks) > fanout_limit:
            continue
        for sink in signal.sinks:
            if sink.entity < graph.cell_count and sink.entity != source:
                adjacent[source].add(sink.entity)
                adjacent[sink.entity].add(source)
    seen, order = bytearray(graph.cell_count), []
    for start in range(graph.cell_count):
        pending = [start]
        while pending:
            cell = pending.pop()
            if seen[cell]:
                continue
            seen[cell] = 1
            order.append(cell)
            pending.extend(sorted((other for other in adjacent[cell] if not seen[other]), reverse=True))
    return order


def bounded_cells(graph: Graph, cell_budget: int, boundary_budget: int):
    incoming, outgoing = [Counter() for _ in range(graph.cell_count)], [[] for _ in range(graph.cell_count)]
    for bit, signal in graph.signals.items():
        if signal.source.entity < graph.cell_count:
            outgoing[signal.source.entity].append(bit)
        for sink in signal.sinks:
            if sink.entity < graph.cell_count:
                incoming[sink.entity][bit] += 1

    def boundary(bit, local_sinks, source_inside):
        if source_inside:
            return int(local_sinks < len(graph.signals[bit].sinks))
        return int(local_sinks > 0)

    groups, members, counts, total = [], set(), Counter(), 0
    for cell in affinity_order(graph):
        touched = set(incoming[cell]) | set(outgoing[cell])

        def projected():
            delta = 0
            for bit in touched:
                source = graph.signals[bit].source.entity
                delta += (boundary(bit, counts[bit] + incoming[cell][bit], source in members or source == cell)
                          - boundary(bit, counts[bit], source in members))
            return total + delta

        proposed = projected()
        if members and (len(members) >= cell_budget or proposed > boundary_budget):
            groups.append(sorted(members))
            members, counts, total = set(), Counter(), 0
            proposed = projected()
        if proposed > boundary_budget:
            raise PlanningError(f"native cell {graph.entities[cell]['name']} alone needs {proposed} boundary terminals; budget={boundary_budget}")
        members.add(cell)
        counts.update(incoming[cell])
        total = proposed
    if members:
        groups.append(sorted(members))
    return groups


def make_tree(groups, cell_count, entity_count, *, arity, port_budget, ports_at_root=False):
    clusters, entity_leaf = [], [-1] * entity_count
    groups = [*groups, *(list(range(start, min(entity_count, start + port_budget)))
                        for start in range(cell_count, entity_count, port_budget))]
    for members in groups:
        cid = len(clusters)
        for entity in members:
            if entity_leaf[entity] != -1:
                raise PlanningError("entity belongs to multiple leaves")
            entity_leaf[entity] = cid
        clusters.append({"id": cid, "parent": None, "children": [], "level": 0,
                         "cell_ids": [entity for entity in members if entity < cell_count],
                         "port_ids": [entity for entity in members if entity >= cell_count]})
    if any(leaf < 0 for leaf in entity_leaf):
        raise PlanningError("partition omitted a native cell or original port bit")
    if not clusters:
        raise PlanningError("empty graph has no placeable entity")

    def reduce_level(active):
        parents = []
        for start in range(0, len(active), arity):
            children = active[start:start + arity]
            if len(children) == 1:
                parents.append(children[0])
                continue
            cid = len(clusters)
            clusters.append({"id": cid, "parent": None, "children": children,
                             "level": 1 + max(clusters[child]["level"] for child in children),
                             "cell_ids": [], "port_ids": []})
            for child in children:
                clusters[child]["parent"] = cid
            parents.append(cid)
        return parents

    if ports_at_root:
        # Original pins are genuine interface leaves, but need not descend
        # through the logic hierarchy before their wires can leave the chip.
        # Keep their complete groups at the shallowest bounded level. This
        # changes only topology: all directed paths are reconstructed below.
        cells = [c["id"] for c in clusters if c["cell_ids"]]
        ports = [c["id"] for c in clusters if c["port_ids"]]
        while len(cells) > 1:
            cells = reduce_level(cells)
        slots = arity - bool(cells)
        while len(ports) > slots:
            ports = reduce_level(ports)
        active = cells + ports
    else:
        active = list(range(len(clusters)))
    while len(active) > 1:
        active = reduce_level(active)
    return clusters, entity_leaf, active[0]


def signal_links(source_leaf, sink_leaves, clusters):
    """Minimal tree union, oriented from the actual source (including clocks)."""
    ancestors, node = [], source_leaf
    while node is not None:
        ancestors.append(node)
        node = clusters[node]["parent"]
    source_ancestors = set(ancestors)
    edges = set()
    for sink in sorted(set(sink_leaves)):
        node = sink
        while node not in source_ancestors:
            parent = clusters[node]["parent"]
            if parent is None:
                raise PlanningError("disconnected partition tree")
            edges.add((node, parent))
            node = parent
        for child in ancestors[:ancestors.index(node)]:
            edges.add((child, clusters[child]["parent"]))
    neighbors = defaultdict(set)
    for child, parent in edges:
        neighbors[child].add(parent)
        neighbors[parent].add(child)
    directed, queue, seen = [], deque([source_leaf]), {source_leaf}
    while queue:
        parent = queue.popleft()
        for child in sorted(neighbors[parent]):
            if child not in seen:
                directed.append((parent, child))
                seen.add(child)
                queue.append(child)
    if not set(sink_leaves).issubset(seen) or len(directed) != len(edges):
        raise PlanningError("signal fanout tree lost a sink or contains a cycle")
    return directed


def build_plan(graph: Graph, *, cell_budget=64, boundary_budget=128,
               domain_budget=256, arity=4, retain_signals=True, ports_at_root=False):
    if any(type(value) is not int for value in (cell_budget, boundary_budget, domain_budget, arity)):
        raise PlanningError("partition budgets must be finite integers")
    if cell_budget < 1 or boundary_budget < 1 or arity < 2 or domain_budget < arity + 1:
        raise PlanningError("budgets cannot represent a complete bounded routing branch")
    if type(ports_at_root) is not bool:
        raise PlanningError("ports_at_root must be an explicit boolean")
    groups = bounded_cells(graph, cell_budget, boundary_budget)
    clusters, entity_leaf, root = make_tree(groups, graph.cell_count, len(graph.entities),
                                           arity=arity, port_budget=min(boundary_budget, cell_budget),
                                           ports_at_root=ports_at_root)
    incoming, outgoing = defaultdict(set), defaultdict(set)
    domains, domain_lists = [], defaultdict(list)
    signal_records, topology_hash, endpoint_hash = [], hashlib.sha256(), hashlib.sha256()
    total_links = clock_sinks = total_sinks = maximum_branch = fanout_branches = 0
    for bit, signal in graph.signals.items():
        source_leaf = entity_leaf[signal.source.entity]
        sink_leaves = {entity_leaf[sink.entity] for sink in signal.sinks}
        for leaf in sink_leaves - {source_leaf}:
            incoming[leaf].add(bit)
        if sink_leaves - {source_leaf}:
            outgoing[source_leaf].add(bit)
        links = signal_links(source_leaf, sink_leaves, clusters)
        destinations, predecessors = defaultdict(list), {}
        for source, sink in links:
            destinations[source].append(sink)
            if sink in predecessors:
                raise PlanningError("multiple incoming branches would short a signal")
            predecessors[sink] = source
        segments = []
        for cid in sorted(set(destinations) | set(predecessors)):
            if not clusters[cid]["children"]:
                continue
            outputs = sorted(destinations[cid])
            if cid not in predecessors:
                raise PlanningError("internal routing node has an invented signal driver")
            terminals = 1 + len(outputs)
            maximum_branch = max(maximum_branch, len(outputs))
            fanout_branches += int(len(outputs) > 1)
            if terminals > domain_budget or len(outputs) > arity:
                raise PlanningError(f"signal {bit} has an unplaceable fanout at cluster {cid}")
            existing = domain_lists[cid]
            if not existing or domains[existing[-1]]["terminal_count"] + terminals > domain_budget:
                did = len(domains)
                domains.append({"id": did, "cluster_id": cid, "terminal_count": 0, "segment_count": 0, "segments": []})
                existing.append(did)
            domain = domains[existing[-1]]
            segment = {"net": bit, "incoming_cluster": predecessors[cid], "outgoing_clusters": outputs}
            domain["terminal_count"] += terminals
            domain["segment_count"] += 1
            if retain_signals:
                domain["segments"].append(segment)
            segments.append((cid, domain["id"], predecessors[cid], outputs))
        clock_uses = sum(sink.entity < graph.cell_count and sink.pin == "C" and
                         template_for(graph.entities[sink.entity]["cell_type"]).cell_type == "DFF"
                         for sink in signal.sinks)
        domain_by_cluster = {cid: did for cid, did, _, _ in segments}
        boundary_links = []
        for source, sink in links:
            identity = f"net{bit}:cluster{source}>cluster{sink}"
            boundary_links.append({
                "id": identity,
                "source": {"cluster": source, "domain": domain_by_cluster.get(source),
                           "terminal_id": identity + ":out"},
                "sink": {"cluster": sink, "domain": domain_by_cluster.get(sink),
                         "terminal_id": identity + ":in"},
            })
        record = {"bit": bit, "source": signal.source.record(), "sinks": [sink.record() for sink in signal.sinks],
                  "clock_leaf_uses": clock_uses, "directed_links": links, "domain_segments": segments}
        if retain_signals:
            record["boundary_links"] = boundary_links
        encoded = json.dumps(record, separators=(",", ":"), sort_keys=True).encode()
        topology_hash.update(encoded)
        endpoint_hash.update(json.dumps([bit, signal.source.record(), [sink.record() for sink in signal.sinks]],
                                        separators=(",", ":"), sort_keys=True).encode())
        if retain_signals:
            signal_records.append(record)
        total_links += len(links)
        clock_sinks += clock_uses
        total_sinks += len(signal.sinks)
    boundaries = []
    for cluster in clusters:
        if cluster["children"]:
            continue
        cid = cluster["id"]
        terminal_count = len(incoming[cid]) + len(outgoing[cid])
        if terminal_count > boundary_budget:
            raise PlanningError(f"leaf {cid} needs {terminal_count} boundary terminals; budget={boundary_budget}")
        boundaries.append({"cluster_id": cid, "incoming_nets": sorted(incoming[cid]),
                           "outgoing_nets": sorted(outgoing[cid]), "terminal_count": terminal_count})
    return {
        "status": ("topology-complete-geometry-and-timing-unverified" if retain_signals
                   else "topology-summary-only-geometry-and-timing-unverified"),
        "source": dict(graph.source),
        "budgets": {"native_cells_per_leaf": cell_budget, "boundary_terminals_per_leaf": boundary_budget,
                    "routing_terminals_per_domain": domain_budget, "children_per_cluster": arity},
        "entities": [dict(entity) for entity in graph.entities], "clusters": clusters,
        "root_cluster": root, "entity_leaf": entity_leaf,
        "leaf_boundaries": boundaries, "routing_domains": domains, "signals": signal_records,
        "constants": [{"value": value, "endpoint": endpoint.record()} for value, endpoint in graph.constants],
        "summary": {"native_cells": graph.cell_count, "original_port_bits": len(graph.entities) - graph.cell_count,
                    "signals": len(graph.signals), "original_sink_uses": total_sinks, "clock_leaf_uses": clock_sinks,
                    "constant_terminals": len(graph.constants), "leaf_clusters": len(boundaries),
                    "internal_clusters": len(clusters) - len(boundaries), "hierarchy_depth": clusters[root]["level"],
                    "bounded_router_domains": len(domains), "signal_tree_links": total_links,
                    "explicit_fanout_branches": fanout_branches, "maximum_router_fanout": maximum_branch,
                    "maximum_leaf_boundary": max((row["terminal_count"] for row in boundaries), default=0),
                    "maximum_domain_terminals": max((row["terminal_count"] for row in domains), default=0),
                    "maximum_domains_at_one_cluster": max(map(len, domain_lists.values()), default=0),
                    "topology_sha256": topology_hash.hexdigest(), "original_signal_endpoints_sha256": endpoint_hash.hexdigest(),
                    "all_signal_records_retained": retain_signals},
        "verification": {"all_entities_assigned_once": True, "all_signal_sink_paths_constructed": True,
                         "all_signal_sink_paths_retained": retain_signals,
                         "all_constants_retained": True, "budgets_checked": True,
                         "physical_geometry_emitted": False, "timing_closed": False,
                         "complete_world_verified": False},
    }


def audit_plan(graph: Graph, plan: dict):
    """Independent reconstruction of each emitted signal's reachable leaves."""
    if any(type(value) is not int for value in plan["budgets"].values()):
        raise PlanningError("partition budgets must be finite integers")
    budgets = plan["budgets"]
    if (budgets["native_cells_per_leaf"] < 1 or budgets["boundary_terminals_per_leaf"] < 1
            or budgets["children_per_cluster"] < 2
            or budgets["routing_terminals_per_domain"] < budgets["children_per_cluster"] + 1):
        raise PlanningError("budgets cannot represent a complete bounded routing branch")
    if not plan["summary"]["all_signal_records_retained"]:
        raise PlanningError("cannot reconstruct a summary-only plan; retain complete signal records")
    for field in ("sha256", "top", "lowering_source_sha256", "raw_cells", "lowered_cells"):
        if plan["source"].get(field) != graph.source.get(field):
            raise PlanningError("plan provenance does not match the exact lowered source graph")
    entities = Counter(entity for cluster in plan["clusters"]
                       for entity in (*cluster["cell_ids"], *cluster["port_ids"]))
    if entities != Counter(range(len(graph.entities))):
        raise PlanningError("plan dropped or duplicated an original entity")
    clusters = plan["clusters"]
    leaf_by_entity = [-1] * len(graph.entities)
    child_uses = Counter(child for cluster in clusters for child in cluster["children"])
    for index, cluster in enumerate(clusters):
        if cluster["id"] != index or len(cluster["children"]) > plan["budgets"]["children_per_cluster"]:
            raise PlanningError("hierarchy has an unbounded or misidentified cluster")
        if len(cluster["cell_ids"]) > plan["budgets"]["native_cells_per_leaf"]:
            raise PlanningError("leaf exceeds native cell budget")
        if cluster["children"] and (cluster["cell_ids"] or cluster["port_ids"]):
            raise PlanningError("native entities were placed in a parent routing domain")
        if not cluster["children"] and not (cluster["cell_ids"] or cluster["port_ids"]):
            raise PlanningError("hierarchy invented an empty native/interface leaf")
        if cluster["parent"] is None:
            if index != plan["root_cluster"] or child_uses[index]:
                raise PlanningError("hierarchy contains a disconnected root")
        elif child_uses[index] != 1 or index not in clusters[cluster["parent"]]["children"]:
            raise PlanningError("cluster has no unique matching parent")
        for entity in (*cluster["cell_ids"], *cluster["port_ids"]):
            leaf_by_entity[entity] = index
        if any(entity >= graph.cell_count for entity in cluster["cell_ids"]) or any(entity < graph.cell_count for entity in cluster["port_ids"]):
            raise PlanningError("leaf changed a native cell into an interface terminal")
    if leaf_by_entity != plan["entity_leaf"]:
        raise PlanningError("entity ownership and boundary attachment disagree")
    reachable_clusters, visited_order, pending = set(), [], [plan["root_cluster"]]
    while pending:
        cid = pending.pop()
        if cid in reachable_clusters:
            raise PlanningError("hierarchy contains a child cycle or duplicate child")
        reachable_clusters.add(cid)
        visited_order.append(cid)
        pending.extend(clusters[cid]["children"])
    if reachable_clusters != set(range(len(clusters))):
        raise PlanningError("hierarchy contains an unreachable cell or interface cluster")
    actual_levels = {}
    for cid in reversed(visited_order):
        actual_levels[cid] = (1 + max(actual_levels[child] for child in clusters[cid]["children"])
                              if clusters[cid]["children"] else 0)
        if clusters[cid]["level"] != actual_levels[cid]:
            raise PlanningError("hierarchy misreports its actual routing depth")
    if plan["entities"] != graph.entities or plan["constants"] != [
        {"value": value, "endpoint": endpoint.record()} for value, endpoint in graph.constants
    ]:
        raise PlanningError("plan changed an original cell, port or constant")
    records = {record["bit"]: record for record in plan["signals"]}
    if len(records) != len(plan["signals"]) or set(records) != set(graph.signals):
        raise PlanningError("plan dropped, duplicated or invented an original signal")
    domain_segments = defaultdict(list)
    for did, domain in enumerate(plan["routing_domains"]):
        if domain["id"] != did or not clusters[domain["cluster_id"]]["children"]:
            raise PlanningError("routing domain has no valid bounded parent")
        if not domain["segments"]:
            raise PlanningError("routing domain does not participate in an original signal")
        actual_terminals = sum(1 + len(segment["outgoing_clusters"]) for segment in domain["segments"])
        if actual_terminals != domain["terminal_count"] or actual_terminals > plan["budgets"]["routing_terminals_per_domain"]:
            raise PlanningError("router domain exceeds or misreports its terminal budget")
        if domain["segment_count"] != len(domain["segments"]):
            raise PlanningError("router domain misreports its segment count")
        for segment in domain["segments"]:
            domain_segments[segment["net"]].append((domain["cluster_id"], domain["id"],
                                                    segment["incoming_cluster"], segment["outgoing_clusters"]))
    if set(domain_segments).difference(graph.signals):
        raise PlanningError("router domain contains an invented signal")
    total_clock_leaves = total_links = total_sinks = fanout_branches = maximum_branch = 0
    topology_hash, endpoint_hash = hashlib.sha256(), hashlib.sha256()
    for bit, original in graph.signals.items():
        record = records[bit]
        if record["source"] != original.source.record() or record["sinks"] != [sink.record() for sink in original.sinks]:
            raise PlanningError("plan changed an original driver or sink identity")
        actual_clock_leaves = sum(sink.entity < graph.cell_count and sink.pin == "C" and
                                  template_for(graph.entities[sink.entity]["cell_type"]).cell_type == "DFF"
                                  for sink in original.sinks)
        if record["clock_leaf_uses"] != actual_clock_leaves:
            raise PlanningError("signal misreports its original clock leaf count")
        total_clock_leaves += actual_clock_leaves
        total_sinks += len(original.sinks)
        total_links += len(record["directed_links"])
        adjacency, indegree = defaultdict(list), Counter()
        for source, sink in record["directed_links"]:
            if source not in range(len(plan["clusters"])) or sink not in range(len(plan["clusters"])):
                raise PlanningError("signal has a nonexistent routing endpoint")
            a, b = plan["clusters"][source], plan["clusters"][sink]
            if a["parent"] != sink and b["parent"] != source:
                raise PlanningError("signal skips its bounded hierarchy boundary")
            adjacency[source].append(sink)
            indegree[sink] += 1
        source_leaf = plan["entity_leaf"][original.source.entity]
        reachable, pending = {source_leaf}, [source_leaf]
        while pending:
            node = pending.pop()
            for neighbor in adjacency[node]:
                if neighbor in reachable:
                    raise PlanningError("signal routing contains a cycle or duplicate driver")
                reachable.add(neighbor)
                pending.append(neighbor)
        edge_nodes = {node for link in record["directed_links"] for node in link}
        if not edge_nodes.issubset(reachable):
            raise PlanningError("signal contains disconnected routing edges")
        targets = {plan["entity_leaf"][sink.entity] for sink in original.sinks}
        if not targets.issubset(reachable) or any(count != 1 for count in indegree.values()):
            raise PlanningError("signal tree dropped or multiply drove a sink leaf")
        if any(node not in targets and not adjacency[node] for node in reachable):
            raise PlanningError("signal tree has an unexplained dangling branch")
        if sorted(domain_segments[bit]) != sorted(tuple(segment) for segment in record["domain_segments"]):
            raise PlanningError("signal topology and bounded domain assignment disagree")
        by_cluster = {segment[0]: segment for segment in domain_segments[bit]}
        internal_nodes = {node for node in reachable if clusters[node]["children"]}
        if set(by_cluster) != internal_nodes or len(by_cluster) != len(domain_segments[bit]):
            raise PlanningError("signal has no unique physical routing domain at every internal node")
        predecessors = {sink: source for source, sink in record["directed_links"]}
        for node in internal_nodes:
            _, _, incoming, outgoing = by_cluster[node]
            if incoming != predecessors.get(node) or sorted(outgoing) != sorted(adjacency[node]):
                raise PlanningError("routing domain does not implement its signal tree links")
            fanout_branches += int(len(outgoing) > 1)
            maximum_branch = max(maximum_branch, len(outgoing))
        boundary_links = record["boundary_links"]
        if len(boundary_links) != len(record["directed_links"]):
            raise PlanningError("signal boundary terminal list is incomplete")
        for link, (source, sink) in zip(boundary_links, record["directed_links"]):
            identity = f"net{bit}:cluster{source}>cluster{sink}"
            for side, node, suffix in (("source", source, "out"), ("sink", sink, "in")):
                if (link["id"] != identity or link[side]["cluster"] != node
                        or link[side]["terminal_id"] != identity + ":" + suffix
                        or link[side]["domain"] != (by_cluster[node][1] if node in by_cluster else None)):
                    raise PlanningError("signal boundary terminal identity or domain is incorrect")
        canonical_record = {key: record[key] for key in (
            "bit", "source", "sinks", "clock_leaf_uses", "directed_links", "domain_segments", "boundary_links")}
        topology_hash.update(json.dumps(canonical_record, separators=(",", ":"), sort_keys=True).encode())
        endpoint_hash.update(json.dumps([bit, original.source.record(), [sink.record() for sink in original.sinks]],
                                        separators=(",", ":"), sort_keys=True).encode())
    incoming, outgoing = defaultdict(set), defaultdict(set)
    for bit, signal in graph.signals.items():
        source = leaf_by_entity[signal.source.entity]
        sinks = {leaf_by_entity[sink.entity] for sink in signal.sinks} - {source}
        for sink in sinks:
            incoming[sink].add(bit)
        if sinks:
            outgoing[source].add(bit)
    expected_boundaries = [{"cluster_id": cluster["id"], "incoming_nets": sorted(incoming[cluster["id"]]),
                            "outgoing_nets": sorted(outgoing[cluster["id"]]),
                            "terminal_count": len(incoming[cluster["id"]]) + len(outgoing[cluster["id"]])}
                           for cluster in clusters if not cluster["children"]]
    if expected_boundaries != plan["leaf_boundaries"] or any(row["terminal_count"] > plan["budgets"]["boundary_terminals_per_leaf"] for row in expected_boundaries):
        raise PlanningError("leaf boundary budget or complete net inventory is incorrect")
    if plan["summary"]["clock_leaf_uses"] != total_clock_leaves:
        raise PlanningError("plan misreports the complete clock leaf count")
    domains_per_cluster = Counter(domain["cluster_id"] for domain in plan["routing_domains"])
    expected_summary = {
        "native_cells": graph.cell_count, "original_port_bits": len(graph.entities) - graph.cell_count,
        "signals": len(graph.signals), "original_sink_uses": total_sinks, "clock_leaf_uses": total_clock_leaves,
        "constant_terminals": len(graph.constants), "leaf_clusters": len(expected_boundaries),
        "internal_clusters": len(clusters) - len(expected_boundaries),
        "hierarchy_depth": actual_levels[plan["root_cluster"]],
        "bounded_router_domains": len(plan["routing_domains"]), "signal_tree_links": total_links,
        "explicit_fanout_branches": fanout_branches, "maximum_router_fanout": maximum_branch,
        "maximum_leaf_boundary": max((row["terminal_count"] for row in expected_boundaries), default=0),
        "maximum_domain_terminals": max((row["terminal_count"] for row in plan["routing_domains"]), default=0),
        "maximum_domains_at_one_cluster": max(domains_per_cluster.values(), default=0),
        "topology_sha256": topology_hash.hexdigest(), "original_signal_endpoints_sha256": endpoint_hash.hexdigest(),
        "all_signal_records_retained": True,
    }
    for key, value in expected_summary.items():
        if plan["summary"].get(key) != value:
            raise PlanningError(f"plan misreports reconstructed structural summary: {key}")
    return {"status": "passed", "signals": len(records), "native_cells": graph.cell_count,
            "scope": "complete structural graph reconstruction; no physical routing or timing claim"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mapped_json", type=Path)
    parser.add_argument("--top", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary-only", action="store_true")
    parser.add_argument("--cell-budget", type=int, default=64)
    parser.add_argument("--boundary-budget", type=int, default=128)
    parser.add_argument("--domain-budget", type=int, default=256)
    args = parser.parse_args()
    started = time.monotonic()
    graph = Graph.read(args.mapped_json, args.top)
    print(json.dumps({"loaded_cells": graph.cell_count, "signals": len(graph.signals), "seconds": time.monotonic()-started}), flush=True)
    plan = build_plan(graph, cell_budget=args.cell_budget, boundary_budget=args.boundary_budget,
                      domain_budget=args.domain_budget, retain_signals=not args.summary_only)
    if not args.summary_only:
        plan["independent_structural_audit"] = audit_plan(graph, plan)
    plan["elapsed_seconds"] = time.monotonic() - started
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(plan, separators=(",", ":")) + "\n")
    print(json.dumps(plan["summary"] | {"elapsed_seconds": plan["elapsed_seconds"], "output_bytes": args.output.stat().st_size}, indent=2), flush=True)


if __name__ == "__main__":
    main()
