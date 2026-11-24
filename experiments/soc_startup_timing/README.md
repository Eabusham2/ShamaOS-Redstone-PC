# Generated-core startup and guarded cache timing

The experimental clock handoff misses the production 35-redstone-tick (RT) half-period. On a saved four-word cache fixture, startup, manual reset, shutdown and repower produced the expected outputs, but three divided-mode register edges had only 1 RT of setup and the power register had only 1 RT of hold. The mapped DFF requires 8 RT of setup and 2 RT of hold. This directory preserves the research branch and native evidence alongside production, without changing the shell used to build the PC.

| Raw clock high / low | Lifecycle ticks | Minimum setup across five DFFs | Minimum hold | Edges below contract |
| --- | ---: | ---: | ---: | ---: |
| 35 / 35 RT | 26,812 | 1 RT | 1 RT | 4 |
| 70 / 70 RT | 45,432 | 40 RT | 1 RT | 1 |
| 140 / 140 RT | 82,672 | 36 RT | 7 RT | 0 |
| 280 / 280 RT | 157,152 | 130 RT | 7 RT | 0 |

Each row uses the same saved fixture, a 2,048 RT POWER lead before the first raw clock, and local clock and data probes on the five registers. The measured nominal violations occurred at ticks 5,290, 10,960 and 24,786 on the divided-mode register and at 15,344 on the power register. The 140 / 140 result applies to this lifecycle and these pins. The physical console inputs have 119 RT pulses. Whole-system timing, full clock fanout, relative POWER and raw clock routing, and whole-PC boot remain open.

## Physical fixture and witnesses

The Yosys-mapped shell clock and reset cone drives the actual guarded cache controller's write cone and a two-bank, two-row storage cut. It comprises 181 cells, 490,298 assigned blocks and 4,667 saved chunks. The proof saves and reopens the Java 1.20.4 Anvil files. The extra 160 RT of delay on the final write guard's reset branch comes from actual repeater settings. The exposed divider boundary supplies deliberate core edges on request. This fixture fixes the write data at zero and includes four mixed preloaded 32-bit words. It does not contain the complete divider, CPU, 4 KiB cache fabric or routed computer.

| Profile | Stimulus and observation |
| --- | --- |
| Cold load | All 128 bits kept their original values over 1,537 ticks, with no generated clock or row-write pulse. |
| Power lifecycle | The nominal case produced 136 full-width 35 RT highs while reset was asserted. Every stored bit and the physical write lines were checked throughout startup, manual reset, delayed shutdown acknowledgment and repower with stale acknowledgment. |
| Receiver cases | At **35 / 35 RT**, the idle, preasserted valid write, and out-of-range write profiles each ran 15,866 ticks. All 17 raw reset pulses arrived under the local reset qualifiers for all six mapped cache registers. Each first deliberate divided edge produced the expected cache state. The last raw high ended 131–152 RT before local reset release. |
| Host write | The standalone write calibration uses **8,192 RT half-periods**. A separate 211,132-tick run energized one physical row-write after repower, wrote zero to row 0 and retained the other 96 bits. |
| POWER lead | At 35 / 35 RT, a 12 RT lead lost the request and a 55 RT lead caused the held-power output to oscillate. The tested 95 and 157 RT leads passed. The repaired standalone oscillator's observed cold enable-to-rise interval was 157 RT. Its eventual differential route delay against POWER at the shell remains unmeasured. |

Earlier designs with a superficially correct source waveform failed at the actual storage or register pins. Each native witness includes its own stream, exact scenario and runner result.

| Archive prefix | Observed failure |
| --- | --- |
| `witness/rtl` | The original RTL produced a zero-duration startup high. The phase-safe RTL regression records the before and after event ordering. |
| `witness/boolean` | The mapped Boolean clock selector generated a false clock and a physical row-write before POWER. The delayed reset branch allowed row 0 to change from `A55AC33C` to `00000000`. |
| `witness/pre-return` | The first core high began at tick 4,496 after source reset ended at 4,311. With a live request, cache state advanced without capturing write enable. |
| `witness/positive1` | A returned clock receipt covered the first local edge, but further raw pulses escaped after reset release. Early address `0x1000` set cache state bit 2 at tick 5,603 before the deliberate divided edge. The following state was POST_COMMIT instead of the required RESP. |
| `witness/mask2` | The returned inhibit used a transient grant. Under a stable powered input, the divided-mode output repeatedly cycled with a 980 RT period. |

## Verify and replay

From a checkout containing this directory, Python 3.11 or newer can verify every archived file, match each native result to its imported world and scenario SHA-256, check the Anvil region hashes, and recompute the timing table directly from per-tick D and C samples. The verifier can also extract the files into a new directory.

```sh
python3 experiments/soc_startup_timing/verify.py --extract /tmp/shama-startup-evidence
```

For a direct native replay, use the terrain-aware MCHPRS runner built by the pinned steps in `.github/workflows/cache-world-validation.yml`. These captures used MCHPRS revision `f9e10d2572116bf5dcb9a36a305f28e40641a61a` and runner SHA-256 `c8032089622501cb57f9edbb0ea7e3be24e3a23fd0b2b80653b0ed4f07045610`. The final argument allows 4,667 loaded chunks plus the export's 128-chunk reserve. Native runner status describes successful execution of the scenario. The independent DFF timing gate is computed from the resulting traces.

```sh
evidence=/tmp/shama-startup-evidence
"$SHAMA_MCHPRS_TERRAIN_RUNNER" "$evidence/final/35/world.jsonl" \
    "$evidence/final/35/power-cycle.json" 4795 > /tmp/shama-startup-replay35.json
"$SHAMA_MCHPRS_TERRAIN_RUNNER" "$evidence/final/140/world.jsonl" \
    "$evidence/final/140/power-cycle.json" 4795 > /tmp/shama-startup-replay140.json
```

To rebuild the maps and the physical world, restore the research branch in a detached worktree. The bundle requires public base commit `044feadda8aeef19c4c36ce941e0f360569364cc` in the local repository. The recorded mapper was Yosys 0.33, git revision `2584903a060`. Install the candidate's Python dependencies and supply the terrain runner through the environment variable above.

```sh
git bundle verify "$evidence/source/research.bundle"
git fetch "$evidence/source/research.bundle" refs/heads/work/returned-reset-clock
git worktree add --detach /tmp/shama-startup-research 370c24df4db7de1d9d34dec6272f40bb90e49a34
python3 experiments/soc_startup_timing/verify.py --source /tmp/shama-startup-research
cd /tmp/shama-startup-research
python3 tools/prove_soc_cold_clock.py --output /tmp/shama-fresh35 --power-half-period 35
python3 tools/prove_soc_cold_clock.py --output /tmp/shama-fresh140 --power-half-period 140
```

Use new output directories and run the commands serially. The 35 RT command intentionally exits 1 after saving all functional and receiver results because its DFF timing measurements fail. Each command sets the power-lifecycle half-period only. The receiver profiles always use 35 / 35 RT and the write calibration always uses 8,192 RT. The archived 140 RT command completed cold, write and power-lifecycle execution and recorded its positive local timing observations. Its process was killed during the subsequent 35 RT receiver replay. The three completed nominal receiver runs are retained under `final/35`.

The archive contains the candidate as a prerequisite-bound Git bundle, exact intermediate RTL for the two uncommitted witnesses, all 25 native scenario/result/stream bindings, the nominal final Anvil world and maps, and a per-file SHA-256 manifest. The source inventories of the intermediate worlds identify the generators used for those captures. Their saved streams permit direct native replay without rebuilding those intermediate experiments.
