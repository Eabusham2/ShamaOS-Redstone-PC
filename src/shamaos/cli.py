from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .assembler import assemble_file, write_binary, write_listing
from .generator import build_plan, load_config, manifest, write_available_placements, write_manifest
from .os_image import build_default_os_image
from .synthesis import build_physical_netlist, synthesize_json
from .model import Vec3


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

    gen = sub.add_parser("generate", help="write currently implemented physical generators into a world")
    gen.add_argument("--world", required=True)
    gen.add_argument("--config", default="configs/default.toml")
    gen.add_argument("--manifest", default="build/generated-manifest.json")

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
        m["physical_generation_status"] = "complete-generated-topology"
        m["written_placements"] = result.written_blocks
        m["system_build"] = result.system_metadata
        m["stage_counts"] = result.stage_counts
        write_manifest(args.manifest, m)
        print(
            "generated complete ShamaOS physical topology; "
            f"wrote {result.written_blocks} blocks"
        )
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
