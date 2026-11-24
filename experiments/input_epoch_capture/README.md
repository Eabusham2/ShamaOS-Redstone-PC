# Bounded physical input capture experiment

The production FIFO must retain 16 complete 26-bit records through rapid keyboard presses, simultaneous controllers and universal actions. Its current depth-16 mapping has 21,627 cells. A press can update controller edge history before the new payload reaches the storage register at the 70-redstone-tick raw period.

This experiment keeps the raw period for sampling. The capture RTL registers the 26 physical input levels, rejects ambiguous matrix chords and produces an immutable epoch containing the controller levels, its ten rising edges, and the ordinary key’s index and Shift bank. Shift history advances on every sampled Shift press. The sampled observation token, exact ACK rise and 26-bit ACK payload traverse the same pipeline. Synchronous reset flushes in-flight validity and edge history. A key held through reset becomes a fresh press on the first accepted post-reset sample.

## Why bounded storage suffices between consumer operations

A window starts with its exact ACK, if one was accepted. All arrivals from the same sampled edge follow that ACK. In each window:

1. Retain the first 16 epochs containing any arriving record. Each such epoch either fills at least one free slot or finds a full queue. Occupancy cannot decrease again within this window.
2. The queue is now full. On a further epoch containing any urgent arrivals, only the highest-priority new universal can displace one existing ordinary record. Store that new universal's identity and the complete controller mask. After 16 such epochs all surviving entries are universal.
3. Every arrival after the first 16 eventful epochs sets sticky overflow, including a successful eviction. Ordinary arrivals and subsequent urgent arrivals after the saturated tail can therefore be summarized by one overflow bit. Edge and Shift history continue to sample actual controls, including releases and repeated identical presses.

The first half of a bank stores 16 × 28 bits. The saturated tail stores 16 × 12 bits. An observation fence seals the old bank before classifying that edge’s arrivals. Empty-FIFO reads use the controller levels at the observing edge. A nonempty FIFO returns the stored head's full snapshot. Shift, occupancy, overflow and the selected record all describe the queue before that edge's admissions.

The Python serial-worker model alternates two banks, processes one packet per worker step, and publishes a complete tuple only at retirement. It keeps sampling while the worker or CPU is paused. A consumer must hold a stable request and its payload until a bank-free handshake. An already-issued ACK cannot simply be delayed. The source and gate-level counterexample in `evidence/ack-credit-equivalence-witness.sv` fills the FIFO with A, acknowledges A and presses B. Moving the ACK one edge later loses B and sets sticky overflow.

## Measurements

Run from the repository root with Yosys, Icarus and the native MCHPRS runner available.

```sh
PYTHONPATH=src:tests:. python -m pytest experiments/input_epoch_capture --basetemp=/tmp/input-epoch-proof
python experiments/input_epoch_capture/timing.py /tmp/input-epoch-proof/test_capture_source_and_mapped0/input.json input_capture
python experiments/input_epoch_capture/timing.py /tmp/input-epoch-proof/test_source_and_actual_mapped_0/input.json input_window_bank
```

The source and production-mapped decoder each replayed 32,430 samples, including all 70 raw phases of a recorded 77-actuator physical console sequence. The compact bank source and JSON netlist each passed 1,600 transactions that checked readback of each occupied slot. The independent chronological model covers 109,000 raw samples at five depths. A separate 26,000-sample run stalls the worker and holds consumer requests until credit. The local, native two-register fixture actuates the real A button in the emitted matrix at every raw phase and checks both unmodified C pins on every tick. Each D pulse was 119 RT. Minimum simultaneous D and Q were 113 and 70 RT respectively. This run uses the production block emitter in memory and local isolated clock stimulus pads.

| Separately mapped module | Cells | DFF | Longest internal Q-to-D logic | Input-to-D logic |
| --- | ---: | ---: | ---: | ---: |
| Registered capture | 534 | 197 | 29 RT | 3 RT |
| One compact bank | 4,362 | 675 | 31 RT | 44 RT |

The two banks and capture total 9,258 cells, including 1,547 DFFs, before the worker and crossing logic. These counts describe separate mappings. Measured local cell delays were 3 RT for NAND/NOR, 2 RT for NOT and 7 RT for DFF clock-to-Q. The DFF specifies 8 RT setup and 2 RT hold. At a legal full-prefix state the next urgent write sensitizes a 44-RT path, leaving 11 RT at period 70 for data routing and clock skew. The native two-DFF fixture checks the physical button handoff; the 44-RT bank result comes from a mapped logic replay with zero wire delays.

## Integration and timing work

The bank and capture are independent synthesizable modules. The transaction scheduler, slow queue engine and clock-domain crossing are modeled. Production integration must reserve a fence before an ACK is issued, synchronize ownership, keep held payloads immutable through receipt, and invalidate stale responses on reset. All four input MMIO selectors and the service consumers for notices, universal switching, close decisions and input syscalls require the same atomic snapshot. The external overflow indicator also needs a defined update point when the worker is behind. The raw time counter, clock divider, reset and step paths have separate timing requirements.

Retiming event classification before the bank is the next small physical comparison. Measure a representative routed fan-out and clock tree with native redstone, including both data polarities, setup and hold. Compare its block and route cost against a banked final FIFO that writes payloads in place and updates slot-order metadata. A default worker period requires the emitted route delays, endpoint-specific clock insertion and native validation. No production oscillator or acceptance registry uses this experiment.
