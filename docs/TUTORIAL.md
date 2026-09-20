# End-to-End Tutorial

This tutorial covers the repository as it exists at version 0.1.0: full target specification plus executable reference/tooling foundation. Read `IMPLEMENTATION_STATUS.md` before assuming every physical redstone subsystem is already generated.

## 1. Clone

```bash
git clone https://github.com/Eabusham2/ShamaOS-Redstone-PC.git
cd ShamaOS-Redstone-PC
```

## 2. Create Python environment

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

For Java-world writing support:

```bash
pip install -e ".[dev,world]"
```

## 3. Run validation

```bash
pytest
```

Tests cover:

- SHA-256 reference vectors.
- double-SHA-256.
- assembler labels/branches/SHA instructions.
- CPU reference execution.
- ShamaFS serialization and mutation.
- GPU framebuffer behavior.
- machine floorplan collision checks.

## 4. Inspect the canonical spec

Read, in this order:

1. `docs/INITIAL_SPEC_AND_TRANSCRIPTS.md`
2. `docs/IMPLEMENTATION_STATUS.md`
3. `docs/ARCHITECTURE.md`
4. `docs/CPU.md`
5. `docs/ISA.md`
6. `docs/GPU.md`
7. `docs/MEMORY.md`
8. `docs/OS.md`
9. `docs/FILESYSTEM.md`
10. `docs/HARDWARE_GENERATION.md`

## 5. Assemble a program

```bash
shamaos asm examples/bitcoin_miner.asm \
  -o build/miner.bin \
  --listing build/miner.lst
```

The listing shows actual emitted 32-bit machine words and source mapping.

## 6. Build the preloaded flash image

```bash
shamaos flash-image -o build/shamaos-flash.img
```

The default image is 1 MiB and contains the ShamaOS boot/kernel/app manifest payloads plus welcome/miner state files.

The physical OS compiler will progressively replace manifest placeholders with assembled machine images while keeping the filesystem ABI stable.

## 7. Plan the machine

```bash
shamaos plan \
  --config configs/default.toml \
  --manifest build/plan.json
```

Inspect `build/plan.json` for:

- CPU/GPU/cache/RAM/flash/input/display districts.
- bounding boxes.
- logical capacities.
- display geometry.
- placement counts.
- config hash.

The planner rejects overlapping component boxes.

## 8. Prepare a disposable Minecraft world

Create a Java 1.20.4 superflat world and close Minecraft.

The repository intentionally targets a disposable world; do not point the writer at a world you care about until physical generation reaches release status.

Example path on macOS:

```text
~/Library/Application Support/minecraft/saves/ShamaOS
```

## 9. Write currently release-complete physical placements

```bash
shamaos generate \
  --world "/path/to/saves/ShamaOS" \
  --config configs/default.toml \
  --manifest build/generated-manifest.json
```

The generator prints the current `physical_generation_status`. It does **not** pretend an unimplemented CPU/RAM/GPU circuit is complete.

As physical component backends are added, the same command grows into the one-shot full-world generator without changing the top-level workflow.

## 10. Minecraft/MCHPRS

Vanilla Java compatibility is the correctness baseline. Large computational redstone may be impractically slow at normal tick rate.

MCHPRS or another compatible high-performance redstone environment may be used for execution speed, but it must not change architectural results.

## 11. Adding a Shama Assembly program

Create a source file:

```asm
.start
    LDI r1 10
.loop
    DEC r1
    BR.NE .loop
    HLT
```

Assemble:

```bash
shamaos asm my_program.asm -o build/my_program.bin
```

Eventually the in-world Editor performs this workflow from inside ShamaOS and saves source/executable files to ShamaFS.

## 12. Bitcoin miner architecture

The miner does not ask Python to calculate hashes while Minecraft pretends.

Runtime path:

```text
ShamaOS Miner App
    -> CPU DSHA256 instruction
    -> native CPU SHA execution unit
    -> HASHCMP
    -> VALID flag
    -> INCNONCE / loop
    -> OS log/display
```

Python's SHA implementation exists as a **test oracle/reference model** used while designing and validating the redstone SHA hardware.

## 13. GUI behavior to preserve

When implementing the physical OS:

- Editor supports New Text File and New Program.
- File Explorer opens text and programs.
- Edit from File Explorer closes Explorer and enters Editor.
- Delete asks for confirmation.
- Bitcoin Miner has Start/Stop and run-history navigation.
- app Exit frees app-owned RAM/cache state, not the resident OS.
- Desktop/System Monitor use real RAM/cache/flash counters.
- universal controller buttons include Exit, Editor and File Explorer.

These are requirements, not optional examples.

## 14. Making generator changes

Every new physical component should:

1. expose a logical contract/reference behavior.
2. get unit/reference tests.
3. define a bounding box/ports.
4. emit deterministic placements.
5. add physical-plan validation.
6. add world readback/in-game validation when appropriate.
7. update `docs/IMPLEMENTATION_STATUS.md`.

Do not bypass a difficult hardware block with runtime commands or external computation without explicit project-owner approval.
