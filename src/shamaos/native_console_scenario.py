"""Prepare a sparse-MCHPRS console scenario from a saved machine's physical I/O.

Creating this scenario observes saved block identities only. Running the
scenario checks console-driven state transitions. Neither operation certifies
the complete machine or its Java client and chunk-loading behavior.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .hardware.controls import control_actuators
from .hardware.debug import debug_ports
from .machine_spec import CACHE_BYTES
from .model import Vec3
from .os_image import APP_IDS, build_default_os_image
from .worldio import NativeWorldReader, WorldWriteError


# shama_cpu exposes registers little-endian at [register_index*32 +: 32].
_FIELD_BITS = {
    "debug_app": range(4), "debug_cache_used": range(32),
    "debug_registers": (*range(8 * 32, 9 * 32), *range(11 * 32, 12 * 32)),
    "debug_fault": range(8), "halted": range(1), "input_overflow": range(1),
}


def _point(record: dict) -> Vec3:
    if (not isinstance(record, dict) or set(record) != {"x", "y", "z"}
            or any(type(value) is not int for value in record.values())):
        raise WorldWriteError(f"Invalid native console coordinate: {record!r}")
    return Vec3(**record)


def _boot_scenario_endpoints(world: Path, manifest: dict) -> tuple[dict, dict]:
    sections = [section for section in manifest["physical_sections"] if section["name"] == "input"]
    if len(sections) != 1 or sections[0]["kind"] != "keyboard-controller":
        raise WorldWriteError("The generation manifest needs one physical input section.")
    origin = _point(sections[0]["origin"])
    reader = NativeWorldReader(world)
    actuators = control_actuators(origin)
    actions = {}
    for name in ("POWER", "EDITOR", "HOME"):
        position = actuators[name]
        block = reader.get_block(position)
        expected = "minecraft:lever" if name == "POWER" else "minecraft:stone_button"
        if block.name != expected or dict(block.properties).get("powered") != "false":
            raise WorldWriteError(f"{name} requires an initially unpowered saved {expected} at {position}.")
        actions[name] = [position.x, position.y, position.z]

    pins = debug_ports(origin.offset(dz=-224))
    reported = manifest["system_build"]["debug_panel"]
    outputs = {}
    for field, bits in _FIELD_BITS.items():
        if (field not in reported or not isinstance(reported[field], list)
                or len(reported[field]) != len(pins[field])):
            raise WorldWriteError(f"Missing or incomplete debug panel field {field}.")
        for bit in bits:
            pin = _point(reported[field][bit])
            if pin != pins[field][bit]:
                raise WorldWriteError(f"Incorrect native debug pin for {field}[{bit}].")
            # The manifest reports the signal input; the visible lamp is three
            # blocks north, beyond the repeater and its powered support.
            lamp = pin.offset(dz=-3)
            if reader.get_block(lamp).name != "minecraft:redstone_lamp":
                raise WorldWriteError(f"Missing saved debug lamp for {field}[{bit}] at {lamp}.")
            outputs[f"{field}[{bit}]"] = [lamp.x, lamp.y, lamp.z]
    return actions, outputs


def build_console_scenario(world: Path, manifest: dict, *, initial_ticks: int,
                           boot_ticks: int, editor_ticks: int, home_ticks: int) -> dict:
    """Use actual POWER/EDITOR/HOME actuators and sample visible debug lamps."""
    durations = (initial_ticks, boot_ticks, editor_ticks, home_ticks)
    if any(type(ticks) is not int or ticks <= 0 for ticks in durations) or sum(durations) >= 2**64:
        raise ValueError("Provide positive integer phase budgets with total ticks fitting a 64-bit runner.")
    actions, outputs = _boot_scenario_endpoints(world, manifest)
    image = build_default_os_image()
    steps = []
    for action, app, r8, ticks in ((None, None, 0, 0), ("POWER", "desktop", 1, boot_ticks),
                                  ("EDITOR", "editor", 0, editor_ticks), ("HOME", "desktop", 1, home_ticks)):
        # An external app jump clears all registers. Desktop must execute its
        # initial selection (r8 = 1), and fresh Editor must execute its initial
        # file type (r11 = 1), before either can satisfy these observations.
        r11 = int(app == "editor")
        values = {"debug_app": APP_IDS[app] if app else 0,
                  "debug_cache_used": min(image.slot_binary_lengths[app], CACHE_BYTES) if app else 0,
                  "debug_registers": (r8 << (8 * 32)) | (r11 << (11 * 32)),
                  "debug_fault": 0, "halted": 0, "input_overflow": 0}
        expected = {f"{field}[{bit}]": bool(values[field] & (1 << bit))
                    for field, bits in _FIELD_BITS.items() for bit in bits}
        step = {"ticks": ticks, "expect": expected}
        if action:
            step["use_blocks"] = [actions[action]]
        steps.append(step)
    return {"inputs": {}, "outputs": outputs, "initial_ticks": initial_ticks,
            "capture_trace": False, "steps": steps}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", type=Path, help="Directory containing world/ and generated-manifest.json")
    parser.add_argument("output", type=Path, help="New scenario JSON file")
    for phase in ("initial", "boot", "editor", "home"):
        parser.add_argument(f"--{phase}-ticks", type=int, required=True,
                            help=f"Redstone ticks to allow in the {phase} phase")
    args = parser.parse_args(argv)
    try:
        manifest = json.loads((args.candidate / "generated-manifest.json").read_text(encoding="utf-8"))
        scenario = build_console_scenario(args.candidate / "world", manifest,
            initial_ticks=args.initial_ticks, boot_ticks=args.boot_ticks,
            editor_ticks=args.editor_ticks, home_ticks=args.home_ticks)
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(scenario, stream, indent=2)
            stream.write("\n")
    except (KeyError, OSError, TypeError, ValueError, WorldWriteError) as exc:
        parser.exit(1, f"Cannot prepare native console scenario: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
