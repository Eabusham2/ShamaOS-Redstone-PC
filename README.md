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
| Main RAM | **192 KiB physical**, 48 × 4 KiB banks |
| Fast/executable memory | **8 KiB physical**, 2 × 4 KiB banks |
| Flash | **500 KiB** physical persistent storage, 125 × 4 KiB banks |
| VRAM | **16 KiB physical**, 4 × 4 KiB banks |
| Display | **320×180** monochrome, double buffered, 57,600 lamps |
| Input | 8×8 keyboard with Shift, controller, Home/Exit/Editor/Files, power/reset/pause/manual step |
| OS | Resident kernel, one foreground app, persistent files, in-world assembler/editor |
| Diagnostics | PC, instruction, registers, flags, CPU/stall state, native SHA state/round, faults and usage counters |

The owner finalized **192 KiB RAM, 500 KiB flash, 8 KiB cache and 16 KiB
VRAM** on 2026-11-12, superseding earlier sizing proposals. Normal Editor/Explorer
RAM use is now about 36%; Editor temporarily needs 44.36% when assembling its
largest source files. The initial flash allocates **159.75 KiB (31.95%)**.
All source files and binaries remain, and packed boot records remove unused
padding. Two full screen buffers plus text/sprites fit in **15,680 bytes**;
scrolling no longer reserves a third frame.

CPU, SHA, screen, controls, in-world assembly and OS features remain required.
All shipped programs fit the new 8 KiB executable cache. Oversized user programs
can still be assembled/saved and must trigger graceful recovery on execution.
[Memory design and efficiency](docs/MEMORY.md) distinguishes software usage,
placed blocks, horizontal footprint, occupied chunks and world file size.

### Current measured footprint

The default superflat layout starts one block above grass and uses up to
30 layers. Counts below include the actual local memory selectors and shared
read-return wiring. They describe occupied horizontal X/Z chunks.

| Component | Physical capacity | Banks | Occupied chunks |
|---|---:|---:|---:|
| Cache / fast memory and local wiring | 8 KiB | 2 | 6,568 |
| Main RAM and local wiring | 192 KiB | 48 | 13,594 |
| Flash and local wiring | 500 KiB | 125 | 34,055 |
| VRAM and local wiring | 16 KiB | 4 | 6,588 |
| Screen | 320×180 pixels | — | 6,328 |
| Keyboard / controller circuitry | — | — | 76 |
| Debug panel | — | — | 9 |
| Clock | — | — | 17 |
| **Measured regular-component subtotal** | **716 KiB memory** | **179** | **67,235** |
| Synthesized CPU/GPU/assembler/OS logic and connecting routes | — | — | **Not yet established** |

This snapshot's regular-component envelope is **6,552×9,842 blocks**, or
**410×616 chunks**; only 67,235 of the 252,560 envelope chunks are occupied.
The rectangular area includes gaps. It is not the complete computer's footprint.
Storage cells alone occupy 39,750 chunks stacked versus 793,786 laid flat at the
same capacities (94.99% fewer). All listed components together emit 444,729,391
placement operations; synthesized logic and external routes are additional.

The current controls are still a backend layout, with a 35×35-block key surface
and controller buttons spread over 110 blocks. **This does not meet the owner's
compact operating-station requirement.** The player-facing keyboard, controller,
power/reset/pause/step and screen view must be usable from one nearby walkable
station without flying. Its revised geometry, perimeter compaction and full
logic routing remain in progress. `generation-preflight` regenerates the current
footprint report; [memory usage](docs/MEMORY.md) lists every app's allocation.

### Capacity protections required for release

- RAM, executable-cache and VRAM capacity faults must stop the invalid transfer,
  report the cause and perform graceful OS recovery/reboot, preserving committed
  files and completing or recovering any active transaction.
- Flash becomes write protected at **475 KiB / 95%**. A save that needs more
  temporary copy-on-write space than that limit is refused before mutation.
  The OS must show a warning. Explicit deletion or zero-length truncation may
  reclaim space; filesystem recovery may complete its metadata transaction.
- File Explorer compaction must preserve full-file viewing, directories, Editor
  handoff and program execution. Per-app reports are regenerated after changes.

These are acceptance requirements; the implementation status document records
which hardware and end-to-end tests have actually passed.

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

The native Java 1.20.4 Anvil backend creates a grass superflat world without Amulet,
preserves the ground, writes exact block properties, and reads the saved chunks
back independently. Every part of the full build stays above the grass. Stacking
uses the available height and leaves room for the upper routing planes; higher
grass presets automatically fit fewer layers with the same memory capacity. An optional
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
shamaos software-usage --report build/software-usage.json
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
The recommended tools depend on the job:

| Tool | Recommended use | ShamaOS verification status |
|---|---|---|
| [MCHPRS](https://github.com/MCHPR/MCHPRS) | Redstone simulation acceleration, including its `/rtps` and Redpiler facilities | The pinned engine verifies the repository's physical circuit fixtures. Full-computer import, loading and timing still need acceptance testing. |
| [Distant Horizons](https://modrinth.com/mod/distanthorizons) | Optional distant views and an overview of the large build; select a release for Java 1.20.4 and the matching mod loader | Its distant geometry is a viewing aid. It does not establish that distant circuits are ticking or that the live lamp screen is readable. |
| [Horizon Launcher / Horizon](https://playhorizon.dev/docs/faq) | Experimental alternative runtime to evaluate for performance | Horizon currently targets Java 26.3 behavior; ShamaOS targets 1.20.4. It needs a separate compatibility run, and ordinary Fabric/Forge/NeoForge Java mods do not run in it. |

See [running and acceleration](docs/RUNNING_AND_ACCELERATION.md) for commands and
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
