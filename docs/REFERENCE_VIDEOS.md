# Reference Videos and Derived Notes

This file records the supplied source material and the facts ShamaOS intentionally borrows conceptually. It does not reproduce full copyrighted transcripts.

## Standalone videos

### jTZaUz8bYW8

Computational-redstone discussion/walkthrough with MattBatWings. The transcript discusses how large computational builds decompose into smaller logic/state machines, including games and display/state logic.

### VaeI9YgE1o8 — CraftGPT

Published description facts used by this project:

- 5,087,280 parameters.
- 240-dimensional embeddings.
- 1,920-token vocabulary.
- six layers.
- 64-token context.
- mostly 8-bit quantized weights.
- enormous ROM-backed model storage.
- generated build volume around 1020×260×1656 blocks.
- MCHPRS acceleration.

Design lesson: automated world construction is appropriate for huge repeated computational structures, provided the generated redstone is what performs the computation.

### -BP7DhHTU-I — Minecraft in Minecraft

Published description facts:

- 8×8×8 3D-rendered world.
- sixteen block types.
- thirty-two items.
- mining, crafting, smelting, building, chests, random ticks.
- no command blocks/datapacks as the implementation.
- MCHPRS acceleration.

Design lesson: a redstone machine can expose a surprisingly rich interactive software-like environment.

### FDiapbD0Xfg — CHUNGUS 2

Published CPU specifications:

- 8-bit data / 16-bit instructions.
- 1 Hz.
- four-stage fetch/decode/execute/writeback pipeline.
- 64-byte 8-way associative cache.
- 256 bytes RAM.
- up to 256 I/O ports.
- seven general-purpose registers.
- 40+ ALU functions.
- hardware barrel shifter/multiplier/divider/square-root.
- 4 KiB program storage.
- 32×32 buffered pixel screen.

Design lesson: caches, pipelines, dedicated execution units and rich I/O all make sense in computational redstone.

## MattBatWings playlist

Playlist ID: `PL5LiOvrbVo8nPTtdXAdSmDWzu85zzdgRT`

### 1. osFa7nwHHz4 — Introduction to Computing

General-purpose vs single-purpose circuits, Turing-complete discussion, hardware diagram, combinational vs sequential components, custom assembly and assembler-to-machine-code workflow.

### 2. ChR7wS94WoY — The Arithmetic Logic Unit

Carry-cancel/ripple-carry ideas, addition/subtraction, bitwise logic, input inversions, carry manipulation, shifts, and integrating many ALU functions through control signals.

### 3. LUQZR8i_t-0 — The Register File

Repeater-lock memory, multi-bit registers, decoders, zero register, dual-read approaches, sixteen 8-bit registers and enable/clock behavior.

### 4. _OXBSX0fPEM — Machine Code & Assembly Language

Fixed instruction format, opcode/control ROM, register operands, machine code, assembly notation and Python assembler.

### 5. wAwMQp0KNMI — Instruction Memory

NOP, 1024-instruction memory via 10-bit addressing, assembler-to-schematic generation, Harvard-style separation, LDI and multiplexers.

### 6. 4C0-qWW9LuU — The Program Counter

Program counter, incrementing, automatic clock execution, HLT, single-cycle timing and discussion of pipelining.

### 7. tuvM7T031zI — Jumping, Branching, and Flags

ADI, INC/DEC pseudo-operations, direct jumps, zero/carry flags, conditional branching, CMP-style pseudo-instruction and Turing-complete control flow.

### 8. XVWUCgqGwHM — The Call Stack

Labels, CALL/RET, nested subroutines, return-address stack and stack overflow.

### 9. KWkGW0lS2-0 — Data Memory

Memory hierarchy discussion, 256-byte data memory, LOD/STR, pointers and signed offsets, bubble sort example.

### 10. wpYU6Zvemck — Input and Output

Port vs memory-mapped I/O, controller, RNG, number display, character buffer, 32×32 buffered screen, pixel read/write and I/O protocol design.

### 11. pCqqpbA9uvs — Assembly Programming

Assembly syntax, labels, definitions, comments, character/port symbols, simulator/debugger, MCHPRS, Hello World, bouncing ball, paint and larger program examples.

## What is copied vs extended

ShamaOS **does not claim** that the videos contained:

- a standalone programmable GPU ISA;
- a 1 MiB RAM system;
- a GUI OS/filesystem/editor;
- Bitcoin SHA-256 CPU instructions.

Those are ShamaOS extensions agreed for this project.

The video material is used as proof-of-concept and architectural inspiration for real redstone computation, memory, I/O and generated circuitry.
