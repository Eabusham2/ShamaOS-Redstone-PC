"""Execute real hardware assembly, not just a compile or a source-pattern test."""
import os,shutil,subprocess
from pathlib import Path
import pytest
from shamaos.assembler import assemble
ROOT=Path(__file__).resolve().parents[1]

@pytest.fixture(scope='module')
def rtl_assembler(tmp_path_factory):
    if not shutil.which('iverilog') or not shutil.which('vvp'):
        pytest.skip('Icarus Verilog is required for hardware behavior tests')
    folder=tmp_path_factory.mktemp('hardware_assembler')
    binary=folder/'assembler'
    subprocess.run(['iverilog','-g2012','-s','tb_asm_cases','-o',str(binary),str(ROOT/'rtl/shama_asm_accel.sv'),str(ROOT/'rtl/tb_asm_cases.sv')],check=True,capture_output=True,text=True)
    return binary


def run_case(binary,tmp_path,source,error=0,line=1):
    encoded=source.encode()
    src=tmp_path/'source.hex';expected=tmp_path/'expected.hex'
    src.write_text('\n'.join(f'{b:02x}' for b in encoded)+'\n')
    words=() if error else assemble(source).words
    expected.write_text('\n'.join(f'{w:08x}' for w in words)+'\n')
    result=subprocess.run(['vvp',str(binary),f'+SRC={src}',f'+EXPECT={expected}',f'+LEN={len(encoded)}',f'+OUT={len(words)*4}',f'+ERROR={error}',f'+LINE={line}'],capture_output=True,text=True,timeout=90)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'ASSEMBLER CASE PASS' in result.stdout


@pytest.mark.parametrize('filename',['editor.asm','files.asm','miner.asm','paint.asm','settings.asm','terminal.asm','calculator.asm','desktop.asm','monitor.asm'])
def test_shipped_source_emits_exact_host_binary(rtl_assembler,tmp_path,filename):
    run_case(rtl_assembler,tmp_path,(ROOT/'src/shamaos/firmware'/filename).read_text())


def test_literals_aliases_labels_and_large_source(rtl_assembler,tmp_path):
    source='; padding source above old 4KiB cap\n'*150+'''define VALUE 0b101
start: LI32 r1 ' '
ADI r1 0010
SUBI r1 -2
ANDI r1 0xffffffff
ORI r1 0x10
XORI r1 3
RSH r2 r1
LDI r3 ';'
LDI r4 '/'
LDI r5 '\\n'
LDI r6 '\\x41'
LDI r7 '\\101'
LDI r8 'a'
BRH NZ start
BR.ZERO end
CAL end
end: HLT
'''
    run_case(rtl_assembler,tmp_path,source)


def test_escaped_quotes_backslash_and_full_byte_literals_match(rtl_assembler,tmp_path):
    source = r'''LDI r1 '\''
LDI r2 '\"'
LDI r3 '\\'
LDI r4 '\xff'
LDI r5 '\377'
LDI r6 '\0'
LDI r7 ';'
HLT
'''
    run_case(rtl_assembler,tmp_path,source)


@pytest.mark.parametrize('source,code,line',[
    ('LDI r1 0b2\n',2,1),('LDI r1 12G\n',2,1),('LDI r1 +\n',2,1),
    ('LDI r1 0x100000000\n',2,1),('LDI r1 -2147483649\n',2,1),
    ('LDI r1 /\n',2,1),('LDI r1 \'broken\'\n',2,1),
    ('NOP\nSTW r1 r2 2048\n',6,2),('SYS 4096\n',6,1),
    ('RDPMC r1 4096\n',6,1),('NOP extra\n',6,1),('UNKNOWN\n',5,1),
    ('loop: NOP\nloop: HLT\n',3,2),('define A missing\n',4,1),
    ('BR.NE missing\n',6,1),('LDI r16 1\n',6,1),
])
def test_invalid_source_is_diagnostic_not_silent_reinterpretation(rtl_assembler,tmp_path,source,code,line):
    run_case(rtl_assembler,tmp_path,source,code,line)
