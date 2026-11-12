# Changelog

## Unreleased — Requirement reconciliation and measured validation

- Restored the complete planning contract, Windows setup and acceleration guidance;
  documentation distinguishes software/RTL, physical fixtures and full-world gates.
- Added independent software SHA, native/software measured comparison, full-width
  editable miner inputs/records, persistence and nonce-exhaustion behavior.
- Completed full-source editing/assembly, Explorer folders/handoff, GPU dirty rows,
  printable lowercase glyphs, cursor overlays and persistent settings.
- Added owned resource cleanup, journaled copy-on-write storage, failure-safe app
  close/power-off, queued input and checked immutable-ROM reboot.
- Corrected actual redstone diode directions, state retention, display isolation,
  clock pulse completion and routed stair/fanout/sequential behavior.
- Reduced a 4 KiB storage bank from 2,511,744 to 1,991,552 placed blocks (20.7%); added
  explicit density/chunk/resource accounting and an experimental stacked fixture.
- Added native Java 1.20.4 world creation, independent saved-block validation and
  actual-engine fixtures without requiring Amulet for generated worlds.
- Added behavioral CPU/GPU/assembler/SoC tests, real Yosys sequential equivalence,
  pinned MCHPRS tests, Windows/Linux CI and exact-source validation artifacts.
- Full-capacity world generation, global timing/loading and in-world acceptance
  remain required; no complete-world success is implied by these changes.

## 1.0.0 — Earlier implementation milestone (full-world validation pending)

- Completed the 32-bit CPU and native SHA execution unit.
- Implemented literal **1 MiB RAM**, **16 KiB cache**, **4 MiB flash**, **32 KiB VRAM**.
- Implemented physical bank/row memory controllers and long-range backbones.
- Implemented external-VRAM GPU and 320×180 graphics path.
- Implemented the real 57,600-lamp horizontal display matrix.
- Implemented keyboard/controller/power/reset and hardware-universal app switching.
- Implemented ShamaFS hardware and persistent miner history/state.
- Implemented the in-world two-pass Shama Assembly assembler.
- Implemented Desktop, Editor, File Explorer, Bitcoin Miner, System Monitor, Terminal, Calculator, Paint and Settings.
- Implemented app RAM/cache flushing, foreground allocation and staged user-program execution.
- Implemented Yosys→redstone mapping, 2-D placement and crossing-safe strength-refreshed routing.
- Implemented direct Amulet chunk/palette world writing.
- Added divided core clock timing for long literal memory routes.
- Added fast behavioral CI and release-only full physical synthesis.

## 0.1.0 — Initial architecture/specification foundation

- Established canonical requirements and supplied-video reference notes.
- Added early assembler/reference models, ShamaFS image tooling and floorplanning foundation.
