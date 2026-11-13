"""Measure the exact AIG mapping and native templates, without placing routes.

Prepare with --prepare-only before reserving a large-process slot. Resume with
--run-prepared after reserving it. Each large JSON is counted in a separate
process so its memory is released before the next phase.
"""
from __future__ import annotations

import argparse
from array import array
from collections import Counter, deque
import hashlib
from itertools import chain
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

if not __debug__:
    raise SystemExit("assembler size measurement requires Python assertions")

ROOT = Path(__file__).resolve().parents[2]
BASELINE = "c3315eb35b3d52c1828e0d5c0c93dc0139ebd6e9"
TOP = "shama_asm_accel"
TOOLS = (
    "src/shamaos/synthesis.py", "src/shamaos/system_build.py",
    "src/shamaos/hardware/logic.py", "src/shamaos/hardware/memory.py",
    "src/shamaos/model.py", "synth/redstone_aig.ys",
    "experiments/assembler_operand_sharing/measure_size.py",
)


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def save(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n")


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=ROOT)


def macros(source: bytes, header: bytes) -> dict[str, str]:
    result = {}
    for name in sorted(set(re.findall(rb"`(SHAMA_\w+)", source))):
        found = re.search(rb"^`define\s+" + name + rb"\s+([^\n]+)", header, re.M)
        assert found is not None, ("missing boot macro", name)
        result[name.decode()] = found.group(1).decode()
    return result


def native_template_counts(kind: str) -> dict[str, object]:
    from shamaos.hardware.logic import template_for
    from shamaos.model import Vec3

    assert Path(template_for.__code__.co_filename).resolve() == ROOT / "src/shamaos/hardware/logic.py", "wrong checkout supplies native templates"
    template = template_for(kind)
    emitted = list(template.emitter(Vec3(0, 64, 0), "operand-sharing-count"))
    unique = {}
    for placement in emitted:
        previous = unique.setdefault(placement.pos, placement.block)
        assert previous == placement.block, ("template state conflict", kind, placement.pos)
    canonical = [
        [p.x, p.y, p.z, state.name, list(state.properties)]
        for p, state in sorted(unique.items(), key=lambda item: (item[0].x, item[0].y, item[0].z))
    ]
    return {
        "native_template": template.cell_type,
        "emitted_placements": len(emitted), "unique_blocks": len(unique),
        "literal_sha256": hashlib.sha256(json.dumps(canonical, separators=(",", ":")).encode()).hexdigest(),
    }


def validate_directions(cell: dict) -> None:
    if "port_directions" in cell:
        expected = {pin: "output" if pin in ("Q", "Y") else "input"
                    for pin in cell["connections"]}
        assert cell["port_directions"] == expected, "cell port directions disagree with native pin roles"


def logical_stats(module: dict) -> dict[str, object]:
    """Unit gate depth and fanout only; DFF Q and primary inputs start at zero.

    Linked adjacency arrays keep overhead bounded; neither these depths nor
    fanouts include redstone repeater delay, physical wire length or skew.
    """
    from shamaos.hardware.logic import template_for

    cells, ports = module["cells"], module.get("ports", {})
    max_bit = max(chain(
        [1], (bit for port in ports.values() for bit in port["bits"] if isinstance(bit, int)),
        (bit for cell in cells.values() for bits in cell["connections"].values()
         for bit in bits if isinstance(bit, int)),
    ))
    size = max_bit + 1
    driver = bytearray(size)  # 0 absent, 1 input/DFF Q, 2 combinational output
    head = array("i", [-1]) * size
    waiting = bytearray(size)
    depth = array("I", [0]) * size
    all_fanout = array("I", [0]) * size
    data_fanout = array("I", [0]) * size
    targets, links = array("i"), array("i")
    roots = deque()
    endpoints = []
    constant_uses = Counter()
    combinational_outputs = 0

    def sink(bit, *, clock=False):
        if isinstance(bit, int):
            all_fanout[bit] += 1
            if not clock:
                data_fanout[bit] += 1
        else:
            assert bit in ("0", "1"), ("unresolved/unsupported constant", bit)
            constant_uses[bit] += 1

    for port in ports.values():
        assert port["direction"] in ("input", "output")
        for bit in port["bits"]:
            if port["direction"] == "input":
                assert isinstance(bit, int), "noninteger primary input"
                if not driver[bit]:
                    driver[bit] = 1
                    roots.append(bit)
            else:
                sink(bit)
                if isinstance(bit, int):
                    endpoints.append(bit)
    for name, cell in cells.items():
        template = template_for(cell["type"])
        validate_directions(cell)
        connections = cell["connections"]
        assert set(connections) == set(template.pins), ("cell pin mismatch", name)
        assert all(len(bits) == 1 for bits in connections.values()), ("nonscalar native cell", name)
        sequential = template.cell_type == "DFF"
        output_pin = "Q" if sequential else "Y"
        output = connections[output_pin][0]
        assert isinstance(output, int) and not driver[output], ("multiple/constant output driver", name)
        driver[output] = 1 if sequential else 2
        if sequential:
            roots.append(output)
            if isinstance(connections["D"][0], int):
                endpoints.append(connections["D"][0])
        else:
            combinational_outputs += 1
        for pin, values in connections.items():
            if pin == output_pin:
                continue
            bit = values[0]
            sink(bit, clock=sequential and pin == "C")
            if sequential:
                continue
            if isinstance(bit, int):
                waiting[output] += 1
                targets.append(output)
                links.append(head[bit])
                head[bit] = len(targets) - 1
        if not sequential and waiting[output] == 0:
            depth[output] = 1
            roots.append(output)
    assert all(driver[bit] for bit, count in enumerate(all_fanout) if count), "undriven net"
    visited_comb = 0
    while roots:
        bit = roots.popleft()
        if driver[bit] == 2:
            visited_comb += 1
        edge = head[bit]
        while edge >= 0:
            target = targets[edge]
            depth[target] = max(depth[target], depth[bit] + 1)
            waiting[target] -= 1
            if waiting[target] == 0:
                roots.append(target)
            edge = links[edge]
    assert visited_comb == combinational_outputs, "combinational cycle in lowered mapping"
    return {
        "scope": "unit native-gate depth between primary inputs/DFF Q and DFF D/top outputs; no route or redstone timing",
        "combinational_cells": combinational_outputs,
        "maximum_unit_gate_depth": max(depth, default=0),
        "maximum_endpoint_unit_gate_depth": max((depth[bit] for bit in endpoints), default=0),
        "maximum_fanout_including_clocks": max(all_fanout, default=0),
        "maximum_fanout_excluding_DFF_clock_pins": max(data_fanout, default=0),
        "constant_input_uses": dict(sorted(constant_uses.items())),
    }


def count_mapping(path: Path, output: Path) -> dict:
    from shamaos.synthesis import _expand_sequential_cells

    assert Path(_expand_sequential_cells.__code__.co_filename).resolve() == ROOT / "src/shamaos/synthesis.py", "wrong checkout supplies sequential lowering"
    before = sha(path)
    with path.open() as stream:
        raw = json.load(stream)
    assert str(raw["creator"]).startswith("Yosys 0.33 "), "mapping must come from Yosys 0.33"
    assert set(raw["modules"]) == {TOP}, "unexpected modules in mapped partition"
    module = raw["modules"][TOP]
    # Check supplied raw directions before lowering can replace an enabled or
    # resettable register with freshly described native cells.
    for cell in module["cells"].values():
        validate_directions(cell)
    original = Counter(cell["type"] for cell in module["cells"].values())
    _expand_sequential_cells(module)
    lowered = Counter(cell["type"] for cell in module["cells"].values())
    templates = {kind: native_template_counts(kind) for kind in sorted(lowered)}
    report = {
        "status": "counted", "top": TOP, "mapped_json_sha256": before,
        "mapped_json_bytes": path.stat().st_size, "creator": raw["creator"],
        "raw_cells": sum(original.values()), "raw_cell_types": dict(sorted(original.items())),
        "lowered_native_cells": sum(lowered.values()), "native_cell_types": dict(sorted(lowered.items())),
        "templates": templates,
        "literal_cell_placements": sum(count * templates[kind]["emitted_placements"] for kind, count in lowered.items()),
        "literal_unique_cell_blocks": sum(count * templates[kind]["unique_blocks"] for kind, count in lowered.items()),
        "logical_stats": logical_stats(module),
        "excluded": ["intercell wiring", "port terminals", "constant source blocks", "placement spacing", "all other partitions"],
        "whole_machine_chunks": None, "physical_clock_speed_verified": False,
    }
    assert sha(path) == before, "mapped JSON changed during counting"
    save(output, report)
    return report


def measured(command: list[str], output: Path, *, cwd: Path) -> dict:
    if sys.platform not in ("linux", "darwin") or not hasattr(os, "wait4"):
        raise SystemExit("whole-process measurement requires Linux or macOS os.wait4")
    started = time.monotonic()
    with output.with_suffix(".stdout.txt").open("w") as stdout, output.with_suffix(".stderr.txt").open("w") as stderr:
        process = subprocess.Popen(command, cwd=cwd, stdout=stdout, stderr=stderr)
        _, status, usage = os.wait4(process.pid, 0)
        process.returncode = os.waitstatus_to_exitcode(status)
    report = {
        "method": "external parent os.wait4 after complete child exit", "platform": sys.platform,
        "elapsed_seconds": time.monotonic() - started,
        "peak_rss_kib": usage.ru_maxrss / 1024 if sys.platform == "darwin" else usage.ru_maxrss,
        "user_seconds": usage.ru_utime, "system_seconds": usage.ru_stime,
        "returncode": process.returncode, "command": command,
    }
    save(output, report)
    assert process.returncode == 0, ("measured phase failed", output, process.returncode)
    return report


def prepare(args) -> dict:
    from shamaos.synthesis import _portable_mapping, _quote_yosys
    from shamaos.system_build import SOC_RTL_FILES, partition_mapping_mode

    assert Path(_portable_mapping.__code__.co_filename).resolve() == ROOT / "src/shamaos/synthesis.py", "wrong checkout supplies synthesis"
    assert Path(partition_mapping_mode.__code__.co_filename).resolve() == ROOT / "src/shamaos/system_build.py", "wrong checkout supplies partition settings"
    assert partition_mapping_mode(TOP) == "aig"
    root = args.output
    root.mkdir(parents=True, exist_ok=False)
    provenance = json.loads(args.baseline_provenance.read_text())
    artifact_ref = provenance["base_commit"]
    baseline = git("rev-parse", args.baseline).decode().strip()
    candidate = git("rev-parse", "HEAD").decode().strip()
    source_path = "rtl/shama_asm_accel.sv"
    header_path = "rtl/shama_boot_params.svh"
    assert git("show", f"{artifact_ref}:synth/redstone_aig.ys") == (ROOT / "synth/redstone_aig.ys").read_bytes(), "mapping script differs from artifact"
    version = subprocess.check_output([args.yosys, "-V"], text=True).strip()
    assert version.startswith("Yosys 0.33 "), ("wrong synthesis version", version)
    source_hashes = {}
    input_files = list(SOC_RTL_FILES) + [str(path.relative_to(ROOT)) for path in sorted((ROOT / "rtl").glob("*.svh"))]
    for name in input_files:
        source = ROOT / name
        target = root / "frozen" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        source_hashes[name] = sha(source)
    for name in TOOLS:
        source_hashes[name] = sha(ROOT / name)
    preprocessed = {}
    for label, ref in (("artifact-source", artifact_ref), ("baseline", baseline), ("candidate", None)):
        folder = root / "preprocess" / label
        folder.mkdir(parents=True)
        source = git("show", f"{ref}:{source_path}") if ref else (ROOT / source_path).read_bytes()
        header = git("show", f"{ref}:{header_path}") if ref else (ROOT / header_path).read_bytes()
        (folder / "shama_asm_accel.sv").write_bytes(source)
        (folder / "shama_boot_params.svh").write_bytes(header)
        subprocess.run(["iverilog", "-E", "-g2012", "-I", str(folder), "-o", str(folder / "preprocessed.sv"),
                        str(folder / "shama_asm_accel.sv")], cwd=folder, check=True, capture_output=True)
        preprocessed[label] = {
            "source_sha256": hashlib.sha256(source).hexdigest(), "header_sha256": hashlib.sha256(header).hexdigest(),
            "preprocessed_sha256": sha(folder / "preprocessed.sv"), "effective_macros": macros(source, header),
        }
    original = provenance["snapshots"]["artifact-source"]
    assert preprocessed["artifact-source"]["source_sha256"] == original["shama_asm_accel.sv_sha256"]
    assert preprocessed["artifact-source"]["preprocessed_sha256"] == original["preprocessed_sha256"]
    assert preprocessed["baseline"]["preprocessed_sha256"] == original["preprocessed_sha256"]
    assert preprocessed["baseline"]["effective_macros"] == preprocessed["candidate"]["effective_macros"]
    mapping = _portable_mapping((ROOT / "synth/redstone_aig.ys").read_text(), fast=False)
    mapped = root / "candidate-mapped.json"
    script = "\n".join([
        "read_verilog -sv -I " + _quote_yosys(root / "frozen/rtl") + " " + " ".join(_quote_yosys(root / "frozen" / name) for name in SOC_RTL_FILES),
        f"hierarchy -check -top {TOP}", mapping, f"write_json {_quote_yosys(mapped)}", "",
    ])
    script_path = root / "candidate.ys"
    script_path.write_text(script)
    report = {
        "status": "prepared-not-synthesized", "top": TOP, "mapping_mode": "aig",
        "baseline_commit": baseline, "candidate_commit": candidate, "yosys_version": version, "yosys": args.yosys,
        "source_hashes": source_hashes, "preprocessing": preprocessed,
        "synthesis_script_sha256": sha(script_path), "rtl_files_in_order": list(SOC_RTL_FILES),
        "baseline_netlist": str(args.baseline_netlist),
        "baseline_provenance": str(args.baseline_provenance), "baseline_provenance_sha256": sha(args.baseline_provenance),
        "expected_baseline_json_sha256": provenance["mapped_json_sha256"],
        "expected_baseline_json_bytes": provenance["mapped_json_bytes"],
        "expected_baseline_lowered_native_cells": provenance["resources"]["cells"],
        "expected_baseline_literal_cell_placements": provenance["resources"]["literal_cell_placements"],
        "baseline_artifact_ci_run": provenance["ci_run_id"],
    }
    save(root / "prepared.json", report)
    return report


def verify_prepared(root: Path, report: dict) -> None:
    for name, expected in report["source_hashes"].items():
        assert sha(ROOT / name) == expected, ("source changed since preparation", name)
        frozen = root / "frozen" / name
        if frozen.exists():
            assert sha(frozen) == expected, ("frozen synthesis input changed", name)
    assert sha(root / "candidate.ys") == report["synthesis_script_sha256"]
    assert sha(Path(report["baseline_provenance"])) == report["baseline_provenance_sha256"]
    version = subprocess.check_output([report["yosys"], "-V"], text=True).strip()
    assert version == report["yosys_version"], "synthesis tool changed"


def run_prepared(root: Path, prepared: dict) -> None:
    verify_prepared(root, prepared)
    baseline = Path(prepared["baseline_netlist"])
    assert baseline.stat().st_size == prepared["expected_baseline_json_bytes"]
    assert sha(baseline) == prepared["expected_baseline_json_sha256"], "retained baseline netlist mismatch"
    for name in ("candidate-mapped.json", "size-comparison.json", "synthesis-resources.json"):
        assert not (root / name).exists(), ("refusing stale measurement output", name)
    resources = {}
    print("Synthesizing frozen candidate with exact production AIG flow", flush=True)
    resources["synthesis"] = measured([prepared["yosys"], "-q", "-s", str(root / "candidate.ys")],
                                      root / "synthesis-resources.json", cwd=ROOT)
    reports = {}
    for label, mapped in (("baseline", baseline), ("candidate", root / "candidate-mapped.json")):
        print(f"Counting {label} after production sequential lowering", flush=True)
        count_path = root / f"{label}-counts.json"
        resources[label] = measured([sys.executable, str(Path(__file__).resolve()), "--count", str(mapped), "--output", str(count_path)],
                                    root / f"{label}-count-resources.json", cwd=ROOT)
        reports[label] = json.loads(count_path.read_text())
    before, after = reports["baseline"], reports["candidate"]
    assert before["lowered_native_cells"] == prepared["expected_baseline_lowered_native_cells"]
    assert before["literal_cell_placements"] == prepared["expected_baseline_literal_cell_placements"]
    assert before["creator"] == after["creator"] == prepared["yosys_version"], "mapped Yosys provenance differs"
    verify_prepared(root, prepared)
    comparison = {
        "status": "passed", "scope": "same-flow logical mapping and exact native cell templates, excluding wiring",
        "provenance": prepared, "baseline": before, "candidate": after,
        "delta": {
            field: {"baseline": before[field], "candidate": after[field], "saved": before[field] - after[field],
                    "reduction_percent": 100 * (before[field] - after[field]) / before[field]}
            for field in ("lowered_native_cells", "literal_cell_placements", "literal_unique_cell_blocks")
        },
        "measurement": resources, "whole_machine_chunks": None,
        "complete_machine_runtime_verified": False, "physical_clock_speed_verified": False,
    }
    save(root / "size-comparison.json", comparison)
    print(json.dumps({"status": comparison["status"], "delta": comparison["delta"]}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline", default=BASELINE)
    parser.add_argument("--baseline-netlist", type=Path)
    parser.add_argument("--baseline-provenance", type=Path)
    parser.add_argument("--yosys", default="yosys")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--prepare-only", action="store_true")
    mode.add_argument("--run-prepared", action="store_true")
    mode.add_argument("--count", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    args.output = args.output.resolve()
    if args.count:
        count_mapping(args.count, args.output)
        return
    if args.run_prepared:
        report = json.loads((args.output / "prepared.json").read_text())
    else:
        if not args.baseline_netlist or not args.baseline_provenance:
            parser.error("preparation requires --baseline-netlist and --baseline-provenance")
        args.baseline_netlist = args.baseline_netlist.resolve()
        args.baseline_provenance = args.baseline_provenance.resolve()
        report = prepare(args)
    if args.prepare_only:
        print(json.dumps({"status": report["status"], "output": str(args.output)}, indent=2))
    else:
        run_prepared(args.output, report)


if __name__ == "__main__":
    main()
