# Raised export and measured apron experiment

This is an isolated physical-placement candidate. The production generator and
the default bounded placement module remain unchanged. The complete retained ABC
display graph, native cell templates, all port identities, North/South access
faces and existing collision/isolation/headroom/height checks are preserved.

The candidate puts a parent's own exports at the highest child block Y plus 9,
below its branch/trunk planes at plus 14/18. It starts with a 24-block South apron
and measures the fully emitted route extent. Every export must lie at the final
outer South boundary. Otherwise it rebuilds monotonically beyond the measured
overshoot plus 4 blocks. Eight unsuccessful attempts reject the layout; there is
no child-envelope exception or silently buried connector.

## Measured progress

| Same complete circuit | Prior blocks | Candidate blocks | Prior occupied chunks | Candidate occupied chunks |
| --- | ---: | ---: | ---: | ---: |
| Small two-level, 7-domain pipeline | 13,176 | 10,340 | 90 | 74 |
| Parent 177, all 256 native cells | 1,287,608 | 916,260 | 9,332 | 4,275 |
| Parent 221, all 1,024 native cells | 11,324,130 | Union incomplete | 55,494 | Unknown |

The composable parent 177 result reduces block count by 28.8% and occupied chunks
by 54.2%. Its width and height are unchanged; bounds shrink to `(0,−60,−380)`
through `(2134,49,226)`. The first 24-block apron put its exports at Z158 while
other routes reached Z222. One measured rebuild uses apron 92 and places every
export exactly at Z226. The exact streamed block union agrees with the recursive
count. These are selected subtree measurements, not whole-computer footprints.

The first measured-apron parent 221 trial returned from parent emission and its
local geometry/isolation/envelope checks after 2,139.64 seconds. Its recursive
count was 10,962,588 blocks, with all 288 own exports on the South boundary at
Z2480 after apron 24 grew to 1,874. Bounds were `(0,−60,−1060)` through
`(4799,181,2480)`. The execution then disappeared during the final streamed
union; the execution session was no longer registered on recovery and no final
report existed. Partial sort files and progress were retained. This is an
incomplete run, not a complete geometry/union pass or a rejected layout. Its exact
occupied chunk count and active runtime after the last progress message remain
unknown. `evidence/parent221-interrupted.json` records this boundary and the exact
unchanged source bindings. A future instrumented retry has a 40-minute wall
budget; this trial's wall budget had already expired when the unavailable
execution was recovered.

The small candidate preserves 3 native cells, 6 original ports, 3 constants,
16 physical modules and all 18 shared contacts. At a 512 RT clock half-period,
the actual pinned engine passes all 16 transfers with zero skew at both register
clock pins; the measured minimum setup and hold times are 878 RT and 88 RT.
At 20 RT, all 16 clock edges still arrive together, but 8 transfers are wrong.
The negative exposes data transfer failure rather than relying on settled final
outputs. Actual saved/reopened native-world execution matches all 745,472 positive
and 304,640 negative trace values, including every handoff. All 40 unchanged
placement regressions also pass against this candidate.

Independent review reconstructed every original native pin, boundary terminal,
constant and actual escape/first-diode face in the small fixture, and recalculated
its timing witnesses. The measured-apron wrapper was separately checked for
monotonic growth, finite failure, mixed export-plane rejection and propagation of
the unchanged geometry guards.

## Rejected shortcuts

A fixed 16-block apron fails the small hierarchy's child-envelope gate. A fixed
24-block apron works in the small fixture and produces a smaller standalone
parent 177 union, but three of the four larger child exports lie behind their
own South wiring. That version is rejected for composition. Its standalone
4,029-chunk parent 177 figure is retained as evidence of that limit, not used as
the accepted composable result. The larger fixed-apron trial was interrupted
after this measured interface failure; it is not a completed parent proof.

## Reproduce

From the repository, run the same 256-cell selected subtree with exact union and
a compact manifest of every module, pin, route decision and contact:

```sh
python experiments/partition_placement/raised_exports/run.py geometry --cluster 177 --literal-union --output build/raised-parent177.json
```

The same command with `--cluster 221` attempts the 1,024-cell parent. The driver
prints each apron attempt's stage, elapsed time and process memory. Reports keep
the entire original graph/topology hashes and identify the candidate snapshot;
no literal block maps are needed in this checkpoint. A successful geometry run
does not establish complete-bridge or whole-system timing.
The launcher enforces a 2,400-second wall budget in a monitored child process,
retains a heartbeat beside the selected output, and stops only that exact child
if the budget expires. `--wall-seconds` changes that explicit budget. A budget
abort preserves any completed module manifest and partial progress without
labelling them a final proof pass or a layout rejection.

Set `SHAMA_MCHPRS_RUNNER` to the built pinned actual redstone engine, then run the
complete positive/negative small proof and the unchanged placement regressions:

```sh
python experiments/partition_placement/raised_exports/run.py pipeline
python experiments/partition_placement/raised_exports/run.py tests
```

`pipeline` also accepts `--runner` and `--output`. Proof entrypoints reject
optimized Python (`-O`) so acceptance assertions cannot disappear. Run larger
geometry, graph parses and full memory-world engine proofs sequentially.

`streamed_union.py` sorts exact 12-byte coordinate/state records with bounded
buffers and file fan-in. It preserves every emitted record, rejects conflicting
states and counts exact occupied chunks from a chunk-prefix coordinate key.
The state palette and run-path inventory remain explicit. This replaces only
the final large union dictionary; the parent router's adjacency, headroom and
envelope checks are still independent. An independent 32-probe review includes
multi-pass duplicates, conflicting states, truncated records, missing records,
negative coordinates and complete property-pair identity.
