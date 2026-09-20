# Shama CPU Instruction Set Architecture

## Goals

ShamaOS deliberately does **not** keep the 16-opcode limit of the MattBatWings teaching machine. The project preserves its useful instruction concepts and expands them into a general-purpose 32-bit ISA with native SHA-256 support.

Default machine properties:

- 32-bit word/data size.
- 32-bit instruction words.
- sixteen GPRs (`r0`–`r15`).
- `r0 = 0` hardwired where physically practical.
- condition flags: Z (zero), C (carry/borrow), N (negative), V (signed overflow), VALID (hash comparator).
- byte-addressed 32-bit logical address space; initial installed RAM is 1 MiB.
- little-endian software representation unless a device explicitly uses network/big-endian byte order.

## Encoding philosophy

The physical CPU uses fixed 32-bit instruction words. Some instructions may consume an extension word for full-width immediates/addresses. The assembler hides extension-word selection.

Logical base encoding:

```text
31          24 23      20 19      16 15      12 11               0
+-------------+----------+----------+----------+-------------------+
|   opcode    |    rd    |    ra    |    rb    | imm12 / function  |
+-------------+----------+----------+----------+-------------------+
```

Exact binary opcode assignment lives in `src/shamaos/isa.py` and is the executable specification.

## Integer/data instructions

- `NOP` — no operation.
- `HLT` — halt current execution until reset/event as defined by OS mode.
- `MOV rd, ra`
- `LDI rd, imm`
- `LUI rd, imm20`
- `ADD rd, ra, rb`
- `ADC rd, ra, rb`
- `SUB rd, ra, rb`
- `SBC rd, ra, rb`
- `MUL rd, ra, rb`
- `DIV rd, ra, rb`
- `MOD rd, ra, rb`
- `NEG rd, ra`
- `INC rd`
- `DEC rd`

## Bitwise/shift instructions

- `AND`
- `OR`
- `XOR`
- `NOR`
- `NAND`
- `XNOR`
- `NOT`
- `SHL`
- `SHR`
- `SAR`
- `ROL`
- `ROR`

Immediate convenience forms may assemble to native opcodes or safe pseudo-instruction sequences:

- `ADDI`
- `SUBI`
- `ANDI`
- `ORI`
- `XORI`

## Compare/control flow

- `CMP ra, rb` — subtract for flags, discard result.
- `TEST ra, rb` — AND for flags, discard result.
- `JMP target`
- `BR.<cond> target`
- `CALL target`
- `RET`
- `PUSH ra`
- `POP rd`

Conditions include:

- `EQ/Z`
- `NE/NZ`
- `C`
- `NC`
- `LT`
- `LE`
- `GT`
- `GE`
- `NEG`
- `POS`
- `VALID`
- `NVALID`

Labels resolve through the assembler.

## Memory

- `LDB rd, [ra+off]`
- `LDH rd, [ra+off]`
- `LDW rd, [ra+off]` — 32-bit word.
- `STB [ra+off], rb`
- `STH [ra+off], rb`
- `STW [ra+off], rb`
- `LEA rd, [ra+off]`

Aliases `LOD` and `STR` are retained for familiarity and map to the configured natural-width load/store or explicit macros.

Optional/microcoded block helpers:

- `MEMCPY`
- `MEMSET`

## System

- `SYS imm` — OS syscall/trap.
- `IRET` — return from trap/event handler.
- `FENCE` — order visible memory/device writes.
- `CFLUSH` — explicit cache flush/invalidate request.
- `RDTIME rd`
- `RDPMC rd, counter`

## Native SHA execution unit

The SHA unit is physically inside the CPU and selected by these opcodes.

Primitive instructions:

- `CH rd, rx, ry, rz` — `(x & y) ^ (~x & z)`.
- `MAJ rd, rx, ry, rz` — majority function.
- `BSIG0 rd, rs` — SHA-256 Σ0.
- `BSIG1 rd, rs` — SHA-256 Σ1.
- `SSIG0 rd, rs` — SHA-256 σ0.
- `SSIG1 rd, rs` — SHA-256 σ1.

Accelerated instructions:

- `SHAROUND` — execute one compression round using architected SHA state/scratch registers.
- `SHA256 ptr_in, ptr_out` — process a prepared SHA-256 message/block sequence according to the instruction contract.
- `DSHA256 header_ptr, hash_ptr` — Bitcoin-style double SHA-256 of the prepared 80-byte header path.
- `HASHCMP hash_ptr, target_ptr` — unsigned 256-bit target comparison; sets VALID.
- `INCNONCE header_ptr` — increment the nonce field according to the miner ABI.

The low-level primitives remain exposed even when high-level instructions are available so SHA behavior can be inspected and programmed manually.

## Pseudo-instructions

Assembler conveniences include:

- `CMP` where a hardware flags-only compare is not separately instantiated.
- `INC/DEC`.
- `LI32` for full-width immediate loading.
- `BR label` aliases.
- `LOD/STR` compatibility aliases.
- `PUSH/POP` may lower to stack-pointer loads/stores if the physical stack path is configured that way.

The assembler listing always shows the actual emitted machine words.

## Assembly language features

- labels: `.name`
- definitions: `define NAME value`
- comments: `// text` or `; text`
- registers: `r0`…`r15`
- decimal, `0x` hex and `0b` binary immediates
- character literals
- syscall symbols
- I/O symbols
- source-to-address listing output

## Why 32-bit

SHA-256 is based on 32-bit words. A 32-bit datapath avoids emulating every SHA operation across four separate 8-bit CPU operations while still allowing classic small redstone programs.
