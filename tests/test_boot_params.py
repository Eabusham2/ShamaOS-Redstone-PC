from pathlib import Path

from shamaos.os_image import (
    APP_RAM_BASE,
    APP_SLOT_BYTES,
    BUNDLE_ORDER,
    KERNEL_RAM_BASE,
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
    assert d["SHAMA_SLOT_BYTES"] == APP_SLOT_BYTES
    assert d["SHAMA_KERNEL_RAM_BASE"] == KERNEL_RAM_BASE
    assert d["SHAMA_APP_RAM_BASE"] == APP_RAM_BASE
    assert d["SHAMA_PC_KERNEL"] == KERNEL_RAM_BASE // 4
    assert d["SHAMA_PC_APP"] == APP_RAM_BASE // 4

    flash_defines = {
        "kernel": "SHAMA_KERNEL_FLASH",
        "desktop": "SHAMA_DESKTOP_FLASH",
        "editor": "SHAMA_EDITOR_FLASH",
        "files": "SHAMA_FILES_FLASH",
        "miner": "SHAMA_MINER_FLASH",
        "monitor": "SHAMA_MONITOR_FLASH",
        "terminal": "SHAMA_TERMINAL_FLASH",
        "calculator": "SHAMA_CALCULATOR_FLASH",
        "paint": "SHAMA_PAINT_FLASH",
        "settings": "SHAMA_SETTINGS_FLASH",
    }
    for name in BUNDLE_ORDER:
        assert d[flash_defines[name]] == image.slot_flash_offsets[name]

    for app, pc_word in image.app_pc_words.items():
        assert pc_word == APP_RAM_BASE // 4
        assert d[f"SHAMA_PC_{app.upper()}"] == pc_word
