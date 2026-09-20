# Memory Hierarchy

## Agreed capacities

- main RAM exposed to software: **1 MiB**.
- cache/fast scratch target: **16 KiB**.
- flash: **4 MiB** (4× the 1 MiB RAM).
- VRAM default: **32 KiB**.

## Why not 2 GiB physical redstone RAM

2 GiB = 2,147,483,648 bytes = 17,179,869,184 bits before any decoders, read/write logic or wiring.

The supplied redstone computers demonstrate useful general-purpose computation with hundreds of bytes of RAM. SHA-256 mining needs very little working memory compared with its arithmetic cost. A physically literal 2 GiB cell array would make the build dominated by storage rather than useful compute.

## Main RAM strategy

The software address space is 32-bit. The initially installed main-memory range is 1 MiB.

Physical implementation is allowed to use:

- banks.
- pages.
- multiplexed address/data buses.
- compact latch/comparator/repeater storage.
- sparse or backing techniques documented in the manifest.

The final build manifest must distinguish:

- `logical_ram_bytes`.
- `physical_ram_bytes`.
- bank/page count.
- access latency.
- backing mode.

## Cache

Target 16 KiB.

Possible layouts:

- direct mapped for simpler redstone.
- 2/4-way set associative if cost is acceptable.
- explicitly managed scratch/cache hybrid.

The software-visible behavior must remain coherent. System Monitor occupancy is based on valid cache lines, not an invented percentage.

## VRAM

320×180 at 1 bit/pixel = 7,200 bytes/frame.

Two frames = 14,400 bytes.

32 KiB VRAM therefore leaves room for:

- front/back buffers.
- dirty tiles.
- font/sprite staging.
- GPU command data.

## Flash

ShamaFS uses persistent block allocation and stores OS/apps/documents/logs.

Flash usage in the UI is calculated from actual allocated filesystem blocks.

## Resource accounting

### RAM

Used = kernel/shared allocations + app allocations + buffers.

### Cache

Used = valid/allocated lines.

### Flash

Used = reserved filesystem metadata + allocated file data blocks.

All three values are exposed through syscalls and System Monitor.
