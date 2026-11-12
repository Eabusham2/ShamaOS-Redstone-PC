# Memory hierarchy and physical efficiency

## Capacity contract

| Memory | Bytes | 4 KiB banks |
|---|---:|---:|
| Executable cache / fast region | 16,384 | 4 |
| Main RAM | 1,048,576 | 256 |
| Persistent flash | 4,194,304 | 1,024 |
| VRAM | 32,768 | 8 |
| **Total** | **5,292,032** | **1,292** |

That total contains **42,336,256 physical stored bits**. The default generator
models every bit; it does not label sparse or host-backed storage as physical
RAM. A bank contains 1,024 words of 32 bits. Capacity is independent of whether
banks are spread across X/Z or stacked vertically.

## Compact bit cell

Each cell stores one binary bit in a locked repeater. The default bit pitch is
five blocks; word pitch is six. Three separated vertical planes carry data,
storage/read gates, and the read-return bus.

A target block makes the straight data column connect into its two-torch riser
without an extra dust branch. The selected row unlocks the storage repeater;
other rows remain locked. An independent subtract comparator masks read output.
That comparator directly powers the second riser. Directed input and spacing
prevent neighbouring data/read signals from feeding back into storage.

Row-bus refresh repeaters avoid every perpendicular lock/read tap. A fixed
repeater interval that happens to land on a side tap can break a cell despite
looking connected, so refresh placement accounts for the actual bit pitch.
Supported wider pitches are also behaviorally tested.

### Measured cell improvement

Counts below include support blocks and internal bank buses, using the real
emitter. They exclude bank-selection backbones, synthesized controllers, display,
CPU/GPU logic and external routes.

| Metric, one 4 KiB bank | Corrected eight-block-pitch baseline | Compact five-block-pitch cell |
|---|---:|---:|
| Placed blocks | 2,511,744 | 1,991,552 |
| Placed blocks per stored bit | 76.65 | 60.78 |
| X/Z chunks touched at an aligned origin | 6,560 | 4,630 |
| Capacity | 4,096 bytes | 4,096 bytes |

The cell change removes **520,192 blocks per bank**, or **672,088,064 blocks**
across the 1,292-bank capacity contract: about **20.7%** of the corrected baseline
storage emission. The new storage-only count is **2,573,085,184 placed blocks**.
This is a measured design cost, not a physical lower bound or a full-machine
count. Wider spaces/extra duplicated buses in the previous layout were not an
unavoidable consequence of the required memory capacity.

`shamaos generation-preflight` recalculates counts/chunks against the current
source and exact bank alignment. Reports include source hashes; don't reuse an
old benchmark after a cell/layout change. The native write benchmark and chunk
packing can change generation time and file size without changing physical
capacity or the placed-block count.

## Stacking, wiring and generation are separate optimizations

Stacking the same banks over one another reduces horizontal footprint and the
number of X/Z chunks. It does **not** by itself remove any of their blocks. It can
also shorten shared wiring if the bank-selection, data and read-return
backbones are redesigned around the stack.

Production layout changes require all of the following: valid world-height
bounds, no block collisions or unintended electrical joins, isolated writes and
reads across layers, full initial-state retention, and timing sufficient for the
longest actual route. The old global control planes above each flat fabric cannot
simply be copied into a densely stacked arrangement. Experimental 8/16-layer
packing is kept separate until the connected multi-bank fixture passes.

Generation has another large avoidable cost: repeatedly recreating identical
empty banks and flushing the same chunks as distant placement streams revisit
them. Ordering placements by nearby chunk/slab and reusing verified chunk
sections can reduce build time. Such reuse must still write every physical block,
translate chunk coordinates correctly, preserve nonzero flash initialization,
and pass independent saved-file readback. It is not virtual memory.

## Revisited source designs

The original [MattBatWings data-memory design](https://github.com/mattbatwings/Logical-Redstone-Archive/tree/6d054aa0a87d8f58f672625b3e6d0e727bd97016/Archive/RWM_read-write-memory/RWM001_256x_8_bit_Data_Memory)
provides 256 addresses × 8 bits and demonstrates denser vertical organization.
Its archived schematic is 69×38×71; counting its non-air blocks gives 79,999,
including its own decoder/interfaces. That is a useful design reference, not a
like-for-like timing or capacity test of ShamaOS's 4 KiB bank. Its archived
Minecraft version is 1.21.4; our target remains 1.20.4.

The archive also contains
[LLucas's cached serial RAM](https://github.com/mattbatwings/Logical-Redstone-Archive/tree/6d054aa0a87d8f58f672625b3e6d0e727bd97016/Archive/RWM_read-write-memory/RWM002_256_Byte_Cached_Serial_RAM).
Its density claim warrants investigation, but the archive currently tags that
entry **Broken**. It has not been installed as working ShamaOS storage.
Analog multi-bit and serial storage additionally require proven encoding,
read/write timing, refresh/persistence and binary interfaces before they can
replace the current bank. Density claims alone are insufficient.

## Runtime allocation and persistence

On foreground launch, services read the selected flash slot's actual length,
save/close the prior app as required, clear its owned memory/handles, load exact
executable bytes into app RAM and fast memory, and jump to PC zero. The resident
kernel remains in RAM. Cache usage reports loaded executable bytes.

Main RAM includes fixed OS/app workspaces and a foreground-owned allocator using
4 KiB pages. Released allocations are cleared; app replacement releases the old
owner's outstanding allocations. Workspaces include the complete 64 KiB Editor
buffer and all miner scratch/state, not merely the visible screen region.

Two 320×180 one-bit frames consume 14,400 bytes of the 32 KiB VRAM. The GPU owns
VRAM independently and maintains dirty-row/cursor state. CPU byte/halfword writes
to the memory fabric perform read-modify-write so untouched lanes survive.

Flash contains ShamaFS metadata, boot/app/source data, documents, directories,
settings and complete miner records. Data replacement is copy-on-write; the
metadata journal permits recovery after an interrupted commit. Flash is writable
persistent redstone storage, not a read-only program ROM.

## Timing and release evidence

Compact-cell validation covers preloaded state, all-zero/all-one and walking-bit
writes, randomized words, data changes while locked, adjacent-word readback, and
bit/row isolation using actual MCHPRS updates. Saved-world block verification and
full-machine timing/loading are additional gates.

The default core divider is deliberately conservative for long routes. A slow
clock alone does not prove clock-skew/hold safety or guarantee that all chunks
are ticking. See [VALIDATION.md](VALIDATION.md) and
[RUNNING_AND_ACCELERATION.md](RUNNING_AND_ACCELERATION.md). Increasing tick rate
or using compatible acceleration is recommended, after the relevant physical
paths and loading configuration have been validated.
