"""Reassemble complete app sources and measure the physical-cache prefix."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from shamaos.assembler import assemble
from shamaos.machine_spec import CACHE_BYTES
from shamaos.memory_map import APP_BACKING_BYTES
from shamaos.os_image import APP_IDS


def measure(baseline: str) -> dict:
    revision = subprocess.check_output(
        ["git", "rev-parse", "--verify", baseline + "^{commit}"], cwd=ROOT, text=True,
    ).strip()
    apps = {}
    for app in APP_IDS:
        path = f"src/shamaos/firmware/{app}.asm"
        old = subprocess.check_output(["git", "show", f"{revision}:{path}"], cwd=ROOT)
        new = (ROOT / path).read_bytes()
        before = len(assemble(old.decode()).to_bytes())
        after = len(assemble(new.decode()).to_bytes())
        apps[app] = {
            "before_bytes": before, "after_bytes": after,
            "bytes_saved": before - after,
            "reduction_percent": 100 * (before - after) / before,
            "cached_bytes": min(after, CACHE_BYTES),
            "ram_tail_bytes": max(0, after - CACHE_BYTES),
            "source_sha256": hashlib.sha256(new).hexdigest(),
            "baseline_source_sha256": hashlib.sha256(old).hexdigest(),
        }
    return {
        "baseline_commit": revision,
        "physical_cache_after_bytes": CACHE_BYTES,
        "execution_window_bytes": APP_BACKING_BYTES,
        "apps": apps,
        "all_app_bytes_before": sum(app["before_bytes"] for app in apps.values()),
        "all_app_bytes_after": sum(app["after_bytes"] for app in apps.values()),
        "scope": "Exact assembled executables; complete sources retained; physical cache "
                 "holds a prefix and existing RAM backs all executable bytes.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default="184faa9d28d871b77ff4de73c022b620d57536a7")
    parser.add_argument("--report", type=Path, default=Path("build/app-cache-reduction.json"))
    args = parser.parse_args()
    result = measure(args.baseline)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + "\n")
    print(f"All app executables: {result['all_app_bytes_before']:,} -> "
          f"{result['all_app_bytes_after']:,} bytes; physical cache {CACHE_BYTES:,} bytes; "
          f"execution window {APP_BACKING_BYTES:,} bytes")


if __name__ == "__main__":
    main()
