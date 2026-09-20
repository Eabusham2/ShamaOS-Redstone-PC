# Roadmap

The roadmap is an implementation order, **not** a reduced feature list. The final target remains the canonical specification.

## Phase 0 — Specification lock

- canonical requirements.
- reference notes.
- memory/display targets.
- ISA/GPU/syscall contracts.
- world-format target.

## Phase 1 — Executable reference model

- assembler.
- ISA table.
- CPU simulator.
- SHA unit/reference vectors.
- GPU framebuffer model.
- ShamaFS.
- OS image builder.
- miner reference program.

## Phase 2 — World writer and primitive library

- Anvil region writer.
- block states/orientations.
- deterministic placement.
- gates/latches/registers/decoders/muxes/adders.
- planner + collision detection.
- manifest/readback.

## Phase 3 — CPU

- register file.
- ALU.
- PC/fetch/decode.
- branches.
- stack.
- load/store.
- cache interface.
- all core ISA instructions.

## Phase 4 — SHA CPU execution unit

- primitive SHA functions.
- message scheduler.
- compression round.
- full SHA256 path.
- DSHA256.
- HASHCMP.
- nonce helper.
- known-answer hardware fixtures.

## Phase 5 — Memory/flash

- cache.
- banked main RAM.
- flash storage controller.
- filesystem physical mapping.
- resource counters.

## Phase 6 — GPU/display/input

- GPU command decode.
- VRAM.
- font.
- raster primitives.
- tiled 320×180 lamp display.
- dirty tiles/double buffer.
- keyboard/controller.

## Phase 7 — ShamaOS

- boot.
- desktop.
- syscall layer.
- Editor.
- File Explorer.
- System Monitor.
- Bitcoin Miner.
- Terminal/Calculator/Paint/Settings baseline.

## Phase 8 — Full integration

- one-shot generator.
- OS flash image preloading.
- boot from generated world.
- app creation/edit/run in-world.
- miner/history.
- shutdown.
- performance tuning.

## Phase 9 — Release validation

- complete test suite.
- world readback.
- target Minecraft smoke test.
- MCHPRS acceleration validation.
- documentation/tutorial refresh.

## No-cut-corners rule

If a phase exposes an implementation problem, acceptable responses are:

- change physical layout.
- change timing.
- change banking.
- change resolution within the agreed “smaller but legible” requirement.
- improve generator architecture.

Not acceptable without explicit owner approval:

- replace CPU hashing with external Python.
- remove the GPU.
- remove filesystem/editor.
- fake RAM/cache/flash meters.
- silently shrink required software-visible RAM.
- replace real redstone computation with command blocks.
