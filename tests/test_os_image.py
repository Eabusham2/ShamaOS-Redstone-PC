import pytest

from shamaos.flashfs import FileType, ShamaFS
from shamaos.memory_map import KERNEL_SLOT_BYTES, EDIT_NAME
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

    assert len(image.image) == 250 << 10
    assert len(image.boot_binary) <= 8 << 10

    bundle = fs.stat("boot.bundle")
    assert image.bundle_flash_offset == bundle.start_block * fs.block_size
    assert bundle.start_block == fs.data_start
    assert image.bundle_bytes == len(bundle.data)
    assert bundle.block_count == (image.bundle_bytes + fs.block_size - 1) // fs.block_size
    assert image.bundle_bytes == sum(4 + image.slot_binary_lengths[name] for name in BUNDLE_ORDER)
    assert image.bundle_bytes < len(BUNDLE_ORDER) * APP_SLOT_BYTES

    for _name, pc_word in image.app_pc_words.items():
        assert pc_word == 0

    bundle_data = fs.read_file("boot.bundle")
    start = 0
    for name in BUNDLE_ORDER:
        assert image.slot_flash_offsets[name] == image.bundle_flash_offset + start
        assert image.slot_flash_offsets[name] % 4 == 0
        encoded_len = int.from_bytes(bundle_data[start:start+4], "little")
        assert encoded_len == image.slot_binary_lengths[name]
        assert 0 < encoded_len <= APP_SLOT_BYTES - 4
        assert encoded_len <= 8 << 10
        executable_name = FIRMWARE[name][1]
        assert bundle_data[start + 4:start + 4 + encoded_len] == fs.read_file(executable_name)
        start += 4 + encoded_len
    assert start == len(bundle_data)

    assert KERNEL_RAM_BASE == 8 << 10
    assert APP_RAM_BASE == 0


def test_miner_persistent_records_have_fixed_capacity():
    image = build_default_os_image()
    fs = ShamaFS.deserialize(image.image)

    state = fs.stat("miner-state.cfg")
    history = fs.stat("miner-history.log")
    assert state.file_type == FileType.CFG
    assert history.file_type == FileType.LOG
    assert len(state.data) == 256
    assert len(history.data) == 32 << 10


def test_miner_hardware_file_entries_are_stable():
    image = build_default_os_image()
    fs = ShamaFS.deserialize(image.image)
    names = [entry.name for entry in fs.list_files()]
    assert names.index("miner-history.log") == image.file_entry_indices["miner-history.log"]
    assert names.index("miner-state.cfg") == image.file_entry_indices["miner-state.cfg"]
    for directory in ("documents","programs","projects","logs","config"):
        assert fs.stat(directory).file_type == FileType.DIR


def test_packed_boot_removes_padding_from_the_fixed_flash_capacity():
    image = build_default_os_image()
    fs = ShamaFS.deserialize(image.image)
    # Preserve the owner's explicit 250 KiB choice. Packing changes allocated
    # bytes, not flash capacity or the separate editable source/binary files.
    assert fs.flash_bytes == 250 << 10
    former_bundle_blocks = len(BUNDLE_ORDER) * APP_SLOT_BYTES // fs.block_size
    removed_padding = (former_bundle_blocks - fs.stat("boot.bundle").block_count) * fs.block_size
    assert removed_padding > 128 << 10
    assert fs.used_bytes + fs.free_bytes == fs.flash_bytes


@pytest.mark.parametrize('extra_word', [False, True], ids=['last_kernel_word', 'metadata_overlap'])
def test_kernel_cannot_overwrite_editor_metadata(monkeypatch, extra_word):
    import shamaos.os_image as module
    original = module._firmware_source
    kernel_source = 'NOP\n' * (KERNEL_SLOT_BYTES // 4 + int(extra_word))
    monkeypatch.setattr(module, '_firmware_source', lambda filename: kernel_source if filename == 'kernel.asm' else original(filename))
    assert KERNEL_RAM_BASE + KERNEL_SLOT_BYTES == EDIT_NAME
    if extra_word:
        with pytest.raises(ValueError, match='kernel firmware.*1024-byte executable capacity'):
            build_default_os_image()
    else:
        image = build_default_os_image()
        assert image.slot_binary_lengths['kernel'] == KERNEL_SLOT_BYTES
