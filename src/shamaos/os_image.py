from __future__ import annotations

from dataclasses import dataclass

from .flashfs import FileType, ShamaFS


DEFAULT_TEXT = """Welcome to ShamaOS.

Use the controller to navigate.
Use the keyboard in Editor.
Preinstalled apps: Editor, File Explorer, Bitcoin Miner, System Monitor,
Terminal, Calculator, Paint and Settings.
"""


@dataclass(frozen=True)
class OSImage:
    image: bytes
    files: tuple[str, ...]


def build_default_os_image(flash_bytes: int = 1 << 20) -> OSImage:
    fs = ShamaFS(flash_bytes)

    # These are manifest/boot payload placeholders for the redstone OS build
    # pipeline. The assembler/OS compiler replaces them with machine images as
    # app sources mature; filenames and filesystem behavior are stable.
    fs.create_file("boot.sys", b"SHAMAOS_BOOT_V1\n", FileType.SYS)
    fs.create_file("kernel.sys", b"SHAMAOS_KERNEL_V1\n", FileType.SYS)
    fs.create_file("desktop.bin", b"SHAMAOS_DESKTOP_V1\n", FileType.BIN)
    fs.create_file("editor.bin", b"SHAMAOS_EDITOR_V1\n", FileType.BIN)
    fs.create_file("files.bin", b"SHAMAOS_FILES_V1\n", FileType.BIN)
    fs.create_file("miner.bin", b"SHAMAOS_MINER_V1\n", FileType.BIN)
    fs.create_file("monitor.bin", b"SHAMAOS_MONITOR_V1\n", FileType.BIN)
    fs.create_file("terminal.bin", b"SHAMAOS_TERMINAL_V1\n", FileType.BIN)
    fs.create_file("calculator.bin", b"SHAMAOS_CALC_V1\n", FileType.BIN)
    fs.create_file("paint.bin", b"SHAMAOS_PAINT_V1\n", FileType.BIN)
    fs.create_file("settings.bin", b"SHAMAOS_SETTINGS_V1\n", FileType.BIN)
    fs.create_file("welcome.txt", DEFAULT_TEXT.encode("utf-8"), FileType.TXT)
    fs.create_file("miner-state.cfg", b"generation=0\nnonce=0\nrunning=0\n", FileType.CFG)
    fs.create_file("miner-history.log", b"", FileType.LOG)

    return OSImage(fs.serialize(), tuple(e.name for e in fs.list_files()))
