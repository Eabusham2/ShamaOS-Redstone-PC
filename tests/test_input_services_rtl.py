"""Input and OS lifecycle behavior through their real RTL test benches."""
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("module,marker", [
    ("input", "INPUT FIFO UNIVERSAL PRIORITY PRINTABLE ASCII SHIFT PASS"),
    ("services", "SERVICES SAVE CLOSE OWNERSHIP CLEAR FLUSH SNAPSHOT SHUTDOWN PASS"),
    ("fs_accel", "SHAMAFS TRANSACTIONS OWNERSHIP DIRECTORIES TRUNCATE MINER PASS"),
])
def test_input_and_services_protocols(tmp_path, module, marker):
    if not shutil.which("iverilog") or not shutil.which("vvp"):
        pytest.skip("Icarus Verilog is required for input/services behavior")
    binary = tmp_path / f"{module}.vvp"
    compiled = subprocess.run([
        "iverilog", "-g2012", "-I", str(ROOT / "rtl"), "-s", f"tb_{module}", "-o", str(binary),
        str(ROOT / f"rtl/shama_{module}.sv"), str(ROOT / f"rtl/tb_{module}.sv"),
    ], capture_output=True, text=True, timeout=30)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    result = subprocess.run(["vvp", str(binary)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert marker in result.stdout


def test_filesystem_transactions_and_final_flash_word_through_production_adapters(tmp_path):
    from shamaos.machine_spec import FLASH_BYTES

    if not shutil.which("iverilog") or not shutil.which("vvp"):
        pytest.skip("Icarus Verilog is required for filesystem controller integration")
    binary = tmp_path / "fs_real_adapters.vvp"
    compiled = subprocess.run([
        "iverilog", "-g2012", "-DSHAMA_REAL_FS_ADAPTERS", "-I", str(ROOT / "rtl"),
        "-s", "tb_fs_accel", "-o", str(binary), str(ROOT / "rtl/shama_fs_accel.sv"),
        str(ROOT / "rtl/shama_memory_adapter.sv"), str(ROOT / "tests/rtl/persistent_bank_models.sv"),
        str(ROOT / "rtl/tb_fs_accel.sv"),
    ], capture_output=True, text=True, timeout=30)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    result = subprocess.run(["vvp", str(binary)], capture_output=True, text=True, timeout=180)
    (tmp_path / "filesystem-output.txt").write_text(result.stdout + result.stderr)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"SHAMAFS REAL ADAPTERS EXACT FLASH TAIL PASS bytes={FLASH_BYTES} final_row={(FLASH_BYTES // 4 - 1) % 1024}" in result.stdout
    assert "SHAMAFS TRANSACTIONS OWNERSHIP DIRECTORIES TRUNCATE MINER PASS" in result.stdout
