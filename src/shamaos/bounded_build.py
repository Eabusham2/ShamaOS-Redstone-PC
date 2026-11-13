"""Save one complete bounded mapped partition as a development Minecraft world.

This is an operational bridge from the complete-graph planner/placer to native
Anvil output. It does not select a new full-system backend or certify runtime.
The complete mapped top is always retained: a selected subtree, a summary-only
plan, or user-supplied acceptance labels cannot be substituted for it.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import gzip
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import struct
import time
from typing import Iterator

from .hardware import external_router, local_router, logic, memory, routing
from .hardware import partition_placement, partition_planner
from .hardware.partition_planner import Graph, audit_plan, build_plan
from .hardware.partition_placement import PlacedCluster, audit_placement, balance_partition_clocks
from .model import BlockState, Placement, Vec3
from . import model, release_io, synthesis, worldio
from .worldio import NativeWorldWriter, WorldWriteError, canonical_block_state, validate_native_world, validate_position


class BoundedBuildError(ValueError):
    """The requested complete partition could not be verified or emitted."""


_CHUNK_BIAS = 1 << 21
_RECORD = struct.Struct(">QI")
_SOURCE_MODULES = (partition_planner, partition_placement, local_router,
                   external_router, logic, memory, routing, model, release_io, synthesis, worldio)


def _json_write(path: Path, value: object) -> None:
    temporary = path.with_name(path.name + ".partial")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _file_sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _source_hashes() -> dict[str, str]:
    paths = [Path(__file__), *(Path(module.__file__) for module in _SOURCE_MODULES)]
    root = Path(__file__).resolve().parent
    return {str(path.resolve().relative_to(root)): _file_sha(path) for path in paths}


def _check_mapping(path: Path, expected: str) -> str:
    if not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None:
        raise BoundedBuildError("An exact lowercase SHA-256 of the uncompressed mapped input is required.")
    with path.open("rb") as source:
        compressed = source.read(2) == b"\x1f\x8b"
    opener = gzip.open if compressed else open
    digest = hashlib.sha256()
    with opener(path, "rb") as source:
        while block := source.read(1 << 20):
            digest.update(block)
    if digest.hexdigest() != expected:
        raise BoundedBuildError("The mapped input differs from its expected SHA-256.")
    return _file_sha(path)


def _coordinate_key(point: Vec3) -> int:
    if not all(type(value) is int for value in (point.x, point.y, point.z)):
        raise BoundedBuildError("Every emitted coordinate must be an integer.")
    validate_position(point)
    return (((point.x // 16 + _CHUNK_BIAS) << 39)
            | ((point.z // 16 + _CHUNK_BIAS) << 17)
            | ((point.x & 15) << 13) | ((point.z & 15) << 9) | (point.y + 64))


def _coordinate(key: int) -> Vec3:
    return Vec3(((key >> 39) - _CHUNK_BIAS) * 16 + ((key >> 13) & 15),
                (key & 511) - 64,
                (((key >> 17) & ((1 << 22) - 1)) - _CHUNK_BIAS) * 16 + ((key >> 9) & 15))


class _LiteralUnion:
    """Disk-backed exact union; never retain all literal positions in RAM."""

    def __init__(self, path: Path):
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA cache_size=-8192")
        self.connection.execute("CREATE TABLE blocks (position INTEGER PRIMARY KEY, state INTEGER NOT NULL) WITHOUT ROWID")
        self.connection.execute("""CREATE TRIGGER reject_conflict BEFORE UPDATE ON blocks
            WHEN old.state != new.state BEGIN SELECT RAISE(ABORT, 'conflicting emitted block states'); END""")
        self.palette: list[BlockState] = []
        self.indices: dict[BlockState, int] = {}
        self.emitted = 0

    def close(self) -> None:
        self.connection.close()

    def collect(self, placements: Iterator[Placement], *, surface_y: int, maximum_y: int,
                bounds, expected_unique: int) -> dict:
        batch = []
        low = high = None

        def flush() -> None:
            if not batch:
                return
            try:
                self.connection.executemany(
                    "INSERT INTO blocks VALUES (?,?) ON CONFLICT(position) DO UPDATE SET state=excluded.state", batch)
            except sqlite3.IntegrityError as error:
                raise BoundedBuildError("The complete emitted partition contains conflicting block states.") from error
            self.connection.commit()
            batch.clear()

        for placement in placements:
            point = placement.pos
            key = _coordinate_key(point)
            if not surface_y < point.y <= maximum_y:
                raise BoundedBuildError(f"Emitted block leaves the protected above-grass envelope: {point}.")
            if not (bounds.min.x <= point.x <= bounds.max.x and bounds.min.y <= point.y <= bounds.max.y
                    and bounds.min.z <= point.z <= bounds.max.z):
                raise BoundedBuildError(f"Emitted block lies outside the audited partition bounds: {point}.")
            state = canonical_block_state(placement.block)
            index = self.indices.get(state)
            if index is None:
                index = len(self.palette)
                self.indices[state] = index
                self.palette.append(state)
            batch.append((key, index))
            self.emitted += 1
            xyz = (point.x, point.y, point.z)
            low = xyz if low is None else tuple(min(a, b) for a, b in zip(low, xyz))
            high = xyz if high is None else tuple(max(a, b) for a, b in zip(high, xyz))
            if len(batch) == 8192:
                flush()
        flush()
        unique = self.connection.execute("SELECT COUNT(*) FROM blocks").fetchone()[0]
        if not unique or unique != expected_unique:
            raise BoundedBuildError(f"Literal union has {unique} blocks; audited placement claims {expected_unique}.")
        actual_bounds = {"min": dict(zip("xyz", low)), "max": dict(zip("xyz", high))}
        if actual_bounds != asdict(bounds):
            raise BoundedBuildError("Literal union bounds differ from the audited complete partition.")
        digest = hashlib.sha256()
        chunks = 0
        previous_chunk = None
        for key, index in self.connection.execute("SELECT position,state FROM blocks ORDER BY position"):
            digest.update(_RECORD.pack(key, index))
            if key >> 17 != previous_chunk:
                chunks += 1
                previous_chunk = key >> 17
        # Every saved chunk needs at least one Anvil sector. This conservative
        # lower bound intentionally does not claim to predict compression.
        regions = self.connection.execute("""SELECT COUNT(*) FROM (
            SELECT DISTINCT (((position >> 39) - ?) >> 5),
                (((position >> 17 & 4194303) - ?) >> 5) FROM blocks)""",
            (_CHUNK_BIAS, _CHUNK_BIAS)).fetchone()[0]
        return {"status": "passed", "method": "disk-backed-exact-literal-union",
                "emitted_placements": self.emitted, "unique_blocks": unique,
                "identical_duplicate_placements": self.emitted - unique,
                "occupied_chunks": chunks, "occupied_regions": regions,
                "bounds": actual_bounds, "sorted_unique_record_sha256": digest.hexdigest(),
                "record_encoding": "big-endian u64 chunk-X/chunk-Z/local-X/local-Z/Y key, u32 palette index",
                "palette": [asdict(state) for state in self.palette],
                "minimum_anvil_bytes": chunks * 4096 + regions * 8192,
                "scope": "Exact partition geometry; no timing, runtime or whole-machine claim"}

    def placements(self) -> Iterator[Placement]:
        for key, index in self.connection.execute("SELECT position,state FROM blocks ORDER BY position"):
            yield Placement(_coordinate(key), self.palette[index], "complete-bounded-partition")


def _ports(graph: Graph, placed: PlacedCluster) -> dict:
    result: dict[str, dict] = {}
    for entity in graph.entities:
        if entity["kind"] != "port":
            continue
        port = result.setdefault(entity["port_name"], {"direction": entity["direction"], "pins": []})
        if entity["port_index"] != len(port["pins"]) or port["direction"] != entity["direction"]:
            raise BoundedBuildError("Original partition port order or direction changed.")
        port["pins"].append(asdict(placed.ports[entity["id"]]))
    return {"ports": result, "scope": "All original scalar terminals; external route accessibility remains unverified",
            "externally_routable_verified": False}


def _report_start(destination: Path, mapped: Path, top: str, expected: str) -> dict:
    destination.mkdir(parents=True, exist_ok=False)
    return {"format": "shamaos-bounded-partition-build-v1", "scope": "complete-mapped-partition-development",
            "status": "running", "stage": "read-mapped-input", "mapped_path": str(mapped.resolve()),
            "top": top, "expected_mapped_sha256": expected,
            "complete_machine_runtime_verified": False, "partition_runtime_verified": False,
            "complete_machine_release_eligible": False,
            "electrical_route_isolation_verified": False, "complete_route_validation_verified": False,
            "route_validation_scope": "Graph/contact conservation and exact literal union/readback only; electrical adjacency, climbing headroom and complete route continuity require separate validation.",
            "remaining_gates": ["complete emitted electrical route validation", "external partition connections", "full sequential timing",
                                "native partition runtime", "complete machine generation and runtime",
                                "client controls/view and loading", "release acceptance"]}


def _read_graph(mapped: Path, top: str, expected: str, report: dict) -> Graph:
    container_sha = _check_mapping(mapped, expected)
    graph = Graph.read(mapped, top)
    if graph.source["sha256"] != expected or graph.source["container_sha256"] != container_sha:
        raise BoundedBuildError("Mapped input changed while reading the complete graph.")
    report["mapped_source"] = graph.source
    report["source_hashes"] = _source_hashes()
    report["hdl_equivalence_scope"] = "Bound to this exact mapped input; original RTL equivalence is separate synthesis evidence."
    return graph


def _check_frozen(mapped: Path, expected: str, report: dict) -> None:
    if _check_mapping(mapped, expected) != report["mapped_source"]["container_sha256"]:
        raise BoundedBuildError("Mapped input container changed during the build.")
    if _source_hashes() != report["source_hashes"]:
        raise BoundedBuildError("A generator source changed during the build; this result is not source-bound.")


def _height_contract(surface_y: int, maximum_y: int) -> None:
    if (type(surface_y) is not int or type(maximum_y) is not int
            or not -63 <= surface_y <= 316 or not surface_y < maximum_y <= 319):
        raise BoundedBuildError("Invalid grass/build-height envelope or no room for a player spawn.")


def _emit(destination: Path, graph: Graph, plan: dict, placed: PlacedCluster, report: dict,
          *, surface_y: int, maximum_y: int, mapped: Path, expected: str) -> dict:
    _height_contract(surface_y, maximum_y)
    report["stage"] = "audit-complete-placement"
    _json_write(destination / "build-report.json", report)
    if placed.cluster_id != plan["root_cluster"]:
        raise BoundedBuildError("A selected subtree cannot stand in for the complete mapped top.")
    report["plan_audit"] = audit_plan(graph, plan)
    report["placement_audit"] = audit_placement(graph, plan, placed)
    report["clock_route_matching"] = balance_partition_clocks(plan, placed)
    # Matching changes repeater delays, so the emitted states and readback below
    # always include the actual matched layout. It does not establish safe rate.
    report["clock_scope"] = "Matched structural repeater routes; pulse, skew, setup/hold and intended transfers require native runtime."
    ports = _ports(graph, placed)
    _json_write(destination / "ports.json", ports)
    _json_write(destination / "plan.json", plan)
    report["plan_sha256"] = _file_sha(destination / "plan.json")
    report["ports_sha256"] = _file_sha(destination / "ports.json")
    report["stage"] = "literal-union"
    _json_write(destination / "build-report.json", report)
    union = _LiteralUnion(destination / "literal-union.sqlite3")
    try:
        literal = union.collect(placed.iter_placements(), surface_y=surface_y, maximum_y=maximum_y,
                                bounds=placed.bounds, expected_unique=placed.unique_blocks)
        report["literal_union"] = literal
        available = shutil.disk_usage(destination).free
        report["available_disk_bytes_before_world"] = available
        if literal["minimum_anvil_bytes"] >= available:
            raise BoundedBuildError("Insufficient disk for even the exact partition's Anvil sector lower bound.")
        _check_frozen(mapped, expected, report)
        report["stage"] = "write-native-world"
        _json_write(destination / "build-report.json", report)
        world = destination / "world"
        with NativeWorldWriter(world, name=f"ShamaOS development partition: {report['top']}",
                               spawn=Vec3(-16, surface_y + 2, -16), spawn_platform=False,
                               surface_y=surface_y, max_cached_chunks=2) as writer:
            writer.set_blocks(union.placements())
        report["stage"] = "independent-native-readback"
        _json_write(destination / "build-report.json", report)
        readback = validate_native_world(world)
        if (readback["assigned_blocks"] != literal["unique_blocks"]
                or readback["chunks"] != literal["occupied_chunks"]
                or readback["regions"] != literal["occupied_regions"]):
            raise BoundedBuildError("Saved native world differs from the exact complete partition union.")
        _check_frozen(mapped, expected, report)
        report["world_validation"] = readback
        inventory = release_io.file_inventory(world)
        _json_write(destination / "world-inventory.json", inventory)
        report["world_inventory_sha256"] = release_io.inventory_digest(inventory)
        report["status"] = "generated-and-readback-verified"
        report["stage"] = "complete-development-partition"
        # Return the same data model as the persisted report (JSON string keys
        # and lists), including route dictionaries and BlockState properties.
        normalized = json.loads(json.dumps(report))
        report.clear()
        report.update(normalized)
        return report
    finally:
        union.close()


def build_bounded_partition(mapped: str | Path, top: str, destination: str | Path, *,
                            expected_mapped_sha256: str, surface_y: int = -61, maximum_y: int = 285,
                            cell_budget: int = 64, boundary_budget: int = 128,
                            domain_budget: int = 256, arity: int = 4) -> dict:
    """Plan, place, audit and save every cell of the exact supplied mapped top."""
    mapped, destination = Path(mapped), Path(destination)
    report = _report_start(destination, mapped, top, expected_mapped_sha256)
    started = time.monotonic()
    try:
        _json_write(destination / "build-report.json", report)
        _height_contract(surface_y, maximum_y)
        graph = _read_graph(mapped, top, expected_mapped_sha256, report)
        plan = build_plan(graph, cell_budget=cell_budget, boundary_budget=boundary_budget,
                          domain_budget=domain_budget, arity=arity, retain_signals=True)
        report["plan_audit"] = audit_plan(graph, plan)
        report["plan_summary"] = plan["summary"]
        _json_write(destination / "plan.json", plan)
        report["stage"] = "place-complete-mapped-top"
        _json_write(destination / "build-report.json", report)
        placed = partition_placement.place_partition(graph, plan, surface_y=surface_y,
                                                      maximum_y=maximum_y, balance_clocks=True)
        return _emit(destination, graph, plan, placed, report, surface_y=surface_y, maximum_y=maximum_y,
                     mapped=mapped, expected=expected_mapped_sha256)
    except Exception as error:
        report.update(status="failed", error_type=type(error).__name__, error=str(error))
        raise
    finally:
        report["seconds"] = time.monotonic() - started
        _json_write(destination / "build-report.json", report)


def emit_bounded_partition(mapped: str | Path, top: str, plan: dict, placed: PlacedCluster,
                           destination: str | Path, *, expected_mapped_sha256: str,
                           surface_y: int = -61, maximum_y: int = 285) -> dict:
    """Save an already placed full top after reloading and auditing its source.

    Callers may restore a source-bound geometry manifest first. This function
    trusts neither that manifest's pass labels nor its inventory: the exact
    mapped graph, full plan and restored contact identities are checked again.
    It does not reconstruct every original allocator isolation/headroom check.
    The report therefore never certifies complete electrical route validation.
    """
    mapped, destination = Path(mapped), Path(destination)
    report = _report_start(destination, mapped, top, expected_mapped_sha256)
    started = time.monotonic()
    try:
        _json_write(destination / "build-report.json", report)
        graph = _read_graph(mapped, top, expected_mapped_sha256, report)
        report["plan_summary"] = plan["summary"]
        return _emit(destination, graph, plan, placed, report, surface_y=surface_y, maximum_y=maximum_y,
                     mapped=mapped, expected=expected_mapped_sha256)
    except Exception as error:
        report.update(status="failed", error_type=type(error).__name__, error=str(error))
        raise
    finally:
        report["seconds"] = time.monotonic() - started
        _json_write(destination / "build-report.json", report)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mapped", type=Path, required=True, help="complete mapped Yosys JSON or gzip")
    parser.add_argument("--top", required=True)
    parser.add_argument("--sha256", required=True, help="expected SHA-256 of the uncompressed mapped input")
    parser.add_argument("--output", type=Path, required=True, help="new development-output directory")
    parser.add_argument("--surface-y", type=int, default=-61)
    parser.add_argument("--maximum-y", type=int, default=285, help="reserve upper world space for later external routes")
    parser.add_argument("--cell-budget", type=int, default=64)
    parser.add_argument("--boundary-budget", type=int, default=128)
    parser.add_argument("--domain-budget", type=int, default=256)
    parser.add_argument("--arity", type=int, default=4)
    args = parser.parse_args(argv)
    try:
        report = build_bounded_partition(args.mapped, args.top, args.output,
                    expected_mapped_sha256=args.sha256, surface_y=args.surface_y, maximum_y=args.maximum_y,
                    cell_budget=args.cell_budget, boundary_budget=args.boundary_budget,
                    domain_budget=args.domain_budget, arity=args.arity)
    except (OSError, ValueError, KeyError, sqlite3.Error, WorldWriteError) as error:
        print(f"Bounded partition build failed: {error}")
        return 1
    print(json.dumps({"status": report["status"], "report": str(args.output / "build-report.json"),
                      "native_cells": report["placement_audit"]["native_cells"],
                      "unique_blocks": report["literal_union"]["unique_blocks"],
                      "occupied_chunks": report["literal_union"]["occupied_chunks"],
                      "complete_machine_runtime_verified": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
