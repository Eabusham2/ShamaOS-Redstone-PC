"""Independent compact-ISA checks using raw encodings and the real CPU RTL."""
from dataclasses import replace
import random

import pytest

from shamaos.isa import BY_NAME, Condition
from shamaos.simulator import CPUReference, Flags
from test_cpu_rtl import reference, rtl


@pytest.mark.parametrize("op", ["LDI", "ADDI", "SUBI", "ANDI", "ORI", "XORI"])
def test_raw_short_immediates_preserve_random_registers_and_all_flags(op):
    # Construct raw words independently of either assembler. Existing flag
    # values include VALID/C/V, which logical operations must not overwrite.
    rng = random.Random(0x20261114 + BY_NAME[op].opcode)
    for _ in range(256):
        rd = rng.randrange(16)
        value = rng.randrange(-524288, 524288)
        registers = [0] + [rng.getrandbits(32) for _ in range(15)]
        flags = Flags(*[bool(rng.getrandbits(1)) for _ in range(5)])
        legacy = CPUReference(regs=registers.copy(), flags=replace(flags))
        compact = CPUReference(regs=registers.copy(), flags=replace(flags))
        legacy.load_program([(BY_NAME[op].opcode << 24) | (rd << 20), value & 0xffffffff])
        compact.load_program([(BY_NAME[op + "S"].opcode << 24) | (rd << 20) | (value & 0xfffff)])
        legacy.step()
        compact.step()
        assert legacy.regs == compact.regs
        assert legacy.flags == compact.flags
        assert legacy.pc == 2 and compact.pc == 1


def _condition_value(condition, flags):
    signed_less = flags.n != flags.v
    return {
        Condition.EQ: flags.z,
        Condition.NE: not flags.z,
        Condition.C: flags.c,
        Condition.NC: not flags.c,
        Condition.LT: signed_less,
        Condition.LE: signed_less or flags.z,
        Condition.GT: not signed_less and not flags.z,
        Condition.GE: not signed_less,
        Condition.NEG: flags.n,
        Condition.POS: not flags.n,
        Condition.VALID: flags.valid,
        Condition.NVALID: not flags.valid,
    }[condition]


@pytest.mark.parametrize("condition", list(Condition))
def test_raw_short_branch_all_flag_combinations_and_high_target_bits(condition):
    # One-step execution can check the entire architectural target field
    # without pretending that all of those addresses have physical RAM.
    for flag_bits in range(32):
        for target in (0, 1, 0x12345, 0x80000, 0xfffff):
            flags = Flags(*[bool(flag_bits & (1 << bit)) for bit in range(5)])
            legacy = CPUReference(flags=replace(flags))
            compact = CPUReference(flags=replace(flags))
            legacy.load_program([0x41000000 | (int(condition) << 20), target])
            compact.load_program([0x47000000 | (int(condition) << 20) | target])
            taken = _condition_value(condition, flags)
            legacy.step()
            compact.step()
            assert legacy.pc == (target if taken else 2)
            assert compact.pc == (target if taken else 1)
            assert legacy.flags == compact.flags == flags


@pytest.mark.parametrize("op", ["JMP", "CALL"])
def test_raw_short_jump_and_call_keep_all_24_target_bits_and_return_length(op):
    for target in (0, 1, 0xfffff, 0x100000, 0x800000, 0xffffff):
        legacy = CPUReference()
        compact = CPUReference()
        legacy.load_program([BY_NAME[op].opcode << 24, target])
        compact.load_program([(BY_NAME[op + "S"].opcode << 24) | target])
        legacy.step()
        compact.step()
        assert legacy.pc == compact.pc == target
        assert legacy.call_stack == ([2] if op == "CALL" else [])
        assert compact.call_stack == ([1] if op == "CALL" else [])


@pytest.mark.parametrize("stall", [0, 5])
def test_rtl_short_valid_flag_true_and_false_and_long_to_short_nested_call(rtl, stall):
    # HASHCMP, rather than CMP, establishes both states of the VALID flag.
    # Nested calls use the reverse direction of the primary compact-ISA test.
    source = """LDIS r10 0x10000
LDIS r11 0x10020
HASHCMP r10 r11
LDIS r3 0
BRS.VALID .valid
JMPS .fail
.valid
ORIS r3 1
BRS.NVALID .fail
LDIS r2 -1
STW r10 r2 0
HASHCMP r10 r11
BRS.NVALID .invalid
JMPS .fail
.invalid
ORIS r3 2
BRS.VALID .fail
CALL .outer
JMPS .done
.outer
CALLS .inner
RET
.inner
LDIS r4 77
RET
.fail
LDIS r3 -1
.done
HLT
"""
    initial = {0x10000: bytes(64)}
    expected = reference(source, initial)
    registers, _, _, fault = rtl(source, initial, stall=stall)
    assert fault == 0
    assert registers[3] == 3 and registers[4] == 77
    assert [registers[index] for index in range(16)] == expected.regs
