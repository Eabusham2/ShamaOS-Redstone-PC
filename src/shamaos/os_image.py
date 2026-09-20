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

# Cache/scratch occupies the first 16 KiB of the CPU address space. The tiny
# boot program is preloaded there by the world generator. The OS bundle is
# copied from flash into fixed 16 KiB RAM slots beginning immediately after it.
APP_SLOT_BYTES = 16 << 10
BUNDLE_RAM_BASE = 16 << 10
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


def app_pc_word(app: str) -> int:
    """Word-address PC for an app after SYS_BOOT_LOAD_OS completes."""
    app_id = APP_IDS[app]
    slot = 1 + app_id  # slot 0 is kernel
    return (BUNDLE_RAM_BASE + slot * APP_SLOT_BYTES) // 4


@dataclass(frozen=True)
class OSImage:
    image: bytes
    files: tuple[str, ...]
    boot_binary: bytes
    bundle_flash_offset: int
    bundle_bytes: int
    app_pc_words: dict[str, int]


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

    # The first data file is deliberately the boot bundle. This makes its
    # physical flash location deterministic from ShamaFS geometry, while the
    # returned OSImage still records the exact offset instead of relying on a
    # duplicated magic constant.
    bundle = bytearray(APP_SLOT_BYTES * len(BUNDLE_ORDER))
    for slot, name in enumerate(BUNDLE_ORDER):
        binary = binaries[name]
        if len(binary) > APP_SLOT_BYTES:
            raise ValueError(
                f"{name} firmware is {len(binary)} bytes; exceeds "
                f"{APP_SLOT_BYTES}-byte boot slot"
            )
        start = slot * APP_SLOT_BYTES
        bundle[start : start + len(binary)] = binary

    fs.create_file("boot.bundle", bytes(bundle), FileType.SYS)
    bundle_entry = fs.stat("boot.bundle")
    bundle_flash_offset = bundle_entry.start_block * fs.block_size

    # Store editable source and individual executable copies too. File Explorer
    # and Editor therefore see the same preinstalled programs the boot bundle
    # contains.
    for name, (source_name, executable_name, executable_type) in FIRMWARE.items():
        fs.create_file(source_name, sources[name].encode("utf-8"), FileType.ASM)
        fs.create_file(executable_name, binaries[name], executable_type)

    fs.create_file("welcome.txt", DEFAULT_TEXT.encode("utf-8"), FileType.TXT)
    fs.create_file(
        "miner-state.cfg",
        b"generation=0\nnonce=0\nattempts=0\nrunning=0\n",
        FileType.CFG,
    )
    fs.create_file("miner-history.log", b"", FileType.LOG)

    return OSImage(
        image=fs.serialize(),
        files=tuple(e.name for e in fs.list_files()),
        boot_binary=binaries["boot"],
        bundle_flash_offset=bundle_flash_offset,
        bundle_bytes=len(bundle),
        app_pc_words={name: app_pc_word(name) for name in APP_IDS},
    )
