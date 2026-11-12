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
bytes intact. Its external memory devices are behavioral test models; this is
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
validated separately from the cell design.

## Canonical capacities remain intact

| Item | Contract |
|---|---|
| Main RAM | 1 MiB, 256 banks |
| Fast memory | 16 KiB, 4 banks |
| Persistent flash | 4 MiB, 1,024 banks |
| VRAM | 32 KiB, 8 banks |
| Screen | 320×180 monochrome lamps, 5×5 physical pixel pitch |

All banks contain 1,024 32-bit words. The capacity validator rejects reduced or
mislabelled configurations for the full release target. No small fixture is
advertised as that full machine.

## Open release gates

- Full partition mapping/reconnection must pass for the exact candidate commit.
- Complete physical routing must pass collision, continuity, electrical isolation
  and timing checks, including dense endpoint escape and global clock skew.
- Stacked-bank selection/data/return paths must pass actual connected read/write
  tests before becoming the default.
- Resource estimates must include logic, backbones, external routing, validation
  work and runtime loading, not just storage cells.
- The complete capacity world must actually be generated, loaded, booted and
  exercised, including in-world editing/assembly, app cleanup and persistent
  miner/files across power cycles.
- The verified world and exact source/validation artifacts must be published
  together with checksums and runtime/loading settings.

The recommended increased tick rate or compatible acceleration is documented in
[RUNNING_AND_ACCELERATION.md](RUNNING_AND_ACCELERATION.md). It improves elapsed
execution time; it cannot replace any of these correctness gates.
