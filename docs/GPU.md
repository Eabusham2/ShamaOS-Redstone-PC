# Shama GPU

## Purpose

The GPU exists so ShamaOS is more than a CPU directly toggling lamps. It is a separate programmable command processor that consumes drawing work from shared memory/VRAM and updates a tiled physical redstone-lamp display.

The GPU is a ShamaOS extension; the supplied MattBatWings videos use memory-mapped display devices rather than this full GPU ISA.

## Logical architecture

```text
CPU
 |
 | MMIO / command ring
 v
+--------------------+
| GPU Command Engine |
+---------+----------+
          |
   +------+------+
   | Raster/Text |
   +------+------+
          |
      +---+---+
      | VRAM  |
      +---+---+
          |
   +------+------+
   | Tile/Dirty  |
   | Controller  |
   +------+------+
          |
   +------+------+
   | Lamp Panel |
   +-------------+
```

## Registers

Minimum memory-mapped control registers:

- `GPU_STATUS`
- `GPU_COMMAND`
- `GPU_ARG0..GPU_ARG7`
- `GPU_QUEUE_HEAD`
- `GPU_QUEUE_TAIL`
- `GPU_FRONTBUFFER`
- `GPU_BACKBUFFER`
- `GPU_CLIP_X0/Y0/X1/Y1`
- `GPU_CURSOR_X/Y`
- `GPU_ERROR`

The normal path is a command ring in RAM. MMIO direct command submission remains available for boot/debug code.

## Command format

Each command is a fixed-size record so redstone decoding is practical:

```text
byte 0    opcode
byte 1    flags
bytes 2-3 reserved/sub-op
words 1-7 arguments
```

The exact packed representation is defined in `src/shamaos/gpu.py`.

## Required commands

- `NOP`
- `CLEAR`
- `SET_PIXEL`
- `CLEAR_PIXEL`
- `INVERT_PIXEL`
- `READ_PIXEL`
- `LINE`
- `RECT`
- `FILL_RECT`
- `BLIT`
- `SPRITE`
- `DRAW_CHAR`
- `DRAW_TEXT`
- `SCROLL`
- `SET_CLIP`
- `RESET_CLIP`
- `SET_FONT`
- `SET_PALETTE/COLOR` (logical even on 1-bpp hardware)
- `SET_CURSOR`
- `COPY_BUFFER`
- `SWAP_BUFFER`
- `PUSH_DIRTY`
- `WAIT/FENCE`

## Framebuffer

Default: 320×180 × 1 bit = 57,600 bits = 7,200 bytes.

Double-buffering requires 14,400 bytes. Default VRAM is 32 KiB, leaving space for:

- dirty map.
- sprite/font staging.
- cursor state.
- command scratch.
- future 2-bpp experiments.

The framebuffer uses packed row-major bits.

## Dirty tiles

The physical display must not scan/re-drive every pixel unnecessarily.

Default tile size: 32×18 pixels.

- display = 10×10 tiles.
- each changed draw marks affected tile(s) dirty.
- `PUSH_DIRTY` or swap walks only dirty tiles.
- local tile controllers decode pixel offsets and latch lamp states.

This is a performance optimization; software sees a normal framebuffer.

## Text

A ROM font provides at least:

- A-Z
- a-z where space allows; case folding is acceptable for early hardware.
- 0-9.
- punctuation required for assembly/file names/status.
- arrows/icons useful to GUI navigation.

Default font cell: 6×8 including spacing.

The GPU text engine supports:

- draw character.
- draw string from RAM/flash buffer.
- optional 2× and 3× integer scale for headings/status.
- clipping.

## Synchronization

GPU status contains at least:

- busy.
- queue empty.
- queue full.
- fault.
- frame/push complete.

CPU can:

- poll.
- `FENCE`.
- receive an event/interrupt when configured.

## GUI use

The ShamaOS shell uses GPU primitives for:

- window frames.
- selection highlights.
- text.
- usage bars.
- confirmation dialogs.
- cursor.
- miner dashboard.
- file lists/editor lines.

Apps do not need to know lamp wiring.

## Testing

The Python reference GPU renders into an in-memory framebuffer. Every hardware command has a reference implementation and golden tests. Physical generator tests compare tile addressing and command decoding against the same definitions.
