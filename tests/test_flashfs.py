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
    fs = ShamaFS(4 << 20)
    assert len(fs.serialize()) == 4 << 20


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
