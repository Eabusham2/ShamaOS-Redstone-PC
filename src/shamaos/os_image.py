from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files

from .assembler import assemble
from .flashfs import FileType, ShamaFS
from .machine_spec import BANK_BYTES, CACHE_BYTES, FLASH_BYTES, FLASH_WRITE_LIMIT_PERCENT, RAM_BYTES, VRAM_BYTES
from .memory_map import (
    APP_RAM_BASE, APP_SLOT_BYTES, KERNEL_RAM_BASE, KERNEL_SLOT_BYTES,
    RTL_MEMORY_CONSTANTS,
)


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
    """Foreground app executes from the software-managed 8 KiB fast cache."""
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
    file_entry_indices: dict[str, int]


def _firmware_source(filename: str) -> str:
    return files("shamaos").joinpath("firmware", filename).read_text(encoding="utf-8")


def build_default_os_image(flash_bytes: int = FLASH_BYTES) -> OSImage:
    fs = ShamaFS(flash_bytes)

    sources: dict[str, str] = {}
    binaries: dict[str, bytes] = {}
    for name, (source_name, _executable_name, _executable_type) in FIRMWARE.items():
        source = _firmware_source(source_name)
        result = assemble(source)
        binary = result.to_bytes()
        sources[name] = source
        binaries[name] = binary

    # The hardware loader uses an image-derived address and a length prefix,
    # so flash records need only word alignment. Padding every executable to
    # the full RAM/fast-memory slot wastes physical banks without helping
    # runtime loading. Keep the complete editable source and individual
    # executable files below; only unused boot-bundle padding is removed.
    bundle = bytearray()
    record_offsets: dict[str, int] = {}
    for name in BUNDLE_ORDER:
        binary = binaries[name]
        limit = min(KERNEL_SLOT_BYTES if name == "kernel" else CACHE_BYTES, APP_SLOT_BYTES - 4)
        if len(binary) > limit:
            raise ValueError(
                f"{name} firmware is {len(binary)} bytes; exceeds "
                f"{limit}-byte executable capacity"
            )
        if len(binary) % 4:
            raise ValueError(f"{name} executable must be word aligned")
        record_offsets[name] = len(bundle)
        bundle.extend(len(binary).to_bytes(4, "little"))
        bundle.extend(binary)

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

    # Canonical folders are real persistent directory entries. Shipped root
    # filenames stay compatible with existing source/open examples.
    for directory in ("documents", "programs", "projects", "logs", "config"):
        fs.mkdir(directory)
    fs.create_file("documents/welcome.txt", DEFAULT_TEXT.encode("utf-8"), FileType.TXT)

    # Fixed-size persistent miner records simplify the hardware history/state
    # service while still appearing as normal LOG/CFG files in File Explorer.
    fs.create_file(
        "miner-state.cfg",
        bytes(256),
        FileType.CFG,
    )
    fs.create_file("miner-history.log", bytes(32 << 10), FileType.LOG)

    slot_flash_offsets = {
        name: bundle_flash_offset + record_offsets[name]
        for name in BUNDLE_ORDER
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
        file_entry_indices={entry.name:index for index,entry in enumerate(fs.list_files())},
    )


def render_boot_parameters(image: OSImage | None = None) -> str:
    """Generate RTL boot/storage constants from the exact serialized image."""
    image = image or build_default_os_image()
    fs = ShamaFS.deserialize(image.image)
    constants = {
        "RAM_BYTES": RAM_BYTES,
        "CACHE_BYTES": CACHE_BYTES,
        "VRAM_BYTES": VRAM_BYTES,
        "FLASH_BYTES": len(image.image),
        "RAM_BANKS": RAM_BYTES // BANK_BYTES,
        "CACHE_BANKS": CACHE_BYTES // BANK_BYTES,
        "VRAM_BANKS": VRAM_BYTES // BANK_BYTES,
        "FLASH_BANKS": len(image.image) // BANK_BYTES,
        "FS_BLOCK_COUNT": fs.block_count,
        "FS_WRITE_LIMIT_BLOCKS": fs.block_count * FLASH_WRITE_LIMIT_PERCENT // 100,
        "FS_BITMAP_WORDS": (fs.block_count + 31) // 32,
        "FS_TABLE_FLASH_OFFSET": fs.table_start * fs.block_size,
        "FS_DATA_START_BLOCK": fs.data_start,
        "BUNDLE_FLASH_OFFSET": image.bundle_flash_offset,
        "BUNDLE_BYTES": image.bundle_bytes,
        "SLOT_BYTES": APP_SLOT_BYTES,
        "SLOT_DATA_OFFSET": 4,
        **{f"{name.upper()}_FLASH":address for name,address in image.slot_flash_offsets.items()},
        **{f"{name.upper()}_BINARY_BYTES":length for name,length in image.slot_binary_lengths.items()},
        **RTL_MEMORY_CONSTANTS,
        "PC_KERNEL": KERNEL_RAM_BASE // 4,
        "PC_APP": 0,
        **{f"PC_{name.upper()}":address for name,address in image.app_pc_words.items()},
        "MINER_HISTORY_ENTRY": image.file_entry_indices["miner-history.log"],
        "MINER_STATE_ENTRY": image.file_entry_indices["miner-state.cfg"],
    }
    return ("`ifndef SHAMA_BOOT_PARAMS_SVH\n`define SHAMA_BOOT_PARAMS_SVH\n\n"
            "// Generated by shamaos.os_image.render_boot_parameters; tested against flash.\n"
            + "".join(f"`define SHAMA_{name:<24} 32'h{value:08x}\n" for name,value in constants.items())
            + "\n`endif\n")
