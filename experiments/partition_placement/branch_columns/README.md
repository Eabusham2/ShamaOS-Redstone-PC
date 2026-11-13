# Isolated unit-step branch-column experiment

This experiment changes one occupied-column increment in `_plan_escapes` from
four blocks to one. The existing two-block exclusion on both sides of a used
column remains unchanged, as do every stair, electrical adjacency, climbing
headroom, component envelope and height check. Production source and defaults
are unchanged. The snapshots are loaded only into a separate proof or pytest
process.

The change matters when the original starting residue skips a legal gap. With
reserved columns `0, 8` and initial candidate `2`, the original loop visits
`2, 6, 10, 14`; the unit-step loop selects the already legal column `3`. The
standalone proof checks this using the actual escape allocator, including a
deterministic repeat and the unchanged height rejection.

## Retained results

| Gate | Result |
|---|---|
| Unchanged placement, generated-clock, local/external routing and real mapped-cut regressions; only column selection rebound | 67 passed, 0 failures, 0 skips |
| Unchanged placement/generated-clock regressions with the measured-apron snapshot as well | 45 passed, 0 failures, 0 skips |
| First batch external process measurement | 30.56 s; 194,332 KiB peak RSS |
| Combined placement batch external process measurement | 7.84 s; 69,036 KiB peak RSS |
| Complete small measured-apron circuit | 10,340 → 10,224 unique blocks |
| Small circuit occupied chunks | 74 → 74; bounds unchanged |
| Actual engine, 512 RT half-period | All 16 pipeline cycles correct; 0 RT clock skew |
| Actual engine, 20 RT half-period | Eight incorrect register transfers detected; 0 RT clock skew |

The 116-block reduction is about 1.12% for this small circuit. It establishes
neither a chunk reduction nor a runtime speed increase. Complete-machine routing
and timing remain unverified. The invalid timing witness deliberately remains
invalid; the retained trace contains every observed signal.

`evidence/` retains the two JUnit results, external kernel resource measurements,
small proof summary, both actual-engine traces, the original recording driver,
and the parent-221 reservation arithmetic. `provenance.json` records the exact
source and dependency hashes. The router snapshot differs from its recorded
baseline only at the occupied-column increment. The placement snapshot is the
separately audited raised-export candidate with bounded measured-apron retries.

The parent-221 arithmetic reconstructs all **896 direct terminals** from the
unchanged complete plan and measured child export planes. The reserved X span
falls from 3,591 to 3,266 for compact packing, or 4,799 to 3,575 for row packing.
These are column-selection calculations: no escape paths or literal blocks were
generated for that comparison, so no larger footprint claim follows. Compact
packing also requires roughly 3.75 times as many reservation-loop iterations.

## Reproduce from the repository root

Use the pinned actual-engine runner already required by the repository tests,
with Yosys and Icarus on `PATH` for the unchanged regressions:

```bash
export PYTHONPATH=src:tests
export SHAMA_MCHPRS_RUNNER=/absolute/path/to/shama-redstone-check
python experiments/partition_placement/branch_columns/probe_unit_step.py
```

The portable proof writes to `build/branch-column-proof` by default. Passing
`--geometry-only` reproduces the exact block counts without making a runtime
acceptance claim. The original mapped fixture is retained under `fixtures/`.

Run the complete 67-case batch with only the column change:

```bash
python experiments/partition_placement/branch_columns/run_candidate_pytest.py -q \
  tests/test_partition_placement.py tests/test_partition_generated_clock.py \
  tests/test_local_router.py tests/test_external_router.py tests/test_routing_partition.py \
  tests/test_mchprs_runtime.py::test_actual_external_routes_keep_four_nearby_signals_independent \
  tests/test_mchprs_runtime.py::test_actual_external_route_fans_out_in_both_directions_and_across_heights \
  tests/test_mchprs_runtime.py::test_actual_router_honors_forced_west_escapes_for_source_and_sink
```

Run the 45-case combination with the measured-apron snapshot:

```bash
python experiments/partition_placement/branch_columns/run_candidate_pytest.py \
  --measured-apron -q tests/test_partition_placement.py tests/test_partition_generated_clock.py
```

The acceptance scripts explicitly reject optimized Python (`-O`/`-OO`). The
environment and source hashes should match the recorded checkpoint before
comparing the retained results.

## Remaining gate before promotion

The allocator fixes a candidate X column before trying its Z escape lanes.
Selecting an earlier legal X can therefore encounter different downstream
geometry, even though its column spacing is valid. None of the unchanged tests
above exposed such a counterexample. A deterministic original-step fallback may
still be necessary; any counterexample must be retained without relaxing a
geometry guard. Compare the same complete parent-177 subtree with all original
pins, contacts and full emitted-state checks before proposing a default change.
