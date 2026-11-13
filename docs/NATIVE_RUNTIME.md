# Native world loading and actual redstone execution

The experimental native-world runner loads a saved Java 1.20.4 world into one
connected, always-ticking MCHPRS `World`. It streams every saved Anvil section,
including terrain, and processes the pinned engine's real redstone updates and
scheduled ticks. This gives the project a working import and execution path for
saved fixtures. Complete-machine boot and application acceptance remain pending
the complete connected physical build and its timing tests.

The production capacities remain **200 KiB flash, 96 KiB RAM, 8 KiB cache and
16 KiB VRAM**. The small fixtures below test loading and circuit behavior; they
do not replace or reduce those capacities.

## Why the stock MCHPRS server needs a separate loading audit

The pinned upstream revision is
`f9e10d2572116bf5dcb9a36a305f28e40641a61a`. Its production server uses a
`PlotWorld` containing 32×32 chunks, or **512×512 blocks**, and 24 vertical
sections. Reads outside that plot return air and writes outside it are ignored.
Placing a connected machine across separate stock plots therefore does not
establish working connections between those plots.

The stock update loop ticks a plot only while a player is inside it. Its
`always_running` setting prevents the ten-minute unload, but the update loop
still stops ticking when the plot has no players. Faster `/rtps` settings,
larger client view distance and keeping a plot allocated do not fix these two
conditions. These statements refer to this exact pinned source, not every
MCHPRS release or custom server configuration.

Source: upstream [`PlotWorld` constants, block access and update loop](https://github.com/MCHPR/MCHPRS/blob/f9e10d2572116bf5dcb9a36a305f28e40641a61a/crates/core/src/plot/mod.rs).

## What the sparse runner does

`python -m shamaos.mchprs_import WORLD OUTPUT.jsonl` independently decodes the
saved `level.dat` and every saved region file. It exports all 24 height sections
for every saved chunk, including all non-air terrain, native block properties
and comparator block entities. Region identity, source hashes and section counts
are recorded. A complete stream is published only after the source files and
region inventory still match their initial snapshots. A source change leaves
an explicitly incomplete `.partial` stream.

The default v2 stream describes a uniform section with its exact block state
and a checked count of either zero or 4,096 non-air blocks. Every height section
still has its own explicit record. Mixed sections retain native palettes and
packed indices. The runner also accepts the original v1 format; the exporter's
`--legacy-sections` option writes its exact original representation while
reusing the all-air packed array. A 257/300-state palette test verifies the
conversion needed when native local palettes exceed the engine's eight-bit
palette limit.

The Rust runner stores actual `mchprs_world::storage::Chunk` objects in a map
keyed by signed chunk coordinates. Empty distances from the origin do not
allocate a rectangle of chunks. Every decoded chunk stays loaded throughout the
scenario, regardless of player distance; every scheduled tick runs in one
global queue. Native Y −64…319 is translated consistently by +64 for the engine's
0…383 storage. Native X and Z, including negative coordinates, are preserved.
Unwritten superflat terrain around saved chunks is represented by the declared
flat background, without inventing extra circuitry.

All circuit behavior comes from the pinned `mchprs_redstone` implementation.
The tick queue keeps its stable delay/priority ordering, decrement rules,
duplicate-tick behavior and actual block-update calls. It has not been replaced
with Boolean gates or an instruction emulator. The current queue retains the
upstream linear `remove(0)` behavior; large pending queues need profiling before
any performance claim or queue redesign.

The imported-byte and scenario SHA-256 values are computed from the exact byte
streams consumed by the parser. This uses the standard `sha256sum` command with
bounded pipes and does not retain another whole JSON copy. Linux CI supplies
that command. The output also records the engine revision, any registry patch,
source hashes, loaded chunks, decoded/initialized blocks, scheduled ticks,
elapsed times and peak process memory.

The in-process memory sample occurs after assembling the final report and
before writing it to stdout. Complete-process measurements below instead use
an external parent's kernel `wait4` resource data after process exit, including
final trace serialization. The workspace did not provide `/usr/bin/time`;
the benchmark records the equivalent kernel resource measurement explicitly.

The scenario uses real `use_blocks` actions for buttons and levers. Bounded
interface tests may use named external power pads, which must initially be air
and must not overlap each other or overwrite saved circuit blocks. Observable
outputs and actuators must be actual saved non-air blocks; all coordinates must
fit the native world bounds. Empty or unobserved scenarios fail. A scenario with
real tick traces but no assertions reports `executed-trace-only`, so an external
differential checker can compare it without claiming an asserted pass.

Current scope is newly generated native worlds with empty saved block/fluid
tick queues. A played-world save containing pending native ticks is rejected
until an explicit conversion preserves those ticks. Unsupported states,
properties, malformed palettes, incomplete sections, duplicate sections,
changed source files and configured chunk-limit overruns also fail explicitly.
They are never silently omitted. The runner always reports
`complete_machine_runtime_verified: false` for this experimental path.

## Exact superflat terrain registration

The unmodified pinned engine recognizes the generated circuit blocks but does
not register bedrock, dirt or grass. Its unknown raw IDs decode as air, which is
unsuitable for an exact superflat import. The normal sparse runner therefore
rejects those blocks explicitly.

The separate terrain build adds only the three missing block types, using the
native IDs and properties in the pinned engine's bundled official Java 1.20.4
registry:

| Block state | Native state ID |
|---|---:|
| Bedrock | 79 |
| Dirt | 10 |
| Grass, `snowy=true` | 8 |
| Grass, `snowy=false` | 9 |

These are opaque full cubes with normal solid-block redstone behavior. Every
pre-existing implemented state retains its name, ID, properties and material
classification. The registry test compares all **3,327** pre-existing states
between the two compiled engines. Every redstone implementation file is
unchanged. The audited patch, 28-file upstream source manifest and upstream MIT
license are retained in `tests/mchprs_sparse_runtime/`; the preparer verifies
those hashes before building a private engine copy outside the source tree.

Source: upstream [official block-state report](https://github.com/MCHPR/MCHPRS/blob/f9e10d2572116bf5dcb9a36a305f28e40641a61a/mc_data/blocks.json),
[implemented-block registry](https://github.com/MCHPR/MCHPRS/blob/f9e10d2572116bf5dcb9a36a305f28e40641a61a/mc_data/gen_info.yaml),
and [generated block implementation](https://github.com/MCHPR/MCHPRS/blob/f9e10d2572116bf5dcb9a36a305f28e40641a61a/crates/blocks/src/generated.rs).

## Measured saved-fixture evidence

The 2026-11-13 prototype produced these measurements. Native block counts
include saved spawn support and, where specified, the complete saved terrain.
Peak RSS is the externally measured complete spawned-process high-water mark
for these fixtures, not a full-machine estimate. The smallest fixture's total
includes process-launch overhead. Exact hashes, timings, engine patch and scope
are preserved in the [compact checkpoint evidence](evidence/native-runtime-2026-11-13.json).

| Saved fixture | Registry | Loaded chunks | Native non-air blocks | Redstone ticks | Approx. peak RSS |
|---|---|---:|---:|---:|---:|
| Two logic circuits with actual levers | Unmodified | 4 | 101 | 96 | 9.6 MiB |
| Colored console, all 79 actuators | Unmodified | 42 | 13,087 | 23,628 | 27.2 MiB |
| Same console, X−1,000,000/Z+1,000,000 relocation | Unmodified | 42 | 13,087 | 23,628 | 27.3 MiB |
| Upright 8×4 panel, all lamps checked | Unmodified | 8 | 1,451 | 26,624 | 28.3 MiB |
| Three stacked 2-word×32-bit banks, with terrain | Terrain extension | 50 | 72,047 | 6,656 | 52.2 MiB |
| Connected seven-domain sequential fixture, 512-RT half-period | Terrain extension | 90 | 105,336 | 27,648 | 32.6 MiB |
| Same connected fixture, intentionally too-fast 20-RT half-period | Terrain extension | 90 | 105,336 | 11,904 | 18.6 MiB |
| Complete 1,024-row×32-bit bank, with terrain | Terrain extension | 4,630 | 6,732,672 | 81,920 | 351.5 MiB |

The first logic/console/panel imports matched **1,524,328** output tick values
against the existing dense actual-engine harness. The terrain memory import
matched another **1,433,600** tick values against that same dense harness built
with the explicit terrain registry. The distant console preserved all 705,000
of its original output tick values without allocating the empty intervening
coordinate range.

The connected fixture's seven routing domains preserve 18 real parent/child handoffs, original ports,
three constants and two DFFs. Its native positive replay matches all 745,472
dense trace values. The deliberately too-fast replay matches all 304,640 values,
including the eight incorrect pipeline transfers observed by the timing test.
Matching that negative behavior verifies that import did not hide the timing
failure. External driver pads materialize two additional chunks during these two
scenarios.

The complete 4 KiB bank saves 1,991,552 circuit blocks, reopens all 13 native
region files, and imports every saved terrain block plus 32,768 native comparator
entities. Reads and writes exercise the first, middle and last addresses; all
32,768 stored bits are checked after each phase. Its **623,008 assertions** pass
over 81,920 redstone ticks with 4,096-tick local settling phases. The compact and
legacy streams preserve all 111,120 section states and all comparator records,
and both runs execute the same 166,946 scheduled ticks. One external driver-pad
chunk is added during the run. This is a complete bank proof, with whole-machine
timing still an independent gate.

## Preparation and stream-size improvements

The full-bank save and independent native readback took 37.92 seconds in this
workspace. Optimizing empty-section export produced the following measurements:

| Export format | Preparation time | Stream bytes | Exactness check |
|---|---:|---:|---|
| Original v1 writer | 148.09 s | 92,647,448 | Baseline native sections |
| Cached all-air v1 writer | 16.42 s | 92,647,448 | Every byte and SHA-256 unchanged |
| v2 explicit uniform sections | 15.72 s | 36,633,708 | Every imported state and comparator unchanged |

The v2 result is **9.42× faster preparation and 60.46% fewer stream bytes**.
All 24 sections per chunk remain explicit; 106,490 uniform-air sections avoid
repeated arrays of zero indices. The compact full-bank run took 17.42 seconds
including import, initialization, execution and final output: approximately
1.51 seconds for import, 11.62 for native initialization and 4.18 for scheduled
redstone execution. These preparation/import gains do not shorten the circuit's
required redstone settling time or change the simulation queue.

The focused runtime regression has 39 passing Python tests with no skips and two Rust
storage/encoding tests. The original and compact formats are both exercised,
including a uniform non-air section, malformed counts/palettes and invalid
scenario evidence.

The portable benchmark has a separate 31-test guard suite. It rejects disabled
assertions (`python -O`), unsupported memory-accounting platforms, stale or
unverified cached worlds, changed generator sources and changed initial images.
The exact region inventory and file hashes must still match before reuse, and
the exported source hashes must match the previously validated native bank.

The behavioral CI job builds both registry variants, runs the native-import and
scheduled-tick regression without skips, and archives compact reports under
`build/validation`. It also archives the exact private-engine patch/provenance.
Large temporary saved worlds and raw trace files stay outside git.

## Memory bounds and Redpiler

Pinned `ChunkSection` storage starts with 2,048 bytes of packed data for each
4,096-block section. Twenty-four sections therefore require at least 49,152
packed bytes per allocated chunk, before palettes, objects, block entities or
updates. A changed section can allocate another 8,192-byte mutation overlay.

For the published 200 KiB checkpoint's 23,328 regular occupied chunks, the packed
baseline alone is **1,146,617,856 bytes**. Allocating all 86,000 chunks of its
rectangular envelope would instead start at **4,227,072,000 bytes**. These are
source-derived storage lower bounds; synthesized logic, global routes, active
state, tick queues and compiler graphs are additional. The complete connected
world has not yet been measured in this runner.

Source: upstream [chunk/section storage](https://github.com/MCHPR/MCHPRS/blob/f9e10d2572116bf5dcb9a36a305f28e40641a61a/crates/world/src/storage.rs).

Redpiler exposes compilation, tick, actual button/lever-use, flush and reset
operations over a `World`. Its current node discovery scans chunk sections over
one bounding rectangle, so sparse allocation does not remove the cost of a vast
compilation envelope. Compiler graphs and connections across routing subdivisions
also need an independent memory and correctness audit. The local offline probe
could not build Redpiler because its `enum_dispatch` dependency was not cached;
no compiled-Redpiler performance or full-world result is claimed here.

Source: upstream [compiler API](https://github.com/MCHPR/MCHPRS/blob/f9e10d2572116bf5dcb9a36a305f28e40641a61a/crates/redpiler/src/lib.rs)
and [world iteration](https://github.com/MCHPR/MCHPRS/blob/f9e10d2572116bf5dcb9a36a305f28e40641a61a/crates/world/src/lib.rs).

## Reproducing an import

Build the ordinary sparse harness for worlds containing only the pinned
registry's supported blocks:

```sh
cargo build --locked --release --manifest-path tests/mchprs_sparse_runtime/Cargo.toml
python -m shamaos.mchprs_import /path/to/native-world build/native-world.jsonl
tests/mchprs_sparse_runtime/target/release/shama-sparse-world-check \
  build/native-world.jsonl /path/to/native-coordinate-scenario.json
```

For an exact superflat import, prepare the isolated terrain build from the
unchanged pinned MCHPRS checkout. `--build` uses already cached dependencies and
records the patch hash in the executable. CI obtains that checkout from Cargo's
resolved dependency metadata.

```sh
python tools/prepare_mchprs_terrain_registry.py \
  --source /path/to/pinned-MCHPRS-checkout \
  --output build/mchprs-terrain --build
```

Reproduce both full-bank formats, exact-state comparisons, actual-engine runs
and external process-memory measurements with the portable benchmark. Use an
untracked working directory; the complete saved world and raw results stay
there, while `benchmark-report.json` records the compact evidence. This memory
benchmark uses the POSIX `wait4` interface on Linux or macOS, normalizes peak RSS
to KiB, and records the platform plus original kernel value and units. Cached
preparation is accepted only while all source, image and native-world hashes
match; use a fresh output directory when those inputs change.

```sh
PYTHONPATH=src:tests python tools/benchmark_mchprs_native_bank.py \
  --output build/native-bank-benchmark \
  --runner /path/to/shama-sparse-world-check-terrain
```

The runner accepts a third argument limiting loaded chunks and fails before
exceeding it. Raising that limit is a memory-budget choice, not a way to bypass
capacity or verification. The runtime scenario must address native coordinates;
the runner applies the fixed Y translation internally.
