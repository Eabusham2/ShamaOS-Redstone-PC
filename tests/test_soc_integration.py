"""Real full-system logic/firmware integration with bounded memory bus models."""
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from shamaos.assembler import assemble, assemble_file
from shamaos.flashfs import FileType, ShamaFS
from shamaos.machine_spec import CACHE_BYTES, FLASH_BYTES, RAM_BYTES
from shamaos.os_image import build_default_os_image, render_boot_parameters
from shamaos.system_build import SOC_RTL_FILES

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def soc_executable(tmp_path_factory, os_image):
    if not shutil.which("iverilog") or not shutil.which("vvp"):
        pytest.skip("Icarus Verilog is required for full-system behavioral integration")
    snapshot = tmp_path_factory.mktemp("soc")
    executable = snapshot / "soc.vvp"
    source_paths = [p for p in SOC_RTL_FILES if p != "rtl/shama_memory_adapter.sv"]
    source_paths += ["tests/rtl/fast_memory_models.sv", "rtl/tb_soc.sv"]
    # These long simulations must use one coherent source/image revision even
    # when a parallel firmware task updates the checkout. Generate constants
    # from this exact image and compile a private copy of the RTL around it.
    for source in source_paths:
        destination = snapshot / source
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((ROOT / source).read_bytes())
    (snapshot / "rtl/shama_boot_params.svh").write_text(render_boot_parameters(os_image))
    sources = [str(snapshot / source) for source in source_paths]
    built = subprocess.run(
        ["iverilog", "-g2012", "-I", str(snapshot / "rtl"), "-s", "tb_soc", "-o", str(executable), *sources],
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


@pytest.mark.parametrize("broken_image,error", [
    ("blank_flash", -5), ("invalid_kernel_length", -7), ("kernel_crosses_packed_record", -7),
    ("bundle_missing", -5), ("bundle_moved", -5), ("bundle_renamed", -5),
    ("bundle_wrong_type", -5), ("bundle_wrong_size", -5),
])
def test_boot_failure_halts_with_original_diagnostics(soc_executable, os_image, tmp_path, broken_image, error):
    if broken_image == "blank_flash":
        image = bytes(len(os_image.image))
    else:
        image = bytearray(os_image.image)
        if broken_image.startswith("bundle_"):
            fs = ShamaFS.deserialize(os_image.image)
            offset = fs.table_start * fs.block_size + os_image.file_entry_indices["boot.bundle"] * 64
            if broken_image == "bundle_missing":
                image[offset] = 0
            elif broken_image == "bundle_moved":
                image[offset + 8:offset + 12] = (fs.stat("boot.bundle").start_block + 1).to_bytes(4, "little")
            elif broken_image == "bundle_renamed":
                image[offset + 21] = ord("x")
            elif broken_image == "bundle_wrong_type":
                image[offset + 1] = FileType.BIN
            else:
                image[offset + 4:offset + 8] = (os_image.bundle_bytes - 4).to_bytes(4, "little")
        else:
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


@pytest.mark.parametrize("fault,notice", [
    ("ram", 2), ("heap", 2), ("cache", 3), ("vram", 4), ("protected_flash", 1),
])
def test_real_app_resource_faults_recover_and_protected_flash_rejects_cpu_stores(
    soc_executable, os_image, tmp_path, fault, notice,
):
    programs = {
        "ram": f"LDI r1 {RAM_BYTES}\nLDB r2 r1 0\nHLT\n",
        "heap": "LDI r1 49153\nSYS SYS_ALLOC\nHLT\n",
        "cache": f"LDI r1 0x28000\nLDI r2 {CACHE_BYTES + 4}\nSYS SYS_RUN_BUFFER\nHLT\n",
        # A one-pixel BLIT beyond the complete sprite payload must fail before
        # DMA, and the following fence must not strand its syscall caller.
        "vram": "LDI r1 9\nLDI r2 1024\nLDI r3 0\nLDI r4 0\n"
                "LDI r5 1\nLDI r6 1\nSYS SYS_GPU_SUBMIT\nSYS SYS_GPU_FENCE\nHLT\n",
        "protected_flash": f"LDI r1 {0x01000000 + FLASH_BYTES - 4}\n"
                           "LDI r2 0xdeadbeef\nSTW r1 r2 0\nHLT\n",
    }
    fs = ShamaFS.deserialize(os_image.image)
    fs.create_file("ZFAULT.BIN", assemble(programs[fault]).to_bytes(), FileType.BIN)
    arguments = ["+recovery_flow", f"+expected_notice={notice}"]
    if fault == "protected_flash":
        fs.create_file("zzreserve.dat", bytes(fs.write_limit_bytes - fs.used_bytes))
        assert fs.used_bytes == fs.write_limit_bytes
        arguments.append("+protected_flash")
    image = fs.serialize()
    bundle_start = os_image.bundle_flash_offset
    bundle_end = bundle_start + os_image.bundle_bytes
    assert image[bundle_start:bundle_end] == os_image.image[bundle_start:bundle_end]
    output = run_soc(soc_executable, image, tmp_path, *arguments)
    assert f"SOC RESOURCE RECOVERY PASS cause={notice}" in output, output
