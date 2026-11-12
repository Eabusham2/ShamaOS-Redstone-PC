"""Compare real CPU RTL execution against the architectural reference.

Tools are mandatory in CI's RTL job; a host without Icarus can run Python-only
checks with an explicit skip, never a simulated green hardware claim.
"""
from pathlib import Path
import hashlib
import random
import shutil
import subprocess
import pytest
from shamaos.assembler import assemble
from shamaos.isa import SPECS
from shamaos.simulator import CPUReference

ROOT=Path(__file__).resolve().parents[1]

class Reference(CPUReference):
    def syscall(self, syscall_id):
        self.regs[1]=0x12345678
        self.regs[2]=0x87654321

@pytest.fixture(scope='module')
def rtl(tmp_path_factory):
    if not shutil.which('iverilog') or not shutil.which('vvp'):
        pytest.skip('real CPU RTL execution requires Icarus Verilog')
    build=tmp_path_factory.mktemp('cpu-rtl')
    output=build/'cpu.vvp'
    proc=subprocess.run(['iverilog','-g2012','-s','tb_cpu','-o',str(output),*(str(ROOT/'rtl'/p) for p in ('sha256_compress.sv','sha256_fixed64.sv','bitcoin_dsha256.sv','shama_cpu.sv','tb_cpu.sv'))],capture_output=True,text=True,timeout=60)
    assert proc.returncode==0,proc.stdout+proc.stderr
    def execute(source,initial=None,dump_base=0x80000,dump_words=0,stall=0,hold_sha=False):
        words=assemble(source).words if isinstance(source,str) else source
        program=build/'program.hex'
        lines=['@00000000',*(f'{w:08x}' for w in words)]
        for addr,data in (initial or {}).items():
            assert addr%4==0 and len(data)%4==0
            lines += [f'@{addr//4:08x}',*(f'{int.from_bytes(data[i:i+4],"little"):08x}' for i in range(0,len(data),4))]
        program.write_text('\n'.join(lines)+'\n')
        result=subprocess.run(['vvp',str(output),f'+program={program}',f'+dump_base={dump_base}',f'+dump_words={dump_words}',f'+stall={stall}',f'+hold_sha={int(hold_sha)}'],capture_output=True,text=True,timeout=60)
        assert result.returncode==0,result.stdout+result.stderr
        regs={};memory={};flags=fault=None
        for line in result.stdout.splitlines():
            p=line.split()
            if p and p[0]=='REG': regs[int(p[1])]=int(p[2],16)
            if p and p[0]=='MEM': memory[int(p[1])]=int(p[2],16)
            if p and p[0]=='FLAGS': flags=int(p[1],16)
            if p and p[0]=='FAULT': fault=int(p[1],16)
        return regs,memory,flags,fault
    return execute

def reference(source,initial=None):
    cpu=Reference(time_counter=12345678)
    cpu.load_program(assemble(source).words)
    for addr,data in (initial or {}).items(): cpu.memory[addr:addr+len(data)]=data
    cpu.run(500000)
    return cpu

@pytest.mark.parametrize('stall',[0,3])
def test_integer_flags_immediates_and_sha_primitives_differential(rtl,stall):
    rng=random.Random(0x5A4A)
    values=[0,1,31,32,0x7fffffff,0x80000000,0xfffffffe,0xffffffff]+[rng.getrandbits(32) for _ in range(12)]
    lines=['LDI r15 0x80000']
    cases=0
    binary='ADD ADC SUB SBC MUL DIV MOD AND OR XOR NOR NAND XNOR SHL SHR SAR ROL ROR'.split()
    unary='MOV NEG NOT BSIG0 BSIG1 SSIG0 SSIG1 RSH1'.split()
    for a in values:
        for b in values[:8]+[rng.getrandbits(32)]:
            for op in binary:
                lines += [f'LDI r1 {a}',f'LDI r2 {b}','CMP r0 r1',f'{op} r3 r1 r2','RDPMC r14 34','STW r15 r3 0','STW r15 r14 4','ADDI r15 8']
                cases+=1
        for op in unary+['INC','DEC','ADDI','SUBI','ANDI','ORI','XORI','CH','MAJ','CMP','TEST']:
            lines += [f'LDI r1 {a}',f'LDI r2 {rng.getrandbits(32)}',f'LDI r3 {a}','CMP r0 r1']
            if op in ('INC','DEC'): lines += [f'{op} r3']
            elif op.endswith('I'): lines += [f'{op} r3 {rng.getrandbits(32)}']
            elif op in ('CH','MAJ'): lines += [f'{op} r3 r1 r2 r3']
            elif op in ('CMP','TEST'): lines += [f'{op} r1 r2']
            else: lines += [f'{op} r3 r1']
            lines += ['RDPMC r14 34','STW r15 r3 0','STW r15 r14 4','ADDI r15 8'];cases+=1
    lines += ['HLT'];source='\n'.join(lines)
    cpu=reference(source)
    regs,memory,flags,fault=rtl(source,dump_words=cases*2,stall=stall)
    assert fault==0
    assert [memory[0x80000+4*i] for i in range(cases*2)]==[int.from_bytes(cpu.memory[0x80000+4*i:0x80004+4*i],'little') for i in range(cases*2)]
    assert [regs[i] for i in range(16)]==cpu.regs


def test_calls_branches_stack_width_and_syscalls(rtl):
    source='''
    NOP
    LUI r1 0x12345
    MOV r0 r1
    LDI r5 256
.loop
    PUSH r5
    DEC r5
    BR.NE .loop
    LDI r5 256
.pop
    POP r6
    DEC r5
    BR.NE .pop
    CALL .nested
    SYS SYS_GET_TIME
    RDTIME r4
    RDPMC r7 1
    RDPMC r8 3
    RDPMC r9 20
    RDPMC r10 32
    RDPMC r11 33
    FENCE
    CFLUSH
    HLT
.nested
    CALL .return
    RET
.return
    LDI r12 42
    IRET
'''
    cpu=reference(source);regs,_,_,fault=rtl(source,stall=2)
    assert fault==0
    assert [regs[i] for i in range(16)]==cpu.regs


def test_memory_lane_strobes_and_negative_offsets(rtl):
    source='''LDI r1 0x90004
    LDI r2 0x89abcdef
    STW r1 r2 -4
    LDB r3 r1 -3
    LDH r4 r1 -2
    LDI r5 0x12345678
    STB r1 r5 -3
    STH r1 r5 -2
    LDW r6 r1 -4
    LEA r7 r1 -4
    HLT'''
    cpu=reference(source);regs,mem,_,fault=rtl(source,dump_base=0x90000,dump_words=1,stall=3)
    assert fault==0 and regs[6]==0x567878ef
    assert [regs[i] for i in range(16)]==cpu.regs
    assert mem[0x90000]==0x567878ef


@pytest.mark.parametrize('hold_sha',[False,True])
def test_cpu_native_sha_full_digest_compare_nonce_and_debug_hold(rtl,hold_sha):
    data=bytes(range(80))
    initial={0x90000:data,0x90400:bytes([255])*32}
    source='''LDI r1 0x90000
    LDI r2 0x90200
    SHA256 r2 r1
    LDI r3 0x90300
    DSHA256 r3 r1
    LDI r4 0x90400
    HASHCMP r3 r4
    BR.NVALID .fail
    INCNONCE r1
    RDPMC r7 34
    HLT
.fail
    LDI r15 0xbad
    HLT'''
    regs,mem,_,fault=rtl(source,initial,dump_base=0x90200,dump_words=72,stall=1,hold_sha=hold_sha)
    assert fault==0 and regs[15]==0 and regs[7]&16
    actual=lambda base:b''.join(mem[base+i].to_bytes(4,'little') for i in range(0,32,4))
    assert actual(0x90200)==hashlib.sha256(data[:64]).digest()
    assert actual(0x90300)==hashlib.sha256(hashlib.sha256(data).digest()).digest()


def test_architectural_sha_round_register_contract(rtl):
    source='\n'.join([f'LDI r{i} {i*0x1234567}' for i in range(1,11)]+['SHAROUND']*65+['RDPMC r11 36','HLT'])
    cpu=reference(source);regs,_,_,fault=rtl(source)
    assert fault==0 and regs[11]==1
    assert [regs[i] for i in range(16)]==cpu.regs

@pytest.mark.parametrize('source,code',[
    ([0xff000000],1),([0x41f00000,0],2),('CALL 0',3),('RET',4),
    ('LDI r1 257\n.loop\nPUSH r1\nDEC r1\nBR.NE .loop\nHLT',5),
    ('POP r1',6),('LDI r1 3\nLDW r2 r1 0',7),
    ('LDI r1 0x100000\nLDB r2 r1 0',8),
    ('LDI r1 0xffff0\nLDI r2 0x90000\nDSHA256 r2 r1',8),
])
def test_architectural_faults_are_visible(rtl,source,code):
    assert rtl(source)[3]==code


def test_all_conditional_branches_with_signed_overflow(rtl):
    conditions='EQ NE C NC LT LE GT GE NEG POS VALID NVALID'.split()
    values=[(0,0),(1,0),(0,1),(0x7fffffff,0xffffffff),(0x80000000,1),(0xffffffff,0x80000000)]
    lines=['LDI r15 0x80000'];index=0
    for a,b in values:
        for condition in conditions:
            lines += [f'LDI r1 {a}',f'LDI r2 {b}','CMP r1 r2','LDI r3 0',f'BR.{condition} .taken{index}',f'JMP .done{index}',f'.taken{index}','LDI r3 1',f'.done{index}','STW r15 r3 0','ADDI r15 4'];index+=1
    source='\n'.join(lines+['HLT']);cpu=reference(source)
    _,memory,_,fault=rtl(source,dump_words=index)
    assert fault==0
    assert [memory[0x80000+4*i] for i in range(index)]==[int.from_bytes(cpu.memory[0x80000+4*i:0x80004+4*i],'little') for i in range(index)]


def test_shipped_software_double_sha_runs_on_cpu_rtl_and_matches_native(rtl):
    source=(ROOT/'src/shamaos/firmware/miner.asm').read_text()
    library=source[source.index('; CPU-only SHA-256.'):]
    data=bytes((i*157+81)&255 for i in range(80))
    program='''CALL .soft_constants
LDI r1 0x90000
LDI r3 0x90300
RDPMC r4 0
CALL .soft_dsha256
RDPMC r5 0
SUB r5 r5 r4
LDI r3 0x90400
RDPMC r6 0
DSHA256 r3 r1
RDPMC r7 0
SUB r7 r7 r6
HLT
'''+library
    regs,memory,_,fault=rtl(program,{0x90000:data},dump_base=0x90300,dump_words=72,stall=1)
    assert fault==0
    expected=hashlib.sha256(hashlib.sha256(data).digest()).digest()
    for addr in (0x90300,0x90400):
        assert b''.join(memory[addr+i].to_bytes(4,'little') for i in range(0,32,4))==expected
    assert 0<regs[7]<regs[5]
