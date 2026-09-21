# Shama GPU

Shama GPU is a programmable graphics command engine with a separate physical **32 KiB VRAM**.

## Commands

Implemented operations include NOP, CLEAR, SET/CLEAR/INVERT/READ_PIXEL, LINE, RECT, FILL_RECT, CIRCLE, BLIT, SPRITE, DRAW_CHAR, DRAW_TEXT, SCROLL, SET/RESET_CLIP, SET_FONT, SET_COLOR, SET_CURSOR, COPY_BUFFER, SWAP_BUFFER, PUSH_DIRTY and FENCE.

The current physical `PUSH_DIRTY` path performs a complete frame push; sparse dirty-tile skipping is a future optimization, not a correctness dependency.

## VRAM

- framebuffer 0: `0x0000`
- framebuffer 1: `0x2000`
- text staging: `0x4000`
- sprite staging: `0x4100`
- scroll scratch: `0x5000`

VRAM uses eight physical 4 KiB banks. CPU byte strobes are preserved for text/sprite writes.

## Display

The display is **320×180 × 1 bit = 57,600 lamps**. It is horizontal in X/Z and viewed from above. Pixel pitch is 5×5 blocks. Row-lock buses run on a lower Y layer so they cross column buses without joining.

Each pixel contains a column-data tap, data repeater, locked storage repeater, row-controlled side-lock repeater, powered support block and lamp.

## Synchronization

GPU exposes busy/queue state and a fence counter. ShamaOS supports generic submit, text, rectangle and blocking fence syscalls.
