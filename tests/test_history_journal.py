"""Bounded Miner record persistence using the complete installed flash image."""
from pathlib import Path
import shutil
import struct
import subprocess

import pytest

from shamaos.flashfs import (
    ENTRY_FMT, ENTRY_SIZE, HISTORY_JOURNAL_DATA_OFFSET, HISTORY_JOURNAL_MAGIC,
    HISTORY_JOURNAL_CHECKSUM_OFFSET, JOURNAL_OFFSET, FileType, ShamaFS,
    FlashWriteProtectedError,
)
from shamaos.os_image import build_default_os_image, render_boot_parameters

ROOT = Path(__file__).resolve().parents[1]
HISTORY = "miner-history.log"


def record(slot, count=128, sequence=0):
    return bytes((slot * 17 + i * 29 + 3 + sequence) & 255 for i in range(count)).ljust(128, b"\0")


@pytest.fixture(scope="module")
def installed_image():
    return build_default_os_image()


@pytest.fixture(scope="module")
def history_runner(tmp_path_factory, installed_image):
    if not shutil.which("iverilog") or not shutil.which("vvp"):
        pytest.skip("Icarus Verilog is required for actual history-journal recovery")
    directory = tmp_path_factory.mktemp("history-journal-rtl")
    (directory / "shama_boot_params.svh").write_text(render_boot_parameters(installed_image))
    # Freeze the exact implementation while other agents work on the checkout.
    for source in ("rtl/shama_fs_accel.sv", "tests/rtl/tb_history_journal.sv"):
        (directory / Path(source).name).write_bytes((ROOT / source).read_bytes())
    binary = directory / "history.vvp"
    result = subprocess.run([
        "iverilog", "-g2012", "-I", str(directory), "-s", "tb_history_journal",
        "-o", str(binary), str(directory / "shama_fs_accel.sv"),
        str(directory / "tb_history_journal.sv"),
    ], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    return binary


def run_history(binary, image, directory, *arguments, trace=False):
    source, destination = directory / "input.hex", directory / "output.hex"
    source.write_text("\n".join(f"{value:08x}" for value in struct.unpack(f"<{len(image)//4}I", image)) + "\n")
    args = ["vvp", str(binary), f"+image={source}", f"+dump={destination}", *arguments]
    if trace:
        args.append(f"+trace={directory / 'writes.txt'}")
    result = subprocess.run(args, capture_output=True, text=True, timeout=45)
    (directory / "output.txt").write_text(result.stdout + result.stderr)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "HISTORY JOURNAL PASS" in result.stdout
    values = [int(line, 16) for line in destination.read_text().splitlines() if not line.startswith("//")]
    return struct.pack(f"<{len(values)}I", *values)


def committed_record_image(image, slot=63):
    fs = ShamaFS.deserialize(image)
    names = [entry.name for entry in fs.list_files()]
    index = names.index(HISTORY)
    entry = fs.stat(HISTORY)
    value = record(slot)
    result = bytearray(image)
    checksum = fs.history_journal_checksum(index, slot, entry.start_block, entry.generation, value)
    struct.pack_into("<6I", result, JOURNAL_OFFSET, HISTORY_JOURNAL_MAGIC, index,
                     slot, entry.start_block, entry.generation, checksum)
    result[HISTORY_JOURNAL_DATA_OFFSET:HISTORY_JOURNAL_DATA_OFFSET + 128] = value
    return result


def expected_history(old, slot=63, count=128, sequence=0):
    return old[:slot * 128] + record(slot, count, sequence) + old[(slot + 1) * 128:]


def test_complete_installed_image_all_records_wrap_short_payloads_and_state_interleaving(history_runner, installed_image, tmp_path):
    before = ShamaFS.deserialize(installed_image.image)
    # App compaction leaves more free flash. Keep this fixture under the write
    # guard but too full for the old whole-history copy, without removing or
    # replacing any installed file.
    history_bytes = len(before.read_file(HISTORY))
    pressure_blocks = max(0, (before.write_limit_bytes - before.used_bytes - history_bytes) // before.block_size + 1)
    if pressure_blocks:
        before.create_file("zz-history-pressure", bytes(pressure_blocks * before.block_size), FileType.DATA)
    assert not before.write_protected
    assert before.used_bytes + history_bytes > before.write_limit_bytes, "fixture must reproduce the old 32 KiB COW failure"
    result = ShamaFS.deserialize(run_history(history_runner, before.serialize(), tmp_path, "+mode=bulk"))
    expected = b"".join(record(slot) for slot in range(256))
    for slot, count, sequence in ((0, 128, 5), (64, 17, 2), (128, 127, 3), (255, 128, 4)):
        expected = expected_history(expected, slot, count, sequence)
    assert result.read_file(HISTORY) == expected
    assert result.stat(HISTORY).start_block == before.stat(HISTORY).start_block
    assert result.used_bytes == before.used_bytes
    assert result.read_file("miner-state.cfg") == bytes((255 * 17 + i * 29 + 4) & 255 for i in range(256))
    for entry in before.list_files():
        if entry.name not in (HISTORY, "miner-state.cfg"):
            assert result.stat(entry.name) == entry


def test_every_real_dma_write_prefix_is_old_or_complete_new(history_runner, installed_image, tmp_path):
    original = ShamaFS.deserialize(installed_image.image)
    old = original.read_file(HISTORY)
    complete = run_history(history_runner, installed_image.image, tmp_path, trace=True)
    writes = [tuple(int(value, 16) for value in line.split()) for line in (tmp_path / "writes.txt").read_text().splitlines()]
    assert len(writes) == 264
    # Only the 128-byte record, its small journal and one generation word change.
    start = original.stat(HISTORY).start_block * 256 + 63 * 128
    index = installed_image.file_entry_indices[HISTORY]
    generation_address = original.table_start * 256 + index * ENTRY_SIZE + 16
    for address, _, _ in writes:
        assert 64 <= address < 216 or start <= address < start + 128 or address == generation_address
    image = bytearray(installed_image.image)
    committed = False
    for step, (address, value, strobe) in enumerate(writes, 1):
        for lane in range(4):
            if strobe & (1 << lane):
                image[(address & ~3) + lane] = (value >> (lane * 8)) & 255
        if address == JOURNAL_OFFSET and value == HISTORY_JOURNAL_MAGIC:
            assert step == 134
            committed = True
        recovered = ShamaFS.deserialize(image)
        assert recovered.read_file(HISTORY) == (expected_history(old) if committed else old), step
        assert recovered.used_bytes == original.used_bytes
        assert recovered.stat(HISTORY).start_block == original.stat(HISTORY).start_block
        assert recovered.stat(HISTORY).generation == original.stat(HISTORY).generation + int(committed)
        for entry in original.list_files():
            if entry.name != HISTORY:
                assert recovered.stat(entry.name) == entry
    assert ShamaFS.deserialize(complete).read_file(HISTORY) == expected_history(old)


@pytest.mark.parametrize("cut", [0, 1, 127, 128, 129, 132, 133, 134, 135, 197, 261, 262, 263, 264])
def test_actual_controller_resets_before_commit_during_replay_and_after_clear(history_runner, installed_image, tmp_path, cut):
    result = ShamaFS.deserialize(run_history(history_runner, installed_image.image, tmp_path, f"+cut={cut}"))
    old = ShamaFS.deserialize(installed_image.image)
    assert result.read_file(HISTORY) == (expected_history(old.read_file(HISTORY)) if cut >= 134 else old.read_file(HISTORY))
    assert result.used_bytes == old.used_bytes


@pytest.mark.parametrize("corruption", ["marker", "payload", "checksum", "slot", "start", "generation", "entry"])
def test_invalid_record_journal_rejects_before_any_flash_write(history_runner, installed_image, tmp_path, corruption):
    image = committed_record_image(installed_image.image)
    if corruption == "marker":
        struct.pack_into("<I", image, 64, 0xDEADBEEF)
    elif corruption == "payload":
        image[HISTORY_JOURNAL_DATA_OFFSET + 127] ^= 1
    elif corruption == "checksum":
        image[HISTORY_JOURNAL_CHECKSUM_OFFSET] ^= 1
    else:
        index, slot, start, generation, _ = struct.unpack_from("<5I", image, 68)
        if corruption == "slot":
            slot = 256
        elif corruption == "start":
            start += 1
        elif corruption == "generation":
            generation += 7
        else:
            index = installed_image.file_entry_indices["boot.bundle"]
        value = bytes(image[HISTORY_JOURNAL_DATA_OFFSET:HISTORY_JOURNAL_DATA_OFFSET + 128])
        checksum = ShamaFS.history_journal_checksum(index, slot, start, generation, value)
        struct.pack_into("<5I", image, 68, index, slot, start, generation, checksum)
    with pytest.raises(ValueError, match="journal"):
        ShamaFS.deserialize(image)
    result = run_history(history_runner, image, tmp_path, "+mode=bad-mount")
    assert result == bytes(image)


def test_protected_import_replays_record_but_rejects_new_history_write(history_runner, installed_image, tmp_path):
    fs = ShamaFS.deserialize(installed_image.image)
    fs.create_file("zz-reserve.bin", bytes(fs.write_limit_bytes - fs.used_bytes), FileType.BIN)
    assert fs.write_protected
    image = committed_record_image(fs.serialize())
    result = ShamaFS.deserialize(run_history(history_runner, image, tmp_path, "+mode=protected"))
    assert result.write_protected
    assert result.read_file(HISTORY) == expected_history(bytes(32768))
    assert result.read_file("zz-reserve.bin") == fs.read_file("zz-reserve.bin")


@pytest.mark.parametrize("cut", [1, 64, 128, 129, 130])
def test_interrupted_recovery_is_idempotent_including_generation_and_clear(history_runner, installed_image, tmp_path, cut):
    original = ShamaFS.deserialize(installed_image.image)
    image = committed_record_image(installed_image.image)
    result = ShamaFS.deserialize(run_history(history_runner, image, tmp_path, "+mode=recovery-cut", f"+cut={cut}"))
    assert result.read_file(HISTORY) == expected_history(original.read_file(HISTORY))
    assert result.stat(HISTORY).generation == original.stat(HISTORY).generation + 1
    assert result.used_bytes == original.used_bytes


def test_fragmented_flash_retains_all_installed_files_and_repeated_state_log_saves(history_runner, installed_image, tmp_path):
    fs = ShamaFS.deserialize(installed_image.image)
    for name, size in (("zz-a", 6144), ("zz-b", 6144), ("zz-c", 6144), ("zz-d", 13312)):
        fs.create_file(name, bytes([size & 255]) * size, FileType.DATA)
    fs.delete("zz-a")
    fs.delete("zz-c")
    # Explicitly keep a fragmented layout with no32KiB replacement extent.
    with pytest.raises(OSError, match="contiguous"):
        fs._find_extent(128)
    before = {entry.name: entry for entry in fs.list_files()}
    result = ShamaFS.deserialize(run_history(history_runner, fs.serialize(), tmp_path, "+mode=interleave"))
    expected = bytes(32768)
    for sequence in range(12):
        expected = expected_history(expected, (sequence * 63) & 255, 128, sequence)
    assert result.read_file(HISTORY) == expected
    assert result.used_bytes == fs.used_bytes
    for name, entry in before.items():
        if name not in (HISTORY, "miner-state.cfg"):
            assert result.stat(name) == entry


def test_history_model_uses_no_new_extent_and_retains_guard_and_full_capacity(installed_image):
    fs = ShamaFS.deserialize(installed_image.image)
    original = fs.stat(HISTORY)
    for slot in (0, 63, 64, 127, 128, 191, 192, 255, 0):
        used = fs.used_bytes
        fs.write_history_record(HISTORY, slot, record(slot))
        assert fs.used_bytes == used
        assert fs.stat(HISTORY).start_block == original.start_block
        assert fs.stat(HISTORY).block_count == 128
    fs.create_file("zz-reserve.bin", bytes(fs.write_limit_bytes - fs.used_bytes), FileType.BIN)
    snapshot = fs.serialize()
    with pytest.raises(FlashWriteProtectedError):
        fs.write_history_record(HISTORY, 0, b"changed")
    assert fs.serialize() == snapshot


@pytest.mark.parametrize("corruption", ["target-name", "target-type", "overlapping-owner", "invalid-owner", "invalid-used-flag"])
def test_recovery_validates_canonical_identity_and_read_only_extent_ownership(history_runner, installed_image, tmp_path, corruption):
    image = committed_record_image(installed_image.image)
    fs = ShamaFS.deserialize(installed_image.image)
    index = installed_image.file_entry_indices[HISTORY]
    target = fs.table_start * fs.block_size + index * ENTRY_SIZE
    if corruption == "target-name":
        image[target + 21] = ord("x")
    elif corruption == "target-type":
        image[target + 1] = FileType.DATA
    else:
        other = fs.table_start * fs.block_size + installed_image.file_entry_indices["welcome.txt"] * ENTRY_SIZE
        if corruption == "overlapping-owner":
            struct.pack_into("<I", image, other + 8, fs.stat(HISTORY).start_block)
        elif corruption == "invalid-owner":
            struct.pack_into("<I", image, other + 8, fs.block_count)
        else:
            image[other] = 2
    with pytest.raises(ValueError, match="journal"):
        ShamaFS.deserialize(image)
    # The controller must reject BEFORE replaying into a potentially shared
    # extent, not overwrite data and discover the overlap during bitmap rebuild.
    assert run_history(history_runner, image, tmp_path, "+mode=bad-mount") == bytes(image)


@pytest.mark.parametrize("which,corruption", [
    ("history", "name"), ("history", "type"), ("history", "reused-slot"),
    ("state", "name"), ("state", "type"), ("state", "reused-slot"),
])
def test_miner_cannot_read_or_overwrite_an_unrelated_file_in_its_old_table_slot(history_runner, installed_image, tmp_path, which, corruption):
    name = HISTORY if which == "history" else "miner-state.cfg"
    size = 32768 if which == "history" else 256
    fs = ShamaFS.deserialize(installed_image.image)
    index = installed_image.file_entry_indices[name]
    if corruption == "reused-slot":
        replacement = "miner-history.bin" if which == "history" else "miner-state.bin"
        fs.delete(name)
        fs.create_file(replacement, b"\x5a" * size, FileType.BIN)
        assert fs.list_files()[index].name == replacement
        image = bytearray(fs.serialize())
    else:
        image = bytearray(installed_image.image)
        target = fs.table_start * fs.block_size + index * ENTRY_SIZE
        if corruption == "name":
            image[target + 21] = ord("x")
        else:
            image[target + 1] = FileType.DATA
    before = ShamaFS.deserialize(image)
    result = ShamaFS.deserialize(run_history(history_runner, image, tmp_path, f"+mode=bad-request-{which}"))
    assert result.list_files() == before.list_files()


@pytest.mark.parametrize("mode", ["native", "software"])
def test_actual_miner_firmware_saves_complete_history_and_state_in_the_full_image(installed_image, mode):
    import hashlib
    from test_miner_firmware import BASE, TARGET, MinerMachine, configure

    initial = ShamaFS.deserialize(installed_image.image)
    initial_used_bytes = initial.used_bytes
    machine = MinerMachine(initial)
    machine.settle()
    configure(machine, "T", b"\xff" * 32)
    if mode == "software":
        machine.type("M")
    machine.event(0x14)
    expected = hashlib.sha256(hashlib.sha256(bytes(80)).digest()).digest()
    assert machine.word(BASE + 24) == 2
    state = machine.fs.read_file("miner-state.cfg")
    history = machine.fs.read_file(HISTORY)
    assert state[:128] == history[:128]
    assert state[48:80] == expected
    assert state[80:112] == bytes(machine.memory[TARGET:TARGET + 32])
    assert len(history) == 32768
    assert machine.fs.used_bytes == initial_used_bytes
    assert ShamaFS.deserialize(machine.fs.serialize()).read_file(HISTORY) == history
