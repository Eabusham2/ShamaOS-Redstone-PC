from shamaos.flashfs import FileType, ShamaFS
from shamaos.os_image import (
    APP_RAM_BASE,
    APP_SLOT_BYTES,
    BUNDLE_ORDER,
    FIRMWARE,
    KERNEL_RAM_BASE,
    build_default_os_image,
)


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
    assert image.bundle_bytes == len(BUNDLE_ORDER) * APP_SLOT_BYTES

    for slot, name in enumerate(BUNDLE_ORDER):
        assert image.slot_flash_offsets[name] == (
            image.bundle_flash_offset + slot * APP_SLOT_BYTES
        )

    for _name, pc_word in image.app_pc_words.items():
        assert pc_word == 0

    bundle_data = fs.read_file("boot.bundle")
    for slot, name in enumerate(BUNDLE_ORDER):
        start = slot * APP_SLOT_BYTES
        encoded_len = int.from_bytes(bundle_data[start:start+4], "little")
        assert encoded_len == image.slot_binary_lengths[name]
        assert 0 < encoded_len <= APP_SLOT_BYTES - 4

    assert KERNEL_RAM_BASE == 16 << 10
    assert APP_RAM_BASE == 32 << 10


def test_miner_persistent_records_have_fixed_capacity():
    image = build_default_os_image()
    fs = ShamaFS.deserialize(image.image)

    state = fs.stat("miner-state.cfg")
    history = fs.stat("miner-history.log")
    assert state.file_type == FileType.CFG
    assert history.file_type == FileType.LOG
    assert len(state.data) == 256
    assert len(history.data) == 16 << 10


def test_miner_hardware_file_entries_are_stable():
    image = build_default_os_image()
    fs = ShamaFS.deserialize(image.image)
    names = [entry.name for entry in fs.list_files()]
    assert names.index("miner-history.log") == 13
    assert names.index("miner-state.cfg") == 14
