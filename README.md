# ShamaOS Redstone PC

**ShamaOS Redstone PC** is a programmable Minecraft Java redstone computer and one-shot world generator. Python/Yosys build the machine; after generation, the CPU, SHA unit, GPU, memory, storage, input, filesystem and ShamaOS software execute in the generated redstone hardware rather than calling Python for runtime computation.

## Locked default machine

| Subsystem | Implemented default |
|---|---|
| CPU | 32-bit general-purpose CPU, 16 GPRs, branches/stacks/load-store/syscalls |
| SHA | Native SHA-256 execution unit inside the CPU; SHA256, DSHA256, HASHCMP, INCNONCE |
| RAM | **1 MiB physical main RAM** = 256 × 4 KiB banks |
| Cache | **16 KiB physical fast/executable cache** = 4 × 4 KiB banks |
| Flash | **4 MiB physical ShamaFS storage** = 1,024 × 4 KiB banks |
| VRAM | **32 KiB physical VRAM** = 8 × 4 KiB banks |
| Display | **320×180**, 1-bit, 57,600 redstone lamps |
| Input | 8×8 keyboard matrix + D-pad/A/B/Home/Exit/Editor/Files + power/reset |
| OS | ShamaOS boot/kernel + GUI apps stored in flash |
| Mining | Real Bitcoin-style double-SHA-256 nonce loop and target compare |

These capacities and the 320×180 resolution are canonical and are not silently reduced.

## Physical generation

`shamaos generate` writes the complete topology into a disposable Minecraft Java 1.20.4 world:

- locked-repeater cache/RAM/flash/VRAM banks;
- one-hot bank/row memory controllers and physical backbones;
- Yosys-synthesized CPU/SHA/GPU/kernel/filesystem/assembler/control logic mapped to redstone technology cells;
- crossing-safe, strength-refreshed interconnect;
- the horizontal 320×180 latched lamp panel;
- physical keyboard/controller, ON/OFF control, reset and clock;
- a preloaded 4 MiB ShamaFS image containing boot, kernel, apps, source files and persistent miner records.

The Amulet world writer creates missing far-away chunks and preserves exact repeater/comparator/wire/lamp block states.

## Timing

The input latch uses the raw physical clock. The computational core uses a heavily divided clock (default divider bit 17) because literal RAM/flash routes can be extremely long. Vanilla operation is intentionally very slow; MCHPRS can accelerate the same circuitry without changing architectural results.

## ShamaOS

ShamaOS boots from flash/cache. The resident kernel stays in RAM; exactly one foreground app is copied into RAM and the 16 KiB executable cache. App switching clears the old foreground RAM/cache state before loading the next program.

Hardware-universal **Home / Exit / Editor / File Explorer** events can seize and replace the foreground app even if it is halted or never polls input.

Preinstalled apps:

- Desktop
- Editor
- File Explorer
- Bitcoin Miner
- System Monitor
- Terminal
- Calculator
- Paint
- Settings

Editor supports New Text, New Program, keyboard editing, Save, Save As, Rename, confirmed Delete, in-world assembly, saving compiled `.BIN` files and running staged binaries. File Explorer reads text, edits source through Editor, runs binaries, renames and confirmed-deletes files. Bitcoin Miner has Start/Stop, generation reset, live nonce/attempt/hash/target fields and persistent 256-entry history.

## Quick start

```bash
git clone https://github.com/Eabusham2/ShamaOS-Redstone-PC.git
cd ShamaOS-Redstone-PC

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev,world]"

pytest
shamaos flash-image -o build/shamaos-flash.img
shamaos plan --config configs/default.toml --manifest build/plan.json
```

Create and close a **disposable Minecraft Java 1.20.4 superflat world**, then run:

```bash
shamaos generate \
  --world "/path/to/.minecraft/saves/ShamaOS" \
  --config configs/default.toml \
  --manifest build/generated-manifest.json
```

This build is enormous and can consume substantial CPU, RAM, disk and time.

## Validation

Fast CI covers Python 3.11/3.12, the real Amulet backend, SHA vectors, CPU/GPU/display/input/memory RTL, foreground RAM/cache lifecycle, ShamaFS, the in-world assembler, GUI kernel, integrated SoC compile and Yosys→redstone smoke mapping.

Release-candidate commits additionally trigger the heavyweight **full physical SoC synthesis/port-mapping gate**.

A final in-game smoke test is per generated world and requires an actual disposable save path.

## Documentation

- `docs/INITIAL_SPEC_AND_TRANSCRIPTS.md`
- `docs/ARCHITECTURE.md`
- `docs/ISA.md`
- `docs/GPU.md`
- `docs/MEMORY.md`
- `docs/OS.md`
- `docs/FILESYSTEM.md`
- `docs/HARDWARE_GENERATION.md`
- `docs/VALIDATION.md`
- `docs/IMPLEMENTATION_STATUS.md`

## License

MIT.
