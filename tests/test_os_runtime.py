import hashlib
import struct

import pytest

from shamaos.flashfs import FileType, FlashWriteProtectedError, ShamaFS
from shamaos.os_image import build_default_os_image
from shamaos.os_runtime import ShamaOSRuntime


def booted():
    image = build_default_os_image()
    os = ShamaOSRuntime.from_flash_image(image.image)
    os.power_on()
    return os


def test_boot_and_resource_accounting():
    os = booted()
    assert os.foreground.name == "desktop"
    s = os.resources()
    assert 0 < s.ram_used < s.ram_total
    assert 0 <= s.cache_used <= s.cache_total
    assert 0 < s.flash_used < s.flash_total


def test_editor_create_save_assemble_and_rename():
    os = booted()
    os.universal_action("EDITOR")
    assert os.foreground.name == "editor"
    os.editor_new_program("counter")
    os.editor_set_buffer("LDI r1 2\n.loop\nDEC r1\nBR.NE .loop\nHLT\n")
    os.editor_save()
    assert os.fs.exists("counter.asm")
    binary = os.editor_assemble()
    assert binary == "counter.bin"
    assert os.fs.stat(binary).file_type == FileType.BIN
    os.editor_rename("renamed")
    assert os.fs.exists("renamed.asm")


def test_explorer_edit_closes_explorer_and_opens_editor():
    os = booted()
    os.fs.create_file("notes.txt", b"hello", FileType.TXT)
    os.universal_action("FILES")
    files_pid = os.foreground.pid
    text = os.explorer_open("notes.txt", edit=True)
    assert text == "hello"
    assert os.foreground.name == "editor"
    assert files_pid not in os.processes


def test_delete_requires_confirmation():
    os = booted()
    os.fs.create_file("killme.txt", b"x", FileType.TXT)
    os.request_delete("killme.txt")
    assert not os.confirm_delete(confirm=False)
    assert os.fs.exists("killme.txt")
    os.request_delete("killme.txt")
    assert os.confirm_delete(confirm=True)
    assert not os.fs.exists("killme.txt")


def test_exit_reclaims_app_ram_and_cache():
    os = booted()
    base = os.resources()
    os.universal_action("EDITOR")
    with_editor = os.resources()
    assert with_editor.ram_used >= base.ram_used
    os.universal_action("EXIT")
    after = os.resources()
    assert os.foreground.name == "desktop"
    assert after.ram_used <= with_editor.ram_used
    assert after.cache_used <= with_editor.cache_used


def test_all_app_replacements_fit_halved_ram_without_hidden_desktop_allocation():
    os = booted()
    for app in ('editor', 'files', 'miner', 'monitor', 'terminal', 'calculator', 'paint', 'settings', 'desktop'):
        os.launch(app, close_foreground=True)
        assert os.foreground.name == app
        assert len(os.processes) == 1
        assert os.resources().ram_used <= os.resources().ram_total == 96 << 10


def test_miner_start_stop_and_valid_history():
    os = booted()
    # All-zero target accepts only zero; all-FF accepts the first hash.
    os.miner_configure(bytes(80), b"\xff" * 32, generation=7)
    os.miner_start()
    assert os.miner_step(1)
    assert os.miner.found
    assert not os.miner.running
    assert os.miner.saved_runs[0][0] == 7
    history = os.fs.read_file("miner-history.log")
    record = history[7 * 128:8 * 128]
    assert len(history) == 32768
    assert struct.unpack_from("<4IQII", record) == (0x31524D53, 1, 7, 0, 1, 2, 0)
    assert record[48:80] == hashlib.sha256(hashlib.sha256(bytes(80)).digest()).digest()
    assert record[80:112] == b"\xff" * 32
    assert history[:7 * 128] + history[8 * 128:] == bytes(32768 - 128)
    assert os.fs.read_file("miner-state.cfg")[:128] == record
    assert len(os.fs.read_file("miner-state.cfg")) == 256


def test_miner_history_uses_all_slots_then_wraps_without_growing_flash():
    os = booted()
    original = {entry.name: entry.data for entry in os.fs.list_files()}
    history_start = os.fs.stat("miner-history.log").start_block
    used = os.fs.used_bytes
    expected = bytearray(32768)
    for generation in range(258):
        header = bytearray(80)
        header[76:80] = generation.to_bytes(4, "little")
        os.miner_configure(bytes(header), b"\xff" * 32, generation=generation)
        os.miner_start()
        assert os.miner_step(1)
        offset = (generation & 255) * 128
        record = os.fs.read_file("miner-state.cfg")[:128]
        assert int.from_bytes(record[8:12], "little") == generation
        assert int.from_bytes(record[12:16], "little") == generation
        expected[offset:offset + 128] = record
        assert os.fs.read_file("miner-history.log") == expected
        assert os.fs.used_bytes == used
        assert os.fs.stat("miner-history.log").start_block == history_start
    reopened = ShamaFS.deserialize(os.flash_image())
    assert reopened.read_file("miner-history.log") == expected
    for name, data in original.items():
        if name not in ("miner-history.log", "miner-state.cfg"):
            assert reopened.stat(name).data == data


def test_miner_record_retains_full_width_counters_hash_target_and_header():
    os = booted()
    header = bytes(range(76)) + b"\xff" * 4
    os.miner_configure(header, b"\xff" * 32, generation=0xFFFFFFFF)
    os.miner.state.attempts = 0xFFFFFFFFFFFFFFFE
    os.miner_start()
    assert os.miner_step(1)
    snapshot = os.fs.read_file("miner-state.cfg")
    assert struct.unpack_from("<IIQ", snapshot, 8) == (
        0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFFFFFFFFFF)
    assert snapshot[48:80] == hashlib.sha256(hashlib.sha256(header).digest()).digest()
    assert snapshot[80:112] == b"\xff" * 32
    assert snapshot[128:208] == header
    assert os.fs.read_file("miner-history.log")[255 * 128:] == snapshot[:128]


def test_miner_history_protection_preserves_existing_records():
    os = booted()
    os.fs.create_file("fill.dat", bytes(os.fs.write_limit_bytes - os.fs.used_bytes))
    before = os.flash_image()
    os.miner_configure(bytes(80), b"\xff" * 32, generation=7)
    os.miner_start()
    with pytest.raises(FlashWriteProtectedError):
        os.miner_step(1)
    assert os.flash_image() == before
    assert not os.miner.saved_runs
