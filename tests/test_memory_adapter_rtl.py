"""Full-capacity address wrappers with real ring controls and layered-bank data."""
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("kind,capacity", [(0, 8*1024), (1, 192*1024), (2, 500*1024), (3, 16*1024)], ids=["cache", "ram", "flash", "vram"])
@pytest.mark.parametrize("layers", [8, 16, 20, 21, 24, 30, 31, 32], ids=lambda count: f"layers{count}")
def test_adapter_full_capacity_layer_boundaries_byte_merge_and_held_requests(tmp_path, kind, capacity, layers):
    if not shutil.which("iverilog") or not shutil.which("vvp"):
        pytest.skip("Icarus Verilog is required for memory adapter behavior")
    binary = tmp_path / "memory_adapter.vvp"
    files = ("shama_onehot_ring.sv", "shama_ring_memctl.sv", "shama_memory_adapter.sv", "tb_memory_adapter.sv")
    compiled = subprocess.run([
        "iverilog", "-g2012", "-I", str(ROOT / "rtl"), "-s", "tb_memory_adapter",
        f"-Ptb_memory_adapter.KIND={kind}", f"-Ptb_memory_adapter.LAYERS={layers}",
        "-o", str(binary), *(str(ROOT / "rtl" / file) for file in files),
    ], capture_output=True, text=True, timeout=30)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    result = subprocess.run(["vvp", str(binary)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"MEMORY ADAPTER LAYERED BOUNDARIES PASS kind={kind} layers={layers} bytes={capacity}" in result.stdout
