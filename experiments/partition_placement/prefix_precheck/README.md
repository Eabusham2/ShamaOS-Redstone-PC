# Conservative flat-prefix precheck experiment

This isolated candidate rejects an impossible North or South escape before
constructing the rest of its path. It applies only when the terminal has one
plain North or South access face. The early predicates are unconditional tests
already enforced by the original allocator against fixed reservations, obstacle
volumes, placed blocks and nearby terminals. Once one such fixed obstruction
occurs in the growing flat prefix, later lanes in that direction contain it too.
The original path construction and all final isolation, headroom, height and
collision checks remain unchanged. Alternative-dependent headroom is excluded
from this early rejection.

Neither snapshot is selected by production. The original allocator remains the
default for the raised-export experiment as well.

## Complete retained 896-terminal comparison

The exact retained parent 221 row-layout input has 896 terminals, 576 native
terminal blocks, branch Y177 and trunk Y181. Both snapshots received the same
explicit North search budget of 512 lanes. The longest selected North run was
840 blocks. The normal global router budget remains 128.

| Measurement | Original allocator | Precheck candidate |
| --- | ---: | ---: |
| Selected escape records | 896 | 896, exact match |
| Source and sink directional paths | 1,792 | 1,792, exact match |
| Emitted block-state operations | 8,472,352 | 8,472,352, exact match |
| Allocation time | 337.942 s | 348.974 s |
| Directional emission time | 17.967 s | 16.541 s |
| Total worker time | 355.928 s | 365.528 s |
| Peak process memory | 331,504 KiB | 331,856 KiB |

The candidate's allocation was **3.26% slower in this run**. The comparison
establishes exact route/state equivalence for this input, not a speed advantage.
It is not a reason to replace the production allocator. A speed claim for
previously rejected fixed-prefix workloads needs a separate measured comparison.

Each selected path was emitted in both electrical directions, including its
4-block branch-to-trunk stair. Coordinates, block names, every ordered property
pair and emission order matched. These prefix records use an admissible common
track for the comparison; they do not constitute complete multi-net trunks, a
parent block union, timing closure or a Minecraft runtime proof.

The retained files under `evidence/complete-896/` include both worker reports,
exact selected escape records, per-direction state sequence hashes, the final
comparison and a separate cross-worker binding/inventory audit. The fixture hash
is `8ce13972ba0c8238a8a5536ecf54077bfed6752d648c306353544019a5d2f228`.
The companion audit checked the fixture, shared source hashes, lane budget,
terminal inventory and every direction independently. Its loader hash was
recorded **after** execution and matched the tracked file; the earlier workers
did not capture a start-time loader hash, and the report states that limit.

The subsequent harness revision records the loader and driver hashes before
each worker runs and compares both reports' complete input/source/budget/inventory
bindings before publishing equivalence. Twenty-seven small guard tests cover
missing or inconsistent bindings, incomplete reports, a different allocator,
wrong inventory, timeout, heartbeat write failure and parent cancellation. The
last two stop only that worker instead of leaving an orphan. These changes do
not alter the retained original run or count as a new performance measurement.

Independent small-case review is retained separately in
`../../partition_planner/evidence/prefix-precheck-independent-review.json`.
It covers 64 exact success/failure cases and 47,472 emitted state operations,
including a valid support-above-stair case that must try a later lane instead of
being permanently pruned. The complete small pipeline's positive and too-fast
negative cases were also byte-identical to the original allocator.

## Reproduce

From the repository root:

```sh
python experiments/partition_placement/prefix_precheck/compare.py --output build/prefix-equivalence-new
```

Use a fresh output directory. The driver starts one allocator worker at a time,
records phase/elapsed-time/memory heartbeats and enforces a 900-second wall budget
per worker. `--wall-seconds` changes that positive finite budget. A timeout or
failure cannot publish an equivalence pass. Run larger geometry, graph parsing
and physical runtime jobs separately so their memory peaks do not overlap.
Measured workers require POSIX process measurement. The comparison validation
helpers can be imported without that platform API.
