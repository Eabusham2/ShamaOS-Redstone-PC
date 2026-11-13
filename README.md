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
| Main RAM | **96 KiB physical**, 24 × 4 KiB banks |
| Fast/executable memory | **8 KiB physical**, 2 × 4 KiB banks |
| Flash | **200 KiB** physical persistent storage, 50 × 4 KiB banks |
| VRAM | **16 KiB physical**, 4 × 4 KiB banks |
| Display | **320×180** monochrome, double buffered, 57,600 lamps |
| Input | 8×8 keyboard with Shift, controller, Home/Exit/Editor/Files, power/reset/pause/manual step |
| OS | Resident kernel, one foreground app, persistent files, in-world assembler/editor |
| Diagnostics | PC, instruction, registers, flags, CPU/stall state, native SHA state/round, faults and usage counters |

The owner finalized **96 KiB RAM, 200 KiB flash, 8 KiB cache and 16 KiB
VRAM**. History updates now stage a **128-byte record** in existing reserved
metadata, below the requested 8 KiB temporary-copy budget, while retaining all
256 complete records and interrupted-save recovery. Editor uses **72.05% normally and 88.71%
at its largest assembly peak**; Explorer uses **72.68%**. Shared workspaces and
compact executable backing preserve the complete 65,535-byte source and
16,380-byte assembly/save limits. Miner now uses **8.44% RAM and 84.28% cache**.
The initial flash allocates **158.5 KiB (79.25%)**. All complete editable source
files, binaries and persistent records remain. Two full screen buffers plus
text/sprites fit in **15,680 bytes**; scrolling reserves no third frame.

CPU, SHA, screen, controls, in-world assembly and OS features remain required.
All shipped programs fit the new 8 KiB executable cache. Oversized user programs
can still be assembled/saved and must trigger graceful recovery on execution.
[Memory design and efficiency](docs/MEMORY.md) distinguishes software usage,
placed blocks, horizontal footprint, occupied chunks and world file size.

### Per-app software occupancy

One foreground app runs at a time. RAM includes the resident kernel, app code
backing and complete workspace, without optional heap pages. Each app uses the
same shared **15,680-byte VRAM reservation (95.70% of 16 KiB)**. Flash shares
include complete editable source, separate executable, packed loader record and
installed persistent data; shared metadata and documents are counted separately.

| App | RAM bytes | RAM | Cache bytes | Cache | Attributed flash |
|---|---:|---:|---:|---:|---:|
| Desktop | 244 | 0.25% | 232 | 2.83% | 0.87% |
| Editor | 70,824 | 72.05%; peak 88.71% | 4,696 | 57.32% | 12.04% |
| File Explorer | 71,448 | 72.68% | 5,320 | 64.94% | 14.10% |
| Bitcoin Miner | 8,292 | 8.44% | 6,904 | 84.28% | 33.87% |
| System Monitor | 64 | 0.07% | 52 | 0.63% | 0.40% |
| Terminal | 2,956 | 3.01% | 1,920 | 23.44% | 4.94% |
| Calculator | 1,836 | 1.87% | 1,824 | 22.27% | 5.14% |
| Paint | 1,152 | 1.17% | 868 | 10.60% | 2.30% |
| Settings | 1,056 | 1.07% | 772 | 9.42% | 2.13% |

Editor additionally has 4 KiB of allocatable heap, Explorer 20 KiB and Miner
80 KiB. Other apps have 80–84 KiB depending on their workspace. The allocator
protects overlapping workspaces; free bytes are not all interchangeable heap.

Flash's 158.5 KiB consists of **74.75 KiB sources, 23.75 KiB separate executables,
22.25 KiB packed loader, 32 KiB Miner history, 0.25 KiB Miner state, 1 KiB welcome
documents and 4.5 KiB metadata**. It leaves **41.5 KiB free**, with **31.5 KiB**
of allocation headroom before the **190 KiB / 95% write guard**. Copy-on-write
saves also need their temporary replacement extent to fit. Miner history uses
its bounded record journal and needs no replacement extent. At this capacity,
saving a maximum-size source and maximum-size output together can require
reclaiming space first; the OS preserves the previous file if a save cannot fit.
Full byte counts, per-app flash attribution and address maps are in
[MEMORY.md](docs/MEMORY.md); `shamaos software-usage` regenerates the figures from
the actual image.

### Current measured footprint

The default superflat layout starts one block above grass. A shared column
stacks cache's 2 banks, VRAM's 4 and RAM's 24; flash uses two more columns.
The top support/control geometry reaches Y=307 in the grass-at-Y=-61 world.
All local bank selectors, binary row decoders and read-return wiring are counted.

| Component | Physical capacity | Bank modules | Occupied X/Z chunks |
|---|---:|---:|---:|
| Shared cache + RAM + VRAM column and local wiring | 8 + 96 + 16 KiB | 30 | 8,102 |
| Flash and local wiring | 200 KiB | 50 | 15,045 |
| Screen | 320×180 pixels | — | 108 |
| Keyboard / controller circuitry | — | — | 47 |
| Debug panel | — | — | 9 |
| Clock | — | — | 17 |
| **Measured regular-component subtotal** | **320 KiB memory** | **80** | **23,328** |
| Synthesized CPU/GPU/assembler/OS logic and connecting routes | — | — | **Not yet established** |

The regular-component envelope is **3,425×6,388 blocks**, or
**215×400 = 86,000 chunks**, including gaps. Compared with the previous
250 KiB flash / horizontal-screen checkpoint, occupied chunks fall **36.15%**
and the envelope **26.30%**. The upright screen retains all 57,600 pixels in
108 occupied chunks; upward memory-return columns remove the long descending
tails. These counts include local decoders and count shared X/Z chunks once.
Placement emits **202,522,304 regular-component operations**;
complete-world logic and routing remain additional.

The [retained geometry report](docs/evidence/capacity-200-upright-return.json)
includes exact source hashes and the excluded full-machine costs.

All 79 input actuators fit a connected, walkable **16×16 activation area**;
47 chunks is the separate backend circuitry. A nearby walking lip provides the
standing view. Conservative ray tests see every lamp past the console and its
30 export paths, and a documented 16:9 perspective contains the whole screen.
The full GPU/bridge/panel replay checks every lamp through 73 actual-engine
steps. In-client legibility with complete-machine routes, global timing and
complete-world boot remain release gates. See [MEMORY.md](docs/MEMORY.md) for per-fabric counts
and [VALIDATION.md](docs/VALIDATION.md) for evidence boundaries.

### Component colors

Existing supports now give each component a distinct color, with simple alignment
stripes: red CPU, blue GPU, green RAM, orange flash, yellow cache and cyan VRAM.
The compact console has a dark deck, alternating keyboard rows and colored
action controls. This adds **no blocks, height, chunks or perimeter**. Functional
blocks and their positions remain unchanged. Saved/read-back memory, display and
all 79 console actuators pass actual-engine checks with the colors applied.

![Console operating layer and component colors](docs/images/component-colors.svg)

This is an operating-layer diagram generated from actual console coordinates;
overhead circuits are omitted for clarity. See [component colors and design
references](docs/APPEARANCE.md) for the complete palette and GitHub inspiration.

### Capacity protections required for release

- RAM, executable-cache and VRAM faults stop invalid transfers, show the cause
  and perform graceful OS recovery/reboot, preserving committed files and
  completing or recovering active storage transactions.
- Flash becomes write protected at **190 KiB / 95%**. A temporary copy-on-write
  reservation beyond that limit is refused before mutation and produces an OS
  warning. Explicit deletion or zero-length truncation can reclaim space;
  filesystem journal recovery remains available.
- Failed normal saves preserve the live app. Keep App is selected initially;
  Discard and Leave requires an explicit confirmed choice. This keeps a full
  flash from trapping the user away from Explorer's reclamation controls.
- Compaction preserves full-file viewing, folders, Editor handoff, assembly,
  program execution and complete Miner records. Reports are regenerated after
  changes; the status document distinguishes tested behavior from open gates.

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
state available and defaults to **Keep App**. An explicit **Discard and Leave**
choice allows reaching Files or shutting down when a save is blocked; cancel
preserves the live buffers. Reboot works even after executable cache was cleared.

The [miner](docs/MINER.md) edits the full header and target, displays all digest
bytes, measures native/software hashing, stops at nonce exhaustion and persists
256 complete history records. Its performance fields report hardware counter
units, not an invented wall-clock hash rate.

## Physical generation

The generator emits compact locked-repeater memory, latched display pixels,
buttons/keyboard/clock/debug lamps, synthesized technology cells, and their
interconnect. Primitive mapping includes enabled and synchronous-reset flip-flop
forms; ABC options are detected for the installed Yosys version.

The assembler now accumulates numbers during its existing byte reads, sharing
one arithmetic path and retaining only the short character prefix needed for
registers and quoted bytes. Its matched AIG mapping drops from **738,790 to
559,593 physical logic cells (24.26%)** while preserving complete symbols,
numeric limits and the exact assembly cycle counts of all nine shipped apps.
The focused assembler regression passes 84 cases without skips. This is a logic
size reduction; complete routing, chunk savings and world timing still need
measurement in the full build.

The native Java 1.20.4 Anvil backend creates a grass superflat world without Amulet,
preserves the ground, writes exact block properties, and reads the saved chunks
back independently. Every part of the full build stays above the grass. Stacking
uses the available height and leaves room for the upper routing planes; higher
grass presets automatically fit fewer memory layers with the same capacity.
The full upright screen has a separate height requirement: its top is 365 blocks
above grass, so the complete default build requires grass at Y=-46 or lower.
A grass-at-Y=63 world cannot hold this screen; generation rejects it before
placing blocks. An optional
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

## Release downloads

The release workflow packages a complete superflat save, a `.litematic`
schematic, an installer for a new copy of a selected superflat save, the exact
source/tools, validation files and a short [README.txt](release/README.txt).
It retains the existing release assets and all original CI evidence. The guide
covers setup, controls, tick speed, simulation/render distance and the
acceleration/viewing tools below. Large downloads are split and checked.

The packaging and installer are implemented and tested with clearly identified
small fixtures. **Complete-world publication remains blocked** while full routing,
timing, screen viewing and the whole-computer acceptance runner are unfinished.
There is no verified complete-world download yet. See the
[release workflow and remaining gates](docs/RELEASES.md).

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
- [Component colors](docs/APPEARANCE.md), [release downloads](docs/RELEASES.md), [simple release guide](release/README.txt)

## License

MIT for this repository's code. Reference projects/designs are attributed in
[REFERENCE_VIDEOS.md](docs/REFERENCE_VIDEOS.md); their separate files and licenses
are not implicitly relicensed by this repository.
