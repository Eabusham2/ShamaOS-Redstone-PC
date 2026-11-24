# Independent default-height input receiver review

This is a retained component proof. It does not enable a new input peripheral
in the production machine or satisfy a full-world release gate.

The archived producer at `176cef9afb17684220fc007744dc1a7680cd9d35` connects the
shipped control area to 26 physical input registers and ten controller-edge
registers. All circuitry starts above the default grass at Y −61. The separate
source/mapped classifier replay in the archive does not mean those classifier
gates or their CPU connections are physically present in the saved world.

## Independent results

The root reviewer reads the saved Minecraft regions and all 141 original
per-tick trace recordings. It does not import the producer's trace codec,
timing grader, gesture scheduler or RTL classifier. Physical actuator
coordinates identify the expected controls. The review checks every declared
end-of-step assertion again, every clock pulse, qualified D-to-Q transfers,
the complete controller edge word, selected keys and absence of unselected
key pulses. It also verifies the complete region/section stream used by the
native runner against a fresh export of the actual Anvil files.

| Check | Verified result |
|---|---:|
| Native scenarios | 141 |
| Actual control gestures | 6,793 |
| Retained native ticks | 3,203,540 |
| Retained endpoint assertions, rechecked from traces | 27,181 |
| Register samples with a complete setup/hold aperture | 1,001,128 |
| Controller edge-word apertures | 428,970 |
| Saved fixture circuit blocks | 35,495 |
| Saved fixture chunks | 156 |
| Preserved grass/dirt/bedrock blocks | 159,744 |

The 156 chunks include the original console, clock and test connections. The
new receiver's 19,322 owned blocks must not be confused with the complete
35,495-block fixture. No new whole-computer chunk total is claimed.

A separate fresh native invocation reproduced every output tick of the
phase-69 sequence. A second fresh invocation reproduced the earlier layout's
one-tick clock skew; the independent reviewer rejects that layout. The native
runner reports `complete_machine_runtime_verified=false` in both cases.

The archive retains both worlds, all 141 positive recordings, the physical
negative, mapped-netlist pin bindings, classifier replays and the incremental
producer Git bundle. `SHA256.json` inventories all 604 members. The archive
SHA-256 is
`5bbc016e4f02ef8dda45624ee95a4581957daac312b505144c2fb0efe60f92b0`.

## Reproduce

From the repository root with its normal Python dependencies installed:

```sh
PYTHONPATH=src:. python -m experiments.input_receiver_review.review \
  --output build/input-receiver-review.json
```

For the two fresh engine invocations, also set
`SHAMA_MCHPRS_TERRAIN_RUNNER` to the pinned terrain-aware MCHPRS runner and add
`--native`. The runner binary's SHA-256 is checked before execution. Temporary
native stdout is used to compare every output sample with the retained raw
trace; only the compact receipt is kept. The native report contains wall-clock
and memory measurements, so its complete stdout digest is an execution record,
not a deterministic result identifier.

The [root receipt](../../docs/evidence/root-default-height-input-review-2026-11-24.json)
identifies the reviewed source and both fresh invocations. Its exact verifier
source is retained as `evidence/native-reviewer-source.py.gz`. The current
portable offline verifier differs only by removing host recompression of the
historical gzip transport; all reconstructed JSON hashes and per-tick checks
remain mandatory. The separately recorded portable pytest rerun identifies that
version. Input admission,
cross-clock requests/responses, packet storage, the complete worker, the CPU,
rendered-client use and whole-world execution remain open integration gates.
