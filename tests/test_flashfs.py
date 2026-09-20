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
    fs = ShamaFS(1 << 20)
    assert len(fs.serialize()) == 1 << 20
