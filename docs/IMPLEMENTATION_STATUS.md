# Implementation and release status

**Development and verification in progress; a complete running Minecraft world
has not been certified or released.** The package version `1.0.0` is not evidence
that all acceptance gates have passed. [REQUIREMENTS.md](REQUIREMENTS.md) is the
feature contract; [VALIDATION.md](VALIDATION.md) defines the evidence required.

## Implemented in code and exercised behaviorally

- 32-bit CPU, sixteen registers, complete integer/logic/branch/memory instruction
  families, bounded 256-entry stacks, defined faults, counters and flush behavior.
- Native SHA with inspectable state, and independent assembly double-SHA with
  complete-byte comparison, configurable header/target, counters, stop/resume,
  nonce exhaustion and 256 × 128-byte persistent history.
- Native-resolution GPU primitives, complete printable ASCII including lowercase,
  dirty-row updates, nondestructive cursors, queue/backpressure and fence semantics.
- Full-source Editor, filesystem-backed Explorer folders and handoff, separate
  source/binary output, cancelled destructive confirmations, settings persistence,
  Paint cursor and the shipped Terminal/Calculator/Monitor/Desktop programs.
- Owned resources, dirty save on app close, memory/cache clearing, orderly paused
  shutdown, immutable boot-ROM recovery after cache clearing, and boot failure
  diagnostics under explicit test.
- ShamaFS canonical paths/directories, handle ownership/transfer/cleanup,
  copy-on-write data replacement, a replayable metadata journal, bounds checking,
  full-disk/table errors and interrupted-write recovery.
- Queued keyboard/controller events with Shift and held-acknowledgement handling.
- Native Java 1.20.4 world creation, exact block-state saving, independent readback,
  corruption/collision/resource checks and a small saved-world runtime fixture.
- Portable ABC option detection and semantic enabled/reset flip-flop lowering.

The integrated SoC test has completed boot → create/type/save → app cleanup →
paused shutdown → cache-cleared reboot → Explorer → Editor reopen with the saved
bytes intact. A separate flow types a new assembly source through the real input
path, saves it, invokes the hardware assembler, runs the generated binary, checks
its result and returns from the halted program using universal Home. Its external
memory devices use bounded test models for broad failure coverage. A separate
96 KiB RAM / 250 KiB flash lifecycle run retains the four production memory adapters and exercises
their physical bank/row/read/write pins against delayed persistent bank models.
It passes boot, edit/save, paused shutdown, cache-cleared reboot and reopened
bytes in 8,603,261 simulator cycles. The production-controller typed-assembly
flow passes in 9,640,839 cycles; heap-backed self-replacement passes in 5,137,683.
That last case checks all 84 KiB of the released heap, copied code in both cache
and DMA-only backing, and a later return to Desktop. Neither backend is evidence
that a complete physical world has performed that flow.

## Physical fixtures already exercised in the actual engine

Generated gates and edge storage, clock startup/shutdown phases, stretched real
buttons, keyboard/controller isolation, compact preloaded memory, small latched
display regions, and representative routed/fanout/sequential nets have actual
MCHPRS behavioral tests. These tests revealed and corrected diode orientation,
read risers, preload retention, display backfeeding, clipped clock pulses and
stair/trunk collisions that source inspection alone missed.

The compact storage cell preserves all capacities while reducing the corrected
bank emission from 2,511,744 to 1,991,552 blocks per 4 KiB bank (about 20.7%).
See [MEMORY.md](MEMORY.md) for exact counts and source-design comparisons.
Three-dimensional bank packing and generation locality/template reuse are being
validated separately from the cell design. The default above-grass layout uses
30 layers at 12-block pitch on a grass Y=-61 world. Tests exercise all selected
banks in representative stacks, including 30/32-layer fixtures, a shared return
across three tiles and full-height routed input/output access. A full 4 KiB bank
separately passes 19 actual-engine steps / 81,920 redstone ticks with all 32,768
stored bits checked after each step. The small saved stacked-world fixture is
reopened and run from its serialized block states, with ground preservation
checked independently.

The owner authorized halving main RAM to **96 KiB**, conditional on all shipped
features still fitting. The map now uses real RAM beneath the CPU cache alias
for executable backing, protects a 1 KiB kernel slot and shares workspaces
between mutually exclusive foreground apps. Full-size editing/assembly, app
ownership, heap cleanup, code copying and fault recovery are exercised at the
new capacity. Every shipped app fits, including Editor's **87,208-byte / 88.71%**
large-source assembly peak before heap allocations. Normal Editor use is 72.05%,
Explorer 72.68%, and Miner 8.44%. The latest Miner executable shrinks from
7,308 to 6,904 bytes; all shipped features remain.

The **93-module** storage contains exactly **370 KiB**, including flash's
62 full 4 KiB banks plus one 2 KiB bank. It occupies **16,984 X/Z chunks** across
four horizontal columns: three flash columns and one shared cache2/VRAM4/RAM24
column. Including local selectors, binary row decoders and shared returns gives
**31,390 memory chunks**. The compact screen/controls/debug/clock bring the regular
union to **36,533 chunks** and **235,478,992 emitted placement operations**.
The regular envelope is **4,338×6,850 blocks / 116,688 chunks**. Native geometry
starts at Y=-60 above preserved grass at -61 and reaches Y=307. These are
geometric subtotals, excluding synthesized logic and external routes.
[MEMORY.md](MEMORY.md) reports exact shared unions and before/after comparisons.

Flash is now **250 KiB**, with **158.5 KiB / 63.40%** initially allocated. Packed
boot records retain complete sources, binaries and persistent records. The final
bank has only 512 physical rows: high addresses are rejected by both the real
controller and physical decoder, with no inaccessible padded storage cells.
The resource report reconciles every allocated byte by category and program,
including file-block rounding, shared metadata and optional heap capacity.

The physical screen now has five-block column spacing and four-block row
spacing. All **57,600 lamps and 57,600 storage repeaters** in a complete generated
320×180 panel pass the actual engine's 22-step retention/isolation trace.
Every pixel is checked after every step; first, middle, last and adjacent rows
are exercised. This saves **1,244 occupied chunks** and **114,560 placement
operations**. A tighter three-block row pitch failed and is not used. Horizontal
panel viewing from the console remains unresolved; this test proves electrical
behavior, not a readable operator view.

## Latest compaction and capacity protections

The owner subsequently halved cache to 8 KiB and VRAM to 16 KiB. GPU frames and
staging now occupy 15,680 bytes; overlap-safe word scrolling replaces its full
7,200-byte scratch frame. Pixel-differential GPU cases pass with
stalled buses, resource-fault queue discard and native-resolution traffic
bounds. A one-pixel horizontal scroll needs 5,399 acknowledged VRAM transactions;
a one-row scroll needs 3,590. This is bus traffic, not elapsed Minecraft speed.

All shipped binaries fit 8 KiB. Miner is now 6,904 bytes (84.28%), down from
8,508 bytes before compaction. Its complete hashing and persistence behavior is
retained; its 1,376-byte workspace replaces a 5,376-byte reservation while
preserving every separately live buffer. This frees a 4 KiB heap page and reduces
normal Miner RAM from 12,696 to 8,292 bytes. The latest focused CPU/services/Miner/
resource regression passes 70 cases with zero skips, including both hash modes,
workspace neighbor guards, allocator ownership and real-controller filesystem
transactions at the smaller flash capacity. Editor and Explorer reuse their source buffer for
assembly/launch when possible; the peak accounting retains dedicated staging
for larger Editor documents. The focused 96 KiB firmware/CPU/allocator/image/hardware-assembler regression
passes 166 distinct cases with no skips. The focused modal/kernel/services
regression passes 17 cases, and six capacity/accounting cases also pass. These
groups overlap at services coverage. The complete GPU suite and memory-adapter
variants additionally remain required CI checks. Eleven integrated
SoC checks pass at these capacities, including boot/lifecycle, typed assembly
and RAM/heap/cache/VRAM/protected-flash recovery. Five additional malformed
boot-bundle cases stop with the original diagnostics. These use real CPU/OS RTL
with modeled external memory; complete-world acceptance remains separate.

The filesystem 95% guard rejects unsafe copy-on-write reservations before any
mutation, preserves the last good file, and permits explicit reclamation and
journal recovery. Service/kernel tests cover the visible storage warning,
RAM/cache/VRAM recovery, failed autosave and interrupted odd-version snapshots.
Committed files survive recovery; incomplete edits are never advertised as saved.
Warnings now use a scanout-only GPU overlay: show/dismiss performs no VRAM
writes and preserves unfinished app frames and drawing state. The private
`boot.bundle` cannot be changed through normal file APIs; mount validates its
image-derived location and size before boot. Separate editable app sources and
binaries remain writable.

The complete compact console has passed physical-engine isolation/release checks
for all 64 keys and 15 perimeter controls. Its 16×16 activation area has connected
walkable access, and its backend occupies 33 chunks at the default alignment.
At the previous checkpoint, the console/screen relocation reduced the envelope
from 252,560 to 186,615 chunks; 96 KiB RAM reduced it to 135,993 and the
latest flash halving reduces it to 116,688. A saved console world was independently reopened and every actuator replayed
from its serialized block states: 81 actual-engine steps / 23,628 redstone ticks.
All 10,654 unique circuit blocks survived readback, and grass at Y=-61 remained
intact with the lowest circuit support at Y=-60. Screen viewing and full-machine
routing remain open gates.

The production interface now carries ten binary row-address bits per memory
fabric to real tile-local decoders, eliminating 4,056 long row-select signals
across the four fabrics. The full decoder checks all 1,024 addresses in the
actual engine. Mixed-bank read/write/retention and external-pin routing are
validated. The saved 30-bank mixed-stack fixture independently reopens **522,568
unique circuit blocks** and passes **84 steps / 43,520 redstone ticks**.
Full-size storage rows and reduced-row multi-bank fixtures remain complementary
evidence; they do not claim a running full-capacity SoC.

Failed normal saves now show **Keep App** initially, with an explicit
**Discard and Leave** choice. Cancel keeps the source, heap and ownership.
Confirmed discard completes the requested app switch or shutdown after the
popup restore fence, allowing a full flash to be reclaimed through Explorer.
Tests include physical-key event encoding, selection redraw, irrelevant queued
keys, Enter/Escape, cancellation and protected-flash Miner exit. Committed files
remain intact.

A final queue-interleaving regression caught a real dropped-warning defect:
an older invalid GPU command could discard a queued NOTICE while its later
fence still completed. The kernel now drains old work, rechecks the desired
notice, submits it and fences its presentation. The reproduced failure,
changed-selection and cancellation cases now pass with the real kernel/GPU.

An experimental bounded local router emits reused tracks and matched private
clock delays without adding blocks. Actual 14-cell and stacked 8+6-cell pipeline
fixtures pass at 140 and 180 redstone ticks per cycle, with zero observed
register-clock skew. The stacked fixture fails three transfers at 170 ticks
and violates hold time, providing a timing-sensitive negative control. This API
is separate from production routing. Scaling it to the mapped full SoC and
proving boundary/clock timing remains work in progress. A retained 64-cell cut
from the green assembler mapping also passes 24 actual-engine vectors with all
22 inputs and 16 boundary outputs retained. It uses 10 shared tracks, 50,626
placement operations and 589 chunks; those numbers belong to that cut only.

## Shared assembler arithmetic

The assembler's numeric accumulator now runs alongside the existing source-byte
reads. It replaces the unrolled 48-character arithmetic chain with one shared
36-bit intermediate path. Numeric bases, signs and overflow diagnostics are
preserved. Character storage falls from 64 bytes to an eight-byte prefix plus
the last character, while all 48 token characters still contribute to the full
symbol hash. Register and quoted-byte parsing need at most six prefix bytes.
No parser states or assembly cycles are added.

The candidate completes AIG mapping and driver-completeness checks. Its matched
physical-cell count is **559,593**, compared with **738,790** in the successful
[96 KiB baseline ASM job](https://github.com/Eabusham2/ShamaOS-Redstone-PC/actions/runs/43322462484/job/131546381733):
**179,197 fewer cells / 24.26%**. That baseline has identical assembler source
and effective preprocessed RTL to the preceding 250 KiB checkpoint. Candidate
local cell placements total 23,030,258, with 17,486 register clock terminals and
a maximum nonclock data-pin fanout of 672. These are mapping measurements, not
a complete routed-world footprint or measured Minecraft execution speed.

The focused host/RTL regression passes **84 tests with no skips**, including
52 permanent numeric, token, symbol-table and source-endpoint cases. Coverage
retains signed/unsigned decimal, hex and binary limits; malformed/overflowing
literals; quoted bytes; 48-character symbols sharing the same short prefix;
128 labels plus 128 definitions; source/output and physical RAM boundaries.
All nine shipped app sources retain their exact assembly cycle counts, including
Editor 67,555, Files 82,267 and Miner 102,541. Firmware image sizes and RCC
occupancy do not change. The normal physical-builder and complete CI gates
must still pass on the exact candidate commit.

## Component appearance and release automation

The generator applies deterministic component colors to existing stone supports
after circuit/routing positions have been determined. The console receives a
dark deck, alternating keyboard rows and colored action controls. No blocks are
added or moved, and all other functional states stay unchanged. Actual-engine
checks cover all 16 concrete colors, then saved/reopened memory, display and all
79 console actuators. The combined appearance/native/world-writing regression
passes 64 cases without skips. [APPEARANCE.md](APPEARANCE.md) contains the palette,
a diagram of the actual operating layer and upstream GitHub design references.

A new release workflow preserves existing assets and collects exact-commit CI
evidence. It can generate the actual full-world candidate and package a checked
world ZIP, multi-region Litematica schematic, source/tools, installer and simple
README. Archive and installation tests cover split downloads, exact state
readback, safe copied saves and failures. **The full-world acceptance runner is
still absent, and publication is explicitly blocked until it exists and passes.**
Hash-valid evidence or a manually supplied pass label cannot certify runtime.
[RELEASES.md](RELEASES.md) records this boundary and the concrete workflow.

The next installer revision corrects the Windows shared world lock and preserves
the owning session-lock descriptor throughout inventory and copying on POSIX.
It also rejects unsafe ZIP member names before platform normalization. The
focused installer/release regression passes 63 cases locally, including a
separate writer process attempting an exclusive lock after inventory and copy.
Both Windows CI variants must independently confirm the fix.

## Canonical capacities remain intact

| Item | Contract |
|---|---|
| Main RAM | 96 KiB, 24 banks |
| Fast memory | 8 KiB, 2 banks |
| Persistent flash | 250 KiB, 62 full 4 KiB banks plus one 2 KiB bank |
| VRAM | 16 KiB, 4 banks |
| Screen | 320×180 monochrome lamps, 5×4 physical pixel pitch |

Full banks contain 1,024 32-bit words; the final flash bank contains 512.
The capacity validator rejects reduced or
mislabelled configurations for the full release target. No small fixture is
advertised as that full machine.

## Exact-commit CI checkpoint

[Commit `0f028a9`](https://github.com/Eabusham2/ShamaOS-Redstone-PC/commit/0f028a9edd6f1a27a039783181eaf53779ae0a0c)
passed [CI run 43312032746](https://github.com/Eabusham2/ShamaOS-Redstone-PC/actions/runs/43312032746):
Linux/Windows on Python 3.11/3.12, RTL, all required behavioral/redstone checks,
the saved-world backend, all twelve full physical synthesis partitions and
reconnection. This is the preceding 192 KiB checkpoint.
The later [96 KiB checkpoint, `6391e3f`](https://github.com/Eabusham2/ShamaOS-Redstone-PC/commit/6391e3f9af36979da5ce509cd3e80300335d34e6)
passed 19 of 20 jobs, including all 638 required behavioral/redstone tests with no skips and all
twelve partition mappings. Its reconnection job failed because a CI assertion
still expected 48 RAM bank-select pins instead of 24. The workflow
now derives bank counts from the canonical capacity constants.

The [250 KiB checkpoint, `5b1e1b0`](https://github.com/Eabusham2/ShamaOS-Redstone-PC/commit/5b1e1b05c700775f4bb89f7af7f763285613cc48)
passed all twelve partition mappings and their full-SoC reconnection, Linux
tests, RTL and the saved-world backend. Its Windows variants exposed shared-lock and ZIP-fixture portability
errors; the recovered fixes above are included with the shared-assembler change.
The required behavioral suite was still running when this checkpoint note was
written. The next candidate requires a fresh complete CI
run; [current CI results](https://github.com/Eabusham2/ShamaOS-Redstone-PC/actions/workflows/ci.yml)
are the authority for the exact candidate. Passing synthesis does not establish
complete-world routing or in-game boot.
The behavioral CI budget is now 75 minutes to include production-controller
SoC persistence and full-size assembly boundaries without dropping coverage.

## Open release gates

- Full partition mapping/reconnection must pass for the exact candidate commit.
- Complete physical routing must pass collision, continuity, electrical isolation
  and timing checks, including dense endpoint escape and global clock skew.
- Resource estimates must include logic, backbones, external routing, validation
  work and runtime loading, not just storage cells.
- The current global-track routing topology has a prohibitive measured analytical
  lower bound. In the mapped assembler partition alone, 738,056 nets imply at
  least 31,252,056,139 X/Z chunks / 128,258,438,402,048 Anvil bytes, excluding
  trunks and stairs. These are the measured lower bounds from the green `a600a9c`
  assembler mapping with the existing global-track router; a redesigned local
  router must be measured separately. Generation now rejects a proven insufficient disk
  budget before creating world blocks; local/tiled routing is being developed.
- The complete capacity world must actually be generated, loaded, booted and
  exercised, including in-world editing/assembly, app cleanup and persistent
  miner/files across power cycles.
- The verified world and exact source/validation artifacts must be published
  together with checksums and runtime/loading settings.

The recommended increased tick rate or compatible acceleration is documented in
[RUNNING_AND_ACCELERATION.md](RUNNING_AND_ACCELERATION.md). It improves elapsed
execution time; it cannot replace any of these correctness gates.
