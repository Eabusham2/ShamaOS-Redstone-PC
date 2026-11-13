"""Retain complete route decisions and graph bindings without literal block maps."""
from dataclasses import asdict
import gzip
import hashlib
import json
from pathlib import Path


def write_manifest(path, placed, *, provenance):
    from shamaos.hardware.partition_placement import PlacedCluster
    path = Path(path)
    clusters = []

    def visit(cluster):
        if type(cluster) is not PlacedCluster:
            raise ValueError("ordinary geometry manifest cannot retain an extended cluster; use its complete specialized writer")
        clusters.append({
            "cluster_id": cluster.cluster_id,
            "children": [child.cluster_id for child in cluster.children],
            "own_module_keys": [list(key) for key in sorted(cluster.modules)],
            "bounds": asdict(cluster.bounds), "unique_blocks": cluster.unique_blocks,
            "packing": cluster.packing,
            "terminals": {name: asdict(point) for name, point in sorted(cluster.terminals.items())},
            "ports": {str(entity): asdict(point) for entity, point in sorted(cluster.ports.items())},
        })
        for child in cluster.children:
            visit(child)

    visit(placed)
    modules = []
    for key, tile in sorted(placed.all_modules().items()):
        modules.append({
            "key": list(key), "physical": asdict(tile.physical),
            "escapes": [{"point": asdict(point), **asdict(escape)}
                        for point, escape in sorted(tile.escapes.items())],
            "tracks": {str(bit): track for bit, track in sorted(tile.tracks.items())},
            "track_base_z": tile.track_base_z,
            "branch_y": tile.branch_y, "trunk_y": tile.trunk_y,
            "overrides": [{"point": asdict(point), "state": asdict(state)}
                          for point, state in sorted(tile.overrides.items())],
            "clock_delays": tile.clock_delays,
            "balanced_positions": {str(bit): [asdict(point) for point in sorted(points)]
                                   for bit, points in sorted(tile._balanced_positions.items())},
        })
    manifest = {
        "schema": "shama-complete-subtree-geometry-v1",
        "scope": "Complete selected subtree graph bindings and actual route decisions; no literal block map or runtime claim",
        "root_cluster": placed.cluster_id, "provenance": provenance,
        "clusters": clusters, "modules": modules,
        "shared_contacts": {name: dict(record, position=asdict(record["position"]))
                            for name, record in sorted(placed.shared_contacts.items())},
    }
    raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    compressed = gzip.compress(raw, mtime=0)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(compressed)
    return {"path": str(path), "sha256": hashlib.sha256(compressed).hexdigest(),
            "uncompressed_sha256": hashlib.sha256(raw).hexdigest(),
            "compressed_bytes": len(compressed), "uncompressed_bytes": len(raw),
            "modules": len(modules), "clusters": len(clusters),
            "shared_contacts": len(placed.shared_contacts)}
