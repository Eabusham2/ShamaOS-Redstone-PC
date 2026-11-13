# Routing progress and remaining whole-machine gates

The bounded routing path now preserves and exercises a real sequential cut of
the mapped assembler across three physical tiers. A separate
[complete-graph planner](BOUNDED_ROUTING_PLAN.md) assigns every cell and signal
in complete mapped bridge partitions to bounded leaf/router domains. The
current-effective assembler also has an independently reconstructed complete
logical summary, with its full route records still unretained.
**The physical hierarchy is still experimental
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

The recovered optimized assembler mapping from green CI source `4e933a5` has
SHA-256 `78a6ab3abcbbe4bde94acaf6bbfdb8388dbeebf301757f34c49172f97b1664ee`.
Its effective assembler RTL, including the 96 KiB RAM macro, is byte-identical
to the current source after preprocessing. The
[retained provenance and resource report](../experiments/partition_planner/evidence/asm-effective-rtl-provenance.json)
gives the following bounds for that exact mapped artifact and the unchanged
production routing topology:

| Measurement | Value |
|---|---:|
| Lowered physical cells | 559,453 |
| Routed nets | 559,617 |
| Endpoint uses | 1,455,315 |
| Guaranteed distinct straight branch wires | 1,744,156,445,483 |
| Minimum X/Z route chunks | 18,168,296,308 |
| Minimum Anvil region bytes | 74,562,688,049,152 |
| Lower bound on the slowest individual net | 452,839 redstone ticks |

These figures exclude trunks, stairs, logic cells and compression beyond the
minimum chunk sectors. They are not the exact finished footprint or a total
execution-time prediction. They are specific to this retained 559,453-cell
mapping; another tool run can produce a different native mapping from the same
effective source. The older historical `build/full-asm-fixed.json` artifact
(`1865d54e9b17f9961a41e5e7e144e862645f8d75bb49447a23e3f0542805d860`) has
737,585 lowered cells and a 31,239,260,074-chunk route lower bound. That older
mapping supplies the frozen sequential cut below, but is no longer the baseline
for current-effective-assembler grouping measurements.

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

The earlier bounded cut emitter has one parent tier over explicitly selected
small groups. The new complete-graph planner supplies a deterministic fixed-arity
tree, bounded domains and complete structural conservation. The separate
recursive placement implementation now has a connected two-level pipeline proof:
all three native cells, six original ports, three constants and 18 shared contacts
are retained in 12 emitted modules. Its passing and failing actual-engine timing
cases, exact fixture dimensions and independent endpoint/block reconstruction are
described in [BOUNDED_ROUTING_PLAN.md](BOUNDED_ROUTING_PLAN.md#connected-physical-proof-and-its-limits).

This establishes a multi-level mechanism on a small complete fixture. Complete
bridge/assembler routing, a closed full-chip clock tree and a measured complete
loaded/ticking arrangement remain open. The production generator is unchanged
and continues to report incomplete routing/runtime gates.

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
