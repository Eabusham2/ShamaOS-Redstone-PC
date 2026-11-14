"""Save the balanced full cache facade, all installed banks and exact interconnect.

The independent sort/merge union establishes the compound inventory. A second
SQLite union must reproduce its exact states, coordinates and bounds before
native writing/readback. Graph/facade conservation and memory capacity remain
explicit separate checks; no synthetic partial PlacedCluster is constructed.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import time
import zipfile

try:
    import resource
except ImportError:  # Small preparation/binding guards also run on Windows.
    resource = None

from experiments.memory_controller_runtime import fixture, geometry, interconnect, scenarios
from experiments.partition_placement.port_egress import audit_egress
from experiments.partition_placement.port_egress_manifest import read_egress_manifest, validate_egress_sources
from experiments.partition_placement.raised_exports.streamed_union import literal_union
from shamaos import bounded_build, release_io
from shamaos.hardware.partition_placement import audit_placement, balance_partition_clocks
from shamaos.hardware.partition_planner import audit_plan
from shamaos.model import Box, Vec3
from shamaos.worldio import NativeWorldReader, NativeWorldWriter, validate_native_world


ROOT = Path(__file__).resolve().parents[2]
PASSED_FACADE = "complete-original-io-facade-geometry-passed-runtime-pending"


def _sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _write(path, value):
    temporary = path.with_name(path.name + ".partial")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def _source_hashes(facade_report):
    names = set(fixture.SOURCE_PATHS) | set(facade_report["source_hashes"])
    names.update(("experiments/memory_controller_runtime/interconnect.py",
                  "experiments/memory_controller_runtime/current_fixture.py",
                  "experiments/partition_planner/audit_cache_frames.py",
                  "experiments/memory_controller_runtime/save_connected.py",
                  "experiments/partition_placement/port_egress_interfaces.py",
                  "experiments/partition_placement/raised_exports/streamed_union.py",
                  "src/shamaos/bounded_build.py", "src/shamaos/worldio.py", "src/shamaos/release_io.py"))
    return {name: _sha(ROOT / name) for name in sorted(names)}


def _accepted_facade(facade_report_path, plan_path, graph):
    """Bind the accepted report to its balanced complete saved geometry."""
    facade_report = json.loads(facade_report_path.read_text())
    if facade_report.get("status") != PASSED_FACADE or "balanced_manifest" not in facade_report:
        raise ValueError("connected native emission requires an accepted fully clock-balanced facade")
    if _sha(plan_path) != facade_report["plan_sha256"]:
        raise ValueError("connected plan is not the exact accepted complete facade plan")
    identity = lambda value: {key: item for key, item in value.items() if key != "path"}
    if identity(facade_report["mapped_source"]) != identity(graph.source):
        raise ValueError("accepted facade report describes a different mapped graph")
    record = facade_report["balanced_manifest"]
    manifest = Path(record["path"])
    if not manifest.is_absolute():
        manifest = ROOT / manifest
    if _sha(manifest) != record["sha256"]:
        raise ValueError("accepted balanced facade manifest digest changed")
    with gzip.open(manifest, "rt") as stream:
        document = json.load(stream)
    validate_egress_sources(document, graph, json.loads(plan_path.read_text()))
    provenance = document["original_geometry"]["provenance"]
    if provenance.get("clock_balance_verified") is not True:
        raise ValueError("accepted facade manifest has no completed structural clock matching")
    if (provenance["source_hashes"] != facade_report["source_hashes"]
            or provenance["plan_sha256"] != facade_report["plan_sha256"]):
        raise ValueError("accepted facade report and its saved geometry have different source or plan bindings")
    return facade_report, record, manifest


def _retain_inputs(destination, report, facade_report_path, manifest, plan_path, *, mapped_path=None, current_prepared=None):
    """Keep the exact reviewed sources and small immutable build inputs."""
    bundle = destination / "connected-source-inputs.zip"
    paths = {name: ROOT / name for name in report["source_hashes"]}
    paths.update({
        "inputs/facade-report.json": facade_report_path,
        "inputs/facade-balanced.json.gz": manifest,
        "inputs/plan.json": plan_path,
        ("inputs/mapped-controller.json" if mapped_path and mapped_path.suffix != ".gz"
         else "inputs/mapped-controller.json.gz"): mapped_path or fixture.HERE / "evidence/cache-controller-c331.json.gz",
        "inputs/initial.bin": destination / "initial.bin",
    })
    if current_prepared is not None:
        from experiments.memory_controller_runtime.current_fixture import INPUTS
        for name in ("prepared.json", "input-inventory.json", "mapped.json", "mapped-raw.json", "plan.json", "initial.bin"):
            paths["inputs/current/" + name] = current_prepared / name
        paths.update({"inputs/current/source/" + name: current_prepared / "source" / name for name in INPUTS})
    with zipfile.ZipFile(bundle, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, path in sorted(paths.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())
    return {"path": str(bundle.resolve()), "sha256": _sha(bundle),
            "bytes": bundle.stat().st_size, "files": len(paths),
            "scope": "Complete source binding inventory and exact mapped, facade, plan and full-capacity initial inputs; native world is separate"}


def _validate_native_driver_pads(reader, inputs):
    """Each future driver directly touches only its intended saved terminal."""
    offsets = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))
    pads = set()
    for name, coords in inputs.items():
        terminal = Vec3(*coords)
        pad = terminal.offset(dz=1)
        if pad in pads or reader.get_block(terminal).name != "minecraft:redstone_wire":
            raise ValueError(f"external host terminal or driver pad identity is invalid: {name}")
        pads.add(pad)
        if reader.get_block(pad).name != "minecraft:air":
            raise ValueError(f"external host driver pad is not saved air: {name}")
        for dx, dy, dz in offsets:
            neighbor = pad.offset(dx, dy, dz)
            if neighbor != terminal and reader.get_block(neighbor).name != "minecraft:air":
                raise ValueError(f"external host driver pad touches an unrelated saved block: {name} at {neighbor}")
    return {"host_pads": len(pads), "saved_pads_are_air": True,
            "each_pad_directly_touches_only_its_intended_terminal": True,
            "scope": "All six block faces inspected in the saved world; complete route electrical behavior remains a native-runtime gate"}


def _native_maps(graph, placed, connection, fabric, *, capacity_bytes=8192):
    _, outputs = geometry.interface_maps(graph, placed, connection.bank_origin, fabric, capacity_bytes=capacity_bytes)
    # Public adapter observations belong at the true exported contacts. Keep
    # the original inner pins as separately named diagnostic observables.
    for (name, bit), point in connection.controller_ports.items():
        public = scenarios.port(name, bit)
        outputs[f"original-{public}"] = outputs[public]
        outputs[public] = [point.x, point.y, point.z]
    inputs = {scenarios.host(name, bit): [point.x, point.y, point.z]
              for (name, bit), point in connection.controller_ports.items()
              if name in scenarios.HOST_WIDTHS and bit < scenarios.HOST_WIDTHS[name]}
    if len(inputs) != 72 or sum(name.startswith("storage:") for name in outputs) != capacity_bytes * 8:
        raise ValueError("native interface map lost a host input or stored bit")
    return inputs, outputs


def save_connected(facade_report_path: Path, plan_path: Path, destination: Path, *, current_prepared=None) -> dict:
    if destination.exists():
        raise FileExistsError(destination)
    if resource is None:
        raise RuntimeError("large connected-cache emission requires POSIX process accounting and bounds")
    prepared_record = None
    if current_prepared is None:
        graph, _, fabric, production_origin = fixture.graph_and_fabric()
        capacity_bytes = 8192
    else:
        from experiments.memory_controller_runtime.current_fixture import load_prepared
        prepared_record, graph, _, fabric, production_origin = load_prepared(current_prepared)
        capacity_bytes = prepared_record["capacity_bytes"]
        if _sha(plan_path) != prepared_record["plan_sha256"]:
            raise ValueError("current cache placement plan differs from its exact prepared graph")
    mapped_path = Path(graph.source["path"])
    facade_report, record, manifest = _accepted_facade(facade_report_path, plan_path, graph)
    plan = json.loads(plan_path.read_text())
    placed = read_egress_manifest(manifest, record["sha256"], graph, plan)
    destination.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    report = {"schema": "complete-connected-cache-native-world-v1", "status": "running",
              "stage": "audit-complete-controller-and-memory", "mapped_source": graph.source,
              "facade_report_sha256": _sha(facade_report_path), "facade_manifest": record,
              "plan_sha256": _sha(plan_path), "source_hashes": _source_hashes(facade_report),
              "controller_clock_native_verified": False, "connected_cache_runtime_verified": False,
              "native_persistence_verified": False, "complete_machine_runtime_verified": False,
              "complete_machine_release_eligible": False,
              "electrical_route_isolation_verified": False, "complete_route_validation_verified": False,
              "execution_limits": {"address_space_bytes": resource.getrlimit(resource.RLIMIT_AS)[0],
                  "cpu_seconds": resource.getrlimit(resource.RLIMIT_CPU)[0],
                  "wall_alarm_remaining_seconds_at_start": signal.getitimer(signal.ITIMER_REAL)[0],
                  "pid": os.getpid()},
              "scope": "Complete mapped cache facade plus all installed physical banks, decoder and all external routes; native runtime remains a separate gate"}
    if current_prepared is not None:
        report["current_preparation"] = {"path": str((current_prepared / "prepared.json").resolve()),
                                         "sha256": _sha(current_prepared / "prepared.json")}

    def save():
        report["seconds"] = time.monotonic() - started
        report["peak_rss_kib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        _write(destination / "connected-build-report.json", report)
        print(json.dumps({key: report[key] for key in ("stage", "status", "seconds", "peak_rss_kib")}), flush=True)

    def check_frozen():
        if _source_hashes(facade_report) != report["source_hashes"]:
            raise ValueError("connected generator sources changed during emission")
        if _sha(facade_report_path) != report["facade_report_sha256"] or _sha(manifest) != record["sha256"]:
            raise ValueError("accepted facade inputs changed during emission")
        if _sha(plan_path) != report["plan_sha256"]:
            raise ValueError("complete facade plan changed during emission")
        if _sha(mapped_path) != graph.source["container_sha256"]:
            raise ValueError("complete mapped controller container changed during emission")
        if current_prepared is not None:
            if _sha(current_prepared / "prepared.json") != report["current_preparation"]["sha256"]:
                raise ValueError("current cache preparation changed during emission")
            load_prepared(current_prepared)

    def observed(placements, phase):
        count = 0
        for placement in placements:
            yield placement
            count += 1
            if count % 1_000_000 == 0:
                report.setdefault("emitted_phase_progress", {})[phase] = count
                save()
        report.setdefault("emitted_phase_progress", {})[phase] = count
        save()

    union = None
    try:
        report["plan_audit"] = audit_plan(graph, plan)
        report["controller_graph_audit"] = audit_placement(graph, plan, placed)
        report["facade_audit"] = audit_egress(graph, placed)
        report["structural_clock_matching"] = balance_partition_clocks(plan, placed)
        report["clock_scope"] = "Original input pin to all registers structurally matched; exterior facade delivery and connected data timing require native execution"
        connection = interconnect.connect_cache(graph, placed, fabric, production_origin, capacity_bytes=capacity_bytes)
        initial = b"".join(word.to_bytes(4, "little") for word in scenarios.initial_words(capacity_bytes=capacity_bytes))
        if len(initial) != capacity_bytes:
            raise ValueError("complete physical cache initial state must retain every installed byte")
        (destination / "initial.bin").write_bytes(initial)
        report["physical_memory"] = {"capacity_bytes": capacity_bytes, "banks": fabric.bank_count, "rows_per_bank": 1024,
            "word_bits": 32, "stored_bits": capacity_bytes * 8, "binary_decoder_bits": 10,
            "bank_origin": asdict(connection.bank_origin), "bounds": asdict(fabric.bounds(connection.bank_origin)),
            "initial_sha256": _sha(destination / "initial.bin"),
            "layer_pitch": fabric.layer_pitch, "decoder_x_offset": fabric.decoder_x_offset}
        report["external_interface"] = {"nets": len(connection.nets), "physical_return_nets": 32,
            "host_drive_pads": 72, "all_memory_return_routes_are_physical": True,
            "electrical_runtime_verified": False}
        _write(destination / "external-nets.json", [asdict(net) for net in connection.nets])
        report["external_nets_sha256"] = _sha(destination / "external-nets.json")
        check_frozen()
        report["source_input_bundle"] = _retain_inputs(destination, report, facade_report_path, manifest, plan_path,
            mapped_path=mapped_path, current_prepared=current_prepared)
        check_frozen()
        save()

        report["stage"] = "independent-sort-merge-connected-union"
        save()
        first = literal_union(observed(interconnect.iter_connected_blocks(placed, connection, fabric, initial, capacity_bytes=capacity_bytes), "sort-union"),
                              run_records=65536, temporary_parent=destination)
        report["independent_sort_union"] = first
        bounds = Box(Vec3(**first["bounds"]["min"]), Vec3(**first["bounds"]["max"]))
        check_frozen()
        report["stage"] = "sqlite-connected-union"
        save()
        union = bounded_build._LiteralUnion(destination / "connected-union.sqlite3")
        second = union.collect(observed(interconnect.iter_connected_blocks(placed, connection, fabric, initial, capacity_bytes=capacity_bytes), "sqlite-union"),
            surface_y=-61, maximum_y=319, bounds=bounds, expected_unique=first["literal_unique_blocks"])
        report["sqlite_union"] = second
        if (second["sorted_unique_record_sha256"] != first["sorted_unique_record_sha256"]
                or json.loads(json.dumps(second["palette"])) != json.loads(json.dumps(first["palette"]))
                or second["occupied_chunks"] != first["occupied_chunks"] or second["bounds"] != first["bounds"]):
            raise ValueError("independent sort and SQLite collectors disagree on exact connected states or bounds")
        report["independent_union_agreement"] = True
        check_frozen()
        available = shutil.disk_usage(destination).free
        report["available_disk_bytes_before_world"] = available
        if second["minimum_anvil_bytes"] >= available:
            raise ValueError("insufficient disk for even the exact native chunk-sector lower bound")

        report["stage"] = "write-complete-connected-native-world"
        save()
        world = destination / "world"
        with NativeWorldWriter(world, name="ShamaOS development connected cache", spawn=Vec3(-16, -59, -16),
                spawn_platform=False, surface_y=-61, max_cached_chunks=2) as writer:
            writer.set_blocks(observed(union.placements(), "native-writer"))
        report["stage"] = "independent-connected-native-readback"
        save()
        readback = validate_native_world(world)
        if (readback["assigned_blocks"] != second["unique_blocks"]
                or readback["chunks"] != second["occupied_chunks"]
                or readback["regions"] != second["occupied_regions"]):
            raise ValueError("native connected world differs from the complete emitted block union")
        report["native_readback"] = readback
        reader = NativeWorldReader(world)
        inputs, outputs = _native_maps(graph, placed, connection, fabric, capacity_bytes=capacity_bytes)
        report["native_host_pad_validation"] = _validate_native_driver_pads(reader, inputs)
        for name, coords in outputs.items():
            if reader.get_block(Vec3(*coords)).name == "minecraft:air":
                raise ValueError(f"declared physical output has no saved native block: {name}")
        _write(destination / "native-inputs.json", inputs)
        _write(destination / "native-outputs.json", outputs)
        report["native_input_map_sha256"] = _sha(destination / "native-inputs.json")
        report["native_output_map_sha256"] = _sha(destination / "native-outputs.json")
        inventory = release_io.file_inventory(world)
        _write(destination / "connected-world-inventory.json", inventory)
        report["world_inventory_sha256"] = release_io.inventory_digest(inventory)
        check_frozen()
        report.update(status="complete-connected-native-cache-generated-readback-verified", stage="native-runtime-pending")
        save()
        return report
    except Exception as error:
        report.update(status="failed", error_type=type(error).__name__, error=str(error))
        save()
        raise
    finally:
        if union is not None:
            union.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("facade_report", type=Path)
    parser.add_argument("plan", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--current-prepared", type=Path)
    parser.add_argument("--memory-limit-mib", type=int, default=2048)
    parser.add_argument("--wall-seconds", type=int, default=5400)
    args = parser.parse_args()
    if resource is None:
        parser.error("large connected-cache emission requires POSIX process accounting and bounds")
    if args.memory_limit_mib < 512 or args.wall_seconds < 1:
        parser.error("an explicit memory limit of at least 512 MiB and positive wall limit are required")
    memory_limit = args.memory_limit_mib << 20
    resource.setrlimit(resource.RLIMIT_AS, (memory_limit, memory_limit))
    resource.setrlimit(resource.RLIMIT_CPU, (args.wall_seconds, args.wall_seconds))
    def timed_out(*unused):
        raise TimeoutError("complete connected cache emission exceeded its explicit wall limit")
    signal.signal(signal.SIGALRM, timed_out)
    signal.alarm(args.wall_seconds)
    try:
        result = save_connected(args.facade_report, args.plan, args.output, current_prepared=args.current_prepared)
    finally:
        signal.alarm(0)
    print(json.dumps({key: result[key] for key in ("status", "seconds", "peak_rss_kib",
                     "connected_cache_runtime_verified", "complete_machine_runtime_verified")}, indent=2))
