from pathlib import Path

from shamaos.os_image import (
    APP_RAM_BASE,
    APP_SLOT_BYTES,
    BUNDLE_ORDER,
    KERNEL_RAM_BASE,
    build_default_os_image,
    render_boot_parameters,
)
from shamaos.flashfs import ShamaFS
from shamaos.memory_map import RTL_MEMORY_CONSTANTS


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
    fs = ShamaFS.deserialize(image.image)
    assert (ROOT / "rtl/shama_boot_params.svh").read_text() == render_boot_parameters(image)
    assert d["SHAMA_MINER_HISTORY_ENTRY"] == image.file_entry_indices["miner-history.log"]
    assert d["SHAMA_MINER_STATE_ENTRY"] == image.file_entry_indices["miner-state.cfg"]
    assert d["SHAMA_RAM_BYTES"] == 96 << 10
    assert d["SHAMA_CACHE_BYTES"] == 8 << 10
    assert d["SHAMA_VRAM_BYTES"] == 16 << 10
    assert d["SHAMA_CACHE_BANKS"] == 2
    assert d["SHAMA_VRAM_BANKS"] == 4
    assert d["SHAMA_FLASH_BYTES"] == len(image.image) == 250 << 10
    assert d["SHAMA_RAM_BANKS"] == 24
    assert d["SHAMA_FLASH_BANKS"] == 63
    assert d["SHAMA_FS_BLOCK_COUNT"] == fs.block_count == 1000
    assert d["SHAMA_FS_BITMAP_WORDS"] == (fs.block_count + 31) // 32 == 32
    assert d["SHAMA_FS_TABLE_FLASH_OFFSET"] == fs.table_start * fs.block_size == 512
    assert d["SHAMA_FS_DATA_START_BLOCK"] == fs.data_start == 18

    assert d["SHAMA_BUNDLE_FLASH_OFFSET"] == image.bundle_flash_offset
    assert d["SHAMA_SLOT_BYTES"] == APP_SLOT_BYTES
    assert d["SHAMA_SLOT_DATA_OFFSET"] == 4
    assert d["SHAMA_KERNEL_RAM_BASE"] == KERNEL_RAM_BASE
    assert d["SHAMA_APP_RAM_BASE"] == APP_RAM_BASE
    assert d["SHAMA_PC_KERNEL"] == KERNEL_RAM_BASE // 4
    assert d["SHAMA_PC_APP"] == 0
    for name, value in RTL_MEMORY_CONSTANTS.items():
        assert d[f"SHAMA_{name}"] == value

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
        assert d[f"SHAMA_{name.upper()}_BINARY_BYTES"] == image.slot_binary_lengths[name]

    for app, pc_word in image.app_pc_words.items():
        assert pc_word == 0
        assert d[f"SHAMA_PC_{app.upper()}"] == pc_word
