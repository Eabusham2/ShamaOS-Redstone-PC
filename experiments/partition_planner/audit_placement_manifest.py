"""Independent artifact audit: traverse literal module endpoint records globally.

This does not use partition_placement.audit_placement and does not assert electrical
correctness from metadata. Actual-engine saved-world checks remain separate.
"""
from __future__ import annotations

if not __debug__:
    raise RuntimeError("Partition experiment acceptance requires assertions; optimized Python (-O/-OO) is unsupported")

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

from shamaos.hardware.logic import template_for
from shamaos.hardware.partition_planner import Graph, audit_plan


def point(record):
    return tuple(record[axis] for axis in ("x", "y", "z"))


def audit(graph, plan, manifest):
    audit_plan(graph, plan)
    expected_modules = {("leaf", cluster["id"]) for cluster in plan["clusters"] if not cluster["children"]}
    expected_modules |= {("domain", domain["id"]) for domain in plan["routing_domains"]}
    modules = {tuple(module["key"]): module for module in manifest["modules"]}
    assert len(modules) == len(manifest["modules"]), "duplicate physical module keys"
    assert set(modules) == expected_modules, "missing or invented physical module"
    entities = {entity["id"]: entity for entity in graph.entities}
    original_ports = {int(entity): point(position) for entity, position in manifest["original_ports"].items()}
    assert set(original_ports) == {entity["id"] for entity in graph.entities if entity["kind"] == "port"}
    expected_cells = defaultdict(dict)
    for entity in graph.entities:
        if entity["kind"] == "cell":
            expected_cells[("leaf", plan["entity_leaf"][entity["id"]])][entity["name"]] = entity["cell_type"]
    for key, module in modules.items():
        assert module["cell_types"] == expected_cells[key], "native cell changed leaf, type, or identity"
        assert set(module["cell_origins"]) == set(module["cell_types"]), "unmatched physical cell origin"

    def native(endpoint):
        entity = entities[endpoint.entity]
        key = ("leaf", plan["entity_leaf"][endpoint.entity])
        module = modules[key]
        if entity["kind"] == "port":
            label = f"entity{endpoint.entity}"
            positions = module["input_ports" if entity["direction"] == "input" else "output_ports"].get(label)
            assert positions is not None and len(positions) == 1, "missing original physical port"
            assert point(positions[0]) == original_ports[endpoint.entity], "misplaced original physical port"
            return (key, f"port:{label}[0]", label), original_ports[endpoint.entity]
        assert endpoint.index == 0
        offset = template_for(entity["cell_type"]).pins[endpoint.pin]
        origin = point(module["cell_origins"][entity["name"]])
        return (key, entity["name"], endpoint.pin), tuple(origin[i] + getattr(offset, axis)
            for i, axis in enumerate(("x", "y", "z")))

    # Check every original port, including unused inputs and literal-zero outputs.
    from shamaos.hardware.partition_planner import Endpoint
    for entity in graph.entities:
        if entity["kind"] == "port":
            native(Endpoint(entity["id"], entity["port_name"], entity["port_index"]))

    terminal_owner = {}
    expected_contacts = {}
    all_links = []
    for record in plan["signals"]:
        for link in record["boundary_links"]:
            all_links.append((record["bit"], link))
            expected_contacts[link["id"]] = link
            for role in ("source", "sink"):
                end = link[role]
                kind = "leaf" if end["domain"] is None else "domain"
                key = (kind, end["cluster"] if kind == "leaf" else end["domain"])
                label = end["terminal_id"]
                name = f"port:{label}[0]" if kind == "leaf" else f"boundary:{label}"
                assert label not in terminal_owner
                terminal_owner[label] = ((key, name, label), role == "sink", record["bit"])
    assert set(manifest["terminals"]) == set(terminal_owner), "extra or missing terminal location"
    assert set(manifest["shared_contacts"]) == set(expected_contacts), "extra or missing shared contact"
    parents = {cluster["id"]: cluster["parent"] for cluster in plan["clusters"]}
    contact_points = {}
    alias_edges = defaultdict(list)
    for bit, link in all_links:
        contact = manifest["shared_contacts"][link["id"]]
        source, sink = link["source"], link["sink"]
        source_point = point(manifest["terminals"][source["terminal_id"]])
        sink_point = point(manifest["terminals"][sink["terminal_id"]])
        assert source_point == sink_point == point(contact["position"]), "disconnected contact coordinates"
        assert contact["bit"] == bit
        assert contact["source_terminal"] == source["terminal_id"] and contact["sink_terminal"] == sink["terminal_id"]
        child = source["cluster"] if parents[source["cluster"]] == sink["cluster"] else sink["cluster"]
        assert contact["child_cluster"] == child and contact["parent_cluster"] == parents[child]
        assert (contact["child_access"], contact["parent_access"]) == ("north", "south")
        assert source_point not in contact_points, "two distinct handoffs alias the same block"
        contact_points[source_point] = bit
        alias_edges[bit].append((terminal_owner[source["terminal_id"]][0], terminal_owner[sink["terminal_id"]][0]))

    expected_endpoints = defaultdict(dict)
    source_node, sink_nodes = {}, {}
    for bit, signal in graph.signals.items():
        source_node[bit], location = native(signal.source)
        expected_endpoints[bit][source_node[bit]] = (location, True)
        sink_nodes[bit] = []
        for endpoint in signal.sinks:
            node, location = native(endpoint)
            assert node not in expected_endpoints[bit], "original endpoint reused with incompatible roles"
            expected_endpoints[bit][node] = (location, False)
            sink_nodes[bit].append(node)
    for label, (node, source, bit) in terminal_owner.items():
        expected_endpoints[bit][node] = (point(manifest["terminals"][label]), source)

    seen_endpoints, local_edges = defaultdict(Counter), defaultdict(list)
    for key, module in modules.items():
        seen_bits = set()
        for net in module["nets"]:
            bit = net["bit"]
            assert bit in graph.signals, "invented physical signal"
            assert bit not in seen_bits, "duplicate local net bit"
            seen_bits.add(bit)
            endpoints = [net["source"], *net["sinks"]]
            assert len(endpoints) > 1, "physical net has no sinks"
            nodes = []
            for index, endpoint in enumerate(endpoints):
                node = (key, endpoint["name"], endpoint["pin"])
                assert node in expected_endpoints[bit], "unexpected local endpoint"
                assert (point(endpoint["pos"]), endpoint["is_source"]) == expected_endpoints[bit][node], "native or boundary pin moved or reversed"
                assert endpoint["is_source"] == (index == 0), "local endpoint role mismatch"
                seen_endpoints[bit][node] += 1
                nodes.append(node)
            local_edges[bit].extend((nodes[0], sink) for sink in nodes[1:])
    for bit in graph.signals:
        assert set(seen_endpoints[bit]) == set(expected_endpoints[bit]), "missing physical endpoint"
        assert set(seen_endpoints[bit].values()) == {1}, "physical endpoint occurs more than once"
        edges = local_edges[bit] + alias_edges[bit]
        outgoing = defaultdict(list)
        for source, sink in edges:
            outgoing[source].append(sink)
        pending, visited = [source_node[bit]], set()
        while pending:
            node = pending.pop()
            if node in visited:
                continue
            visited.add(node)
            pending.extend(outgoing[node])
        assert visited == set(expected_endpoints[bit]), "physical endpoint is not source-reachable"
        assert set(sink_nodes[bit]) <= visited

    expected_high, expected_north = defaultdict(list), defaultdict(list)
    for value, endpoint in graph.constants:
        node, location = native(endpoint)
        assert all(node not in expected for expected in expected_endpoints.values()), "constant terminal also driven by a net"
        if value == "1":
            expected_high[node[0]].append(location)
            if entities[endpoint.entity]["kind"] == "port":
                expected_north[node[0]].append(location)
    for key, module in modules.items():
        assert sorted(map(point, module["constant_high"])) == sorted(expected_high[key])
        assert sorted(map(point, module["north_constant_high"])) == sorted(expected_north[key])
    return {
        "status": "independent-artifact-graph-audit-passed",
        "physical_modules": len(modules),
        "native_cells": graph.cell_count,
        "original_ports": len(original_ports),
        "signals": len(graph.signals),
        "original_sink_uses": sum(len(signal.sinks) for signal in graph.signals.values()),
        "constant_terminals": len(graph.constants),
        "physical_endpoint_records": sum(sum(values.values()) for values in seen_endpoints.values()),
        "local_directed_edges": sum(map(len, local_edges.values())),
        "shared_contact_edges": sum(map(len, alias_edges.values())),
        "clock_leaf_uses": plan["summary"]["clock_leaf_uses"],
        "electrical_runtime_verified_by_this_audit": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact_directory", type=Path)
    parser.add_argument("--top", default="pipeline")
    args = parser.parse_args()
    folder = args.artifact_directory
    graph = Graph.read(folder / "mapped.json", args.top)
    report = audit(graph, json.loads((folder / "plan.json").read_text()),
                   json.loads((folder / "placement.json").read_text()))
    report["artifact_sha256"] = {name: hashlib.sha256((folder / name).read_bytes()).hexdigest()
                                 for name in ("mapped.json", "plan.json", "placement.json")}
    print(json.dumps(report, indent=2))
