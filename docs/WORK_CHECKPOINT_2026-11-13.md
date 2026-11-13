# Resumable work checkpoint — 2026-11-13

The owner requested saving the work and ending the turn so the unavailable
execution environment can recover. This branch preserves reviewed follow-ups
and isolated experiments. It is NOT a completed computer or release candidate.

## Authoritative production checkpoint

Main is 4e933a5b6f37894cc333dde3d57d10e3e78de047, based on
5b1e1b05c700775f4bb89f7af7f763285613cc48. It contains the 250 KiB flash
implementation, packed Miner, horizontal 5×4 screen, component colors, gated
release packaging, portable installer fixes, and shared assembler arithmetic.
The assembler mapping falls from 738,790 to 559,593 cells (24.26%) with unchanged
assembly cycles; 84 focused tests passed before the executor outage.

CI: https://github.com/Eabusham2/ShamaOS-Redstone-PC/actions/runs/43333367519
At the pause, both Windows variants, Ubuntu 3.12 and world-backend were green;
the remaining jobs were queued. Do not call the whole run green without checking.
The preceding 5b1 run passed all 12 physical partitions and reconnection, but
had the Windows failures fixed in 4e933a5. The required behavioral run was
superseded. Neither run certifies complete-world boot or routing.

This recovery branch adds the reviewed guide/14-day retention updates, two
documentation corrections, and isolated upright-screen recovery sources. It
does not select the experimental panel in production or change flash capacity.

## Latest owner instruction: 224 KiB flash

After the temporary 200 KiB proposal, the owner asked for approximately 225 or
226 KiB, or a clean value near there. The selected new target is **224 KiB**:
229,376 bytes = 56 full 4 KiB banks = 896 filesystem blocks.
This supersedes the pending 200 KiB plan. RAM remains 96 KiB, cache 8 KiB,
VRAM 16 KiB. The 224 KiB code change has NOT been applied or tested yet.
Preserve generic exact-partial-bank support.

The existing block-granular protection formula floors 95% to whole filesystem
blocks. At 224 KiB, it gives 851 blocks = 217,856 bytes = 212.75 KiB
(94.9777% of capacity). Do not round upward beyond 95%.

With the unchanged shipped image's 162,304-byte / 158.5 KiB allocation, projected
occupancy is 70.7589%, free capacity 65.5 KiB and allocation headroom below the
guard 54.25 KiB. Miner history replacement reserves a full 32 KiB COW extent:
158.5 + 32 = 190.5 KiB, leaving 22.25 KiB below the new guard. Thus no welcome-text
compaction is needed for the proposed capacity. These are calculations from
existing code and allocation, not a rebuilt-image/runtime result.

The superseded 200 KiB proposal exposed a real interaction: its 190 KiB guard
would have been exceeded by 512 bytes during an initial history save. A proposed
251-byte welcome text could recover that space, but it was never applied.
Keep the original welcome files at the new 224 KiB target.
Retest repeated Miner logs, state/settings saves, fragmentation, copy-on-write
interruption and protection/reclamation. Additional user files consume the
available headroom. A new 64 KiB-rounded maximum-size source does not fit beside
all shipped allocations below this guard without reclamation; preserve the full
Editor/assembler limits and truthful save-failure behavior.

The audited production values are centralized in src/shamaos/machine_spec.py,
configs/default.toml and the generated rtl/shama_boot_params.svh:
FLASH_BYTES = 224 << 10; config flash_bytes = 229376; FLASH_BANKS = 56.
Regenerate header values:
SHAMA_FLASH_BYTES = 32'h00038000
SHAMA_FLASH_BANKS = 32'h00000038
SHAMA_FS_BLOCK_COUNT = 32'h00000380
SHAMA_FS_WRITE_LIMIT_BLOCKS = 32'h00000353
SHAMA_FS_BITMAP_WORDS = 32'h0000001c
Table/data offsets remain 512 bytes / 18 blocks; firmware program sources and
executables require no capacity-driven edits. Record the owner amendment date
2026-11-13. Keep generic ceiling-division bank accounting.

Update assertions in tests/test_spec_lock.py, test_boot_params.py,
test_os_image.py, test_flashfs.py, test_layout.py and test_world_resources.py.
Update rtl/tb_kernel_ui.sv's displayed total to 00038000 and the two popup
fixtures' guard inputs to 217856 (prefer generated constants).
Retain explicit 250 KiB generic partial-bank fixtures in test_partial_memory.py
and their real MCHPRS coverage. The default now has no partial bank, so its
exact-capacity rejection regression should reject an incorrectly truncated tail.

Rebuild old/current and new images and compare all installed files, types, boot
bytes, executable lengths, bundle offsets and entry identities. At this new
target NO welcome-file difference is expected. Exercise actual Miner history
persistence from the complete shipped image, not a minimal two-file fixture.
Require generated-header equality and all filesystem/controller/kernel gates.

Arithmetic totals: RAM24 + cache2 + VRAM4 + flash56 = 86 full banks,
344 KiB / 2,818,048 bits, 171,273,472 unchanged storage-cell placements before
local controls. Expected flash placement uses two horizontal columns rather than
three. Exact local wiring, chunk/region and complete regular-component totals
must be regenerated; do not reuse the 250 KiB totals or claim a full-world size.

## Current measured statistics (still 250 KiB production)

RAM: Files normal 71,448/98,304 = 72.68%; Editor assembly peak 87,208 = 88.71%
before optional heap. With all optional heap filled, Files reaches 91,928 =
93.51%, which is an allocation ceiling, not normal occupancy.
Cache: Miner 6,904/8,192 = 84.28%.
VRAM: every app shares 15,680/16,384 = 95.70%.
Flash: 162,304/256,000 = 63.40%; 91.5 KiB free and 79 KiB before the guard.

Occupied X/Z chunks: cache 8,111, RAM 7,583, VRAM 7,748; their shared union
is 8,355 (do not sum those three rows). Flash 23,035, screen 5,084,
keyboard/controller backend 33, debug 9, clock 17. Regular-component union
36,533; envelope 4,338×6,850 blocks = 272×429 = 116,688 chunks.
Regular placements 235,478,992. Logic and global connecting routes are additional
and unresolved. All 79 input actuators fit a walkable 16×16 activation area.

## Upright screen and console recovery

Source is saved under experiments/vertical-display. Its README and SHA256.json
distinguish recovered passing sources from newly prepared, unvalidated changes.
Copy to /tmp/shama-vertical-display for the existing probe scripts, or carefully
adapt paths without changing electrical logic.

The exact 4×2 prototype source SHA-256 is
1c2c9e73bd49931ac2d3786b13f98bc4cd6b09062502408ef292f4f2de742c4b.
Its complete 320×180 panel passed 73 steps / 75,776 redstone ticks, checking all
57,600 lamps after each step. It uses 185 occupied chunks and 2,600,523 unique
blocks. An 8×180 saved/reopened fixture also passed. The operator view was not
accepted.

The tighter 3×2 candidate has SHA-256
d725aa024478f382828ab079db843bba7adccc22c2a4c7a2b696ce8af026bfa0.
Its geometry is 108 chunks / 2,371,162 blocks. 4×4, 5×5, 8×180 and 320×4
fixtures passed; complete dense-runner attempts were killed with exit 137.
Do NOT count 108 chunks as a production result.

Both upright panels need logical odd-row polarity correction for height 180.
Apply one XOR to the serial incoming pixel before buffering, not 320 parallel
output XORs. Logical row zero must remain the top row. The top is surface+365:
classic grass Y=-61 fits, high-grass Y=63 does not. Add a safe fallback or an
explicit incompatible-layout rejection. The 3:2 physical pixel pitch produces a
roughly 2.67:1 physical face for the 16:9 logical image; verify practical viewing.

Existing keyboard pulse circuits obstruct the upright screen. The recovered
console correction keeps all 79 actuator positions, moves eight column pulse
circuits 96 blocks east, detours the measured Shift-return collision and adds
two floor rows. It is newly prepared and UNVALIDATED.
Require collision checks, all 79 actual input traces, preserved pulses and
at least 80 RT row/column overlap, saved/reopened checks, intact terrain,
connected two-block walking clearance, all 57,600 sightlines and rendered view.
The sightline checker must derive bounds from the relocated circuit.

## Sparse actual-engine runner: planned, not recovered or verified

The earlier /tmp/shama-sparse-runtime-prototype source did not survive as
recoverable Git objects. Cargo exited 101 before the outage; its compiler error
was never retrieved. Only a Python fluid-tick rejection case passed.
Do not register it as a complete-world acceptance validator.

Resume with a small adapter of tests/mchprs_runtime/src/main.rs:
sparse chunks; streaming input blocks; compact positions retained for exact
legacy initialization order; bounded step/output checking. Legacy inputs drive
one block SOUTH of each terminal; false removes the driver. Preserve void
background for legacy panels and every one of the 57,600 output assertions.
The frozen full case uses 1,024 initial RT plus 73×1,024 RT.

Scheduling must remove one due event immediately before invoking its handler,
leaving other same-time events pending. Stable order is due time, priority,
insertion order. Do not drain an entire due batch. Explicitly reject delay zero
until modeled. Legacy fixtures use redstone ticks. Native Anvil block_ticks.t
uses GAME ticks, so forwarding it as RT is wrong: even values require t/2,
and odd phases need native game-tick scheduling or explicit rejection.
Keep native saved-world resume separate until parity is proven.

Required next tests: exact existing-harness parity, far-apart sparse circuits,
saved comparator/button/tick resume with correct units, very long idle jumps,
bounded trace/scope rejection, unsupported fluid ticks, then the full panel.

## Newly found analytical preflight defect

hardware/controls.py::control_route_access supplies east-facing terminals and
hardware/interfaces.py::system_routing_interfaces passes them through.
resource_accounting.py::_minimum_escape_high_z currently rejects east while
the actual emitter accepts it. It also needs consistent wall-north support.
Thus the production global preflight can fail with an access-direction error
instead of reporting its intended route/disk lower bound.

Fix against the literal emitter and add tests in tests/test_resource_accounting.py:
the existing final test covers only wall-south and west. Exercise east,
wall-north, the actual production console access map and emitted geometry.
Do not substitute an optimistic estimate.

## Release follow-ups preserved on this branch

The release guide now explains all split-world dependencies and Windows/Mac/Linux
extraction commands, short single-player Litematica setup/pasting, and its
source-template status until tested settings are appended. All five artifact
upload steps use 14 days. Existing uploads retain their original expiry.
The gate, permissions and asset-preservation behavior were not relaxed.

The actual full-world acceptance registry remains empty and publication blocked.
No verified complete world ZIP or schematic has been published. Keep that
boundary until full routing, timing, readable operation and actual boot pass.

The two older docs now describe exact full/half banks and direct bounds-checked
addressing, plus measured regular geometry versus analytical routed-logic bounds.
Their capacity text still matches the 250 KiB code and needs the later 224 KiB
amendment, rather than falsely stating that the code already changed.
Other audited doc fixes still pending: VALIDATION.md focused test command should
include test_hardware_numeric_literals.py; ISA.md's LDI r4,0x20000 example exceeds
96 KiB RAM and should use 0x3000; symbol hashing excludes a trailing label colon.

## Execution recovery and safe continuation

Original workspace:
 /workspace/scratch/fb0eaf1c89fe/ShamaOS-Redstone-PC
It became unavailable around 2026-11-12 23:53 UTC. No hard reset or destructive
cleanup was done. The local checkout may still have the already-committed
installer edits; inspect before syncing. An isolated worktree from this branch
can avoid overwriting the original /tmp prototypes if that environment returns.

Old usable tools were under:
 /workspace/scratch/fb0eaf1c89fe/validation-tools/bin
 /workspace/scratch/fb0eaf1c89fe/validation-tools/artifact-1/target/release/shama-redstone-check
Rust toolchain and public offline Cargo caches are also in that directory.

A callable Composio remote Bash fallback was found (Python 3.13.18, x86_64,
glibc 2.41; numpy/pytest/Pillow already installed). Public git clone timed out,
but signed artifact downloads can be read and nbtlib 2.0.4 installed successfully
under /mnt/files/shamaos-recovery/python. A first background SDK download failed
opening its target file with EIO; no compiler was restored there. Prefer /tmp
for active work and verify subprocess/file lifetime before long background jobs.
Do not claim the fallback is ready for actual-engine work yet.

Public toolchain artifact:
repo Eabusham2/ShamaOS-Redstone-PC, run43329680447, artifact13995011442.
It contains Rust1.100.0, compiler libraries and public Cargo registry/git caches,
not credentials. Re-request a fresh download URL when needed; none is committed.
The tool-export branch is automation/rust-validation-tools at1ae0585086960c9abaaa47c792482022ce6cadfc.

## Next work order

1. Restore execution, inspect main's CI, and reproduce the exact checkpoint.
2. Apply 224 KiB without changing welcome files; validate Miner COW and all
   capacity/protection behavior; regenerate usage and per-component chunks.
3. Correct the east/wall-north preflight bug with literal-geometry regression.
4. Finish sparse harness parity, full compact panel and revised console/view
   acceptance, then integrate and regenerate reports.
5. Reassign an agent to complete-partition bounded hierarchical routing.
   The current emitter uses a unique global Z track per net; gate reductions
   alone do not solve that scaling. The local experimental router needs horizontal
   tiles and bounded boundary routing, preserving every cell/bit/clock leaf.
6. Run full exact-commit CI and complete-world acceptance before publishing.

No zero-bug or complete-Minecraft-run claim is justified yet.
