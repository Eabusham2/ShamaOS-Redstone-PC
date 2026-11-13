"""Compare complete-record persistence against the immutable 32 KiB COW baseline.

Run from a development checkout with Icarus Verilog installed:
    python tools/benchmark_history_save.py

Both controllers receive the same complete 250 KiB image, unaligned 128-byte
source and one-in-three-cycle DMA stall schedule. The larger image is necessary
for the old whole-history replacement extent to fit below its 95% guard.
This measures controller cycles/transactions, not Minecraft wall-clock speed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from shamaos.flashfs import ShamaFS
from shamaos.os_image import build_default_os_image, render_boot_parameters
from test_history_journal import expected_history, run_history

BASELINE = "4e933a5b6f37894cc333dde3d57d10e3e78de047"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "build/history-journal-benchmark")
    args = parser.parse_args()
    folder = args.output.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    image = build_default_os_image(250 << 10)
    bench = (ROOT / "tests/rtl/tb_history_journal.sv").read_text()
    # Keep the common persisted-record and all-data checks. Only these two
    # new-implementation-specific assertions differ for the legacy baseline.
    replacements = {
        '            if(flash_writes-writes_before!=264) $fatal(1,"record write amplification %0d",flash_writes-writes_before);\n': "",
        '            if(flash[(`SHAMA_FS_TABLE_FLASH_OFFSET+`SHAMA_MINER_HISTORY_ENTRY*64+8)>>2]!=history_start)\n                $fatal(1,"history extent moved");':
        '            history_start=flash[(`SHAMA_FS_TABLE_FLASH_OFFSET+`SHAMA_MINER_HISTORY_ENTRY*64+8)>>2];',
    }
    for old, new in replacements.items():
        if bench.count(old) != 1:
            raise RuntimeError("history bench changed; review the matched baseline adaptation")
        bench = bench.replace(old, new)
    baseline = subprocess.check_output(
        ["git", "show", f"{BASELINE}:rtl/shama_fs_accel.sv"], cwd=ROOT, text=True,
    )
    current = (ROOT / "rtl/shama_fs_accel.sv").read_text()
    report = {
        "fixture": "same250KiBinstalledimage,unaligned128byterecord,1in3DMAwait",
        "baseline_commit": BASELINE,
        "image_sha256": hashlib.sha256(image.image).hexdigest(),
        "bench_sha256": hashlib.sha256(bench.encode()).hexdigest(),
        "results": {},
    }
    before = ShamaFS.deserialize(image.image)
    for label, source in (("old32KiBcow", baseline), ("new128Byteredojournal", current)):
        directory = folder / label
        directory.mkdir(exist_ok=True)
        (directory / "shama_boot_params.svh").write_text(render_boot_parameters(image))
        (directory / "fs.sv").write_text(source)
        (directory / "bench.sv").write_text(bench)
        binary = directory / "bench.vvp"
        compiled = subprocess.run(
            ["iverilog", "-g2012", "-I", str(directory), "-s", "tb_history_journal",
             "-o", str(binary), str(directory / "fs.sv"), str(directory / "bench.sv")],
            capture_output=True, text=True, timeout=30,
        )
        if compiled.returncode:
            raise RuntimeError(compiled.stdout + compiled.stderr)
        result = ShamaFS.deserialize(run_history(binary, image.image, directory))
        if result.read_file("miner-history.log") != expected_history(before.read_file("miner-history.log")):
            raise AssertionError(f"{label}: persisted record mismatch")
        for entry in before.list_files():
            if entry.name != "miner-history.log" and result.stat(entry.name) != entry:
                raise AssertionError(f"{label}: unrelated file changed: {entry.name}")
        output = (directory / "output.txt").read_text()
        match = re.search(
            r"HISTORY SAVE METRICS slot=(\d+) bytes=(\d+) cycles=(\d+) reads=(\d+) writes=(\d+)",
            output,
        )
        if match is None:
            raise RuntimeError(f"{label}: missing metrics: {output}")
        metrics = dict(zip(("slot", "bytes", "cycles", "reads", "writes"), map(int, match.groups())))
        metrics["source_sha256"] = hashlib.sha256(source.encode()).hexdigest()
        report["results"][label] = metrics
        print(label, metrics, flush=True)
    (folder / "comparison.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
