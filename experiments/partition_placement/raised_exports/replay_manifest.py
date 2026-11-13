"""Re-emit a complete retained geometry manifest without repeating route search.

This replays recorded decisions. It does not replace the original allocator's
isolation guards, a graph inventory audit or scheduled Minecraft runtime proof.
The command requires an independently retained manifest digest and exact source
bindings before it publishes a new literal-union result.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, fields
import gzip
import hashlib
import json
from pathlib import Path
import sys
import time

DIRECTORY = Path(__file__).resolve().parent
REPOSITORY = DIRECTORY.parents[2]
sys.path.insert(0, str(REPOSITORY / "src"))
REQUIRED_SOURCE_BINDINGS = (
    "src/shamaos/hardware/partition_placement.py", "src/shamaos/hardware/partition_planner.py",
    "src/shamaos/hardware/local_router.py", "src/shamaos/hardware/external_router.py",
    "src/shamaos/hardware/logic.py", "src/shamaos/hardware/routing.py",
    "src/shamaos/hardware/memory.py", "src/shamaos/synthesis.py",
)

from shamaos.hardware.external_router import _Escape
from shamaos.hardware.local_router import LocalTile
from shamaos.hardware.partition_placement import PlacedCluster
from shamaos.model import BlockState, Box, Vec3
from shamaos.synthesis import Endpoint, PhysicalNetlist, RoutedNet


def _mapping(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r} in retained geometry")
        result[key] = value
    return result


def _integer(value):
    if type(value) is not int:
        raise ValueError("geometry integer is not an exact JSON integer")
    return value


def _indices(record):
    result = {}
    for key, value in record.items():
        index = int(key)
        if str(index) != key:
            raise ValueError("noncanonical integer identity in retained geometry")
        result[index] = value
    return result


def _vector(record):
    if set(record) != {"x", "y", "z"}:
        raise ValueError("invalid geometry coordinate fields")
    return Vec3(*(_integer(record[axis]) for axis in ("x", "y", "z")))


def _box(record):
    if set(record) != {"min", "max"}:
        raise ValueError("invalid geometry envelope fields")
    return Box(_vector(record["min"]), _vector(record["max"]))


def _key(record):
    if not isinstance(record, list) or len(record) != 2 or record[0] not in ("leaf", "domain"):
        raise ValueError("invalid physical module key")
    return record[0], _integer(record[1])


def _endpoint(record):
    if set(record) != {field.name for field in fields(Endpoint)}:
        raise ValueError("invalid native endpoint fields")
    if type(record["is_source"]) is not bool:
        raise ValueError("endpoint direction must be a JSON boolean")
    return Endpoint(record["name"], record["pin"], _vector(record["pos"]), record["is_source"])


def _physical(record):
    if set(record) != {field.name for field in fields(PhysicalNetlist)}:
        raise ValueError("manifest does not preserve the complete physical netlist")
    value = dict(record)
    value["origin"] = _vector(value["origin"])
    value["cell_origins"] = {name: _vector(point) for name, point in value["cell_origins"].items()}
    if value["cell_origins"].keys() != value["cell_types"].keys():
        raise ValueError("native cell type/origin inventories differ")
    for field in ("input_ports", "output_ports"):
        value[field] = {name: tuple(_vector(point) for point in points)
                        for name, points in value[field].items()}
    nets = []
    for net in value["nets"]:
        if set(net) != {field.name for field in fields(RoutedNet)}:
            raise ValueError("invalid routed-net fields")
        nets.append(RoutedNet(_integer(net["bit"]), _endpoint(net["source"]),
                              tuple(_endpoint(sink) for sink in net["sinks"]), _integer(net["track"])))
    if len({net.bit for net in nets}) != len(nets):
        raise ValueError("duplicate local net identity in retained geometry")
    value["nets"] = tuple(nets)
    for field in ("constant_high", "north_constant_high"):
        value[field] = tuple(_vector(point) for point in value[field])
    for field in ("cell_stride", "track_pitch", "routing_branch_y", "routing_trunk_y", "minimum_build_y"):
        _integer(value[field])
    return PhysicalNetlist(**value)


def _state(record):
    if set(record) != {"name", "properties"} or not isinstance(record["name"], str):
        raise ValueError("invalid retained block state")
    properties = []
    for pair in record["properties"]:
        if not isinstance(pair, list) or len(pair) != 2 or any(not isinstance(value, str) for value in pair):
            raise ValueError("block properties must retain ordered string pairs")
        properties.append(tuple(pair))
    return BlockState(record["name"], tuple(properties))


def _tile(record):
    expected = {"key", "physical", "escapes", "tracks", "track_base_z", "branch_y", "trunk_y",
                "overrides", "clock_delays", "balanced_positions"}
    if set(record) != expected:
        raise ValueError("manifest does not preserve every local tile field")
    physical = _physical(record["physical"])
    escapes = {}
    for encoded in record["escapes"]:
        escape = dict(encoded)
        point = _vector(escape.pop("point"))
        if point in escapes:
            raise ValueError("duplicate endpoint escape in retained geometry")
        if set(escape) != {field.name for field in fields(_Escape)}:
            raise ValueError("invalid retained escape fields")
        escape["endpoint"] = _vector(escape["endpoint"])
        if point != escape["endpoint"]:
            raise ValueError("escape key disagrees with its native endpoint")
        for field in ("escape_z", "branch_x", "branch_direction"):
            _integer(escape[field])
        for field in ("escape_x", "wall_lead_z"):
            if escape[field] is not None:
                _integer(escape[field])
        escapes[point] = _Escape(**escape)
    endpoints = {endpoint.pos for net in physical.nets for endpoint in (net.source, *net.sinks)}
    if set(escapes) != endpoints:
        raise ValueError("retained escapes differ from the exact routed endpoint inventory")
    tracks = {bit: _integer(track) for bit, track in _indices(record["tracks"]).items()}
    if set(tracks) != {net.bit for net in physical.nets}:
        raise ValueError("retained track inventory differs from the routed nets")
    overrides = {}
    for item in record["overrides"]:
        if set(item) != {"point", "state"}:
            raise ValueError("invalid retained route override")
        point = _vector(item["point"])
        if point in overrides:
            raise ValueError("duplicate override position in retained geometry")
        overrides[point] = _state(item["state"])
    balanced = {}
    for bit, points in _indices(record["balanced_positions"]).items():
        values = [_vector(point) for point in points]
        if len(set(values)) != len(values):
            raise ValueError("duplicate balanced clock position in retained geometry")
        balanced[bit] = set(values)
    delays = _indices(record["clock_delays"])
    if not set(balanced).issubset(tracks) or not set(delays).issubset(tracks):
        raise ValueError("retained clock data refers to an absent net")
    if not set().union(*balanced.values()).issubset(overrides):
        raise ValueError("balanced clock positions are missing their exact state overrides")
    return LocalTile(physical, escapes, tracks, _integer(record["track_base_z"]),
                     _integer(record["branch_y"]), _integer(record["trunk_y"]), overrides, delays, balanced)


def restore_manifest(document):
    """Restore every retained module without allocating or changing an escape."""
    if document.get("schema") != "shama-complete-subtree-geometry-v1":
        raise ValueError("unsupported complete geometry manifest schema")
    records = {}
    for record in document["clusters"]:
        if set(record) != {"cluster_id", "children", "own_module_keys", "bounds", "unique_blocks",
                           "packing", "terminals", "ports"}:
            raise ValueError("manifest does not preserve every cluster field")
        identity = _integer(record["cluster_id"])
        if identity in records:
            raise ValueError("duplicate retained cluster identity")
        records[identity] = record
    modules = {}
    for record in document["modules"]:
        key = _key(record["key"])
        if key in modules:
            raise ValueError("duplicate retained physical module")
        modules[key] = _tile(record)
    contacts = {name: dict(record, position=_vector(record["position"]))
                for name, record in document["shared_contacts"].items()}
    parents = {}
    for cluster_id, record in records.items():
        if len(set(record["children"])) != len(record["children"]):
            raise ValueError("duplicate child cluster in retained geometry")
        for child in record["children"]:
            _integer(child)
            if child not in records or child in parents:
                raise ValueError("missing child or multiply owned retained cluster")
            parents[child] = cluster_id
    root = _integer(document["root_cluster"])
    if root not in records or root in parents or set(records) - set(parents) != {root}:
        raise ValueError("retained cluster hierarchy does not have exactly its declared root")
    for name, contact in contacts.items():
        if set(contact) != {"position", "bit", "source_terminal", "sink_terminal", "child_cluster",
                            "parent_cluster", "child_access", "parent_access"}:
            raise ValueError("invalid retained shared contact fields")
        for field in ("bit", "child_cluster", "parent_cluster"):
            _integer(contact[field])
        if parents.get(contact["child_cluster"]) != contact["parent_cluster"]:
            raise ValueError(f"contact {name} does not join a direct parent/child boundary")
        if (contact["child_access"], contact["parent_access"]) != ("north", "south"):
            raise ValueError("retained boundary access faces changed")
    used_modules, completed, pending = set(), {}, set()

    def visit(cluster_id):
        if cluster_id in pending or cluster_id in completed:
            raise ValueError("retained hierarchy cycles or repeats a cluster")
        pending.add(cluster_id)
        record = records[cluster_id]
        children = tuple(visit(child) for child in record["children"])
        keys = [_key(key) for key in record["own_module_keys"]]
        if len(set(keys)) != len(keys) or set(keys) & used_modules or not set(keys).issubset(modules):
            raise ValueError("retained physical module ownership is not exact")
        used_modules.update(keys)
        descendants = {cluster_id}
        for child in children:
            descendants.update(completed[child.cluster_id][1])
        shared = {name: record for name, record in contacts.items() if record["parent_cluster"] in descendants}
        terminals = {name: _vector(point) for name, point in record["terminals"].items()}
        for contact in shared.values():
            for name in (contact["source_terminal"], contact["sink_terminal"]):
                if terminals.get(name) != contact["position"]:
                    raise ValueError("retained contact no longer matches its terminal position")
        count = _integer(record["unique_blocks"])
        if count < 1:
            raise ValueError("retained literal block count must be positive")
        placed = PlacedCluster(cluster_id, _box(record["bounds"]), children,
            {key: modules[key] for key in keys}, terminals,
            {entity: _vector(point) for entity, point in _indices(record["ports"]).items()},
            count, shared, record["packing"])
        completed[cluster_id] = placed, descendants
        pending.remove(cluster_id)
        return placed

    placed = visit(root)
    if set(completed) != set(records) or used_modules != set(modules):
        raise ValueError("retained manifest has unreachable clusters or unowned physical modules")
    if placed.shared_contacts != contacts:
        raise ValueError("retained root lost a shared contact")
    return placed


def read_manifest(path, expected_sha256):
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("retained manifest differs from its independently recorded digest")
    return json.loads(gzip.decompress(raw), object_pairs_hook=_mapping)


def validate_sources(document):
    provenance = document["provenance"]
    bindings = provenance.get("source_hashes")
    if not bindings or not set(REQUIRED_SOURCE_BINDINGS).issubset(bindings):
        raise ValueError("this manifest lacks source bindings required for a resumable union proof")
    for name, expected in bindings.items():
        path = (REPOSITORY / name).resolve()
        if not path.is_relative_to(REPOSITORY) or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"geometry source binding changed: {name}")
    effective = hashlib.sha256((DIRECTORY / "partition_placement.py").read_bytes()).hexdigest()
    if effective != provenance["effective_partition_placement_sha256"]:
        raise ValueError("retained geometry no longer binds to the isolated placement snapshot")


def _peak_rss():
    # The generic manifest reader/restorer is also used by Windows preparation
    # tests. Process measurement is optional and must not prevent that import.
    try:
        import resource
    except ImportError:
        return {"peak_rss_kib": None, "peak_rss_measurement": "unavailable: platform has no resource module"}
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        value = (value + 1023) // 1024
    return {"peak_rss_kib": value, "peak_rss_measurement": "resource.getrusage, normalized to KiB"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("use a fresh output path; earlier proof records are retained")
    started = time.monotonic()
    document = read_manifest(args.manifest, args.sha256)
    validate_sources(document)
    placed = restore_manifest(document)
    from streamed_union import literal_union
    union = literal_union(placed.iter_placements())
    if union["literal_unique_blocks"] != placed.unique_blocks or union["bounds"] != asdict(placed.bounds):
        raise ValueError("resumed exact union disagrees with retained block count or envelope")
    report = {
        "status": "retained-manifest-literal-union-passed",
        "scope": "Exact re-emission and union of all recorded modules; original routing guards, graph audit and runtime are separate evidence",
        "manifest_sha256": args.sha256,
        "replay_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "streamed_union_sha256": hashlib.sha256((DIRECTORY / "streamed_union.py").read_bytes()).hexdigest(),
        "original_provenance": document["provenance"],
        "cluster_id": placed.cluster_id,
        "modules": len(placed.all_modules()), "shared_contacts": len(placed.shared_contacts),
        "native_cells": sum(len(tile.physical.cell_types) for tile in placed.all_modules().values()),
        "literal_union": union, "seconds": time.monotonic() - started, **_peak_rss(),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as output:
        output.write(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
