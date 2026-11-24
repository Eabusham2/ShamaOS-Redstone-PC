# Registered 16 × 32 input bank

All 32 data columns of this saved Java 1.20.4 bank receive words through native positive-edge D flip-flops. A single seed pulse propagates from each registered row select to the next. The production 35-high/35-low redstone oscillator clocks the data registers and all 16 row registers. The scenario operates 32 saved data levers, one seed lever and the oscillator enable lever. Every native scenario declares an empty external drive map.

The fixture retains the earlier bank's two end-column launch routes, all 512 locked storage repeaters and all 16 row guards. Each additional data register has a separate clock riser and output route. Clock delays are balanced by counting the physical spine repeaters and the additional data repeater on the highest tier. The four tiers keep the registers clear of one another.

| Measured property | Positive saved bank |
|---|---|
| Circuit blocks / saved chunks | 75,255 / 367 |
| Oscillator high / low | 35 / 35 redstone ticks |
| Original endpoint clock arrival / bank terminal arrival | Bit 0 at +8 / +19, bit 31 at +22 / +33 |
| Middle 30 local clocks / launched Q | +25 or +26 / +32 or +33 |
| Middle 30 bank terminals | Every rising and falling data transition at +45, within the earlier +34 through +49 terminal interval |
| Four 16-row scans and idle epochs | 2,048 selected cell writes and 38,912 unselected retention observations |
| Minimum data setup to relock / hold after relock / open duration | 39 / 2 / 35 redstone ticks |
| Observed storage transitions | 895 rises and 647 falls, all inside selected apertures |
| Snapshot / fresh-process result | Zero pending ticks, 512 independently read locked repeater states and 512 matching assertions after a fresh 1,000-tick process |

The word sequence includes complements and independently seeded row words. Every pair of bit columns sees all four combinations. A seed scans the 16 rows once and drains. Four further idle epochs per scan exercise retention with changing data. The upstream classifier, event-qualified advancement across gaps, bank handoff, downstream reads and CDC remain open integration work. The present fixture sets the launch registers' exterior D inputs using levers. Its measured transfer interval applies to the geometry in this archive.

Adding six physical ticks to row control in the same 16 × 32 fixture leaves the endpoint launch registers active but corrupts writes. Sampling columns 0 and 31 across all rows found 96 unstable data apertures, 86 incorrect Q pulse counts or close states and 1,381 wrong retained values. These counts cover repeated observations and can overlap. The two-tick measured positive hold margin remains relevant when integrating new routes.

## Reproduce and inspect

From the repository root, use the pinned terrain runner with SHA-256 `c8032089622501cb57f9edbb0ea7e3be24e3a23fd0b2b80653b0ed4f07045610`. Use distinct new directories for the positive run and the delayed control.

```sh
export PYTHONPATH="$PWD/src:$PWD"
python -m experiments.input_registered_bank.prove build/registered-bank \
  --runner "$SHAMA_MCHPRS_TERRAIN_RUNNER"
python -m experiments.input_registered_bank.challenge build/delayed-bank \
  --runner "$SHAMA_MCHPRS_TERRAIN_RUNNER"
python -m experiments.input_registered_bank.recording build/registered-bank.tar.xz \
  --pack build/registered-bank build/delayed-bank
```

Replay the delivered recording without an engine process. This command verifies
the two approved capture source inventories, reconstructs their original
producer modules in a temporary directory and invokes the reviewer there.
It also works after the production logic templates or other tracked code change.
Offline replay supports Windows without modifying the archived producer. Its
isolated process forbids native subprocesses. Where the POSIX `resource` module
is absent, the historical unused import has no resource methods available;
attempting to invoke its native memory limiter still fails. A fresh native
capture requires the actual Linux runner and its resource limits.

```sh
PYTHONPATH="$PWD/src:$PWD" python -m experiments.input_registered_bank.review_sources \
  experiments/input_registered_bank/evidence.tar.xz
```

The recording contains the positive and negative native Anvil saves, source hashes and source bytes, complete native streams, actual scenarios, compressed raw transitions, snapshot, restored save and fresh-process results. Its SHA-256 is `359a0d44918784c5858dc0b76e47e090074a157eef71287476476fc8c78525f5` (1,279,184 bytes). Replay checks every captured scenario against the physical actuator schedule, reconstructs the original block positions and states, recomputes launch and storage timing from transition traces, and decodes all 512 restored Anvil repeaters. The physical data, clock and row pin coordinates are in `positive/probes.json` inside the hash-addressed recording.


## Compare a newer checkout

The named startup integration changes six of the files in the historical bank
source inventory. A fresh geometry comparison at `a7284c3` reconstructed both
bank layouts from the working source and decoded their captured native Anvil
regions. Both had exactly 75,255 identical block positions and states, 1,830
matching probe names and coordinates, and 34 matching actuators. The separate
`named-startup-geometry-comparison.json` receipt lists each input hash and
the changed files. It carries forward the comparison of the physical layout.
Electrical execution and save/reopen remain bound to the original capture.

To compare the current working source and write a separate receipt:

```sh
PYTHONPATH="$PWD/src:$PWD" python -m experiments.input_registered_bank.review_sources \
  experiments/input_registered_bank/evidence.tar.xz --compare-current build/current-bank-geometry.json
```
