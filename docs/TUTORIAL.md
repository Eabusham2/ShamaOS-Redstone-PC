# Build, test, and generate

The complete acceptance checklist is [REQUIREMENTS.md](REQUIREMENTS.md). This
workflow distinguishes a small saved-world fixture from a full physical computer.
Consult [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md) before treating any
world as a release candidate.

## 1. Python setup

Clone the repository with Git, then enter its directory:

```bash
git clone https://github.com/Eabusham2/ShamaOS-Redstone-PC.git
cd ShamaOS-Redstone-PC
```

### Linux/macOS shell

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

### Windows PowerShell

The Unix `source .venv/bin/activate` command is not a Windows command. Invoke the
virtual environment's Python directly; this also avoids a PowerShell activation
policy requirement:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -ra
.\.venv\Scripts\python.exe -m shamaos.cli plan --config configs/default.toml --manifest build/plan.json
```

In the remaining examples, an activated environment can use `shamaos`. Without
activation on Windows, replace `shamaos` with
`.\.venv\Scripts\python.exe -m shamaos.cli`. All CLI commands accept normal
Windows paths. Use one command per line rather than copying Bash backslash
continuations into PowerShell.

## 2. Actual hardware tools

Install Icarus Verilog (`iverilog` and `vvp`) and Yosys. On Ubuntu/Debian:

```bash
sudo apt-get install iverilog yosys
```

On Windows, an OSS CAD Suite installation can provide both. Add its `bin`
directory to PATH for the current shell, using its actual installed location:

```powershell
$env:Path = 'C:\oss-cad-suite\bin;' + $env:Path
iverilog -V
yosys -V
```

The generator probes ABC's supported options. Do not work around an unsupported
`abc -fast` by deleting flags manually: the compatibility path chooses supported
arguments. Enabled and synchronous-reset flip-flops (`DFFE`, `SDFF`, `SDFFE`,
`SDFFCE`, including polarities/priority) are lowered semantically rather than
being dropped. [Issue #1](https://github.com/Eabusham2/ShamaOS-Redstone-PC/issues/1)
provided the Windows/ABC/flip-flop reproduction that motivated these checks.

Build the actual redstone test runner with Rust/Cargo:

```bash
cargo build --locked --release --manifest-path tests/mchprs_runtime/Cargo.toml
```

On Linux/macOS:

```bash
export SHAMA_MCHPRS_RUNNER="$PWD/tests/mchprs_runtime/target/release/shama-redstone-check"
pytest -ra
```

On Windows PowerShell, use the built `.exe`:

```powershell
$env:SHAMA_MCHPRS_RUNNER = "$PWD\tests\mchprs_runtime\target\release\shama-redstone-check.exe"
.\.venv\Scripts\python.exe -m pytest -ra
```

The harness links a pinned MCHPRS redstone engine. It places the emitted blocks,
applies actual neighbour updates, drives buttons/signals, and advances scheduled
redstone ticks. It does not replace the cells with Python truth functions.
`pytest -ra` exposes unavailable-tool skips. A test run with hardware skips is
not a successful hardware release gate.

## 3. Flash and resource plan

```bash
shamaos flash-image -o build/shamaos-flash.img
shamaos software-usage --report build/software-usage.json
shamaos asm examples/bitcoin_miner.asm -o build/miner.bin --listing build/miner.lst
shamaos plan --config configs/default.toml --manifest build/plan.json
shamaos generation-preflight --config configs/default.toml --world build/ShamaOS --report build/resource-preflight.json
```

The flash image is exactly 200 KiB and contains packed, length-prefixed boot/application records,
complete editable sources, documents, directories, settings/miner data and
filesystem metadata. The preflight reports physical storage bits, banks,
placements, touched chunks, disk lower bounds, source identity and unverified
full-machine costs. Its lower bound excludes logic and interconnect; it is not
a promise that the complete world fits that amount.

## 4. A small playable mechanism fixture

```bash
shamaos fixture-world --world build/native-runtime-fixture --runtime-runner "$SHAMA_MCHPRS_RUNNER"
shamaos validate-world --world build/native-runtime-fixture
shamaos stacked-fixture-world --world build/stacked-native-runtime-fixture --runtime-runner "$SHAMA_MCHPRS_RUNNER"
shamaos validate-world --world build/stacked-native-runtime-fixture
```

The stacked fixture preserves its grass terrain and exercises three representative
32-bit banks through their shared read-return wiring. It uses two words per bank
to keep the independent save/reopen/runtime check small; its report explicitly
does not claim the complete machine's capacity or timing.

Use `$env:SHAMA_MCHPRS_RUNNER` instead of `$SHAMA_MCHPRS_RUNNER` in PowerShell.
The command creates native Java 1.20.4 world files, reads them back, and, when a
runner is supplied, tests the reconstructed saved blocks in the actual engine.
Its report names its limited scope. Copy that world folder into Minecraft's
`saves` directory to inspect the fixture. It is not the ShamaOS desktop computer.

## 5. Full generation

Create a new grass superflat world directly, without installing Amulet or
manually launching Minecraft:

```bash
shamaos create-world --world build/ShamaOS --name ShamaOS
shamaos generate --world build/ShamaOS --config configs/default.toml --manifest build/generated-manifest.json
shamaos validate-world --world build/ShamaOS
shamaos validate-manifest --manifest build/generated-manifest.json
```

The generated terrain has bedrock at Y=-64, dirt at -63/-62, and grass at -61.
The first permitted build block is -60. Every support, memory cell, selector and
route stays above the grass; the writer rejects a placement on or below it.
Ground is present in every written chunk and is included in independent saved
world verification. The spawn platform is also above the grass.

Memory stacking is automatic. The default surface allows 30 layers at a
12-block pitch while reserving the upper routing canopy. A grass surface at
Y=63 allows 20 memory layers in memory-only profiles. All 80 physical banks remain present; the generator
uses more horizontal groups when fewer layers fit. The final group contains
only its actual banks and retains their original address order. The full upright
screen is a separate constraint: its top is 365 blocks above grass, so the
complete build requires grass at Y=-46 or lower. Use the default grass-at-Y=-61
superflat profile for the complete computer. A grass-at-Y=63 save cannot hold
this screen and is rejected before any placement.

`generate` and `generation-preflight --world ...` read an existing world's flat
layer settings from `level.dat` instead of assuming the grass height. For a new
custom-height grass world within those limits, set `world.surface_y` to the grass-block coordinate
and `world.origin_y` to five blocks above it; keep `memory.stack_layers` and
`memory.storage_base_y` automatic. Unsafe explicit heights/layer counts fail
validation. The standalone mechanism fixture remains a small isolated test save.

The generator performs resource/capacity checks before a huge emission. It must
not silently reduce memory or display resolution to fit available disk. A real
save with unrelated existing content uses the optional Amulet backend, installed
with `python -m pip install -e ".[dev,world]"`; keep the game/server closed while
writing. The native backend is intended for its own generated worlds and does
not rewrite unknown existing-world metadata as a shortcut.

The full build includes memory/backbones, clock/controls/debug lamps, latched
screen, synthesized CPU/SHA/GPU/services/filesystem/assembler logic and external
routing. Native saved-file validation proves saved blocks agree with the emitted
manifest. It does not prove that every route functions or that the OS has booted.

Full-capacity world generation, loading strategy and physical timing remain
separate expensive gates. A failure is reported as a failure, not converted into
a smaller demonstration world labelled as complete.

## 6. Run and accelerate

Increasing game tick rate or using a compatible acceleration mod/server is
recommended. See [RUNNING_AND_ACCELERATION.md](RUNNING_AND_ACCELERATION.md) for
`/tick rate`, reset/freeze/step commands and MCHPRS guidance. Changing
`randomTickSpeed` does not speed up the ordinary redstone components here.

Use power/reset/pause/manual-step and the diagnostic lamps to observe execution.
Home/Exit/Editor/Files request the common save/cleanup lifecycle even when an app
has halted or does not poll events. Check the lifecycle error display if a save
cannot complete. The [controls](CONTROLS.md), [OS](OS.md), and [miner](MINER.md)
guides describe the implemented interactions.

## Release evidence

CI runs host tests on Python 3.11/3.12 and Windows/Linux, the optional real Amulet
conversion gate, behavioral HDL/actual-redstone checks without skipped required
tests, and a saved fixture artifact. Commits containing `[full-synth]` additionally
run every SoC synthesis partition and reconnect the shell. See
[VALIDATION.md](VALIDATION.md) for what each result proves and the remaining full
world acceptance sequence.
