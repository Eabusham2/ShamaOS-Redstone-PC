# Implementation Status

## v1.0.0 release candidate

The agreed ShamaOS Redstone PC implementation is present on `main`. This is no longer the original specification-only foundation.

### Locked physical capacities

- CPU datapath: **32 bit**
- main RAM: **1 MiB physical** — 256 × 4 KiB banks
- fast/executable cache: **16 KiB physical** — 4 × 4 KiB banks
- flash: **4 MiB physical** — 1,024 × 4 KiB banks
- VRAM: **32 KiB physical** — 8 × 4 KiB banks
- display: **320×180 × 1 bit**, 57,600 redstone lamps
- display physical pitch: 5×5 blocks, horizontal X/Z panel viewed from above

### Implemented hardware

- 32-bit CPU and project ISA.
- Native SHA execution unit inside the CPU, including SHA-256 and Bitcoin double-SHA.
- Programmable GPU with external physical VRAM.
- Double-buffered framebuffer and text/pixel/line/rectangle/circle/blit/sprite/scroll/clip/copy/swap/push/fence operations.
- Locked-repeater cache/RAM/flash/VRAM storage.
- One-hot bank/row selectors and physical read/write/data backbones.
- 320×180 latched lamp panel.
- 8×8 keyboard matrix and controller with D-pad/A/B/Home/Exit/Editor/Files.
- power/reset hardware and safe power-off clock behavior.
- raw input clock plus divided computational core clock for long-route settling.
- partitioned Yosys mapping into real redstone BUF/NOT/NAND/NOR/DFF cells, with a black-boxed top-level shell physically reconnecting the same integrated SoC ports.
- crossing-safe two-plane routing with explicit wire states and repeater-regenerated stairs.
- direct Amulet chunk/palette writer with missing-chunk creation.

### Implemented OS/storage/software

- 4 MiB ShamaFS image with boot/kernel/apps/editable assembly sources.
- hardware ShamaFS mount/create/open/read/write/truncate/rename/delete/stat/list.
- ShamaFS metadata is streamed from the real physical flash instead of duplicating the complete bitmap/file table in gate-expanded accelerator state.
- persistent allocation bitmap and file metadata.
- hardware two-pass Shama Assembly assembler.
- the assembler streams source line-by-line from RAM while retaining two-pass labels/definitions/syscall resolution, avoiding a second gate-expanded copy of the source/token database.
- resident kernel + one foreground app loaded from flash into RAM/cache.
- foreground page allocator with automatic release on app switch.
- hardware-universal Home/Exit/Editor/File Explorer switching.
- real RAM/cache/flash usage counters.
- Desktop, Editor, File Explorer, Bitcoin Miner, System Monitor, Terminal, Calculator, Paint and Settings.
- persistent miner state plus 256 × 64-byte history records.

### Generator

`shamaos generate` emits in one workflow:

1. literal cache/RAM/flash/VRAM/display/input fabrics;
2. memory backbones and physical clock;
3. synthesized SoC logic;
4. named long-distance interconnect;
5. generation manifest;
6. direct writes into an existing disposable Java 1.20.4 world.

Python/Yosys are build tools only. Runtime CPU/SHA/GPU/filesystem/OS computation is not delegated back to Python.

## Validation status

The fast release suite is green for Python 3.11/3.12, real Amulet block-state conversion, SHA known answers, CPU/GPU/display/memory/input RTL, app lifecycle, ShamaFS hardware, in-world assembler, GUI kernel and integrated SoC compilation.

The release-candidate SHA is physically synthesis-validated only when CI `full-physical-synthesis` succeeds on that same SHA.

Per-world in-game smoke testing still requires a concrete disposable Minecraft save.
