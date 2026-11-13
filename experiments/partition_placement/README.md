# Bounded physical placement checkpoint

This directory retains successful connected geometry, a reproduced routing
failure and the bounded search adjustment being tested against it. The
production generator has not switched to this experimental path.
The input is the complete retained ABC mapping of `shama_display_bridge`, with
10,910 native cells, 351 original port bits and all 649 clock leaves. No cell,
port, signal or constant is removed to make a selected subtree fit.

## What has passed

Four complete first-level parents, each containing four 64-cell native leaves,
generate real blocks and bounded parent routes. Their children are stacked in
four tiers, with every support above classic superflat grass at Y−61. All four
parent envelopes span Y−60 through Y49.

| Parent | Native cells | Routed parent domains | Shared contacts | Unique blocks |
| --- | ---: | ---: | ---: | ---: |
| 177 | 256 | 2 | 248 | 1,287,608 |
| 178 | 256 | 2 | 174 | 948,836 |
| 179 | 256 | 2 | 235 | 1,170,644 |
| 180 | 256 | 2 | 235 | 1,321,198 |

Parent 177's independently materialized complete block union matches its recursive
count. It occupies 9,332 horizontal chunks, within the bounding coordinates
`(0,−60,−384)` through `(2134,49,1158)`. These are subtree numbers, not a completed
computer footprint. `evidence/first-parent-177.json` retains that measurement and
its exact source hashes; `evidence/parents-177-180.json` retains all four parents.

The separate small two-level sequential proof exercises original I/O, constant
ties, both DFF clock leaves and every one of its 18 shared contacts. Its stronger
configuration contains 7 bounded parent domains and 16 total physical modules:
13,176 unique circuit blocks across 90 native occupied chunks. The actual pinned
Minecraft redstone engine passes all 16 expected pipeline transfers with a 512 RT
clock half-period. At 20 RT, all 16 clock edges still arrive together, but 8 transfers
are wrong. Saved/reopened native-world replay matches every dense trace sample
for both cases. This establishes a working small hierarchy and a meaningful
timing negative; it does not establish whole-bridge or whole-machine timing.

Actual engine tests also distinguish a valid support two blocks above a rising
wire from an obstruction directly above it, and expose the short caused by an
independent crossing wire only two blocks above. Active-wire adjacency, literal
overlap and the required climbing air space remain enforced.

## Next gate and retained failure

Parent 221 must connect all four successful parents, preserving 1,024 native cells.
With the earlier 128-lane North search limit, the compact X/Z grid fails at
endpoint `(32,−59,1542)`. The implemented fallback keeps the safe vertical tiers
and places horizontal stacks side by side along X; that attempt also fails, at a
new parent export port `(2208,−59,3342)`. The latter uses North access, after 738 of
896 prefixes have been allocated, with candidate branch X4258. No failed layout
is emitted as a working circuit.

The exact failure and source hashes are retained in
`evidence/parent-221-layout-failure.json`. Small allocator metadata is retained
separately for the compact and row layouts. The compressed JSON row-prefix
fixture retains all 896 endpoints, 576 terminal obstacle blocks, child envelopes
and access directions. It roundtrips exactly to the captured allocator inputs;
large materialized block maps and opaque call captures are excluded.

An independent replay with a 512-lane North budget allocates all 896 prefixes
without weakening collision, adjacency, headroom or volume checks. The longest
North run is 840 blocks, or 140 lanes: beyond the earlier 128-lane cap. That
report is retained as `evidence/parent-221-north512-prefix.json`. This is prefix
allocation evidence only. The bounded parent path now derives its North search
budget from the complete parent endpoint count; the general router still
defaults to 128. Each direction keeps its own independent search limit. A full
parent retry must still pass emitted-wire isolation and its complete literal
block union before a successful geometry claim is made.

The remaining gates include this dense parent connection, all remaining complete
bridge clusters, clock and data timing closure, whole-system physical routing,
and real saved-world execution of the complete computer. A parent exceeding the
explicit 4,096-prefix allocation budget fails and requires real horizontal banks
with all delivery paths; allocating empty bank boxes cannot satisfy that gate.

## Reproduce

Run from the repository with its Python dependencies installed. The positive
subtree command materializes its block union for an independent count:

```sh
python experiments/partition_placement/reproduce_geometry.py --cluster 177 --literal-union --output build/parent177.json
```

The following command rebuilds all four successful child parents and reproduces
the historical larger-layout failure with its earlier North limit. It reports
the failure and returns a nonzero exit status:

```sh
python experiments/partition_placement/reproduce_geometry.py --cluster 221 --parent-north-lanes 128 --output build/parent221-north128.json
```

Omit that diagnostic override to exercise the current bounded-parent policy and
its full emitted geometry:

```sh
python experiments/partition_placement/reproduce_geometry.py --cluster 221 --literal-union --output build/parent221-current.json
```

To isolate prefix allocation without rebuilding the native children, replay the
portable JSON fixture. This does not verify the complete emitted parent circuit:

```sh
python experiments/partition_placement/replay_prefix.py --north-lanes 512 --output build/parent221-prefix512.json
```

Omitting `--cluster` attempts the complete default bridge. `--plan` accepts a
complete alternative plan, which is independently audited against the same
mapped graph before placement. Successful partial results are written to a
neighboring `*-progress.json` even if a later parent fails. Reports record the
mapping hash, topology hash, source hashes, bounds, counts, elapsed time and
measured process memory.

The direct regression suites are `tests/test_partition_placement.py` and
`tests/test_local_router.py`; real engine cases require `SHAMA_MCHPRS_RUNNER` as
described in the repository's validation documentation. Run large graph parses,
geometry generations and complete memory-world benchmarks sequentially: an
overlapped graph parse was killed even though earlier separate runs succeeded.
