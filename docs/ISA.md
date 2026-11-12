# Shama CPU instruction set

This is the implemented contract shared by `src/shamaos/isa.py`, the host
assembler, `rtl/shama_asm_accel.sv`, `rtl/shama_cpu.sv`, and the CPU reference
model. The instruction set extends the concepts in the original MattBatWings
series to a 32-bit machine with general-purpose programs and native SHA.

## Registers, addresses, and encoding

There are sixteen 32-bit general registers. Reads of `r0` always return zero;
writes to it have no architectural effect. Arithmetic wraps modulo 2^32.
Software memory is byte addressed and little endian. The PC, branch targets,
labels, and return addresses are **instruction-word indices**: a PC of 4 fetches
from byte address 16. An instruction with an extension occupies two words.

| Header bits | Meaning |
|---|---|
| 31..24 | Opcode |
| 23..20 | Destination/first register, or branch condition |
| 19..16 | First source/base register |
| 15..12 | Second source/store-data register |
| 11..0 | Signed memory offset, syscall/counter ID, or auxiliary field |

The fourth source register of `CH` and `MAJ` occupies bits 3..0. `LDI`, `LUI`,
the five immediate arithmetic/logic instructions, jumps, calls, and conditional
branches always have one full 32-bit extension word. The exact opcode table is
in `src/shamaos/isa.py`; the assembler listing shows every emitted word.

## Integer and logic operations

Operands below use destination-first order. Commas are optional separators.

| Instructions | Operands / behavior |
|---|---|
| `NOP`, `HLT` | No operands; HLT stops instruction execution until reset or an explicit service jump. |
| `MOV` | `rd, ra` |
| `LDI` | `rd, imm32`; accepts signed values down to −2^31 or unsigned values through 2^32−1. |
| `LUI` | `rd, imm32`; loads `imm32 << 12`, truncated to 32 bits. |
| `ADD`, `ADC` | `rd, ra, rb`; ADC adds the incoming carry. |
| `SUB`, `SBC` | `rd, ra, rb`; SBC subtracts `ra - rb - (1-C)`. |
| `MUL`, `DIV`, `MOD` | `rd, ra, rb`; unsigned operands, low 32 bits of multiply. Division by zero yields `0xffffffff`; modulo by zero yields the dividend. |
| `NEG`, `NOT` | `rd, ra` |
| `INC`, `DEC` | `rd`; update that register in place. |
| `ADDI`, `SUBI`, `ANDI`, `ORI`, `XORI` | `rd, imm32`; update that register in place. These are real opcodes. |
| `AND`, `OR`, `XOR`, `NOR`, `NAND`, `XNOR` | `rd, ra, rb` |
| `SHL`, `SHR`, `SAR`, `ROL`, `ROR` | `rd, ra, rb`; shift/rotation amount is the low five bits of `rb`. SAR is signed right shift. |
| `RSH1` | `rd, ra`; logical right shift by one. |
| `CMP`, `TEST` | `ra, rb`; update flags without writing a result. CMP subtracts; TEST ANDs. |

### Flags

`Z` means zero, `N` is result bit 31, `C` is carry/no-borrow, `V` is signed
overflow, and `VALID` is the 256-bit comparison result. ADD/ADC/SUB/SBC,
ADDI/SUBI, NEG, INC, DEC, and CMP update Z/N/C/V. The other flagged integer
operations update Z/N and preserve C/V. SHA primitives do not overwrite flags.
`HASHCMP` updates VALID.

For subtraction, C=1 means no unsigned borrow. Signed comparisons use `N xor V`,
so a signed overflow does not reverse LT/GE incorrectly.

| Condition | Test |
|---|---|
| EQ / Z / ZERO | Z |
| NE / NZ / NOTZERO | !Z |
| C / CARRY | C |
| NC / NOTCARRY | !C |
| LT | N xor V |
| LE | Z or (N xor V) |
| GT | !Z and !(N xor V) |
| GE | !(N xor V) |
| NEG | N |
| POS | !N, including zero |
| VALID / NVALID | VALID / !VALID |

## Branches and stacks

Use `JMP label`, `BR.EQ label` (or another condition), and `CALL label`.
`RET` pops the return address. `IRET` uses the same call-stack return mechanism;
it is not a separate automatic interrupt controller.

The CPU has independent **256-entry hardware call and data stacks**. `PUSH ra`
and `POP rd` access the data stack. There is no implicit RAM stack pointer in
r13. The 256th entry is usable; the next push faults instead of silently wrapping.
A return/pop from an empty stack also faults.

## Loads and stores

The actual assembler syntax is three operands, without brackets:

```asm
LDI r4, 0x20000
LDI r5, 0x12345678
STW r4, r5, 0
LDB r6, r4, 1       // r6 = 0x56
LDW r7, r4, -4
LEA r8, r4, 12
```

Loads and LEA use `destination, base, signed_offset`; stores use
`base, source, signed_offset`. Offsets range from −2048 through 2047.
`LDB`/`LDH` zero extend. Halfword and word accesses require 2- and 4-byte
alignment. Byte/halfword stores preserve the other lanes of the word.

Installed CPU data regions are RAM `0x00000000..0x0002ffff`, flash
`0x01000000..0x0107cfff`, and the device window
`0x02000000..0x0200ffff`. Individual devices define their registers within that
window. A transfer must fit entirely inside its region. The SoC routes executable
fetches through its fast-memory path and supplies immutable boot instructions
when the OS has not yet loaded.

There are no `MEMCPY` or `MEMSET` opcodes. Programs implement loops or use the
specific OS services that own copying/clearing.

## System and counters

`SYS symbol` presents r1..r6 to the service bus, waits for completion, and returns
r1/r2. r3..r15 are preserved by the hardware syscall boundary. See
[SYSCALLS.md](SYSCALLS.md) for service-specific behavior.

`FENCE` orders this CPU's synchronous memory transactions: an earlier load/store
has already completed before execution continues. It does not wait for the
asynchronous GPU queue; use `SYS_GPU_FENCE` for that. `CFLUSH` requests the app
cache writeback service and waits for it to finish, preserving r1/r2.

`RDTIME rd` reads the low 32 bits of the raw clock counter and captures its high
half. `RDPMC rd, id` provides:

| ID | Value |
|---:|---|
| 0 | CPU clock counter low; captures its high half |
| 1 | Instruction execution counter low; captures its high half |
| 2 | High half captured by the last ID 0 read |
| 3 | High half captured by the last ID 1 read |
| 4 | Raw clock counter low; captures its high half |
| 5 | High half captured by the last ID 4 or RDTIME read |
| 16..31 | r0..r15 |
| 32 | Current PC |
| 33 | Current instruction word |
| 34 | Flags: bit 0 Z, 1 C, 2 N, 3 V, 4 VALID |
| 35 | Fault code |
| 36 | Architectural SHAROUND counter, modulo 64 |

Read the low half before its snapshot high half for a coherent 64-bit value.
These counters count hardware events, not wall-clock seconds. The Python CPU
reference advances its model counter per instruction; cycle-performance
comparisons use actual CPU RTL, not that reference counter.

## SHA execution unit

| Instruction | Exact contract |
|---|---|
| `CH rd, rx, ry, rz` | `(x & y) ^ (~x & z)` |
| `MAJ rd, rx, ry, rz` | `(x & y) ^ (x & z) ^ (y & z)` |
| `BSIG0 rd, rs`, `BSIG1 rd, rs` | SHA-256 big sigma functions |
| `SSIG0 rd, rs`, `SSIG1 rd, rs` | SHA-256 small sigma functions |
| `SHAROUND` | r1..r8 contain A..H; r9 contains the message-schedule word W; r10 contains K. Updates A..H and increments the six-bit round counter. The caller supplies scheduling and final state addition. |
| `SHA256 rDst, rSrc` | Hashes exactly **64 message bytes** from RAM, including the required extra padding block, and writes the standard 32 digest bytes to RAM. |
| `DSHA256 rDst, rHeader` | Double-SHA-256 of exactly **80 header bytes**, writing 32 digest bytes. |
| `HASHCMP rHash, rTarget` | Compares two 32-byte values as unsigned big-endian integers; VALID means hash ≤ target. |
| `INCNONCE rHeader` | Increments the little-endian 32-bit field at header offset 76. |

All SHA input/output pointers must be word aligned, and their complete ranges
must fit in the 192 KiB RAM region. The digest bytes have conventional SHA output
order. Bitcoin's commonly displayed reversed hash string is a separate display
convention; software must not silently substitute it into HASHCMP.

The debug panel exposes the active native compressor round and A..H while a
native operation is busy. Otherwise it exposes the architectural SHAROUND state.
Paused execution retains accelerator completion so resuming does not miss a
one-cycle completion pulse. See [MINER.md](MINER.md) for the independent software
SHA implementation and the comparison benchmark.

## Faults

Faults halt the CPU and retain a visible diagnostic code:

| Code | Cause |
|---:|---|
| 0 | No fault |
| 1 | Unknown opcode |
| 2 | Invalid encoded branch condition |
| 3 / 4 | Call-stack overflow / underflow |
| 5 / 6 | Data-stack overflow / underflow |
| 7 | Misaligned transfer/pointer |
| 8 | Transfer outside an installed region |

## Assembly language and compatibility

Labels can be `.name` or `name:`. `define NAME value` supplies constants. Comments
begin with `//` or `;` outside quoted literals. Decimal, hexadecimal (`0x`), and
binary (`0b`) integers are accepted; zero-padded decimal remains decimal.
Character literals contain exactly one byte. Both assemblers support ordinary
quoted characters, newline/carriage-return/tab/NUL escapes, escaped quotes and
backslash, two-digit hexadecimal byte escapes, and up to three octal digits.

Aliases are `CAL`→CALL, `LOD`→LDW, `STR`→STW, `ADI`→ADDI, and `LI32`→LDI.
`RSH rd, ra` shifts by one; its three-register form means SHR.
`BRH condition, label` and `BR condition, label` mean conditional branch;
`BR label` means JMP. Invalid mnemonics, labels, register numbers, ranges,
characters, and missing operands produce a line/error instead of partial success.
