"""Independently conserve selected subtree pins against the complete graph.

No literal block union is materialized. The full mapped graph and plan retain
the identities of consumers beyond the selected subtree. Literal collision,
isolation and actual-engine timing remain independent acceptance gates.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import gzip
import hashlib
import json
from pathlib import Path

from shamaos.hardware.logic import template_for
from shamaos.hardware.partition_planner import Graph, audit_plan, build_plan


def check(value, message):
    if not value:
        raise ValueError(message)


def point(value):
    check(set(value) == {"x", "y", "z"}, "coordinate fields changed")
    result = tuple(value[axis] for axis in ("x", "y", "z"))
    check(all(type(v) is int for v in result), "coordinate is not an integer")
    return result


def contains(bounds, p):
    lo, hi = point(bounds["min"]), point(bounds["max"])
    return all(a <= value <= b for a, value, b in zip(lo, p, hi))


def audit(graph, plan, manifest):
    audit_plan(graph, plan)
    check(manifest["schema"] == "shama-complete-subtree-geometry-v1", "unknown manifest schema")
    provenance = manifest["provenance"]
    check(provenance["full_plan_topology_sha256"] == plan["summary"]["topology_sha256"], "full-plan topology changed")
    check(provenance["mapped_source"]["sha256"] == graph.source["sha256"], "complete mapped graph changed")
    planned = {cluster["id"]: cluster for cluster in plan["clusters"]}
    root = manifest["root_cluster"]
    check(root in planned, "selected root is not in complete plan")
    descendants = {}

    def below(cluster):
        if cluster not in descendants:
            descendants[cluster] = {cluster}
            for child in planned[cluster]["children"]:
                descendants[cluster].update(below(child))
        return descendants[cluster]

    selected = below(root)
    clusters = {record["cluster_id"]: record for record in manifest["clusters"]}
    check(len(clusters) == len(manifest["clusters"]) and set(clusters) == selected,
          "selected hierarchy lost, duplicated or invented a cluster")
    modules = {tuple(record["key"]): record for record in manifest["modules"]}
    check(len(modules) == len(manifest["modules"]), "duplicate physical module identity")
    domains = defaultdict(set)
    for domain in plan["routing_domains"]:
        domains[domain["cluster_id"]].add(("domain", domain["id"]))
    expected_modules = set()
    owner = {}
    for cluster_id, record in clusters.items():
        expected = planned[cluster_id]
        check(sorted(record["children"]) == sorted(expected["children"]), "a complete child group changed")
        own = domains[cluster_id] if expected["children"] else {("leaf", cluster_id)}
        check(len(record["own_module_keys"]) == len(own) and set(map(tuple, record["own_module_keys"])) == own,
              "cluster changed its own leaf/domain inventory")
        expected_modules.update(own)
        owner.update(dict.fromkeys(own, cluster_id))
        lo, hi = point(record["bounds"]["min"]), point(record["bounds"]["max"])
        check(all(a <= b for a, b in zip(lo, hi)) and lo[1] >= -60 and hi[1] <= 319,
              "cluster leaves the default above-grass build envelope")
        for child in record["children"]:
            check(contains(record["bounds"], point(clusters[child]["bounds"]["min"]))
                  and contains(record["bounds"], point(clusters[child]["bounds"]["max"])),
                  "child leaves its declared parent envelope")
    check(set(modules) == expected_modules, "selected subtree changed complete module inventory")
    entities = {entity["id"]: entity for entity in graph.entities}
    local_entities = {entity for entity in entities if plan["entity_leaf"][entity] in selected}
    root_ports = {int(entity): point(p) for entity, p in clusters[root]["ports"].items()}
    local_ports = {entity for entity in local_entities if entities[entity]["kind"] == "port"}
    check(set(root_ports) == local_ports, "original selected ports changed")
    expected_cells = defaultdict(dict)
    for entity in local_entities:
        record = entities[entity]
        if record["kind"] == "cell":
            expected_cells[("leaf", plan["entity_leaf"][entity])][record["name"]] = record["cell_type"]
    actual_origins = []
    for key, record in modules.items():
        physical = record["physical"]
        check(physical["cell_types"] == expected_cells[key], "native cell owner, identity or type changed")
        check(set(physical["cell_origins"]) == set(physical["cell_types"]), "missing or extra cell origin")
        for name, origin in physical["cell_origins"].items():
            actual_origins.append(point(origin))
            check(contains(clusters[owner[key]]["bounds"], point(origin)), "cell origin leaves owning cluster")
    check(len(actual_origins) == len(set(actual_origins)), "native cells alias a physical origin")

    expected_ports = defaultdict(lambda: ({}, {}))

    def native(endpoint):
        entity = entities[endpoint.entity]
        key = ("leaf", plan["entity_leaf"][endpoint.entity])
        if entity["kind"] == "port":
            label = f"entity{endpoint.entity}"
            direction = 0 if entity["direction"] == "input" else 1
            expected_ports[key][direction][label] = (root_ports[endpoint.entity],)
            return (key, f"port:{label}[0]", label), root_ports[endpoint.entity]
        check(endpoint.index == 0, "unexpanded native bus pin")
        base = point(modules[key]["physical"]["cell_origins"][entity["name"]])
        offset = template_for(entity["cell_type"]).pins[endpoint.pin]
        return ((key, entity["name"], endpoint.pin),
                tuple(base[i] + getattr(offset, axis) for i, axis in enumerate(("x", "y", "z"))))

    from shamaos.hardware.partition_planner import Endpoint
    for entity in local_ports:
        record = entities[entity]
        native(Endpoint(entity, record["port_name"], record["port_index"]))
    all_links = [(signal["bit"], link) for signal in plan["signals"] for link in signal["boundary_links"]]
    terminal_nodes, terminal_cluster, internal, crossing = {}, {}, {}, []
    expected = defaultdict(dict)
    original_sources, native_sinks = {}, defaultdict(list)
    for bit, signal in graph.signals.items():
        if signal.source.entity in local_entities:
            node, p = native(signal.source)
            expected[bit][node] = (p, True)
            original_sources[bit] = node
        for sink in signal.sinks:
            if sink.entity in local_entities:
                node, p = native(sink)
                expected[bit][node] = (p, False)
                native_sinks[bit].append(node)
    terminal_positions = {label: point(p) for label, p in clusters[root]["terminals"].items()}
    incoming, outgoing = defaultdict(list), defaultdict(list)
    for bit, link in all_links:
        inside = [label for label in ("source", "sink") if link[label]["cluster"] in selected]
        if not inside:
            continue
        if len(inside) == 2:
            internal[link["id"]] = (bit, link)
        else:
            crossing.append((bit, link))
        for label in inside:
            end, other = link[label], link["sink" if label == "source" else "source"]
            key = ("leaf", end["cluster"]) if end["domain"] is None else ("domain", end["domain"])
            terminal = end["terminal_id"]
            name = f"port:{terminal}[0]" if key[0] == "leaf" else f"boundary:{terminal}"
            check(terminal in terminal_positions and terminal not in terminal_nodes, "lost or repeated boundary terminal")
            node = (key, name, terminal)
            terminal_nodes[terminal] = node
            terminal_cluster[terminal] = end["cluster"]
            expected[bit][node] = (terminal_positions[terminal], label == "sink")
            if key[0] == "leaf" or planned[end["cluster"]]["parent"] == other["cluster"]:
                expected_ports[key][0 if label == "sink" else 1][terminal] = (terminal_positions[terminal],)
            if len(inside) == 1:
                (incoming if label == "sink" else outgoing)[bit].append(node)
    check(set(terminal_positions) == set(terminal_nodes), "invented subtree boundary terminal")
    for cluster_id, record in clusters.items():
        wanted = {label: terminal_positions[label] for label, cluster in terminal_cluster.items()
                  if cluster in descendants[cluster_id]}
        check({label: point(p) for label, p in record["terminals"].items()} == wanted,
              "recursive cluster terminal aggregate changed")
        wanted_ports = {entity: root_ports[entity] for entity in local_ports
                        if plan["entity_leaf"][entity] in descendants[cluster_id]}
        check({int(entity): point(p) for entity, p in record["ports"].items()} == wanted_ports,
              "recursive original-port aggregate changed")
    contacts = manifest["shared_contacts"]
    check(set(contacts) == set(internal), "internal contact omitted or invented")
    alias_edges, shared_positions = defaultdict(list), set()
    actual_faces = 0
    for bit, link in [(bit, link) for bit, link in internal.values()] + crossing:
        endpoints = [link[label] for label in ("source", "sink") if link[label]["cluster"] in selected]
        if link["id"] in internal:
            contact = contacts[link["id"]]
            a, b = link["source"], link["sink"]
            p = terminal_positions[a["terminal_id"]]
            check(p == terminal_positions[b["terminal_id"]] == point(contact["position"]), "disconnected handoff coordinates")
            check(p not in shared_positions, "distinct handoffs alias a block")
            shared_positions.add(p)
            child = a["cluster"] if planned[a["cluster"]]["parent"] == b["cluster"] else b["cluster"]
            check((contact["child_cluster"], contact["parent_cluster"]) == (child, planned[child]["parent"]),
                  "handoff child/parent changed")
            check(contact["bit"] == bit and contact["source_terminal"] == a["terminal_id"]
                  and contact["sink_terminal"] == b["terminal_id"], "handoff directed identity changed")
            check((contact["child_access"], contact["parent_access"]) == ("north", "south"), "declared handoff face changed")
            alias_edges[bit].append((terminal_nodes[a["terminal_id"]], terminal_nodes[b["terminal_id"]]))
        else:
            child = root
        for end in endpoints:
            key = terminal_nodes[end["terminal_id"]][0]
            p = terminal_positions[end["terminal_id"]]
            escapes = {point(escape["point"]): escape for escape in modules[key]["escapes"]}
            check(p in escapes, "missing boundary escape")
            escape = escapes[p]
            north = end["cluster"] == child
            check(escape["escape_x"] is None and escape["wall_lead_z"] is None
                  and ((escape["escape_z"] < p[2]) if north else (escape["escape_z"] > p[2])),
                  "actual directed boundary escape face changed")
            actual_faces += 1
    seen, local_edges = defaultdict(Counter), defaultdict(list)
    for key, record in modules.items():
        physical = record["physical"]
        for index, field in enumerate(("input_ports", "output_ports")):
            observed = {name: tuple(map(point, positions)) for name, positions in physical[field].items()}
            check(observed == expected_ports[key][index], "physical module port inventory changed")
        bits, positions = set(), set()
        for net in physical["nets"]:
            bit = net["bit"]
            check(bit in expected and bit not in bits, "invented or duplicate local net")
            bits.add(bit)
            nodes = []
            check(net["sinks"], "routed net has no sinks")
            for index, endpoint in enumerate([net["source"], *net["sinks"]]):
                node = (key, endpoint["name"], endpoint["pin"])
                p = point(endpoint["pos"])
                check(node in expected[bit] and (p, endpoint["is_source"]) == expected[bit][node]
                      and type(endpoint["is_source"]) is bool and endpoint["is_source"] == (index == 0),
                      "native or boundary pin moved, reversed, or changed identity")
                check(p not in positions, "different module endpoints alias one position")
                positions.add(p)
                seen[bit][node] += 1
                nodes.append(node)
            local_edges[bit].extend((nodes[0], node) for node in nodes[1:])
        escapes = {point(escape["point"]): escape for escape in record["escapes"]}
        check(len(escapes) == len(record["escapes"]) and set(escapes) == positions, "actual escape inventory changed")
        for p, escape in escapes.items():
            check(point(escape["endpoint"]) == p and escape["branch_direction"] == -1, "escape identity or supported direction changed")
        tracks = {int(bit): value for bit, value in record["tracks"].items()}
        check(len(tracks) == len(record["tracks"]) and set(tracks) == bits
              and all(type(track) is int and track >= 0 for track in tracks.values()), "local track inventory changed")
        check(record["trunk_y"] - record["branch_y"] == 4 and -56 <= record["branch_y"] < record["trunk_y"] <= 319,
              "actual routing planes leave the permitted height or separation")
    for bit, nodes in expected.items():
        check(set(seen[bit]) == set(nodes) and set(seen[bit].values()) == {1}, "missing or duplicated physical endpoint")
        roots = ([original_sources[bit]] if bit in original_sources else []) + incoming[bit]
        check(len(roots) == 1, "selected signal has no unique original or retained boundary driver")
        edges = defaultdict(list)
        for source, sink in local_edges[bit] + alias_edges[bit]:
            edges[source].append(sink)
        pending, reached = list(roots), set()
        while pending:
            node = pending.pop()
            if node not in reached:
                reached.add(node)
                pending.extend(edges[node])
        check(reached == set(nodes), "selected endpoint is disconnected from its retained driver")
    high, north_high = defaultdict(list), defaultdict(list)
    local_constants = 0
    for value, endpoint in graph.constants:
        if endpoint.entity not in local_entities:
            continue
        local_constants += 1
        node, p = native(endpoint)
        check(all(node not in nodes for nodes in expected.values()), "constant pin also has a net driver")
        if value == "1":
            high[node[0]].append(p)
            if entities[endpoint.entity]["kind"] == "port":
                north_high[node[0]].append(p)
    for key, record in modules.items():
        physical = record["physical"]
        check(sorted(map(point, physical["constant_high"])) == sorted(high[key])
              and sorted(map(point, physical["north_constant_high"])) == sorted(north_high[key]),
              "literal constant tie or access changed")
    export_planes = []
    for cluster_id, record in clusters.items():
        if not planned[cluster_id]["children"]:
            continue
        exports = [p for key in map(tuple, record["own_module_keys"])
                   for group in expected_ports[key] for pins in group.values() for p in pins]
        if exports:
            check(all(p[2] == record["bounds"]["max"]["z"] for p in exports),
                  "outward interface is buried behind its own complete route envelope")
            export_planes.append({"cluster": cluster_id, "ports": len(exports),
                                  "z": exports[0][2], "y_values": sorted({p[1] for p in exports})})
    return {"status": "independent-subtree-bindings-passed", "root_cluster": root,
            "complete_partition": root == plan["root_cluster"], "clusters": len(selected),
            "physical_modules": len(modules), "native_cells": len(actual_origins), "original_port_bits": len(local_ports),
            "signal_endpoint_records": sum(sum(values.values()) for values in seen.values()),
            "local_signal_uses": sum(map(len, native_sinks.values())), "constant_terminals": local_constants,
            "internal_shared_contacts": len(internal), "crossing_full_plan_boundaries": len(crossing),
            "incoming_boundary_sources": sum(map(len, incoming.values())),
            "outgoing_boundary_consumers": sum(map(len, outgoing.values())), "actual_boundary_faces_checked": actual_faces,
            "exposed_parent_interfaces": export_planes,
            "full_plan_topology_sha256": plan["summary"]["topology_sha256"],
            "scope": "Selected subtree native pins, constants, contacts, directed graph and declared escape faces; full graph retains outside consumers. Literal collision/isolation and actual-engine timing are separate gates.",
            "literal_union_recomputed": False, "runtime_verified_by_this_audit": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("mapped", type=Path)
    parser.add_argument("--top", required=True)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    graph = Graph.read(args.mapped, args.top)
    plan = json.loads(args.plan.read_text()) if args.plan else build_plan(graph)
    raw = args.manifest.read_bytes()
    contents = gzip.decompress(raw) if raw.startswith(b"\x1f\x8b") else raw
    report = audit(graph, plan, json.loads(contents))
    report.update(manifest_sha256=hashlib.sha256(raw).hexdigest(),
                  uncompressed_manifest_sha256=hashlib.sha256(contents).hexdigest(),
                  auditor_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
