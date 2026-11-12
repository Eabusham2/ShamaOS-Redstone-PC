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
        if op in (G.SWAP_BUFFER, G.PUSH_DIRTY, G.NOTICE):
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

    def seed_back(self, seed):
        """Seed dense packed pixels so scroll checks cover every bit position."""
        rng = random.Random(seed)
        width, height = self.gpu.back.width, self.gpu.back.height
        self.gpu.back._rows[:] = [rng.getrandbits(width) for _ in range(height)]
        bits = sum(row << (width * y) for y, row in enumerate(self.gpu.back._rows))
        for word in range((width * height + 31) // 32):
            self.records.append([7, word, (bits >> (word * 32)) & 0xffffffff, *([0] * 7)])
        return self

    def capacity_status(self, expected, acknowledge=False):
        self.records.append([4, int(expected), int(acknowledge), *([0] * 7)])
        return self

    def mark_traffic(self):
        self.records.append([5] + [0] * 9)
        return self

    def traffic_at_most(self, transactions):
        self.records.append([6, transactions] + [0] * 8)
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
    return result.stdout


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


@pytest.mark.parametrize('offset', [
    (0, 0), (1, 0), (-1, 0), (31, 0), (-31, 0), (32, 0), (-32, 0),
    (33, -3), (-33, 3), (0, 1), (0, -1), (1, -1), (-1, 1),
    (319, 179), (-319, -179), (0, 179), (320, 0),
    (0, 0x80000000),
])
def test_native_scroll_moves_in_place_with_bounded_word_traffic(gpu_binary, tmp_path, record_property, offset):
    s = Scenario(320, 180).seed_back(0x5100)
    s.resource(0, b'TEXT SURVIVES').resource(0, bytes(range(256)) * 4, sprite=True)
    s.check().mark_traffic()
    s.command(G.SET_CLIP, 10, 10, 10, 10).command(G.SCROLL, *offset).check()
    # At most two source reads and one destination write per 32 pixels;
    # no snapshot pass, front-buffer writes or third framebuffer is allowed.
    s.traffic_at_most(0 if offset == (0, 0) else 3 * 1800)
    s.read(0x400, int.from_bytes(b'TEXT', 'little'))
    s.read(0x800, 0x03020100).read(0xbfc, 0xfffefdfc)
    s.command(G.SWAP_BUFFER).check()
    output = run(gpu_binary, tmp_path, s)
    assert 'GPU VRAM TRANSACTIONS' in output
    actual = int(output.split('GPU VRAM TRANSACTIONS actual=')[1].split()[0])
    record_property('scroll_vram_transactions', actual)
    record_property('scroll_offset', str(offset))


def test_resource_fault_discards_queued_app_work_and_allows_recovery(gpu_binary, tmp_path):
    s = Scenario().seed_back(120)
    s.command(G.SWAP_BUFFER).check()
    s.command(G.FILL_RECT, 0, 0, 37, 23)
    s.command(G.DRAW_TEXT, 255, 2, 0, 0)
    # These enter the queue behind the resource violation while the rectangle
    # is still drawing. Recovery must discard both before either can execute.
    s.records.append([0, int(G.CLEAR), 0] + [0] * 7)
    s.records.append([0, int(G.FENCE)] + [0] * 8)
    s.check().capacity_status(True, acknowledge=True)
    s.command(G.CLEAR_PIXEL, 4, 5).command(G.SWAP_BUFFER).command(G.FENCE).check()
    s.capacity_status(False)
    run(gpu_binary, tmp_path, s)


@pytest.mark.parametrize('code', [1, 2, 3, 4, 5, 6, 7, 0x82, 0x84])
def test_notice_overlay_preserves_both_frames_resources_and_drawing_state(gpu_binary, tmp_path, code):
    s = Scenario(320, 180).seed_back(611)
    s.command(G.SWAP_BUFFER).check().seed_back(622)
    s.resource(0, bytes(range(256))).resource(0, bytes(range(256)) * 4, sprite=True)
    s.command(G.SET_CLIP, 1, 1, 2, 2).command(G.SET_COLOR, 0)
    s.command(G.SET_CURSOR, 42, 61, 1).command(G.PUSH_DIRTY)
    original_front = tuple(s.gpu.front._rows)
    original_back = tuple(s.gpu.back._rows)
    original_display = tuple(s.gpu.display._rows)
    s.mark_traffic().command(G.NOTICE, code).command(G.FENCE).check()
    s.traffic_at_most(720).read(0x3c, 72)
    assert tuple(s.gpu.display._rows) != original_display
    assert tuple(s.gpu.front._rows) == original_front
    assert tuple(s.gpu.back._rows) == original_back
    s.read(0x44, 1).read(0x48, 1).read(0x4c, 2).read(0x50, 2)
    s.read(0x54, 0).read(0x58, 0).read(0x5c, code)
    s.read(0x400, 0x03020100).read(0x4fc, 0xfffefdfc)
    s.read(0x800, 0x03020100).read(0xbfc, 0xfffefdfc)
    s.mark_traffic().command(G.NOTICE, 0).command(G.FENCE).check().read(0x5c, 0)
    s.traffic_at_most(720).read(0x3c, 72)
    assert tuple(s.gpu.display._rows) == original_display
    assert tuple(s.gpu.front._rows) == original_front
    assert tuple(s.gpu.back._rows) == original_back
    run(gpu_binary, tmp_path, s)


def test_notice_presentation_retains_pending_cursor_rows_outside_warning(gpu_binary, tmp_path):
    s = Scenario(320, 180).seed_back(811)
    s.command(G.SWAP_BUFFER).command(G.SET_CURSOR, 21, 12, 3).command(G.PUSH_DIRTY).check()
    s.command(G.SET_CURSOR, 26, 160, 3)
    # The old and new cursor each mark five rows. Both lie outside the 72-row
    # warning, and neither cursor change has reached the display yet.
    s.mark_traffic().command(G.NOTICE, 1).command(G.FENCE).check()
    s.traffic_at_most(820).read(0x3c, 82)
    assert s.gpu.display.get(21, 12) == s.gpu.front.get(21, 12)
    assert s.gpu.display.get(26, 160) != s.gpu.front.get(26, 160)
    s.command(G.NOTICE, 0).command(G.FENCE).check().read(0x3c, 72)
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
    resource_fault = op in (G.BLIT, G.SPRITE, G.DRAW_TEXT)
    s.capacity_status(resource_fault, acknowledge=resource_fault)
    s.command(G.SET_PIXEL, 3, 3).command(G.SWAP_BUFFER).command(G.FENCE).check()
    run(gpu_binary, tmp_path, s)


def test_native_resolution_presentation_includes_last_pixel(gpu_binary, tmp_path):
    s = Scenario(320, 180)
    s.command(G.CLEAR, 0).command(G.SET_PIXEL, 319, 179).command(G.SWAP_BUFFER).check()
    s.command(G.COPY_BUFFER).command(G.SET_CURSOR, 319, 179, 3).command(G.PUSH_DIRTY).check()
    assert s.gpu.front.get(319, 179) == 1 and s.gpu.display.get(319, 179) == 0
    run(gpu_binary, tmp_path, s)
