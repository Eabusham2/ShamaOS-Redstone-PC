# Roadmap / Release State

## Implemented for v1.0.0

- 32-bit CPU and native SHA unit
- 1 MiB physical RAM
- 16 KiB physical executable cache
- 4 MiB physical flash
- 32 KiB physical VRAM
- 320×180 physical lamp display
- keyboard/controller/power/reset
- ShamaFS hardware
- in-world assembler
- ShamaOS boot/app lifecycle and hardware-universal switching
- Desktop, Editor, File Explorer, Bitcoin Miner, System Monitor, Terminal, Calculator, Paint, Settings
- Yosys→redstone logic synthesis
- physical memory/display fabrics
- crossing-safe strength-refreshed routing
- direct Java-world writer
- fast CI and release-only full physical synthesis gate

## Post-v1.0 improvements

Optimizations/extensions only:

- sparse dirty-tile pushes instead of full-frame pushes
- richer font
- larger paged Editor source buffer
- more Terminal commands
- timing/area compaction after in-game profiling
- automated capture of generated-world MCHPRS smoke tests

Locked capacities/resolution must not be reduced without explicit owner approval.
