from shamaos.flashfs import FileType, ShamaFS
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


def test_miner_start_stop_and_valid_history():
    os = booted()
    # All-zero target accepts only zero; all-FF accepts the first hash.
    os.miner_configure(bytes(80), b"\xff" * 32, generation=7)
    os.miner_start()
    assert os.miner_step(1)
    assert os.miner.found
    assert not os.miner.running
    assert os.miner.saved_runs[0][0] == 7
    assert b"gen=7" in os.fs.read_file("miner-history.log")
