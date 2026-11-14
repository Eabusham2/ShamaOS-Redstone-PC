"""Measure source-only Editor/Files savings against an immutable git revision."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from shamaos.assembler import assemble


def measure(baseline: str) -> dict:
    commit = subprocess.check_output(["git", "rev-parse", baseline], cwd=ROOT, text=True).strip()
    apps = {}
    for app in ("editor", "files"):
        path = f"src/shamaos/firmware/{app}.asm"
        old = subprocess.check_output(["git", "show", f"{commit}:{path}"], cwd=ROOT)
        new = (ROOT / path).read_bytes()
        before = len(assemble(old.decode()).to_bytes())
        after = len(assemble(new.decode()).to_bytes())
        apps[app] = {
            "baseline_source_sha256": hashlib.sha256(old).hexdigest(),
            "candidate_source_sha256": hashlib.sha256(new).hexdigest(),
            "baseline_source_bytes": len(old),
            "candidate_source_bytes": len(new),
            "baseline_executable_bytes": before,
            "candidate_executable_bytes": after,
            "executable_bytes_saved": before - after,
            "executable_reduction_percent": 100 * (before - after) / before,
        }
    return {"baseline_commit": commit, "apps": apps}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default="184faa9d28d871b77ff4de73c022b620d57536a7")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    text = json.dumps(measure(args.baseline), indent=2) + "\n"
    if args.output:
        args.output.write_text(text)
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
