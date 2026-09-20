# ShamaOS Architecture

## Overview

ShamaOS is a 32-bit redstone computer with a CPU containing a native SHA execution unit, a separate GPU, cache, banked main RAM, persistent flash storage, keyboard/controller input and a tiled lamp display.

```text
                         +----------------------+
                         |      Boot / Power    |
                         +----------+-----------+
                                    |
                                    v
+-----------+        +--------------+--------------+       +-------------+
| Keyboard  |------->|          System I/O         |<------| Controller  |
+-----------+        +--------------+--------------+       +-------------+
                                    |
                          +---------+---------+
                          |    System Bus     |
                          +--+------+------+--+
                             |      |      |
              +--------------+      |      +----------------+
              v                     v                       v
       +------+-------+       +-----+------+          +-----+------+
       |     CPU      |<----->| Cache/RAM  |<-------->|   Flash    |
       | ALU + SHA EU |       +-----+------+          +------------+
       +------+-------+             |
              |                     |
              +----------+----------+
                         |
                         v
                  +------+------+
                  |     GPU     |
                  +------+------+
                         |
                    +----+----+
                    |  VRAM   |
                    +----+----+
                         |
                  +------+------+
                  | Lamp Screen |
                  +-------------+
```

## CPU

Default datapath: 32 bits.

Core execution blocks:

- integer ALU.
- multiply/divide/modulo unit.
- barrel shift/rotate unit.
- branch/condition unit.
- load/store unit.
- native SHA-256 unit.
- register file.
- PC/instruction fetch.
- call/stack support.
- control/decode.
- trap/syscall interface.
- cache interface.
- debug/performance counters.

The SHA unit is architecturally part of the CPU. It may be physically large and internally multi-cycle/pipelined, but software sees it through normal CPU opcodes.

## Memory

### Registers

Sixteen 32-bit GPRs. `r0` is strongly preferred as a hardwired zero register because it simplifies software and follows the useful teaching-computer pattern.

### Cache

Target 16 KiB. The physical design may be set-associative or explicitly managed scratch/cache, but its behavior and accounting must be deterministic.

### Main RAM

Software-visible target: 1 MiB.

A fully literal 8,388,608-bit redstone cell array plus decoding is enormous. The implementation may therefore bank, page, sparsely instantiate or otherwise hierarchically back the address space. The build manifest records:

- logical capacity.
- physically instantiated capacity.
- bank/page geometry.
- access mechanism.

### VRAM

Default 32 KiB. A 320×180 1-bit framebuffer consumes 7,200 bytes; double buffering consumes 14,400 bytes before metadata, leaving comfortable room for tile state/font/sprite working data.

### Flash

Default 1 MiB persistent storage. Flash contains:

- boot image.
- kernel/shell.
- apps.
- source files.
- text files.
- miner history.
- filesystem metadata.

## System bus

The logical bus carries:

- 32-bit data.
- 32-bit address.
- read/write.
- byte enables.
- device select.
- request/acknowledge.
- interrupt/event signals.
- GPU/SHA/cache status.

Physical redstone wiring may multiplex signals to reduce width/area, but the logical interface remains stable.

## GPU

The GPU is independently clockable and consumes commands from a memory-backed queue or memory-mapped command registers.

GPU owns/coordinates:

- framebuffer writes.
- font ROM.
- line/rectangle rasterization.
- text.
- blits/sprites.
- scrolling/clipping.
- buffer swap/copy.
- dirty tile map.
- display tile update scheduling.

CPU does not individually bit-bang every lamp for normal GUI rendering.

## Display

Default 320×180, 1 bpp.

Physical panel is divided into 32×18-pixel tiles by default: ten tiles across and ten tiles down. Tile dimensions are configurable.

Each tile receives:

- tile address/select.
- local pixel index/data.
- latch/update.
- optional dirty flag.

This avoids a unique long wire from the GPU to every pixel.

## Flash filesystem

The filesystem is intentionally simple enough to implement in redstone:

- fixed superblock.
- file table/inodes.
- block allocation bitmap.
- fixed-size blocks.
- checksummed metadata where affordable.
- files with name/type/size/start-block metadata.
- atomic-ish metadata update sequence for safe save/rename/delete.

See `FILESYSTEM.md`.

## Boot

1. physical ON sets/reset-latches to known state and enables clock.
2. boot ROM verifies basic hardware status.
3. cache/RAM/GPU initialized.
4. filesystem superblock read.
5. kernel image copied from flash to RAM.
6. GUI/font/core app metadata loaded.
7. desktop starts.
8. cache warms through normal instruction/data fetch.

## Shutdown

1. block new app launches.
2. flush dirty file buffers.
3. finish/abort GPU commands safely.
4. save settings/miner state.
5. clear app-owned allocations.
6. mark filesystem clean.
7. gate/stop main clock.

## Application execution

Apps execute on the CPU. The OS provides syscalls for files, memory, input, GUI and process lifecycle.

On app exit:

- close handles.
- save requested state.
- free app RAM.
- invalidate app cache entries.
- clear app window/temporary VRAM.
- return to desktop.

The resident OS is not wiped.

## Mining path

```text
Miner app
  -> prepare 80-byte header/target
  -> CPU SHA instructions
  -> nonce increment
  -> DSHA256
  -> HASHCMP
  -> repeat or save result
  -> update GUI/log through OS
```

The SHA path is actual CPU/redstone work, not computed by the Python generator at runtime.
