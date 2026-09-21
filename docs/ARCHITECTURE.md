# ShamaOS Architecture

ShamaOS is a 32-bit redstone computer generated as one synthesized SoC plus large regular memory/display/input fabrics.

```text
keyboard/controller --raw clock--> event latch
                              |
                              v
                        ShamaOS services
                              |
      +-----------------------+-----------------------+
      |                       |                       |
      v                       v                       v
  32-bit CPU              Shama GPU              ShamaFS
  + native SHA               |                       |
      |                       v                       v
      |                  32 KiB VRAM             4 MiB flash
      |
      +---- 16 KiB executable cache
      +---- 1 MiB main RAM
                              |
                              v
                    320×180 lamp panel
```

## CPU

32-bit datapath, 16 GPRs, integer ALU, multiply/divide/modulo, shifts/rotates, flags, branches, calls/returns, load/store, syscalls, stacks and counters. SHA primitives plus SHA256/DSHA256/HASHCMP/INCNONCE are native CPU operations.

## Timing

A fast raw clock feeds input/event capture. Core divider bit 17 is the default for CPU/GPU/services/filesystem/assembler/memory controllers. On reset, core clock follows raw clock.

## Memory

Literal default capacities:

- cache 16 KiB / 4 banks;
- RAM 1 MiB / 256 banks;
- flash 4 MiB / 1,024 banks;
- VRAM 32 KiB / 8 banks.

Each bank is 4 KiB (1,024 × 32-bit words).

## GPU/display

Large GPU state is external VRAM. The 320×180 panel is a horizontal X/Z latch matrix viewed from above and receives 320 data bits plus 180 one-hot row selects.

## OS/storage

ShamaFS is hardware. The in-world assembler is a hardware two-pass assembler. Boot loads resident kernel RAM plus one foreground app mirrored into the 16 KiB fast cache.

## Physical synthesis

Regular memories/display/input are custom generators. Stateful control logic is synthesized from SystemVerilog with Yosys into redstone cells, placed in 2-D and routed on isolated planes.

Python/Yosys are build tools only; generated runtime behavior is performed by the redstone machine.
