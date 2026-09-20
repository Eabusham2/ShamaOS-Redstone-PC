# ShamaOS Redstone PC

**ShamaOS Redstone PC** is a deliberately overbuilt, fully programmable Minecraft Java redstone computer project. The goal is not a decorative computer or a command-block animation: the generated machine is intended to contain real redstone computation, memory, display logic, input hardware, persistent storage, and a small GUI operating system.

The project takes inspiration from the computational-redstone designs discussed in the reference videos listed in `docs/REFERENCE_VIDEOS.md`, especially MattBatWings' *Let's Make a Redstone Computer!* series and Sammyuri's CHUNGUS 2 / large-scale computational-redstone projects. ShamaOS is a new design, not a copy: it keeps the useful ideas and expands them into a general-purpose system with a wider ISA, a GPU, persistent flash filesystem, a GUI OS, an in-world Editor, and native SHA-256 execution hardware inside the CPU.

## Target machine

| Subsystem | Target |
|---|---|
| CPU | General-purpose redstone CPU with extended custom ISA |
| SHA | Native SHA-256 execution unit integrated into the CPU and exposed as CPU instructions |
| GPU | Separate programmable graphics processor / command engine |
| RAM | **1 MiB** main RAM |
| Cache | **16 KiB** fast cache/scratch |
| Flash | **4 MiB** persistent ShamaFS storage (4× RAM) |
| Display | **320×180** 1-bit redstone-lamp framebuffer, double-buffered |
| Input | Keyboard plus GUI controller/D-pad with universal navigation buttons |
| OS | ShamaOS GUI shell loaded from flash into RAM at boot |
| Apps | Editor, File Explorer, Bitcoin Miner, System Monitor, Terminal/utility apps |
| Mining | Real Bitcoin-style double-SHA-256 computation and target comparison; educational/simulation only |

> **Important:** “1 MiB RAM” is the architectural target exposed to software. The generator may use hierarchical/banked physical implementations when a literal one-redstone-cell-per-bit design would be absurdly large. Physical, cached, banked, logical, VRAM and flash capacities must always be distinguished honestly.

## Core rules

1. **Real computation.** Python builds the machine; it must not secretly perform the redstone computer's mining work after generation.
2. **Modular generation.** CPU, SHA unit, memory, GPU, display, controller, flash and OS image are independent generator modules.
3. **Deterministic builds.** Same config + same source revision should produce the same layout/build manifest.
4. **Disposable-world friendly.** The primary workflow may generate into a fresh/disposable Java superflat world.
5. **No command-block substitute.** Commands/datapacks may not replace the actual CPU/GPU/SHA computation.
6. **Test before placement.** ISA, SHA, assembler, filesystem and reference CPU/GPU behavior are tested in software before mass placement.
7. **Honest capacity reporting.** Logical vs physically instantiated capacity is documented.
8. **One branch.** `main` is the project branch unless the owner explicitly changes that.

## Quick start

Python 3.11+:

```bash
git clone https://github.com/Eabusham2/ShamaOS-Redstone-PC.git
cd ShamaOS-Redstone-PC

python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

pytest
shamaos --help
```

Generate into a disposable Minecraft Java world folder:

```bash
shamaos generate \
  --world "/path/to/.minecraft/saves/ShamaOS" \
  --config configs/default.toml
```

Assemble a program:

```bash
shamaos asm examples/hello.asm -o build/hello.bin
```

## CPU and SHA

The CPU keeps the useful concepts from the MattBatWings teaching computer—ALU, registers, PC, flags, branches, call stack, load/store and assembler—but expands them into a broader general-purpose ISA.

The SHA accelerator is **inside the CPU** as a native execution unit. Software can use ordinary integer instructions or SHA-specific instructions such as SHA primitives, round operations, block hashing, double-SHA-256 and hash-target comparison.

The Bitcoin Miner app performs the real Bitcoin-style structure:

```text
SHA256(SHA256(80-byte block header))
```

with nonce iteration and target comparison. It is an educational Minecraft miner, not a competitive real-world Bitcoin miner.

## GPU and display

The GPU is a separate programmable graphics processor connected to shared memory/VRAM. It supports commands for pixels, lines, rectangles, text, sprites/blits, clearing, clipping, scrolling and buffer swaps.

The physical display is a **320×180** tiled redstone-lamp panel. That agreed target is locked unless the project owner explicitly approves a change. At 1 bit/pixel it uses 7,200 bytes per framebuffer (14,400 bytes double-buffered), fitting comfortably inside the 32 KiB VRAM budget.

## ShamaOS

ShamaOS lives in flash and boots into RAM/cache. The GUI includes:

- Desktop / launcher
- **Editor** (the final name; not “Program Editor”)
- **File Explorer**
- **Bitcoin Miner** with Start/Stop
- **System Monitor** with real-time RAM/cache/flash usage
- Terminal / utility support
- keyboard text entry
- controller navigation with universal Exit / Editor / File Explorer shortcuts

The Editor supports **New Text File** and **New Program**, saving, renaming, deleting and reopening source. Programs are written in Shama Assembly and may use OS system calls for file and GUI operations.

File Explorer can open/read `.txt` documents, run programs, open programs in Editor, rename files and delete with confirmation. Opening a program for editing closes the Explorer view.

When an app exits, the OS releases that app's allocated RAM and invalidates its cache entries while preserving the resident OS/kernel and shared buffers.

## Documentation

The canonical initial requirements are in **[`docs/INITIAL_SPEC_AND_TRANSCRIPTS.md`](docs/INITIAL_SPEC_AND_TRANSCRIPTS.md)**.

- `docs/REFERENCE_VIDEOS.md` — all supplied videos and transcript-derived facts
- `docs/ARCHITECTURE.md` — complete system topology
- `docs/ISA.md` — CPU ISA and SHA extensions
- `docs/GPU.md` — GPU command set and display architecture
- `docs/OS.md` — boot, GUI, apps and lifecycle
- `docs/FILESYSTEM.md` — flash filesystem and syscalls
- `docs/HARDWARE_GENERATION.md` — world generation strategy
- `docs/VALIDATION.md` — correctness/testing gates
- `docs/ROADMAP.md` — staged implementation without weakening final requirements

## Status

The repository starts by preserving the **full target specification**. Intermediate milestones are implementation stages, not permission to remove or silently shrink final requirements.

## License

MIT.
