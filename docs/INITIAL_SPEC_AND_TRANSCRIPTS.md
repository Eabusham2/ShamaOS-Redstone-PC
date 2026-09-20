# Initial Specification and Transcript-Derived Requirements

**Status:** canonical initial project agreement  
**Project:** ShamaOS Redstone PC  
**Repository:** `Eabusham2/ShamaOS-Redstone-PC`

This document freezes the initial intent of the project so later implementation work cannot quietly simplify it. It separates facts learned from the supplied videos/transcripts from requirements that were agreed for ShamaOS.

## 1. Source material ingested

The supplied material consisted of four standalone YouTube videos plus all eleven videos in MattBatWings' *Let's Make a Redstone Computer!* playlist.

Standalone video IDs:

1. `jTZaUz8bYW8` — computational redstone walkthrough/discussion with MattBatWings.
2. `VaeI9YgE1o8` — Sammyuri, *I built ChatGPT with Minecraft redstone!*
3. `-BP7DhHTU-I` — Sammyuri, *I made Minecraft in Minecraft with redstone!*
4. `FDiapbD0Xfg` — Sammyuri, *CHUNGUS 2 - A very powerful 1Hz Minecraft CPU*.

Playlist `PL5LiOvrbVo8nPTtdXAdSmDWzu85zzdgRT`, eleven episodes:

1. Introduction to Computing
2. The Arithmetic Logic Unit
3. The Register File
4. Machine Code & Assembly Language
5. Instruction Memory
6. The Program Counter
7. Jumping, Branching, and Flags
8. The Call Stack
9. Data Memory
10. Input and Output
11. Assembly Programming

All eleven playlist entries exposed English auto-generated transcript tracks and were ingested. The CraftGPT, Minecraft-in-Minecraft and CHUNGUS 2 uploads are primarily visual/music showcase videos; their auto-caption tracks contain little useful narration, so project facts from those videos come from their published descriptions rather than invented speech.

This repository stores detailed *derived notes*, not verbatim full third-party transcripts.

## 2. Transcript-derived architecture facts

### MattBatWings teaching computer

The series builds a real programmable redstone computer and explains:

- ALU design using redstone adders and bitwise logic.
- 8-bit data paths in the teaching machine.
- a dual-read register file with 16 addressable registers and a zero register.
- machine code and a custom assembly language.
- fixed-size instructions and a control ROM.
- instruction memory generated from assembly through Python tooling/schematics.
- a program counter and clock.
- single-cycle execution in the teaching implementation, with a discussion of pipelines/multicycle processors.
- flags, jumps and conditional branches.
- a call stack for nested subroutine calls.
- data memory.
- memory-mapped I/O.
- a controller, RNG, number display, character display and buffered pixel screen.
- programs such as Hello World, graphics demos, paint and larger games.
- MCHPRS as a practical way to accelerate large redstone computers.

The final teaching computer uses a compact 16-opcode ISA family. The series explicitly includes the concepts/instructions NOP, HLT, ADD, SUB, bitwise operations, right shift, LDI, ADI, JMP, BRH, CAL/CALL, RET, LOD and STR, with pseudo-instructions such as INC, DEC and CMP and assembler support for labels/definitions/comments. ShamaOS preserves these concepts but intentionally uses a larger ISA instead of being constrained to sixteen opcodes.

The teaching computer's data memory is 256 bytes. Its instruction memory stores 1024 fixed-size instructions. The register file contains 16 8-bit registers, with register zero acting as a zero register.

### CHUNGUS 2

Published CPU specifications from the supplied video description:

- 8-bit data.
- 16-bit fixed-size instructions.
- 1 Hz clock.
- four-stage pipeline: fetch, decode, execute, writeback.
- 64-byte automatic 8-way associative data cache.
- 256 bytes RAM.
- up to 256 addressable I/O ports.
- seven general-purpose registers.
- more than forty ALU functions, including barrel shifter, multiplier, divider and square-root hardware.
- 32 × 128-byte program pages = 4 KiB program storage.
- 32×32 buffered pixel screen plus text/number displays and controller hardware.
- MCHPRS used to accelerate programs.

### CraftGPT

Published project facts from the supplied description include:

- 5,087,280 model parameters.
- embedding dimension 240.
- vocabulary of 1,920 tokens.
- six model layers.
- 64-token context.
- mostly 8-bit quantized weights, with some higher precision weight storage.
- model weights split across many ROM sections.
- physical build volume roughly 1020×260×1656 blocks.
- MCHPRS used at extreme acceleration.

The important lesson for ShamaOS is that automated generation of extremely repetitive computational redstone is legitimate: the external generator constructs the machine, while the generated redstone remains the computational artifact.

### Minecraft in Minecraft

The supplied description states a fully redstone implementation with:

- an 8×8×8 3D-rendered world.
- sixteen block types.
- thirty-two item types.
- mining, crafting, smelting, building, chests and random ticks.
- no command blocks or datapacks as the game-computation implementation.
- MCHPRS acceleration because vanilla execution would be extremely slow.

## 3. ShamaOS agreed hardware requirements

### 3.1 CPU

ShamaOS shall contain a real, general-purpose redstone CPU rather than a purpose-specific animation.

Target architecture:

- 32-bit integer datapath, chosen because SHA-256 naturally operates on 32-bit words.
- sixteen general-purpose registers unless layout testing demonstrates a materially better count without reducing software capability.
- program counter.
- status/condition flags.
- ALU.
- multiply/divide/modulo capability.
- shift and rotate unit.
- load/store unit.
- branch/jump/call/return logic.
- stack support.
- instruction decode/control.
- cache interface.
- system-call/trap mechanism.
- debug/performance counters.
- integrated SHA execution unit.

The CPU ISA shall include the useful operations from the video-derived designs **and more**, so the machine has general purpose beyond Bitcoin mining.

### 3.2 SHA unit is part of the CPU

The SHA accelerator is not an external fake miner. It is a native CPU execution unit selected by CPU opcodes.

It must support enough hardware primitives to implement/accelerate SHA-256:

- 32-bit modular addition.
- rotate right.
- logical right shift.
- XOR/AND/NOT.
- Choice `Ch`.
- Majority `Maj`.
- SHA-256 big sigma 0/1.
- SHA-256 small sigma 0/1.
- message schedule support.
- compression-round support.
- complete block SHA-256.
- double-SHA-256.
- hash/target comparison.
- nonce increment/mining helpers.

Software must be able to use lower-level SHA instructions for inspection/education as well as higher-level accelerated operations.

### 3.3 Bitcoin miner

The preinstalled Bitcoin Miner app must execute the Bitcoin-style double-hash structure:

`SHA256(SHA256(80-byte block header))`

The miner must support:

- Start.
- Stop.
- nonce iteration.
- target comparison.
- current/last hash.
- attempt counter.
- run/generation counter.
- viewing previous/saved generation/run results.
- result/history persistence to flash.
- live status display.

The project makes no claim that a Minecraft redstone miner is competitive with real ASIC mining.

### 3.4 RAM/cache/VRAM

Agreed target:

- **1 MiB logical main RAM**.
- **16 KiB cache / fast scratch target**.
- separate VRAM/framebuffer allocation sufficient for the selected display and double buffering.

The final implementation may use a hierarchical/banked physical arrangement to keep the world tractable. It must never label sparse/backed/logical memory as literal one-cell-per-bit physical redstone RAM without documentation.

The reason 2 GiB was rejected as a physical requirement is scale: 2 GiB contains 17,179,869,184 bits, while the referenced computers use hundreds of bytes of RAM. Bitcoin SHA-256 itself is compute-heavy rather than RAM-heavy.

### 3.5 Flash

ShamaOS requires persistent flash-like storage for:

- OS image.
- boot image.
- applications.
- source programs.
- text documents.
- miner history/results.
- settings.
- filesystem metadata.

Default configuration targets 1 MiB flash and may be scaled.

### 3.6 GPU

ShamaOS shall have a real separate graphics processor/command engine, not merely CPU wires directly driving a lamp matrix.

The GPU shall have:

- command decoder.
- graphics registers.
- framebuffer/VRAM interface.
- text/font ROM.
- pixel read/write.
- line drawing.
- rectangle/fill.
- blit/sprite operations.
- text/character rendering.
- scrolling.
- clipping.
- buffer clear/copy/swap.
- dirty-tile or changed-region tracking.
- status/fence/wait behavior so CPU and GPU can coordinate.

The CPU submits GPU commands through memory-mapped registers and/or a command queue.

### 3.7 Display

The original 1280×720 idea was intentionally reduced because legibility matters more than raw pixel count.

Default target:

- 320×180.
- one-bit redstone-lamp pixels.
- tiled physical layout.
- local tile decoders/controllers.
- readable 5×7/6×8-style text rendering with scaling where useful.
- double buffering where practical.
- dashboard/UI optimized for status and text.

The display resolution is configurable. A smaller screen is acceptable if it is materially easier to run and all required GUI/status information remains legible.

### 3.8 Input

Physical input must include:

- keyboard for text/program entry.
- D-pad/controller navigation.
- Confirm/Open.
- Back/Cancel.
- Home.
- universal Exit.
- universal Editor.
- universal File Explorer.

Universal buttons are OS-level shortcuts.

### 3.9 Power

The machine requires a physical on/off control path.

Power-on sequence:

1. reset/known hardware state.
2. boot ROM entry.
3. initialize memory/cache/GPU/input.
4. mount flash filesystem.
5. load kernel/GUI from flash into RAM.
6. warm/populate cache naturally.
7. show desktop.

Power-off should perform a clean filesystem/app shutdown before gating/stopping the machine clock where possible.

## 4. ShamaOS agreed software requirements

### 4.1 GUI desktop

ShamaOS is a GUI OS, not only a monitor screen.

Desktop/launcher must expose preinstalled applications and live resource indicators.

At minimum the desktop/status area shows real:

- RAM used/total.
- cache used/total.
- flash used/total.

Counters must be derived from actual OS allocation/cache/filesystem accounting, not arbitrary animations.

### 4.2 Editor

Final application name: **Editor**.

Editor must support:

- New Text File.
- New Program.
- open existing source.
- edit text using keyboard.
- save.
- Save As.
- rename.
- delete.
- Run/assemble program.
- confirmation before destructive delete.

Programs are written in Shama Assembly.

Opening a program from File Explorer in Editor closes the File Explorer application/view and transfers to Editor.

### 4.3 File Explorer

File Explorer must:

- list flash files.
- distinguish programs/source/text.
- open/read `.txt` documents.
- run programs.
- open programs in Editor.
- rename.
- delete with confirmation.
- create/open via OS filesystem services.
- display file size/type and useful metadata within display limits.

### 4.4 Filesystem and system calls

The assembly environment shall expose OS system calls rather than making every app know raw flash geometry.

Required syscall families:

- file create.
- file open.
- file close.
- file read.
- file write.
- file truncate.
- file rename.
- file delete.
- directory/listing operations if directories are implemented.
- flash free/usage.
- RAM allocation/free/usage.
- cache status/flush hints.
- app exit.
- input polling.
- GUI draw/text/window helpers.
- timer/performance counters.
- miner result logging.

### 4.5 App lifecycle

When an app launches:

1. executable/source image is resolved from flash.
2. required program/data pages are loaded into RAM.
3. instructions/data naturally populate cache.
4. app receives an isolated allocation region and handles/resources.

When Exit is requested:

1. app receives an exit/close path when safe.
2. requested persistent state is saved.
3. file handles/resources close.
4. app-owned RAM is released/cleared as appropriate.
5. app cache lines are invalidated/released.
6. temporary GPU/window state is cleared.
7. OS remains resident.

“Flush RAM on exit” therefore means flush/free the **app's memory**, not destroy the kernel, filesystem state or shared display buffers.

### 4.6 Preinstalled apps

Required:

- Editor.
- File Explorer.
- Bitcoin Miner.
- System Monitor.

Useful baseline apps also targeted:

- Terminal.
- Calculator.
- Paint.
- Settings.

## 5. Assembly requirements

Shama Assembly must support:

- labels.
- definitions/constants.
- comments.
- registers.
- decimal/binary/hex immediates.
- normal arithmetic/logical instructions.
- memory operations.
- branch conditions.
- stack/subroutine operations.
- GPU/system calls.
- SHA instructions.
- pseudo-instructions/macros where they improve readability without hiding machine behavior.

Programs created inside ShamaOS must be saveable to flash and later reopened/edited/run without regenerating the Minecraft world.

## 6. World generator requirements

The world is disposable, so preservation of existing terrain is not a primary concern. Correctness and openability in Minecraft matter more.

The generator must:

- accept or create a disposable Java superflat world.
- modify the world folder to place the complete machine.
- use modular Python source rather than a single enormous script.
- be deterministic.
- reserve non-overlapping component bounding boxes.
- write a build manifest with component locations/capacities.
- validate chunk/region/NBT output after writing.
- validate machine topology at the reference-model level before mass placement.
- avoid relying on millions of slow in-game `/setblock` commands as the primary generation method.
- make Python the *builder*, not the runtime computational substitute.

## 7. Validation requirements

Before a generated world is considered valid, tests shall cover:

- every architected CPU instruction.
- assembler encode/decode behavior.
- branch/call/stack behavior.
- memory reads/writes and boundaries.
- cache accounting/coherency model.
- standard SHA-256 known-answer vectors.
- double-SHA-256 known-answer vectors.
- miner nonce/target comparison.
- GPU primitive reference rendering.
- text/font rendering.
- filesystem create/read/write/rename/delete.
- deletion confirmation behavior at OS/UI model level.
- boot image creation.
- app load/exit/free behavior.
- resource usage counters.
- region/chunk structural validation.
- deterministic generation manifests.

## 8. Non-goals / honesty rules

- No claim that redstone mining is economically useful.
- No fake “2 GiB physical redstone RAM” label for sparse/logical storage.
- No command block secretly computing hashes while lamps pretend the CPU did it.
- No external Python process acting as the running OS after world generation.
- No silent removal of agreed features because a milestone implementation is smaller.

## 9. Repository policy

- Public repository.
- Project title: **ShamaOS Redstone PC**.
- Repository name: **ShamaOS-Redstone-PC**.
- Primary/default branch: **main**.
- Initial semantic version: **0.1.0**.
- Documentation is part of the implementation contract.
