"""Architectural short encodings retain legacy semantics and reject truncation."""
import pytest

from shamaos.assembler import AssemblyError, assemble
from shamaos.isa import CONDITION_ALIASES
from shamaos.simulator import CPUReference


IMMEDIATES = ("LDI", "ADDI", "SUBI", "ANDI", "ORI", "XORI")


@pytest.mark.parametrize("op", IMMEDIATES)
@pytest.mark.parametrize("value", [-524288, -2, -1, 0, 524287, 0xFFF80000, 0xFFFFFFFF])
def test_short_immediate_matches_long_value_and_flags(op, value):
    result = []
    for instruction in (op, op + "S"):
        source = f"LDI r1 0x7fffffff\nCMP r0 r1\n{instruction} r1 {value}\nHLT"
        words = assemble(source).words
        cpu = CPUReference()
        cpu.load_program(words)
        cpu.run()
        result.append((cpu.regs, cpu.flags, len(words)))
    assert result[0][:2] == result[1][:2]
    assert result[0][2] == result[1][2] + 1


@pytest.mark.parametrize("op", IMMEDIATES)
@pytest.mark.parametrize("value", [-524289, 524288, 0xFFF7FFFF, 0x100000000, -0x100000001])
def test_short_immediate_rejects_values_that_would_be_truncated(op, value):
    with pytest.raises(AssemblyError, match="signed 20-bit"):
        assemble(f"{op}S r1 {value}")


@pytest.mark.parametrize("condition", CONDITION_ALIASES)
def test_short_condition_spellings_encode_identically(condition):
    expected = (0x47 << 24) | (int(CONDITION_ALIASES[condition]) << 20) | 0xFFFFF
    assert assemble(f"BRS.{condition} 0xfffff").words == (expected,)
    assert assemble(f"BRS {condition} 0xfffff").words == (expected,)


@pytest.mark.parametrize("op,limit", [("JMPS", 0xFFFFFF), ("CALLS", 0xFFFFFF), ("BRS.NE", 0xFFFFF)])
def test_short_targets_are_unsigned_word_addresses(op, limit):
    assert len(assemble(f"{op} {limit}").words) == 1
    for value in (-1, limit + 1):
        with pytest.raises(AssemblyError, match="target out of range"):
            assemble(f"{op} {value}")


def test_mixed_long_and_short_labels_and_nested_return_addresses():
    source = """LDIS r1 2
.loop
CALLS .outer
DEC r1
BRS.NE .loop
JMP .done
.outer
CALL .inner
RET
.inner
ADDIS r2 17
RET
.done
HLT
"""
    assembled = assemble(source)
    assert assembled.labels == {".LOOP": 1, ".OUTER": 6, ".INNER": 9, ".DONE": 11}
    assert assembled.words[0] == 0x05100002
    assert assembled.words[1] == 0x48000006
    assert assembled.words[3] == 0x47100001
    cpu = CPUReference()
    cpu.load_program(assembled.words)
    cpu.run()
    assert cpu.regs[1:3] == [0, 34]
    assert not cpu.call_stack


def test_legacy_encodings_stay_byte_for_byte_compatible():
    assert assemble("LDI r1 -1\nADDI r1 7\nCALL .end\nBR.NE .end\n.end HLT").words == (
        0x03100000, 0xFFFFFFFF, 0x1A100000, 7, 0x42000000, 8,
        0x41100000, 8, 0x01000000,
    )
