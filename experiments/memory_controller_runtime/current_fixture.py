"""Freeze and map the exact current cache capacity for a separate native proof.

The retained c331 fixture stays an 8 KiB historical case. This entry point
copies current elaboration inputs, binds their hashes, runs production mapping
and checks the complete request schedule against the actual copied RTL.
Placement, native wiring and saved-world execution remain explicit later gates.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from experiments.memory_controller_runtime import geometry, scenarios
from experiments.partition_planner.audit_cache_frames import rtl_review
from shamaos.hardware.logic import template_for
from shamaos.hardware.partition_planner import Graph, audit_plan, build_plan
from shamaos.layout import default_origins, memory_specs
from shamaos.synthesis import synthesize_json
from shamaos.system_build import _geometry_from_config


TOP = "shama_cache_adapter"
INPUTS = ("configs/default.toml", "rtl/shama_memory_adapter.sv", "rtl/shama_boot_params.svh",
          "synth/redstone.ys", "synth/redstone.lib")
GENERATOR_SOURCES = (
    "experiments/memory_controller_runtime/current_fixture.py",
    "experiments/memory_controller_runtime/geometry.py",
    "experiments/memory_controller_runtime/scenarios.py",
    "experiments/partition_planner/audit_cache_frames.py",
    "src/shamaos/synthesis.py", "src/shamaos/layout.py", "src/shamaos/system_build.py",
    "src/shamaos/hardware/partition_planner.py", "src/shamaos/hardware/logic.py",
    "src/shamaos/hardware/memory.py", "src/shamaos/hardware/memory_backbone.py",
)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def macro_value(header, name):
    matches = re.findall(rf"(?m)^`define\s+{re.escape(name)}\s+((?:32'h[0-9a-fA-F]+)|(?:[0-9]+))\s*$", header)
    if len(matches) != 1:
        raise ValueError(f"current header must contain one exact numeric {name}")
    raw = matches[0]
    return int(raw[4:], 16) if raw.startswith("32'h") else int(raw)


def physical_configuration(source_root, capacity_bytes):
    scenarios.check_capacity(capacity_bytes)
    config = tomllib.loads((source_root / "configs/default.toml").read_text())
    header = (source_root / "rtl/shama_boot_params.svh").read_text()
    count = (capacity_bytes + 4095) // 4096
    if (config["memory"]["cache_bytes"] != capacity_bytes
            or macro_value(header, "SHAMA_CACHE_BYTES") != capacity_bytes
            or macro_value(header, "SHAMA_CACHE_BANKS") != count):
        raise ValueError("current configuration, generated header and requested cache capacity disagree")
    shape = _geometry_from_config(config)
    fabric = memory_specs(shape)[0]
    origin = default_origins(shape).cache
    if (shape.surface_y != -61 or fabric.total_bytes != capacity_bytes or fabric.bank_count != count
            or fabric.group_count != 1 or not fabric.binary_rows):
        raise ValueError("current cache must retain its exact above-grass decoded physical capacity")
    return shape, fabric, origin


def validate_interface(graph, capacity_bytes):
    banks = (capacity_bytes + 4095) // 4096
    expected = {name: ("input", width) for name, width in scenarios.HOST_WIDTHS.items()}
    expected.update(selected_word=("input", 32), req_ready=("output", 1), req_rdata=("output", 32),
                    bank_select=("output", banks), row_address=("output", 10), read_enable=("output", 1),
                    write_commit=("output", 1), write_data=("output", 32))
    exact = {(name, bit): direction for name, (direction, width) in expected.items() for bit in range(width)}
    observed = {(entity["port_name"], entity["port_index"]): entity["direction"]
                for entity in graph.entities if entity["kind"] == "port"}
    if observed != exact:
        raise ValueError("fresh mapped cache changed the exact original physical interface")
    return {"original_port_bits": len(exact), "host_bits": sum(scenarios.HOST_WIDTHS.values()),
            "bank_select_bits": banks, "memory_nets": 76 + banks,
            "registers": sum(entity["kind"] == "cell" and template_for(entity["cell_type"]).cell_type == "DFF"
                             for entity in graph.entities)}


def retain_native_mapping(raw_path: Path, mapped_path: Path, *, yosys, liberty):
    """Discard only Yosys 0.52's explicitly pinless hierarchy metadata.

    ``help flatten`` documents $scopeinfo as removed-scope attributes. Keep
    the raw artifact and removal command; reject any such cell with a pin or
    connection. All actual cells, module ports and named nets must survive the
    Yosys pass exactly. Unsupported physical cells still reach Graph.read and
    are rejected there.
    """
    raw = json.loads(raw_path.read_text())
    if set(raw["modules"]) != {TOP}:
        raise ValueError("current cache must flatten to its single complete mapped top")
    metadata = {module: [name for name, cell in value.get("cells", {}).items()
                        if cell["type"] == "$scopeinfo"] for module, value in raw["modules"].items()}
    for module, names in metadata.items():
        for name in names:
            cell = raw["modules"][module]["cells"][name]
            if cell.get("connections") or cell.get("port_directions"):
                raise ValueError("scope metadata unexpectedly owns physical pins or signal connections")
    count = sum(map(len, metadata.values()))
    annotated = 0
    if count:
        script = raw_path.with_name("remove-scope-metadata.ys")
        script.write_text(f"read_liberty -lib {json.dumps(str(liberty))}\nread_json {json.dumps(str(raw_path))}\ndelete t:$scopeinfo\ncheck -assert\nselect {TOP}\nwrite_json -selected {json.dumps(str(mapped_path))}\n")
        result = subprocess.run([yosys, "-Q", "-T", "-s", str(script)], capture_output=True, text=True, timeout=60)
        script.with_suffix(".log").write_text(result.stdout + result.stderr)
        if result.returncode:
            raise ValueError("Yosys could not remove validated pinless scope metadata: " + result.stdout + result.stderr)
        actual = json.loads(mapped_path.read_text())
        if set(actual["modules"]) != set(raw["modules"]):
            raise ValueError("scope cleanup changed the module inventory")
        for module, before in raw["modules"].items():
            expected = {name: cell for name, cell in before.get("cells", {}).items() if name not in metadata[module]}
            after = actual["modules"][module]
            if set(expected) != set(after.get("cells", {})) or any(before.get(key, {}) != after.get(key, {}) for key in ("ports", "netnames")):
                raise ValueError("scope cleanup changed an actual cell, pin, signal or named net")
            for name, cell in expected.items():
                compared = dict(after["cells"][name])
                if "port_directions" not in cell and "port_directions" in compared:
                    template = template_for(cell["type"])
                    directions = {pin: "output" if pin in ("Q", "Y") else "input" for pin in template.pins}
                    if compared.pop("port_directions") != directions:
                        raise ValueError("library lookup changed a native pin direction")
                    annotated += 1
                if compared != cell:
                    raise ValueError("scope cleanup changed a native cell or its connections")
    else:
        shutil.copyfile(raw_path, mapped_path)
    return {"raw_mapped_sha256": sha(raw_path), "pinless_scope_records": count,
            "scope_metadata": metadata, "validated_library_pin_annotations_added": annotated,
            "all_native_cells_ports_nets_unchanged": True}


def prepare(source_root: Path, directory: Path, *, capacity_bytes=4096):
    source_root, directory = source_root.resolve(), directory.resolve()
    if directory.exists():
        raise FileExistsError(directory)
    physical_configuration(source_root, capacity_bytes)
    data = {name: (source_root / name).read_bytes() for name in INPUTS}
    input_hashes = {name: hashlib.sha256(value).hexdigest() for name, value in data.items()}
    generator_hashes = {name: sha(ROOT / name) for name in GENERATOR_SOURCES}
    snapshot = directory / "source"
    snapshot.mkdir(parents=True)
    for name, value in data.items():
        target = snapshot / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(value)
    if input_hashes != {name: sha(source_root / name) for name in INPUTS}:
        raise ValueError("current source inputs changed while copying the cache snapshot")
    (directory / "input-inventory.json").write_text(json.dumps({"source_root": str(source_root),
        "capacity_bytes": capacity_bytes, "input_hashes": input_hashes,
        "generator_hashes": generator_hashes}, indent=2) + "\n")
    shape, fabric, origin = physical_configuration(snapshot, capacity_bytes)
    yosys = shutil.which("yosys")
    if not yosys:
        raise RuntimeError("fresh current-cache mapping requires Yosys")
    version = subprocess.run([yosys, "-V"], capture_output=True, text=True, check=True).stdout.strip()
    mapped, raw = directory / "mapped.json", directory / "mapped-raw.json"
    synthesize_json([snapshot / "rtl/shama_memory_adapter.sv"], top=TOP, output_json=raw,
                    repo_root=snapshot, mapping_mode="abc")
    metadata = retain_native_mapping(raw, mapped, yosys=yosys, liberty=snapshot / "synth/redstone.lib")
    graph = Graph.read(mapped, TOP)
    interface = validate_interface(graph, capacity_bytes)
    plan = build_plan(graph, ports_at_root=True)
    audit = audit_plan(graph, plan)
    (directory / "plan.json").write_text(json.dumps(plan, separators=(",", ":")) + "\n")
    cold = list(scenarios.functional_frames(capacity_bytes=capacity_bytes))
    summary = scenarios.summarize_frames(cold)
    resumed = list(scenarios.functional_frames(summary["final_words"], capacity_bytes=capacity_bytes, resume=True))
    (directory / "initial.bin").write_bytes(b"".join(word.to_bytes(4, "little")
        for word in scenarios.initial_words(capacity_bytes=capacity_bytes)))
    review_directory = directory / "rtl-review"
    review_directory.mkdir()
    oracle = rtl_review(review_directory, source_root=snapshot, capacity_bytes=capacity_bytes)
    contract = geometry.cache_route_contract(origin, fabric, capacity_bytes=capacity_bytes)
    if (input_hashes != {name: sha(snapshot / name) for name in INPUTS}
            or generator_hashes != {name: sha(ROOT / name) for name in GENERATOR_SOURCES}):
        raise ValueError("copied inputs or generator changed during current-cache preparation")
    report = {"schema": "current-cache-native-fixture-v1", "status": "mapped-prepared-native-runtime-pending",
        "scope": "Fresh exact-capacity mapped controller, complete logical plan, physical bank contract and real RTL frame oracle; complete geometry and native execution remain pending",
        "capacity_bytes": capacity_bytes, "banks": fabric.bank_count, "stored_bits": capacity_bytes * 8,
        "input_hashes": input_hashes, "generator_hashes": generator_hashes, "yosys_version": version,
        "mapped_source": graph.source, "metadata_removal": metadata, "plan_sha256": sha(directory / "plan.json"),
        "plan_summary": plan["summary"], "plan_audit": audit, "interface": interface,
        "initial_sha256": sha(directory / "initial.bin"), "bank_bounds": asdict(fabric.bounds(origin)),
        "physical_memory_terminals": len(contract.access), "rtl_oracle": oracle,
        "cold_frames": {k: v for k, v in summary.items() if k not in ("final_words", "boundary_states")},
        "resume_frames": {k: v for k, v in scenarios.summarize_frames(resumed).items() if k not in ("final_words", "boundary_states")},
        "geometry_verified": False, "native_runtime_verified": False, "whole_machine_verified": False}
    (directory / "prepared.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def load_prepared(directory: Path):
    """Recheck exact copied inputs, generator, graph, plan and stored capacity."""
    directory = directory.resolve()
    report = json.loads((directory / "prepared.json").read_text())
    if (report.get("schema") != "current-cache-native-fixture-v1"
            or report.get("status") != "mapped-prepared-native-runtime-pending"):
        raise ValueError("current cache preparation has not completed")
    if report["input_hashes"] != {name: sha(directory / "source" / name) for name in INPUTS}:
        raise ValueError("current cache copied elaboration inputs changed")
    if report["generator_hashes"] != {name: sha(ROOT / name) for name in GENERATOR_SOURCES}:
        raise ValueError("current cache preparation generator changed")
    inventory = json.loads((directory / "input-inventory.json").read_text())
    if (inventory["input_hashes"] != report["input_hashes"]
            or inventory["generator_hashes"] != report["generator_hashes"]
            or inventory["capacity_bytes"] != report["capacity_bytes"]):
        raise ValueError("current cache frozen input inventory disagrees with its preparation")
    capacity = report["capacity_bytes"]
    shape, fabric, origin = physical_configuration(directory / "source", capacity)
    graph = Graph.read(directory / "mapped.json", TOP)
    identity = lambda source: {key: value for key, value in source.items() if key != "path"}
    if (identity(graph.source) != identity(report["mapped_source"])
            or sha(directory / "mapped-raw.json") != report["metadata_removal"]["raw_mapped_sha256"]):
        raise ValueError("current cache mapping differs from the exact prepared artifact")
    if validate_interface(graph, capacity) != report["interface"]:
        raise ValueError("current cache mapping interface changed")
    plan = json.loads((directory / "plan.json").read_text())
    if sha(directory / "plan.json") != report["plan_sha256"] or plan["summary"] != report["plan_summary"]:
        raise ValueError("current cache complete plan changed")
    if audit_plan(graph, plan) != report["plan_audit"]:
        raise ValueError("current cache independent plan audit changed")
    if (len((directory / "initial.bin").read_bytes()) != capacity
            or sha(directory / "initial.bin") != report["initial_sha256"]):
        raise ValueError("current cache complete initial memory changed")
    return report, graph, shape, fabric, origin


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--capacity-bytes", type=int, default=4096)
    args = parser.parse_args()
    report = prepare(args.source_root, args.output, capacity_bytes=args.capacity_bytes)
    print(json.dumps({key: report[key] for key in ("status", "capacity_bytes", "banks", "stored_bits", "interface", "plan_summary")}, indent=2))


if __name__ == "__main__":
    main()
