# Exact parent-state inventory

The earlier full cache facade exhausted its explicit 2,048 MiB address-space
limit while copying a validated routing domain into a second parent block
dictionary. `../packed_parent_union.py` retains one exact inventory, with reversible
integer coordinate keys and shared immutable domain/state values. This changes
host-side validation storage, not routing geometry or circuit behavior.

The retained `baseline_egress.py` is the exact hardened facade builder before
this inventory change (SHA-256
`35c209b4b56b32f76309ea0337f6c346d0d65ce328fc943fe401defbd002d281`).
It is used only as an independent comparison baseline. It must not be selected
by the full build or substituted for the current generator source binding.

`audit.py` compares the candidate against the original literal union, isolation
and envelope guards. The first independent run and the portable rerun agree on
all 779 cases: 608 accepted and 171 rejected, covering 46,888 emitted operations.
Every accepted case has exactly equal block states, bounds and child-duplicate
counts. Cases include physical ordinary and facade pipeline parents, active-wire
neighbors, climbing dust and support interference, duplicate emissions, property
tuple semantics, overlapping child bounds and coordinate packing boundaries.
A rejected collector cannot be read, resumed or reused for another attempt.

The portable audit changes only file-location handling from the independently
run harness. Both reports retain the original and candidate source hashes plus
per-fixture literal digests:

- `../evidence/packed-parent-union-independent-original.json`
- `../evidence/packed-parent-union-independent.json`

Run from the repository:

```sh
PYTHONPATH=src:. python experiments/partition_placement/parent_union/audit.py
PYTHONPATH=src:. python -m pytest -q tests/test_packed_parent_union.py
```

The focused tests additionally compare complete original and candidate facade
generation, including every emitted block, graph/facade module, hierarchy bound,
contact, port, packing decision and unique-block count for two domain budgets.
These small exact comparisons establish guard preservation. The separate
complete-cache result below establishes geometry and planned clock matching;
saved-world timing remains a further gate.

## Retaining a capped complete retry

`run_capped.py` saves the complete mapped graph, plan and exact generator-source
ZIP before starting. The retained configuration cannot silently change the
height, clock reservation or resource ceiling. The launcher records process
memory and preserves the driver's progress and failure report. The driver saves
its complete unbalanced manifest before clock matching and its balanced manifest
before the independently sorted literal union. The source archive is an overlay
of the bound generation files and inputs; remaining package dependencies come
from the matching repository checkout. The small pipeline's exact 26,422-block
union was reproduced with the prepared source snapshot.

The accepted complete retry retains all 1,144 cells and 183 exterior contacts,
matches the original 115 clock consumers and passes a 27,156,924-block exact
union. It uses the original 128-block reservation except for domain 4's full
sibling group, which reserves 672 blocks before placement. The targeted policy
is explicit and experimental; no matching, collision or isolation check is
disabled. Its separate focused suite contains 19 passing tests, including
original matcher rollback and actual positive/too-fast pipeline timing.

Supply the already audited complete cache plan and a new output directory:

```sh
PYTHONPATH=src:. python experiments/partition_placement/parent_union/run_capped.py \
  --mapped experiments/memory_controller_runtime/evidence/cache-controller-c331.json.gz \
  --top shama_cache_adapter --plan PATH_TO_COMPLETE_CACHE_PLAN.json \
  --clock-padding 128 --clock-domain-padding 4=672 \
  --maximum-y 285 --memory-limit-mib 2048 \
  --wall-seconds 1800 --output build/complete-cache-facade-packed
```

`--prepare-only` retains the exact inputs without running placement. A later
`--run-prepared DIRECTORY` first verifies that the sources, retained inputs,
configuration and ZIP still agree. An existing attempt is never overwritten.
The launcher does not raise the 2,048 MiB ceiling after a failed attempt.

For the accepted historical result, create a fresh checkout of base commit
`f711074956957b95f61c9db307a0d3c303af497d`, then extract
`../evidence/cache-facade-domain4-672-source-bundle.zip` over that checkout.
Run the retained launcher from the checkout with the overlaid `inputs/plan.json`
and `inputs/cache-controller-c331.json.gz`. The 21-file source overlay does not
contain all imported dependencies: the base checkout supplies modules such as
`shamaos.worldio`, used by the literal-union code. This preserves the exact
generation sources even when the working checkout has advanced. Do not replace
the source hashes in the retained manifests with newer checkout hashes. The
artifact and source inventory is
`../evidence/cache-facade-domain4-672-review.json`.

The accepted run took 395.93 seconds with 928,456 KiB peak RSS. Its 136,764
occupied chunks cover the controller and facade only. The original-input clock
model gives 3,843 RT to each register; the facade adds 3,773 RT of common delivery.
Native exterior timing and the complete two-bank transactions are not certified
by this geometry result.
