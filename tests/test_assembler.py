from shamaos.assembler import assemble
from shamaos.isa import decode_header
from shamaos.assembler import AssemblyError
import pytest


def test_labels_and_branch():
    result = assemble("""
        LDI r1 3
    .loop
        DEC r1
        BR.NE .loop
        HLT
    """)
    assert result.labels[".LOOP"] == 2
    assert len(result.words) == 6


def test_sha_program_assembles():
    result = assemble("""
        LDI r1 0x2000
        LDI r2 0x2100
        LDI r3 0x2200
    .loop
        DSHA256 r2 r1
        HASHCMP r2 r3
        BR.VALID .found
        INCNONCE r1
        JMP .loop
    .found
        SYS SYS_MINER_LOG_RESULT
        HLT
    """)
    assert len(result.words) > 10
    first, rd, ra, rb, imm = decode_header(result.words[0])
    assert first.mnemonic == "LDI"
    assert rd == 1


def test_aliases():
    result = assemble("CAL .x\n.x HLT\n")
    assert len(result.words) == 3


@pytest.mark.parametrize('literal', [r"'\u0041'",r"'\U00000041'",r"'\N{LATIN CAPITAL LETTER A}'",r"'\f'",r"'\400'","'é'"])
def test_host_rejects_character_forms_missing_from_the_in_world_language(literal):
    with pytest.raises(AssemblyError,match='line 2'):
        assemble('NOP\nLDI r1 '+literal+'\n')
