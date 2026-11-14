# Current 4 KiB cache: exact sources and compact controller

The current cache uses one complete 4 KiB physical bank. This checkpoint binds
the current RTL, generated header and configuration to a fresh controller
mapping, a complete logical plan, the retained RTL transaction oracle, and all
emitted controller and original-I/O facade geometry. The old 8 KiB evidence
remains a separate historical fixture.

## Accepted controller geometry

| Measurement | Current 4 KiB controller and original I/O |
| --- | ---: |
| Native logic cells | 1,178 |
| Original signal terminals | 182 |
| Original sink uses | 2,208 |
| Register clock terminals | 115 |
| Unique emitted blocks | 15,645,176 |
| Occupied chunks | 67,206 |
| Measured generation time | 341.65 seconds |
| Peak resident memory | 853,716 KiB |

Every native cell, original port, directed signal path and clock remains in
the complete graph. An independent external-sort union checks the exact
block states, coordinates, identical duplicates, bounds and occupied chunks.
All 115 original-input-to-register clock paths pass the unchanged structural
matcher at 2,324 repeater-delay ticks. The exterior facade adds a common
delivery path; this number does not establish a usable native clock rate.

The previous historical 8 KiB controller and facade occupied 136,764 chunks.
The current controller occupies 50.86% fewer chunks. This comparison includes
both the new layout and fresh capacity/toolchain elaboration. The unchanged
historical-graph layout comparison is separately recorded under
`experiments/partition_placement/root_io`.

These first counts are for the complete controller and original I/O. The
following separate measurement includes its complete physical bank and wires.
No complete-cache runtime or release claim is made by this evidence.

## Complete connected-cache geometry

Two independent implementations now agree on the complete current cache's
exact block states, coordinates, duplicates, bounds, palette and union digest:
22,805,546 unique blocks across 118,781 occupied chunks. This includes every
current controller cell, every original-I/O wire, one full 4 KiB bank with all
32,768 stored bits, the real row decoder, and all 77 external nets, including
32 physical return paths. One union uses external sorting; the other uses
SQLite. Both produce digest
`b070e0c2ac33a53cc32e24e2232fc2a765b18ea6ae7be3fc69ad4813e728836e`.

| Complete connected cache | Historical 8 KiB | Current 4 KiB | Reduction |
| --- | ---: | ---: | ---: |
| Unique blocks | 37,102,012 | 22,805,546 | 38.53% |
| Occupied chunks | 192,026 | 118,781 | 38.14% |

This comparison includes the combined capacity and layout changes. It is a
cache fixture measurement; it must not be added blindly to overlapping
regular-component counts or called the finished computer's size.

`evidence/connected-union-checkpoint.json` binds the observed full build report
and `connected-source-inputs.zip`, which retains all 55 exact generator and
input files. Native writing is in progress at this checkpoint. Full native
readback, complete-controller clock pulses, actual reads/writes, all-bit
transaction checks, and shutdown/reopen behavior remain separate gates.

## Reproduction and provenance

`evidence/current-cache-source-inputs.zip` contains 51 files, including all
bound preparation and placement sources, the exact five current elaboration
inputs, raw and native mapped JSON, complete plan, every initial storage byte,
the generated RTL test bench and expected data, tool logs, and both the
unbalanced and balanced geometry manifests. `receipt.json` records every
member hash plus the archive hash. The accepted preparation and controller
reports are also available directly in `evidence`.

Yosys 0.52 emits one pinless `$scopeinfo` metadata record when flattening this
module. The raw mapped JSON is retained. A separate recorded Yosys pass removes
only that record, after proving it owns no pins or connections. The native
artifact retains every actual cell, port and named net; 115 missing DFF port
direction annotations are checked against the actual native cell template.
The strict graph loader still rejects unsupported physical cell types.

The actual copied RTL passes 601 cold/reopen frames and 49 full-storage
checkpoints, totaling 1,605,632 stored-bit assertions. Deliberately incorrect
read and write addresses fail the same oracle. This RTL result is separate
from the pending native controller/decoder/memory integration.

To create a fresh fixture from an exact current checkout:

```sh
PYTHONPATH=src:. python -m experiments.memory_controller_runtime.current_fixture \
  --source-root /absolute/path/to/current/checkout \
  --output build/current-cache-fixture --capacity-bytes 4096
```

The current config and generated header must agree on 4,096 bytes and one
bank. Fresh mapping requires Yosys/ABC; the independent frame check requires
Icarus and vvp. Preparation rejects changed inputs, mapped artifacts, plans,
initial storage or generator source bindings.

## Production bounded-cache policy

The opt-in bounded system backend now selects the measured policy only for
the `u_cache` partition with its complete one-bank adapter interface. It keeps
the original port leaves at the root, uses cell/boundary/domain budgets of
64/128/256 with arity four, and reserves 128 blocks for clock tracks, increasing
domain 6 and its complete sibling group to 160 blocks. The policy and every
effective reservation are recorded in the partition manifest.

The selected target must exist and carry the requested clock. Every targeted
domain must be encountered, and the ordinary matcher must still succeed.
Missing timing space remains a failure. `cache_compaction=0` selects the
previous generic bounded policy when deliberately exploring a different
layout. Other partitions retain their existing policy, and the default flat
system backend is unchanged.

Focused tests reproduce the full frozen logical plan, compare the promoted
reservation's exact small-circuit block states and clock overrides against
the accepted experimental method, reject absent/non-clock/already-balanced
targets, restore the core callbacks after failure, and check backend
selection. Production manifests retain `native_runtime_verified: false`
until the separate current connected-cache gate is complete.

An additional independent run regenerated the complete controller through
production `place_compact_cache`. All 30 clusters, 33 graph modules and two
facade modules matched the accepted saved geometry field for field, including
every native cell, port, route, contact, repeater override and bound. The run
completed in 227.03 seconds with 874,204 KiB peak resident memory. This binds
the full production policy to the retained 15,645,176-block controller union;
it compares complete route definitions rather than repeating the literal
union or proving electrical operation. The reproduction script and receipt
are `experiments/firmware_cache/audit_production_geometry.py` and
`experiments/firmware_cache/evidence/production-geometry-parity.json`.

The connected source archive was frozen before the integration changed the
generic `machine_spec.CACHE_BYTES` default from 8 KiB to 4 KiB. Its explicit
config, generated RTL header, mapped controller and bank fabric already use
4 KiB. A separate final-default audit confirms this is the sole semantic
source difference, and reproduces the exact physical fabric, complete bank
bounds, initial bytes and all 77 external routes with the integrated defaults.
Both original union implementations, their digests and the reported reduction
percentages also agree. The archive remains unchanged; this bridge is recorded
in `experiments/firmware_cache/evidence/connected-final-default-provenance.json`.
The subsequent source-provenance fix adds `hardware/cache_layout.py` to the
generated world's bound sources. The final integration receipt
`experiments/firmware_cache/evidence/connected-final-integration-provenance.json`
checks that exact metadata addition separately: the complete generator AST is
otherwise unchanged, and a deliberately changed capacity contract is rejected.
It rechecks the same final physical inputs without changing the original
source archive or earlier receipt.

The later saved-world fingerprint optimization is bound separately in
`experiments/firmware_cache/evidence/connected-final-native-io-provenance.json`.
The auditor accepts only the exact reviewed old and new `_Chunk.fingerprint`
method ASTs and requires the entire remaining `worldio.py` AST to be identical.
It compares the retained method with the optimized method on 13 fresh semantic
fixtures, including ownership, terrain, dense and sparse blocks, used duplicate
palette entries, and unused states. Both reject invalid palette indices. A
changed native encoding helper and an unreviewed fingerprint replacement fail
the bridge. The same physical bank, initial bytes and all 77 routes still
match the original measured geometry. Original archives and earlier receipts
are preserved; this does not rerun the full geometry union or native circuit.

## Independent saved-world and native-evidence audit

`experiments/memory_controller_runtime/audit_native_connected.py` checks each
completed native stage without starting another engine process. Its first
stage independently binds the original preparation, mapped graph, plan,
initial bytes and all 77 physical nets; reconstructs every output coordinate;
and inspects all 72 saved exterior drive pads. It checks the saved-world file
inventory against the actual imported stream's source identity and binds every
runtime helper. Missing or changed preparation inputs fail before a runtime
claim can be made.

With `--clocks`, the auditor derives requested rise times directly from the
saved scenario and checks all 115 original clock traces. Every requested pulse
must arrive at the same fixed latency, with the original width and spacing.
The safe run must pass and the deliberately too-fast run must fail. Trace
sample zero is taken after the first tick; the auditor accounts for this when
reporting arrival latency.

With `--transactions`, the auditor requires the complete cold/reopen schedule,
not the preliminary smoke run. It checks each exact expanded frame and all
32,768 stored bits at each of the 49 boundaries against the retained RTL word
oracle, for 601 frames and 1,605,632 stored-bit assertions. Disabled continuous
tracing does not narrow assertions. Response observations establish values
and control states at the declared frame endpoints; they do not measure
per-tick response latency or glitches. Both actual quiescent snapshot files,
the reopened native save and its fresh-process import must match. Every
stored bit in the reopened save is also read independently before acceptance.

```sh
PYTHONPATH=src:. python -m experiments.memory_controller_runtime.audit_native_connected \
  --build /absolute/path/to/complete-connected-build \
  --runtime /absolute/path/to/prepared-native-runtime \
  --clocks /absolute/path/to/clock-report.json \
  --transactions /absolute/path/to/transaction-report.json \
  --output /absolute/path/to/new-independent-native-audit.json
```

Omit the two optional reports to audit only completed preparation, or omit
`--transactions` to audit preparation and clocks while transactions remain
pending. Each audit writes a new receipt and keeps whole-machine verification
false. The verifier tests include missing or changed stored bits, lost frames,
internal signal drives, changed world/scenario/engine identities, substituted
trace names, truncated snapshots, and distorted clock pulses. Those tests
validate the auditor; they do not assert that the pending full native cache
run has passed.
