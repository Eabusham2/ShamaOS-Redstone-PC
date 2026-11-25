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
    # The services bench takes 971,690 simulated clocks, including RAM
    # clearing and full-window DMA. Its 1.2M-clock watchdog also covers stalls.
    timeout = 120 if module == "services" else 30
    result = subprocess.run(["vvp", str(binary)], capture_output=True, text=True, timeout=timeout)
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


def test_queued_repeated_keys_each_receive_a_distinct_modal_ack(tmp_path):
    if not shutil.which("iverilog") or not shutil.which("vvp"):
        pytest.skip("Icarus Verilog required for joined services and input FIFO")
    executable = tmp_path / "modal"
    subprocess.run(["iverilog", "-g2012", "-I", ROOT / "rtl", "-s", "input_modal_fifo_tb",
                    "-o", executable, ROOT / "rtl/shama_input.sv", ROOT / "rtl/shama_services.sv",
                    ROOT / "tests/rtl/input_modal_fifo_tb.sv"], check=True, capture_output=True)
    result = subprocess.run(["vvp", executable], check=True, capture_output=True, text=True, timeout=15)
    assert "QUEUED MODAL FIFO PASS 10 exact ACKs" in result.stdout
