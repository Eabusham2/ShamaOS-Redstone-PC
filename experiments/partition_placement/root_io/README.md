# Root-level original I/O for bounded cache logic

The planner can keep complete original-port leaves next to the root of the
logic hierarchy. Original inputs and outputs no longer descend through every
logic level before a separate facade routes them back out. Native-cell groups,
every directed signal path, clocks, constants and port identities are retained.
The general planner default is unchanged; callers explicitly request
`ports_at_root=True`.

## Same-graph comparison

This comparison uses the exact historical 8 KiB controller mapping from c3315eb,
with 1,144 native cells, 183 original port bits, 2,155 sink uses and 115 register
clock pins. It compares the complete controller and all original-I/O facade
wires. Physical storage, the row decoder and external memory interconnect are
additional components.

| Complete controller and original I/O | Previous layout | Root-I/O layout | Reduction |
| --- | ---: | ---: | ---: |
| Unique blocks | 27,156,924 | 16,025,544 | 40.99% |
| Occupied chunks | 136,764 | 70,081 | 48.76% |
| Directed hierarchy links | 1,484 | 1,380 | 7.01% |
| Facade handoffs | 494 | 183 | 62.96% |

The native leaf budget remains 64, the boundary budget 128, the router-domain
budget 256 and the arity four. All original signal endpoints have the same
digest. The default planner is also compared against the previous retained
plan to ensure this optional policy has not changed other callers.

The first layout with 128 blocks of clock reservation failed the unchanged
matcher: one private branch needed 62 more delay ticks. Its complete
unbalanced manifest and clock-capacity diagnostic were retained. The accepted
trial reserves 160 blocks for domain 6 and its siblings, retaining the 128-block
baseline elsewhere. The matcher now balances all 115 register clocks; an
independent external-sort union checks every emitted block state, coordinate,
duplicate and bound. Peak resident memory was 857,264 KiB and elapsed time
435.74 seconds. These are generation measurements, not Minecraft execution
speed.

`evidence/root-io-source-inputs.zip` retains the exact reviewed generator files,
mapped graph, full plan, unbalanced and balanced geometry manifests, and the
accepted report. The uncompressed mapped graph SHA-256 remains
`d4989124e9dca8b5afa865d954ded3be680b99f0b1e2f472aed6b5ecc42dd468`.
`evidence/comparison.json` binds the bundle and records the precise savings.

## Native and saved-world checks

The complete small root-I/O pipeline passes in the pinned redstone engine.
Its 16,788 actual circuit blocks survive native save/reopen, and sparse and dense
engines produce identical traces at every declared original and handoff pin.
The positive case compares 958,464 samples. The deliberately faster case
compares 377,856 samples and detects four missed transfers in the second
register while preserving all 16 clock edges and matching clock waveforms.

The old 20-tick half-period stress case is valid for this shorter pipeline.
The retained negative uses eight ticks; the conservative positive uses 512.
These small-circuit results do not establish the full cache's maximum clock
rate or full connected-cache execution.

Run the regression with the pinned native runner environment configured:

```sh
PYTHONPATH=src:tests python -m pytest tests/test_root_port_partition.py
```

The 17-test result and concrete native reports are retained in `evidence`.
Fresh 4 KiB mapping, complete connected-cache geometry and its native runtime
are separate acceptance gates. This experiment does not certify the complete
computer or a releasable Minecraft world.
