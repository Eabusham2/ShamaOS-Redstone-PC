"""Exercise the actual shipped miner assembly, including pure-CPU SHA."""
import hashlib
from pathlib import Path
import pytest
from firmware_machine import FirmwareMachine
from shamaos.assembler import assemble
from shamaos.flashfs import FileType,ShamaFS
from shamaos.isa import SYSCALLS
from shamaos.simulator import CPUReference

BASE=0x2d200
HEADER=0x2d000
HASH=0x2d100
TARGET=0x2d140
ROOT=Path(__file__).resolve().parents[1]

class MinerMachine(FirmwareMachine):
    def __init__(self,fs=None):
        if fs is None:
            fs=ShamaFS()
            fs.create_file('miner-state.cfg',bytes(256),FileType.CFG)
            fs.create_file('miner-history.log',bytes(32768),FileType.LOG)
        self.stop_after=None
        super().__init__('miner',fs)
    def step(self):
        # The reference executes one instruction/step. A changing time source
        # lets the firmware's elapsed/rate calculation be exercised separately
        # from the RTL's multi-cycle instruction cost.
        self.time_counter=self.cycle_counter*32
        super().step()
    def write_memory(self,addr,size,value):
        super().write_memory(addr,size,value)
        if addr==0x02000028 and value and self.read_memory(0x02000004,4)==0x16:
            count=self.read_memory(0x02000030,4)+1
            self.gpu[48:52]=(count&0xffffffff).to_bytes(4,'little')
    def syscall(self,sid):
        a,b,c=self.regs[1:4]
        if sid==SYSCALLS['SYS_GET_EVENT'] and self.stop_after is not None and self.word(BASE+16)>=self.stop_after:
            self.events.append(0x15<<10);self.stop_after=None
        if sid==SYSCALLS['SYS_MINER_LOAD_STATE']:
            data=self.fs.read_file('miner-state.cfg')[:b]
            self.memory[a:a+len(data)]=data;self.regs[1]=len(data);self.regs[2]=0
        elif sid==SYSCALLS['SYS_MINER_SAVE_STATE']:
            assert b==256 and not self.word(BASE+252)&1,'torn snapshot reached persistence'
            self.fs.write_file('miner-state.cfg',bytes(self.memory[a:a+b]));self.regs[1]=b;self.regs[2]=0
        elif sid==SYSCALLS['SYS_MINER_LOG_RESULT']:
            assert b==128 and c<256
            history=bytearray(self.fs.read_file('miner-history.log'))
            history[c*128:(c+1)*128]=self.memory[a:a+b]
            self.fs.write_file('miner-history.log',bytes(history));self.regs[1]=b;self.regs[2]=0
        elif sid==SYSCALLS['SYS_MINER_LOAD_HISTORY']:
            data=self.fs.read_file('miner-history.log')[a*128:a*128+c]
            self.memory[b:b+len(data)]=data;self.regs[1]=len(data);self.regs[2]=0
        else:super().syscall(sid)


def configure(machine,key,data):
    machine.type(key+data.hex()+'\n')


def test_miner_line_clear_packs_writes_and_preserves_following_text_bytes():
    source=(ROOT/'src/shamaos/firmware/miner.asm').read_text()
    routine=source.split('\n.line_clear\n',1)[1].split('\n.line_char\n',1)[0]
    program=assemble('define GPU_TEXT 0x02000400\nCALL .line_clear\nHLT\n.line_clear\n'+routine)
    machine=MinerMachine();machine.load_program(program.words)
    machine.gpu[0x400:0x500]=bytes([0xa5])*256
    machine.regs[1:4]=[0x1111,0x2222,0x3333]
    writes=[];original_write=machine.write_memory
    def count_write(address,size,value):
        if 0x02000400<=address<0x02000500:writes.append((address,size))
        original_write(address,size,value)
    machine.write_memory=count_write
    for _ in range(1000):
        if machine.halted:break
        machine.step()
    assert machine.halted
    assert machine.gpu[0x400:0x435]==b' '*53
    assert machine.gpu[0x435:0x500]==bytes([0xa5])*203
    assert machine.regs[1:4]==[0x1111,0x2222,0x3333]
    assert len(writes)==14 and [size for _,size in writes]==[4]*13+[1]


def test_first_boot_full_width_record_and_found_attempt_count():
    m=MinerMachine();m.settle()
    assert m.word(BASE)==0x31524d53 and m.word(BASE+4)==1 and m.word(BASE+24)==0
    assert bytes(m.memory[TARGET:TARGET+32])==b'\x00'+b'\xff'*31
    configure(m,'T',b'\xff'*32)
    m.event(0x14)
    expected=hashlib.sha256(hashlib.sha256(bytes(80)).digest()).digest()
    assert m.word(BASE+16)==1 and m.word(BASE+24)==2
    assert bytes(m.memory[HASH:HASH+32])==expected
    state=m.fs.read_file('miner-state.cfg');history=m.fs.read_file('miner-history.log')[:128]
    assert state[:128]==history
    assert state[48:80]==expected and state[80:112]==b'\xff'*32
    assert state[128:208]==bytes(80)
    assert int.from_bytes(state[120:124],'little')>0


def test_stop_resume_reopen_and_new_generation_preserve_state():
    m=MinerMachine();m.settle();configure(m,'T',bytes(32))
    m.stop_after=3;m.event(0x14)
    assert m.word(BASE+16)==3 and m.word(HEADER+76)==3 and m.word(BASE+12)==2
    assert m.word(BASE+24)==0
    restored=MinerMachine(ShamaFS.deserialize(m.fs.serialize()));restored.settle()
    assert restored.word(BASE+16)==3 and restored.word(HEADER+76)==3
    restored.stop_after=5;restored.event(0x14)
    assert restored.word(BASE+16)==5 and restored.word(HEADER+76)==5
    restored.event(0x10)
    assert restored.word(BASE+8)==1 and restored.word(BASE+16)==0 and restored.word(HEADER+76)==0
    previous=restored.fs.read_file('miner-history.log')[:128]
    assert int.from_bytes(previous[16:20],'little')==5
    assert previous[80:112]==bytes(32)


def test_software_mode_and_measured_comparison():
    m=MinerMachine();m.settle();configure(m,'T',b'\xff'*32)
    m.type('M');assert m.word(BASE+28)==1
    m.event(0x14)
    assert m.word(BASE+16)==1 and m.word(BASE+24)==2
    software_hash=bytes(m.memory[HASH:HASH+32])
    assert software_hash==hashlib.sha256(hashlib.sha256(bytes(80)).digest()).digest()
    assert m.word(BASE+116)>10000
    m.type('C')
    assert m.word(BASE+236)==1
    assert 0<m.word(BASE+112)<m.word(BASE+116)
    assert bytes(m.memory[HASH:HASH+32])==software_hash


def test_configuration_is_exact_and_cancel_preserves_original():
    m=MinerMachine();m.settle();original=bytes(m.memory[TARGET:TARGET+32])
    m.type('T0011ZZ\n')
    assert m.word(BASE+208)==1 and m.word(BASE+212)==4
    assert bytes(m.memory[TARGET:TARGET+32])==original
    m.event(1,27)
    assert m.word(BASE+208)==0 and bytes(m.memory[TARGET:TARGET+32])==original
    header=bytes(range(80))
    configure(m,'H',header)
    assert bytes(m.memory[HEADER:HEADER+80])==header
    assert m.fs.read_file('miner-state.cfg')[128:208]==header
    m.type('D')
    assert m.word(BASE+40)==12 and bytes(m.memory[TARGET:TARGET+32])==bytes([0,15])+bytes([255])*30


def test_nonce_exhaustion_stops_without_wrapping_or_losing_attempt():
    m=MinerMachine();m.settle();configure(m,'T',bytes(32))
    configure(m,'H',bytes(76)+bytes([255])*4)
    m.event(0x14)
    assert m.word(BASE+16)==1 and m.word(BASE+24)==3
    assert m.word(HEADER+76)==0xffffffff
    assert m.word(BASE+12)==0xffffffff
    m.event(0x14)
    assert m.word(BASE+16)==1 and m.word(HEADER+76)==0xffffffff
    restored=MinerMachine(ShamaFS.deserialize(m.fs.serialize()));restored.settle()
    assert restored.word(BASE+24)==3 and restored.word(BASE+244)==2
    restored.event(0x14)
    assert restored.word(BASE+16)==1 and restored.word(HEADER+76)==0xffffffff


def test_found_maximum_nonce_cannot_wrap_when_resumed():
    m=MinerMachine();m.settle();configure(m,'T',bytes([255])*32)
    configure(m,'H',bytes(76)+bytes([255])*4)
    m.event(0x14)
    assert m.word(BASE+24)==2 and m.word(BASE+16)==1
    m.event(0x14)
    assert m.word(BASE+24)==3 and m.word(BASE+16)==1
    assert m.word(HEADER+76)==0xffffffff
    m.event(0x10)
    assert m.word(BASE+244)==0 and m.word(HEADER+76)==0


def test_empty_history_is_identified_without_displaying_a_fake_valid_result():
    m=MinerMachine();m.settle();m.event(0x12)
    assert m.word(BASE+220)==2 and m.word(BASE+216)==255
    m.event(0x11)
    assert m.word(BASE+220)==0


def test_history_slot_wrap_and_attempt_high_word_are_preserved():
    m=MinerMachine();m.settle();configure(m,'T',bytes([255])*32)
    m.event(0x14)
    original=m.fs.read_file('miner-history.log')[:128]
    m.write_memory(BASE+8,4,255)
    m.write_memory(BASE+16,4,0xffffffff)
    m.write_memory(BASE+20,4,3)
    m.event(0x14)
    history=m.fs.read_file('miner-history.log')
    last=history[255*128:]
    assert history[:128]==original
    assert int.from_bytes(last[8:12],'little')==255
    assert int.from_bytes(last[16:24],'little')==4 << 32
    m.event(0x10)
    m.event(0x14)
    history=m.fs.read_file('miner-history.log')
    assert int.from_bytes(history[8:12],'little')==256
    assert history[255*128:]==last


@pytest.mark.parametrize('size',[0,1,32,55,56,64,80,119])
def test_shipped_software_sha_padding_boundaries(size):
    source=(ROOT/'src/shamaos/firmware/miner.asm').read_text()
    # Use the shipped routine, including its constants, rather than a second
    # test implementation of the algorithm.
    library=source[source.index('; CPU-only SHA-256.'):]
    program=assemble(f'CALL .soft_constants\nLDI r1 0x10000\nLDI r2 {size}\nLDI r3 0x10100\nCALL .soft_sha256\nHLT\n'+library)
    data=bytes((i*137+19)&255 for i in range(size))
    cpu=CPUReference();cpu.load_program(program.words);cpu.memory[0x10000:0x10000+size]=data
    cpu.run(30000)
    assert bytes(cpu.memory[0x10100:0x10120])==hashlib.sha256(data).digest()
