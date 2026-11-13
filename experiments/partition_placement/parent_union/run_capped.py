"""Retain exact facade sources and inputs before a capped complete retry."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from experiments.partition_placement.port_egress_manifest import REQUIRED_SOURCE_BINDINGS
from experiments.partition_placement.targeted_clock_reserve import parse_domain_padding


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _write(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _sources():
    names = set(REQUIRED_SOURCE_BINDINGS) | {
        "experiments/partition_placement/packed_parent_union.py",
        "experiments/partition_placement/run_port_egress.py",
        "experiments/partition_placement/targeted_clock_reserve.py",
        "experiments/partition_placement/raised_exports/streamed_union.py",
        "experiments/partition_placement/parent_union/run_capped.py",
    }
    for name in tuple(names):
        for parent in (ROOT / name).parents:
            if parent == ROOT:
                break
            initializer = parent / "__init__.py"
            if initializer.is_file():
                names.add(initializer.relative_to(ROOT).as_posix())
    return {name: (ROOT / name).read_bytes() for name in sorted(names)}


def prepare(output, mapped, plan, *, top, padding, maximum_y, memory_mib, wall_seconds, domain_padding=None):
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    sources = _sources()
    inputs = {"inputs/" + mapped.name: mapped.read_bytes(), "inputs/plan.json": plan.read_bytes()}
    if len(inputs) != 2:
        raise ValueError("mapped input must have a different filename from plan.json")
    for name, data in inputs.items():
        destination = output / name
        destination.parent.mkdir(exist_ok=True)
        destination.write_bytes(data)
    mapped_name = next(name for name in inputs if name != "inputs/plan.json")
    configuration = {"mapped_input": mapped_name, "top": top, "memory_limit_mib": memory_mib,
        "wall_limit_seconds": wall_seconds, "clock_padding_blocks": padding, "maximum_y": maximum_y,
        "clock_domain_padding": {str(identity): blocks for identity, blocks in (domain_padding or {}).items()}}
    source_hashes = {name: _digest(data) for name, data in sources.items()}
    input_hashes = {name: _digest(data) for name, data in inputs.items()}
    inventory = {"schema": "shama-capped-facade-source-bundle-v1", "source_hashes": source_hashes,
                 "input_hashes": input_hashes, "python": sys.version, "configuration": configuration,
                 "scope": "Exact input and generator snapshot before execution; preparation is not geometry or runtime proof"}
    archive = output / "generator-sources.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for name, data in sorted({**sources, **inputs}.items()):
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            bundle.writestr(entry, data)
        bundle.writestr("SOURCE_INVENTORY.json", json.dumps(inventory, indent=2, sort_keys=True) + "\n")
    receipt = {**inventory, "status": "prepared-not-run", "command": _command(configuration, output),
        "working_directory": str(ROOT), "source_bundle_sha256": _digest(archive.read_bytes())}
    _validate(receipt, output)
    _write(output / "prepared.json", receipt)
    return receipt


def _command(configuration, output):
    mapped_name = configuration["mapped_input"]
    if (not isinstance(mapped_name, str) or Path(mapped_name).parts != ("inputs", Path(mapped_name).name)
            or mapped_name == "inputs/plan.json"):
        raise ValueError("retained mapped input must be one direct file in inputs/")
    for key in ("memory_limit_mib", "wall_limit_seconds", "clock_padding_blocks", "maximum_y"):
        if type(configuration[key]) is not int:
            raise ValueError("prepared resource and geometry limits must be exact integers")
    if not 128 <= configuration["memory_limit_mib"] <= 2048 or configuration["wall_limit_seconds"] < 1:
        raise ValueError("prepared limits exceed this experiment's explicit resource bounds")
    if not isinstance(configuration["top"], str) or not configuration["top"]:
        raise ValueError("prepared complete graph needs its exact top name")
    targeted = parse_domain_padding([f"{identity}={blocks}" for identity, blocks in configuration.get("clock_domain_padding", {}).items()],
                                    configuration["clock_padding_blocks"])
    return [sys.executable, str(ROOT / "experiments/partition_placement/run_port_egress.py"),
        "--mapped", str(output / mapped_name), "--top", configuration["top"],
        "--plan", str(output / "inputs/plan.json"), "--clock-padding", str(configuration["clock_padding_blocks"]),
        "--maximum-y", str(configuration["maximum_y"]), "--memory-limit-mib", str(configuration["memory_limit_mib"]),
        "--wall-seconds", str(configuration["wall_limit_seconds"]), "--literal-union", "--output", str(output / "report.json"),
        *(argument for identity, blocks in sorted(targeted.items()) for argument in ("--clock-domain-padding", f"{identity}={blocks}"))]


def _validate(receipt, output):
    _command(receipt["configuration"], output)
    sources = _sources()
    if set(receipt["source_hashes"]) != set(sources):
        raise ValueError("prepared generator source inventory is incomplete or changed")
    if set(receipt["input_hashes"]) != {receipt["configuration"]["mapped_input"], "inputs/plan.json"}:
        raise ValueError("prepared complete input inventory changed")
    for name, expected in receipt["source_hashes"].items():
        if _digest(sources[name]) != expected:
            raise ValueError(f"prepared generator source changed: {name}")
    for name, expected in receipt["input_hashes"].items():
        if _digest((output / name).read_bytes()) != expected:
            raise ValueError(f"retained complete input changed: {name}")
    if _digest((output / "generator-sources.zip").read_bytes()) != receipt["source_bundle_sha256"]:
        raise ValueError("retained complete source bundle changed")
    with zipfile.ZipFile(output / "generator-sources.zip") as bundle:
        inventory = json.loads(bundle.read("SOURCE_INVENTORY.json"))
        for key in ("schema", "source_hashes", "input_hashes", "python", "scope", "configuration"):
            if inventory[key] != receipt[key]:
                raise ValueError(f"prepared receipt differs from its retained source bundle: {key}")


def run(output):
    output = output.resolve()
    receipt = json.loads((output / "prepared.json").read_text())
    _validate(receipt, output)
    if (output / "process.json").exists() or (output / "report.json").exists():
        raise ValueError("prepared proof output was already used; create a fresh run directory")
    started, peak = time.monotonic(), 0
    log = output / "process.log"
    resources = output / "resources.jsonl"
    with log.open("x") as log_stream, resources.open("x") as resource_stream:
        process = subprocess.Popen(_command(receipt["configuration"], output), cwd=ROOT,
                                   stdout=log_stream, stderr=subprocess.STDOUT)
        while process.poll() is None:
            elapsed = time.monotonic() - started
            if elapsed > receipt["configuration"]["wall_limit_seconds"] + 30:
                process.kill()
                process.wait()
                break
            try:
                fields = {}
                for line in Path(f"/proc/{process.pid}/status").read_text().splitlines():
                    if line.startswith(("VmRSS:", "VmSize:", "VmHWM:")):
                        name, value, _ = line.split()
                        fields[name.rstrip(":")] = int(value)
                peak = max(peak, fields.get("VmHWM", 0), fields.get("VmRSS", 0))
                resource_stream.write(json.dumps({"elapsed_seconds": elapsed, "pid": process.pid, "kib": fields}) + "\n")
                resource_stream.flush()
            except FileNotFoundError:
                pass
            time.sleep(2)
        result = {"exit_code": process.returncode, "seconds": time.monotonic() - started,
                  "monitored_peak_rss_kib": peak, "complete_geometry_verified": False,
                  "runtime_verified": False, "whole_computer_verified": False}
    try:
        _validate(receipt, output)
        result["source_and_input_bindings_unchanged"] = True
    except ValueError as error:
        result.update(source_and_input_bindings_unchanged=False, binding_error=str(error))
    report_path = output / "report.json"
    if report_path.is_file():
        report = json.loads(report_path.read_text())
        result["driver_status"] = report.get("status")
        result["driver_report_sha256"] = _digest(report_path.read_bytes())
        result["driver_peak_rss_kib"] = report.get("peak_rss_kib")
        result["observed_peak_rss_kib"] = max(peak, report.get("peak_rss_kib", 0))
        result["complete_geometry_verified"] = bool(process.returncode == 0 and result["source_and_input_bindings_unchanged"]
            and report.get("status") == "complete-original-io-facade-geometry-passed-runtime-pending"
            and "literal_union" in report)
    result["status"] = ("complete-geometry-runtime-pending" if result["complete_geometry_verified"]
                        else "incomplete-or-rejected-see-driver-report")
    _write(output / "process.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-prepared", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--mapped", type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--top", default="shama_cache_adapter")
    parser.add_argument("--clock-padding", type=int, default=128)
    parser.add_argument("--clock-domain-padding", action="append", default=[], metavar="DOMAIN=ABSOLUTE_BLOCKS")
    parser.add_argument("--maximum-y", type=int, default=285)
    parser.add_argument("--memory-limit-mib", type=int, default=2048)
    parser.add_argument("--wall-seconds", type=int, default=1800)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    if args.run_prepared is not None:
        result = run(args.run_prepared)
    else:
        if args.output is None or args.mapped is None or args.plan is None:
            parser.error("preparation requires --output, --mapped and --plan")
        if not 128 <= args.memory_limit_mib <= 2048 or args.wall_seconds < 1:
            parser.error("this experiment permits 128 through 2048 MiB and a positive wall limit")
        receipt = prepare(args.output, args.mapped, args.plan, top=args.top, padding=args.clock_padding,
                          maximum_y=args.maximum_y, memory_mib=args.memory_limit_mib, wall_seconds=args.wall_seconds,
                          domain_padding=parse_domain_padding(args.clock_domain_padding, args.clock_padding))
        result = receipt if args.prepare_only else run(args.output)
    print(json.dumps({key: value for key, value in result.items() if key in
                     ("status", "exit_code", "seconds", "observed_peak_rss_kib", "complete_geometry_verified")}, indent=2))
    return 0 if result["status"] in ("prepared-not-run", "complete-geometry-runtime-pending") else 1


if __name__ == "__main__":
    raise SystemExit(main())
