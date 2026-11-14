"""Full-capacity direct-address controls with delayed layered-bank data."""
from pathlib import Path
import shutil
import subprocess
import re

import pytest

from shamaos.machine_spec import RAM_BYTES, CACHE_BYTES, FLASH_BYTES, VRAM_BYTES

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("kind,capacity", [(0, CACHE_BYTES), (1, RAM_BYTES), (2, FLASH_BYTES), (3, VRAM_BYTES)], ids=["cache", "ram", "flash", "vram"])
@pytest.mark.parametrize("layers", [8, 16, 20, 21, 24, 30, 31, 32], ids=lambda count: f"layers{count}")
def test_adapter_full_capacity_layer_boundaries_byte_merge_and_held_requests(tmp_path, kind, capacity, layers):
    if not shutil.which("iverilog") or not shutil.which("vvp"):
        pytest.skip("Icarus Verilog is required for memory adapter behavior")
    binary = tmp_path / "memory_adapter.vvp"
    files = ("shama_memory_adapter.sv", "tb_memory_adapter.sv")
    compiled = subprocess.run([
        "iverilog", "-g2012", "-I", str(ROOT / "rtl"), "-s", "tb_memory_adapter",
        f"-Ptb_memory_adapter.KIND={kind}", f"-Ptb_memory_adapter.LAYERS={layers}",
        "-o", str(binary), *(str(ROOT / "rtl" / file) for file in files),
    ], capture_output=True, text=True, timeout=30)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    result = subprocess.run(["vvp", str(binary)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"MEMORY ADAPTER LAYERED BOUNDARIES PASS kind={kind} layers={layers} bytes={capacity}" in result.stdout
    if capacity % 4096:
        assert f"PARTIAL BANK EXACT CAPACITY PASS banks={(capacity + 4095) // 4096} tail_rows={(capacity % 4096) // 4}" in result.stdout


def test_adapter_reset_suppresses_commit_without_resetting_persistent_cells(tmp_path):
    if not shutil.which("iverilog") or not shutil.which("vvp"):
        pytest.skip("Icarus Verilog is required for memory adapter behavior")
    binary = tmp_path / "memory_adapter_reset.vvp"
    compiled = subprocess.run([
        "iverilog", "-g2012", "-I", str(ROOT / "rtl"), "-s", "tb_memory_adapter_reset",
        "-o", str(binary), str(ROOT / "rtl/shama_memory_adapter.sv"),
        str(ROOT / "rtl/tb_memory_adapter_reset.sv"),
    ], capture_output=True, text=True, timeout=30)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    result = subprocess.run(["vvp", str(binary)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "DIRECT RESET WRITE-SUPPRESSION AND RETENTION PASS" in result.stdout


@pytest.mark.parametrize("kind,region", [(0, "CACHE"), (1, "RAM"), (2, "FLASH"), (3, "VRAM")])
@pytest.mark.parametrize("capacity", [1024, 2048, 4096])
def test_single_bank_wrappers_keep_legal_fields_and_exact_capacity(tmp_path, kind, region, capacity):
    """Exercise real wrapper elaboration, byte merges, reset and rejected tails.

    The full boundary bench is reused with a generated one-bank header. A
    constant in Python cannot hide a zero-width Verilog bank address.
    """
    if not shutil.which("iverilog") or not shutil.which("vvp"):
        pytest.skip("Icarus Verilog is required for memory adapter behavior")
    header = (ROOT / "rtl/shama_boot_params.svh").read_text()
    for symbol, value in ((f"SHAMA_{region}_BYTES", capacity), (f"SHAMA_{region}_BANKS", 1)):
        header, count = re.subn(rf"(?m)^(`define\s+{symbol}\s+).*$", rf"\g<1>32'h{value:08x}", header)
        assert count == 1
    (tmp_path / "shama_boot_params.svh").write_text(header)
    for name in ("shama_memory_adapter.sv", "tb_memory_adapter.sv"):
        shutil.copyfile(ROOT / "rtl" / name, tmp_path / name)
    binary = tmp_path / "single-bank.vvp"
    compiled = subprocess.run([
        "iverilog", "-g2012", "-I", str(tmp_path), "-s", "tb_memory_adapter",
        f"-Ptb_memory_adapter.KIND={kind}", "-Ptb_memory_adapter.LAYERS=1",
        "-o", str(binary), str(tmp_path / "shama_memory_adapter.sv"), str(tmp_path / "tb_memory_adapter.sv"),
    ], capture_output=True, text=True, timeout=30, cwd=tmp_path)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    result = subprocess.run(["vvp", str(binary)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"MEMORY ADAPTER LAYERED BOUNDARIES PASS kind={kind} layers=1 bytes={capacity}" in result.stdout
    if capacity < 4096:
        assert f"PARTIAL BANK EXACT CAPACITY PASS banks=1 tail_rows={capacity // 4}" in result.stdout
