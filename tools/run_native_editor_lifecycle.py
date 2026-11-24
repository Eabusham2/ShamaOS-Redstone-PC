"""Exercise a complete saved production topology through physical Editor controls.

Run from a clean source checkout. The output is diagnostic evidence for the
specified application lifecycle. It leaves runtime budgets explicit: supply
the measured shutdown-ready lamp sample as well as the total shutdown duration.
The full placement comparison uses external sort space. Original and restored
streams, scenarios, snapshots, native saves, engine reports and hashes remain
in the requested output directory. This command never writes release acceptance.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
from itertools import chain
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from shamaos.generator import load_config, source_provenance
from shamaos.hardware.controls import control_actuators
from shamaos.mchprs_import import digest, export_world
from shamaos.mchprs_snapshot import ENGINE_REVISION, TERRAIN_PATCH, restore_snapshot
from shamaos.native_console_scenario import _console_origin
from shamaos.native_editor_lifecycle import build_lifecycle_scenarios, inspect_closed_storage
from shamaos.native_program_flash import _FlashReader
from shamaos.native_lifecycle_topology import check_manifest, check_topology
from shamaos.os_image import build_default_os_image
from shamaos.release_io import file_inventory, inventory_digest
from shamaos.worldio import WorldWriteError, resolve_world_surface
from tools.prepare_mchprs_terrain_registry import prepare


# Actual pinned terrain executable used for the independently retained native clock review.
# Different build/platform binaries can use the controlled source-build path.
REVIEWED_RUNNER_SHA256 = "c8032089622501cb57f9edbb0ea7e3be24e3a23fd0b2b80653b0ed4f07045610"
FLOWS = ("cold-desktop", "new-text-and-dirty-home", "assemble-and-run", "home-and-shutdown",
         "quiescent-restore", "flash-files-and-erased-cache", "explorer-editor-reopen")


def write_json(path, data):
    with path.open("x", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def tracked_stamps(root):
    names = subprocess.check_output(["git", "ls-files", "-z"], cwd=root).split(b"\0")
    return {os.fsdecode(name): _FlashReader._stamp(root / os.fsdecode(name)) for name in names if name}


def tree_stamps(root):
    # Include directories so creating and removing an extra region is observable.
    return {path: _FlashReader._stamp(path) for path in chain((root,), root.rglob("*"))}


def require_stable(current, previous, label):
    if current != previous:
        raise WorldWriteError(f"{label} changed during lifecycle execution")


def reviewed_runner(path):
    path = path.resolve(strict=True)
    if digest(path) != REVIEWED_RUNNER_SHA256 or not os.access(path, os.X_OK):
        raise WorldWriteError("Unrecognized native runner; build from the pinned engine source instead")
    return path


def make_engine(source, output):
    # Build the pinned sparse harness directly from project and engine sources
    # into a fresh cargo target. A supplied executable cannot certify its own output.
    inputs = tracked_stamps(source)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
    dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=source)
    if revision != ENGINE_REVISION or dirty:
        raise WorldWriteError("Lifecycle execution requires a clean pinned MCHPRS engine checkout")
    target = output / "target"
    report = prepare(source, output / "engine")
    if report["engine_revision"] != ENGINE_REVISION or report["patch_sha256"] != TERRAIN_PATCH:
        raise WorldWriteError("Unexpected native engine sources")
    runner = report["runners"]["sparse"]
    private = output / "engine" / "vendor"
    vendor_stamps = tree_stamps(private)
    project_manifest = Path(runner["manifest"])
    manifest_stamp = _FlashReader._stamp(project_manifest)
    subprocess.run(["cargo", "build", "--offline", "--release", "--target-dir", str(target),
                    "--manifest-path", runner["manifest"]], check=True,
                   env={**os.environ, "SHAMA_TERRAIN_PATCH_SHA256": TERRAIN_PATCH})
    require_stable(tracked_stamps(source), inputs, "Pinned engine source")
    require_stable(tree_stamps(private), vendor_stamps, "Compiled private engine source")
    require_stable(_FlashReader._stamp(project_manifest), manifest_stamp, "Compiled native harness manifest")
    return target / "release" / runner["binary_name"]


def check_result(result, imported, scenario, scenario_hash, snapshot, runner_hash):
    assertions = sum(len(s.get("expect", {})) for s in scenario["steps"])
    expected = {"kind": "streamed-native-anvil-sparse-mchprs-runtime", "status": "passed",
                "source": imported["source"], "scenario_sha256": scenario_hash,
                "imported_stream_sha256": imported["stream_sha256"], "engine_revision": ENGINE_REVISION,
                "engine_registry_patch_sha256": TERRAIN_PATCH, "loaded_chunks": imported["chunks"],
                "initialized_nonair_blocks": imported["nonair"], "decoded_nonair_blocks": imported["nonair"],
                "comparator_entities": imported["comparators"], "pending_ticks_after_scenario": 0,
                "complete_machine_runtime_verified": False, "steps": len(scenario["steps"]),
                "ticks": scenario["initial_ticks"] + sum(s["ticks"] for s in scenario["steps"]),
                "assertions": assertions}
    if assertions <= 0 or any(type(result.get(k)) != type(v) or result[k] != v for k, v in expected.items()):
        raise WorldWriteError("Native result differs from the actual import, scenario, or quiescence requirements")
    snap = result.get("snapshot")
    if (not isinstance(snap, dict) or snap.get("status") != "quiescent-snapshot-written"
            or snap.get("path") != str(snapshot) or not snapshot.is_file()
            or snap.get("bytes") != snapshot.stat().st_size or digest(snapshot) != snap.get("sha256")
            or snap.get("chunks") != result["chunks_after_drives"]):
        raise WorldWriteError("Native result lacks an authentic bound snapshot")
    with snapshot.open("rb") as f:
        header = json.loads(f.readline(16 << 20))
    if (header.get("imported_stream_sha256") != expected["imported_stream_sha256"]
            or header.get("scenario_sha256") != scenario_hash or header.get("source") != imported["source"]
            or header.get("engine_revision") != ENGINE_REVISION or header.get("pending_scheduled_ticks") != 0
            or header.get("engine_registry_patch_sha256") != TERRAIN_PATCH
            or header.get("executed_redstone_ticks") != expected["ticks"]):
        raise WorldWriteError("Snapshot header is not bound to this scenario execution")
    return {"runner_sha256": runner_hash, "scenario_sha256": scenario_hash,
            "stream_sha256": imported["stream_sha256"], "snapshot_sha256": snap["sha256"],
            "ticks": expected["ticks"], "assertions": assertions, "loaded_chunks": imported["chunks"]}


def execute_saved(world, scenario, output, runner, runner_hash, timeout):
    """Export, execute, restore, and bind a quiescent native run in a new directory."""
    if scenario.get("inputs") != {} or any("drive" in step for step in scenario["steps"]):
        raise WorldWriteError("Only physical use_blocks interactions may drive this lifecycle")
    original_world = tree_stamps(world)
    output.mkdir()
    stream, scenario_file = output / "source.jsonl", output / "scenario.json"
    imported = export_world(world, stream)
    write_json(output / "import.json", imported)
    write_json(scenario_file, scenario)
    files = (runner, stream, scenario_file)
    initial_stamps = tuple(map(_FlashReader._stamp, files))
    case_hash = digest(scenario_file)
    snapshot, result_file = output / "snapshot.jsonl", output / "runtime.json"
    if digest(runner) != runner_hash:
        raise WorldWriteError("Native runner changed before execution")
    with result_file.open("xb") as stdout, (output / "stderr.log").open("xb") as stderr:
        subprocess.run([str(runner), str(stream), str(scenario_file), str(imported["chunks"] + 128),
                        "--snapshot", str(snapshot)], check=True, timeout=timeout, stdout=stdout, stderr=stderr)
    require_stable(tuple(map(_FlashReader._stamp, files)), initial_stamps, "Runner, source stream or scenario")
    require_stable(tree_stamps(world), original_world, "Imported native world")
    snapshot_stamp = _FlashReader._stamp(snapshot)
    result_stamp = _FlashReader._stamp(result_file)
    result_hash = digest(result_file)
    report = check_result(json.loads(result_file.read_bytes()), imported, scenario, case_hash, snapshot, runner_hash)
    if (digest(scenario_file) != case_hash or digest(stream) != imported["stream_sha256"]
            or digest(runner) != runner_hash):
        raise WorldWriteError("Native execution input changed")
    saved = output / "restored-world"
    rebuilt = restore_snapshot(snapshot, world, saved, expected_sha256=report["snapshot_sha256"])
    if (rebuilt["parent_scenario_sha256"] != case_hash or rebuilt["parent_imported_stream_sha256"] != report["stream_sha256"]
            or not rebuilt["original_chunk_inventory_preserved"] or rebuilt["original_source_chunks"] != imported["chunks"]):
        raise WorldWriteError("The restored world is not a descendant of this exact native execution")
    require_stable(tuple(map(_FlashReader._stamp, files)), initial_stamps, "Runner, source stream or scenario")
    require_stable(tree_stamps(world), original_world, "Imported native world")
    require_stable(_FlashReader._stamp(result_file), result_stamp, "Native runtime report")
    require_stable(_FlashReader._stamp(snapshot), snapshot_stamp, "Native snapshot")
    report["native_result_sha256"] = result_hash
    write_json(output / "verified-run.json", report | {"restore": rebuilt})
    return saved, report


def run(candidate, output, source, config_path, budgets, timeout, *, runner_path=None):
    if not (sys.platform.startswith("linux") or sys.platform == "darwin"):
        raise WorldWriteError("Native restore requires atomic directory publication on Linux or macOS")
    candidate, output = candidate.resolve(strict=True), output.resolve()
    if output.is_relative_to(candidate) or candidate.is_relative_to(output):
        raise WorldWriteError("Use a separate fresh output outside the candidate")
    world = candidate / "world"
    metadata = candidate / "generated-manifest.json"
    if metadata.is_symlink() or world.is_symlink():
        raise WorldWriteError("Lifecycle evidence must use the actual candidate files")
    source_stamps = tracked_stamps(ROOT)
    pair = (metadata, config_path)
    original_stamps = tuple(map(_FlashReader._stamp, pair))
    world_stamps = tree_stamps(world)
    raw = metadata.read_bytes()
    generated = json.loads(raw)
    # The release builder resolves a new world's configured surface before writing level.dat.
    config_raw = config_path.read_bytes()
    config = resolve_world_surface(load_config(config_path), None)
    config["generator"]["logic_backend"] = generated["logic_backend"]
    if config["generator"]["logic_backend"] not in ("flat", "bounded"):
        raise WorldWriteError("Unsupported source generation backend")
    commit = generated["source"]["base_revision"]
    check_manifest(generated, config, commit)
    if (source is None) == (runner_path is None):
        raise WorldWriteError("Select one verified binary or pinned engine source")
    if source is not None and shutil.which("cargo") is None:
        raise WorldWriteError("The source-built runtime requires Cargo, or use the reviewed binary")
    runner_stamp = _FlashReader._stamp(runner_path) if runner_path is not None else None
    runner = reviewed_runner(runner_path) if runner_path is not None else None
    output.mkdir(parents=True, exist_ok=False)
    inventory = file_inventory(world)
    binding = {"source_commit": commit, "generated_manifest_sha256": sha256(raw).hexdigest(),
               "config_file_sha256": sha256(config_raw).hexdigest(), "world_inventory_sha256": inventory_digest(inventory)}
    write_json(output / "source.json", binding | {"world_files": inventory})
    stage = "topology"
    try:
        topology = check_topology(world, config, generated, output / "topology")
        write_json(output / "verified-topology.json", topology)
        require_stable(tracked_stamps(ROOT), source_stamps, "Source checkout during mapping")
        require_stable(tree_stamps(world), world_stamps, "Native candidate during mapping")
        stage = "console"
        program, reopen = build_lifecycle_scenarios(world, generated, **budgets)
        initial = program["shamaos_initial_flash_sha256"]
        if initial != sha256(build_default_os_image().image).hexdigest():
            raise WorldWriteError("World does not start with the exact shipped OS flash image")
        power = control_actuators(_console_origin(generated))["POWER"]
        stage = "build-engine"
        if runner is None:
            runner = make_engine(source, output)
            runner_stamp = _FlashReader._stamp(runner)
            binary_hash = digest(runner)
        else:
            binary_hash = REVIEWED_RUNNER_SHA256
        phases = {}
        retained = []
        evidence_stamps = {}
        for phase, scenario in (("create-and-run", program), ("reopen-and-edit", reopen)):
            stage = phase
            saved, measured = execute_saved(world, scenario, output / phase, runner, binary_hash, timeout)
            saved_stamps = tree_stamps(saved)
            stage = phase + "-storage"
            storage = inspect_closed_storage(saved, generated, power, initial)
            write_json(output / phase / "closed-storage.json", storage)
            phases[phase] = measured | {"closed_storage": storage}
            require_stable(tree_stamps(saved), saved_stamps, "Restored native world during flash readback")
            retained.append((saved, saved_stamps))
            for name in ("runtime.json", "scenario.json", "source.jsonl", "snapshot.jsonl",
                         "import.json", "verified-run.json", "closed-storage.json"):
                artifact = output / phase / name
                evidence_stamps[artifact] = _FlashReader._stamp(artifact)
            world = saved
        stage = "final-bindings"
        require_stable({p: _FlashReader._stamp(p) for p in evidence_stamps}, evidence_stamps, "Native evidence")
        for saved, stamps in retained:
            require_stable(tree_stamps(saved), stamps, "Restored native world evidence")
        require_stable(tracked_stamps(ROOT), source_stamps, "Source checkout")
        require_stable(tuple(map(_FlashReader._stamp, pair)), original_stamps, "Config or generation manifest")
        require_stable(_FlashReader._stamp(runner), runner_stamp, "Pinned runtime executable")
        require_stable(tree_stamps(candidate / "world"), world_stamps, "Original world")
        if (metadata.read_bytes() != raw or config_path.read_bytes() != config_raw or
                source_provenance() != generated["source"] or file_inventory(candidate / "world") != inventory):
            raise WorldWriteError("Production source, manifest, config or original world changed during execution")
        receipt = binding | {"scope": "native editor lifecycle in continuously loaded chunks",
                             "observed_flows": list(FLOWS), "phases": phases, "complete_machine_runtime_verified": False,
                             "release_acceptance_verified": False}
        write_json(output / "editor-lifecycle.json", receipt)
        return receipt
    except Exception as exc:
        write_json(output / "failure.json", binding | {"stage": stage, "error": str(exc),
                                                     "complete_machine_runtime_verified": False})
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("output", type=Path)
    engine = parser.add_mutually_exclusive_group(required=True)
    engine.add_argument("--engine-source", type=Path, help="clean pinned MCHPRS source for offline compilation")
    engine.add_argument("--runner", type=Path, help="previously reviewed terrain binary, verified by SHA-256")
    parser.add_argument("--require-flow", choices=FLOWS, action="append", default=[],
                        help="reject an unsupported required flow before running; all listed flows always run")
    parser.add_argument("--config", type=Path, default=ROOT / "configs/default.toml")
    parser.add_argument("--timeout-seconds", type=int, required=True, help="wall time allowed per actual native execution")
    names = ("initial", "boot", "editor", "home", "key", "action", "run", "shutdown")
    parser.add_argument("--shutdown-ack-ticks", type=int, required=True, help="sample shutdown_ready this many RT after POWER off")
    for name in names:
        parser.add_argument(f"--{name}-ticks", type=int, required=True)
    args = parser.parse_args(argv)
    budgets = {name + "_ticks": getattr(args, name + "_ticks") for name in names}
    budgets["shutdown_ack_ticks"] = args.shutdown_ack_ticks
    if not 0 < args.shutdown_ack_ticks < args.shutdown_ticks:
        parser.error("The physical shutdown acknowledgment sample must be inside the total shutdown budget")
    if args.timeout_seconds < 1 or min(budgets.values()) < 1 or min(budgets["key_ticks"], budgets["action_ticks"]) <= 119:
        parser.error("Provide positive budgets and allow more than 119 ticks per physical key/action")
    try:
        run(args.candidate, args.output, args.engine_source, args.config, budgets, args.timeout_seconds, runner_path=args.runner)
    except (WorldWriteError, OSError, KeyError, ValueError, TypeError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"Native editor lifecycle blocked: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
