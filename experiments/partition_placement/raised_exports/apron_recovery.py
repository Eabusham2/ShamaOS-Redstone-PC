"""Resume one measured apron search while rerunning every geometry guard.

The retained experiment snapshot remains unchanged. A recovery hint only picks
the first apron for the same cluster and packing seen in source-bound evidence;
it never supplies routes, blocks, counts, or an acceptance result.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate recovery evidence key: {key}")
        result[key] = value
    return result


def load_recovery_hint(path: Path, expected_sha256: str, report: dict, clusters: dict) -> dict:
    """Require an exact retained measurement from the current graph and sources."""
    raw = path.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    if actual != expected_sha256:
        raise ValueError("recovery evidence digest does not match the supplied SHA-256")
    evidence = json.loads(raw, object_pairs_hook=_unique_object)
    if evidence.get("status") != "incomplete-after-parent-emission":
        raise ValueError("recovery requires a completed parent emission, not a partial route search")
    if evidence.get("full_graph_topology_sha256") != report["topology_sha256"]:
        raise ValueError("recovery graph topology binding changed")
    old_mapped = evidence.get("mapped_source", {})
    current_mapped = report["mapped_source"]
    for key in ("sha256", "container_sha256", "top", "lowering_source_sha256"):
        if not old_mapped.get(key) or old_mapped[key] != current_mapped.get(key):
            raise ValueError(f"recovery mapped source binding changed: {key}")
    if evidence.get("effective_candidate_sha256") != report["effective_partition_placement_sha256"]:
        raise ValueError("recovery placement snapshot binding changed")
    bindings = evidence.get("source_bindings", {})
    if set(bindings) != set(report["source_hashes"]):
        raise ValueError("recovery evidence lacks the exact source binding inventory")
    for name, digest in report["source_hashes"].items():
        if bindings[name].get("recorded_sha256") != digest or bindings[name].get("current_sha256") != digest:
            raise ValueError(f"recovery source binding changed: {name}")
    progress = evidence.get("computed_parent_progress", {})
    cluster_id = progress.get("cluster_id")
    if type(cluster_id) is not int or cluster_id not in clusters or not clusters[cluster_id]["children"]:
        raise ValueError("recovery hint must name a real internal cluster")
    selected = report["cluster_id"]
    ancestor = cluster_id
    while ancestor != selected and ancestor is not None:
        ancestor = clusters[ancestor]["parent"]
    if ancestor is None:
        raise ValueError("recovery hint is outside the selected complete subtree")
    packing = progress.get("packing", {})
    layout = packing.get("horizontal_layout")
    apron = packing.get("export_apron")
    if layout not in ("compact", "row"):
        raise ValueError("recovery hint has an unknown child packing")
    if type(apron) is not int or apron < 24:
        raise ValueError("recovery apron must be an integer of at least 24 blocks")
    if progress.get("own_exports_at_south_envelope") is not True:
        raise ValueError("recovery measurement did not expose every export")
    return {"cluster_id": cluster_id, "horizontal_layout": layout, "initial_apron": apron,
            "evidence_path": str(path), "evidence_sha256": actual,
            "scope": "Starting apron only; all route allocation, geometry guards and final union must run again"}


def install_recovery_hint(module, hint: dict):
    """Install a narrow proof-driver override, returning the original function."""
    original = module._connect_parent

    def connect(context, cluster_id, children, maximum_y, packing):
        if cluster_id != hint["cluster_id"] or packing["horizontal_layout"] != hint["horizontal_layout"]:
            return original(context, cluster_id, children, maximum_y, packing)
        apron = hint["initial_apron"]
        attempts = []
        for _ in range(8):
            # This is the original guarded route builder. A previous result is
            # never substituted for current emitted blocks or their checks.
            placed = module._connect_parent_attempt(context, cluster_id, children, maximum_y,
                                                     packing, export_apron=apron)
            points = [point for tile in placed.modules.values()
                      for ports in (tile.physical.input_ports, tile.physical.output_ports)
                      for pins in ports.values() for point in pins]
            if not points or all(point.z == placed.bounds.max.z for point in points):
                placed.packing["export_apron"] = apron
                placed.packing["export_apron_attempts"] = attempts
                placed.packing["recovered_initial_export_apron"] = hint["initial_apron"]
                return placed
            front_z = min(point.z for point in points)
            if any(point.z != front_z for point in points):
                raise module.PlacementError("parent export contacts no longer form one declared South face")
            attempts.append({"apron": apron, "export_z": front_z,
                             "actual_maximum_z": placed.bounds.max.z})
            apron += placed.bounds.max.z - front_z + 4
            del placed
        raise module.PlacementError(
            f"parent export apron did not expose every South contact after measured retries: {attempts}")

    module._connect_parent = connect
    return original
