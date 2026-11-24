# STEP storage and admitted core pulses

The original 43-cell STEP control is the complete fan-in of the mapped grant and its three registers, cut at the 23 actual shell boundary bits. A single 119-redstone-tick STEP input produced 23 separate 70-tick requests before any consume was possible. The test uses a compact placement of the original cells.

A 40-cell candidate stores the set reason from its physical clock gate. Joined to the production 29-cell startup island and five wired interconnects, it held one press across a raw-clock stop and restart. The named controller emitted one complete 2,100-tick divided pulse and suppressed the second. The same recording shows the request clearing at tick 22,935, before the applied counter reached its predecessor at 23,216.

The decisive control stops all further counting at `0x1fff9`. The normal core clock stays low and the startup reset remains released, yet the write gate still clears the request. The published counter inputs and raw clock are external stimuli for this bounded world. The archive contains the actual two cold Anvil worlds, native streams, mapped source, scenarios and full per-tick outputs. All three finite cases ended with zero scheduled events.

Run the independent offline review from the repository root.

```sh
python experiments/step_control_timing/review_predecessor.py
```

For a fresh physical replay, specify the pinned terrain runner. The verifier checks its SHA-256, runs three fresh imports and compares every observed tick.

```sh
python experiments/step_control_timing/review_predecessor.py --runner "$SHAMA_MCHPRS_TERRAIN_RUNNER"
```

The transient wide decode fails the STEP ownership requirement. Whole-machine timing, generated console edges and naturally reached full-period divider counts remain outside these measurements.
