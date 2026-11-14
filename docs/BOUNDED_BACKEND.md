# Opt-in bounded physical backend

The complete-machine generator now has an explicit bounded logic backend. It
uses the same twelve synthesized modules, mapping choices, and mapped top shell
as the existing backend. Each complete child graph is partitioned and placed
with real exterior contacts, and its original bus vectors are connected through
the existing shell and global routers.

The default remains `flat`. To select the development backend explicitly:

```sh
python -m shamaos generate --world build/bounded-world \
  --config configs/default.toml --logic-backend bounded \
  --manifest build/bounded-generated-manifest.json
```

This command attempts the complete machine and can fail on placement, clock
matching, routing, height, resources, or native-world validation. It does not
publish a release, assert a playable machine, or bypass an acceptance gate.
The full set of twelve bounded partitions has not yet been generated and run
as one saved Minecraft computer.

## Production integration

`system_build._prepare_partitioned_logic` retains the complete synthesis inputs
and exact shell blackbox inventory. Its bounded branch calls
`bounded_system.prepare_from_artifacts`; the flat branch remains unchanged.
The bounded implementation is installable under `src/shamaos` and does not
import experiment modules.

`BoundedPartition` retains every original cell, signal, constant and original
port bit. Its facade is a physical part of the emitted hierarchy, rather than
a renamed map of buried leaf terminals. Shell macro bindings reject missing,
extra or misdirected ports, altered widths, unsupported constants and missing
implementations. The shell preserves original bit order, including intentional
permutations and constant-valued outputs.

Fresh placement requires clock matching and propagates insufficient private
branch capacity as an error. Reused placed objects are checked again: a
metadata-only clone of the complete hierarchy recomputes the required repeater
overrides, and the accepted object must contain exactly that matching. The
check does not mutate the accepted object. Its scope is the original input or
generated clock source through the graph routes to registers; facade input
delivery adds a common prefix, and engine timing remains a separate check.

All components remain above the protected grass surface and inside Minecraft
coordinates and height. Children are positioned using their actual envelopes;
there are no fixed-size partition slots. Shell routing receives each child's
full obstacle volume and opens only the actual exterior terminal faces.

## Resource and global-routing gates

The bounded hierarchy does not expose fictitious flat nets or routing planes
to satisfy the old estimator. `resource_accounting` has an explicit bounded
branch, and generation proceeds in two phases:

1. The normal capacity/storage checks and conservative regular-component,
   shell-route and global-route disk lower bounds run first. A proven disk
   failure stops before whole-logic exact-union measurement or world writing.
   Local placement's necessary collision and isolation checks still run while
   the hierarchy is constructed.
2. If those lower bounds fit, `finalize_prepared_system` streams every bounded
   child, facade and shell block through an external-sort literal union. It
   rejects conflicting states, counts actual occupied chunks and blocks, and
   derives the complete logic envelope. It then recomputes the disk gate with
   those measured values.

The measured envelope, every actual top-level contact, and its support/dust
obstacles are merged into the existing regular-component routing contract.
The global router cannot run on an unmeasured bounded hierarchy. Its escape
paths may enter the logic envelope only at the declared exterior contacts.

Reports retain `whole_machine_exact_chunks: null`: exact logic occupancy is
not the union of logic, all regular components and global wires. The disk
minimum uses the maximum of applicable overlapping bounds instead of adding
the same chunks twice. The report also retains the regular-fabric and global
route bounds and distinguishes shell/global single-net timing bounds from
unverified internal and whole-machine timing.

The intermediate report is
`bounded-resource-preflight-before-union.json`; the normal final report is
`mapped-resource-preflight.json`. Native save/readback, appearance handling,
runtime requirements, and release acceptance remain in the existing pipeline.

## Configuration

The CLI flag overrides an explicit configuration choice. Without either, the
existing flat backend remains selected. The optional settings are:

```toml
[generator]
logic_backend = "bounded"

[generator.bounded_logic]
cell_budget = 64
boundary_budget = 128
domain_budget = 128
arity = 4
cache_compaction = 1
maximum_partition_y = 285
component_gap = 128
shell_branch_y = 300
shell_trunk_y = 304
shell_front_clearance = 96
# shell_track_base_z may be an explicit integer for a bounded test layout.
# Omitting it preserves the existing external router's track selection.
```

Unknown settings and noninteger values are rejected. A taller child envelope,
insufficient separation, insufficient shell clearance, or impossible clock
matching is not silently relaxed.

Within the bounded backend, `cache_compaction = 1` is the default. It selects
the measured compact policy only for `u_cache` with the complete one-bank
`shama_cache_adapter` interface. The cache retains cell/boundary/arity values
64/128/4, keeps the original I/O leaves near the root, and uses a cache-specific
domain budget of 256; the generic domain budget remains 128. The effective
cache budgets are recorded even when another `domain_budget` is configured.
Changing the global cell budget, boundary budget or arity requires
`cache_compaction = 0` to deliberately use the unverified generic cache layout.

The installable `hardware.cache_layout` helper owns the measured clock-space
reservation: 128 blocks normally and 160 blocks for domain 6 and its complete
sibling group. It validates that the target exists and carries an actual
clock, records every effective reservation, and requires the unchanged clock
matcher to succeed. Experimental wrappers are not imported by production.
Other partitions and the default flat backend retain their existing paths.
This policy does not assert native timing or whole-machine acceptance.

## Tested scope

`tests/test_bounded_system_backend.py` exercises the production backend and
finalization hooks using a complete, deliberately small mapped top. Synthesis
is replaced in this fixture by complete known mapped artifacts; the test does
not synthesize or run the full ShamaOS machine. The child contains three real
native cells and the shell adds one, retaining a reversed output bus and
constant ties. The production global router carries both clock and data from
external drive stations through the complete logic keepout.

The fixture writes native Anvil chunks, checks every literal block after
reopening, imports all saved terrain and circuitry, and runs the actual pinned
redstone engine. It occupies 796 saved chunks with 19,104 native sections. At
a 512-redstone-tick clock half-period, all twelve tested register transfers
succeed with no clock skew. At a 20-tick half-period, six missed transfers are
detected. That negative case remains a required test; eventual settled output
alone is insufficient.

The suite also checks exact graph/bus/constant inventory, missing or modified
clock matching, source-package independence, CLI/default selection, early disk
rejection, strengthened post-measurement disk rejection, and full-envelope
global-routing clearance. Existing flat resource, synthesis, interface-routing
and world-generation regressions remain required.

## Remaining full-machine gates

The historical two-bank 8 KiB connected cache fixture has accepted geometry:
37,102,012 unique blocks across 192,026 occupied chunks. Its retained
[geometry checkpoint](../experiments/memory_controller_runtime/evidence/connected-cache-union-checkpoint.json)
records that historical scope. The current one-bank 4 KiB controller and
original-I/O facade have separately accepted geometry of 15,645,176 blocks
across 67,206 chunks, with all 1,178 current mapped cells, 182 original terminals
and 115 register clocks retained. Exact sources, complete mapped/plan inputs,
geometry manifests and test evidence are retained under
[current cache evidence](../experiments/memory_controller_runtime/current_four_kib/README.md).
The complete current bank, decoder and connecting routes are being saved and
checked; their clocks, transactions and persistence still need native evidence.
All complete production partitions must then be
generated, joined and timed, including generated clocks and input pulse
capture. Whole-machine loaded/ticking coverage and native boot, applications,
save, shutdown and reopen remain unproved. The opt-in backend supplies the
missing production integration seam; it does not change those completion
requirements or mark the finished world ready for release.
