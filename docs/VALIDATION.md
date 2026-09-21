# Validation

## Fast CI

Python 3.11/3.12 tests validate ISA/assembler/reference models, the 4 MiB ShamaFS image, locked capacities, routing/floorplan and world-writer behavior.

A dedicated Amulet job verifies real block-state conversion for repeaters, comparators, lamps and redstone wire.

RTL validation covers:

- SHA known-answer vectors;
- CPU;
- external-VRAM GPU;
- display bridge;
- ring memory controller including held-valid DMA;
- input encoder;
- boot/app lifecycle;
- RAM allocator/free/app-release;
- hardware-universal app switching;
- ShamaFS hardware including miner persistence;
- in-world assembler;
- GUI kernel;
- integrated SoC compile.

A small Yosys fixture is also technology-mapped and physically routed as a synthesis smoke test.

## Full physical synthesis

Release-candidate commits trigger `full-physical-synthesis`. It synthesizes the complete current `shama_soc`, builds the physical redstone netlist and verifies physical external contracts, including:

- cache banks 4
- RAM banks 256
- flash banks 1,024
- VRAM banks 8
- display 320×180
- required port widths
- nonzero cell/net/route counts

## Per-world validation

After generating a concrete world:

1. verify generation/manifest;
2. open in Minecraft/MCHPRS;
3. smoke power/reset/input/display;
4. boot ShamaOS;
5. exercise RAM/flash;
6. run an Editor-assembled program;
7. run the Bitcoin Miner and observe nonce/hash/target changes.

The repository can automate the build/file-format side; launching Minecraft requires the actual destination world.
