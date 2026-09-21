# Changelog

## 1.0.0 — Full ShamaOS Redstone PC release candidate

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
