# Memory hierarchy and physical efficiency

## Capacity contract

| Memory | Bytes | 4 KiB banks |
|---|---:|---:|
| Executable cache / fast region | 8,192 | 2 |
| Main RAM | 196,608 | 48 |
| Persistent flash | 512,000 | 125 |
| VRAM | 16,384 | 4 |
| **Total** | **733,184 (716 KiB)** | **179** |

That total contains **5,865,472 physical stored bits**. The owner explicitly
amended RAM/flash to 192 KiB/500 KiB and then halved cache/VRAM to 8 KiB/16 KiB
on 2026-11-12. The screen and application feature contract remain intact. The default generator
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

The cell change removes **520,192 blocks per bank**, or **93,114,368 blocks**
across the final 179-bank contract: about **20.7%** of the corrected same-capacity
baseline of 449,602,176 blocks. Storage cells and internal bank buses now emit
**356,487,808 placements**. Separately, the owner's capacity reduction removes
1,113 banks / 2,216,597,376 compact-cell placements compared with the original
1 MiB RAM / 4 MiB flash design (2,573,085,184 placements including fast memory
and VRAM). These are distinct savings; reducing capacity does not make each
stored bit cheaper.
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

The production default fits **30 layers at 12-block vertical pitch** above grass
at Y=-61, preserving the grass, dirt and bedrock. Its lowest support is at Y=-60.
A grass surface at Y=63 permits 20 layers. Exact bank counts determine the final
partial tile; no empty banks are added to round 48 or 125 up to a power of two.

| Fabric | Banks | Tiles at up to 30 layers | Banks in last tile | Storage-only chunks | With local controls / returns |
|---|---:|---:|---:|---:|---:|
| Fast memory | 2 | 1 | 2 | 4,246 | 6,568 |
| Main RAM | 48 | 2 | 18 | 8,876 | 13,594 |
| Flash | 125 | 5 | 5 | 22,382 | 34,055 |
| VRAM | 4 | 1 | 4 | 4,246 | 6,588 |
| **Total** | **179** | **9** | — | **39,750** | **60,805** |

The same final capacities laid flat touch 793,786 storage chunks: the
above-ground stack cuts that horizontal storage count by **94.99%**. The
126 storage regions have a 163,848,192-byte Anvil sector/header lower bound,
not a predicted final world file size. Local memory selection and shared return
circuits add 85,626,782 placement operations beyond the 356,487,808 storage-cell
and bank-bus operations.

The complete screen adds 6,328 occupied chunks, controls 33, debug wall 9 and
clock 17. Their union is 6,387 chunks, disjoint from the memory district, so the
**regular-component union is 67,192 chunks** and emits **444,728,976 placement
operations**. The current regular-component envelope is X=-64..6887 and
Z=-235..6614: **6,952×6,850 blocks**, or **435×429 envelope chunks**. Its
186,615 envelope chunks include gaps; only 67,192 are occupied by these
components. The perimeter of that rectangle is 27,604 blocks. Moving the screen
into unused space and compacting the console reduces the previous 252,560-chunk
envelope by 26.11%, without reducing the memory or display capacities.

These exact geometric counts exclude synthesized CPU/GPU/assembler/OS logic
and all internal/global logic routing. A complete-machine total has not yet
been established. They also do not establish that every chunk can be loaded
and timed as a working machine. The console's 79 actuators now fit a connected,
walkable 16×16 area; full-screen viewing from that station remains a release
gate. Regenerate the report after any geometry changes.

Production layout changes require all of the following: valid world-height
bounds, no block collisions or unintended electrical joins, isolated writes and
reads across layers, full initial-state retention, and timing sufficient for the
longest actual route. The old global control planes above each flat fabric cannot
simply be copied into a densely stacked arrangement. Actual-engine tests cover
30/32-layer selected-bank isolation/retention and a three-tile shared read return;
the full 1,024-word bank separately passes long-bus reads/writes while checking
all 32,768 stored bits after each step. Full-machine access routes remain a gate.

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

Two 320×180 one-bit frames consume 14,400 bytes of the 16 KiB VRAM. The GPU owns
VRAM independently and maintains dirty-row/cursor state. CPU byte/halfword writes
to the memory fabric perform read-modify-write so untouched lanes survive.

Flash contains ShamaFS metadata, boot/app/source data, documents, directories,
settings and complete miner records. Data replacement is copy-on-write; the
metadata journal permits recovery after an interrupted commit. Flash is writable
persistent redstone storage, not a read-only program ROM.

### Current software usage snapshot

Measured from the assembled default image on 2026-11-12, including the signed
Calculator and full-source Editor. Reproduce these figures with:

```bash
shamaos software-usage --report build/software-usage.json
```

These are software allocations within the fixed capacities, not utilization of
all physical redstone logic. CPU/GPU busy percentages and achieved execution
speed require a running workload. Flash capacity is the owner's final explicit
500 KiB selection; the earlier 60%-occupancy proposal is superseded.

| Resource | Allocated payload | Capacity | Used |
|---|---:|---:|---:|
| Initial flash, including metadata and block rounding | 163,840 bytes (160 KiB) | 512,000 bytes (500 KiB) | 32.0000% |
| Flash file/directory entries | 32 (27 files, 5 directories) | 64 entries | 50.0000% |
| Front and back framebuffer pixels | 14,400 bytes | 16,384 bytes VRAM | 87.8906% |
| Frames, text and sprite staging; no scroll scratch | 15,680 bytes | 16,384 bytes VRAM | 95.7031% |

Initial flash leaves **348,160 bytes (340 KiB)** unallocated,
and **322,560 bytes** before the 475 KiB write-protection
threshold. Its used count includes the **23,044-byte packed boot/app
bundle**, all editable sources and separate executables, the full 32 KiB miner
history file, state, documents and 4,608 bytes of filesystem metadata. Packing
removes 140,796 bytes of former 16 KiB record padding.
Records retain length prefixes and word alignment. Shipped executables must fit
the 8 KiB fast memory. The assembler can still produce and save up to 16,380
bytes; attempting to execute more than 8,192 bytes invokes capacity recovery.
A zero-filled allocated file still consumes space. Copy-on-write replacement
needs a second extent temporarily; a reservation exceeding 95% is refused before
changing the original file.

The table uses hardware-service accounting: loaded kernel bytes + foreground
executable RAM backing + complete owned workspace, without extra heap pages.
**Only one foreground app is counted at a time.** Fast-memory use is actual
executable length. VRAM is a shared fixed reservation of **95.7031%** for every
app, leaving **704 bytes**; it is not a separate allocation per app or a count
of lit pixels.

| Foreground app | RAM bytes | Of 192 KiB RAM | Fast-memory bytes | Of 8 KiB fast memory | Peak RAM without heap |
|---|---:|---:|---:|---:|---:|
| Desktop | 244 | 0.1241% | 232 | 2.8320% | 0.1241% |
| Editor | 70,824 | 36.0229% | 4,696 | 57.3242% | 44.3563% |
| File Explorer | 71,448 | 36.3403% | 5,320 | 64.9414% | 36.3403% |
| Miner | 12,696 | 6.4575% | 7,308 | 89.2090% | 6.4575% |
| System Monitor | 64 | 0.0326% | 52 | 0.6348% | 0.0326% |
| Terminal | 2,956 | 1.5035% | 1,920 | 23.4375% | 1.5035% |
| Calculator | 1,836 | 0.9338% | 1,824 | 22.2656% | 0.9338% |
| Paint | 1,152 | 0.5859% | 868 | 10.5957% | 0.5859% |
| Settings | 1,056 | 0.5371% | 772 | 9.4238% | 0.5371% |

Editor and Explorer reserve a complete 65,536-byte source buffer, 512-byte name
workspace and 68-byte lifecycle context. Explorer launches binaries from its
text buffer. Editor assembles sources up to 49,152 bytes into the aligned unused
tail of that buffer. Longer sources temporarily reserve another 16,384 bytes,
so the full 65,535-byte source limit is preserved. Code is copied before reused
source/staging memory is cleared. The peak column includes that conditional
Editor staging, but no optional heap allocations. Miner includes its software
SHA scratch.

The resident kernel executable is a 12-byte entry loop. OS services, filesystem,
assembler and UI also use dedicated physical logic whose registers are not
charged to main RAM. The four memory pools total **716 KiB**. With Explorer
active, initial flash and shared VRAM included, the counted allocation is
**256,288 bytes / 34.9555%**. These
physically separate pools are not interchangeable. This is allocation
accounting, not overall hardware utilization or project completion.

### Compact main-RAM address map

| Region | Inclusive byte addresses | Size |
|---|---|---:|
| Boot / executable cache alias range | `0x00000..0x01FFF` | 8 KiB |
| Unmapped executable-alias gap | `0x02000..0x03FFF` | 8 KiB, not usable cache |
| Resident kernel slot | `0x04000..0x07FFF` | 16 KiB |
| Foreground executable backing | `0x08000..0x0BFFF` | 16 KiB |
| Foreground heap | `0x0C000..0x17FFF` | 48 KiB |
| Complete Editor source buffer | `0x18000..0x27FFF` | 64 KiB |
| Assembly / user-program staging | `0x28000..0x2BFFF` | 16 KiB |
| Filename workspace | `0x2C000..0x2C1FF` | 512 bytes |
| Editor lifecycle context | `0x2C200..0x2C243` | 68 bytes |
| Miner header / digest / state / history | `0x2D000..0x2DFFF` | 4 KiB |
| Miner software SHA / Terminal workspace | `0x2E000..0x2E4FF` | 1,280 bytes (Terminal uses 1,024) |
| Settings record and filename | `0x2F000..0x2F10F` | 272 bytes |
| Paint record and filename | `0x2F200..0x2F30F` | 272 bytes |

The allocator exposes **12 pages of 4 KiB**, or **49,152 bytes (25% of RAM)**,
with up to 16 live allocation records. Other address ranges have fixed uses or
are reserved. Therefore `total - used` is not a promise that the allocator can
hand out that entire remainder. Each allocated page adds 2.083333 percentage
points to RAM use. Unused VRAM gaps are likewise not an application allocation
API. The executable alias uses the separate fast-memory fabric; its address
range is not an extra copy of application workspace.

The report is generated by [`software_resources.py`](../src/shamaos/software_resources.py)
from [`os_image.py`](../src/shamaos/os_image.py) and
[`flashfs.py`](../src/shamaos/flashfs.py), matching the fixed workspace/usage
expressions in [`shama_services.sv`](../rtl/shama_services.sv) and frame/staging
map in [`shama_gpu.sv`](../rtl/shama_gpu.sv). Reassemble after firmware changes
instead of treating a dated snapshot as a permanent usage limit.

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
