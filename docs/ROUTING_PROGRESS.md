# Routing progress and remaining whole-machine gates

The bounded routing path now preserves and exercises a real sequential cut of
the mapped assembler across three physical tiers. **It is still experimental
and is not selected by the complete-machine generator.** The new checks do not
establish a complete computer footprint, a safe global clock or a loaded world
that boots the OS.

This report supplements [REQUIREMENTS.md](REQUIREMENTS.md), especially H11–H12
and V05–V09, and the [full-world acceptance gates](VALIDATION.md#full-world-acceptance-still-required).

## Why the production router remains blocked

[`PhysicalNetlist`](../src/shamaos/synthesis.py) computes an interval-coloring
estimate but its production emitter deliberately gives every net a separate
global Z track. Every endpoint also receives an isolated X branch. The first
track is placed north of all endpoints by the complete track count plus a
4,096-block apron. Large partitions therefore pay long branch costs for almost
every signal, even when its driver and sink are logically close.

The restored historical assembler artifact `build/full-asm-fixed.json` has
SHA-256 `1865d54e9b17f9961a41e5e7e144e862645f8d75bb49447a23e3f0542805d860`.
Its retained analytical report gives the following bounds for that specific
artifact and production routing topology:

| Measurement | Value |
|---|---:|
| Lowered physical cells | 737,585 |
| Routed nets | 737,749 |
| Endpoint uses | 1,914,510 |
| Guaranteed distinct straight branch wires | 2,998,968,967,094 |
| Minimum X/Z route chunks | 31,239,260,074 |
| Minimum Anvil region bytes | 128,205,923,344,384 |
| Lower bound on the slowest individual net | 596,112 redstone ticks |

These figures exclude trunks, stairs, logic cells and compression beyond the
minimum chunk sectors. They are not the exact finished footprint or a total
execution-time prediction. They also do **not** describe the later optimized
assembler mapping: the restored artifact contains 682,079 raw mapped cells and
17,902 sequential variants. The documented optimized mapping must be measured
from its own artifact before applying an updated full-partition bound.

[`resource_accounting.py`](../src/shamaos/resource_accounting.py) makes this
distinction explicit and the generator rejects an insufficient proven disk
budget before emitting a world. Merely increasing tick rate or render distance
cannot remove the structural routing cost or prove clock/hold correctness.

## Bounded hierarchy changes

[`local_router.py`](../src/shamaos/hardware/local_router.py) already supports
small connected placements, real interval-track reuse, separate child tiers
and private-branch repeater delay matching. This update addresses three limits
found while expanding beyond the previous two-register demonstration:

1. **Parent interface clearance.** The previous fixed parent strip at
   `origin.z + 120` could trap a larger interface between child envelopes and
   already allocated endpoint stairs. The strip now reserves a six-block lane
   per root terminal beyond the measured child front, with a bounded margin.
   The existing small demonstration retains its previous strip position.
2. **Explicit boundary budgets.** Every child and the complete parent tier must
   fit the caller's terminal budget, which defaults to 256. An oversized parent
   fails instead of silently recreating an unbounded global routing domain.
3. **Structural conservation.** The hierarchy resolves its boundary links back
   to the original driver/sink identities. It checks every native cell, every
   signal sink including clock leaves, original port names and bus widths, and
   constant-one terminals. Dropped clock leaves, missing cells, rewired inputs
   and shortened ports are negative regression cases.

An internally generated clock consumed both inside its source tile and in
another tile is rejected. That topology does not share the imported-clock root
assumed by the current parent-tail matching algorithm. A future clock-tree
implementation must handle every such leaf explicitly; the small imported-clock
proof cannot be applied to it by analogy.

The new [`mapped_cell_cut`](../src/shamaos/hardware/netlist_cut.py) helper retains
selected mapped cells unchanged, exposes every incoming signal and every output
used outside the cut, and records the original cell/pin and port/bit identities.
It keeps constants and rejects unknown values instead of grounding them. It
does not delete outside consumers from the evidence to obtain a smaller example.

## Real mapped sequential cut

[`asm_state_cut.json`](../tests/fixtures/asm_state_cut.json) retains two original
state registers from the historical assembler mapping and a bounded input cone.
The fixture records its source artifact hash and complete boundary consumers.
It contains 40 unchanged raw cells: 28 ANDs, ten inverters and two synchronous
reset flops. Normal sequential lowering produces 47 physical cells.

The experiment places these cells in groups of **16, 16 and 15**, with the two
registers deliberately on different tiers. All 30 input signals, seven output
signals and 88 driver-to-sink uses are preserved. The complete hierarchy emits
101,818 placement operations and 100,678 unique blocks in **643 occupied X/Z
chunks**. Its occupied bounds are X=128–746, Y=-60–25 and Z=934–1310. Thus every
support and route stays above the default superflat grass at Y=-61 and below
the world height ceiling.

Those are the dimensions of **this cut only**. The hierarchy is larger than
the corresponding single-tile experiment because its boundary and clock routes
are additional work. Stacking by itself is not evidence of lower total chunk
usage, and these figures must not be multiplied into a claimed whole-machine
footprint.

### Actual-engine timing evidence

[`test_routing_partition.py`](../tests/test_routing_partition.py) runs the emitted
blocks in the pinned MCHPRS engine. The engine harness uses a finite plot with
nonnegative coordinates, so every emitted block and observed/driven terminal
is translated by the same +64 Y offset. The original above-grass bounds are
checked separately; the circuit geometry is unchanged.

The independent cycle oracle evaluates the original mapped AND/inverter and
active-low synchronous-reset flop semantics. It does not reuse the physical
reset-mux lowering to calculate expected results. The test drives 32 boundary
vectors and checks 96 clock edges at both actual register outputs, 12 ticks
after each measured register-clock edge. It also checks all seven exported
boundary outputs after every vector, for 224 additional output samples.

| Measurement | Passing timing | Deliberately too fast |
|---|---:|---:|
| Clock half-period | 400 redstone ticks | 100 redstone ticks |
| Actual engine ticks | 128,256 | 61,056 |
| Register clock skew observed | 0 ticks | 0 ticks |
| Minimum observed local setup | 117 ticks | 17 ticks |
| Minimum observed local hold | 1,853 ticks | 58 ticks |
| Sampled register transfers | 192 | 192 |
| Incorrect register transfers | **0** | **26** |
| Incorrect settled boundary outputs | **0 of 224** | **0 of 224** |

Every clock pulse has the requested complete width in both runs. The negative
case still has local pin setup/hold margins above the DFF minimum, but some
intended input data has not finished traveling through the longer boundary path.
This is why a clock-skew number and stable local D pin alone cannot establish
system timing. The cycle's intended data must also reach the correct register
before its clock edge. Increasing the clock period without checking minimum
paths and hold behavior is likewise insufficient.

The ten new tests pass alongside all seven existing local-router tests, which
include the retained 64-cell combinational assembler cut and positive/negative
two-register pipeline timing tests. These are bounded regression results,
not full assembler or full-machine acceptance.

## Smallest credible route to production

The next production step is a bounded, complete partition tree, rather than
switching the existing flat global emitter to the experimental API:

| Required step | Acceptance evidence |
|---|---|
| Partition the exact lowered candidate graph with explicit cell and boundary budgets | Every cell owned once; complete driver, sink, constant, bus and clock identities preserved. High-fanout data and clock nets remain represented. |
| Build bounded parent domains repeatedly | Every tier fits its terminal budget. Child interfaces include measured envelope/escape clearance. Parent domains cannot collapse back into one all-chip routing plane. |
| Place additional groups horizontally when vertical space runs out | All emitted supports and routes stay above grass and within Y≤319; increasing layers cannot remove the reserved upper routing space. |
| Distribute clock and high-fanout signals through explicit trees | Every original leaf has a complete path, pulse-width evidence and route-derived early/late arrival bounds; internally generated clocks are included. |
| Close data and clock timing together | Correct cycle-by-cycle transfers across representative long and short paths, reset/enable transitions, positive tests and failing speed/hold controls. |
| Emit and independently reopen the whole candidate | No conflicting placements, exact capacities and connectivity, measured complete route footprint and disk cost. |
| Establish a loaded/ticking-world plan | Measure the exact world's loaded set, host memory/disk usage and runtime settings before claiming a usable computer. |

The current implementation only has one parent tier over explicitly selected
small groups. It has no complete graph partitioner, no repeated bounded parent
domains, no closed full-chip clock tree and no measured complete loaded/ticking
arrangement. These remain real engineering tasks. The production generator is
therefore unchanged and continues to report incomplete routing/runtime gates.

## Full-machine requirement gaps

The remaining blocking evidence is the same regardless of the smaller RCC
capacities or application optimizations:

- Exact-candidate full partition mapping and shell reconnection.
- Complete collision-free, continuous and electrically isolated routing, with
  all clock leaves and actual end-to-end sequential timing closed.
- Complete world generation and independent saved-file readback at the agreed
  capacities, including screen, controls, memory, assembler and all logic.
- A measured loading/ticking arrangement for that world; the existing storage
  preflight explicitly labels loading estimates as assumptions.
- Actual full-world Desktop boot and physical input, create/edit/save/reopen/
  assemble/run, all app lifecycles, native/software Miner agreement and persistent
  history/files across real power cycles.
- Release assets generated from that verified world and exact source revision,
  with checksums and the actual runtime settings.

The filesystem and firmware regressions, physical memory/display/control
fixtures and these routing cuts provide complementary evidence. None supplies
the missing full-world run on its own.
