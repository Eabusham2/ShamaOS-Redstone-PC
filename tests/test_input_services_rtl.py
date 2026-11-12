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
