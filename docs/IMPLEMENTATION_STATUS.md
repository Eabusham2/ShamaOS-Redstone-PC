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
memory devices are behavioral test models; this is
not evidence that a complete physical world has performed that flow.

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

The owner finalized main RAM at 192 KiB and flash at 500 KiB on 2026-11-12.
The revised 179-bank storage emits 356,487,808 placements and occupies 39,750
X/Z chunks. Including every local selector/shared return raises memory to
60,805 chunks; screen, controls, debug and clock add 6,430, giving **67,235
regular-component chunks**. Their current envelope is 6,552×9,842 blocks;
synthesized logic and its routes are additional. The Editor retains its full
source buffer after relocation, and metadata/boot offsets follow the image.

The packed boot bundle removes unused fixed-record padding while retaining all
editable sources, binaries and persistent records. The latest image allocates
159.75 KiB / 31.95% of flash. Editor normal RAM use is 36.03% (44.36% with
large-source assembly staging); Explorer uses 36.34%. Both retain the full
65,535-byte source limit. `software-usage` regenerates per-app values.

## Latest compaction and capacity protections

The owner subsequently halved cache to 8 KiB and VRAM to 16 KiB. GPU frames and
staging now occupy 15,680 bytes; overlap-safe word scrolling replaces its full
7,200-byte scratch frame. Forty-eight pixel-differential GPU cases pass with
stalled buses, resource-fault queue discard and native-resolution traffic
bounds. A one-pixel horizontal scroll needs 5,399 acknowledged VRAM transactions;
a one-row scroll needs 3,590. This is bus traffic, not elapsed Minecraft speed.

All shipped binaries fit 8 KiB. Editor and Explorer reuse their source buffer for
assembly/launch when possible; the peak accounting retains dedicated staging
for larger Editor documents. Host firmware/assembler/image/filesystem tests
pass 155 cases at the new capacities. Physical 8 KiB cache and 16 KiB VRAM adapter
variants have also passed; full integrated SoC reruns for the latest recovery
changes are in progress.

The filesystem 95% guard rejects unsafe copy-on-write reservations before any
mutation, preserves the last good file, and permits explicit reclamation and
journal recovery. Service/kernel tests cover the visible storage warning,
RAM/cache/VRAM recovery, failed autosave and interrupted odd-version snapshots.
Committed files survive recovery; incomplete edits are never advertised as saved.

A compact 64-key prototype has passed physical engine isolation/release checks
at a 15×15-block button span. Its complete controller/power/clock backend and
walkable access are being verified before replacing the current controls.
Screen viewing and full-machine routing remain open gates.

## Canonical capacities remain intact

| Item | Contract |
|---|---|
| Main RAM | 192 KiB, 48 banks |
| Fast memory | 8 KiB, 2 banks |
| Persistent flash | 500 KiB, 125 banks |
| VRAM | 16 KiB, 4 banks |
| Screen | 320×180 monochrome lamps, 5×5 physical pixel pitch |

All banks contain 1,024 32-bit words. The capacity validator rejects reduced or
mislabelled configurations for the full release target. No small fixture is
advertised as that full machine.

## Open release gates

- Full partition mapping/reconnection must pass for the exact candidate commit.
- Complete physical routing must pass collision, continuity, electrical isolation
  and timing checks, including dense endpoint escape and global clock skew.
- Resource estimates must include logic, backbones, external routing, validation
  work and runtime loading, not just storage cells.
- The current global-track routing topology has a prohibitive measured analytical
  lower bound. In the mapped assembler partition alone, 737,749 nets imply at
  least 31,239,260,074 X/Z chunks / 128,205,923,344,384 Anvil bytes, excluding
  trunks and stairs. This applies to that recorded mapping and router, not to a
  redesigned local router. Generation now rejects a proven insufficient disk
  budget before creating world blocks; local/tiled routing is being developed.
- The complete capacity world must actually be generated, loaded, booted and
  exercised, including in-world editing/assembly, app cleanup and persistent
  miner/files across power cycles.
- The verified world and exact source/validation artifacts must be published
  together with checksums and runtime/loading settings.

The recommended increased tick rate or compatible acceleration is documented in
[RUNNING_AND_ACCELERATION.md](RUNNING_AND_ACCELERATION.md). It improves elapsed
execution time; it cannot replace any of these correctness gates.
