"""Check the shipped renderer against the visible Miner record contract.

The expected text is built from record values, independently of the firmware's
row loops, register allocation and hexadecimal helpers.
"""
from pathlib import Path

import pytest

from shamaos.assembler import assemble
from shamaos.isa import SYSCALLS
from shamaos.memory_map import MINER_BASE, MINER_RECORD, MINER_WORKSPACE_BYTES
from test_miner_firmware import MinerMachine


ROOT = Path(__file__).resolve().parents[1]
BASE = MINER_RECORD
HISTORY = BASE + 256
CONFIG = BASE + 384


def record(seed, valid):
    """Distinct high and low words expose truncation and wrong-base reads."""
    data = bytearray((i * 157 + seed) & 255 for i in range(128))
    for offset in (8, 12, 16, 20, 24, 28, 32, 36, 40, 112, 116, 120, 124):
        value = (0xF1234567 ^ (seed << 19) ^ (offset * 0x1020301)) & 0xFFFFFFFF
        data[offset:offset + 4] = value.to_bytes(4, "little")
    data[48:80] = bytes(range(32))
    data[80:112] = bytes(range(32)) if valid else bytes(32)
    return bytes(data)


def expected_record_rows(selected, live):
    word = lambda data, offset: int.from_bytes(data[offset:offset + 4], "little")
    hexword = lambda offset: f"{word(selected, offset):08X}"
    liveword = lambda offset: f"{word(live, offset):08X}"
    elapsed = word(live, 232)
    rate = 0 if word(live, 248) else (1000000 // elapsed if elapsed else 0xFFFFFFFF)
    valid = int(selected[48:80] <= selected[80:112])
    return [
        (48, f"G{hexword(8)} N{hexword(12)} A{hexword(20)}{hexword(16)}"),
        (64, "H " + selected[48:64].hex().upper()),
        (80, "H " + selected[64:80].hex().upper()),
        (96, "T " + selected[80:96].hex().upper()),
        (112, "T " + selected[96:112].hex().upper()),
        (128, f"S{hexword(24)} M{hexword(28)} D{hexword(40)} V{valid:08X}"),
        (144, f"N{hexword(112)} S{hexword(116)} R{rate:08X} C{liveword(236)}"),
        (160, f"E{liveword(240)} X{liveword(244)} T{hexword(124)}{hexword(120)}"),
    ]


@pytest.mark.parametrize("view", ["live", "history", "empty", "target", "header"])
@pytest.mark.parametrize("elapsed,high", [(0, 0), (113, 0), (0xFFFFFFFF, 0x80000000)])
@pytest.mark.parametrize("valid", [False, True])
def test_miner_preserves_every_displayed_digit_and_row(view, elapsed, high, valid):
    source = (ROOT / "src/shamaos/firmware/miner.asm").read_text()
    result = assemble(source)
    machine = MinerMachine()
    machine.load_program([*result.words, *assemble("HLT").words])
    machine.pc = result.labels[".DRAW"]
    machine.call_stack.append(len(result.words))
    machine.regs[15] = BASE
    machine.memory[BASE:BASE + 128] = record(7, valid)
    machine.memory[HISTORY:HISTORY + 128] = record(41, not valid)
    staged = bytes((i * 41 + 29) & 255 for i in range(80))
    machine.memory[CONFIG:CONFIG + 80] = staged
    values = {
        208: {"target": 1, "header": 2}.get(view, 0),
        212: 0xA1234567,
        216: 0xFF,
        220: {"history": 1, "empty": 2}.get(view, 0),
        232: elapsed,
        236: 0x12345678,
        240: 0xFFFF0102,
        244: 0xF2345678,
        248: high,
    }
    for offset, value in values.items():
        machine.memory[BASE + offset:BASE + offset + 4] = value.to_bytes(4, "little")
    before = bytes(machine.memory[MINER_BASE:MINER_BASE + MINER_WORKSPACE_BYTES])
    machine.gpu[0x435:0x500] = b"\xA5" * 203

    if view == "empty":
        expected = [(64, "EMPTY 000000FF")]
    elif view in ("target", "header"):
        expected = [(48, f"E{values[208]:08X} A1234567")]
        expected += [(64 + 16 * row, staged[row * 20:row * 20 + 20].hex().upper()) for row in range(4)]
    else:
        ptr = HISTORY if view == "history" else BASE
        expected = expected_record_rows(bytes(machine.memory[ptr:ptr + 128]), bytes(machine.memory[BASE:BASE + 256]))

    for _ in range(20000):
        if machine.halted:
            break
        machine.step()
    assert machine.halted
    actual = [(args[3], text.decode("ascii")) for op, args, text in machine.draws if op == 12]
    assert actual == expected
    assert all(args[:3] == [0, len(text), 4] for op, args, text in machine.draws if op == 12)
    assert [op for op, _, _ in machine.draws if op != 22] == [19] + [12] * len(expected) + [20]
    assert machine.gpu[0x435:0x500] == b"\xA5" * 203
    assert bytes(machine.memory[MINER_BASE:MINER_BASE + MINER_WORKSPACE_BYTES]) == before
    assert machine.regs[15] == BASE


def test_failed_save_keeps_complete_result_and_successful_retry_clears_error():
    class FailedSaveMachine(MinerMachine):
        fail_saves = False

        def syscall(self, sid):
            if sid == SYSCALLS["SYS_MINER_SAVE_STATE"] and self.fail_saves:
                self.regs[1] = self.regs[2] = 0xFFFFFFFF
                return
            super().syscall(sid)

    machine = FailedSaveMachine()
    machine.settle()
    machine.type("T" + "ff" * 32 + "\n")
    saved_before = machine.fs.read_file("miner-state.cfg")
    machine.fail_saves = True
    machine.event(0x14)
    assert machine.word(BASE + 240) == 0xFFFFFFFF
    assert machine.word(BASE + 16) == 1
    assert machine.word(BASE + 24) == 2
    assert machine.fs.read_file("miner-state.cfg") == saved_before
    complete_result = bytes(machine.memory[BASE:BASE + 128])
    assert machine.fs.read_file("miner-history.log")[:128] == complete_result
    assert any(text.startswith(b"EFFFFFFFF") for op, _, text in machine.draws if op == 12)
    machine.fail_saves = False
    machine.event(0x15)
    assert machine.word(BASE + 240) == 0
    assert machine.word(BASE + 16) == 1
    assert machine.fs.read_file("miner-state.cfg")[48:112] == complete_result[48:112]
