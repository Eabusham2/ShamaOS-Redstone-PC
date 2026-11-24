"""Build and electrically validate the complete guarded 4 KiB cache.

This deliberately uses a fresh output directory. Every expensive stage keeps
its existing native evidence, so the strict experiment helpers can investigate
or resume a failed stage without accepting a manually supplied pass label.
The result covers one complete cache, never the whole computer or a release.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from experiments.memory_controller_runtime import audit_native_connected, current_fixture
from experiments.memory_controller_runtime import run_connected, save_connected
from experiments.memory_controller_runtime.audit_native_connected import file_record as record, sha
from experiments.partition_placement.port_egress import EgressCluster as ManifestCluster
from experiments.partition_placement.port_egress_manifest import REQUIRED_SOURCE_BINDINGS, write_egress_manifest
from shamaos.hardware.bounded_facade import EgressCluster, audit_egress
from shamaos.hardware.cache_layout import cache_policy, place_compact_cache
from shamaos.hardware.partition_placement import audit_placement, balance_partition_clocks
from shamaos.hardware.partition_planner import audit_plan
from shamaos.mchprs_import import export_world
from shamaos.model import BlockState, Placement, Vec3
from shamaos.release_io import file_inventory
from shamaos.worldio import NativeWorldWriter


STAGES = ("prepare", "geometry", "world", "runtime", "clocks", "transactions", "audit")
AUDIT_PASSED = "complete-cache-native-transactions-and-reopen-independently-verified"


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".partial")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def source_names() -> tuple[str, ...]:
    """Record the full tracked working tree, including locally modified files."""
    result = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], check=True,
                            capture_output=True)
    names = {os.fsdecode(name) for name in result.stdout.split(b"\0") if name}
    required = {*current_fixture.INPUTS, Path(__file__).relative_to(ROOT).as_posix()}
    if not required.issubset(names):
        raise ValueError("cache validation requires the complete source checkout")
    for name in names:
        relative = Path(name)
        path = ROOT / relative
        if (relative.is_absolute() or ".." in relative.parts or path.is_symlink()
                or not path.is_file() or not path.resolve().is_relative_to(ROOT)):
            raise ValueError(f"source is missing or leaves the checkout: {name}")
    return tuple(sorted(names))


def signature(path: Path) -> tuple[int, ...]:
    value = path.stat()
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns


class FrozenInputs:
    """Detect changed inputs, including a file changed back between stages."""

    def __init__(self, output: Path, runner: Path | None):
        self.names = source_names()
        self.paths = {name: ROOT / name for name in self.names}
        tools = {name: shutil.which(name) for name in ("yosys", "iverilog", "vvp")}
        if any(value is None for value in tools.values()):
            raise RuntimeError("complete cache preparation requires yosys, iverilog and vvp")
        # Some Yosys installations bundle ABC privately. Include a separately
        # visible mapper executable when the installation provides one.
        for mapper in ("yosys-abc", "berkeley-abc"):
            if executable := shutil.which(mapper):
                tools[mapper] = executable
        self.tool_paths = {name: Path(value).resolve() for name, value in tools.items()}
        if runner is not None:
            if not runner.is_file() or not os.access(runner, os.X_OK):
                raise ValueError("native cache runner must be an existing executable")
            self.tool_paths["native_runner"] = runner.resolve()
        self.stamps = {name: signature(path) for name, path in self.paths.items()}
        self.hashes = {name: sha(path) for name, path in self.paths.items()}
        self.tool_stamps = {name: signature(path) for name, path in self.tool_paths.items()}
        self.tool_records = {name: record(path) for name, path in self.tool_paths.items()}
        self.snapshot = output / "source"
        for name, path in self.paths.items():
            target = self.snapshot / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        self.copy_stamps = {name: signature(self.snapshot / name) for name in self.names}
        self.check()
        self.inventory_path = output / "source-inventory.json"
        write_json(self.inventory_path, {"schema": "connected-cache-frozen-inputs-v1",
            "source_root": str(ROOT), "snapshot_root": str(self.snapshot),
            "source_hashes": self.hashes, "tools": self.tool_records,
            "python_version": sys.version, "complete_machine_runtime_verified": False})

    def check(self) -> None:
        if source_names() != self.names:
            raise ValueError("source file inventory changed during cache validation")
        for name, path in self.paths.items():
            if signature(path) != self.stamps[name] or sha(path) != self.hashes[name]:
                raise ValueError(f"source changed during cache validation: {name}")
            copy = self.snapshot / name
            if signature(copy) != self.copy_stamps[name] or sha(copy) != self.hashes[name]:
                raise ValueError(f"frozen cache source changed: {name}")
        for name, path in self.tool_paths.items():
            if signature(path) != self.tool_stamps[name] or record(path) != self.tool_records[name]:
                raise ValueError(f"cache validation tool changed: {name}")
            if name != "native_runner" and Path(shutil.which(name) or "").resolve() != path:
                raise ValueError(f"cache validation tool selection changed: {name}")


def manifest_cluster(placed: EgressCluster) -> ManifestCluster:
    """Expose the production cluster through the retained writer's typed ABI.

    The two dataclasses are defined in separate modules. Reuse every actual
    tile, contact, port and bounds object. Only the cluster wrappers differ.
    """
    children = tuple(manifest_cluster(child) for child in placed.children)
    return ManifestCluster(**(vars(placed) | {"children": children}))


def write_controller_facade(graph, plan, placed, prepared_directory: Path,
                            directory: Path, hashes: dict[str, str]) -> dict:
    """Retain production geometry using the existing strict native-save ABI."""
    plan_path = prepared_directory / "plan.json"
    # The in-memory planner uses tuples for some routes. Compare their full
    # JSON representations, including types and every route, before saving.
    persisted = audit_native_connected.read_json(plan_path)
    if json.dumps(plan, sort_keys=True) != json.dumps(persisted, sort_keys=True):
        raise ValueError("production cache plan differs from its prepared and RTL-audited plan")
    required = set(REQUIRED_SOURCE_BINDINGS) | {
        "src/shamaos/hardware/cache_layout.py", "src/shamaos/hardware/bounded_facade.py",
        "src/shamaos/hardware/packed_parent_union.py", "tools/validate_connected_cache.py"}
    if not required.issubset(hashes):
        raise ValueError("cache geometry lacks complete production source bindings")
    directory.mkdir(parents=True, exist_ok=False)
    result = {"scope": "Complete production cache controller and original I/O; connected native runtime is a later gate",
        "mapped_source": graph.source, "source_hashes": hashes,
        "plan_sha256": sha(plan_path), "plan_audit": audit_plan(graph, plan),
        "graph_audit": audit_placement(graph, plan, placed),
        "facade_audit": audit_egress(graph, placed),
        "clock_matching": balance_partition_clocks(plan, placed),
        "bounds": asdict(placed.bounds), "computed_unique_blocks": placed.unique_blocks,
        "cache_layout_policy": placed.packing.get("cache_layout_policy"),
        "native_runtime_verified": False, "complete_machine_runtime_verified": False}
    result["balanced_manifest"] = write_egress_manifest(directory / "controller-balanced.json.gz",
        graph, plan, manifest_cluster(placed), provenance={"source_hashes": hashes, "plan_sha256": result["plan_sha256"],
            "clock_balance_verified": True, "cache_layout_policy": result["cache_layout_policy"]})
    result["status"] = save_connected.PASSED_FACADE
    path = directory / "facade-report.json"
    write_json(path, result)
    # The consumer must accept the actual manifest we wrote, not just our label.
    save_connected._accepted_facade(path, plan_path, graph)
    return result


def guarded_graph(prepared_directory: Path):
    report, graph, shape, _, _ = current_fixture.load_prepared(prepared_directory)
    policy = cache_policy(graph)
    if policy is None or policy["name"] != "one-bank-cache-front-io-guarded-v3":
        raise ValueError("validation requires the complete guarded production cache policy")
    oracle = report["rtl_oracle"]
    if (oracle["frames"], oracle["boundaries"], oracle["bit_boundary_assertions"]) != (601, 49, 1_605_632):
        raise ValueError("current guarded cache has an incomplete RTL oracle")
    return graph, shape


def check_fresh_rtl_oracle(prepared: Path) -> dict:
    """Check the new HDL run against the schedule required by the final auditor."""
    cold, reopened, _ = audit_native_connected.bound_transaction_frames()
    states = [frame.stored_words for frame in (*cold, *reopened) if frame.stored_words is not None]
    expected_path = prepared / "rtl-review/expected.hex"
    saved = [int(word, 16) for word in expected_path.read_text().splitlines()]
    if saved != [word for state in states for word in state]:
        raise ValueError("fresh RTL expected.hex differs from the independent complete cache oracle")
    return {"fresh_expected": record(expected_path), "cold_frames": len(cold), "reopen_frames": len(reopened),
            "full_storage_checkpoints": len(states), "checked_words": len(saved),
            "independent_receipt": record(ROOT / "experiments/memory_controller_runtime/current_four_kib/evidence/receipt.json")}


def check_native_runner(directory: Path, runner: Path, source_inventory: dict, *, memory_limit_mib: int,
                        wall_seconds: int) -> dict:
    """Run the supplied binary against a tiny saved grass world before placement."""
    directory.mkdir()
    world = directory / "world"
    lever, lamp = Vec3(4, -59, 4), Vec3(5, -59, 4)
    with NativeWorldWriter(world, surface_y=-61, spawn_platform=False) as writer:
        writer.set_blocks([
            Placement(lever.offset(dy=-1), BlockState.of("minecraft:stone"), "support"),
            Placement(lever, BlockState.of("minecraft:lever", face="floor", facing="north", powered="false"), "power"),
            Placement(lamp, BlockState.of("minecraft:redstone_lamp", lit="false"), "indicator")])
        readback = writer.validate()
    if readback["status"] != "passed" or not readback["terrain_preserved"] or readback["grass_surface_y"] != -61:
        raise ValueError("native runner probe needs the complete saved grass world")
    stream = directory / "world.jsonl"
    imported = export_world(world, stream)
    write_json(directory / "import.json", imported)
    inventory = file_inventory(world)
    audit_native_connected.check_imported_source(imported, world, inventory)
    switch = [lever.x, lever.y, lever.z]
    outputs = {"lever": switch, "lamp": [lamp.x, lamp.y, lamp.z]}
    off, on = dict.fromkeys(outputs, False), dict.fromkeys(outputs, True)
    scenario = {"inputs": {}, "outputs": outputs, "initial_ticks": 8, "capture_trace": False, "steps": [
        {"ticks": 0, "expect": off}, {"use_blocks": [switch], "ticks": 8, "expect": on},
        {"use_blocks": [switch], "ticks": 8, "expect": off}]}
    scenario_path = directory / "scenario.json"
    write_json(scenario_path, scenario)
    path = directory / "runner-probe.json"
    report = {"scope": "Saved grass, lever and lamp only", "status": "running", "runner": record(runner),
              "source_inventory": source_inventory, "world_files": inventory, "readback": readback,
              "stream": record(stream), "import": record(directory / "import.json"), "scenario": record(scenario_path),
              "connected_cache_runtime_verified": False, "complete_machine_runtime_verified": False}
    write_json(path, report)
    try:
        case = run_connected.execute(runner, stream, scenario_path, directory, "probe", imported["chunks"] + 1,
                                     memory_limit_mib=memory_limit_mib, wall_seconds=min(wall_seconds, 60))
        report["process"] = record(directory / "probe-process.json")
        audit_native_connected.check_process(case)
        result = audit_native_connected.read_json(audit_native_connected.check_file(case["stdout"]))
        audit_native_connected.check_runtime_result(result, imported, report["scenario"]["sha256"],
            steps=3, ticks=24, assertions=6, output_names=outputs)
        report.update(status="passed", assertions=result["assertions"], engine_revision=result["engine_revision"],
                      terrain_patch_sha256=result["engine_registry_patch_sha256"])
    except BaseException as error:
        report.update(status="failed", error_type=type(error).__name__, error=str(error))
        raise
    finally:
        write_json(path, report)
    return record(path)


def prepare_geometry(prepared_directory: Path, directory: Path, hashes: dict[str, str]) -> dict:
    graph, shape = guarded_graph(prepared_directory)
    plan, placed = place_compact_cache(graph, surface_y=shape.surface_y, maximum_y=285)
    return write_controller_facade(graph, plan, placed, prepared_directory, directory, hashes)


def checked_complete_audit(result: dict) -> None:
    if (result.get("schema") != "independent-complete-cache-native-audit-v1"
            or result.get("status") != AUDIT_PASSED
            or any(result.get(name) is not True for name in
                   ("native_clock_verified", "connected_cache_runtime_verified", "native_persistence_verified"))
            or result.get("complete_machine_runtime_verified") is not False):
        raise ValueError("only the complete independent cache audit can finish validation")
    transactions = result.get("transactions", {})
    if tuple(transactions.get(name) for name in
             ("frames", "full_storage_checkpoints", "all_bit_assertions", "reopened_saved_bits_independently_checked")) != (601, 49, 1_605_632, 32768):
        raise ValueError("independent cache audit is missing full transactions or saved-bit checks")
    physical = result.get("physical_input_review", {})
    registers = physical.get("register_clocks")
    clocks = result.get("clocks", {})
    if (physical.get("stored_bits") != 32768 or type(registers) is not int or registers < 1
            or set(clocks) != {"safe", "too-fast"}
            or clocks["safe"].get("passed") is not True
            or clocks["too-fast"].get("passed") is not False
            or any(case.get("register_clocks") != registers for case in clocks.values())):
        raise ValueError("independent cache audit is missing physical register or safe/negative clock evidence")


def validate(output: Path, *, runner: Path | None, stop_after="audit", half_period=12000,
             settle_ticks=48000, initial_ticks=48000, memory_limit_mib=4096, wall_seconds=18000) -> dict:
    output = output.resolve()
    if output.exists():
        raise FileExistsError("use a fresh validation directory; retained stages are never overwritten")
    if stop_after not in STAGES:
        raise ValueError("unknown cache validation stage")
    values = (half_period, settle_ticks, initial_ticks, memory_limit_mib, wall_seconds)
    if any(type(value) is not int for value in values) or min(values) < 1 or half_period <= 1 or memory_limit_mib < 512:
        raise ValueError("cache timing and resource limits must be positive bounded integers")
    if STAGES.index(stop_after) >= STAGES.index("clocks") and runner is None:
        raise ValueError("clock and transaction validation require the real native terrain runner")
    runner = runner.resolve() if runner is not None else None
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    summary = {"schema": "guarded-connected-cache-validation-v1", "status": "running", "stage": "freeze-inputs",
        "capacity_bytes": 4096, "stage_reports": {}, "native_clock_verified": False,
        "connected_cache_runtime_verified": False, "native_persistence_verified": False,
        "complete_machine_runtime_verified": False, "complete_machine_release_eligible": False,
        "options": {"stop_after": stop_after, "half_period": half_period, "settle_ticks": settle_ticks,
            "initial_ticks": initial_ticks, "memory_limit_mib": memory_limit_mib,
            "wall_seconds_per_writer_or_engine_process": wall_seconds},
        "scope": "Complete guarded physical cache only; whole-machine boot, rendering and release remain separate gates"}
    summary_path = output / "validation-report.json"

    def checkpoint():
        summary["seconds"] = time.monotonic() - started
        write_json(summary_path, summary)
        print(json.dumps({name: summary[name] for name in ("status", "stage", "seconds")}), flush=True)

    checkpoint()
    try:
        frozen = FrozenInputs(output, runner)
        summary["source_inventory"] = record(frozen.inventory_path)
        if runner is not None:
            summary["stage"] = "native-runner-probe"
            checkpoint()
            summary["native_runner_probe"] = check_native_runner(output / "native-runner-probe", runner,
                summary["source_inventory"], memory_limit_mib=memory_limit_mib, wall_seconds=wall_seconds)
            frozen.check()
        # The independent auditor deliberately checks a complete source root
        # two levels above the preparation directory. Preserve that real tree.
        prepared = frozen.snapshot / "build" / "current"
        geometry = output / "geometry"
        world, runtime = output / "connected", output / "runtime"
        clocks, transactions = output / "clocks", output / "transactions"
        audit_path = output / "independent-audit.json"
        limits = {"memory_limit_mib": memory_limit_mib, "wall_seconds": wall_seconds}
        reports = {
            "prepare": prepared / "prepared.json", "geometry": geometry / "facade-report.json",
            "world": world / "connected-build-report.json", "runtime": runtime / "prepared.json",
            "clocks": clocks / "clock-report.json", "transactions": transactions / "transaction-report.json",
            "audit": audit_path}
        actions = {
            "prepare": lambda: current_fixture.prepare(frozen.snapshot, prepared, capacity_bytes=4096),
            "geometry": lambda: prepare_geometry(prepared, geometry, frozen.hashes),
            # The command-line writer enforces its own POSIX memory/CPU/wall
            # bounds in a child, including during both large union passes.
            "world": lambda: subprocess.run([
                sys.executable, "-m", "experiments.memory_controller_runtime.save_connected",
                str(reports["geometry"]), str(prepared / "plan.json"), str(world),
                "--current-prepared", str(prepared), "--memory-limit-mib", str(memory_limit_mib),
                "--wall-seconds", str(wall_seconds)], cwd=ROOT, check=True,
                env=dict(os.environ, PYTHONPATH=os.pathsep.join([str(ROOT / "src"), *sys.path]))),
            "runtime": lambda: run_connected.prepare(world, runtime),
            "clocks": lambda: run_connected.clocks(runtime, runner, clocks, half_period=half_period,
                                                    settle_ticks=settle_ticks, **limits),
            "transactions": lambda: run_connected.transactions(runtime, runner, reports["clocks"], transactions,
                half_period=half_period, initial_ticks=initial_ticks, smoke=False, **limits),
            "audit": lambda: audit_native_connected.main(world, runtime, audit_path,
                clocks=reports["clocks"], transactions=reports["transactions"])}
        for stage in STAGES:
            summary["stage"] = stage
            checkpoint()
            actions[stage]()
            if stage == "prepare":
                guarded_graph(prepared)
                oracle_path = output / "rtl-oracle-crosscheck.json"
                write_json(oracle_path, check_fresh_rtl_oracle(prepared))
                summary["independent_rtl_oracle"] = record(oracle_path)
                # Bind the actual compiler products and positive/negative
                # logs so later stages cannot silently replace the RTL proof.
                summary["rtl_evidence"] = {
                    path.name: record(path) for path in sorted((prepared / "rtl-review").iterdir()) if path.is_file()}
            frozen.check()
            previous = [summary["source_inventory"], summary["independent_rtl_oracle"],
                        *summary["stage_reports"].values(), *summary["rtl_evidence"].values()]
            if "native_runner_probe" in summary:
                previous.append(summary["native_runner_probe"])
            for receipt in previous:
                if record(Path(receipt["path"])) != receipt:
                    raise ValueError(f"retained stage evidence changed: {receipt['path']}")
            summary["stage_reports"][stage] = record(reports[stage])
            if stage == "audit":
                checked_complete_audit(audit_native_connected.read_json(audit_path))
                summary.update(status="complete-connected-cache-validation-passed", native_clock_verified=True,
                    connected_cache_runtime_verified=True, native_persistence_verified=True)
            elif stage == stop_after:
                summary["status"] = f"stopped-after-{stage}-full-validation-pending"
            checkpoint()
            if stage == stop_after:
                break
        return summary
    except BaseException as error:
        summary.update(status="failed", error_type=type(error).__name__, error=str(error),
                       native_clock_verified=False, connected_cache_runtime_verified=False,
                       native_persistence_verified=False)
        checkpoint()
        raise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--runner", type=Path)
    parser.add_argument("--stop-after", choices=STAGES, default="audit")
    parser.add_argument("--half-period", type=int, default=12000)
    parser.add_argument("--settle-ticks", type=int, default=48000)
    parser.add_argument("--initial-ticks", type=int, default=48000)
    parser.add_argument("--memory-limit-mib", type=int, default=4096)
    parser.add_argument("--wall-seconds", type=int, default=18000,
                        help="maximum seconds for the world writer and each native runner process")
    args = parser.parse_args(argv)
    validate(**vars(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
