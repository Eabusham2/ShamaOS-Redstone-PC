import shutil,subprocess
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]


def test_real_kernel_renders_selection_used_totals_safe_refresh_and_diagnostics(tmp_path):
    if not shutil.which('iverilog') or not shutil.which('vvp'):
        pytest.skip('Icarus Verilog required for kernel hardware behavior')
    binary=tmp_path/'kernel_ui'
    subprocess.run(['iverilog','-g2012','-I'+str(ROOT/'rtl'),'-s','tb_kernel_ui','-o',str(binary),str(ROOT/'rtl/shama_kernel_accel.sv'),str(ROOT/'rtl/tb_kernel_ui.sv')],check=True,capture_output=True,text=True)
    result=subprocess.run(['vvp',str(binary)],capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'KERNEL UI BEHAVIOR PASS' in result.stdout
