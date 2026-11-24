# Private repeater delays at the nominal native clock

Four existing repeaters in the archived 181-cell startup fixture can satisfy its five local power-control DFF timing checks on the 35/35 redstone-tick lifecycle. A 12 RT POWER lead still loses startup. The two measurements exercise distinct conditions.

The fixture uses research commit `370c24d`, the original mapped shell/cache cone, its two-bank, two-row memory, and the existing 160 RT reset-branch stress. Each variant was emitted to a new Java 1.20.4 Anvil world and reopened. The test changed only the delay properties of already placed repeaters. Each selected repeater belongs to the private route of one mapped sink. The divided-mode C repeaters are at (1520, −31, 347), (1520, −31, 356), and (1520, −31, 366). The power D repeater is at (1674, −31, 355). All 112,143 exported stream records were compared with the saved reference, including full block-state comparisons in changed sections. The unchanged footprints preserve the existing local clearances.

| Variant | Changed repeaters | Divided-mode C setup / hold (RT) | Power D setup / hold (RT) | Violating edges |
| --- | ---: | ---: | ---: | ---: |
| Original route | 0 | 1 / 45 | 69 / 1 | 4 |
| Divided-mode C +7 RT | 3 | 8 / 38 | 69 / 1 | 1 |
| Power D +1 RT | 1 | 1 / 45 | 68 / 2 | 3 |
| Both adjustments | 4 | 8 / 38 | 68 / 2 | 0 |

Across all four full 26,812-tick lifecycle replays, each of the 136 generated core highs was 35 RT wide and entirely under reset. All seven startup, manual reset, shutdown and stale-acknowledgment checkpoints matched. The combined case passed setup and hold on all five power-control DFFs and the six mapped cache receiver DFFs. Its tightest power-control edges land exactly on the 8 RT setup and 2 RT hold requirements. The divided input is a fixture boundary. The lifecycle uses raw pulses while the standalone writes and first deliberate divided edge exercise that boundary separately.

Every lifecycle step continuously checks the 128 real stored bits and the physical row write and commit lines. Cold initialization and the host-write calibration were rerun from separate fresh imports of the combined Anvil world. The timing, prior values and stable storage checks use the actual native readback. The combined variant still lost the 12 RT POWER request. A 55 RT lead produced 47 intervals on the held-power output and no divided-mode entry. Leads of 95 and 157 RT retained the expected first receiver state.

## Replay

The receipt binds the evidence ZIP, the archived baseline, the complete mapped graph, every case, the Java world and the native results. Run `python verify.py` here to recheck the member inventory, scenario and world digests, and recompute all measured register setup and hold values directly from per-tick C and D samples. Use `python verify.py --extract /tmp/nominal-routes` to extract the same evidence for a native replay.

```sh
python reconstruct_stream.py both /tmp/nominal-routes/combined.jsonl
"$SHAMA_MCHPRS_TERRAIN_RUNNER" /tmp/nominal-routes/combined.jsonl \
  /tmp/nominal-routes/scenarios/power-cycle.json 4795
```

The reconstruction restores each emitted stream byte-for-byte from the archived reference and its retained changed records. The ZIP also contains the complete combined Anvil world with its expected block fingerprints. The pinned terrain runner is identified in the parent startup timing archive. Earlier attempts at the combined lifecycle ended with an external SIGKILL. The retained successful replay completed directly under a 384 MiB address-space limit. The operating-system cause of the earlier interruptions remains undetermined.

The private adjustments apply to this measured fixture and source graph. Production clock routing, whole-system clock distribution, nominal-rate software execution and a whole-computer boot remain open.
