# ShamaOS complete requirements and acceptance contract

Reconciled with the planning conversation and owner instructions; latest
capacity amendment recorded on 2026-11-13 UTC.

This is the implementation checklist for the complete promised Minecraft computer.
It supplements `INITIAL_SPEC_AND_TRANSCRIPTS.md`; implementation shortcuts and
later status documents do not remove requirements. A feature is complete only
when its behavior is implemented and the relevant acceptance checks pass. A
successful compile, a manifest capacity, or an opcode name is not behavioral proof.

## Deliverable and architectural rules

- One complete, disposable Minecraft Java world that can be opened in the game,
  together with the modular generator, assembler, firmware source, documentation,
  tutorial, version tags, and release artifacts. The default repository branch is
  `main`, and the public project is **ShamaOS Redstone PC**.
- Release automation must retain the existing source/tools and validation assets,
  and add a full-machine schematic, a ZIP of the superflat world with the computer
  already installed, and a script that lets the player select a closed superflat
  save and install into a separate copy. Include a short, plain-language
  `README.txt` describing each download, setup, controls and speed/view-distance
  settings. Explain the recommended acceleration and viewing tools with their
  compatibility limits. The installer must preserve the original save and reject
  collisions, incompatible ground heights or incomplete world evidence.
- Runtime CPU, SHA, GPU, memory, input, filesystem, and OS operations execute in
  physical redstone. Python/Yosys construct and verify the machine; they do not
  secretly perform its runtime computation. Command blocks must not compute the
  program or hashes on behalf of decorative circuits.
- Preserve the owner-amended default capacities (2026-11-13 UTC): 32-bit CPU, 16
  architectural registers, 96 KiB RAM, 8 KiB cache/fast scratch,
  200 KiB flash, 16 KiB VRAM, and a readable
  320x180 one-bit lamp display. Banked layout is allowed; document physical
  capacity, access latency, and the actual cache/scratchpad semantics accurately.
- This explicit amendment replaces the earlier 1 MiB RAM / 4 MiB flash target.
  RAM uses 24 full 4 KiB banks; flash uses 50 full 4 KiB banks, with no
  padded or inaccessible substitute cells. The latest owner instruction replaces
  the intermediate 224 KiB proposal with 200 KiB, conditional on reducing the
  history-save temporary copy to at most 8 KiB while preserving every record
  and recovery after interrupted saves. The screen, assembler, controls, all supporting circuits and
  every software feature remain required. Seek further size and speed improvements,
  including parallel buses in unused space inside the perimeter, and validate the
  actual resulting behavior before claiming an improvement.
- The latest RAM halving is conditional on every shipped application retaining
  its required behavior. Preserve the complete 65,535-byte editable source,
  16,380-byte assembly/save output, 8 KiB execution capacity and full Miner
  state/software-SHA workspace. Check app switches and allocator ownership where
  mutually exclusive workspaces overlap; capacity arithmetic alone is insufficient.
- Normal Java redstone semantics are the correctness baseline. Faster tick rates,
  a compatible acceleration mod, or MCHPRS are recommended for practical use;
  see [Running and acceleration](RUNNING_AND_ACCELERATION.md).
- The world is disposable. A supplied personal save is not a prerequisite for
  delivering a generated example world. Generation may create a new world or
  write to a closed, disposable existing save.

## Hardware acceptance checklist

| ID | Required behavior |
|---|---|
| H01 | General-purpose CPU: arithmetic including carry/borrow, multiply/divide/modulo, bitwise operations, shifts/rotates, comparison/flags, immediates, byte/halfword/word memory, branches, call/return, stack, system calls and counters. |
| H02 | The documented ISA, compatibility aliases, assembler encodings, CPU implementation and executable tests agree. Unsupported instructions produce an error rather than silently executing another operation. |
| H03 | Native SHA unit inside the CPU: choice, majority, sigma operations, usable compression-round state, SHA-256, 80-byte-header double-SHA-256, full 256-bit comparison and nonce increment. |
| H04 | Software SHA running on ordinary CPU instructions and native accelerated SHA are selectable and comparable. Both produce the same known answers. |
| H05 | Physical RAM, fast memory, flash and VRAM support read/write, boundary cases and persistence/initialization appropriate to each region. Advertised flush/fence operations have defined working semantics. |
| H06 | Independent GPU command processing: pixel read/set/clear/invert, line, rectangle/fill, circle, blit/sprite, character/text, clipping, scrolling, cursor, font/color state, buffer clear/copy/swap and fences. |
| H07 | Dirty-tile or changed-region tracking updates changed screen regions. A full-frame implementation is not completion of this requirement. |
| H08 | Legible GUI on the 320x180 lamp display, including filenames, assembly syntax, selected actions, confirmation dialogs and complete hash/target information through readable pages if needed. |
| H09 | Physical keyboard and D-pad, Confirm, Back, Home, Exit, Editor and Files controls; queued input preserves short presses within a documented queue capacity and reports overflow. |
| H10 | Power, reset, pause/manual stepping and debugging controls. Expose PC/opcode/registers/flags, CPU/stall state, SHA round/state and memory/cache information for inspection. |
| H11 | Valid physical logic cells, isolated routes, regenerated long wires, correct repeater/torch timing, defined reset state, build-height/world-border bounds and a workable loaded/ticking-chunk strategy. |
| H12 | Build entirely above the superflat grass surface. Preserve every grass, dirt and bedrock block; supports, selectors, buses and other wiring must obey the same lower bound. Choose the maximum safe stack height above that surface while reserving the upper routing space. Fewer vertical layers must be compensated by more horizontal groups, without reducing any memory capacity. |
| H13 | Put all keyboard keys, Shift, controller/navigation and power/reset/pause/step in a compact walkable operating station with a readable screen view. The player must not fly around the machine to use controls. Backend circuits may be larger but must be clearly separated from the interaction footprint and stay above grass. |
| H14 | Give components distinct, simple colors and consistent visual accents, informed by the referenced Minecraft computers. Reuse existing support positions so styling adds no footprint, perimeter, blocks or timing changes. Validate the styled functional circuits and document the color legend and reference provenance. |

## OS, files and app acceptance checklist

| ID | Required behavior |
|---|---|
| O01 | Power-on initializes hardware, mounts persistent flash, loads the resident OS and GUI into RAM/fast memory, and displays Desktop. |
| O02 | Desktop launches all preinstalled apps and shows genuine used/total RAM, cache/fast-memory and flash counters derived from allocation and storage state. |
| O03 | Every app has a usable Exit path. Exit saves requested persistent state, closes its handles/resources, releases and clears app-owned memory/cache state, removes temporary GUI state and keeps the OS resident. |
| O04 | Universal Home/Exit/Editor/Files works even for a halted or nonpolling app. A normal close/save path precedes replacement; forced recovery is explicit and must not silently discard a save already in progress. |
| O05 | Clean shutdown finishes pending storage work before clock gating. Saved files and settings survive power cycles; reset/shutdown interruption behavior is tested and documented. |
| O06 | The app is named **Editor**. It provides New Text, New Program, Open, keyboard editing with cursor/navigation, Save, Save As, Rename, confirmed Delete, Assemble and Run. Selected actions are visibly identified. |
| O07 | Write, save, reopen, edit, assemble and run a new program entirely inside Minecraft without regenerating the world. Preserve editable source and executable separately. |
| O08 | Supplied program sources can be reopened and edited completely. Never silently truncate a file to fit a UI buffer or overwrite the complete file with its displayed prefix. |
| O09 | Assembly supports labels, definitions, comments, registers, decimal/hex/binary/character literals, full documented ISA and syscall symbols. Errors identify the failure and retain the previous valid executable. |
| O10 | File Explorer lists names/types/sizes, reads and scrolls text, runs programs, renames and deletes files. Edit closes Explorer and transfers the selected source/document to Editor. |
| O11 | Deletion names the selected file, initially selects Cancel and requires explicit Delete confirmation. Cancel changes neither content nor metadata. |
| O12 | Filesystem APIs implement create/open/close/read/write/truncate/rename/delete/stat/list, accurate free/used space and per-app handle cleanup/ownership. Duplicate names, invalid ranges, full storage and interrupted writes are handled. |
| O13 | Preserve the planned folder/file organization. A flat file table is an intermediate implementation, not evidence that the earlier folder commitment was completed. |
| O14 | Memory and UI syscalls implement allocation/free/usage, cache status/flush, app exit, input, drawing/text/dialogs, timers/counters and miner persistence. |
| O15 | Preinstalled Desktop, Editor, File Explorer, Bitcoin Miner, System Monitor, Terminal, Calculator, Paint and Settings are usable apps. Settings changes persist; Paint exposes a visible cursor and usable canvas controls. |
| O16 | Detect RAM, executable-cache and VRAM capacity faults before an invalid transfer; show the cause and gracefully recover/reboot the OS. Drain or recover storage transactions, preserve committed files, and never silently save partial/corrupt state or deadlock on an interrupted app snapshot. |
| O17 | At 95% allocated flash usage, disable new data/metadata writes and show an OS popup. Refuse a save whose temporary copy-on-write reservation would exceed that threshold before mutating flash. Permit explicit DELETE/zero-TRUNCATE reclamation and journal/mount recovery so protection is recoverable. Enforce the guard for direct CPU flash writes too. |
| O18 | Reduce Editor and File Explorer RAM/code costs, Miner executable-cache use and GPU VRAM needs where possible without reducing source limits, file viewing, folders, editing handoff, navigation, assembly, executable launch, complete hashes or screen functionality. Recompute and document per-app RAM/cache/shared-VRAM percentages after changes. |
| O19 | When a normal close/save fails, preserve the app and show Keep App as the default. Require an explicit Discard and Leave selection before releasing unsaved state. Cancel must preserve ownership and data; an acknowledged discard must allow reaching File Explorer or completing shutdown even when flash writes are protected. |

## Bitcoin Miner acceptance checklist

| ID | Required behavior |
|---|---|
| M01 | Real double-SHA-256 on an 80-byte header, configurable target/difficulty, nonce progression and exact full-width target comparison. |
| M02 | Start, Stop/pause, resume, new/reset generation, previous/next saved run and Exit; Stop preserves the last nonce/statistics. |
| M03 | Live complete hash/target, nonce, attempts, generation, validity, execution mode and measured rate. Indicators derive from computation and elapsed ticks. |
| M04 | Select software or native accelerated SHA and compare correct results and measured work/rate. |
| M05 | Persist and restore run state and history, including full hash, full target, nonce, attempts, generation, status and timer/counter. App exit and shutdown use the same persistence path. |
| M06 | Preserve all 256 complete 128-byte history records while limiting each history update's temporary copy to at most 8 KiB. A smaller journaled record update is allowed if the last committed state and idempotent interrupted-write recovery remain correct. Test the complete installed 200 KiB image, repeated saves, record boundaries, protection and recovery. |

## Generator, tests and release gates

| ID | Required evidence |
|---|---|
| V01 | Reproducible dependency/toolchain instructions for Windows and Unix, with useful prerequisite and synthesis errors; issue #1's two failures have regression coverage. |
| V02 | Modular deterministic generation with nonoverlapping components, exact block states, capacity/location manifest, chunk/region/NBT verification and read-back checks. |
| V03 | Behavioral coverage of every ISA operation, flags/branch/call/stack cases, memory bounds, SHA known answers, miner comparisons and assembler diagnostics. |
| V04 | GPU primitive/reference comparisons, dirty updates, text legibility, input queues, visible GUI navigation, app lifecycle, filesystem transactions and power-cycle persistence. |
| V05 | Physical cell truth tables and sequential traces, routed-signal tests and representative memory/display tests in a redstone simulator or Minecraft runtime. Net counts alone are insufficient. |
| V06 | Full default-capacity synthesis, physical placement/interconnect validation, complete world creation and structural read-back. Reduced test fixtures do not substitute for the full deliverable. |
| V07 | End-to-end generated-world run: power/boot, keyboard/controller, create/save/edit/assemble/run, Explorer handoff, exit/cleanup, reboot/persistence, miner both modes and display updates. |
| V08 | CI for the exact release commit passes the required gates; publish the actual tested world and sources with checksums/version metadata. Report any unexecuted gate explicitly. |
| V09 | Release CI packages the complete schematic, installed superflat world, existing-world installer, simple `README.txt`, source/tool bundle and retained validation artifacts. Check archive contents, checksums, exact capacity and independent readback; require actual full-world routing/timing/runtime evidence before publishing complete-world assets. Test backup/install failure paths and never substitute a small fixture for the computer. |

## Conditional ideas and provenance

Color beyond one bit, optional sprite flips/transforms, dedicated MEMCPY/MEMSET
hardware, a high-level MINE opcode, and a particular pipeline organization were
conditional implementation ideas. Example future games (Snake/Pong/Tetris) and
the reference videos' unrelated showcase features are not a promised preinstalled
game collection. This does not weaken the general-purpose instruction or GPU
capabilities needed to write such software.

The original discussion described natural cache use; the saved contract also
permits explicitly managed fast scratch. Whichever is implemented must be named
accurately and must implement its documented operations and real usage counters.

Source history: the planning conversation, `INITIAL_SPEC_AND_TRANSCRIPTS.md`, the
original CPU/ISA/GPU/OS/filesystem/control contracts, and issue #1. Record later
owner-approved changes explicitly instead of silently replacing this checklist.
