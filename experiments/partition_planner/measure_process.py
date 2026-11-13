"""Measure a complete child process with the kernel resource data used by time."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time

parser = argparse.ArgumentParser()
parser.add_argument("measurement", type=Path)
parser.add_argument("output", type=Path)
parser.add_argument("command", nargs=argparse.REMAINDER)
args = parser.parse_args()
started = time.monotonic()
with args.output.open("w") as stdout, args.measurement.with_suffix(".stderr.txt").open("w") as stderr:
    process = subprocess.Popen(args.command, stdout=stdout, stderr=stderr)
    _, status, usage = os.wait4(process.pid, 0)
    process.returncode = os.waitstatus_to_exitcode(status)
report = {"method": "external parent os.wait4 kernel rusage after complete child exit",
          "elapsed_seconds": time.monotonic() - started, "max_rss_kib": usage.ru_maxrss,
          "user_seconds": usage.ru_utime, "system_seconds": usage.ru_stime,
          "exit_status": process.returncode, "command": args.command}
args.measurement.write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps(report, indent=2))
raise SystemExit(process.returncode)
