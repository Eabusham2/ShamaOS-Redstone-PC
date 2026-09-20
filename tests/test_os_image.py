from shamaos.flashfs import FileType, ShamaFS
from shamaos.os_image import FIRMWARE, build_default_os_image


def test_flash_contains_editable_sources_and_real_binaries():
    image = build_default_os_image()
    fs = ShamaFS.deserialize(image.image)

    for _name, (source_name, executable_name, executable_type) in FIRMWARE.items():
        source = fs.read_file(source_name)
        binary = fs.read_file(executable_name)
        assert source
        assert binary
        assert len(binary) % 4 == 0
        assert not binary.startswith(b"SHAMAOS_")
        assert fs.stat(source_name).file_type == FileType.ASM
        assert fs.stat(executable_name).file_type == executable_type


def test_preinstalled_source_is_visible_to_editor():
    image = build_default_os_image()
    fs = ShamaFS.deserialize(image.image)
    names = {e.name for e in fs.list_files()}
    assert {"editor.asm", "files.asm", "miner.asm", "desktop.asm"} <= names
