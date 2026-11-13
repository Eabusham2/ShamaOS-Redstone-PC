# Next complete controller and memory runtime target

The **8 KiB cache adapter** is the selected next integration target. Its
complete current mapping is smaller than the current VRAM adapter, and its
two full 4 KiB banks are the smallest installed memory fabric. Complete
controller placement and structural clock matching now pass as detailed below;
the connected controller-plus-memory runtime remains unverified.

| Complete partition | Installed storage | Native cells | DFFs | Cell-template blocks | Scalar input/output pins | Nets with sinks |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `shama_cache_adapter` | 8 KiB, 2 banks | 1,144 | 115 | 50,844 | 104 / 79 | 1,248 |
| `shama_vram_adapter` | 16 KiB, 4 banks | 1,152 | 116 | 51,132 | 104 / 81 | 1,256 |

The cell block counts exclude routing, port terminals, storage, decoders and
returns. They do not provide a connected-world chunk count. RAM/flash mapped
counts were not recovered for this scoping comparison; their capacities remain
96 KiB and 200 KiB. No capacity, pin or state is removed to fit this target.

## Provenance and complete interface

These are complete `c3315eb35b3d52c1828e0d5c0c93dc0139ebd6e9` CI artifacts,
created by Yosys 0.33 (`2584903a060`) through the production ABC flow in
`synth/redstone.ys`. The cache artifact ID is `14007345466`. The captured
mapped/ZIP hashes, source hashes, all port widths, native cell inventories and
clock sink counts are in `evidence/cache-vram-scope.json`. The memory-adapter
RTL, generated header, mapping script and production lowering source were
independently checked byte-for-byte against that commit. Parsing both small
complete mappings through the production graph loader found no unresolved
constants or ambiguous drivers/pins.

The cache's 183 scalar pins include the original request address/data/strobes,
read/write request, clock/reset, ready/read response, both bank selects, all ten
row-address bits, read enable, write commit, 32 write-data outputs and all 32
selected-word inputs. Its single original clock drives all 115 mapped DFF clock
pins. The seven controller states perform latched direct addressing, read
settling, byte merging, commit, post-commit holding and response. No rotating
row seek is part of this wrapper.

## Required connected proof

The next fixture must keep every mapped controller cell and pin, two complete
1,024-row × 32-bit banks, the real ten-bit binary decoder, local bank/write
gates, and the upward read-return wiring. Use the production cache component's
layer spacing and decoder offset from `memory_specs`/`default_origins`; the
other fabrics remain separate proof targets. Every support begins above the
superflat grass. The controller outputs and selected-word inputs must be wired
to the native bank circuit in one loaded, ticking world. Drive only the original
external request/reset/clock pins; a sampled host-memory response would not
establish this integration.

Acceptance should cover both banks' first/last rows; opposing bit patterns;
aligned byte/halfword lane merges on each side of the 4 KiB boundary;
held-valid consecutive requests; rejection of reads and writes addressed at
`0x2000` or higher; reset suppression of writes without erasing stored bits;
and a quiescent native
snapshot followed by fresh-process continuation. Check all 65,536 stored bits
at transaction boundaries. Clock/data timing must include the complete decoder
and return path and all 115 clock destinations, with a deliberately too-fast
negative case where a timing violation is observable.

The interface has four byte-lane strobes and no transfer-size field. The master
supplies aligned data/strobes; the adapter selects the word using address bits
above bit 1. An operation spanning `0x0fff` to `0x1000` requires two bus
requests. An adapter test must not invent automatic cross-word splitting or
size-based rejection that this interface does not express.

The existing full 4 KiB bank proof uses physical row-select inputs directly.
It proves complete bank storage, writes and native loading, but does not
substitute for the binary-decoder or mapped-controller integration above.

## Reproducible preparation

`fixture.py prepare` checks the pinned complete mapping against the current
memory-adapter sources, reconstructs and audits the complete bounded plan, and
records every source/artifact hash. The exact mapping is included as the
deterministic 23,096-byte `evidence/cache-controller-c331.json.gz`; decompression
must reproduce SHA-256
`d4989124e9dca8b5afa865d954ded3be680b99f0b1e2f472aed6b5ecc42dd468`.
No external download or replacement netlist is needed.

From the repository root:

```sh
PYTHONPATH=src:tests python experiments/memory_controller_runtime/fixture.py prepare --clock-track-padding 128 --output build/cache-controller
PYTHONPATH=src:tests python -m pytest tests/test_memory_controller_runtime_experiment.py
```

Preparation writes a complete logical plan, the 8 KiB initial image, cold and
continuation request plans, the exact native cache geometry/access contract,
and explicit false geometry/runtime acceptance flags. It preserves 21 leaf
clusters, 12 bounded router domains, 1,484 signal-tree links and all 115 clock
leaves. The controller ceiling is Y=285; Y=314/318 remain reserved for the
external memory routes. If a complete child cannot stack within that height,
the existing placer must move it horizontally or report a real placement
failure. It must not discard circuitry or change the superflat grass.

After reserving the exclusive large-job slot, the separate placement phase is:

```sh
PYTHONPATH=src:tests python experiments/memory_controller_runtime/fixture.py place-controller --output build/cache-controller
```

This phase uses the unchanged bounded placer, all isolation/face guards and
clock matching. It retains per-cluster progress and any failure. A successful
placement saves the shared `shama-complete-subtree-geometry-v1` manifest with
all native modules, contacts, physical ports, chosen escapes, repeater
overrides and clock metadata. Reopening checks the independently recorded
manifest hash and all preparation bindings before replaying those exact
decisions without another route search. Structural placement success does
not imply complete interconnect, native-world loading or timing success.

## Request and persistence oracle

The scenario generator drives exactly the 72 original request/clock/reset
input bits. All 32 `selected_word` inputs are connected to physical memory
returns; they are never externally driven. The 78-net interface follows
`memory_fabric_nets`, including its receiving-hub `wired_or=True` convention
for each return bit. The decoder remains at the cache's actual X offset −170,
with two full banks at the original 12-block layer pitch.

| Prepared sequence | Frames | Rising clock edges | Complete storage boundaries | Storage-bit assertions |
| --- | ---: | ---: | ---: | ---: |
| Cold reset, requests and quiescence | 595 | 248 | 47 | 3,080,192 |
| Fresh-process continuation | 54 | 22 | 6 | 393,216 |
| Combined | 649 | 270 | 53 | 3,473,408 |

Every boundary includes all 2,048 physical words. Cases include all 16 byte
masks, both banks' first/last words, opposing patterns, held-valid requests,
separate boundary-spanning requests, and rejected reads/writes at and above
8 KiB. Clearing all host drive pads before the snapshot leaves the physical
controller idle with its clock low, so the fresh runner can validate and reuse
the original input locations.

Reset expectations distinguish a read that has not reached its sample from
a write whose complete native commit pulse already arrived. The first must
leave memory unchanged. The second first proves the intended word was written,
then asserts reset concurrently with the next clock edge and rejects any
wrong-bank/row mutation. Native locked-repeater storage writes while its local
select is high; it cannot use an abstract positive-edge-only commit count as
the late-reset oracle. Every other stored bit remains checked in either case.

The independent actual-RTL auditor in
`experiments/partition_planner/audit_cache_frames.py` checks the generated
expectations and all complete-memory boundaries using an explicitly scoped
level-sensitive abstract store. Its swapped-read-bank and swapped-write-bank
controls fail at the expected observations. This validates the request oracle
and ABI; physical decoder/reset timing and fresh-process persistence remain
the native-engine gates described above.

## Trace budget and remaining native gates

The verified sparse runner now accepts an optional `trace_outputs` selection
while retaining every declared output and every boundary assertion. The
fixture computes `sum(step ticks) × selected output count` before writing a
runtime scenario and enforces an explicit sample budget (20 million by
default). Initial settling ticks are not captured. Cold and continuation runs
use 555 and 56 half-period units respectively; no physical half-period value
has yet been accepted for this connected fixture.

Full observation of all 115 physical register clocks belongs in a short
dedicated timing probe. Long functional runs can record a bounded set of
controller controls or disable stored traces while retaining all memory
assertions. The report records the exact chosen trace set and sample count.
This limit is not a process-memory estimate: retained boolean vectors,
`serde_json::Value`, final serialization, and the complete native world require
separate whole-process peak-RSS measurement.

The complete controller now places with all 1,144 cells, 183 original pins,
115 registers and 1,484 shared contacts retained. A required clock-space fix
reserves 128 actual blocks of branch length before geometry and parent packing
are checked. The unchanged matcher then matches all 115 planned clock paths;
this result is **not** a native timing or functional-runtime acceptance.

The zero-padding trial exposed a real capacity defect: one leaf register had
only 34 extra delay ticks available but needed 53; another needed 76. A
64-block reserve passed that leaf's seven-clock native pulse test, but a parent
branch still needed 90 more delay ticks than it could hold. The accepted
128-block placement contains 10,607,088 unique blocks, spans
X=0..6,486 / Y=-60..199 / Z=0..2,958, and reports a planned common clock delay
of 2,642 redstone ticks. Actual clock rate and complete connected cache speed
remain unmeasured. This is added physical timing capacity, not a footprint
reduction. The wrapper changes no cell, port, net, escape or track identity,
and its complete source is included in the preparation hash inventory.

The complete controller has also passed exact disk-backed block union, native
world generation, and independent saved-world readback: **10,607,088 unique
blocks in 44,370 occupied chunks across 56 region files**. Grass is preserved
at Y=-61 and all circuit supports begin at Y=-60. The region files occupy
182,198,272 bytes. Generation plus readback took 833.50 seconds in this
environment. These figures cover the controller alone; the two cache banks,
their external routes, and the rest of the computer are additional. The
[summary](evidence/cache-controller-native-summary.json), full source-bound
[build report](evidence/cache-controller-native-build.json), and
[world inventory](evidence/cache-controller-native-world-inventory.json) retain
the exact graph, literal-union and saved-file digests.

`probe_controller.py` saves and reopens that exact complete controller, then
measures every register clock in the native engine with an ordinary and a
deliberately too-fast source clock. It is a controller-only gate, with no
host-supplied memory responses or claim of connected-cache operation:

```sh
PYTHONPATH=src:tests python -m experiments.memory_controller_runtime.probe_controller build/cache-controller build/cache-controller-native "$SHAMA_MCHPRS_TERRAIN_RUNNER"
```

All 78 external routes, exact connected native save/readback, real decoder/bank
runtime, full clock-arrival timing and a meaningful too-fast negative remain
pending until their respective reports actually pass. The first complete
straight-corridor audit found 87 of the original controller's 183 south-facing
I/O paths obstructed; placing the controller does not make those original pins
externally accessible. Real export wires must preserve every original pin.
Quiescent engine state must then be exported
to a native save and reimported into a fresh process before continuation is
accepted. The prepared fixture and its tests do not establish a complete
machine boot or a connected-world chunk count.
