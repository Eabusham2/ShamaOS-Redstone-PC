# Bounded physical placement checkpoint

This directory retains successful connected geometry, a reproduced routing
failure and the bounded search adjustment that clears its larger parent. The
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

## Larger parent and retained failure

Parent 221 connects all four successful parents, preserving 1,024 native cells.
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
defaults to 128. Each direction keeps its own independent search limit.

The complete parent retry now passes emitted-wire isolation, child-envelope
exclusion, above-grass height checks and its independently materialized literal
block union. The compact grid still fails at the retained South endpoint; the
row fallback succeeds with two parent tiers in each of two horizontal stacks.

| Parent 221 result | Measured value |
| --- | ---: |
| Native cells | 1,024 |
| Physical modules | 28 |
| Shared contacts, including descendant contacts | 1,500 |
| Unique blocks, recursive count and independent literal union | 11,324,130 |
| Occupied horizontal chunks | 55,494 |
| Bounds | `(0,−60,−42)` through `(4886,181,3422)` |
| Complete proof time | 548.25 seconds |
| Process peak RSS | 3,585,632 KiB |

`evidence/parent-221-north-workload.json` retains the exact mapping, topology and
source hashes. `evidence/parent-221-packing.json` retains the successful packing
and the rejected compact layout. These remain subtree measurements: actual
runtime and the complete bridge have not been established by this geometry run.

The remaining gates include all remaining complete bridge clusters, clock and
data timing closure, whole-system physical routing,
and real saved-world execution of the complete computer. A parent exceeding the
explicit 4,096-prefix allocation budget fails and requires real horizontal banks
with all delivery paths; allocating empty bank boxes cannot satisfy that gate.

## Original I/O requires an exterior connection

`PlacedCluster.ports` records original pins in their leaf circuits. Recursive
packing preserves their positions but does not guarantee that they sit on the
completed partition's outside boundary. The complete graph audit intentionally
checks those original leaf contacts. A shell must not treat them as proven
exterior contacts or relocate their coordinates without actual connecting wire.

`audit_port_egress.py` streams the actual emitted blocks to check a candidate
straight South corridor from every original pin to a separate exterior row.
It checks exact block conflicts, active-wire adjacency, existing climbing
headroom and clashes between candidate corridors, retaining original port
identity and direction. It emits no wires and makes no runtime claim. Nine
focused tests cover valid same-net handoff, missing original pins and each
obstruction class. A blocked corridor requires another fully checked route or
reserved egress through its ancestors before a partition can join the shell.

The full 1,144-cell cache measurement confirms that this is a real constraint:
the scan inspected 9,791,996 emitted operations and found 87 of 183 straight
South original-pin corridors blocked. The source-bound report is
`evidence/cache-original-port-egress-padding64.json`; the scan does not claim
that no alternative route exists.

### Constructive original-I/O facade

`port_egress.py` reserves and routes original-pin delivery through each ancestor
while its normal graph boundary wires are being allocated. Both use the same
shared escape allocator and the original emitted-block isolation, child-envelope
and height guards. Original mapped pins remain in `ports`; `exposed_ports` is a
separate map of actual outer contacts. Every additional wire-only facade module
and directed handoff has its own conservation audit. No native cell, graph net,
constant or original port is removed or substituted.

The two-ancestor pipeline retains all 3 native cells, 6 original pins, 18 graph
handoffs and 3 constants, and adds 6 facade modules with 11 original-I/O handoffs.
Its complete literal union has 26,422 blocks. Real engine tests driving and
observing the exterior contacts pass all 16 transfers at a 512 RT half-period,
with zero register-clock skew, 850 RT minimum setup and 87 RT minimum hold.
The 20 RT negative retains all 16 clock edges but exposes eight wrong transfers.
The positive and negative reports are in `evidence/port-egress-pipeline-*.json`.

The separate `port_egress_manifest.py` format includes every additional module,
exposed contact and handoff. Its reader reconstructs and audits both the original
graph and facade; the ordinary graph-only replay rejects the distinct schema.
The ordinary writer also rejects extension-bearing clusters anywhere in the
hierarchy, so it cannot silently omit facade wires. The specialized disk reader
checks the recorded manifest digest, exact mapped identity, canonical plan and
topology, and every required generator source hash within its source root. Pure
typed reconstruction performs the graph and schema checks but does not certify
source files or electrical isolation. Its clock audit explicitly covers the
original input pin onward; exterior clock delivery adds a separate common path.

The hardened facade, obstruction and external-interface suites pass 45 tests
without skips, including missing/reversed/invented connections, stale or escaped
source bindings, malformed nested data, and the actual-engine timing cases.
Two additional saved-world tests reopen all 26,422 circuit blocks exactly and
compare every declared signal with the dense reference engine: 1,198,080 samples
for the passing timing case and 489,600 for the deliberately failing fast case.
Their reports are `evidence/port-egress-saved-*.json`. The external-interface
adapter routes to the actual outer contacts while retaining the complete
component envelope and contact keepouts; the six-pin pipeline passes with those
additional external routes too.

The complete cache facade is still incomplete. An explicitly capped 1,228 MiB
trial completed the 128-port ancestor before exhausting host memory. The next
2,048 MiB trial completed the 183-port ancestor but exhausted host memory while
copying the next parent domain's literal states into a second union dictionary.
Neither trial reached a complete root manifest, final union or runtime proof.
Their unchanged source-bound reports and progress are retained as
`evidence/cache-facade-cap1228-*` and `evidence/cache-facade-cap2048-*`. The next
step is to remove duplicate host-side state storage while preserving exact
collision, adjacency, headroom and child-envelope checks. The small pipeline is
not a full-cache result.

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
