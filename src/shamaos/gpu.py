from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from .font5x7 import glyph_rows


class GPUOp(IntEnum):
    NOP = 0x00
    CLEAR = 0x01
    SET_PIXEL = 0x02
    CLEAR_PIXEL = 0x03
    INVERT_PIXEL = 0x04
    READ_PIXEL = 0x05
    LINE = 0x06
    RECT = 0x07
    FILL_RECT = 0x08
    BLIT = 0x09
    SPRITE = 0x0A
    DRAW_CHAR = 0x0B
    DRAW_TEXT = 0x0C
    SCROLL = 0x0D
    SET_CLIP = 0x0E
    RESET_CLIP = 0x0F
    SET_FONT = 0x10
    SET_COLOR = 0x11
    SET_CURSOR = 0x12
    COPY_BUFFER = 0x13
    SWAP_BUFFER = 0x14
    PUSH_DIRTY = 0x15
    FENCE = 0x16
    CIRCLE = 0x17


@dataclass(frozen=True)
class GPUCommand:
    op: GPUOp
    args: tuple[int, ...] = ()


class Framebuffer:
    def __init__(self, width: int = 320, height: int = 180) -> None:
        if width <= 0 or height <= 0:
            raise ValueError("invalid framebuffer size")
        self.width = width
        self.height = height
        self._rows = [0] * height

    def _inside(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height

    def get(self, x: int, y: int) -> int:
        if not self._inside(x, y):
            return 0
        return (self._rows[y] >> x) & 1

    def set(self, x: int, y: int, value: int = 1) -> None:
        if not self._inside(x, y):
            return
        mask = 1 << x
        if value:
            self._rows[y] |= mask
        else:
            self._rows[y] &= ~mask

    def invert(self, x: int, y: int) -> None:
        if self._inside(x, y):
            self._rows[y] ^= 1 << x

    def clear(self, value: int = 0) -> None:
        row = (1 << self.width) - 1 if value else 0
        self._rows[:] = [row] * self.height

    def line(self, x0: int, y0: int, x1: int, y1: int, value: int = 1) -> None:
        dx = abs(x1 - x0)
        sx = 1 if x0 < x1 else -1
        dy = -abs(y1 - y0)
        sy = 1 if y0 < y1 else -1
        err = dx + dy
        while True:
            self.set(x0, y0, value)
            if x0 == x1 and y0 == y1:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x0 += sx
            if e2 <= dx:
                err += dx
                y0 += sy

    def rect(self, x: int, y: int, w: int, h: int, value: int = 1, *, fill: bool = False) -> None:
        if w <= 0 or h <= 0:
            return
        if fill:
            for yy in range(y, y + h):
                for xx in range(x, x + w):
                    self.set(xx, yy, value)
            return
        self.line(x, y, x + w - 1, y, value)
        self.line(x, y + h - 1, x + w - 1, y + h - 1, value)
        self.line(x, y, x, y + h - 1, value)
        self.line(x + w - 1, y, x + w - 1, y + h - 1, value)

    def scroll(self, dx: int, dy: int) -> None:
        new_rows = [0] * self.height
        mask = (1 << self.width) - 1
        if abs(dx) >= self.width or abs(dy) >= self.height:
            self._rows = new_rows
            return
        for y in range(self.height):
            src_y = y - dy
            if not 0 <= src_y < self.height:
                continue
            row = self._rows[src_y]
            if dx > 0:
                row <<= dx
            elif dx < 0:
                row >>= -dx
            new_rows[y] = row & mask
        self._rows = new_rows

    def packed(self) -> bytes:
        row_bytes = (self.width + 7) // 8
        out = bytearray()
        for row in self._rows:
            out.extend(row.to_bytes(row_bytes, "little"))
        return bytes(out)


class GPUReference:
    """Executable model of the physical one-bit command processor.

    Drawing targets the back buffer. SWAP_BUFFER presents and swaps; PUSH_DIRTY
    presents the current front (including a non-destructive cursor). Text uses
    the resident font and 256-byte text RAM; sprite RAM stores 1024 one-byte
    pixels whose low bit is used. Invalid resource spans fail before drawing.

    Coordinates for raster shapes are signed 32-bit integers limited to
    [-4096,4096], dimensions to [0,4096], and circle radii to [0,512]. These are
    explicit hardware limits, not Python-only clipping shortcuts.
    """

    COORD_LIMIT = 4096
    MAX_RADIUS = 512

    def __init__(self, width: int = 320, height: int = 180) -> None:
        self.front = Framebuffer(width, height)
        self.back = Framebuffer(width, height)
        self.display = Framebuffer(width, height)
        self.color = 1
        self.font = 0
        self.clip = (0, 0, width - 1, height - 1)
        self.cursor = (0, 0, 0)
        self.text = bytearray(256)
        self.sprites = bytearray(1024)
        self.buffer_diff_rows = set(range(height))
        self.present_rows = set(range(height))
        self.last_updates: list[tuple[int, int]] = []
        self.presented_rows = 0
        self.fence_count = 0
        self.readback = 0
        self.error = False

    @property
    def dirty(self) -> bool:
        return bool(self.present_rows or self.buffer_diff_rows)

    @staticmethod
    def _signed(value: int) -> int:
        return (value & 0x7fffffff) - (value & 0x80000000)

    def _valid_coords(self, *values: int) -> bool:
        return all(-self.COORD_LIMIT <= value <= self.COORD_LIMIT for value in values)

    def _inside(self, x: int, y: int) -> bool:
        x0, y0, x1, y1 = self.clip
        return self.back._inside(x, y) and x0 <= x <= x1 and y0 <= y <= y1

    def _pixel(self, x: int, y: int, value: int) -> None:
        if self._inside(x, y):
            self.back.set(x, y, value)
            self.buffer_diff_rows.add(y)

    def _line(self, x0: int, y0: int, x1: int, y1: int) -> None:
        dx, dy = abs(x1 - x0), -abs(y1 - y0)
        sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
        err = dx + dy
        while True:
            self._pixel(x0, y0, self.color)
            if x0 == x1 and y0 == y1:
                return
            twice = 2 * err
            if twice >= dy:
                err += dy
                x0 += sx
            if twice <= dx:
                err += dx
                y0 += sy

    def _char(self, character: int, x: int, y: int) -> None:
        for row, bits in enumerate(glyph_rows(character)):
            for col in range(5):
                if bits & (1 << (4 - col)):
                    self._pixel(x + col, y + row, self.color)

    def write_text(self, data: bytes, offset: int = 0) -> None:
        if offset < 0 or offset + len(data) > len(self.text):
            raise ValueError("text RAM range")
        self.text[offset:offset + len(data)] = data

    def write_sprites(self, data: bytes, offset: int = 0) -> None:
        if offset < 0 or offset + len(data) > len(self.sprites):
            raise ValueError("sprite RAM range")
        self.sprites[offset:offset + len(data)] = data

    def _present(self) -> None:
        self.last_updates = []
        self.presented_rows = len(self.present_rows)
        cx, cy, flags = self.cursor
        visible = bool(flags & 1) and self.front._inside(cx, cy)
        for y in sorted(self.present_rows):
            for x in range(self.front.width):
                bit = self.front.get(x, y)
                if visible:
                    on_cursor = (x == cx and y == cy) if flags & 2 else (
                        y == cy and abs(x - cx) <= 2
                        or x == cx and abs(y - cy) <= 2
                    )
                    bit ^= int(on_cursor)
                self.display.set(x, y, bit)
                self.last_updates.append((y * self.front.width + x, bit))
        self.present_rows.clear()

    def execute(self, cmd: GPUCommand) -> int | None:
        if len(cmd.args) > 8:
            raise ValueError("GPU commands have at most eight arguments")
        a = tuple(v & 0xffffffff for v in cmd.args) + (0,) * (8 - len(cmd.args))
        s = tuple(self._signed(v) for v in a)
        if cmd.op == GPUOp.NOP:
            return None
        if cmd.op == GPUOp.CLEAR:
            self.back.clear(a[0] & 1)
            self.buffer_diff_rows.update(range(self.back.height))
        elif cmd.op == GPUOp.SET_PIXEL:
            self._pixel(s[0], s[1], 1)
        elif cmd.op == GPUOp.CLEAR_PIXEL:
            self._pixel(s[0], s[1], 0)
        elif cmd.op == GPUOp.INVERT_PIXEL:
            self._pixel(s[0], s[1], self.back.get(s[0], s[1]) ^ 1)
        elif cmd.op == GPUOp.READ_PIXEL:
            self.readback = self.back.get(s[0], s[1])
            return self.readback
        elif cmd.op == GPUOp.LINE:
            if not self._valid_coords(*s[:4]):
                self.error = True
            else:
                self._line(*s[:4])
        elif cmd.op in (GPUOp.RECT, GPUOp.FILL_RECT):
            x, y, w, h = s[:4]
            if not self._valid_coords(x, y) or not (0 <= w <= self.COORD_LIMIT and 0 <= h <= self.COORD_LIMIT):
                self.error = True
            else:
                for yy in range(max(0, y), min(self.back.height, y + h)):
                    for xx in range(max(0, x), min(self.back.width, x + w)):
                        if cmd.op == GPUOp.FILL_RECT or xx in (x, x + w - 1) or yy in (y, y + h - 1):
                            self._pixel(xx, yy, self.color)
        elif cmd.op == GPUOp.CIRCLE:
            x, y, radius = s[:3]
            if not self._valid_coords(x, y) or not 0 <= radius <= self.MAX_RADIUS:
                self.error = True
            else:
                inner = max(0, radius - 1) ** 2
                for yy in range(max(0, y - radius), min(self.back.height, y + radius + 1)):
                    for xx in range(max(0, x - radius), min(self.back.width, x + radius + 1)):
                        if inner <= (xx - x) ** 2 + (yy - y) ** 2 <= radius ** 2:
                            self._pixel(xx, yy, self.color)
        elif cmd.op in (GPUOp.BLIT, GPUOp.SPRITE):
            start, x, y, w, h = s[:5]
            if not (0 <= start <= 1024 and 0 <= w <= 1024 and 0 <= h <= 1024
                    and start + w * h <= 1024 and self._valid_coords(x, y)):
                self.error = True
            else:
                for yy in range(h):
                    for xx in range(w):
                        value = self.sprites[start + yy * w + xx] & 1
                        if value or cmd.op == GPUOp.BLIT:
                            self._pixel(x + xx, y + yy, self.color if value else self.color ^ 1)
        elif cmd.op == GPUOp.DRAW_CHAR:
            if not self._valid_coords(s[1], s[2]):
                self.error = True
            else:
                self._char(a[0], s[1], s[2])
        elif cmd.op == GPUOp.DRAW_TEXT:
            start, length, x, y = s[:4]
            if not (0 <= start <= 256 and 0 <= length <= 256 - start and self._valid_coords(x, y)):
                self.error = True
            else:
                for index in range(length):
                    self._char(self.text[start + index], x + index * 6, y)
        elif cmd.op == GPUOp.SCROLL:
            self.back.scroll(s[0], s[1])
            self.buffer_diff_rows.update(range(self.back.height))
        elif cmd.op == GPUOp.SET_CLIP:
            self.clip = s[:4]
        elif cmd.op == GPUOp.RESET_CLIP:
            self.clip = (0, 0, self.back.width - 1, self.back.height - 1)
        elif cmd.op == GPUOp.SET_FONT:
            if a[0] != 0:
                self.error = True
            else:
                self.font = 0
        elif cmd.op == GPUOp.SET_COLOR:
            self.color = a[0] & 1
        elif cmd.op == GPUOp.SET_CURSOR:
            _, old_y, old_flags = self.cursor
            self.present_rows.update(
                y for y in range(self.front.height)
                if old_flags & 1 and abs(y - old_y) <= 2
                or a[2] & 1 and abs(y - s[1]) <= 2
            )
            self.cursor = (s[0], s[1], a[2] & 3)
        elif cmd.op == GPUOp.COPY_BUFFER:
            self.back._rows = list(self.front._rows)
            self.buffer_diff_rows.clear()
        elif cmd.op == GPUOp.SWAP_BUFFER:
            self.front, self.back = self.back, self.front
            self.present_rows.update(self.buffer_diff_rows)
            self._present()
        elif cmd.op == GPUOp.PUSH_DIRTY:
            self._present()
        elif cmd.op == GPUOp.FENCE:
            self.fence_count = (self.fence_count + 1) & 0xffffffff
            self.readback = self.fence_count
            return self.readback
        else:
            self.error = True
        return None
