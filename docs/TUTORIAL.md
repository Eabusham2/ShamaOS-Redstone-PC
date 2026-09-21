# End-to-End Tutorial

## Install

```bash
git clone https://github.com/Eabusham2/ShamaOS-Redstone-PC.git
cd ShamaOS-Redstone-PC
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev,world]"
```

## Validate

```bash
pytest
```

CI additionally validates SHA/CPU/GPU/memory/input RTL, ShamaOS app lifecycle, ShamaFS, the in-world assembler, kernel and integrated SoC compile.

## Build flash image

```bash
shamaos flash-image -o build/shamaos-flash.img
```

The image is exactly **4 MiB** and includes boot/kernel/app slots, editable source, text and fixed miner persistence files.

## Plan

```bash
shamaos plan --config configs/default.toml --manifest build/plan.json
```

Defaults: 1 MiB RAM, 16 KiB cache, 4 MiB flash, 32 KiB VRAM, 320×180 lamps.

## Generate

Create a **disposable Minecraft Java 1.20.4 superflat world**, close Minecraft, then:

```bash
shamaos generate \
  --world "/path/to/.minecraft/saves/ShamaOS" \
  --config configs/default.toml \
  --manifest build/generated-manifest.json
```

The command emits all regular fabrics, preloaded flash/cache, display/input, physical backbones, synthesized CPU/SHA/GPU/OS/filesystem/assembler logic, physical clock and long-distance interconnect.

Generation is extremely large.

## Run

Vanilla is the correctness baseline but is intentionally extremely slow. MCHPRS or a compatible accelerator is strongly recommended.

Universal hardware controls: Home, Exit, Editor and File Explorer. Editor can create/save/rename/delete/assemble/run programs and text. File Explorer can view/edit/run/rename/delete persistent files. Bitcoin Miner performs real CPU double-SHA work.

## Release validation

Normal CI runs the fast behavioral suite. Release-candidate commits containing `[full-synth]` run the expensive complete SoC→redstone physical synthesis/port-mapping job.

An in-game smoke test still requires the actual generated save.
