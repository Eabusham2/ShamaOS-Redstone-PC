from pathlib import Path
import re

from shamaos.os_image import (
    BUNDLE_RAM_BASE,
    build_default_os_image,
)


ROOT = Path(__file__).resolve().parents[1]


def _defines() -> dict[str, int]:
    text = (ROOT / "rtl/shama_boot_params.svh").read_text(encoding="utf-8")
    result: dict[str, int] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("`define SHAMA_"):
            continue
        parts = line.split()
        if len(parts) < 3 or not parts[2].startswith("32'h"):
            continue
        result[parts[1]] = int(parts[2][4:], 16)
    return result

def test_rtl_boot_parameters_match_generated_flash_image():
    d = _defines()
    image = build_default_os_image()

    assert d["SHAMA_BUNDLE_FLASH_OFFSET"] == image.bundle_flash_offset
    assert d["SHAMA_BUNDLE_RAM_BASE"] == BUNDLE_RAM_BASE
    assert d["SHAMA_BUNDLE_BYTES"] == image.bundle_bytes

    expected = {
        "desktop": "SHAMA_PC_DESKTOP",
        "editor": "SHAMA_PC_EDITOR",
        "files": "SHAMA_PC_FILES",
        "miner": "SHAMA_PC_MINER",
        "monitor": "SHAMA_PC_MONITOR",
        "terminal": "SHAMA_PC_TERMINAL",
        "calculator": "SHAMA_PC_CALCULATOR",
        "paint": "SHAMA_PC_PAINT",
        "settings": "SHAMA_PC_SETTINGS",
    }
    for app, define in expected.items():
        assert d[define] == image.app_pc_words[app]
