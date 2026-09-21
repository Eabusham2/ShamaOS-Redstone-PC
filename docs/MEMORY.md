# Memory Hierarchy

## Locked capacities

| Memory | Capacity | Physical banks |
|---|---:|---:|
| executable cache / fast region | **16 KiB** | 4 × 4 KiB |
| main RAM | **1 MiB** | 256 × 4 KiB |
| flash | **4 MiB** | 1,024 × 4 KiB |
| VRAM | **32 KiB** | 8 × 4 KiB |

All four are physically instantiated by the default generator.

## Physical bank

A default bank contains 1,024 × 32-bit words = 4 KiB. Each stored bit uses a locked repeater. One-hot bank/row selectors avoid a gigantic binary decoder.

## Cache / app execution

When ShamaOS launches a foreground app:

1. its flash slot length is read;
2. old foreground RAM/cache contents are cleared;
3. exact executable bytes are copied into app RAM and cache;
4. the CPU starts the app at cache PC 0.

The resident kernel remains in main RAM. Cache usage is the actual number of loaded executable bytes.

## Main RAM

Main RAM is a literal 1 MiB fabric. ShamaOS uses fixed kernel/app workspaces plus a foreground-owned 4 KiB page allocator. Outstanding foreground allocations are released on app replacement.

## VRAM

The GPU owns a separate 32 KiB physical VRAM fabric. Two 320×180×1-bit frames consume 14,400 bytes total, leaving space for text/sprite/scratch data.

## Flash

ShamaFS occupies the full 4 MiB physical flash fabric and stores boot/kernel/apps/source/text/binaries/miner records plus metadata/allocation bitmap.

## Timing

Long memory routes are why the computational core uses a divided clock. Vanilla is intentionally very slow; MCHPRS accelerates the same physical paths.
