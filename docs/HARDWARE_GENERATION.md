# Hardware and World Generation

## One-shot pipeline

```text
configs/default.toml
        |
        +--> firmware assembler --> 4 MiB ShamaFS image
        |
        +--> custom regular fabrics
        |      cache / RAM / flash / VRAM / display / input
        |
        +--> SystemVerilog RTL --> Yosys --> redstone technology cells
        |
        +--> 2-D placement + crossing-safe routing
        |
        +--> physical memory backbones + system interconnect + clock
        |
        v
     Amulet Java chunks
```

Target format: Minecraft Java 1.20.4, normally a disposable superflat Overworld.

## Regular physical fabrics

- cache: 4 × 4 KiB banks;
- RAM: 256 × 4 KiB banks;
- flash: 1,024 × 4 KiB banks;
- VRAM: 8 × 4 KiB banks;
- 320×180 lamp display;
- keyboard/controller.

A default memory bank is 1,024 × 32-bit words = 4 KiB. Flash is initialized directly from the assembled 4 MiB ShamaFS image.

## Synthesized logic

CPU, native SHA unit, GPU control/raster logic, OS services, ShamaFS controller and in-world assembler are synthesized with Yosys into BUF/NOT/NAND/NOR/DFF redstone cells. Cells are placed on a 2-D grid rather than a world-border-sized line.

## Routing

Internal SoC nets and system interconnect use separate routing planes. The router:

- isolates logical nets on distinct tracks;
- uses branch/trunk planes for crossings;
- emits explicit redstone-wire states;
- regenerates long runs with repeaters;
- uses flat repeater plateaus on rising/falling stair routes.

Memory fabrics use local bank/row backbones.

## Display

The screen remains **320×180**. It is physically horizontal in X/Z and viewed from above so all repeater/dust buses are valid vanilla redstone. Each pixel has a locked-repeater latch and lamp.

## Clocking

The input/event latch receives the raw comparator clock. Core logic uses divider bit 17 by default to allow long literal memory routes to settle. During reset, core clock follows the raw clock so state receives reset edges before power-off.

## World writer

Amulet Core is used to:

- verify an existing Java world folder;
- create missing far-away chunks;
- convert exact Java block states;
- cache palette IDs;
- write palette IDs directly;
- periodically save/unload chunks;
- reject invalid build-height/world-border placements.

No giant `/setblock` command script is generated.

## Command

```bash
shamaos generate \
  --world "/path/to/.minecraft/saves/ShamaOS" \
  --config configs/default.toml \
  --manifest build/generated-manifest.json
```

Minecraft should be closed while writing.
