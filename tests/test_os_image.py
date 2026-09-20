from shamaos.flashfs import FileType, ShamaFS
from shamaos.os_image import APP_SLOT_BYTES, BUNDLE_RAM_BASE, FIRMWARE, build_default_os_image


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


def test_default_boot_bundle_geometry():
    image = build_default_os_image()
    fs = ShamaFS.deserialize(image.image)

    assert len(image.image) == 4 << 20
    assert len(image.boot_binary) <= 16 << 10

    bundle = fs.stat("boot.bundle")
    assert image.bundle_flash_offset == bundle.start_block * fs.block_size
    assert bundle.start_block == fs.data_start
    assert image.bundle_bytes == bundle.block_count * fs.block_size
    assert image.bundle_bytes == 10 * APP_SLOT_BYTES

    for name, pc_word in image.app_pc_words.items():
        byte_address = pc_word * 4
        assert BUNDLE_RAM_BASE <= byte_address < (1 << 20)
        assert byte_address % APP_SLOT_BYTES == 0
