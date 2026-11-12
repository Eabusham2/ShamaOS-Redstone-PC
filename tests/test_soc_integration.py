"""Real full-system logic/firmware integration with bounded memory bus models."""
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from shamaos.assembler import assemble_file
from shamaos.os_image import build_default_os_image
from shamaos.system_build import SOC_RTL_FILES

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def soc_executable(tmp_path_factory):
    if not shutil.which("iverilog") or not shutil.which("vvp"):
        pytest.skip("Icarus Verilog is required for full-system behavioral integration")
    executable = tmp_path_factory.mktemp("soc") / "soc.vvp"
    sources = [str(ROOT / p) for p in SOC_RTL_FILES if p != "rtl/shama_memory_adapter.sv"]
    sources += [str(ROOT / "tests/rtl/fast_memory_models.sv"), str(ROOT / "rtl/tb_soc.sv")]
    built = subprocess.run(
        ["iverilog", "-g2012", "-I", str(ROOT / "rtl"), "-s", "tb_soc", "-o", str(executable), *sources],
        capture_output=True, text=True, timeout=60,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    return executable


@pytest.fixture(scope="module")
def os_image():
    return build_default_os_image()


def run_soc(executable, image, tmp_path, *arguments, timeout=600):
    flash = tmp_path / "flash.hex"
    flash.write_text("\n".join(
        f'{int.from_bytes(image[i:i + 4], "little"):08x}' for i in range(0, len(image), 4)
    ) + "\n")
    run = subprocess.run(
        ["vvp", str(executable), f"+flash={flash}", *arguments],
        capture_output=True, text=True, timeout=timeout,
    )
    (tmp_path / "soc-output.txt").write_text(run.stdout + run.stderr)
    assert run.returncode == 0, run.stdout + run.stderr
    return run.stdout


def test_immutable_boot_rom_matches_checked_boot_firmware():
    source = (ROOT / "rtl/shama_soc.sv").read_text()
    boot_function = source.split("function automatic [31:0] boot_word", 1)[1].split("endfunction", 1)[0]
    entries = {int(address): int(word, 16) for address, word in re.findall(
        r"(\d+):boot_word=32'h([0-9a-fA-F]+);", boot_function
    )}
    expected = assemble_file(ROOT / "src/shamaos/firmware/boot.asm").words
    assert entries == {4 * index: word for index, word in enumerate(expected)}


@pytest.mark.parametrize("broken_image,error", [("blank_flash", -5), ("invalid_kernel_length", -7), ("kernel_crosses_packed_record", -7)])
def test_boot_failure_halts_with_original_diagnostics(soc_executable, os_image, tmp_path, broken_image, error):
    if broken_image == "blank_flash":
        image = bytes(len(os_image.image))
    else:
        image = bytearray(os_image.image)
        offset = os_image.slot_flash_offsets["kernel"]
        length = 3 if broken_image == "invalid_kernel_length" else os_image.slot_binary_lengths["kernel"] + 4
        image[offset:offset + 4] = length.to_bytes(4, "little")
    output = run_soc(soc_executable, image, tmp_path, "+expect_boot_failure", f"+boot_error={error}", timeout=90)
    assert f"SOC BOOT FAILURE PASS error={error}" in output, output


def test_boot_edit_dirty_close_shutdown_reboot_and_explorer_handoff(soc_executable, os_image, tmp_path):
    output = run_soc(soc_executable, os_image.image, tmp_path)
    assert "SOC INTEGRATION PASS" in output, output


def test_physical_keyboard_source_assembly_runs_saved_binary_and_exits_halted_app(soc_executable, os_image, tmp_path):
    output = run_soc(soc_executable, os_image.image, tmp_path, "+program_flow")
    assert "SOC PROGRAM ASSEMBLE RUN HOME PASS" in output, output
