# Hardware and World Generation

## Goal

Generate the full computer into a Minecraft Java world folder without relying on a gigantic hand-written script or millions of in-game `/setblock` commands.

The world is disposable. The generator can replace the reserved generated region.

## Supported baseline

Initial target world format:

- Minecraft Java Edition 1.20.4.
- DataVersion 3700.
- Overworld.
- superflat/disposable world.

The format target is explicit because raw Anvil/NBT output is version-sensitive.

## Generator layers

### 1. Reference models

Pure Python models define:

- ISA/opcodes.
- SHA behavior.
- GPU framebuffer behavior.
- flash filesystem.
- memory map.
- OS image metadata.

### 2. Redstone primitives

Reusable physical templates:

- wire/bus lanes.
- repeater delay/isolator.
- torch/comparator gates.
- latches.
- registers.
- decoders.
- muxes.
- adders.
- shift/rotate structures.
- memory cells/banks.
- lamp tile.
- button/lever/controller inputs.

### 3. Components

Generated from primitives:

- ALU.
- register file.
- control decoder.
- PC/fetch.
- branch unit.
- stack.
- cache.
- RAM banks.
- SHA execution unit.
- flash controller.
- GPU.
- VRAM/display tile controllers.
- keyboard/controller.
- boot/power logic.

### 4. Layout planner

Every component declares a bounding box and named ports. Planner:

- places components.
- reserves routing channels.
- detects overlap.
- computes bus routes.
- checks maximum configured delays/fanout rules.
- emits manifest.

### 5. World writer

World writer converts block placements into chunk section palettes and Anvil region chunks.

Rules:

- write to temporary region data first.
- verify NBT/region structure.
- atomically replace destination region file when possible.
- preserve `level.dat` and unrelated chunks unless configured to rebuild.
- emit generated-area coordinates to manifest.

## Determinism

Block placements are sorted by coordinate and component IDs are stable.

Manifest includes:

- generator version.
- config hash.
- world target version.
- block count by type.
- component bounding boxes.
- logical/physical memory capacities.
- display geometry.
- source file hashes where practical.

## World safety

Although the world is disposable, the writer still:

- checks `level.dat` exists.
- checks Java DataVersion when readable.
- rejects unsupported dimensions/world versions unless `--force-version` is explicitly used.
- writes temp files first.
- validates after write.

## Redstone correctness strategy

A physically placed build is not accepted simply because blocks exist.

Each generated component carries logical net/port metadata. The validator checks:

- every required input/output port connected.
- no accidental overlapping blocks where forbidden.
- power-source direction/orientation metadata valid.
- bus bit order consistent.
- expected repeater/comparator orientations.
- display tile address mapping.
- memory bank address mapping.

Functional behavior is first proven in software reference models and component truth-table/net tests.

## Performance modes

### Vanilla-compatible

All logic uses vanilla Java redstone blocks/mechanics and can run at normal tick rate, even if extremely slowly.

### Accelerated server

MCHPRS or compatible acceleration may be used to make enormous builds practically observable. Acceleration must not change architectural results.

## No fake compute

The generator may preinitialize:

- flash OS image.
- program ROM/boot image.
- font ROM.
- constants such as SHA K values.

It may **not** precompute future mining outputs and wire them as if the CPU found them.

## Memory physicalization

The build manifest must state:

```text
logical_ram_bytes
physical_ram_bytes
cache_bytes
flash_bytes
vram_bytes
backing_mode
```

If logical RAM exceeds literal redstone cells, the backing strategy is described explicitly.

## CLI

```bash
shamaos generate --world PATH --config configs/default.toml
shamaos validate --world PATH
shamaos plan --config configs/default.toml --manifest out/plan.json
```

`plan` allows layout validation before writing a world.
