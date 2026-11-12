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

### Measured physical fixture contracts

`tests/test_mchprs_runtime.py` checks the following against actual scheduled
redstone updates, and rejects conflicting block placements before simulation:

- Technology DFF pins require at least 12 redstone ticks per clock half-period,
  8 ticks of data setup and 2 ticks of hold. Falling-edge data changes do not
  leak through the two storage repeaters.
- The emitted gated clock produces complete 35-tick high and low phases at all
  70 possible power-change offsets. A cold disabled clock stays low, including
  after its signal travels through a long external route. Hold keeps cleanup
  clocked after the power request is removed.
- Real stone buttons produce continuous 94-tick controller pulses. Keyboard
  row/column delay lines produce 119-tick pulses; the representative 16-net
  routed matrix has at least 95 ticks of simultaneous high for the tested
  corner/middle keys, exceeding one raw period plus the local setup/hold budget.
- Four nearby independent routes, three-way fanout across different heights,
  and forced west-side escapes preserve their signals. A placed/routed DFF and
  a lowered five-cell enabled register capture, hold and overwrite correctly
  when the test allows complete route propagation.
- A vertical debug wall and an 8×4 display preserve independent lamp values
  through their routed interfaces. Display data approaches from the front;
  interior row selectors approach from the side instead of crossing other rows.
- A three-level partition/shell/global register fixture captures and holds data,
  including a constant-one macro input, over 136,064 simulated ticks. Partition
  tracks use Y=286/290, shell tracks Y=300/304, and global tracks Y=314/318.
  Internal top-port connections leave north; the enclosing connections enter
  from the south, beyond the lower tier's branch starts.
- All external faces of a thirty-layer memory tile are connected in a real
  engine fixture: thirty bank selects, row/data/read/write inputs and read
  returns. First/middle/last-bank reads and a write/hold sequence pass over
  85,128 ticks. The literal fixture and its routes fit Y=-60..318 above the
  classic-flat grass. This fixture has four words and eight bits per bank;
  it does not establish full-size row-interface timing.

The router uses a separate endpoint landing plane and reserves stair headroom.
Directional repeaters refresh junctions as well as straight runs. The caller
must supply component obstacles for peripheral ports; an empty obstacle set
cannot prove that an otherwise valid route avoids an existing device. Internal
netlist routing supplies the emitted cell geometry. Dense placement that cannot
be isolated fails explicitly instead of accepting overlapping wires.

Global interconnect now supplies each peripheral's intended access face,
small exact terminal neighborhoods, complete memory-tile/display envelopes,
and the selected grass surface's minimum build Y. A stair must leave its native
envelope before rising and cannot enter another tile. These checks operate on
terminal count and tile bounds, without materializing storage billions of times.
The complete 993-signal diagnostic wall has a separate bounded-lead geometry
regression; full-wall electrical behavior is not inferred from that geometry.

These are fixture contracts, not a measured full-SoC frequency. The enabled
register fixture deliberately waits thousands of ticks for its long routes.
Full clock-tree skew, minimum data-path delay, maximum combinational delay,
matrix overlap after global routing and loaded/ticking chunks still require
validation on the exact complete placement.

The current default raw period of 70 redstone ticks and `CORE_DIV_BIT=17` give
`70 × 2^18 = 18,350,080` redstone ticks per core cycle: about 21.24 days at normal
10-redstone-tick/s operation. This is a configured delay, not a measured safe
frequency or a practical execution-speed claim. A shorter setting requires
route-derived clock-skew, setup and hold closure; a larger setting alone cannot
repair a hold-time failure.

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
