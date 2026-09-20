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


@dataclass(frozen=True)
class OSImage:
    image: bytes
    files: tuple[str, ...]


def _firmware_source(filename: str) -> str:
    return files("shamaos").joinpath("firmware", filename).read_text(encoding="utf-8")


def build_default_os_image(flash_bytes: int = 1 << 20) -> OSImage:
    fs = ShamaFS(flash_bytes)

    # Store both editable source and the actually assembled executable.
    for _name, (source_name, executable_name, executable_type) in FIRMWARE.items():
        source = _firmware_source(source_name)
        result = assemble(source)
        fs.create_file(source_name, source.encode("utf-8"), FileType.ASM)
        fs.create_file(executable_name, result.to_bytes(), executable_type)

    fs.create_file("welcome.txt", DEFAULT_TEXT.encode("utf-8"), FileType.TXT)
    fs.create_file(
        "miner-state.cfg",
        b"generation=0\nnonce=0\nattempts=0\nrunning=0\n",
        FileType.CFG,
    )
    fs.create_file("miner-history.log", b"", FileType.LOG)

    return OSImage(fs.serialize(), tuple(e.name for e in fs.list_files()))
