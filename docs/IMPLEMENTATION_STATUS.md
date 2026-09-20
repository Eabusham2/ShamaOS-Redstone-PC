# Implementation Status

This repository was initialized with the **full agreed target specification** plus executable reference/tooling foundations.

## Reference/tooling present

- canonical feature/transcript-derived specification.
- CPU ISA table.
- assembler.
- SHA-256 and double-SHA-256 reference implementation.
- CPU reference simulator foundation.
- GPU framebuffer/command reference.
- ShamaFS **4 MiB** flash-image reference.
- preloaded OS flash-image builder.
- coarse full-machine floorplanner.
- redstone placement primitives.
- optional Amulet world writer adapter.
- CLI.
- tests/examples.

## Physical generator status

The repository does **not** falsely claim that the complete physical CPU, 1 MiB memory hierarchy, SHA datapath, GPU tile drivers, keyboard, flash controller and OS redstone circuits are already emitted by the first foundation commit.

`shamaos generate` writes only physical component generators marked implemented and emits `physical_generation_status` in the manifest.

The canonical specification remains mandatory. Physical completion proceeds subsystem by subsystem with tests; unimplemented circuitry is never represented as complete by decorative blocks.

This file exists specifically to prevent “documentation says full PC, generator places a shell” from being mistaken for project completion.
