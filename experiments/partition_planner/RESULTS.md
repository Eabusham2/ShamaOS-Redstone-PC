# Complete partition parent-grouping experiments

## Result

The retained candidate reduces complete production-mode display-bridge topology
from **15,487 to 13,176 inter-cluster links**, **117 to 96 bounded router domains**,
and **seven to four domains at the busiest cluster**. Those are reductions of
14.92%, 17.95%, and 42.86%, respectively. The default planner and the physical
placement agent's current baseline topology have not changed.

This is a logical routing-work reduction. It does not establish a chunk,
block-count, clock-frequency, or complete-world improvement. Both complete
topologies still need the same actual geometry and timing gates.

## Preserved workload and provenance

Every candidate retains the same 10,910 native cells, 351 original port bits,
10,931 signals, 20,944 original signal sinks, 649 DFF clock leaves, and 177 native
or interface leaves. The ABC mapping has no surviving literal-constant terminals.
There are 59 internal clusters, four hierarchy levels, at most 64 native cells
and 128 boundary terminals per leaf, four children per internal cluster, and
256 terminals per routing domain.

Mapped source SHA-256:
`bdc22086b9146cdf7ea9d58cbfddfeea09120bd19a744f704b9605ff573609a4`.

Every candidate has the unchanged original-endpoint SHA-256:
`1b87f736c932acc0713c534f4e3d95d7e9d3e63e6a9779e42a5ed9efcb9df472`.

The mapping is the complete frozen `shama_display_bridge` ABC artifact, not a
selected cut. Its source and tool details are also retained in the checked-in
`display_bridge_mapping_provenance.json` fixture record.

## Comparison

| Complete-tree strategy | Links | Domains | Most domains at a cluster |
|---|---:|---:|---:|
| Unchanged consecutive-parent baseline | 15,487 | 117 | 7 |
| Consecutive parents, local swaps | 13,334 | 103 | 5 |
| Greedy net-aware parents, local swaps | 13,402 | 99 | 5 |
| Weighted parents, corrected singleton objective, local swaps | 13,226 | 100 | 5 |
| Whole-tree link-priority swaps | 13,050 | 102 | 5 |
| Whole-tree domain-priority candidate | **13,176** | **96** | **4** |

The last candidate uses 126 more links than the minimum-link result, while using
six fewer router domains and reducing the maximum domains at a cluster by one.
It has 1,226 explicit internal fanout branches, compared with 1,222 in the
baseline. Native leaves and their local boundaries are unchanged; the reduction
comes from their grouping and resulting parent paths.

The selected first grouping stage took 3.97 seconds. Its whole-tree refinement
took 11.45 seconds including a full independent plan audit. These are planner
measurements, not Minecraft execution timings. The final pass accepted no further
swap in the searched neighborhood; this is not a proof of a global optimum.

## Current-effective assembler comparison

The bounded-neighbor implementation also processes the complete recovered
assembler mapping without an all-pairs cluster search:

| Complete logical topology | Default grouping | Audited summary candidate | Reduction |
|---|---:|---:|---:|
| Inter-cluster links | 1,343,021 | 1,068,216 | 20.46% |
| Router domains | 10,752 | 8,759 | 18.54% |
| Maximum domains at one cluster | 175 | 122 | 30.29% |

The candidate retains all 559,453 native cells, 364 original port bits, 559,617
signals, 895,698 signal sinks, 17,486 clock leaves, 498 constant terminals and
8,748 leaves. The seven-level hierarchy and all original endpoint hashes remain
unchanged. Explicit internal fanout branches increase from 111,197 to 134,130;
total links and domain counts are lower, but geometry must measure that tradeoff.

The search uses at most 64 scored neighbor candidates, fanout 32 for choosing
neighbors, eight candidate neighbor groups per group, and one local-swap pass.
Those caps affect search only. Every original signal remains in the constructed
topology and is independently reconstructed by the summary oracle.

The whole successful process took 86.73 seconds with a 1,534,544 KiB external
peak RSS. Its complete summary hash is
`015937bc8f2637bb3ca70dadbeacf820bd206384b51696c47e72c22baf706011`.
The original endpoint hash remains
`fdd37e36a758a1ab10ab9a3856d4a7d38ac7f74823561cecc67fe97331b6327d`.

This is an independently audited **summary-only logical candidate**. Full route
records are not retained; it cannot be passed directly to the physical emitter.
No chunk reduction, speed increase or complete-world run has been established.
The complete ABC/AIG and high-fanout cases have repeated-run evidence; this
large assembler candidate has one successful exclusive run and full independent
logical reconstruction. An earlier overlapped parse was killed before producing
a candidate and is retained separately as a failed attempt.

The compact evidence is `evidence/asm-grouping-candidate-report.json`,
`evidence/asm-grouping-candidate-resources.json`, and
`evidence/asm-candidate-source-hashes.json`. The unsuccessful parse is recorded
in `evidence/asm-overlapped-parse-termination.json`.

## Method and checks

The first stage chooses parent groups using exact shared-net cut gain and then
swaps complete children between groups. It does not move individual cells out
of their original leaves. The second stage swaps same-level subtrees and scores
the effects on all changed ancestors, rather than just one local parent.

An independent hypergraph-cut oracle identifies a participating tree edge when
its child subtree contains some, but not all, of a signal's endpoint leaves.
Its total equals the actual planner's complete directed-link count. At every
internal cluster, the incident edge count also reproduces each net segment's
terminal workload and the deterministic 256-terminal domain packing.

The retained candidate is accepted only after `audit_plan` reconstructs the
complete original source/sink/constant identities and directed interfaces. It
also verifies every cell, clock count, segment, bounded domain, and reported
hash. The selected two-stage pipeline was repeated from the same original
mapping and produced **byte-identical complete plan files at both stages**.

The domain-priority neighborhood permits at most 16 additional links in one
proposed swap, but accepts it only when the lexicographic objective improves:
maximum domains at any cluster, total domains, total links, then squared terminal
workload. The final candidate improves all three requested metrics over baseline.

## Retained evidence

Compact files below are relative to this experiment directory. Raw result paths
inside their JSON records refer to the original measured workspace. The
[README](README.md) provides portable reproduction commands.

- `evidence/abc-parent-comparison-v1.json`: first complete comparison.
- `evidence/abc-parent-comparison-v2.json`: corrected singleton-cost
  comparison. A carried singleton has no newly emitted parent domain.
- `evidence/abc-link-priority-report.json`: minimum-link candidate report.
- `evidence/abc-domain-priority-plan.json.gz`: selected complete candidate.
- `evidence/abc-domain-priority-report.json`: exact
  domain/link oracle, complete audit, every optimization pass, and provenance.
- `evidence/abc-repeat-determinism.json`: repeated serialized
  plan equality for both selected stages.
- `compare_parent_grouping_v1.py`: exact first-stage implementation for the
  selected 99-domain starting point.
- `compare_parent_grouping.py`: corrected singleton-cost comparison experiment.
- `improve_complete_tree.py`: whole-tree link/domain refinement implementation.

Selected complete plan file SHA-256:
`5d90561aae65f48aaac8b6fd2ff1d318c15f433bbf276eb0a6f13610a715eb1f`.

Selected topology SHA-256:
`590db4c6a48665583427ffb3b24cb6beadf332f6eca3093c3aef715a7bb32d82`.

Parent relationships, internal domain assignments, and boundary IDs change in
this candidate. Existing physical-route caches therefore cannot be reused as
its proof. Native leaf identities remain stable, but complete parent routing
and end-to-end timing must be regenerated and independently checked before any
default promotion.
