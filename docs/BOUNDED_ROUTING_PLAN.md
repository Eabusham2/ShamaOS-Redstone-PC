# Bounded complete-graph routing plan

The new planner preserves the complete lowered graph while limiting the cell,
boundary and routing-terminal workload of each local domain. It is a structural
planning stage. **It does not emit a Minecraft world, close clock/data timing or
replace the production global router.** Physical placement and inter-domain
connections must supply their own evidence before this can become a production
generation path.

## Input and provenance

[`partition_planner.py`](../src/shamaos/hardware/partition_planner.py) reads plain
or gzipped Yosys JSON with an explicit top module. It applies the same sequential
lowering as the physical builder and records the exact mapped JSON hash, the
container hash, the top module and the lowering-source hash. Native cells retain
their expanded names and types. All original port names and bit indices remain
explicit entities, including unused and constant-valued output terminals.

Every native pin must match a supported technology template. An unresolved
constant, missing pin, ambiguous driver or unsupported cell fails explicitly.
The planner does not ground an unknown signal, omit a load or discard a cell
to make a partition fit.

The shared `load_mapped_module(path, top)` loader returns the same lowered module
and provenance to physical placement. This keeps expanded sequential-cell
identities consistent across planning, placement and audit.

## Independent bounds

Four limits apply separately:

| Default limit | Purpose |
|---|---|
| 64 native cells per leaf | Bounds the local placement problem. |
| 128 incoming/outgoing signal terminals per leaf | Prevents a small cell group with a large boundary from being treated as a small routing problem. |
| Four children per hierarchy cluster | Bounds the number of directions at an internal distribution branch. |
| 256 routing terminals per router domain | Prevents an upper hierarchy node from becoming one enormous flat router. |

A deterministic affinity walk groups cells. High-fanout signals may be ignored
when choosing that order; every one remains in the actual routing graph.
If one native cell cannot fit the declared boundary budget, planning stops with
that cell and the required terminal count rather than quietly exceeding it.

Original top-level port bits use explicit bounded interface leaves. A fixed-arity
tree connects all leaves. Each signal follows the minimal tree connecting its
actual source leaf to every destination leaf. This creates explicit directed
fanout branches, including for clocks. Internally generated clocks retain their
original source; they do not become independent replacement clocks.

An internal cluster can need several routing domains. Its complete connections
are divided into bounded domains, and **every domain still needs real physical
space and wiring**. Sharding does not make those wires disappear or establish
that their delays are acceptable.

## Plan interface

`build_plan(Graph.read(path, top))` returns:

| Field | Contents |
|---|---|
| `entities` | Every native cell and original port bit with stable IDs and names. |
| `clusters` | Parent/child IDs and complete leaf ownership. |
| `entity_leaf` | The unique owning leaf for every entity. |
| `leaf_boundaries` | Every incoming/outgoing signal and exact terminal count per leaf. |
| `routing_domains` | Owning cluster, terminal count and every assigned net segment. |
| `signals` | Original source/sinks, directed cluster links and domain assignments. |
| `constants` | Literal values with their original destination identities. |

Every `boundary_links` record has a unique link ID and distinct `:out` / `:in`
terminal IDs. It names the source and destination clusters and owning routing
domains. A null domain denotes the native/interface leaf's local boundary.
These are connection identities; they must be bound to emitted terminals by
the physical implementation.

Placement must preserve these identities when using additional vertical tiers
or horizontal groups. It must measure real local circuitry and routing domains,
reserve their exact envelopes and keep every support above the grass. A planned
box without its circuit is not emitted hardware. Clock/data timing across the
resulting links remains an independent gate.

## Structural audit

`audit_plan(graph, plan)` reconstructs each signal's reachable leaf set from the
actual directed links. It verifies:

- Every original entity is owned exactly once, with unchanged native type and
  original port identity.
- Every source, sink and constant is retained.
- The hierarchy has one connected root, bounded child counts and no cycles.
- Leaf boundaries agree with the original graph and fit their budgets.
- Each internal node on a signal path has exactly one matching routing-domain
  assignment, and every segment implements that path's incoming/outgoing links.
- Every directed edge is reachable from its original signal source. Disconnected
  extra edges, invented nets and empty routing domains are rejected.
- Boundary terminal IDs and domain attachment match those links.
- Per-net clock leaves, domain segment counts, hierarchy depths, all reported
  structural totals and both topology/endpoint hashes are recomputed.

The plan holds an independent entity snapshot so annotating or modifying a plan
does not alter the original graph used by its audit. Source hashes are checked
before accepting the structural evidence.

Negative regression cases remove cells, clock sinks, constants, links and domain
segments; add disconnected valid hierarchy edges, invented signals or empty
domains; change terminal attachment or reported totals/hashes; exceed budgets;
and supply unresolved constants or incomplete physical pins. These failures
cannot be converted into an apparently successful partial plan. The current
planner suite contains 29 passing cases.

## Complete mapped workload evidence

The checked-in compressed fixtures contain the complete default 320×180
display bridge. Their [provenance record](../tests/fixtures/display_bridge_mapping_provenance.json)
identifies the frozen RTL, mapping mode, tool version and exact artifact hashes.
ABC is the production mapping mode for this partition; the AIG mapping is a
separate additional workload. These fixed fixtures test the planner, not future
RTL equivalence or the entire SoC.

| Complete graph, unchanged grouping | Production-mode bridge (ABC) | Additional bridge (AIG) | Current-effective assembler |
|---|---:|---:|---:|
| Native cells after lowering | 10,910 | 13,314 | 559,453 |
| Original port bits | 351 | 351 | 364 |
| Original signal sink uses | 20,944 | 23,328 | 895,698 |
| Clock leaf uses | 649 | 649 | 17,486 |
| Constant terminals | 0 | 649 | 498 |
| Leaf clusters | 177 | 215 | 8,748 |
| Internal clusters | 59 | 73 | 2,917 |
| Tree depth | 4 | 4 | 7 |
| Bounded router domains | 117 | 158 | 10,752 |
| Directed inter-cluster links | 15,487 | 19,572 | 1,343,021 |
| Explicit internal fanout branches | 1,222 | 1,362 | 111,197 |
| Maximum leaf boundary | 108 | 81 | 128 |
| Maximum domain terminals | 256 | 256 | 256 |
| Maximum router fanout | 4 | 4 | 4 |
| Most domains at one cluster | 7 | 13 | 175 |

Both complete bridge plans retain every signal record and pass the independent
reconstruction audit. The assembler mapping was recovered from green CI source
`4e933a5` rather than resynthesized. Its raw assembler RTL and the only referenced
boot macro, `SHAMA_RAM_BYTES=96 KiB`, match the current source; complete
preprocessing is byte-identical. Its mapped JSON SHA-256 is
`78a6ab3abcbbe4bde94acaf6bbfdb8388dbeebf301757f34c49172f97b1664ee`.
That provenance applies to this assembler partition, not every other SoC module.

The current assembler result retains summary/domain inventories and hashes to
bound memory use. A separate oracle independently reconstructs all **559,617
original signals** using subtree endpoint membership and matches the complete
topology and endpoint hashes, domain inventories, clocks and constants. The
external kernel measurement of that audit is 35.67 seconds and 1,534,812 KiB
peak process RSS. It is complete logical reconstruction of a summary hash;
**complete route records are not retained**, so the result cannot be fed directly
to the default retained-plan auditor or physical emitter.

The older 737,585-cell assembler remains historical stress evidence: 1,994,800
links, 15,821 domains and a maximum of 290 domains at a cluster. Its artifact
SHA-256 is `1865d54e9b17f9961a41e5e7e144e862645f8d75bb49447a23e3f0542805d860`.
It is superseded by the current-effective baseline above for current-RTL claims.
Even the current 175-domain cluster is a substantial congestion/placement task;
bounded endpoints do not by themselves establish a smaller Minecraft footprint.

### Audited grouping improvement, not yet a default

Isolated net-aware parent grouping and whole-tree subtree swaps improve the
complete production-mode bridge while keeping every native leaf unchanged:

| Complete ABC topology | Default grouping | Retained candidate | Change |
|---|---:|---:|---:|
| Directed inter-cluster links | 15,487 | 13,176 | −14.92% |
| Bounded router domains | 117 | 96 | −17.95% |
| Most domains at one cluster | 7 | 4 | −42.86% |
| Native cells / original port bits / clock leaves | 10,910 / 351 / 649 | 10,910 / 351 / 649 | Preserved |
| Leaves / internal clusters / hierarchy depth | 177 / 59 / 4 | 177 / 59 / 4 | Preserved |

Every retained candidate passes the complete independent plan audit. A separate
hypergraph-cut oracle also matches every actual link and internal terminal
workload. Repeating the selected two-stage search produced byte-identical
complete plan files. The measured stages took 3.97 and 11.45 seconds; those are
planning costs, not Minecraft execution times.

The production grouping and the placement baseline remain unchanged. Parent
relationships and boundary identities change in the candidate, so full physical
routing and timing must be regenerated before promotion. No chunk, block-count
or clock-speed reduction is claimed from these logical metrics alone.

Compact reports, the compressed complete candidate and reproducible scripts are
preserved under [experiments/partition_planner](../experiments/partition_planner/README.md).
The large recovered assembler mapping remains outside the repository.

A bounded-neighbor search also improves the current-effective assembler summary:

| Complete assembler logical topology | Default grouping | Audited summary candidate | Change |
|---|---:|---:|---:|
| Directed inter-cluster links | 1,343,021 | 1,068,216 | −20.46% |
| Bounded router domains | 10,752 | 8,759 | −18.54% |
| Most domains at one cluster | 175 | 122 | −30.29% |

All 559,453 native cells, 559,617 signals, 895,698 original sinks, 17,486 clock
leaves, 498 constants and 8,748 native/interface leaves remain unchanged. The
independent summary oracle reconstructs every original net and matches both
complete hashes and the domain inventory. Explicit fanout branches increase
from 111,197 to 134,130, so fewer total links must not be interpreted as every
physical cost improving independently.

The exclusive candidate run took 86.73 seconds and peaked at 1,534,544 KiB under
the external kernel measurement. An earlier overlapped process received SIGKILL
during parsing and produced no candidate; it is retained as a failed attempt,
not included as accepted evidence. The successful assembler result remains
**summary-only logical evidence**, with full routes unretained. Its reports,
source hashes and reproduction command are preserved with the bridge experiments.

## Connected physical proof and its limits

[`partition_placement.py`](../src/shamaos/hardware/partition_placement.py) now
implements a separate experimental recursive emitter. It reserves real ancestor
routing height, packs complete child groups into available vertical tiers, then
moves remaining groups horizontally. Each parent/child handoff binds the two
planned terminal identities to one physical contact with distinct access faces.
The production generator has not selected this emitter.

The two-level pipeline in
[`test_partition_placement.py`](../tests/test_partition_placement.py) retains
three native cells, six original port bits, three constant terminals and both
DFF clock leaves across 12 physical modules. An independent artifact traversal
reconstructed all 48 physical endpoint records, 25 local directed connections
and 18 shared contacts from the original sources to their complete sink sets.
It did not reuse the placement auditor to decide connectivity.

For this fixture, the emitter's 13,304 placement operations produce **13,112
unique blocks in 96 occupied X/Z chunks**, within X=0–278, Y=-60–169 and Z=0–132.
Independent regeneration matched every block state in both retained runtime
cases after their common coordinate translation. These are fixture dimensions;
they do not predict the complete computer's footprint.

The actual scheduled engine transfers all 16 tested pipeline cycles correctly
at a 512-redstone-tick clock half-period, with zero observed clock skew. A
deliberately short 20-tick half-period preserves the 16 clock pulses but produces
eight incorrect register transfers. Exact saved-world replay is a separate gate;
this small dense-engine case is not full-bridge or full-machine acceptance.

Physical destructive checks also reject missing zero-only leaves, duplicated
module keys, repeated local nets, additional terminal identities and changed
parent/child contact ownership. An empty or unused original terminal still
requires its actual module and geometry.

## Run the bounded checks

```sh
PYTHONPATH=src:tests python -m pytest -q tests/test_partition_planner.py
PYTHONPATH=src python -m shamaos.hardware.partition_planner \
  tests/fixtures/display_bridge_abc.json.gz --top shama_display_bridge \
  --output build/bounded-display-plan.json
```

The fixtures reuse retained complete mappings; these commands do not launch
another large synthesis job. The output explicitly reports that geometry,
timing and complete-world verification have not run.

The production path still needs complete physical routing and isolation,
route-derived clock/data timing, an exact saved-world resource measurement,
globally connected loaded/ticking behavior, and the OS/lifecycle acceptance
tests described in [VALIDATION.md](VALIDATION.md#full-world-acceptance-still-required).
