# Placement, clock ownership and native reload

A structural test reads the integrated 6,934-cell shell and the measured startup map. The shell has one driver for each of `rst`, `core_clk` and `clock_hold`, all on the named startup instance. One raw input child and eleven generated-core children share the startup reset output. The same test exercises the normal flat and bounded generator entry points with the production macro's nine actual signal IDs. For each translated component, every emitted block and exposed contact matched the original island, including the private four-tick repeater on the reset-request register.

The independent runtime review uses the original named island's native save and observes the actual held inhibit and lock repeaters in addition to the public contacts. It verifies both the controller's input assertions and the observed Q for all four native registers seven ticks after each local rising edge. For the cold and fresh scenarios, the inhibit acknowledgment equaled the actual held mask one tick later throughout. The façade released reset only after that acknowledgment was high.

| Scenario | Actual ticks | Complete reset / divided pulses | Result |
|---|---:|---:|---|
| Cold start, manual reset, clean shutdown | 21,772 | 80 / 0 | Shutdown reached zero pending ticks. The off state was saved with all six exterior drive pads clear. |
| Restore and fresh native restart | 17,749 | 44 / 1 | Restored all 393 Anvil chunks. A standing ACK applied after reopening kept the machine in reset through power-up. Withdrawing ACK allowed a 1,024-tick divided pulse. |
| POWER at the initial saved-world sample | 11,927 | 8 / 1 | Replayed the recorded 157-tick POWER-to-raw lead without the preliminary 2,048-tick warmup. All four registers passed their setup, hold and Q checks. |

The reset-request register's minimum setup in the cold and immediate-load cases was exactly its required eight ticks. The 29-cell island's timing depends on the routing and placements exercised here. The production POWER split across the oscillator and startup input, the returning hold signal, the raw divider and time counter, and downstream reset and clock skew require their own integrated evidence. These tests drive the island's six contacts. The restored shutdown snapshot contains a deasserted ACK, and its restart explicitly exercises an ACK asserted after reopen.

The source-bound recording `evidence/independent-reopen.tar.xz` is 379,884 bytes with SHA-256 `080542863a74fe697c6e111ec2744583c05223c3a658c90c4fedf32f3ae9c5f4`. Its scenarios, native outputs, snapshot, restored Anvil regions and full reopened stream can be verified with the following commands.

```sh
export PYTHONPATH="$PWD/src:$PWD"
python -m experiments.named_startup_domain.replay_reopen \
  experiments/named_startup_domain/evidence/independent-reopen.tar.xz
python -m experiments.named_startup_domain.review_reopen build/startup-reopen \
  --runner "$SHAMA_MCHPRS_TERRAIN_RUNNER"
python -m experiments.named_startup_domain.replay_reopen build/startup-reopen \
  --capture-immediate "$SHAMA_MCHPRS_TERRAIN_RUNNER"
```
