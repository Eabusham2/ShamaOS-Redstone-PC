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


def test_kernel_fence_waits_for_later_queued_drawing_and_counter_wrap(tmp_path):
    if not shutil.which('iverilog') or not shutil.which('vvp'):
        pytest.skip('Icarus Verilog required for kernel/GPU integration')
    binary=tmp_path/'kernel_fence'
    sources=['rtl/shama_kernel_accel.sv','rtl/shama_gpu.sv','rtl/shama_font5x7.sv','rtl/tb_kernel_fence.sv']
    subprocess.run(['iverilog','-g2012','-I'+str(ROOT/'rtl'),'-s','tb_kernel_fence','-o',str(binary),
                    *(str(ROOT/path) for path in sources)],check=True,capture_output=True,text=True)
    result=subprocess.run(['vvp',str(binary)],capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'KERNEL GPU FENCE QUEUED WORK AND WRAP PASS' in result.stdout


def test_modal_popup_preserves_and_restores_all_underlying_display_pixels(tmp_path):
    if not shutil.which('iverilog') or not shutil.which('vvp'):
        pytest.skip('Icarus Verilog required for real popup rendering')
    binary=tmp_path/'kernel_popup'
    sources=['rtl/shama_kernel_accel.sv','rtl/shama_gpu.sv','rtl/shama_font5x7.sv','rtl/tb_kernel_popup.sv']
    compiled=subprocess.run(['iverilog','-g2012','-I'+str(ROOT/'rtl'),'-s','tb_kernel_popup','-o',str(binary),
                             *(str(ROOT/path) for path in sources)],capture_output=True,text=True,timeout=30)
    assert compiled.returncode==0,compiled.stdout+compiled.stderr
    result=subprocess.run(['vvp',str(binary)],capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'KERNEL MODAL POPUP PIXEL RESTORE PASS' in result.stdout


def test_popup_drains_faulting_app_work_and_uses_latest_uncancelled_choice(tmp_path):
    if not shutil.which('iverilog') or not shutil.which('vvp'):
        pytest.skip('Icarus Verilog required for popup/resource-fault interleaving')
    binary=tmp_path/'popup_fault_interleave'
    sources=['rtl/shama_kernel_accel.sv','rtl/shama_gpu.sv','rtl/shama_font5x7.sv','rtl/tb_popup_fault_interleave.sv']
    compiled=subprocess.run(['iverilog','-g2012','-I'+str(ROOT/'rtl'),'-s','tb_popup_fault_interleave','-o',str(binary),
                             *(str(ROOT/path) for path in sources)],capture_output=True,text=True,timeout=30)
    assert compiled.returncode==0,compiled.stdout+compiled.stderr
    result=subprocess.run(['vvp',str(binary)],capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'KERNEL POPUP RESOURCE FAULT INTERLEAVING PASS' in result.stdout
