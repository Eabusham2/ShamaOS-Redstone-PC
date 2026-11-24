# Workspace recovery checkpoint — November 24, 2026

## Durable production source

Main checkpoint `f747dba089f0b750ab07d7f2c70a7992d7e04747` has tree
`e2665780d30d88bec9c5435df144c8cdb42280c1`. It retains the reviewed native
Editor lifecycle runner, input timing experiments, private startup-route
measurements and exact-cycle counter experiment. None of those experiments
silently replaces production RTL.

The machine contract remains 96 KiB RAM, 4 KiB physical cache with an 8 KiB
executable window, 16 KiB VRAM and 200 KiB flash. The installed image allocates
143.25 KiB, or 71.625% of flash. All nine apps, editable sources, 256 Miner
history records, the full 320×180 display and physical controls remain required.

## Verification scopes

Root independently passed 81 focused native-lifecycle/console/title/flash/release
tests and eight input-worker/retiming tests, with no skips. The nominal-routing
archive verifier and root's complete literal comparison checked all 112,143
input stream records: its combined adjustment changes only four existing
repeater delay settings. The original short-POWER failure is retained.
These proofs do not constitute a full-machine boot.

The standard CI on `f7f2755`, run
[45352217215](https://github.com/Eabusham2/ShamaOS-Redstone-PC/actions/runs/45352217215),
had 19 successful jobs and its behavioral job still running at the recovery
check. Its twelve mapped partitions and reconnection succeeded. The separate
[complete-cache run 45351382058](https://github.com/Eabusham2/ShamaOS-Redstone-PC/actions/runs/45351382058)
is bound to production `6785ca9`; its combined generation/readback/runtime
step was still running. Check both live runs before assigning a final result.

## Recovered development work

The original coding environment became unavailable during ongoing tests. A
separate execution environment was restored from the exact published source.
The archives below preserve recovery inputs and distinguish retained bytes,
freshly repeated results and measurements whose raw files were not recovered.

| Archive | What it preserves | Remaining proof |
| --- | --- | --- |
| [Input protocol](../experiments/input_receiver_timing/recovery/input-protocol-recovery.tar.gz) | Four RTL files matched their pre-interruption SHA-256 values. Separate Git bundles reconstruct the receiver, CPU/service observation interface and background reader. Fresh source and actual Yosys/ABC comparisons pass for both versions; the actual CPU passed ten MMIO loads, ten coincident service observations and two exact ACKs. | Passive-store settling, all data lanes, physical capture/clock distribution and complete-system execution. The prior long Editor/assemble/run result is historical; the reconstructed version needs its own full flow. |
| [Power startup](../experiments/soc_startup_timing/level_power/recovery.zip) | Exact three-file level-request source patch, original private C+7 selection, preparation and persistence drivers. The recovered RTL matches its original SHA-256. | Source-only level startup still has a 1 RT setup failure at tick 15,333. The private C+7 run and actual shutdown/snapshot/reopen must finish before promotion. |
| [Full-width passive input store](../experiments/input_storage_bus/recovery/source-recovery-v1.tgz) | Exact prior +33 and +41 phase producers, a detailed historical manifest, replay code and tests. | The +33 run retained all 512 bits but found 13 one-tick hold violations at row 13 / bit 30. +41 had only partial completed traces before interruption; repeat it fully. |

The input Git bundles require published `f747dba`. The source-level startup
patch requires research commit `370c24df4db7de1d9d34dec6272f40bb90e49a34`,
which is reconstructible from the already published startup evidence archive.
Each recovery package includes its own reconstruction steps and integrity
checks. A recovered source hash does not recover a missing electrical result.

The input protocol retains the logical sixteen-record FIFO, controller
snapshots, universal-action priority, exact ACK identity and reset generation.
Its tested background worker consumes committed physical-model rows without
stealing client observation credit. The data/clock propagation of the actual
five-store component remains to be measured. Fast capture, row qualifiers
and stores must be physically local; an unmeasured distant strobe is not an
accepted implementation.

## Reusable pinned test tools

GitHub toolchain run
[45337191954](https://github.com/Eabusham2/ShamaOS-Redstone-PC/actions/runs/45337191954)
on source `044feadda8aeef19c4c36ce941e0f360569364cc` retains:

- Artifact `14894364026`, HDL ZIP SHA-256
  `4801002d8359536e0d9b97a4606152be658d4b114b95f2ecf4e110d70bab203b`:
  Yosys 0.33 and Icarus Verilog 12.0.
- Artifact `14893808979`, runtime ZIP SHA-256
  `861df9ec98055b72f8a03de27340e225300e357415a3036ddf896c15a5bc7375`.
- The terrain engine itself is SHA-256
  `c8032089622501cb57f9edbb0ea7e3be24e3a23fd0b2b80653b0ed4f07045610`
  for pinned MCHPRS `f9e10d2572116bf5dcb9a36a305f28e40641a61a`.

The temporary recovered checkout is `/home/user/shamaos-rescue`; tools are
initialized with `. /home/user/shama-test-tools/env.sh`. The small fallback
environment has about 1 GiB RAM, so run substantial native jobs serially.
Record every interrupted process as incomplete, even when earlier assertions
passed. Keep trace and snapshot stages separate when needed.

## Continuation order

1. Collect the complete-cache workflow's actual world, readback, clock,
   transaction and persistence results. Diagnose the first failing stage.
2. Finish the level-request power fixture and save/reopen, including every
   physical D/C setup and hold measurement and all short-lead cases.
3. Finish all-lane passive input timing and the connected physical
   capture/row-selector/storage fixture, then bind the exact tested geometry
   into the full generator and its accepted observation protocol.
4. Complete placement and physical timing for all production partitions,
   connect their memory and I/O, and demonstrate a workable ticking/client
   configuration.
5. Run the native Editor lifecycle on that complete saved machine, then all
   remaining apps, mining and resource-fault flows. Only then enable a real
   full-world validator and publish the world/schematic/installer release.

The full-world acceptance registry remains empty. There is no accepted
complete Minecraft computer or final whole-machine chunk total yet.
