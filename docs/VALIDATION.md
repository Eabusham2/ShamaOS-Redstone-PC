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
| Integrated SoC RTL | CPU, native SHA, GPU, input, services, filesystem, kernel and assembler running shipped code | Boot, pause/step, universal Editor, X.TXT creation/ABC entry, dirty save, memory cleanup, paused shutdown, cache-cleared reboot, Explorer-to-Editor handoff and reopened bytes | Broad failure cases use bounded fast memory models; this is not generated-world execution. |
| Production-controller SoC RTL | The same SoC with its actual cache/RAM/flash/VRAM adapters, binary row addresses and physical bank control pins | Full lifecycle, typed assembly/run and heap-backed self-replacement at 96 KiB RAM; delayed pin-level reads and persistent writes on all four fabrics | Bank-cell storage is modeled; redstone cells and full-world timing require separate proof. |
| Real synthesis | Installed Yosys/ABC and Icarus equivalence | ABC option compatibility; DFF/DFFE/SDFF/SDFFE/SDFFCE polarities, reset values, edge behavior and priority; real primitive-bank differential traces | Mapping a netlist does not establish electrical/timing correctness of all routes. |
| Actual redstone fixtures | Pinned MCHPRS redstone engine, emitted blocks and scheduled neighbour/tick updates | Gate/DFF traces; complete clock pulses at all phases; buttons/keys; preloaded compact memory read/write/retention/isolation; small display; representative routed signals/fanout/sequential circuits | Small fixtures do not prove complete-world loading or global clock skew. |
| Native world backend | Java NBT/Anvil files written, reopened and independently decoded | Exact states, coordinate/height checks, conflicting-write rejection, save/readback integrity, corruption detection and a saved-block runtime fixture | File fidelity is not proof of OS execution in that save. |

The integrated lifecycle scenario is a behavioral integration test, not a
pre-recorded UI animation. It drives the actual input path and verifies data
written by the filesystem across shutdown/reboot. Its recorded successful run
with the production adapters required **8,604,079 testbench clock cycles** at
96 KiB RAM. Observed write commits were 18,188 cache, 39,485 RAM, 1,447 flash
and 180,420 VRAM, with delayed reads covered on every fabric. Those are simulator
transactions and cycles, not Minecraft speed. Subsequent revisions must rerun
the scenario rather than inheriting that result.

The same production-controller backend passes typed assembly/save/run in
**9,644,941 cycles**. A separate **5,137,109-cycle** case allocates the complete
84 KiB generic heap, writes a new program into its RUN pages, self-replaces,
executes the marker, compares both code copies and checks every released heap
word before returning Home. It directly guards against cleanup erasing the
staged source before the loader reads it.

The 96 KiB map regression executes 49,152-, 49,153- and 65,535-byte source
assembly/save/run flows and the 16,380-byte hardware-assembler output boundary.
It checks every app's workspace/heap exclusion, both ends of the 8 KiB RAM
executable backing, cache flush/clear, kernel/metadata retention and graceful
heap exhaustion. A staged launch from an allocated RUN page must preserve its
code until loading completes, then clear the entire released allocation.

Failed-close modal tests check the safe initial choice, Left/Right selection,
Enter/A confirmation, Escape/B cancellation, unrelated queued keys, shutdown
and a blocked Miner save followed by explicit discard to Explorer. Pixel and
kernel tests verify selection redraw/fences and preserve every VRAM word,
unfinished back frame and drawing setting.
The queued-fault regression places an invalid BLIT after a long draw and before
a pending warning: the old implementation reported readiness without rendering
the notice. Waiting for old work to drain before submitting the current notice
fixes the reproduction; choice changes and cancellation during that wait are
also checked.

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
- All 79 compact-console actuators are operated in the engine. Real stone
  buttons produce continuous 94-tick controller pulses; all 64 matrix keys and
  the dedicated Shift produce 119-tick row/column pulses with at least 101 ticks
  of native overlap. The representative 30-output routed console checks every
  controller/switch plus corner/middle keys and requires at least 80 ticks of
  simultaneous row/column high: one raw period plus local setup/hold.
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
- The local binary decoder checks all 1,024 row addresses. Production memory
  interfaces expose ten address pins instead of 1,024 global row-select pins.
  External escape planning includes the complete decoder/control geometry.
- A mixed cache2/VRAM4/RAM24 column has independent bank/control/read-return
  paths. Its saved fixture reopens **522,568 unique circuit blocks** and passes
  **84 steps / 43,520 redstone ticks**, with native blocks at Y=-60..307 above
  preserved grass. This uses a reduced number of rows per bank; full 1,024-word
  storage and full-address decoding are separately exercised.
- The bounded experimental local router reuses real isolated tracks and matches
  private clock paths without adding blocks. A 14-cell register pipeline passes
  140-redstone-tick cycles; two stacked 8+6-cell tiles pass 180-tick cycles, with
  zero observed register-clock skew, at least 10 ticks of setup and 171 of hold.
  The single-tile 70-tick and stacked 170-tick negative controls lose transfers;
  the latter also reduces observed hold to one tick. These timing-sensitive
  small-circuit tests do not change the production divider or certify arbitrary
  mapped logic.

The router uses a separate endpoint landing plane and reserves stair headroom.
Directional repeaters refresh junctions as well as straight runs. The caller
must supply component obstacles for peripheral ports; an empty obstacle set
cannot prove that an otherwise valid route avoids an existing device. Internal
netlist routing supplies the emitted cell geometry. Dense placement that cannot
be isolated fails explicitly instead of accepting overlapping wires.

Global interconnect now supplies each peripheral's intended access face,
small exact terminal neighborhoods, complete memory-tile/display/console envelopes,
and the selected grass surface's minimum build Y. A stair must leave its native
envelope before rising and cannot enter another tile. These checks operate on
terminal count and tile bounds, without materializing storage billions of times.
The complete 993-signal diagnostic wall has a separate bounded-lead geometry
regression; full-wall electrical behavior is not inferred from that geometry.

The console's 79 activation positions occupy 16×16 X/Z blocks. A separate
literal-geometry test proves a connected player deck, two-block headroom and
four ordinary steps from above-grass supports. The backend bounds are
`origin+(2,-4,0)..origin+(126,39,128)`, with 10,902 emitted placements and 33
occupied chunks at the production alignment. The walking proof does not prove
screen readability: the 320×180 lamp panel still spans 1600×900 physical blocks,
and viewing it coherently from the console remains an explicit release gap.

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
