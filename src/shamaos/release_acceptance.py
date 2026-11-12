"""Trust boundary between recorded test reports and actual runtime validation.

The fixture harness does not implement a complete-world acceptance run. No
complete-world validator is registered yet. This intentionally keeps publication
closed even if somebody supplies plausible JSON, hashes and a large block file.
A future validator must be implemented and reviewed in this repository; neither
a CLI flag, an environment variable nor candidate JSON can register one.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from .worldio import WorldWriteError


_FULL_WORLD_VALIDATORS: dict[str, Callable] = {}


def verify_full_world_acceptance(candidate: Path, acceptance: dict, *, commit: str,
                                 world_digest: str) -> None:
    runner = acceptance.get("runner")
    for gate, result in acceptance.get("gates", {}).items():
        for record in result.get("evidence", []):
            path = candidate / record["path"]
            # Human notes and screenshots may be extra evidence, but cannot
            # replace an actual machine-readable trace report for each gate.
            if path.suffix != ".json":
                continue
            try:
                report = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise WorldWriteError(f"Invalid {gate} acceptance report") from exc
            if (not isinstance(report, dict) or report.get("format") != "shamaos-full-world-trace-v1"
                    or report.get("source_commit") != commit or report.get("world_inventory_sha256") != world_digest
                    or report.get("scope") != "complete-machine" or report.get("runner") != runner
                    or gate not in report.get("gates", [])):
                raise WorldWriteError(f"The {gate} evidence is not a source/world-bound complete-machine trace.")
            break
        else:
            raise WorldWriteError(f"The {gate} gate has no complete-machine trace report.")
    validator = _FULL_WORLD_VALIDATORS.get(runner)
    if validator is None:
        raise WorldWriteError(
            "Complete-world publication is closed: no supported full-world acceptance validator exists yet. "
            "Recorded pass labels, fixture results and file hashes cannot certify global timing, boot, "
            "screen usability, loading or application operation. See docs/RELEASES.md.")
    validator(candidate, acceptance, commit=commit, world_digest=world_digest)
