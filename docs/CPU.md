# CPU Hardware Contract

## Blocks inside the CPU

The CPU district contains:

- instruction fetch/program counter.
- instruction register/decode.
- control ROM/microcontrol where needed.
- sixteen 32-bit architectural registers.
- integer ALU.
- multiply/divide/modulo execution path.
- 32-bit barrel shift/rotate path.
- condition/flags register.
- branch target/select logic.
- call/data stack interface.
- load/store address generator.
- cache/bus interface.
- syscall/trap interface.
- native SHA execution unit.
- performance counters/debug outputs.

## Pipeline/timing

The final physical timing may be multicycle or pipelined. Unlike the MattBatWings teaching CPU, ShamaOS is not required to remain single-cycle.

Architectural requirements:

- deterministic instruction retirement.
- hazards resolved by stall/forwarding or by explicit multicycle control.
- no instruction observes half-written architectural state.
- GPU/cache/flash waits use request/acknowledge or a defined stall protocol.
- SHA high-level instructions may take many internal cycles.

## Register file

- sixteen 32-bit registers.
- `r0` reads zero and ignores writes if the physical layout supports it without disproportionate cost.
- enough read ports/microcycles to satisfy ALU/SHA operand needs.
- writeback arbitration for integer/load/SHA results.

## Flags

- Z: zero.
- C: carry/no-borrow convention documented by instruction.
- N: sign bit.
- V: signed overflow.
- VALID: 256-bit hash <= target.

Flags are updated only by instructions specified to do so.

## SHA execution unit internals

Required sub-blocks:

- rotate-right network.
- logical shifts.
- XOR network.
- Ch.
- Maj.
- Σ0 / Σ1.
- σ0 / σ1.
- 32-bit modular adder stages.
- SHA K constant ROM.
- W message-schedule state.
- eight-word working state A..H.
- round counter/control.
- feed-forward add.
- second-pass controller for DSHA256.
- 256-bit target comparator.
- nonce increment path.

The unit may pipeline/reuse arithmetic to reduce physical size. Higher latency is acceptable; incorrect math is not.

## Debug visibility

Physical debug taps should expose at least:

- PC.
- current opcode.
- selected register values.
- flags.
- CPU state/stall.
- SHA round number.
- SHA busy.
- cache hit/miss or active-state indicators.

These taps support the System Monitor and physical debugging.
