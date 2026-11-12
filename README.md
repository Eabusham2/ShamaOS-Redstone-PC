# ShamaOS Redstone PC

A programmable Minecraft Java redstone computer, its ShamaOS software, and the
tools that generate its physical circuitry. Python and Yosys construct the
machine; the design performs CPU, SHA, graphics, memory and filesystem work in
redstone after generation.

**Status: implementation and validation in progress.** Firmware/RTL execution,
actual redstone fixtures, saved-world readback, full synthesis, and complete-world
boot are separate checks. A verified complete ShamaOS Minecraft world has not yet
been released. See [implementation status](docs/IMPLEMENTATION_STATUS.md) and the
[requirements and release gates](docs/REQUIREMENTS.md).

## Agreed machine

| Subsystem | Contract |
|---|---|
| CPU | 32-bit datapath, sixteen registers, full integer/logic/control-flow ISA, separate 256-entry call and data stacks |
| SHA | Native SHA-256/double-SHA execution plus programmable primitives and an independent software implementation |
| Main RAM | **1 MiB physical**, 256 × 4 KiB banks |
| Fast/executable memory | **16 KiB physical**, 4 × 4 KiB banks |
| Flash | **4 MiB** physical persistent storage, 1,024 × 4 KiB banks |
| VRAM | **32 KiB physical**, 8 × 4 KiB banks |
| Display | **320×180** monochrome, double buffered, 57,600 lamps |
| Input | 8×8 keyboard with Shift, controller, Home/Exit/Editor/Files, power/reset/pause/manual step |
| OS | Resident kernel, one foreground app, persistent files, in-world assembler/editor |
| Diagnostics | PC, instruction, registers, flags, CPU/stall state, native SHA state/round, faults and usage counters |

The density work preserves these capacities. [Memory design and efficiency](docs/MEMORY.md)
distinguishes placed-block count, horizontal footprint, touched chunks and world
file size; stacking improves different costs from making the cell smaller.

## Software

Desktop launches Editor, File Explorer, Bitcoin Miner, System Monitor, Terminal,
Calculator, Paint and Settings. Editor handles complete source/documents up to
65,535 bytes, cursor editing, Save As/Rename, confirmed Delete, assembly diagnostics
and separate `.BIN` output. Explorer traverses real folders and transfers an
opened source/document into Editor. Paint has a visible nondestructive cursor;
Settings persists its choices.

ShamaFS has owned handles, bounds/path checks, copy-on-write data replacement,
a metadata journal, recovery, and explicit full-disk errors. App switching and
power-off request a save/cleanup sequence. A failed save keeps the foreground
state available. Reboot works even after the executable cache was cleared.

The [miner](docs/MINER.md) edits the full header and target, displays all digest
bytes, measures native/software hashing, stops at nonce exhaustion and persists
256 complete history records. Its performance fields report hardware counter
units, not an invented wall-clock hash rate.

## Physical generation

The generator emits compact locked-repeater memory, latched display pixels,
buttons/keyboard/clock/debug lamps, synthesized technology cells, and their
interconnect. Primitive mapping includes enabled and synchronous-reset flip-flop
forms; ABC options are detected for the installed Yosys version.

The native Java 1.20.4 Anvil backend creates a new world without Amulet, writes
exact block properties, and reads the saved chunks back independently. An optional
Amulet path remains available for existing unrelated saves. Conflicting block
assignments are errors. Full generation also runs a resource/capacity preflight.
Large-scale routing/timing and a workable full-world loading configuration remain
release gates; local cell tests cannot establish them by themselves.

## Start here

Linux/macOS Python setup:

```bash
git clone https://github.com/Eabusham2/ShamaOS-Redstone-PC.git
cd ShamaOS-Redstone-PC
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
pytest -ra
shamaos flash-image -o build/shamaos-flash.img
shamaos plan --config configs/default.toml --manifest build/plan.json
shamaos generation-preflight --config configs/default.toml --report build/resource-preflight.json
```

Windows PowerShell, without requiring script activation:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -ra
.\.venv\Scripts\python.exe -m shamaos.cli flash-image -o build/shamaos-flash.img
```

Actual RTL/synthesis checks require Icarus Verilog and Yosys on PATH. Actual
redstone checks require the pinned MCHPRS harness; plain Python tests report
these checks as skipped if the tools are missing. CI's required behavioral job
installs the tools and rejects skipped tests.

For a small, independently saved/read-back redstone world:

```bash
shamaos fixture-world --world build/native-runtime-fixture
shamaos validate-world --world build/native-runtime-fixture
```

This fixture is a mechanism test, not the complete computer. The
[step-by-step tutorial](docs/TUTORIAL.md) covers tool installation, the real
runtime harness, new-world creation, preflight and full generation.

## Running speed

**Increasing the game tick rate or using a compatible acceleration mod/server is
recommended.** The default physical clock and conservative core divider make
vanilla operation extremely slow. `/tick rate` changes game tick scheduling;
`randomTickSpeed` does not accelerate this computer's ordinary redstone logic.
MCHPRS is a specialized redstone server. See
[running and acceleration](docs/RUNNING_AND_ACCELERATION.md) for commands and
scope. Acceleration does not repair wiring errors or unloadable parts of a build.

## Documentation

- [Requirements](docs/REQUIREMENTS.md), [original planning/source notes](docs/INITIAL_SPEC_AND_TRANSCRIPTS.md)
- [ISA](docs/ISA.md), [GPU](docs/GPU.md), [memory](docs/MEMORY.md), [controls](docs/CONTROLS.md)
- [ShamaOS](docs/OS.md), [syscalls](docs/SYSCALLS.md), [filesystem](docs/FILESYSTEM.md), [miner](docs/MINER.md)
- [Tutorial](docs/TUTORIAL.md), [validation](docs/VALIDATION.md), [implementation status](docs/IMPLEMENTATION_STATUS.md)

## License

MIT for this repository's code. Reference projects/designs are attributed in
[REFERENCE_VIDEOS.md](docs/REFERENCE_VIDEOS.md); their separate files and licenses
are not implicitly relicensed by this repository.
