# Memory hierarchy and physical efficiency

## Capacity contract

| Memory | Bytes | 4 KiB banks |
|---|---:|---:|
| Executable cache / fast region | 8,192 | 2 |
| Main RAM | 98,304 | 24 |
| Persistent flash | 256,000 | 62 full + one 2 KiB |
| VRAM | 16,384 | 4 |
| **Total** | **378,880 (370 KiB)** | **93 modules, one half-size** |

That total contains **3,031,040 physical stored bits**. The owner explicitly
halved RAM to **96 KiB**, conditional on every shipped app still fitting, then
halved flash to **250 KiB**. Cache remains 8 KiB and VRAM 16 KiB. These are the
2026-11-12 amended capacities; the screen and application feature contract remain
intact. The generator models every bit as physical storage.

A full bank contains 1,024 words of 32 bits. Flash has 62 full banks and a final
512-word bank, for exactly 256,000 bytes. The final bank contains no inaccessible
padding cells; its decoder rejects high rows instead of aliasing them onto valid
addresses. Physical capacity does not change when banks are stacked vertically.

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

The cell change removes **520,192 blocks per full 4 KiB bank**, about **20.7%**
of the corrected same-capacity baseline. The exact final 2 KiB bank emits
**996,224 placements** and contains only 16,384 stored bits. Across 92 full banks
and this half-size bank, storage cells/internal buses emit **184,219,008
placements**. With the compact cell held fixed, the authorized capacity
reductions remove 2,388,866,176 placements from the original 1 MiB RAM / 4 MiB flash design's
2,573,085,184 compact-cell placements, including its original fast memory and
VRAM. Capacity reduction and cell efficiency are distinct savings; fewer stored
bits do not make an individual bit cheaper.

These counts are measured design costs, not physical lower bounds or full-machine
counts. Wider spaces/extra duplicated buses in the previous layout were not an
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

The default world has grass at Y=-61 and the lowest circuit support at Y=-60.
The revised **93-module** layout uses **four horizontal memory columns**. Three
hold flash (30 + 30 + 2 full banks plus the final half-size bank). The fourth
stacks cache's two banks, VRAM's four and RAM's 24, with four-block separators
between independent fabrics. It reaches Y=307; flash reaches Y=299. Grass, dirt
and bedrock remain intact. Higher grass presets fall back to fewer layers or
separate columns when the shared stack cannot satisfy world-height and routing
clearance. The final flash bank's shorter storage rows connect to the same
full-length shared return plane, and that extension is included in the counts.

The production memory interface uses **10 binary row-address wires per fabric**
plus bank selection and data/read/write signals. Each full tile contains a real
1,024-row decoder; the final partial bank masks uninstalled rows. This replaces four sets of 1,024 system-wide row wires with
four sets of ten, eliminating **4,056 global row-select wires**. Decoder blocks
and local returns are included in the following counts. The tradeoff is local
logic in exchange for fewer long connections; it is not a measured whole-machine
speed claim.

The RTL controller latches an accepted address directly and returns a read
after three core-clock edges, or a byte-preserving write after five, with
separate address/read settling and post-commit hold phases. There is no rotating
row seek. The duration of a safe physical core clock still depends on route,
decoder and storage timing; these controller phase counts are not Minecraft
wall-clock measurements.

| Fabric | Bank modules | Logical tiles | Storage-only chunks | With local controls and decoder |
|---|---:|---:|---:|---:|
| Cache | 2 | 1 | 4,246 | 8,111 |
| Main RAM | 24 | 1 | 4,246 | 7,583 |
| Flash | 62 full + one half | 3 | 12,738 | 23,035 |
| VRAM | 4 | 1 | 4,246 | 7,748 |
| **Occupied union, sharing cache/RAM/VRAM X/Z** | **93** | **4 horizontal columns** | **16,984** | **31,390** |

The three non-flash fabrics share horizontal chunks at different heights.
Their occupied union is **8,355 chunks** with local wiring; adding their separate
counts would overcount **15,087 chunks**. Storage alone occupies 56 region files
and has a **70,025,216-byte** minimum Anvil sector/header cost. That is a lower
bound, not a forecast of the compressed world file size.

Storage cells/internal bank buses emit **184,219,008 placement operations**.
Local selectors/returns add **44,384,910**, binary decoders **4,375,248** and
screen/controls/debug/clock **2,499,826**, producing **235,478,992 regular-component
placement operations**. These are emission operations, not a claim of that many
unique blocks in a complete world. Flash's exact half bank avoids 995,328
storage/internal-bus placements that padding it to 4 KiB would have introduced.

| Measured regular component | Occupied horizontal chunks |
|---|---:|
| Cache + RAM + VRAM shared column, including local wiring | 8,355 |
| Flash, including local wiring | 23,035 |
| Screen | 5,084 |
| Keyboard/controller circuitry | 33 |
| Debug panel | 9 |
| Clock | 17 |
| **Regular-component union** | **36,533** |

The full 320×180 panel now uses five-block column pitch and four-block row pitch.
Its 2,482,200 placements and 5,084 occupied chunks are checked against the actual
emitter. Compared with the previous five-by-five panel, it saves 114,560
placements and 1,244 occupied chunks (19.66%) without changing screen resolution
or VRAM. All 57,600 lamps and stored bits pass the full-panel actual-engine
retention/isolation trace. This does not establish a readable console view.

The regular-component union uses **87 region files** with a **150,351,872-byte**
minimum Anvil sector/header cost. Its envelope is X=-64..4273, Z=-235..6614:
**4,338×6,850 blocks**, or **272×429 = 116,688 envelope chunks**. The rectangle's
perimeter is **22,376 blocks**. Only 36,533 chunks in that rectangle contain these
components. Coloring their existing supports adds zero placements or chunks.

| Checkpoint | Regular occupied chunks | Envelope chunks | Regular placement operations |
|---|---:|---:|---:|
| 192 KiB RAM / 500 KiB flash | 67,192 | 186,615 | 444,728,976 |
| 96 KiB RAM / 500 KiB flash, shared memory column | 53,482 | 135,993 | 391,253,772 |
| **96 KiB RAM / 250 KiB flash, compact screen** | **36,533** | **116,688** | **235,478,992** |

The latest step removes **16,949 occupied chunks (31.69%)**, **19,305 envelope
chunks (14.20%)** and **155,774,780 placement operations (39.81%)** from the
96 KiB/500 KiB checkpoint. Against the earlier 192 KiB checkpoint, occupied and
envelope chunks are down **45.63%** and **37.47%**, respectively.

Counts come from the real geometry and exact X/Z chunk union, including overlaps
between independent fabrics. **Synthesized CPU/GPU/assembler/OS logic and their
internal/global routes remain additional.** A complete-machine chunk total and
verified running world remain open release gates. The console's 79 actuators fit
a connected, walkable 16×16 area; full-screen viewing from that station also
remains a gate. Regenerate the report after any geometry changes.

Production placement requires world-height bounds, no block collisions or
unintended electrical joins, isolated reads/writes across layers, initialization
retention and sufficient timing for actual routes. The shared-stack fixture
covers all 30 cache/VRAM/RAM bank selections with real MCHPRS updates and is
saved, independently reopened and replayed from serialized blocks. A full
1,024-address binary decoder is exercised separately, as are full-height
external-pin escapes. The complete 4 KiB storage bank separately checks long-bus
reads/writes and all 32,768 stored bits. Those complementary fixtures do not
substitute for a fully generated default-capacity machine.

Generation locality and reusable verified chunk sections can reduce export time
without changing runtime capacity or execution speed. Every physical block must
still be written at its real translated coordinates, including nonzero flash
initialization, and pass saved-file readback.

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

Measured from the assembled default image on 2026-11-12, after the authorized
RAM and flash halvings and compact Miner code/workspace changes. Reproduce these figures with:

```bash
shamaos software-usage --report build/software-usage.json
```

These are software allocations within the four physical capacities. They are
not CPU/GPU load, redstone gate utilization, measured Minecraft speed or project
completion. **Only one foreground app runs at a time.** RAM counts the loaded
12-byte kernel, foreground executable backing and complete owned workspace;
optional heap allocations are additional. Cache counts executable bytes.

| Foreground app | RAM bytes | Of 96 KiB RAM | Cache bytes | Of 8 KiB cache | Peak RAM without heap |
|---|---:|---:|---:|---:|---:|
| Desktop | 244 | 0.2482% | 232 | 2.8320% | 0.2482% |
| Editor | 70,824 | 72.0459% | 4,696 | 57.3242% | 88.7126% |
| File Explorer | 71,448 | 72.6807% | 5,320 | 64.9414% | 72.6807% |
| Bitcoin Miner | 8,292 | 8.4351% | 6,904 | 84.2773% | 8.4351% |
| System Monitor | 64 | 0.0651% | 52 | 0.6348% | 0.0651% |
| Terminal | 2,956 | 3.0070% | 1,920 | 23.4375% | 3.0070% |
| Calculator | 1,836 | 1.8677% | 1,824 | 22.2656% | 1.8677% |
| Paint | 1,152 | 1.1719% | 868 | 10.5957% | 1.1719% |
| Settings | 1,056 | 1.0742% | 772 | 9.4238% | 1.0742% |

Editor and Explorer reserve the complete 65,536-byte source buffer, 512-byte
filename workspace and 68-byte lifecycle context. Explorer launches binaries
from that source buffer. Editor assembles sources up to 49,152 bytes into its
aligned unused tail. Larger sources use another 16,384 bytes, producing the
**87,208-byte / 88.7126%** peak. The full **65,535-byte editing limit** and
**16,380-byte assembly/save limit** are preserved. Execution still requires a
binary of at most **8,192 bytes**; larger results invoke visible recovery when
run. Miner includes all software-SHA scratch, state and complete digests in a
1,376-byte contiguous workspace. Packing its separately live regions removes
4,000 unused bytes and frees a 4 KiB heap page; no digest or history fields were
removed. Redundant register loads/saves and address-only immediates were also
removed from its executable, reducing 7,308 bytes to 6,904.

All apps share the same **15,680-byte / 95.7031% VRAM reservation**, leaving
704 bytes. Two 320×180 one-bit frames consume 14,400 bytes (87.8906%), text
staging uses 256 and sprite staging 1,024. Scrolling needs no extra frame.
VRAM use is a reserved capacity, not a count of lit pixels or a new reservation
for every app. The screen, double buffering and GPU primitives remain intact.

The four pools total **370 KiB**. With Explorer active, the initial flash image
and shared VRAM included, counted allocation is **254,752 bytes / 67.2382%**.
Editor with its largest assembly staging reaches **269,888 bytes / 71.2331%**
before optional heap allocations. These physically separate pools cannot lend
capacity to one another. OS services, filesystem, assembler and GPU also have
physical logic registers that are not charged to RAM or cache.

### What occupies flash

The initial image allocates **162,304 bytes (158.5 KiB), or 63.40% of 250 KiB**.
Allocation includes metadata, whole-file block rounding and zero-filled files.

| Category | Allocated bytes | KiB | Of 250 KiB flash |
|---|---:|---:|---:|
| Filesystem metadata and journal | 4,608 | 4.50 | 1.80% |
| Complete editable program sources | 76,544 | 74.75 | 29.90% |
| Separate program executable files | 24,320 | 23.75 | 9.50% |
| Packed boot and application loader bundle | 22,784 | 22.25 | 8.90% |
| Miner history: 256 complete records | 32,768 | 32.00 | 12.80% |
| Miner saved state | 256 | 0.25 | 0.10% |
| Welcome documents | 1,024 | 1.00 | 0.40% |
| **Total** | **162,304** | **158.50** | **63.40%** |

There are **32 of 64 entries used**: 27 files and five real directories.
**93,696 bytes (91.5 KiB)** remain unallocated. Ordinary writes stop at
**243,200 bytes (237.5 KiB / 95%)**, leaving **80,896 bytes (79 KiB)** of
allocation headroom before that limit. Copy-on-write replacement also needs its
temporary second extent to fit; the guard rejects an excessive reservation
before changing the original file. DELETE and zero-length truncation can reclaim
space even at the limit. An OS popup explains protection.

At this smaller capacity, the full 65,535-byte source and 16,380-byte executable
limits still apply, but they are not a promise that every maximum-size file and
its temporary replacement fit alongside all shipped data. A new 64 KiB-rounded
source fits; adding a new 16 KiB-rounded output too would reach 238.5 KiB, one
KiB over the write limit. Reclaim space in Explorer first when required. A
replacement save can need more free space than a new file because the previous
good copy is retained until commit. Failed saves preserve it and the live app.

The packed loader file contains **22,640 logical bytes**, allocated in 22,784
bytes of filesystem blocks. Packing removes 141,200 bytes of unused old record
padding while retaining all editable sources and separate executable files.
Each loader record has a length word and complete word-aligned code. The private
`boot.bundle` is immutable through normal file APIs; editable files remain usable.
Miner's 32 KiB history is allocated from installation, including unused records.
Settings creates its persistent defaults on first launch when its file is
missing. Paint reads those settings; its canvas remains temporary and it does
not create a separate saved file.

| Installed program | Source allocated bytes | Executable allocated bytes | Packed loader record bytes | Persistent bytes | Total attributed flash bytes | Of flash |
|---|---:|---:|---:|---:|---:|---:|
| Boot | 512 | 256 | 0 | 0 | 768 | 0.3000% |
| Kernel | 256 | 256 | 16 | 0 | 528 | 0.2062% |
| Desktop | 1,280 | 256 | 236 | 0 | 1,772 | 0.6922% |
| Editor | 15,104 | 4,864 | 4,700 | 0 | 24,668 | 9.6359% |
| File Explorer | 18,176 | 5,376 | 5,324 | 0 | 28,876 | 11.2797% |
| Bitcoin Miner | 22,528 | 6,912 | 6,908 | 33,024 | 69,372 | 27.0984% |
| System Monitor | 512 | 256 | 56 | 0 | 824 | 0.3219% |
| Terminal | 6,144 | 2,048 | 1,924 | 0 | 10,116 | 3.9516% |
| Calculator | 6,656 | 2,048 | 1,828 | 0 | 10,532 | 4.1141% |
| Paint | 2,816 | 1,024 | 872 | 0 | 4,712 | 1.8406% |
| Settings | 2,560 | 1,024 | 776 | 0 | 4,360 | 1.7031% |

These program shares are attribution, not additional copies on top of the
category table. They count each source, separate binary, exact loader record and
installed persistent data once. Shared metadata, welcome documents and final
loader-file block padding contribute another **5,776 bytes**, reconciling the
program shares exactly to the 162,304-byte allocated image. New user files,
saves and copy-on-write reservations change occupancy; regenerate the report
rather than treating this snapshot as a permanent limit.

### Compact main-RAM address map

| Region | Inclusive physical RAM byte addresses | Size |
|---|---|---:|
| Foreground executable backing, accessible by service DMA | `0x0000..0x1FFF` | 8 KiB |
| Protected resident kernel slot | `0x2000..0x23FF` | 1 KiB |
| Filename workspace | `0x2400..0x25FF` | 512 bytes |
| Editor lifecycle context | `0x2600..0x2643` | 68 bytes |
| General page arena, with per-app exclusions below | `0x3000..0x17FFF` | 84 KiB |
| Editor / Explorer complete source buffer | `0x4000..0x13FFF` | 64 KiB |
| Assembly / user-program staging | `0x14000..0x17FFF` | 16 KiB |
| Miner header / digests / state / history / software SHA, overlays source | `0x4000..0x455F` | 1,376 bytes |
| Terminal workspace, overlays source | `0x5000..0x53FF` | 1,024 bytes |
| Settings record and filename, overlays source | `0x4000..0x410F` | 272 bytes |
| Paint record and filename, overlays source | `0x4200..0x430F` | 272 bytes |

CPU reads/writes below `0x2000` reach the separate cache. The services reach the
underlying physical RAM directly for load/flush/cleanup; that RAM is real
executable backing, not inaccessible padding. The generated image enforces the
kernel's 1 KiB slot limit. The current kernel occupies only 12 bytes.

The source, Miner, Terminal, Settings and Paint workspaces overlap because their
apps are mutually exclusive. Replacement completes persistence, releases the
old allocations, clears outgoing data and initializes the next app before it
runs. Explorer-to-Editor handoff preserves its transferred document and handle.
Staged launch retains source words until both executable copies finish, including
when the staging region is part of an outstanding heap allocation.

The allocator considers 21 pages of 4 KiB, excludes every page touched by the
active workspace, and permits up to 16 live allocation records. Editor's four
staging pages remain excluded even when its current document does not need them.

| Foreground app | Available heap | Largest initial contiguous allocation | Peak RAM with all available heap |
|---|---:|---:|---:|
| Desktop | 84 KiB | 84 KiB | 87.7482% |
| Editor | 4 KiB | 4 KiB | 92.8792% |
| File Explorer | 20 KiB | 16 KiB | 93.5140% |
| Bitcoin Miner | 80 KiB | 76 KiB | 91.7684% |
| System Monitor | 84 KiB | 84 KiB | 87.5651% |
| Terminal | 80 KiB | 72 KiB | 86.3403% |
| Calculator | 84 KiB | 84 KiB | 89.3677% |
| Paint | 80 KiB | 76 KiB | 84.5052% |
| Settings | 80 KiB | 76 KiB | 84.4076% |

Staged user code, like Desktop/Monitor/Calculator, has 84 KiB available before
its own allocations. Each allocated page adds **4.1667 percentage points** to
RAM use. Fragmentation and workspace ownership mean `total - used` is not a
promise of a single allocation of that size. Free and app exit clear released
pages; exhaustion invokes the requested graceful recovery path.

The report comes from [`software_resources.py`](../src/shamaos/software_resources.py),
the actual assembled image and [`memory_map.py`](../src/shamaos/memory_map.py).
The same map generates RTL service constants; firmware, allocator, boundary,
full-source assembly/save/run and per-app ownership tests check its behavior.

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
