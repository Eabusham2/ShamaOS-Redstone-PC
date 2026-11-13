# Bounded grouping experiments

These programs are isolated experiments. They do not change the production
planner's default grouping or select an alternative Minecraft emitter. The
complete bridge results and their limits are in [RESULTS.md](RESULTS.md).

All commands below run from the repository root with `PYTHONPATH=src`. The
scripts reuse the checked-in complete display mappings; they do not synthesize
another large partition. Experimental audit/search entry points explicitly
reject optimized Python (`-O` or `-OO`) before accepting input, because their
independent checks use assertions as acceptance gates. The production planner
uses explicit errors and does not depend on that experimental guard.

## Reproduce the selected bridge candidate

The selected first stage is preserved exactly in `compare_parent_grouping_v1.py`.
The newer comparison script adjusts the heuristic treatment of carried
singletons, but its best local result was not the chosen starting point.

```sh
PYTHONPATH=src python experiments/partition_planner/compare_parent_grouping_v1.py \
  tests/fixtures/display_bridge_abc.json.gz --top shama_display_bridge \
  --only greedy-degree-swaps --output build/grouping/first-stage
PYTHONPATH=src python experiments/partition_planner/improve_complete_tree.py \
  tests/fixtures/display_bridge_abc.json.gz --top shama_display_bridge \
  --plan build/grouping/first-stage/greedy-degree-swaps-plan.json \
  --output build/grouping/domain-priority-plan.json --passes 5 --objective domains
```

The second command runs the full independent retained-plan audit, checks all
link/domain workloads against the separate cut oracle, and writes the complete
plan and a report. The expected topology SHA-256 is
`590db4c6a48665583427ffb3b24cb6beadf332f6eca3093c3aef715a7bb32d82`.
Absolute source paths in the complete JSON file may differ between workspaces;
the topology and original-endpoint hashes do not depend on that path.

The selected complete plan is also retained as
[`evidence/abc-domain-priority-plan.json.gz`](evidence/abc-domain-priority-plan.json.gz),
with a decompressed SHA-256 of
`5d90561aae65f48aaac8b6fd2ff1d318c15f433bbf276eb0a6f13610a715eb1f`.
Its original local repeated run produced byte-identical complete plans at both
stages; the report preserves that scoped evidence.

## Current-effective assembler source and logical baseline

The large assembler mapping is deliberately outside this repository. The
recovered artifact is `13999023957` from CI run `43333367519`, source commit
`4e933a5`. Its compressed artifact SHA-256 is
`b249a8a1f6205d4fb7ba7438bdab55bc2fefe79b1fa2404c3b92f695160d49f0`;
the mapped JSON SHA-256 is
`78a6ab3abcbbe4bde94acaf6bbfdb8388dbeebf301757f34c49172f97b1664ee`.

The retained mapping contains 505,104 mapped primitives, lowered by the current
builder to 559,453 native cells. The only referenced boot macro is
`SHAMA_RAM_BYTES=32'h00018000`. Both artifact-source and current-source
preprocessing produce identical 48,878-byte assembler RTL. Flash-header changes
do not change that module's effective input. This provenance statement applies
to this assembler partition only.

With the original artifact extracted to `build/recovered-asm`, the checks are:

```sh
python experiments/partition_planner/verify_asm_provenance.py \
  build/recovered-asm --artifact-commit 4e933a5 --current-ref HEAD \
  --output build/grouping/asm-effective-provenance.json
PYTHONPATH=src python -m shamaos.hardware.partition_planner \
  build/recovered-asm/partition-u_asm-shama_asm_accel.json --top shama_asm_accel \
  --summary-only --output build/grouping/asm-baseline-summary.json
PYTHONPATH=src python experiments/partition_planner/audit_summary_topology.py \
  build/recovered-asm/partition-u_asm-shama_asm_accel.json --top shama_asm_accel \
  --plan build/grouping/asm-baseline-summary.json \
  --report build/grouping/asm-baseline-audit.json
```

The summary oracle derives each edge independently from subtree endpoint
membership, then reconstructs every original signal's directed paths and domain
assignment. All 559,617 signals match the summary topology hash, including
17,486 clock leaves and 498 constant terminals. It also checks every cell,
original port, boundary budget, domain inventory and reported total.

This is complete logical reconstruction of a summary hash. It **does not retain
the complete route records**, does not pass the default retained-plan auditor,
and cannot be consumed directly as a complete physical-routing plan. No geometry,
chunk reduction, timing closure or Minecraft-world run follows from this check.

The retained baseline has 8,748 leaves, 2,917 internal clusters, 1,343,021 links,
10,752 routing domains, and at most 175 domains at one cluster. The external
kernel measurement of its independent audit is 35.67 seconds and 1,534,812 KiB
peak process RSS. The old 737,585-cell /290-domain result remains historical
stress evidence, not the current-effective assembler baseline.

## Scalable grouping search

`scale_parent_grouping.py` caps the heuristic neighbor search so that large
graphs do not require comparing every pair of clusters. Its fanout/candidate
limits affect search order only; the original graph and oracle still include
every signal, load, constant and clock leaf. Use `--retain` for manageable
complete fixtures; its default writes a summary and runs the independent
summary-hash oracle described above.

```sh
PYTHONPATH=src python experiments/partition_planner/scale_parent_grouping.py \
  tests/fixtures/display_bridge_abc.json.gz --top shama_display_bridge \
  --output build/grouping/scalable-bridge.json --retain
PYTHONPATH=src python experiments/partition_planner/scale_parent_grouping.py \
  tests/fixtures/display_bridge_aig.json.gz --top shama_display_bridge \
  --output build/grouping/scalable-aig.json --retain
PYTHONPATH=src python experiments/partition_planner/scale_parent_grouping.py \
  experiments/partition_planner/evidence/high-fanout-129.json --top fanout \
  --neighbor-fanout 1 --output build/grouping/no-search-net-omission.json --retain
PYTHONPATH=src python experiments/partition_planner/scale_parent_grouping.py \
  build/recovered-asm/partition-u_asm-shama_asm_accel.json --top shama_asm_accel \
  --output build/grouping/asm-candidate-summary.json --swap-passes 1
```

The high-fanout fixture's `--neighbor-fanout 1` case excludes every crossing
signal from heuristic neighbor selection, yet its complete audit retains all
129 clock leaves, 258 signal sinks and 131
literal-constant terminals. Complete AIG and high-fanout cases were also repeated
deterministically; their scoped results are retained in
[`evidence/scalable-heuristic-checks.json`](evidence/scalable-heuristic-checks.json).

The last command reconstructs every original assembler net before accepting its
summary. The retained successful run reduces 1,343,021 links to 1,068,216 and
10,752 router domains to 8,759, with the busiest cluster reduced from 175 to 122
domains. Its 86.73-second process and 1,534,544 KiB peak RSS are preserved in the
compact [candidate report](evidence/asm-grouping-candidate-report.json) and
[external resource measurement](evidence/asm-grouping-candidate-resources.json).
It remains summary-only logical evidence. The measured run used an exclusive
large-process slot after an overlapped parse terminated without a candidate.

`audit_placement_manifest.py` is a separate artifact checker for a connected
physical fixture. It reconstructs original endpoint reachability through the
literal module manifest and the shared-contact identities without calling the
placement auditor. It is structural evidence; saved-world engine replay remains
a separate gate.

The compact retained fixture can be checked with:

```sh
PYTHONPATH=src python experiments/partition_planner/audit_placement_manifest.py \
  experiments/partition_planner/evidence/connected-pipeline-v2
```

`measure_process.py` is the Linux external `os.wait4` wrapper used for peak RSS.
It accepts a measurement JSON path, stdout path, then the complete child command;
the process is measured after it fully exits, including final output allocation.
