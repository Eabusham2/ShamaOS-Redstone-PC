"""Prepare/place the complete cache controller; large phases require explicit CLI modes."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys
import time
import tomllib
import traceback
import zipfile

if not __debug__:
    raise SystemExit("complete-cache fixture requires assertions")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from experiments.memory_controller_runtime.geometry import (
    cache_route_contract, read_manifest, restore_manifest, write_manifest,
)
from experiments.memory_controller_runtime.clock_reserve import reserve_clock_tracks, validate_padding
from experiments.memory_controller_runtime.scenarios import functional_frames, initial_words, summarize_frames, words_sha256
from shamaos.hardware import partition_placement
from shamaos.hardware.logic import template_for
from shamaos.hardware.partition_planner import Graph, audit_plan, build_plan
from shamaos.hardware.memory_backbone import fabric_tile_ports
from shamaos.layout import default_origins, memory_specs
from shamaos.system_build import _geometry_from_config

TOP = "shama_cache_adapter"
MAPPED_SHA = "d4989124e9dca8b5afa865d954ded3be680b99f0b1e2f472aed6b5ecc42dd468"
HERE = Path(__file__).resolve().parent
HISTORICAL_INPUTS = HERE / "evidence/cache-controller-c331-inputs.zip"
HISTORICAL_CONFIG_SHA = "630b70f01ac7de1f1b74250f8b454c9d090e461891f347fadc4c375c27cc5b20"
SOURCE_PATHS = (
    "experiments/memory_controller_runtime/evidence/cache-controller-c331-inputs.zip",
    "experiments/memory_controller_runtime/evidence/cache-vram-scope.json",
    "src/shamaos/synthesis.py", "src/shamaos/layout.py", "src/shamaos/system_build.py",
    "src/shamaos/model.py", "src/shamaos/machine_spec.py", "src/shamaos/generator.py",
    "src/shamaos/hardware/partition_planner.py", "src/shamaos/hardware/partition_placement.py",
    "src/shamaos/hardware/local_router.py", "src/shamaos/hardware/external_router.py",
    "src/shamaos/hardware/logic.py", "src/shamaos/hardware/memory.py",
    "src/shamaos/hardware/memory_backbone.py", "src/shamaos/hardware/memory_streaming.py",
    "src/shamaos/hardware/binary_row_decoder.py", "src/shamaos/hardware/routing.py",
    "src/shamaos/hardware/interfaces.py",
    "experiments/partition_placement/raised_exports/geometry_manifest.py",
    "experiments/partition_placement/raised_exports/replay_manifest.py",
    "experiments/memory_controller_runtime/fixture.py", "experiments/memory_controller_runtime/geometry.py",
    "experiments/memory_controller_runtime/scenarios.py",
    "experiments/memory_controller_runtime/clock_reserve.py",
)


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def save(path: Path, value) -> None:
    with path.open("x") as stream:
        stream.write(json.dumps(value, indent=2) + "\n")


def historical_inputs():
    """Validate the exact elaboration inputs of the retained 8 KiB mapping.

    Current production capacities and RTL may evolve without rewriting this
    historical experiment's source identity. Its original hashes remain
    authoritative, and every archived input is checked before its config is
    used. This does not validate a new production controller or capacity.
    """
    scope = json.loads((HERE / "evidence/cache-vram-scope.json").read_text())
    expected = dict(scope["source_sha256"], **{"configs/default.toml": HISTORICAL_CONFIG_SHA})
    with zipfile.ZipFile(HISTORICAL_INPUTS) as archive:
        if len(archive.namelist()) != len(expected) or set(archive.namelist()) != set(expected):
            raise ValueError("historical cache elaboration input inventory changed")
        contents = {name: archive.read(name) for name in expected}
    for name, digest in expected.items():
        if hashlib.sha256(contents[name]).hexdigest() != digest:
            raise ValueError(f"historical cache elaboration input changed: {name}")
    return contents


def graph_and_fabric():
    """Return the frozen c331 8 KiB case, never the current production cache."""
    mapped = HERE / "evidence/cache-controller-c331.json.gz"
    graph = Graph.read(mapped, TOP)
    if graph.source["sha256"] != MAPPED_SHA or graph.cell_count != 1144:
        raise ValueError("fixture must use the exact complete historical c331 cache mapping")
    if sum(entity["kind"] == "port" for entity in graph.entities) != 183:
        raise ValueError("original cache pin inventory changed")
    flops = [entity for entity in graph.entities if entity["kind"] == "cell"
             and template_for(entity["cell_type"]).cell_type == "DFF"]
    if len(flops) != 115:
        raise ValueError("original cache register inventory changed")
    config = tomllib.loads(historical_inputs()["configs/default.toml"].decode())
    geometry = _geometry_from_config(config)
    fabric = memory_specs(geometry)[0]
    origin = default_origins(geometry).cache
    if (fabric.total_bytes, fabric.bank_count, fabric.bank.words, fabric.bank.word_bits,
            fabric.layers, fabric.layer_pitch, fabric.decoder_x_offset) != (8192, 2, 1024, 32, 2, 12, -170):
        raise ValueError("historical cache geometry/capacity changed")
    if geometry.surface_y != -61 or geometry.minimum_build_y != -60:
        raise ValueError("fixture uses the default superflat grass without ground edits")
    return graph, geometry, fabric, origin


def prepare(directory: Path, *, controller_ceiling=285, clock_track_padding=0) -> dict:
    if controller_ceiling > 285 or controller_ceiling < -40:
        raise ValueError("controller ceiling must reserve external Y314/318 routing above the grass")
    validate_padding(clock_track_padding)
    directory.mkdir(parents=True, exist_ok=False)
    graph, geometry, fabric, origin = graph_and_fabric()
    plan = build_plan(graph)
    audit = audit_plan(graph, plan)
    if plan["summary"]["clock_leaf_uses"] != 115 or not plan["summary"]["all_signal_records_retained"]:
        raise ValueError("complete clock/signal plan was not retained")
    save(directory / "plan.json", plan)
    initial = b"".join(word.to_bytes(4, "little") for word in initial_words())
    (directory / "initial.bin").write_bytes(initial)
    frames = list(functional_frames())
    schedule = summarize_frames(frames)
    save(directory / "request-plan.json", schedule)
    continuation = summarize_frames(list(functional_frames(schedule["final_words"], resume=True)))
    save(directory / "continuation-plan.json", continuation)
    native_ports = fabric_tile_ports(origin, fabric)
    if len(native_ports) != 1:
        raise ValueError("cache must retain its complete single two-bank tile")
    routing = cache_route_contract(origin, fabric)
    report = {
        "schema": "complete-cache-native-fixture-v1", "status": "prepared-not-placed",
        "configuration_scope": "Frozen c331 8 KiB controller and capacity; current production cache is a separate proof",
        "historical_elaboration_inputs_sha256": sha(HISTORICAL_INPUTS),
        "mapped_source": graph.source, "source_hashes": {name: sha(ROOT / name) for name in SOURCE_PATHS},
        "plan_sha256": sha(directory / "plan.json"), "plan_summary": plan["summary"], "plan_audit": audit,
        "initial_bytes": len(initial), "initial_sha256": sha(directory / "initial.bin"),
        "request_plan_sha256": sha(directory / "request-plan.json"),
        "continuation_plan_sha256": sha(directory / "continuation-plan.json"),
        "request_summary": {key: value for key, value in schedule.items() if key not in ("final_words", "boundary_states")},
        "continuation_summary": {key: value for key, value in continuation.items() if key not in ("final_words", "boundary_states")},
        "trace_preparation": {
            "default_maximum_samples": 20_000_000,
            "sample_formula": "sum(step half-period units) * half-period ticks * selected output count",
            "cold_half_period_units": schedule["half_period_units"],
            "continuation_half_period_units": continuation["half_period_units"],
            "initial_ticks_are_traced": False, "physical_half_period_chosen": False,
            "scope": "Full register clock observation must use a short dedicated probe; storage boundary assertions are never narrowed by trace selection",
        },
        "final_expected_words_sha256": words_sha256(schedule["final_words"]),
        "controller_ceiling_y": controller_ceiling, "surface_y": -61,
        "clock_track_padding_blocks": clock_track_padding,
        "external_routing_planes": {"branch_y": 314, "trunk_y": 318},
        "cache": {"capacity_bytes": 8192, "storage_bits": 65536, "banks": 2, "rows_per_bank": 1024,
                  "word_bits": 32, "layer_pitch": 12, "decoder_x_offset": -170,
                  "production_origin": asdict(origin), "production_bounds": asdict(fabric.bounds(origin)),
                  "ports": asdict(native_ports[0])},
        "cache_routing_contract": {
            "terminal_count": len(routing.access), "literal_keepout_placements": len(routing.obstacles),
            "conservative_volumes": [asdict(box) for box in routing.obstacle_bounds],
            "minimum_y": routing.minimum_y,
            "scope": "Native memory access faces and obstacles only; connected controller routing remains pending",
        },
        "controller_native_cells": 1144, "controller_original_pins": 183, "controller_DFFs": 115,
        "externally_driven_host_bits": 72, "memory_interconnect_nets": 78,
        "geometry_verified": False, "native_world_written": False, "connected_cache_runtime_verified": False,
        "complete_machine_runtime_verified": False,
    }
    save(directory / "prepared.json", report)
    return report


def verify_prepared(directory: Path) -> dict:
    report = json.loads((directory / "prepared.json").read_text())
    if report["schema"] != "complete-cache-native-fixture-v1":
        raise ValueError("unexpected fixture schema")
    if set(report["source_hashes"]) != set(SOURCE_PATHS):
        raise ValueError("fixture source binding inventory changed")
    for name, expected in report["source_hashes"].items():
        if sha(ROOT / name) != expected:
            raise ValueError(f"fixture source changed after preparation: {name}")
    for name, key in (("plan.json", "plan_sha256"), ("initial.bin", "initial_sha256"),
                      ("request-plan.json", "request_plan_sha256"), ("continuation-plan.json", "continuation_plan_sha256")):
        if sha(directory / name) != report[key]:
            raise ValueError(f"prepared fixture artifact changed: {name}")
    graph, _, _, _ = graph_and_fabric()
    if any(graph.source[key] != report["mapped_source"][key] for key in ("sha256", "container_sha256")):
        raise ValueError("mapped fixture container changed")
    return report


def place_controller(directory: Path) -> dict:
    prepared = verify_prepared(directory)
    if (directory / "controller.json.gz").exists() or (directory / "controller-placement-report.json").exists():
        raise FileExistsError("placement results already exist; use a new preparation directory")
    graph, _, _, _ = graph_and_fabric()
    plan = json.loads((directory / "plan.json").read_text())
    started = time.monotonic()
    progress = directory / "controller-progress.jsonl"
    originals = {name: getattr(partition_placement, name) for name in ("_leaf", "_parent")}

    def observed(name):
        original = originals[name]

        def wrapped(*args, **kwargs):
            begin = time.monotonic()
            try:
                result = original(*args, **kwargs)
            except Exception as error:
                row = {"stage": name, "cluster": args[1], "status": "failed", "error": str(error),
                       "seconds": time.monotonic() - begin}
                with progress.open("a") as stream:
                    stream.write(json.dumps(row) + "\n")
                print(json.dumps(row), flush=True)
                raise
            row = {"stage": name, "cluster": result.cluster_id, "status": "placed",
                   "seconds": time.monotonic() - begin, "unique_blocks": result.unique_blocks,
                   "bounds": asdict(result.bounds), "modules": len(result.all_modules())}
            with progress.open("a") as stream:
                stream.write(json.dumps(row) + "\n")
            print(json.dumps(row), flush=True)
            return result

        return wrapped

    report = {"schema": "complete-cache-controller-placement-v1", "mapped_sha256": MAPPED_SHA,
              "source_hashes": prepared["source_hashes"], "plan_sha256": prepared["plan_sha256"],
              "clock_track_padding_blocks": prepared["clock_track_padding_blocks"],
              "clock_balance_verified": False,
              "connected_cache_runtime_verified": False, "complete_machine_runtime_verified": False}
    try:
        for name in originals:
            setattr(partition_placement, name, observed(name))
        clock_bits = {signal["bit"] for signal in plan["signals"]
                      if any(sink["pin"] == "C" for sink in signal["sinks"])}
        with reserve_clock_tracks(clock_bits, prepared["clock_track_padding_blocks"]):
            placed = partition_placement.place_partition(graph, plan, surface_y=-61,
                        maximum_y=prepared["controller_ceiling_y"], balance_clocks=False)
        report["structural_audit"] = partition_placement.audit_placement(graph, plan, placed)
        report["unbalanced_manifest"] = write_manifest(directory / "controller-unbalanced.json.gz", placed, provenance={
            "schema": "complete-cache-controller-unbalanced-checkpoint-v1", "mapped_sha256": MAPPED_SHA,
            "plan_sha256": prepared["plan_sha256"], "source_hashes": prepared["source_hashes"],
            "controller_ceiling_y": prepared["controller_ceiling_y"],
            "clock_track_padding_blocks": prepared["clock_track_padding_blocks"],
            "clock_balance_verified": False,
        })
        report["clock_matching"] = partition_placement.balance_partition_clocks(plan, placed)
        report["clock_balance_verified"] = True
        verify_prepared(directory)
        record = write_manifest(directory / "controller.json.gz", placed, provenance={
            "schema": "complete-cache-controller-manifest-v1", "mapped_sha256": MAPPED_SHA,
            "plan_sha256": prepared["plan_sha256"], "source_hashes": prepared["source_hashes"],
            "controller_ceiling_y": prepared["controller_ceiling_y"],
            "clock_track_padding_blocks": prepared["clock_track_padding_blocks"],
        })
        report.update(status="complete-controller-placed-interconnect-and-runtime-pending",
                      controller_manifest=record,
                      controller_unique_blocks=placed.unique_blocks, bounds=asdict(placed.bounds))
    except Exception as error:
        report.update(status="failed", error=str(error), traceback=traceback.format_exc())
        raise
    finally:
        for name, original in originals.items():
            setattr(partition_placement, name, original)
        report["seconds"] = time.monotonic() - started
        save(directory / "controller-placement-report.json", report)
    return report


def load_controller(directory: Path):
    """Reopen exact retained routes without performing another allocation."""
    prepared = verify_prepared(directory)
    report = json.loads((directory / "controller-placement-report.json").read_text())
    if report["status"] != "complete-controller-placed-interconnect-and-runtime-pending":
        raise ValueError("complete controller placement has not passed")
    if not report.get("clock_balance_verified"):
        raise ValueError("complete controller clock matching has not passed")
    expected = {
        "schema": "complete-cache-controller-manifest-v1", "mapped_sha256": MAPPED_SHA,
        "plan_sha256": prepared["plan_sha256"], "source_hashes": prepared["source_hashes"],
        "controller_ceiling_y": prepared["controller_ceiling_y"],
        "clock_track_padding_blocks": prepared["clock_track_padding_blocks"],
    }
    document = read_manifest(directory / "controller.json.gz", report["controller_manifest"]["sha256"])
    if document["provenance"] != expected:
        raise ValueError("controller manifest does not bind the exact prepared graph and sources")
    placed = restore_manifest(document)
    graph, _, fabric, origin = graph_and_fabric()
    plan = json.loads((directory / "plan.json").read_text())
    audit = partition_placement.audit_placement(graph, plan, placed)
    if audit != report["structural_audit"] or asdict(placed.bounds) != report["bounds"]:
        raise ValueError("reopened controller inventory/envelope differs from its placement report")
    if placed.unique_blocks != report["controller_unique_blocks"]:
        raise ValueError("reopened controller literal count differs from its placement report")
    if placed.bounds.min.y < -60 or placed.bounds.max.y > prepared["controller_ceiling_y"]:
        raise ValueError("retained controller breaches its reserved above-grass height")
    return prepared, graph, plan, placed, fabric, origin


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "place-controller"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--controller-ceiling", type=int, default=285)
    parser.add_argument("--clock-track-padding", type=int, default=0)
    args = parser.parse_args()
    directory = args.output.resolve()
    report = prepare(directory, controller_ceiling=args.controller_ceiling,
                     clock_track_padding=args.clock_track_padding) if args.phase == "prepare" else place_controller(directory)
    print(json.dumps({key: value for key, value in report.items() if key in
                     ("status", "plan_summary", "request_summary", "seconds", "controller_unique_blocks", "bounds")}, indent=2))


if __name__ == "__main__":
    main()
