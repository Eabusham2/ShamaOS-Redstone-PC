from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from .assembler import assemble_file, write_binary, write_listing
from .generator import build_plan, create_runtime_fixture_world, generation_preflight, load_config, manifest, validate_manifest_capacity, write_available_placements, write_manifest
from .os_image import build_default_os_image
from .synthesis import build_physical_netlist, synthesize_json
from .model import Vec3
from .worldio import NativeWorldWriter, WorldWriteError, validate_native_world


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="shamaos")
    sub = p.add_subparsers(dest="command", required=True)

    asm = sub.add_parser("asm", help="assemble Shama Assembly")
    asm.add_argument("source")
    asm.add_argument("-o", "--output", required=True)
    asm.add_argument("--listing")

    plan = sub.add_parser("plan", help="create deterministic machine layout manifest")
    plan.add_argument("--config", default="configs/default.toml")
    plan.add_argument("--manifest", default="build/plan.json")

    fs = sub.add_parser("flash-image", help="build preloaded ShamaOS flash image")
    fs.add_argument("-o", "--output", default="build/shamaos-flash.img")
    fs.add_argument("--bytes", type=int, default=4 << 20)

    synth = sub.add_parser("synth-redstone", help="synthesize SystemVerilog into physical redstone cells/routes")
    synth.add_argument("--top", required=True)
    synth.add_argument("--rtl", action="append", required=True, help="RTL file; repeat for multiple files")
    synth.add_argument("--json", default="build/redstone-netlist.json")
    synth.add_argument("--manifest", default="build/redstone-physical.json")
    synth.add_argument("--origin-x", type=int, default=0)
    synth.add_argument("--origin-y", type=int, default=64)
    synth.add_argument("--origin-z", type=int, default=0)

    gen = sub.add_parser("generate", help="create a Java 1.20.4 world from the full physical generators")
    gen.add_argument("--world", required=True)
    gen.add_argument("--config", default="configs/default.toml")
    gen.add_argument("--manifest", default="build/generated-manifest.json")

    create = sub.add_parser("create-world", help="create an empty Java 1.20.4 void world with a spawn platform")
    create.add_argument("--world", required=True)
    create.add_argument("--name", default="ShamaOS Redstone PC")

    validate = sub.add_parser("validate-world", help="read saved Anvil chunks and verify every generated block state")
    validate.add_argument("--world", required=True)

    fixture = sub.add_parser("fixture-world", help="create and verify a small playable redstone fixture (not the full computer)")
    fixture.add_argument("--world", required=True)
    fixture.add_argument("--runtime-runner", default=os.environ.get("SHAMA_MCHPRS_RUNNER"), help="optional compiled MCHPRS runner to exercise the saved blocks")

    preflight = sub.add_parser("generation-preflight", help="measure storage geometry and report full-capacity resource/loading limits")
    preflight.add_argument("--config", default="configs/default.toml")
    preflight.add_argument("--world", help="destination whose available disk space should be checked")
    preflight.add_argument("--benchmark", help="optional JSON from a measured default-bank export")
    preflight.add_argument("--report", default="build/generation-preflight.json")

    capacity = sub.add_parser("validate-manifest", help="check manifest capacity against the literal full-machine specification")
    capacity.add_argument("--manifest", required=True)

    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    if args.command == "asm":
        result = assemble_file(args.source)
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        write_binary(result, args.output)
        if args.listing:
            write_listing(result, args.listing)
        return 0

    if args.command == "plan":
        config = load_config(args.config)
        plan = build_plan(config)
        write_manifest(args.manifest, manifest(config, plan))
        return 0

    if args.command == "flash-image":
        image = build_default_os_image(args.bytes)
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(image.image)
        print(f"wrote {len(image.image)} bytes with {len(image.files)} preloaded files")
        return 0

    if args.command == "synth-redstone":
        netlist_path = synthesize_json(args.rtl, top=args.top, output_json=args.json)
        physical = build_physical_netlist(
            netlist_path,
            top=args.top,
            origin=Vec3(args.origin_x, args.origin_y, args.origin_z),
        )
        write_manifest(args.manifest, physical.manifest())
        print(
            f"synthesized {physical.manifest()['cell_count']} cells, "
            f"{physical.manifest()['net_count']} routed nets on "
            f"{physical.manifest()['routing_tracks']} tracks"
        )
        return 0

    if args.command == "generate":
        config = load_config(args.config)
        plan = build_plan(config)
        result = write_available_placements(args.world, config, plan)
        m = manifest(config, plan)
        m["physical_generation_status"] = (
            "generated-topology-readback-verified"
            if result.world_validation.get("status") == "passed"
            else "generated-topology-readback-not-run"
        )
        m["written_placements"] = result.written_blocks
        m["system_build"] = result.system_metadata
        m["stage_counts"] = result.stage_counts
        m["world_validation"] = result.world_validation
        m["runtime_validation"] = {"status": "not-run"}
        write_manifest(args.manifest, m)
        print(
            f"wrote {result.written_blocks} placements; "
            f"disk readback: {result.world_validation.get('status', 'not-run')}; "
            "full-machine runtime validation remains outstanding"
        )
        return 0

    if args.command == "create-world":
        with NativeWorldWriter(args.world, name=args.name) as writer:
            report = writer.validate()
        print(f"created Java 1.20.4 world at {args.world}; verified {report['assigned_blocks']} spawn-platform blocks")
        return 0

    if args.command == "validate-world":
        print(json.dumps(validate_native_world(args.world), indent=2, sort_keys=True))
        return 0

    if args.command == "fixture-world":
        report = create_runtime_fixture_world(args.world)
        if args.runtime_runner:
            case_path = Path(args.world) / "shamaos" / "runtime_case.json"
            result = subprocess.run([args.runtime_runner], input=case_path.read_text(encoding="utf-8"), text=True, capture_output=True, timeout=120)
            if result.returncode:
                raise WorldWriteError(f"saved-world MCHPRS fixture failed: {result.stdout}{result.stderr}")
            runtime = json.loads(result.stdout)
            if runtime.get("status") != "passed":
                raise WorldWriteError("saved-world MCHPRS fixture did not report success")
            report["runtime_validation"] = runtime
            write_manifest(Path(args.world) / "shamaos" / "fixture.json", report)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0

    if args.command == "generation-preflight":
        report = generation_preflight(load_config(args.config), world_path=args.world, benchmark_path=args.benchmark)
        write_manifest(args.report, report)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["capacity_contract"]["status"] == "passed" and report["storage_minimum_fits_available_disk"] else 1

    if args.command == "validate-manifest":
        report = validate_manifest_capacity(json.loads(Path(args.manifest).read_text(encoding="utf-8")))
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["status"] == "passed" else 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
