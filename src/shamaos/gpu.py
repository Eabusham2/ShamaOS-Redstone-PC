from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Iterable


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
    def __init__(self, width: int = 320, height: int = 180) -> None:
        self.front = Framebuffer(width, height)
        self.back = Framebuffer(width, height)
        self.dirty = True

    def execute(self, cmd: GPUCommand) -> int | None:
        a = cmd.args
        if cmd.op == GPUOp.NOP:
            return None
        if cmd.op == GPUOp.CLEAR:
            self.back.clear(a[0] if a else 0)
        elif cmd.op == GPUOp.SET_PIXEL:
            self.back.set(a[0], a[1], 1)
        elif cmd.op == GPUOp.CLEAR_PIXEL:
            self.back.set(a[0], a[1], 0)
        elif cmd.op == GPUOp.INVERT_PIXEL:
            self.back.invert(a[0], a[1])
        elif cmd.op == GPUOp.READ_PIXEL:
            return self.back.get(a[0], a[1])
        elif cmd.op == GPUOp.LINE:
            self.back.line(*a[:4], value=(a[4] if len(a) > 4 else 1))
        elif cmd.op == GPUOp.RECT:
            self.back.rect(*a[:4], value=(a[4] if len(a) > 4 else 1), fill=False)
        elif cmd.op == GPUOp.FILL_RECT:
            self.back.rect(*a[:4], value=(a[4] if len(a) > 4 else 1), fill=True)
        elif cmd.op == GPUOp.SCROLL:
            self.back.scroll(a[0], a[1])
        elif cmd.op == GPUOp.COPY_BUFFER:
            self.back._rows = list(self.front._rows)
        elif cmd.op == GPUOp.SWAP_BUFFER:
            self.front, self.back = self.back, self.front
        elif cmd.op in {
            GPUOp.PUSH_DIRTY,
            GPUOp.FENCE,
            GPUOp.SET_CLIP,
            GPUOp.RESET_CLIP,
            GPUOp.SET_FONT,
            GPUOp.SET_COLOR,
            GPUOp.SET_CURSOR,
            GPUOp.BLIT,
            GPUOp.SPRITE,
            GPUOp.DRAW_CHAR,
            GPUOp.DRAW_TEXT,
        }:
            # Hardware/OS layer supplies font/sprite/clip resources.
            pass
        else:
            raise ValueError(f"unsupported GPU command: {cmd.op}")
        self.dirty = True
        return None
