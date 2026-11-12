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
from shamaos.worldio import WorldWriteError


def build_candidate(destination: Path, *, commit: str, ci: dict, config_path: Path) -> dict:
    validate_ci(ci, commit)
    root = Path(__file__).resolve().parents[1]
    if subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip() != commit:
        raise WorldWriteError("The build checkout is not the release commit.")
    if subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], cwd=root):
        raise WorldWriteError("Full-world release generation needs a clean tracked checkout.")
    if destination.exists():
        raise WorldWriteError("Use a new candidate folder; existing candidates are never overwritten.")
    destination.mkdir(parents=True)
    config = load_config(config_path)
    preflight = generation_preflight(config, world_path=destination / "world")
    write_manifest(destination / "generation-preflight.json", preflight)
    if preflight["capacity_contract"]["status"] != "passed":
        raise WorldWriteError("Full-world preflight failed the literal capacity contract.")
    if not preflight["storage_minimum_fits_available_disk"]:
        raise WorldWriteError("Full-world preflight needs more disk even for storage alone.")
    # This invokes synthesis and mapped routing preflight BEFORE the native world
    # is created. The current global router stops here on its actual disk bound.
    plan = build_plan(config)
    result = write_available_placements(destination / "world", config, plan)
    generated = manifest(config, plan)
    generated.update(physical_generation_status="generated-topology-readback-verified",
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--ci-report", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=Path("configs/default.toml"))
    args = parser.parse_args()
    try:
        build_candidate(args.candidate, commit=args.commit,
            ci=json.loads(args.ci_report.read_text(encoding="utf-8")), config_path=args.config)
        return 0
    except (WorldWriteError, OSError, ValueError, KeyError) as exc:
        print(f"Full-world release blocked: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
