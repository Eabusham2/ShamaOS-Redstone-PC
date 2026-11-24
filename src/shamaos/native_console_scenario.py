"""Prepare a sparse-MCHPRS console scenario from a saved machine's physical I/O.

Creating this scenario observes saved block identities only. Running the
scenario checks console-driven state transitions. Neither operation certifies
the complete machine or its Java client and chunk-loading behavior.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .hardware.controls import BUTTON_FLOOR, control_actuators, keyboard_buttons
from .hardware.debug import debug_ports
from .machine_spec import CACHE_BYTES
from .native_program_flash import PROGRAM_BINARY, PROGRAM_SOURCE, inspect_saved_program, require_new_program_files
from .native_title_crop import add_boot_title_observations
from .model import Vec3
from .os_image import APP_IDS, build_default_os_image
from .worldio import NativeWorldReader, WorldWriteError


# shama_cpu exposes registers little-endian at [register_index*32 +: 32].
_FIELD_BITS = {
    "debug_app": range(4), "debug_cache_used": range(32),
    "debug_registers": (*range(8 * 32, 9 * 32), *range(11 * 32, 12 * 32)),
    "debug_fault": range(8), "halted": range(1), "input_overflow": range(1),
}
_KEYS = {key: (row, col) for row, keys in enumerate(("ABCDEFGH", "IJKLMNOP", "QRSTUVWX", "YZ012345", "6789 \n"))
         for col, key in enumerate(keys)}


def _point(record: dict) -> Vec3:
    if (not isinstance(record, dict) or set(record) != {"x", "y", "z"}
            or any(type(value) is not int for value in record.values())):
        raise WorldWriteError(f"Invalid native console coordinate: {record!r}")
    return Vec3(**record)


def _console_origin(manifest):
    sections = [section for section in manifest["physical_sections"] if section["name"] == "input"]
    if len(sections) != 1 or sections[0]["kind"] != "keyboard-controller":
        raise WorldWriteError("The generation manifest needs one physical input section.")
    return _point(sections[0]["origin"])


def _boot_scenario_endpoints(world: Path, manifest: dict, *, program=False) -> tuple[dict, dict]:
    origin = _console_origin(manifest)
    reader = NativeWorldReader(world)
    actuators = control_actuators(origin)
    actions = {}
    names = ("POWER", "EDITOR", "HOME", "DOWN", "A", "B") if program else ("POWER", "EDITOR", "HOME")
    for name in names:
        position = actuators[name]
        block = reader.get_block(position)
        expected = "minecraft:lever" if name == "POWER" else "minecraft:stone_button"
        if block.name != expected or dict(block.properties).get("powered") != "false":
            raise WorldWriteError(f"{name} requires an initially unpowered saved {expected} at {position}.")
        actions[name] = [position.x, position.y, position.z]

    fields = _FIELD_BITS
    if program:
        buttons = keyboard_buttons(origin)
        for key in sorted(set("Z" + PROGRAM_SOURCE.decode("ascii"))):
            row, col = _KEYS[key]
            position = buttons[row][col]
            if reader.get_block(position) != BUTTON_FLOOR:
                raise WorldWriteError(f"Missing unpressed saved keyboard key {key!r} at {position}.")
            actions["key:" + key] = [position.x, position.y, position.z]
        fields = {**fields, "debug_lifecycle": range(32),
                  "debug_registers": tuple(reg * 32 + bit for reg in (1, 7, 8, 9, 11) for bit in range(32))}
    pins = debug_ports(origin.offset(dz=-224))
    reported = manifest["system_build"]["debug_panel"]
    outputs = {}
    for field, bits in fields.items():
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


def _word_expect(field, value, *, first=0, width=32):
    return {f"{field}[{first + bit}]": bool(value & (1 << bit)) for bit in range(width)}


def build_program_scenario(world: Path, manifest: dict, *, initial_ticks: int, boot_ticks: int,
                           editor_ticks: int, home_ticks: int, key_ticks: int, action_ticks: int,
                           run_ticks: int, shutdown_ticks: int) -> dict:
    """Create, type, assemble and run the RTL bench's Z.ASM through physical keys."""
    # The local stretched row/column signal lasts up to 119 redstone ticks.
    # A release interval must also reach the raw-clock sampler before another
    # press. Settling through the complete routed machine remains a measured input.
    if (any(type(n) is not int or n < 1 for n in (key_ticks, action_ticks, run_ticks, shutdown_ticks))
            or min(key_ticks, action_ticks) <= 119):
        raise ValueError("Provide positive phase budgets and physical key/action intervals exceeding 119 ticks.")
    scenario = build_console_scenario(world, manifest, initial_ticks=initial_ticks, boot_ticks=boot_ticks,
                                      editor_ticks=editor_ticks, home_ticks=home_ticks)
    actions, scenario["outputs"] = _boot_scenario_endpoints(world, manifest, program=True)
    scenario["outputs"]["physical_power"] = actions["POWER"]
    steps = scenario["steps"]
    home = steps.pop()
    healthy = _word_expect("debug_fault", 0, width=8) | {"input_overflow[0]": False}
    def register(index, value):
        return _word_expect("debug_registers", value, first=index * 32)

    def press(action, ticks, expect=None):
        steps.append({"use_blocks": [actions[action]], "ticks": ticks, "expect": healthy | (expect or {})})

    press("DOWN", action_ticks, register(9, 1))
    press("A", action_ticks, register(8, 2) | register(11, 2))
    press("key:Z", key_ticks)
    press("key:\n", action_ticks, register(8, 3) | register(7, 0))
    for length, char in enumerate(PROGRAM_SOURCE.decode("ascii"), 1):
        # A settled observation on *every* character checks consumption and
        # prevents an unserviced FIFO from masquerading as successful typing.
        press("key:" + char, key_ticks, register(7, length) | register(8, 3))
    press("B", action_ticks, register(8, 4) | register(9, 0))
    for selection in range(1, 6):
        press("DOWN", action_ticks, register(9, selection))
    press("A", run_ticks, _word_expect("debug_app", 15, width=4) | {"halted[0]": True}
          | register(1, 42) | _word_expect("debug_cache_used", len(PROGRAM_BINARY)))
    home["expect"].update(_word_expect("debug_lifecycle", 0))
    steps.append(home)
    press("POWER", shutdown_ticks, _word_expect("debug_cache_used", 0) | _word_expect("debug_lifecycle", 0))
    for index, step in enumerate(steps):
        step["expect"]["physical_power"] = 0 < index < len(steps) - 1
    if initial_ticks + sum(step["ticks"] for step in steps) >= 2**64:
        raise ValueError("Program scenario exceeds a 64-bit runner's tick counter.")
    scenario["shamaos_initial_flash_sha256"] = require_new_program_files(world, manifest, control_actuators(_console_origin(manifest))["POWER"])
    return scenario


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    inspect = argv[:1] == ["--inspect-saved-program"]
    if inspect:
        argv.pop(0)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", type=Path, help="Directory containing world/ and generated-manifest.json")
    if inspect:
        parser.add_argument("saved_world", type=Path, help="Separate closed-world save after the program and shutdown")
    parser.add_argument("output", type=Path, help="New JSON output file")
    if not inspect:
        parser.add_argument("--program", action="store_true", help="Type Z.ASM, assemble, run, return Home and power off")
        parser.add_argument("--titles", action="store_true", help="Check the initial, Desktop, Editor and Home title lamps")
        for phase in ("initial", "boot", "editor", "home", "key", "action", "run", "shutdown"):
            parser.add_argument(f"--{phase}-ticks", type=int, required=phase in ("initial", "boot", "editor", "home"),
                                help=f"Redstone ticks to allow in the {phase} phase")
    args = parser.parse_args(argv)
    try:
        if args.output.exists():
            raise FileExistsError(f"Output already exists: {args.output}")
        manifest = json.loads((args.candidate / "generated-manifest.json").read_text(encoding="utf-8"))
        world = args.candidate / "world"
        if inspect:
            document = inspect_saved_program(world, args.saved_world, manifest, control_actuators(_console_origin(manifest))["POWER"])
        else:
            phases = {key: value for key, value in vars(args).items() if key.endswith("_ticks") and value is not None}
            if args.program and len(phases) != 8:
                parser.error("--program requires explicit key, action, run and shutdown tick budgets")
            if not args.program and len(phases) != 4:
                parser.error("key, action, run and shutdown budgets apply only to --program")
            build = build_program_scenario if args.program else build_console_scenario
            document = build(world, manifest, **phases)
            if args.titles:
                add_boot_title_observations(world, manifest, document, home_step=-2 if args.program else -1)
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(document, stream, indent=2)
            stream.write("\n")
    except (KeyError, OSError, TypeError, ValueError, WorldWriteError) as exc:
        parser.exit(1, f"Native console check failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
