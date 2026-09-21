from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files

from .assembler import assemble
from .flashfs import FileType, ShamaFS


DEFAULT_TEXT = """Welcome to ShamaOS.

Use the controller to navigate.
Use the keyboard in Editor.
Preinstalled apps: Editor, File Explorer, Bitcoin Miner, System Monitor,
Terminal, Calculator, Paint and Settings.

Assembly source for the preinstalled apps is stored in flash too, so it can be
opened in Editor. Saving source never overwrites the last good executable
unless assembly succeeds.
"""

FIRMWARE = {
    "boot": ("boot.asm", "boot.sys", FileType.SYS),
    "kernel": ("kernel.asm", "kernel.sys", FileType.SYS),
    "desktop": ("desktop.asm", "desktop.bin", FileType.BIN),
    "editor": ("editor.asm", "editor.bin", FileType.BIN),
    "files": ("files.asm", "files.bin", FileType.BIN),
    "miner": ("miner.asm", "miner.bin", FileType.BIN),
    "monitor": ("monitor.asm", "monitor.bin", FileType.BIN),
    "terminal": ("terminal.asm", "terminal.bin", FileType.BIN),
    "calculator": ("calculator.asm", "calculator.bin", FileType.BIN),
    "paint": ("paint.asm", "paint.bin", FileType.BIN),
    "settings": ("settings.asm", "settings.bin", FileType.BIN),
}

# Physical/ABI memory contract.
CACHE_BYTES = 16 << 10
APP_SLOT_BYTES = 16 << 10
KERNEL_RAM_BASE = 16 << 10
APP_RAM_BASE = 32 << 10

BUNDLE_ORDER = (
    "kernel",
    "desktop",
    "editor",
    "files",
    "miner",
    "monitor",
    "terminal",
    "calculator",
    "paint",
    "settings",
)
APP_IDS = {
    "desktop": 0,
    "editor": 1,
    "files": 2,
    "miner": 3,
    "monitor": 4,
    "terminal": 5,
    "calculator": 6,
    "paint": 7,
    "settings": 8,
}


def app_pc_word(_app: str) -> int:
    """Foreground app executes from the software-managed 16 KiB fast cache."""
    return 0


@dataclass(frozen=True)
class OSImage:
    image: bytes
    files: tuple[str, ...]
    boot_binary: bytes
    bundle_flash_offset: int
    bundle_bytes: int
    app_pc_words: dict[str, int]
    slot_flash_offsets: dict[str, int]
    slot_binary_lengths: dict[str, int]


def _firmware_source(filename: str) -> str:
    return files("shamaos").joinpath("firmware", filename).read_text(encoding="utf-8")


def build_default_os_image(flash_bytes: int = 4 << 20) -> OSImage:
    fs = ShamaFS(flash_bytes)

    sources: dict[str, str] = {}
    binaries: dict[str, bytes] = {}
    for name, (source_name, _executable_name, _executable_type) in FIRMWARE.items():
        source = _firmware_source(source_name)
        result = assemble(source)
        binary = result.to_bytes()
        sources[name] = source
        binaries[name] = binary

    # Fixed-size flash slots make hardware loading simple and deterministic.
    bundle = bytearray(APP_SLOT_BYTES * len(BUNDLE_ORDER))
    for slot, name in enumerate(BUNDLE_ORDER):
        binary = binaries[name]
        if len(binary) > APP_SLOT_BYTES - 4:
            raise ValueError(
                f"{name} firmware is {len(binary)} bytes; exceeds "
                f"{APP_SLOT_BYTES - 4}-byte length-prefixed flash/app slot"
            )
        start = slot * APP_SLOT_BYTES
        bundle[start : start + 4] = len(binary).to_bytes(4, "little")
        bundle[start + 4 : start + 4 + len(binary)] = binary

    # First data extent so the hardware can mount/load without a filesystem
    # pathname lookup during the earliest boot stages.
    fs.create_file("boot.bundle", bytes(bundle), FileType.SYS)
    bundle_entry = fs.stat("boot.bundle")
    bundle_flash_offset = bundle_entry.start_block * fs.block_size

    # Store editable source and individual binaries in the normal filesystem too.
    for name, (source_name, executable_name, executable_type) in FIRMWARE.items():
        fs.create_file(source_name, sources[name].encode("utf-8"), FileType.ASM)
        fs.create_file(executable_name, binaries[name], executable_type)

    fs.create_file("welcome.txt", DEFAULT_TEXT.encode("utf-8"), FileType.TXT)

    # Fixed-size persistent miner records simplify the hardware history/state
    # service while still appearing as normal LOG/CFG files in File Explorer.
    fs.create_file(
        "miner-state.cfg",
        b"generation=0\nnonce=0\nattempts=0\nrunning=0\n".ljust(256, b"\x00"),
        FileType.CFG,
    )
    fs.create_file("miner-history.log", bytes(16 << 10), FileType.LOG)

    slot_flash_offsets = {
        name: bundle_flash_offset + slot * APP_SLOT_BYTES
        for slot, name in enumerate(BUNDLE_ORDER)
    }

    return OSImage(
        image=fs.serialize(),
        files=tuple(e.name for e in fs.list_files()),
        boot_binary=binaries["boot"],
        bundle_flash_offset=bundle_flash_offset,
        bundle_bytes=len(bundle),
        app_pc_words={name: app_pc_word(name) for name in APP_IDS},
        slot_flash_offsets=slot_flash_offsets,
        slot_binary_lengths={name: len(binaries[name]) for name in BUNDLE_ORDER},
    )
