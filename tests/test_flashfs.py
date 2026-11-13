from shamaos.flashfs import FileType, ShamaFS


def test_round_trip_and_mutations():
    fs = ShamaFS()
    fs.create_file("hello.txt", b"hello", FileType.TXT)
    fs.create_file("code.asm", b"HLT\n", FileType.ASM)
    before_free = fs.free_bytes

    image = fs.serialize()
    loaded = ShamaFS.deserialize(image)

    assert loaded.read_file("hello.txt") == b"hello"
    assert loaded.read_file("code.asm") == b"HLT\n"

    loaded.rename("hello.txt", "notes.txt")
    assert loaded.read_file("notes.txt") == b"hello"

    loaded.write_file("notes.txt", b"updated", file_type=FileType.TXT)
    assert loaded.read_file("notes.txt") == b"updated"

    loaded.delete("code.asm")
    assert not loaded.exists("code.asm")
    assert loaded.free_bytes >= before_free


def test_full_image_size():
    fs = ShamaFS()
    assert len(fs.serialize()) == 200 << 10
    assert fs.block_count == 800
    assert fs.bitmap_blocks == 1
    assert fs.table_start == 2
    assert fs.data_start == 18


def test_paths_preserve_literal_spaces_across_persistence_and_reject_controls():
    import pytest

    fs = ShamaFS()
    fs.mkdir(" notes ")
    fs.create_file("/ notes / source.asm ", b"HLT\n", FileType.ASM)
    fs.create_file("source.asm", b"NOP\n", FileType.ASM)
    fs.create_file(" ", b"space is a filename", FileType.TXT)
    restored = ShamaFS.deserialize(fs.serialize())
    assert restored.read_file(" notes / source.asm ") == b"HLT\n"
    assert restored.read_file("source.asm") == b"NOP\n"
    assert restored.read_file(" ") == b"space is a filename"
    assert [entry.name for entry in restored.list_files(" notes ")] == [" notes / source.asm "]
    with pytest.raises(FileNotFoundError):
        restored.read_file("notes/source.asm")
    for byte in (0, 9, 10, 13, 31):
        for path in (chr(byte) + "invalid.txt", "invalid.txt" + chr(byte)):
            with pytest.raises(ValueError, match="control"):
                fs.create_file(path, b"must not be accepted", FileType.TXT)


def test_directory_paths_handles_and_app_cleanup():
    import pytest

    fs = ShamaFS()
    fs.mkdir('/user')
    fs.mkdir('/user/programs')
    fs.create_file('/user/programs/hello.asm', b'HLT\n', FileType.ASM)
    assert [e.name for e in fs.list_files('/user')] == ['user/programs']
    assert [e.name for e in fs.list_files('user/programs')] == ['user/programs/hello.asm']
    with pytest.raises(FileNotFoundError):
        fs.create_file('missing/file.txt')
    with pytest.raises(ValueError):
        fs.create_file('user/../escape.txt')
    with pytest.raises(OSError, match='not empty'):
        fs.delete('user/programs')
    with pytest.raises(OSError, match='nonempty'):
        fs.rename('user/programs', 'moved')
    with pytest.raises(IsADirectoryError):
        fs.read_file('user')

    handle = fs.open_file('/user/programs/hello.asm', owner=2)
    with pytest.raises(PermissionError):
        fs.read_handle(handle, 10, owner=1)
    fs.transfer_handle(handle, 1, owner=2)
    assert fs.close_owner(2) == 0
    assert fs.read_handle(handle, 10, owner=1) == b'HLT\n'
    fs.rename('user/programs/hello.asm', 'user/programs/new.asm')
    assert fs.read_handle(handle, 10, owner=1) == b'HLT\n'
    assert fs.close_owner(1) == 1
    with pytest.raises(OSError, match='closed'):
        fs.read_handle(handle, 10, owner=1)
    assert ShamaFS.deserialize(fs.serialize()).read_file('user/programs/new.asm') == b'HLT\n'


def test_truncate_shrink_zero_extend_and_file_type_zero():
    fs = ShamaFS()
    fs.create_file('value.txt', b'abcdef', FileType.TXT)
    fs.truncate('value.txt', 3)
    assert fs.read_file('value.txt') == b'abc'
    fs.truncate('value.txt', 300)
    assert fs.read_file('value.txt') == b'abc' + bytes(297)
    fs.write_file('value.txt', b'raw', file_type=FileType.DATA)
    assert fs.stat('value.txt').file_type == FileType.DATA
    fs.truncate('value.txt', 0)
    assert fs.stat('value.txt').block_count == 0
    assert ShamaFS.deserialize(fs.serialize()).read_file('value.txt') == b''


def test_interrupted_copy_on_write_journal_recovers_old_or_complete_new():
    import struct
    from shamaos.flashfs import (
        JOURNAL_OFFSET, JOURNAL_ENTRY_OFFSET, JOURNAL_CHECKSUM_OFFSET,
        JOURNAL_MAGIC, ENTRY_SIZE,
    )

    fs = ShamaFS()
    fs.create_file('a.txt', b'old data', FileType.TXT)
    old = fs.serialize()
    fs.write_file('a.txt', b'new and complete data')
    new = fs.serialize()
    table = fs.table_start * fs.block_size
    payload = new[table:table + ENTRY_SIZE]
    disk = bytearray(old)
    data_start = fs.stat('a.txt').start_block * fs.block_size
    data = fs.read_file('a.txt')
    disk[data_start:data_start + len(data)] = data
    # Orphaned copy-on-write allocation is not trusted after a reset.
    disk[fs.bitmap_start * fs.block_size + 10] = 0xff
    assert ShamaFS.deserialize(disk).read_file('a.txt') == b'old data'

    writes = [(JOURNAL_OFFSET + 4, struct.pack('<I', 0))]
    writes += [(JOURNAL_ENTRY_OFFSET + i, payload[i:i + 4]) for i in range(0, 64, 4)]
    writes += [(JOURNAL_CHECKSUM_OFFSET, struct.pack('<I', fs.journal_checksum(0, payload)))]
    commit_index = len(writes)
    writes += [(JOURNAL_OFFSET, struct.pack('<I', JOURNAL_MAGIC))]
    writes += [(table + i, payload[i:i + 4]) for i in range(0, 64, 4)]
    writes += [(JOURNAL_OFFSET, bytes(4))]
    for i, (offset, value) in enumerate(writes):
        disk[offset:offset + 4] = value
        recovered = ShamaFS.deserialize(disk)
        assert recovered.read_file('a.txt') == (b'old data' if i < commit_index else data)
        assert recovered.used_bytes == (fs.data_start + 1) * fs.block_size
    clean = ShamaFS.recover_image(disk)
    assert clean[JOURNAL_OFFSET:JOURNAL_OFFSET + 4] == bytes(4)


def test_corrupt_committed_journal_and_bad_extents_fail_explicitly():
    import struct
    import pytest
    from shamaos.flashfs import JOURNAL_OFFSET, JOURNAL_MAGIC

    fs = ShamaFS()
    fs.create_file('safe.txt', b'safe', FileType.TXT)
    image = bytearray(fs.serialize())
    struct.pack_into('<II', image, JOURNAL_OFFSET, JOURNAL_MAGIC, 0)
    with pytest.raises(ValueError, match='checksum'):
        ShamaFS.deserialize(image)
    image = bytearray(fs.serialize())
    struct.pack_into('<I', image, fs.table_start * fs.block_size + 8, 1)
    with pytest.raises(ValueError, match='outside'):
        ShamaFS.deserialize(image)


def test_95_percent_guard_is_atomic_persistent_and_has_a_reclamation_path():
    import pytest
    from shamaos.flashfs import FlashWriteProtectedError

    fs = ShamaFS()
    assert fs.write_limit_bytes == 194560
    fs.create_file("bulk.bin", bytes(fs.write_limit_bytes - fs.used_bytes - 256), FileType.BIN)
    fs.create_file("last.txt", b"", FileType.TXT)
    assert not fs.write_protected
    before = fs.serialize()
    # A COW reservation needs two blocks and is refused before any mutation.
    with pytest.raises(FlashWriteProtectedError, match="95%"):
        fs.write_file("last.txt", bytes(257))
    assert fs.serialize() == before

    fs.write_file("last.txt", b"last safe save")
    assert fs.used_bytes == fs.write_limit_bytes
    assert fs.write_protected
    fs = ShamaFS.deserialize(fs.serialize())
    assert fs.write_protected
    before = fs.serialize()
    for operation in (
        lambda: fs.write_file("last.txt", b"replacement"),
        lambda: fs.create_file("empty.txt"),
        lambda: fs.mkdir("folder"),
        lambda: fs.rename("last.txt", "renamed.txt"),
        lambda: fs.truncate("last.txt", 1),
    ):
        with pytest.raises(FlashWriteProtectedError):
            operation()
        assert fs.serialize() == before
    assert fs.read_file("last.txt") == b"last safe save"

    fs.truncate("last.txt", 0)
    assert not fs.write_protected
    fs.write_file("last.txt", b"new safe save")
    assert fs.write_protected
    fs.delete("last.txt")
    assert not fs.write_protected
    fs.create_file("recovered.txt", b"writable again", FileType.TXT)
    assert fs.read_file("recovered.txt") == b"writable again"


def test_full_imported_flash_mounts_protected_and_retains_last_physical_byte():
    import struct
    import pytest
    from shamaos.flashfs import ENTRY_FMT, FlashWriteProtectedError

    fs = ShamaFS()
    image = bytearray(fs.serialize())
    data_bytes = fs.flash_bytes - fs.data_start * fs.block_size
    struct.pack_into(ENTRY_FMT, image, fs.table_start * fs.block_size,
                     1, int(FileType.BIN), 0, data_bytes, fs.data_start,
                     fs.block_count - fs.data_start, 1, 8, b"full.bin")
    image[-1] = 0xA5
    loaded = ShamaFS.deserialize(image)
    assert loaded.used_bytes == 200 << 10
    assert loaded.free_bytes == 0
    assert loaded.write_protected
    assert loaded.read_file("full.bin")[-1] == 0xA5
    before = loaded.serialize()
    with pytest.raises(FlashWriteProtectedError):
        loaded.write_file("full.bin", b"must not replace the original")
    assert loaded.serialize() == before
    loaded.delete("full.bin")
    assert not loaded.write_protected


def test_private_boot_bundle_is_immutable_but_installed_sources_remain_editable():
    import pytest
    from shamaos.flashfs import BootBundleReadOnlyError
    from shamaos.os_image import build_default_os_image

    image = build_default_os_image()
    fs = ShamaFS.deserialize(image.image)
    original = fs.serialize()
    original_bundle = fs.stat('boot.bundle')
    operations = (
        lambda: fs.write_file('boot.bundle', b'replacement'),
        lambda: fs.truncate('boot.bundle', 0),
        lambda: fs.rename('boot.bundle', 'renamed.bundle'),
        lambda: fs.delete('boot.bundle'),
    )
    for mutate in operations:
        with pytest.raises(BootBundleReadOnlyError):
            mutate()
        assert fs.serialize() == original
    source = fs.read_file('editor.asm') + b'\n; user edit\n'
    fs.write_file('editor.asm', source)
    fs.write_file('editor.bin', b'\0' * 4)
    assert fs.read_file('editor.asm') == source
    assert fs.read_file('editor.bin') == b'\0' * 4
    assert fs.stat('boot.bundle') == original_bundle
