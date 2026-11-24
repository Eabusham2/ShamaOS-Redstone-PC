"""Physical Editor, filesystem, and re-open observations for a lifecycle exercise."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256

from .flashfs import FileType, JOURNAL_OFFSET, ShamaFS
from .hardware.clock import clock_ports, system_clock_origin
from .hardware.controls import BUTTON_FLOOR, control_actuators, keyboard_buttons
from .hardware.debug import debug_ports
from .hardware.memory import MemoryBankSpec, repeater
from .hardware.memory_backbone import StackedMemoryTileSpec, stacked_bank_origin
from .machine_spec import BANK_BYTES, CACHE_BYTES, FLASH_BYTES
from .memory_map import EDIT_BUF, EDIT_CTX, EDIT_NAME
from .model import BlockState, Vec3
from .native_console_scenario import _console_origin, _word_expect, build_program_scenario
from .native_program_flash import PROGRAM_BINARY, PROGRAM_SOURCE, _FlashReader, _flash_banks, _read_flash_bank
from .native_title_crop import add_boot_title_observations
from .os_image import APP_IDS, build_default_os_image
from .worldio import NativeWorldReader, WorldWriteError


FILES = {"X.TXT": (FileType.TXT, b"ABC"), "Z.ASM": (FileType.ASM, PROGRAM_SOURCE),
         "Z.BIN": (FileType.BIN, PROGRAM_BINARY)}
BANK = MemoryBankSpec()


def _coords(point):
    return [point.x, point.y, point.z]


def bank_at(manifest, fabric, index):
    for section in manifest["physical_sections"]:
        if not section["name"].startswith(fabric + "-tile-"):
            continue
        first, count = section["first_bank_index"], section["banks"]
        if first <= index < first + count:
            tile = StackedMemoryTileSpec(bank=BANK, banks=count, layer_pitch=section["layer_pitch"])
            return stacked_bank_origin(Vec3(**section["origin"]), tile, index - first)
    raise WorldWriteError(f"Missing production {fabric} storage bank {index}")


def observe_word(reader, manifest, outputs, name, address):
    origin = bank_at(manifest, "ram", address // BANK_BYTES)
    row = (address % BANK_BYTES) // 4
    for bit in range(32):
        point = origin.offset(2 + bit * BANK.bit_pitch, BANK.cell_y, 3 + row * BANK.word_pitch)
        if reader.get_block(point) != repeater("east", locked=True):
            raise WorldWriteError(f"Expected initially clear RAM at {name}[{bit}]")
        outputs[f"{name}[{bit}]"] = _coords(point)


def _reg(index, value):
    return _word_expect("debug_registers", value, first=index * 32)


def build_lifecycle_scenarios(world, manifest, *, shutdown_ack_ticks, **budgets):
    """Build a physical text and program run, then a separate cold reopen and file handoff."""
    if type(shutdown_ack_ticks) is not int or not 0 < shutdown_ack_ticks < budgets["shutdown_ticks"]:
        raise ValueError("Place the shutdown acknowledgment sample inside the shutdown tick budget")
    program = build_program_scenario(world, manifest, **budgets)
    add_boot_title_observations(world, manifest, program, home_step=-2)
    origin = _console_origin(manifest)
    reader = NativeWorldReader(world)
    controls = control_actuators(origin)
    keyboard = keyboard_buttons(origin)
    extra = {"X": keyboard[2][7], "A": keyboard[0][0], "B": keyboard[0][1], "C": keyboard[0][2],
             "Enter": keyboard[4][5], "Files": controls["FILES"], "Left": controls["LEFT"]}
    for name, point in extra.items():
        expected = BUTTON_FLOOR
        if name in ("Files", "Left"):
            expected = BlockState.of("minecraft:stone_button", face="wall",
                                     facing="west" if name == "Files" else "south", powered="false")
        if reader.get_block(point) != expected:
            raise WorldWriteError(f"Missing unpressed physical {name} button at {point}")
    out = program["outputs"]
    for name, address in (("text_bytes", EDIT_BUF), ("editor_dirty", EDIT_CTX+20),
                          ("edit_name", EDIT_NAME)):
        observe_word(reader, manifest, out, name, address)
    clock_origin = system_clock_origin(origin)
    if Vec3(**manifest["system_build"]["clock_origin"]) != clock_origin:
        raise WorldWriteError("Clock origin differs from the physical console layout")
    clock = clock_ports(clock_origin)
    out.update(clock_hold=_coords(clock.hold), raw_clock=_coords(clock.clock))
    ack_pin, = debug_ports(origin.offset(dz=-224))["shutdown_ready"]
    ack_lamp = ack_pin.offset(dz=-3)
    if (manifest["system_build"]["debug_panel"]["shutdown_ready"] != [vars(ack_pin)]
            or reader.get_block(ack_lamp).name != "minecraft:redstone_lamp"):
        raise WorldWriteError("Missing or incorrectly located shutdown-ready lamp")
    out["shutdown_ready"] = _coords(ack_lamp)

    def app(name):
        return _word_expect("debug_app", APP_IDS[name], width=4)

    healthy = _word_expect("debug_fault", 0, width=8) | {"input_overflow[0]": False, "halted[0]": False}

    def press(point, ticks, values):
        return {"use_blocks": [_coords(point)], "ticks": ticks,
                "expect": healthy | {"physical_power": True, "clock_hold": True} | values}

    key, action = budgets["key_ticks"], budgets["action_ticks"]
    first, boot, editor = program["steps"][:3]
    home, shutdown = program["steps"][-2:]
    first["expect"].update(clock_hold=False, raw_clock=False)
    for step in program["steps"][1:-1]:
        step["expect"].update(clock_hold=True, shutdown_ready=False)
    shutdown["expect"].update(clock_hold=False, raw_clock=False)
    acknowledge = {"use_blocks": shutdown.pop("use_blocks"), "ticks": shutdown_ack_ticks,
                   "expect": {"physical_power": False, "shutdown_ready": True}}
    shutdown["ticks"] -= shutdown_ack_ticks
    program["steps"].insert(-1, acknowledge)
    dirty_home = deepcopy(home)
    dirty_home["expect"].update(_word_expect("text_bytes", 0))
    text = [press(controls["A"], action, app("editor") | _reg(8, 1) | _reg(11, 1)),
            press(extra["X"], key, _reg(8, 1)),
            press(extra["Enter"], action, _reg(8, 3) | _reg(7, 0)
                  | _word_expect("edit_name", int.from_bytes(b"X.TX", "little")))]
    for i, char in enumerate("ABC", 1):
        data = int.from_bytes(b"ABC"[:i], "little")
        text.append(press(extra[char], key, _reg(7, i) | _word_expect("text_bytes", data, width=8*i)
                          | _word_expect("editor_dirty", 1)))
    program["steps"][3:3] = [*text, dirty_home, deepcopy(editor)]

    # A saved powered-off panel may retain visible pixels. Its first assertion
    # describes physical shutdown; the first post-POWER observation checks Desktop.
    off = {"physical_power": False, "clock_hold": False, "raw_clock": False}
    reopen = {"inputs": {}, "outputs": dict(out), "initial_ticks": budgets["initial_ticks"],
              "capture_trace": False, "steps": [
                  {"ticks": 0, "expect": off}, deepcopy(boot),
                  press(extra["Files"], action, app("files") | _reg(9, 0)),
                  press(extra["X"], key, app("files") | _word_expect("edit_name", int.from_bytes(b"X.TX", "little"))),
                  press(extra["Left"], budgets["editor_ticks"], app("editor") | _reg(7, 3) | _reg(8, 3)
                        | _reg(11, 1) | _word_expect("text_bytes", int.from_bytes(b"ABC", "little"), width=24)),
                  deepcopy(dirty_home), deepcopy(acknowledge), deepcopy(shutdown)]}
    for scenario in (program, reopen):
        if scenario["initial_ticks"] + sum(s["ticks"] for s in scenario["steps"]) >= 2**64:
            raise ValueError("Lifecycle scenario exceeds the native tick counter")
    return program, reopen


def inspect_closed_storage(saved, manifest, power, initial_flash_sha):
    """Sample all 50 flash banks and the erased cache directly from the restored Anvil save."""
    reader = _FlashReader(saved)
    sw = reader.get_block(power)
    if sw.name != "minecraft:lever" or dict(sw.properties).get("powered") != "false":
        raise WorldWriteError("Snapshot power lever remains on")
    cache = _read_flash_bank(reader, bank_at(manifest, "cache", 0), BANK)
    if cache != bytes(CACHE_BYTES):
        raise WorldWriteError("Restored physical cache did not clear at shutdown")
    flash = b"".join(_read_flash_bank(reader, p, BANK) for p in _flash_banks(manifest))
    reader.check_files()
    return check_saved_files(flash, initial_flash_sha) | {"erased_cache_bytes": len(cache)}


def check_saved_files(flash, initial_flash_sha):
    original = build_default_os_image().image
    if (len(flash) != FLASH_BYTES or sha256(original).hexdigest() != initial_flash_sha
            or int.from_bytes(flash[JOURNAL_OFFSET:JOURNAL_OFFSET+4], "little") != 0):
        raise WorldWriteError("Restored flash differs in capacity, initial image binding, or journal state")
    before = {f.name: f for f in ShamaFS.deserialize(original).list_files()}
    fs = ShamaFS.deserialize(flash)
    after = {f.name: f for f in fs.list_files()}
    if set(before).intersection(FILES) or set(after) != set(before) | set(FILES):
        raise WorldWriteError("Unexpected saved directory changes")
    for name, f in before.items():
        if after[name].file_type != f.file_type or after[name].data != f.data:
            raise WorldWriteError(f"Installed file changed: {name}")
    bitmap = fs.bitmap_start * fs.block_size
    extents = set()
    for entry in after.values():
        for block in range(entry.start_block, entry.start_block + entry.block_count):
            if block in extents or not flash[bitmap + block//8] & (1 << (block%8)):
                raise WorldWriteError(f"Unallocated or aliased extent for {entry.name}")
            extents.add(block)
    for name, (kind, content) in FILES.items():
        if after[name].file_type != kind or after[name].data != content:
            raise WorldWriteError(f"Incorrect saved {name} type or bytes")
    return {"initial_flash_sha256": initial_flash_sha, "saved_flash_sha256": sha256(flash).hexdigest(),
            "flash_bytes_read": len(flash), "files": {name: value[1].hex() for name, value in FILES.items()}}
