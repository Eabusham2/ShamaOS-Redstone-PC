from shamaos.assembler import assemble
from shamaos.isa import decode_header


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
