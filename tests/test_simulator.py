from shamaos.assembler import assemble
from shamaos.simulator import CPUReference


def test_arithmetic_loop_program():
    program = assemble("""
        LDI r1 3
        LDI r2 0
    .loop
        INC r2
        DEC r1
        BR.NE .loop
        HLT
    """)
    cpu = CPUReference()
    cpu.load_program(program.words)
    cpu.run()
    assert cpu.regs[2] == 3


def test_call_return():
    program = assemble("""
        LDI r1 1
        CALL .add
        HLT
    .add
        INC r1
        RET
    """)
    cpu = CPUReference()
    cpu.load_program(program.words)
    cpu.run()
    assert cpu.regs[1] == 2
