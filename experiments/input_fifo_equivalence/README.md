# ShamaOS input FIFO equivalence

This experiment compares two archived versions of the 16-slot input controller. The optimization computes
admission from an event's rank and the number of slots free after an ACK. A
lane shifts when an occupied slot at or before it matches the rising ACK's
entire 26-bit payload. The comparison checks the original simultaneous-write
order, including eviction from the pre-edge FIFO during saturation.

From the repository root, run the following with Yosys, Icarus Verilog and
`vvp` on `PATH`. The output directory must be new.

```bash
python experiments/input_fifo_equivalence/verify.py /tmp/shama-input-check
```

The default inputs are the hash-checked source snapshots in `evidence/`. The
reference came from Git commit `99ee9400c9d7a85418d3432dd5efc9bbc748733a`.
The second snapshot contains the behavior-preserving admission and ACK
optimization before the urgent-eviction correction. The historical widths,
mapping counts, SAT results and simulation coverage are in
`evidence/optimization-receipt.json`. Current FIFO behavior has its own
transaction tests in `tests/test_input_eviction.py`.

Use `--baseline FILE` and `--candidate FILE` to compare explicit sources.
The output includes the exact inputs, SHA-256 hashes, command logs and a JSON
result after successful completion. For a smaller check use
`--simulate-depths 3 --prove-depths 3`.

The simulation compares every stored FIFO slot and history register, the
occupancy, Shift, sticky overflow, and all public outputs before and after
clock edges. Directed and deterministic random inputs exercise full queues,
simultaneous controls, held and malformed acknowledgements, keyboard chords,
Shift, and acknowledgements of previously displayed records.

For a proof, the tool adds one input to load the same arbitrary state into
both copies. The first formal edge loads all stored bits. The following edge
runs the actual FIFO transition, with reset and the ordinary inputs symbolic.
The miter compares the full state and all public outputs following that transition. It permits every Boolean prestored
count, including otherwise unreachable counts. At depth 16 the comparison
covers 450 stored bits and 37 output bits. Yosys must prove the mismatch
signal is always zero. Electrical propagation, clock budgets and complete
machine operation require separate validation.

The subsequent urgent-eviction fix has a separate, source-bound receipt in
`evidence/corrected-transactions.json`. Its oracle compares the physical RTL
array and outputs against chronological list operations. It covers every
universal-button subset with a full ordinary, mixed or universal queue and
checks ACKs against the first, middle and last entries. The original
optimization fails this oracle on the Editor/Files admission collision.

Run the production-mapped snapshot regression from the repository root.

```bash
python -m pytest tests/test_input_eviction.py tests/test_input_mapped_snapshots.py
```

Yosys 0.33 widened the control-event code addition in the old payload
concatenation to 32 bits during Verilog frontend elaboration. Narrowing the
complete assignment to 26 bits discarded the ten controller snapshot bits.
The baseline and the later urgent-eviction RTL both pass the 37-edge
`evidence/controller-snapshot-witness.hex` in source simulation. Both archived
mapped versions return controller mask zero on the sixteenth-slot Editor
replacement at edge 33, where the expected mask is 0x100.

The current RTL concatenates explicitly sized fields. The regression uses the
production synthesis driver, reads the resulting JSON and the production
Liberty DFF model into Yosys, then simulates all 37 public bits and every
stored 26-bit word in both source and mapped implementations. The 14,967 edges
include each of the ten controller buttons at each starting occupancy from
zero through sixteen. Image hashes, the archived pre-fix source and the
matched counts are recorded in `evidence/controller-snapshot.json`. The
earlier 18,030-cell and 19,049-cell images omitted controller snapshot data.
The repaired depth-16 image mapped to 21,627 cells with this toolchain.
The routing and physical-clock acceptance gates still require separate
electrical evidence.
