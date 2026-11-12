# Validation and release gates

The requirement IDs in [REQUIREMENTS.md](REQUIREMENTS.md) distinguish executable
software/RTL, local physical circuits, saved-world fidelity, complete physical
connectivity/timing, and a fully running Minecraft world. A green result at one
level must not be presented as a result at another.

## Automated evidence

| Layer | What executes | Important coverage | Limit |
|---|---|---|---|
| Host models and firmware | The shipped assembler/firmware on CPU, GPU and filesystem reference interfaces | Complete source round trips, full buffers, editing/navigation, persistence, independent SHA, bounds and failure behavior | Reference service interfaces are not physical circuits. |
| CPU RTL | Icarus runs the actual CPU with instruction fetches, byte strobes, wait states and SHA blocks | Arithmetic/flags/branches/stacks, faults, negative offsets, byte preservation, counters, SHA primitives/native hashes, actual software/native SHA comparison | Memory device responses are testbench models. |
| GPU RTL differential | Actual command engine against full reference frames | All primitives, clipping, text/glyphs, dirty rows, cursor overlays, queue depths/backpressure, native-resolution last pixel, VRAM/display stalls | This does not exercise a complete placed lamp panel. |
| Services/filesystem/input RTL | Actual hardware state machines and explicit bus stalls | Owned handles, allocator cleanup, queued keys and held acknowledgements, directory rules, full disk/table, journal recovery, save refusal and shutdown | Flash is represented by an exact byte-addressed test memory. |
| Integrated SoC RTL | CPU, native SHA, GPU, input, services, filesystem, kernel and assembler running shipped code | Boot, pause/step, universal Editor, X.TXT creation/ABC entry, dirty save, memory cleanup, paused shutdown, cache-cleared reboot, Explorer-to-Editor handoff and reopened bytes | Fast test memory devices replace the slow physical bank adapters for this long scenario. |
| Real synthesis | Installed Yosys/ABC and Icarus equivalence | ABC option compatibility; DFF/DFFE/SDFF/SDFFE/SDFFCE polarities, reset values, edge behavior and priority; real primitive-bank differential traces | Mapping a netlist does not establish electrical/timing correctness of all routes. |
| Actual redstone fixtures | Pinned MCHPRS redstone engine, emitted blocks and scheduled neighbour/tick updates | Gate/DFF traces; complete clock pulses at all phases; buttons/keys; preloaded compact memory read/write/retention/isolation; small display; representative routed signals/fanout/sequential circuits | Small fixtures do not prove complete-world loading or global clock skew. |
| Native world backend | Java NBT/Anvil files written, reopened and independently decoded | Exact states, coordinate/height checks, conflicting-write rejection, save/readback integrity, corruption detection and a saved-block runtime fixture | File fidelity is not proof of OS execution in that save. |

The integrated lifecycle scenario is a behavioral integration test, not a
pre-recorded UI animation. It drives the actual input path and verifies data
written by the filesystem across shutdown/reboot. Its recorded successful run
required 8,034,643 raw testbench clock cycles; subsequent source revisions must
rerun the same scenario rather than inheriting that result.

## Actual runtime harness

`tests/mchprs_runtime/Cargo.lock` pins the dependency graph. MCHPRS is pinned to
`f9e10d2572116bf5dcb9a36a305f28e40641a61a`. The harness instantiates emitted Java
blocks, applies their initial neighbour updates, operates real buttons/levers or
drives documented fixture pins, and advances the engine's scheduled ticks. A
trace can assert transitions throughout a hold period as well as final values.
It contains no replacement implementation of the tested gates or memory cells.

Set `SHAMA_MCHPRS_RUNNER` to the built executable. The ordinary Python developer
suite can explicitly skip unavailable external tools. The required CI behavioral
job installs Icarus/Yosys, builds the pinned runner, and rejects any skipped
required test. Optional Amulet conversion is isolated in its own real-backend job.

## CI jobs and artifacts

- Python 3.11/3.12 host tests on Linux and Windows plus CLI image/plan smoke tests.
- Real Amulet conversion of functional block-state properties, using system
  CMake for its native dependency build.
- HDL benches for SHA, memory controllers, input, GPU, services, filesystem,
  assembler and integrated compile.
- Complete behavioral and MCHPRS regression, with JUnit evidence and a native
  saved-world mechanism fixture reconstructed and run in the engine.
- On `[full-synth]` commits and pull requests: every physical SoC partition,
  a reconnected shell, exact port widths and canonical capacities.

Artifacts include the exact commit in their names and source hashes in world
reports. A report from a modified working tree explicitly says so. Never attach
a previous commit's green run as evidence for a later release.

## Full physical synthesis

`full-physical-partitions` synthesizes every direct `shama_soc` partition.
`full-physical-synthesis` downloads those mapped outputs, constructs the shell
with the real partition port positions and checks all connections/capacities.
Enabled/reset flops are lowered with their original semantics; unsupported
asynchronous forms fail explicitly.

This gate proves that the complete design maps and reconnects at the netlist/port
level. It does **not** prove that all emitted blocks are collision free, that
unrelated dust cannot couple, that every sink receives sufficient pulse width,
or that a Minecraft world containing it has booted. Those claims need the next
gates.

## Full-world acceptance still required

A release world must provide evidence of:

1. Actual default-capacity emission, exact saved-file readback, and no conflicting
   placements across regular fabrics, every technology cell and every route.
2. Driven-net continuity, fanout, electrical isolation and sequential timing on
   the complete placed design, including setup/hold and clock-distribution skew.
3. A documented loaded/ticking-chunk arrangement and measured host memory/disk
   requirements for the exact generated world and runtime.
4. First boot to the native 320×180 Desktop, physical keyboard/controller input,
   reset, pause/manual stepping and inspectable diagnostics.
5. In-world create/edit/save/reopen/assemble/run, all app launch/exit paths,
   owned-memory release, failed-save handling and orderly power cycles.
6. Native and software miner agreement with recorded header/target/hash vectors,
   editable configuration, stop/resume and history persistence after reboot.
7. Published world/source checksums, source revision, generator/runtime versions,
   actual clock/loading settings and passing CI for that exact revision.

Capacity, runtime and timing flags must remain false/unverified until those
checks actually run. A storage-only disk lower bound or nonzero route count must
never be substituted for them.

## Focused local commands

```bash
pytest -ra
pytest tests/test_cpu_rtl.py tests/test_gpu_execution.py tests/test_hardware_assembler.py
pytest tests/test_soc_integration.py
pytest tests/test_mchprs_runtime.py tests/test_memory_density.py
pytest tests/test_native_world.py tests/test_world_generation.py tests/test_world_resources.py
shamaos generation-preflight --config configs/default.toml --report build/resource-preflight.json
```

Repeated tests should resolve a concrete changed behavior or remaining risk.
Adding more compile-only checks does not compensate for missing full-world
runtime evidence.
