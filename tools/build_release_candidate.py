"""Build the real full-capacity world, then enforce its outstanding runtime gate.

This path never substitutes CI's small fixture when the complete generator fails.
It leaves diagnostic manifests in the ignored build directory on failure.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

from shamaos.generator import (
    build_plan, generation_preflight, load_config, manifest, write_available_placements, write_manifest,
)
from shamaos.release import validate_candidate, validate_ci
from shamaos.worldio import WorldWriteError, resolve_world_surface


def _require_native_readback(report: dict, config: dict) -> None:
    surface = config["world"]["surface_y"]
    if (not isinstance(report, dict) or report.get("status") != "passed"
            or report.get("method") != "independent-anvil-block-state-readback"
            or report.get("data_version") != config["world"]["data_version"]
            or report.get("terrain_preserved") is not True or report.get("grass_surface_y") != surface
            or report.get("minimum_build_y") != surface + 1
            or any(type(report.get(key)) is not int or report[key] <= 0
                   for key in ("assigned_blocks", "chunks", "regions", "region_bytes"))):
        raise WorldWriteError("Full-world generation did not return a complete, passing native Anvil readback.")


def build_candidate(destination: Path, *, commit: str, ci: dict, config_path: Path,
                    logic_backend: str | None = None) -> dict:
    validate_ci(ci, commit)
    root = Path(__file__).resolve().parents[1]
    if subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip() != commit:
        raise WorldWriteError("The build checkout is not the release commit.")
    if subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], cwd=root):
        raise WorldWriteError("Full-world release generation needs a clean tracked checkout.")
    if destination.exists():
        raise WorldWriteError("Use a new candidate folder; existing candidates are never overwritten.")
    config = resolve_world_surface(load_config(config_path), destination / "world")
    # The workflow selects bounded explicitly; direct callers keep their config's
    # backend unless they request an override. All later stages see this same
    # effective configuration, including the manifest's configuration digest.
    generator = dict(config.get("generator", {}))
    backend = generator.get("logic_backend", "flat") if logic_backend is None else logic_backend
    if backend not in ("flat", "bounded"):
        raise WorldWriteError("Logic backend must be 'flat' or 'bounded'.")
    if generator.get("validate_after_write", True) is not True:
        raise WorldWriteError("Release generation requires generator.validate_after_write = true.")
    generator["logic_backend"] = backend
    config = {**config, "generator": generator}
    destination.mkdir(parents=True)
    preflight = generation_preflight(config, world_path=destination / "world")
    preflight["logic_backend"] = backend
    write_manifest(destination / "generation-preflight.json", preflight)
    if preflight["capacity_contract"]["status"] != "passed":
        raise WorldWriteError("Full-world preflight failed the literal capacity contract.")
    if not preflight["storage_minimum_fits_available_disk"]:
        raise WorldWriteError("Full-world preflight needs more disk even for storage alone.")
    # This invokes synthesis and mapped routing preflight BEFORE the native world
    # is created. Either backend must pass its actual resource and routing gates.
    plan = build_plan(config)
    result = write_available_placements(destination / "world", config, plan)
    _require_native_readback(result.world_validation, config)
    generated = manifest(config, plan)
    generated.update(logic_backend=backend,
        physical_generation_status="generated-topology-readback-verified",
        written_placements=result.written_blocks, system_build=result.system_metadata,
        stage_counts=result.stage_counts, world_validation=result.world_validation,
        runtime_validation={"status": "not-run", "scope": "complete-machine"})
    write_manifest(destination / "generated-manifest.json", generated)
    if not (destination / "acceptance.json").is_file():
        raise WorldWriteError(
            "The real full world was generated and read back, but complete-world runtime, routing/timing, "
            "screen/controls, loading, app persistence and relocation acceptance are still missing. "
            "No complete-world release files were packaged. See docs/RELEASES.md.")
    return validate_candidate(destination, commit, ci)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--ci-report", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=Path("configs/default.toml"))
    parser.add_argument("--logic-backend", choices=("flat", "bounded"),
        help="Override the configuration's logic backend for a new candidate.")
    args = parser.parse_args(argv)
    try:
        build_candidate(args.candidate, commit=args.commit,
            ci=json.loads(args.ci_report.read_text(encoding="utf-8")), config_path=args.config,
            logic_backend=args.logic_backend)
        return 0
    except (WorldWriteError, OSError, ValueError, KeyError) as exc:
        print(f"Full-world release blocked: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
