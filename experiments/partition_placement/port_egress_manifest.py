"""Retain and restore every graph and original-I/O facade wire explicitly."""
from __future__ import annotations

from dataclasses import asdict, replace
import gzip
import hashlib
import json
from pathlib import Path
import re

from experiments.partition_placement.port_egress import EgressCluster, audit_egress
from experiments.partition_placement.raised_exports import replay_manifest as original
from shamaos.hardware.partition_placement import audit_placement

SCHEMA = "shama-original-io-facade-geometry-v1"
REPOSITORY = Path(__file__).resolve().parents[2]
REQUIRED_SOURCE_BINDINGS = (*original.REQUIRED_SOURCE_BINDINGS,
    "src/shamaos/model.py", "experiments/memory_controller_runtime/clock_reserve.py",
    "experiments/partition_placement/port_egress.py",
    "experiments/partition_placement/packed_parent_union.py",
    "experiments/partition_placement/port_egress_manifest.py",
    "experiments/partition_placement/raised_exports/replay_manifest.py")
ORIGINAL_GEOMETRY_FIELDS = {"schema", "scope", "root_cluster", "provenance", "clusters", "modules", "shared_contacts"}


def _sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _digest(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("facade provenance requires exact lowercase SHA-256 digests")
    return value


def _plan_digest(plan):
    return hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _source_path(name, source_root):
    if not isinstance(name, str):
        raise ValueError("facade source binding path must be a canonical relative string")
    relative = Path(name)
    if relative.is_absolute() or relative.as_posix() != name or any(part in (".", "..") for part in relative.parts):
        raise ValueError("facade source binding path leaves its source root or is not canonical")
    root = Path(source_root).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError(f"facade source binding is missing or leaves its source root: {name}")
    return path


def _validate_source_hashes(provenance, source_root):
    bindings = provenance.get("source_hashes")
    if not isinstance(bindings, dict) or not set(REQUIRED_SOURCE_BINDINGS).issubset(bindings):
        raise ValueError("facade manifest lacks its complete required source bindings")
    for name, expected in bindings.items():
        if _sha(_source_path(name, source_root)) != _digest(expected):
            raise ValueError(f"facade source binding changed: {name}")


def _validate_graph_bindings(document, graph, plan):
    nested = document.get("original_geometry")
    if not isinstance(nested, dict) or set(nested) != ORIGINAL_GEOMETRY_FIELDS:
        raise ValueError("facade nested original geometry has unknown or missing fields")
    provenance = nested["provenance"]
    if not isinstance(provenance, dict):
        raise ValueError("facade manifest has invalid provenance")
    mapped = provenance.get("mapped_source")
    # A relocation may change the local filename, but cannot change any part
    # of the exact mapped/container/top/lowering identity supplied by Graph.
    identity = lambda value: {key: item for key, item in value.items() if key != "path"}
    if not isinstance(mapped, dict) or identity(mapped) != identity(graph.source):
        raise ValueError("facade manifest mapped graph binding differs from the supplied complete graph")
    if provenance.get("topology_sha256") != plan["summary"]["topology_sha256"]:
        raise ValueError("facade manifest topology binding differs from the supplied plan")
    if provenance.get("plan_canonical_sha256") != _plan_digest(plan):
        raise ValueError("facade manifest canonical plan binding differs from the supplied plan")
    return provenance


def validate_egress_sources(document, graph, plan, *, source_root=REPOSITORY):
    """Validate real source files separately from pure typed reconstruction."""
    provenance = _validate_graph_bindings(document, graph, plan)
    _validate_source_hashes(provenance, source_root)


def _bound_provenance(provenance, graph, plan):
    result = dict(provenance)
    required = {"mapped_source": dict(graph.source), "topology_sha256": plan["summary"]["topology_sha256"],
                "plan_canonical_sha256": _plan_digest(plan)}
    for name, expected in required.items():
        if name in result and result[name] != expected:
            raise ValueError(f"provided facade provenance conflicts with actual {name}")
        result[name] = expected
    if "source_hashes" not in result:
        result["source_hashes"] = {name: _sha(_source_path(name, REPOSITORY)) for name in REQUIRED_SOURCE_BINDINGS}
    _validate_source_hashes(result, REPOSITORY)
    return result


def _tile_record(key, tile):
    return {"key": list(key), "physical": asdict(tile.physical),
            "escapes": [{"point": asdict(point), **asdict(escape)}
                        for point, escape in sorted(tile.escapes.items())],
            "tracks": {str(bit): track for bit, track in sorted(tile.tracks.items())},
            "track_base_z": tile.track_base_z, "branch_y": tile.branch_y, "trunk_y": tile.trunk_y,
            "overrides": [{"point": asdict(point), "state": asdict(state)}
                          for point, state in sorted(tile.overrides.items())],
            "clock_delays": tile.clock_delays,
            "balanced_positions": {str(bit): [asdict(point) for point in sorted(points)]
                                   for bit, points in sorted(tile._balanced_positions.items())}}


def write_egress_manifest(path, graph, plan, placed, *, provenance):
    audit_placement(graph, plan, placed)
    audit_egress(graph, placed)
    provenance = _bound_provenance(provenance, graph, plan)
    graph_clusters, facade_clusters = [], []

    def visit(cluster):
        graph_clusters.append({"cluster_id": cluster.cluster_id,
            "children": [child.cluster_id for child in cluster.children],
            "own_module_keys": [list(key) for key in sorted(cluster.modules)],
            "bounds": asdict(cluster.bounds), "unique_blocks": cluster.unique_blocks,
            "packing": cluster.packing,
            "terminals": {name: asdict(point) for name, point in sorted(cluster.terminals.items())},
            "ports": {str(entity): asdict(point) for entity, point in sorted(cluster.ports.items())}})
        facade_clusters.append({"cluster_id": cluster.cluster_id,
            "own_module_keys": [list(key) for key in sorted(cluster.facade_modules)],
            "exposed_ports": {str(entity): asdict(point) for entity, point in sorted(cluster.exposed_ports.items())}})
        for child in cluster.children:
            visit(child)

    visit(placed)
    document = {"schema": SCHEMA,
        "scope": "Complete original graph plus every explicit ancestor I/O delivery wire; runtime is a separate gate",
        "original_geometry": {"schema": "shama-complete-subtree-geometry-v1",
            "scope": "Graph component of facade document; do not emit without its sibling facade modules",
            "root_cluster": placed.cluster_id, "provenance": provenance,
            "clusters": graph_clusters,
            "modules": [_tile_record(key, tile) for key, tile in sorted(placed.all_modules().items())],
            "shared_contacts": {name: dict(record, position=asdict(record["position"]))
                                for name, record in sorted(placed.shared_contacts.items())}},
        "facade_clusters": facade_clusters,
        "facade_modules": [_tile_record(key, tile) for key, tile in sorted(placed.all_facade_modules().items())],
        "facade_contacts": [{"key": list(key), **dict(record, position=asdict(record["position"]))}
                            for key, record in sorted(placed.facade_contacts.items())]}
    raw = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    compressed = gzip.compress(raw, mtime=0)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(compressed)
    return {"path": str(path), "sha256": hashlib.sha256(compressed).hexdigest(),
            "uncompressed_sha256": hashlib.sha256(raw).hexdigest(),
            "compressed_bytes": len(compressed), "graph_modules": len(document["original_geometry"]["modules"]),
            "facade_modules": len(document["facade_modules"]), "facade_handoffs": len(document["facade_contacts"])}


def _facade_key(value):
    if not isinstance(value, list) or len(value) != 3 or value[0] != "egress":
        raise ValueError("invalid original-I/O facade module key")
    result = value[0], original._integer(value[1]), original._integer(value[2])
    if min(result[1:]) < 0:
        raise ValueError("facade module identifiers cannot be negative")
    return result


def restore_egress_manifest(document, graph, plan):
    """Restore typed graph-bound data; source-file verification is separate.

    For a retained on-disk artifact use read_egress_manifest, which also checks
    its independently recorded digest and every real generator source binding.
    This pure function does not certify replayed route isolation or runtime.
    """
    if document.get("schema") != SCHEMA or set(document) != {
            "schema", "scope", "original_geometry", "facade_clusters", "facade_modules", "facade_contacts"}:
        raise ValueError("unsupported or incomplete original-I/O facade geometry document")
    _validate_graph_bindings(document, graph, plan)
    graph_root = original.restore_manifest(document["original_geometry"])
    graph_clusters = {}

    def inventory(cluster):
        graph_clusters[cluster.cluster_id] = cluster
        for child in cluster.children:
            inventory(child)

    inventory(graph_root)
    records = {}
    for record in document["facade_clusters"]:
        if set(record) != {"cluster_id", "own_module_keys", "exposed_ports"}:
            raise ValueError("incomplete facade cluster fields")
        identity = original._integer(record["cluster_id"])
        if identity in records:
            raise ValueError("duplicate facade cluster")
        records[identity] = record
    if set(records) != set(graph_clusters):
        raise ValueError("facade cluster inventory differs from the original graph")
    modules = {}
    for record in document["facade_modules"]:
        key = _facade_key(record["key"])
        if key in modules:
            raise ValueError("duplicate facade module")
        modules[key] = original._tile(record)
    contacts = {}
    for record in document["facade_contacts"]:
        if set(record) != {"key", "position", "entity", "child_cluster", "parent_cluster", "direction", "child_access", "parent_access"}:
            raise ValueError("incomplete original-I/O facade handoff")
        key = record["key"]
        if not isinstance(key, list) or len(key) != 2:
            raise ValueError("invalid facade handoff identity")
        key = tuple(original._integer(value) for value in key)
        if key in contacts:
            raise ValueError("duplicate facade handoff")
        value = {name: field for name, field in record.items() if name != "key"}
        value["position"] = original._vector(value["position"])
        for name in ("entity", "child_cluster", "parent_cluster"):
            original._integer(value[name])
        if key != (value["parent_cluster"], value["entity"]):
            raise ValueError("facade handoff key changed original entity or parent")
        contacts[key] = value
    used_modules = set()

    def restore(cluster):
        record = records[cluster.cluster_id]
        children = tuple(restore(child) for child in cluster.children)
        keys = [_facade_key(key) for key in record["own_module_keys"]]
        if len(set(keys)) != len(keys) or set(keys) & used_modules or not set(keys).issubset(modules):
            raise ValueError("facade module ownership is not exact")
        if any(key[1] != cluster.cluster_id for key in keys):
            raise ValueError("facade module belongs to the wrong ancestor")
        used_modules.update(keys)
        own_contacts = {key: value for key, value in contacts.items() if key[0] == cluster.cluster_id}
        all_contacts = {key: value for child in children for key, value in child.facade_contacts.items()}
        all_contacts.update(own_contacts)
        return EgressCluster(**dict(vars(cluster), children=children),
            facade_modules={key: modules[key] for key in keys},
            exposed_ports={entity: original._vector(point)
                           for entity, point in original._indices(record["exposed_ports"]).items()},
            facade_contacts=all_contacts)

    placed = restore(graph_root)
    if used_modules != set(modules) or placed.facade_contacts != contacts:
        raise ValueError("retained facade contains unreachable modules or handoffs")
    audit_placement(graph, plan, placed)
    audit_egress(graph, placed)
    return placed


def read_egress_manifest(path, expected_sha256, graph, plan, *, source_root=REPOSITORY):
    document = original.read_manifest(path, expected_sha256)
    validate_egress_sources(document, graph, plan, source_root=source_root)
    return restore_egress_manifest(document, graph, plan)
