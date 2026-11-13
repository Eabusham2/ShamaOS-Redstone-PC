"""Stream a source-bound saved partition through its original-I/O clearance check."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPOSITORY), str(REPOSITORY / "src")]

from experiments.partition_placement.audit_port_egress import audit_south_corridors, south_corridors
from experiments.partition_placement.raised_exports.replay_manifest import read_manifest, restore_manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    started = time.monotonic()
    manifest = read_manifest(args.manifest, args.sha256)
    provenance = manifest["provenance"]
    plan_bytes = args.plan.read_bytes()
    if hashlib.sha256(plan_bytes).hexdigest() != provenance.get("plan_sha256"):
        raise ValueError("plan bytes differ from the retained physical manifest")
    sources = ["src/shamaos/model.py", "src/shamaos/synthesis.py",
               "src/shamaos/hardware/logic.py", "src/shamaos/hardware/memory.py",
               "src/shamaos/hardware/routing.py", "src/shamaos/hardware/local_router.py",
               "src/shamaos/hardware/external_router.py",
               "experiments/partition_placement/raised_exports/replay_manifest.py"]
    source_hashes = {name: hashlib.sha256((REPOSITORY / name).read_bytes()).hexdigest() for name in sources}
    for name, digest in source_hashes.items():
        if provenance["source_hashes"].get(name) != digest:
            raise ValueError(f"physical emission source differs from the retained manifest: {name}")
    placed = restore_manifest(manifest)
    plan = json.loads(plan_bytes)
    corridors = south_corridors(plan["entities"], placed.ports, placed.bounds.max.z)
    report = audit_south_corridors(corridors, placed.iter_placements())
    report.update(manifest_sha256=args.sha256, plan_sha256=provenance["plan_sha256"],
                  emission_source_hashes=source_hashes,
                  audit_source_sha256=hashlib.sha256((Path(__file__).parent / "audit_port_egress.py").read_bytes()).hexdigest(),
                  driver_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  seconds=time.monotonic() - started)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: report[k] for k in ("status", "original_ports", "blocked_ports", "scanned_emitted_blocks", "seconds")}), flush=True)
    return 0 if report["status"] == "clearance-passed-runtime-unverified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
