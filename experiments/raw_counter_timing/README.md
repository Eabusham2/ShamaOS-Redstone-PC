# Exact-cycle raw counter experiment

The time counter and clock divider use the raw clock independently of the CPU's
slower execution clock. A long binary carry chain can therefore remain a
timing constraint after the CPU is slowed down. This isolated experiment stores
the carry prediction for each eight-bit lane one edge before it is needed.
The visible counter still increments once on every edge and resets on the same
edge as the ordinary binary counter. No user-visible count is delayed.

The next carry into a lane is true when all the lower bits are one except the
least significant bit, which is zero. After the current increment those lower
bits will be all ones, so the following increment consumes that predicted carry.
Reset clears both the visible value and the predictions.

## Measurements

Yosys 0.33 and the project's production ABC/redstone mapping produced:

| Counter | Mapped cells | Registers | Cell-template placements | Longest local Q-to-D gates |
| --- | ---: | ---: | ---: | ---: |
| Ordinary 18-bit | 154 | 18 | 7,038 | 29 RT |
| Predicted 18-bit | 157 | 20 | 7,150 | 26 RT |
| Ordinary 64-bit | 592 | 64 | 26,284 | 41 RT |
| Predicted 64-bit | 590 | 71 | 27,214 | 26 RT |

The 64-bit local gate path is 36.6% shorter; its cell-template placement count
is 3.54% higher because a register costs more physical blocks than a small
gate. Interconnect, clock distribution, exposed terminals and chunk footprints
are not included. The delay calculation weights measured local NAND/NOR cells
at 3 RT and NOT at 2 RT. It excludes clock-to-Q, setup, hold, wires and skew;
it is a structural upper-bound calculation on these isolated mapped graphs,
not a sensitized native-world timing result. Mapping the whole shell can change
the gate structure and must be measured separately.

SAT temporal induction proved equal visible counts at every reachable cycle,
with arbitrary synchronous reset, for widths 1, 7, 8, 9, 18 and 64 at eight-bit
lanes, and for 3/1 and 17/4 width/lane configurations. The proof includes
rollover. A deliberately off-by-one prediction failed the same proof. The
archive retains both sources, mapped graphs, proof logs and the measurements.

```sh
PYTHONPATH=src python experiments/raw_counter_timing/verify.py /tmp/counter-proof
```

This remains an isolated experiment. Production RTL, the raw clock period,
resource capacities and release acceptance are unchanged. Physical placement,
route cost and actual setup/hold measurements are required before promotion.
