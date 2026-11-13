"""Read-only loading arithmetic for a completed connected-cache literal union.

This does not launch Minecraft, create loading tickets, or certify a runtime.
The vanilla view predicate is transcribed from the SHA-1-verified official
Java 1.20.4 server artifact; its provenance is recorded beside this script.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from shamaos.generator import geometry_from_config, load_config
from shamaos.hardware.display import LampPanelSpec, panel_pixel
from shamaos.hardware.viewing import operator_camera
from shamaos.layout import default_origins


def digest(path: Path, algorithm: str = "sha256") -> str:
    value = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for piece in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(piece)
    return value.hexdigest()


def vanilla_view_contains(
    center_x: int, center_z: int, radius: int, x: int, z: int,
    include_outer: bool,
) -> bool:
    """Official 1.20.4 ChunkTrackingView.isWithinDistance, integer semantics."""
    dx = max(0, abs(x - center_x) - 1)
    dz = max(0, abs(z - center_z) - 1)
    far = max(0, max(dx, dz) - int(include_outer))
    near = min(dx, dz)
    return near * near + far * far < radius * radius


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connected-report", required=True, type=Path)
    parser.add_argument("--mchprs-source", required=True, type=Path)
    parser.add_argument("--official-artifacts", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report_bytes = args.connected_report.read_bytes()
    report = json.loads(report_bytes)
    sort_union, sqlite_union = report["independent_sort_union"], report["sqlite_union"]
    assert report["independent_union_agreement"] is True
    assert sqlite_union["status"] == "passed"
    for left, right in (
        ("literal_unique_blocks", "unique_blocks"),
        ("occupied_chunks", "occupied_chunks"), ("bounds", "bounds"),
        ("sorted_unique_record_sha256", "sorted_unique_record_sha256"),
    ):
        assert sort_union[left] == sqlite_union[right], (left, right)
    bounds = sort_union["bounds"]
    extent = {axis: bounds["max"][axis] - bounds["min"][axis] + 1 for axis in "xyz"}
    chunk_min = {axis: bounds["min"][axis] // 16 for axis in "xz"}
    chunk_max = {axis: bounds["max"][axis] // 16 for axis in "xz"}
    chunk_extent = {axis: chunk_max[axis] - chunk_min[axis] + 1 for axis in "xz"}
    chunks, regions = sqlite_union["occupied_chunks"], sqlite_union["occupied_regions"]
    minimum_plot_width_chunks = 1 << (max(chunk_extent.values()) - 1).bit_length()

    geometry = geometry_from_config(load_config(ROOT / "configs/default.toml"))
    origin = default_origins(geometry).display
    eye = operator_camera(geometry).eye
    player_chunk = [math.floor(eye[0] / 16), math.floor(eye[2] / 16)]
    spec = LampPanelSpec(320, 180, 3, 2, "upright")
    pixels = [panel_pixel(origin, spec, row, col)
              for row in range(spec.height) for col in range(spec.width)]
    lamp_chunks = Counter((point.x // 16, point.z // 16) for point in pixels)
    coverage = {}
    for radius in (32, 38, 39, 40):
        coverage[str(radius)] = {
            name: sum(count for (x, z), count in lamp_chunks.items()
                      if vanilla_view_contains(*player_chunk, radius, x, z, outer))
            for name, outer in (("inner", False), ("tracking", True))
        }
    minimum_radius = {
        name: next(radius for radius in range(1, 128)
                   if all(vanilla_view_contains(*player_chunk, radius, x, z, outer)
                          for x, z in lamp_chunks))
        for name, outer in (("inner", False), ("tracking", True))
    }

    pinned_engine = "f9e10d2572116bf5dcb9a36a305f28e40641a61a"
    actual_engine = subprocess.check_output(
        ["git", "-C", str(args.mchprs_source), "rev-parse", "HEAD"], text=True
    ).strip()
    assert actual_engine == pinned_engine
    upstream_files = (
        "crates/core/src/plot/mod.rs", "crates/core/src/plot/data.rs",
        "crates/core/src/config.rs", "crates/world/src/storage.rs",
        "crates/save_data/src/plot_data.rs", "README.md", "docs/Redpiler.md",
    )
    local_files = (
        "configs/default.toml", "src/shamaos/layout.py",
        "src/shamaos/hardware/display.py", "src/shamaos/hardware/viewing.py",
        "tests/mchprs_sparse_runtime/src/main.rs",
        "tests/mchprs_sparse_runtime/src/lazy_storage.rs",
        "tests/mchprs_sparse_runtime/src/pending.rs", "src/shamaos/mchprs_import.py",
    )
    official = json.loads((args.official_artifacts / "version.json").read_text())
    artifact_names = {"server": "server-bundler.jar", "server_mappings": "server-mappings.txt",
                      "client": "client.jar", "client_mappings": "client-mappings.txt"}
    artifacts = {}
    for key, name in artifact_names.items():
        expected = official["downloads"][key]
        path = args.official_artifacts / name
        actual_sha1 = digest(path, "sha1")
        assert actual_sha1 == expected["sha1"]
        assert path.stat().st_size == expected["size"]
        artifacts[key] = {"url": expected["url"], "sha1": actual_sha1,
                          "bytes": expected["size"], "sha256": digest(path)}

    output = {
        "schema": "shamaos-world-loading-audit-v1",
        "recorded_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "Static source inspection and arithmetic; no actual server/client loading proof",
        "actual_server_runtime_verified": False, "actual_client_view_verified": False,
        "complete_machine_runtime_verified": False,
        "repository_revision": subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip(),
        "connected_report_snapshot_sha256": hashlib.sha256(report_bytes).hexdigest(),
        "connected_report_status_at_read": report["status"],
        "facade_manifest_sha256": report["facade_manifest"]["sha256"],
        "literal_union": {
            key: sqlite_union[key] for key in (
                "unique_blocks", "occupied_chunks", "occupied_regions", "bounds",
                "sorted_unique_record_sha256", "minimum_anvil_bytes")
        },
        "geometry_calculations": {
            "block_extent": extent, "chunk_min": chunk_min, "chunk_max": chunk_max,
            "chunk_extent": chunk_extent,
            "enclosing_chunk_rectangle": chunk_extent["x"] * chunk_extent["z"],
            "minimum_chebyshev_radius_from_best_integer_chunk_center": max(
                value // 2 for value in chunk_extent.values()),
            "force_load_command_area_limit": 256,
            "occupied_chunk_command_count_lower_bound": math.ceil(chunks / 256),
            "rectangle_16_by_16_batch_count": math.ceil(chunk_extent["x"] / 16)
                * math.ceil(chunk_extent["z"] / 16),
            "minimum_anvil_bytes_recomputed": chunks * 4096 + regions * 8192,
            "native_import_section_records_at_24_per_chunk": chunks * 24,
        },
        "pinned_stock_mchprs_calculations": {
            "revision": pinned_engine, "plot_scale_default": 5,
            "plot_width_blocks_default": 512, "sections_per_chunk": 24,
            "default_index_array_bytes_per_section": 4096 * 4 // 8,
            "default_index_array_bytes_per_chunk": 24 * 4096 * 4 // 8,
            "occupied_chunks_only_index_arrays_bytes": chunks * 24 * 2048,
            "relocated_fixture_minimum_power_of_two_plot_width_chunks": minimum_plot_width_chunks,
            "relocated_fixture_plot_scale": minimum_plot_width_chunks.bit_length() - 1,
            "relocated_fixture_plot_width_blocks": minimum_plot_width_chunks * 16,
            "relocated_fixture_plot_chunks": minimum_plot_width_chunks ** 2,
            "relocated_fixture_plot_default_index_arrays_bytes": minimum_plot_width_chunks ** 2 * 24 * 2048,
            "resource_scope": "Current eager plot representation only; not vanilla or lazy runner RAM; excludes all other objects and peak copies",
        },
        "operator_view": {
            "eye": eye, "player_chunk": player_chunk, "lamp_pixels": len(pixels),
            "lamp_chunk_count": len(lamp_chunks),
            "lamp_chunk_min": [min(x for x, z in lamp_chunks), min(z for x, z in lamp_chunks)],
            "lamp_chunk_max": [max(x for x, z in lamp_chunks), max(z for x, z in lamp_chunks)],
            "covered_lamps_by_radius": coverage, "minimum_radius": minimum_radius,
            "scope": "Chunk delivery predicate only; no client frustum/fog/mesh/update/legibility observation",
        },
        "official_java_version": official["id"], "official_artifacts": artifacts,
        "official_version_metadata_sha1": digest(args.official_artifacts / "version.json", "sha1"),
        "local_source_sha256": {name: digest(ROOT / name) for name in local_files},
        "pinned_mchprs_source_sha256": {name: digest(args.mchprs_source / name) for name in upstream_files},
        "calculation_script_sha256": digest(Path(__file__)),
    }
    assert output["geometry_calculations"]["minimum_anvil_bytes_recomputed"] == sqlite_union["minimum_anvil_bytes"]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"output": str(args.output), "chunks": chunks,
                      "minimum_view_radius": minimum_radius,
                      "stock_mchprs_resized_plot_index_array_gib": minimum_plot_width_chunks ** 2 * 24 * 2048 / 2**30}))


if __name__ == "__main__":
    main()
