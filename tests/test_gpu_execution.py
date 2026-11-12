"""Pixel/state differential tests of the real GPU with stalled RAM/display buses."""
from pathlib import Path
import random
import shutil
import subprocess

import pytest

from shamaos.font5x7 import FONT5X7, verilog_source
from shamaos.gpu import GPUCommand, GPUOp, GPUReference

ROOT = Path(__file__).resolve().parents[1]
G = GPUOp


class Scenario:
    def __init__(self, width=37, height=23):
        self.gpu = GPUReference(width, height)
        self.records = []
        self.expected = []
        self.front_sel = 0
        self.updates = 0
        self.checks = 0

    def command(self, op, *args):
        self.records.append([0, int(op), *args, *([0] * (8 - len(args)))])
        self.gpu.execute(GPUCommand(op, args))
        if op == G.SWAP_BUFFER:
            self.front_sel ^= 1
        if op in (G.SWAP_BUFFER, G.PUSH_DIRTY):
            self.updates += len(self.gpu.last_updates)
        return self

    def resource(self, offset, data, sprite=False):
        target = self.gpu.sprites if sprite else self.gpu.text
        assert 0 <= offset <= len(target) - len(data)
        target[offset:offset + len(data)] = data
        base = 0x800 if sprite else 0x400
        # Exercise CPU-style byte strobes, including unaligned first/last bytes.
        for start in range(offset & ~3, (offset + len(data) + 3) & ~3, 4):
            word = strobes = 0
            for lane in range(4):
                index = start + lane - offset
                if 0 <= index < len(data):
                    word |= data[index] << (lane * 8)
                    strobes |= 1 << lane
            self.records.append([1, base + start, word, strobes, *([0] * 6)])
        return self

    def read(self, address, value):
        self.records.append([3, address, value, *([0] * 7)])
        return self

    def check(self):
        gpu = self.gpu
        self.records.append([2] + [0] * 9)
        self.expected.extend([self.front_sel, gpu.readback, gpu.fence_count,
                              int(gpu.error), gpu.presented_rows, self.updates])
        self.expected.extend(
            gpu.front.get(x, y) | gpu.back.get(x, y) << 1 | gpu.display.get(x, y) << 2
            for y in range(gpu.front.height) for x in range(gpu.front.width)
        )
        self.checks += 1
        return self


@pytest.fixture(scope='module')
def gpu_binary(tmp_path_factory):
    if not shutil.which('iverilog') or not shutil.which('vvp'):
        pytest.skip('Icarus Verilog required for GPU differential execution')
    directory = tmp_path_factory.mktemp('gpu_rtl')
    binaries = {}

    def build(width, height, depth):
        key = width, height, depth
        if key not in binaries:
            path = directory / f'gpu_{width}_{height}_{depth}'
            result = subprocess.run([
                'iverilog', '-g2012', '-s', 'tb_gpu_cases',
                f'-Ptb_gpu_cases.WIDTH={width}', f'-Ptb_gpu_cases.HEIGHT={height}',
                f'-Ptb_gpu_cases.QUEUE_DEPTH={depth}', '-o', str(path),
                str(ROOT / 'rtl/shama_font5x7.sv'), str(ROOT / 'rtl/shama_gpu.sv'),
                str(ROOT / 'rtl/tb_gpu_cases.sv'),
            ], capture_output=True, text=True, timeout=60)
            assert result.returncode == 0, result.stdout + result.stderr
            binaries[key] = path
        return binaries[key]

    return build


def run(gpu_binary, tmp_path, scenario, depth=4, queue_stall=False, direct_stall=False):
    binary = gpu_binary(scenario.gpu.front.width, scenario.gpu.front.height, depth)
    command_file = tmp_path / 'commands.hex'
    expected_file = tmp_path / 'expected.hex'
    command_file.write_text(''.join(f'{value & 0xffffffff:08x}\n' for row in scenario.records for value in row))
    expected_file.write_text(''.join(f'{value & 0xffffffff:08x}\n' for value in scenario.expected))
    result = subprocess.run([
        'vvp', str(binary), f'+COMMANDS={command_file}', f'+EXPECTED={expected_file}',
        f'+COUNT={len(scenario.records)}', f'+EXPECT_COUNT={scenario.checks}',
        f'+QUEUE_STALL={int(queue_stall)}', f'+DIRECT_STALL={int(direct_stall)}',
    ], capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'GPU DIFFERENTIAL PASS' in result.stdout


def test_pixels_lines_rectangles_circles_and_clip_match_reference(gpu_binary, tmp_path):
    s = Scenario()
    s.command(G.NOP).command(G.CLEAR, 1).command(G.CLEAR_PIXEL, 3, 4)
    s.command(G.INVERT_PIXEL, 4, 4).command(G.SET_PIXEL, 3, 4).command(G.READ_PIXEL, 4, 4).check()
    s.command(G.SET_CLIP, 2, 3, 20, 16).command(G.SET_COLOR, 0)
    for endpoint in [(32, 11), (20, 22), (0, 22), (-9, 11), (0, -8), (20, -8), (14, 11), (14, 20)]:
        s.command(G.LINE, 14, 11, *endpoint).check()
    s.command(G.RECT, -3, 2, 25, 20).check()
    s.command(G.FILL_RECT, 16, -3, 30, 11).check()
    s.command(G.RESET_CLIP).command(G.SET_COLOR, 1)
    s.command(G.CIRCLE, 18, 11, 9).check()
    s.command(G.CIRCLE, -2, 2, 7).command(G.CIRCLE, 35, 21, 0).check()
    s.command(G.CLEAR, 0).command(G.FILL_RECT, -2000, -2000, 4096, 4096).check()
    s.command(G.SET_COLOR, 0).command(G.RECT, -2000, -2000, 4096, 4096).check()
    s.command(G.RECT, 4, 4, 0, 12).command(G.CIRCLE, 2000, 2000, 512).check()
    s.command(G.SWAP_BUFFER).command(G.FENCE).check().read(0x30, 1)
    run(gpu_binary, tmp_path, s)


def test_seeded_line_rasterization_all_octants_and_signed_coordinates(gpu_binary, tmp_path):
    random_source = random.Random(818)
    s = Scenario()
    for index in range(90):
        s.command(G.SET_COLOR, index & 1)
        s.command(G.LINE, *(random_source.randrange(-20, 55) for _ in range(4)))
        if index % 5 == 4:
            s.check()
    s.command(G.SWAP_BUFFER).check()
    run(gpu_binary, tmp_path, s)


def test_entire_resident_ascii_font_and_text_ram_lanes(gpu_binary, tmp_path):
    assert (ROOT / 'rtl/shama_font5x7.sv').read_text() == verilog_source()
    assert FONT5X7[ord('a')] != FONT5X7[ord('A')]
    s = Scenario()
    s.command(G.SET_FONT, 0)
    for character in range(32, 127):
        s.command(G.CLEAR, 0).command(G.DRAW_CHAR, character, 3, 5).check()
    s.resource(3, b'a;"\\,{}Z09+/-')
    s.command(G.CLEAR, 0).command(G.DRAW_TEXT, 3, 12, -4, 2).check()
    s.resource(241, b'LAST TEXT BYTES')
    s.command(G.SET_COLOR, 0).command(G.DRAW_TEXT, 241, 15, 0, 2).check()
    s.command(G.DRAW_TEXT, 256, 0, 0, 0).command(G.SWAP_BUFFER).check()
    run(gpu_binary, tmp_path, s)


def test_blit_and_transparent_sprites_use_exact_bounded_resources(gpu_binary, tmp_path):
    s = Scenario()
    s.resource(7, bytes((index * 73) & 255 for index in range(64)), sprite=True)
    s.command(G.CLEAR, 1).command(G.BLIT, 7, 4, 4, 8, 8).check()
    s.command(G.SET_COLOR, 0).command(G.SPRITE, 7, 8, 2, 8, 8).check()
    s.command(G.SET_CLIP, 2, 3, 18, 20).command(G.SET_COLOR, 1)
    s.command(G.BLIT, 15, -4, 18, 8, 6).check()
    s.command(G.SPRITE, 7, 16, 4, 8, 8).check()
    s.command(G.BLIT, 1024, 0, 0, 0, 5).check()
    s.command(G.SWAP_BUFFER).check()
    run(gpu_binary, tmp_path, s)


@pytest.mark.parametrize('offset', [(1, 1), (-2, -3), (3, -2), (-3, 2), (37, 0), (0, -23), (0x80000000, 0)])
def test_scroll_preserves_bits_and_clears_exposed_area(gpu_binary, tmp_path, offset):
    s = Scenario()
    s.command(G.LINE, 0, 0, 36, 22).command(G.RECT, 4, 3, 21, 15)
    s.command(G.SET_CLIP, 10, 10, 10, 10).command(G.SCROLL, *offset).check()
    s.command(G.SWAP_BUFFER).check()
    run(gpu_binary, tmp_path, s)


def test_dirty_rows_swaps_and_cursor_are_visible_without_changing_vram(gpu_binary, tmp_path):
    s = Scenario()
    s.command(G.CLEAR, 0).command(G.SWAP_BUFFER).command(G.COPY_BUFFER).check()
    s.command(G.SET_PIXEL, 30, 21).command(G.PUSH_DIRTY).check()
    assert not s.gpu.display.get(30, 21) and s.gpu.presented_rows == 0
    s.command(G.SWAP_BUFFER).check()
    assert s.gpu.display.get(30, 21) and s.gpu.presented_rows == 1
    s.command(G.SWAP_BUFFER).check()
    assert not s.gpu.display.get(30, 21)
    s.command(G.COPY_BUFFER).command(G.SET_CURSOR, 17, 10, 1).command(G.PUSH_DIRTY).check()
    assert sum(row.bit_count() for row in s.gpu.display._rows) == 9
    assert not any(s.gpu.front._rows) and not any(s.gpu.back._rows)
    s.command(G.SET_CURSOR, 18, 11, 3).command(G.PUSH_DIRTY).check()
    assert sum(row.bit_count() for row in s.gpu.display._rows) == 1
    s.command(G.SET_CURSOR, 0, 0, 1).command(G.PUSH_DIRTY).check()
    s.command(G.SET_CURSOR, 0, 0, 0).command(G.PUSH_DIRTY).check()
    assert not any(s.gpu.display._rows)
    run(gpu_binary, tmp_path, s)


@pytest.mark.parametrize('depth', [1, 4])
def test_queue_backpressure_and_direct_ram_wait_never_drop_or_deadlock(gpu_binary, tmp_path, depth):
    s = Scenario()
    s.command(G.FILL_RECT, 0, 0, 37, 23)
    for index in range(12):
        s.command(G.CLEAR_PIXEL, index + 2, index + 2)
    s.command(G.FILL_RECT, 0, 0, 37, 23).command(G.DRAW_CHAR, ord('A'), 3, 2)
    # This access arrives while drawing is queued: it must wait and let it drain.
    s.resource(0, b'BATCHED TEXT')
    s.command(G.SET_COLOR, 0).command(G.DRAW_TEXT, 0, 12, 1, 8)
    s.resource(1, b'after').command(G.FENCE).command(G.SWAP_BUFFER).check()
    s.read(0x400, int.from_bytes(b'Baft', 'little')).read(0x600, 0)
    run(gpu_binary, tmp_path, s, depth=depth, queue_stall=True, direct_stall=True)


def test_full_256_entry_queue_reports_count_without_overlapping_status_flags(gpu_binary, tmp_path):
    s = Scenario()
    s.command(G.LINE, -4096, 0, 4096, 0)
    for _ in range(256):
        s.command(G.NOP)
    s.read(0, (256 << 4) | 1)
    s.command(G.NOP).command(G.SWAP_BUFFER).command(G.FENCE).check()
    s.read(0, 1 << 16)
    run(gpu_binary, tmp_path, s, depth=256, queue_stall=True)


@pytest.mark.parametrize('op,args', [
    (G.LINE, (0x80000000, 0, 2, 2)), (G.LINE, (0, 0, 0x7fffffff, 1)),
    (G.RECT, (0, 0, 0xffffffff, 4)), (G.FILL_RECT, (0, 0, 4, 0x7fffffff)),
    (G.CIRCLE, (2, 2, 0xffffffff)), (G.CIRCLE, (2, 2, 513)),
    (G.BLIT, (1023, 0, 0, 2, 1)), (G.SPRITE, (0, 0, 0, 0x10000, 0x10000)),
    (G.DRAW_TEXT, (255, 2, 0, 0)), (G.DRAW_TEXT, (0xffffffff, 2, 0, 0)),
    (G.DRAW_CHAR, (65, 0x7fffffff, 0)), (G.SET_FONT, (1,)), (0xff, ()),
])
def test_malformed_commands_fail_atomically_and_leave_queue_live(gpu_binary, tmp_path, op, args):
    s = Scenario()
    s.command(G.SET_PIXEL, 2, 2).check()
    s.command(op, *args).check()
    assert s.gpu.error
    s.command(G.SET_PIXEL, 3, 3).command(G.SWAP_BUFFER).command(G.FENCE).check()
    run(gpu_binary, tmp_path, s)


def test_native_resolution_presentation_includes_last_pixel(gpu_binary, tmp_path):
    s = Scenario(320, 180)
    s.command(G.CLEAR, 0).command(G.SET_PIXEL, 319, 179).command(G.SWAP_BUFFER).check()
    s.command(G.COPY_BUFFER).command(G.SET_CURSOR, 319, 179, 3).command(G.PUSH_DIRTY).check()
    assert s.gpu.front.get(319, 179) == 1 and s.gpu.display.get(319, 179) == 0
    run(gpu_binary, tmp_path, s)
