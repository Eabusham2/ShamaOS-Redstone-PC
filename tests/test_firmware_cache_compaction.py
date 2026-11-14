"""Behavioral boundaries for the smaller Editor and Files executables."""
import pytest

from firmware_machine import FirmwareMachine, GPU
from shamaos.flashfs import FileType, ShamaFS
from shamaos.isa import SYSCALLS
from shamaos.memory_map import EDIT_BUF, EDIT_CTX


A, B, UP, DOWN, LEFT, RIGHT = 0x14, 0x15, 0x10, 0x11, 0x12, 0x13
NAME_EDIT = 0x2480


class DeferredTextMachine(FirmwareMachine):
    """Keep submitted text busy until a fence, as an asynchronous GPU can."""

    def __init__(self, app, fs=None):
        self.text_pending = False
        self.frame_depth = 0
        self.frames = 0
        super().__init__(app, fs)

    def write_memory(self, addr, size, value):
        if addr < GPU + 0x500 and addr + size > GPU + 0x4a0:
            assert not self.text_pending, "text scratch changed before its fence"
        super().write_memory(addr, size, value)
        if addr == GPU + 40 and value:
            op = self.read_memory(GPU + 4, 4)
            if op == 0x13:
                assert self.frame_depth == 0, "nested text frame"
                self.frame_depth = 1
                self.frames += 1
            elif op == 0x14:
                assert self.frame_depth == 1, "end without a text frame"
                self.frame_depth = 0
            elif op == 0x0c:
                assert self.frame_depth == 1, "text submitted outside its frame"
                self.text_pending = True

    def syscall(self, sid):
        if sid == SYSCALLS["SYS_GPU_FENCE"]:
            self.text_pending = False
        super().syscall(sid)

    def settle(self, limit=400000):
        super().settle(limit)
        assert not self.text_pending
        assert self.frame_depth == 0
        assert not self.call_stack and not self.data_stack


def _action(machine, index):
    machine.event(B)
    for _ in range(index):
        machine.event(DOWN)
    machine.event(A)


@pytest.mark.parametrize("length", [1, 2, 3, 4, 39])
@pytest.mark.parametrize("program", [False, True])
def test_editor_extension_keeps_unaligned_names_and_full_name_capacity(length, program):
    machine = FirmwareMachine("editor")
    machine.settle()
    if program:
        machine.event(DOWN)
    machine.event(A)
    machine.memory[NAME_EDIT + 44] = 0xa5
    name = "n" * length
    machine.type(name)
    machine.event(1, 13)
    expected = name + (".ASM" if program else ".TXT")
    assert machine.regs[8] == 3
    assert machine.fs.stat(expected).file_type == (FileType.ASM if program else FileType.TXT)
    assert machine.cstring(NAME_EDIT) == expected
    assert machine.regs[6] == len(expected)
    assert machine.memory[NAME_EDIT + 44] == 0xa5


@pytest.mark.parametrize("new_action", [6, 7])
def test_editor_failed_save_before_new_document_retains_old_type_and_mode(monkeypatch, new_action):
    machine = FirmwareMachine("editor")
    machine.settle()
    machine.event(A)
    machine.type("original")
    machine.event(1, 13)
    machine.type("saved")
    machine.event(A)
    machine.type(" dirty")

    def reject_write(*args, **kwargs):
        raise OSError("injected full filesystem")

    monkeypatch.setattr(machine.fs, "write_handle", reject_write)
    _action(machine, new_action)
    assert machine.regs[8] == 8
    assert machine.regs[11] == int(FileType.TXT)
    assert machine.word(EDIT_CTX + 40) == 4
    assert machine.word(EDIT_CTX + 20) == 1
    assert machine.fs.read_file("original.TXT") == b"saved"
    assert bytes(machine.memory[EDIT_BUF:EDIT_BUF + machine.regs[7]]) == b"saved dirty"
    machine.event(B)
    assert machine.regs[8] == 4 and machine.regs[9] == new_action


def test_editor_shared_rendering_preserves_fences_rows_and_caller_state():
    machine = DeferredTextMachine("editor")
    machine.settle()
    machine.event(A)
    machine.type("rows")
    machine.event(1, 13)
    machine.type("first\n\tsecond\n" + "long" * 14 + "\nlast")
    for event in (UP, UP, LEFT, RIGHT, DOWN):
        machine.event(event)
    machine.event(A)
    _action(machine, 2)
    machine.type("renamed")
    machine.event(1, 13)
    assert machine.fs.read_file("renamed.TXT") == b"first\n\tsecond\n" + b"long" * 14 + b"\nlast"
    assert machine.frames > 30
    assert any(op == 0x0c and b" second" in data for op, args, data in machine.draws)


def test_files_shared_rendering_preserves_fences_in_every_display_mode():
    fs = ShamaFS()
    fs.create_file("docs", file_type=FileType.DIR)
    fs.create_file("docs/readme.txt", b"one\n\ttwo\r\n\n" + b"x" * 60 + b"\nfive\nsix\nseven\neight\nnine", FileType.TXT)
    machine = DeferredTextMachine("files", fs)
    machine.settle()
    machine.event(A)  # enter docs
    machine.event(A)  # open the text viewer
    machine.event(DOWN)
    machine.event(UP)
    machine.event(B)
    machine.event(1, ord("r"))
    machine.type("renamed.txt")
    machine.event(1, 13)
    machine.event(RIGHT)
    machine.event(A)  # Cancel remains the initial deletion choice.
    machine.event(1, ord("n"))
    machine.type("empty")
    machine.event(1, 13)
    assert fs.exists("docs/renamed.txt")
    assert fs.stat("docs/empty").file_type == FileType.DIR
    machine.event(1, ord("e"))
    machine.event(A)  # enter an empty folder, including its empty-state text
    assert any(op == 0x0c and b"EMPTY FOLDER" in data for op, args, data in machine.draws)
    machine.event(B)
    machine.event(B)
    machine.event(RIGHT)
    machine.event(RIGHT)
    machine.event(A)  # A nonempty folder reports an error and keeps its contents.
    assert machine.regs[9] == 4 and fs.exists("docs/renamed.txt")
    machine.event(B)
    assert machine.frames > 30
