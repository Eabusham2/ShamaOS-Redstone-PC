"""Package an evidenced complete world; incomplete builds fail before output.

This is an evidence checker and packager, not a substitute for running the
complete machine. There is deliberately no 'skip checks' or fixture-release
option. The ordinary CI fixtures remain separately named evidence artifacts.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from .generator import REQUIRED_CAPACITY, validate_manifest_capacity
from .machine_spec import CACHE_BYTES, FLASH_BYTES, RAM_BYTES, VRAM_BYTES
from .release_acceptance import verify_full_world_acceptance
from .release_io import file_inventory, inventory_digest, safe_relative, sha256, split_asset, write_zip
from .schematic import export_litematic, machine_bounds
from .worldio import WorldWriteError, validate_native_world


GATES = ("literal_capacity", "routing", "timing", "world_runtime", "controls_and_screen",
         "app_lifecycle", "persistence", "mining", "capacity_faults", "loaded_chunks", "relocation")


def _require(condition, message):
    if not condition:
        raise WorldWriteError(message)


def validate_ci(report: dict, commit: str) -> None:
    _require(report.get("head_sha") == commit and report.get("conclusion") == "success"
             and report.get("status") == "completed", "The exact release commit needs a completed green CI run.")
    _require(report.get("path") == ".github/workflows/ci.yml", "CI evidence must come from the repository CI workflow.")
    jobs = report.get("jobs", [])
    _require(bool(jobs) and all(job.get("conclusion") == "success" for job in jobs), "Every required CI job must pass.")
    names = [job["name"] for job in jobs]
    for name in ("world-backend", "rtl", "behavioral-and-redstone", "full-physical-synthesis"):
        _require(names.count(name) == 1, f"Missing required CI job: {name}")
    _require(sum(name.startswith("test (") for name in names) == 4, "All four Linux/Windows/Python matrix jobs must pass.")
    _require(sum(name.startswith("full-physical-partitions (") for name in names) == 12,
             "All twelve full synthesis partitions must pass; request CI with [full-synth].")


def validate_candidate(candidate: Path, commit: str, ci: dict) -> dict:
    _require(re.fullmatch(r"[0-9a-f]{40}", commit) is not None, "A full Git source commit is required.")
    validate_ci(ci, commit)
    try:
        manifest = json.loads((candidate / "generated-manifest.json").read_text(encoding="utf-8"))
        acceptance = json.loads((candidate / "acceptance.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise WorldWriteError("A generated full world and its complete-machine acceptance evidence are required.") from exc
    _require(acceptance.get("format") == "shamaos-full-world-acceptance-v1"
             and acceptance.get("scope") == "complete-machine" and acceptance.get("status") == "passed",
             "Small fixtures and untested worlds cannot be released as the complete computer.")
    _require(acceptance.get("source_commit") == commit, "World acceptance evidence belongs to another source commit.")
    source = manifest.get("source", {})
    _require(source.get("base_revision") == commit and source.get("working_tree_dirty") is False,
             "The generated world must come from the clean exact release commit.")
    capacity = validate_manifest_capacity(manifest)
    _require(capacity["status"] == "passed", "The candidate violates the literal machine capacity: " + "; ".join(capacity["errors"]))
    _require(acceptance.get("observed_capacity_bytes") == {"ram": RAM_BYTES, "cache": CACHE_BYTES,
              "flash": FLASH_BYTES, "vram": VRAM_BYTES}, "Full-world observed storage capacities must match the installed RCC values.")
    _require(manifest.get("physical_generation_status") == "generated-topology-readback-verified",
             "The machine must have been generated and read back, not just planned or synthesized.")
    stages = manifest.get("stage_counts", {})
    for stage in ("synthesized-soc", "system-infrastructure", "system-interconnect"):
        _require(type(stages.get(stage)) is int and stages[stage] > 0, f"Complete generation is missing {stage}.")
    evidence = {}
    for gate in GATES:
        result = acceptance.get("gates", {}).get(gate, {})
        _require(result.get("status") == "passed" and result.get("scope") == "complete-machine",
                 f"Complete-world release gate remains open: {gate}.")
        paths = result.get("evidence", [])
        _require(bool(paths), f"The {gate} gate needs recorded evidence, not only a pass label.")
        for record in paths:
            relative = safe_relative(record["path"])
            _require(relative.parts[0] == "evidence", "Acceptance evidence must be inside the candidate evidence folder.")
            path = candidate.joinpath(*relative.parts)
            _require(path.is_file() and not path.is_symlink() and path.stat().st_size > 0
                     and sha256(path) == record["sha256"], f"Missing or changed {gate} evidence: {relative}")
            evidence[str(relative)] = record["sha256"]
    runtime = acceptance.get("runtime", {})
    for key in ("name", "version", "clock", "loading", "setup_steps"):
        _require(bool(runtime.get(key)), f"Acceptance must record the tested runtime {key}.")
    for key in ("render_distance", "simulation_distance"):
        _require(type(runtime.get(key)) is int and runtime[key] > 0,
                 f"Acceptance must record its tested {key}.")
    _require(bool(acceptance.get("installer_setup_steps")), "The translated installation needs tested loading/startup directions.")
    console = acceptance.get("console", {})
    _require(set(console) == {"x", "y", "z"} and all(type(x) is int for x in console.values()),
             "A tested usable console position is required.")
    world = candidate / "world"
    inventory = file_inventory(world)
    _require(inventory_digest(inventory) == acceptance.get("world_inventory_sha256"),
             "The world files differ from the accepted runtime snapshot.")
    readback = validate_native_world(world)
    _require(readback.get("terrain_preserved") is True, "The complete build must preserve the grass and bedrock.")
    # A fixture with copied full-size labels must still fail. This deliberately
    # loose lower bound is independent of candidate metadata; it cannot certify
    # routing/timing, which require their separate recorded full-world gates.
    _require(readback["assigned_blocks"] >= (RAM_BYTES + CACHE_BYTES + FLASH_BYTES + VRAM_BYTES) * 8,
             "The saved block inventory is too small to contain the literal physical memories.")
    verify_full_world_acceptance(candidate, acceptance, commit=commit,
                                 world_digest=inventory_digest(inventory))
    bounds = machine_bounds(world)
    return {"acceptance": acceptance, "manifest": manifest, "world_files": inventory,
            "readback": readback, "bounds": bounds, "evidence": evidence}


def _source_files(root: Path, commit: str) -> dict[str, Path]:
    actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    _require(actual == commit, "Packaging checkout differs from the release commit.")
    _require(not subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], cwd=root),
             "Packaging requires a clean tracked checkout.")
    names = subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode().split("\0")
    return {f"ShamaOS-source/{name}": root / name for name in names if name}


def package_release(candidate: Path, output: Path, *, commit: str, tag: str, ci: dict,
                    ci_artifacts: Path, root: Path) -> dict:
    checked = validate_candidate(candidate, commit, ci)
    _require(not output.exists(), "Release output already exists; use a new directory.")
    source_files = _source_files(root, commit)
    _require(bool(list(ci_artifacts.glob("*.zip"))), "The existing CI artifacts must also be retained in the release.")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="shamaos-release-", dir=output.parent) as temp:
        stage = Path(temp) / "assets"
        stage.mkdir()
        acceptance = checked["acceptance"]
        world = candidate / "world"
        world_inventory = {f"ShamaOS/{name}": details for name, details in checked["world_files"].items()}
        world_zip = stage / "ShamaOS-world.zip"
        write_zip(world_zip, {f"ShamaOS/{name}": world / name for name in checked["world_files"]})
        schematic_path = stage / "ShamaOS.litematic"
        schematic_report = export_litematic(world, schematic_path, name=f"ShamaOS {tag}", bounds=checked["bounds"])
        source_path = stage / "ShamaOS-source-and-tools.zip"
        write_zip(source_path, source_files)
        evidence_files = {}
        evidence_files.update({name: candidate / name for name in checked["evidence"]})
        evidence_files.update({name: candidate / name for name in ("acceptance.json", "generated-manifest.json")})
        ci_path = Path(temp) / "ci.json"
        ci_path.write_text(json.dumps(ci, indent=2) + "\n", encoding="utf-8")
        evidence_files["ci.json"] = ci_path
        schematic_check = Path(temp) / "schematic-readback.json"
        schematic_check.write_text(json.dumps(schematic_report, indent=2) + "\n", encoding="utf-8")
        evidence_files["schematic-readback.json"] = schematic_check
        evidence_path = stage / "ShamaOS-validation.zip"
        write_zip(evidence_path, evidence_files)
        assets = {key: split_asset(path) for key, path in (("world", world_zip), ("schematic", schematic_path),
                  ("source", source_path), ("validation", evidence_path))}
        for path in sorted(ci_artifacts.glob("*.zip")):
            destination = stage / path.name
            _require(not destination.exists(), "A CI artifact name conflicts with a release file.")
            shutil.copyfile(path, destination)
            assets["ci-" + path.stem] = split_asset(destination)
        metadata = {"format": "shamaos-release-v1", "scope": "complete-machine", "status": "passed",
            "tag": tag, "source_commit": commit, "minecraft_version": "1.20.4", "data_version": 3700,
            "capacities": REQUIRED_CAPACITY, "assets": assets, "world_files": world_inventory,
            "world_inventory_sha256": inventory_digest(checked["world_files"]),
            "console": acceptance["console"], "runtime": acceptance["runtime"],
            "installer_setup_steps": acceptance["installer_setup_steps"], "schematic": schematic_report}
        manifest_path = stage / "release-manifest.json"
        manifest_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        guide = (root / "release" / "README.txt").read_text(encoding="utf-8")
        runtime = acceptance["runtime"]
        exact = (f"\nTHIS RELEASE'S TESTED SETTINGS\nVersion: {tag}\nMinecraft: Java 1.20.4\n"
                 f"Runtime: {runtime['name']} {runtime['version']}\n"
                 f"Simulation distance: {runtime['simulation_distance']} chunks\n"
                 f"Render distance: {runtime['render_distance']} chunks\n"
                 + "\n".join(runtime["setup_steps"]) + "\n")
        (stage / "README.txt").write_text(guide + exact, encoding="utf-8")
        installer_files = {name: root / "release" / name for name in (
            "Install-ShamaOS.py", "Start-Windows.cmd", "Start-Mac-Linux.command")}
        installer_files.update({"README.txt": stage / "README.txt", "release-manifest.json": manifest_path})
        for path in (root / "src" / "shamaos").rglob("*.py"):
            installer_files["src/" + path.relative_to(root / "src").as_posix()] = path
        installer_path = stage / "ShamaOS-installer.zip"
        write_zip(installer_path, installer_files)
        # Manifest outside the kit may add the kit descriptor; the kit's copy
        # intentionally has no recursive checksum of itself.
        assets["installer"] = split_asset(installer_path)
        manifest_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (stage / "SHA256SUMS.txt").write_text("".join(
            f"{sha256(path)}  {path.name}\n" for path in sorted(stage.iterdir()) if path.is_file()), encoding="ascii")
        _require(len(list(stage.iterdir())) < 1000, "The release exceeds GitHub's asset count limit.")
        _require(all(path.stat().st_size < 2 << 30 for path in stage.iterdir()), "A release asset exceeds GitHub's size limit.")
        stage.rename(output)
    return metadata


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--ci-report", required=True, type=Path)
    parser.add_argument("--ci-artifacts", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--tag", required=True)
    args = parser.parse_args(argv)
    try:
        package_release(args.candidate, args.output, commit=args.commit, tag=args.tag,
            ci=json.loads(args.ci_report.read_text(encoding="utf-8")), ci_artifacts=args.ci_artifacts,
            root=Path(__file__).resolve().parents[2])
        print(f"Verified release files are ready in {args.output}")
        return 0
    except (WorldWriteError, OSError, ValueError, KeyError) as exc:
        print(f"Release blocked: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
