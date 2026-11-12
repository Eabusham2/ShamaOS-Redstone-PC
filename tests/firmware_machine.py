"""Run shipped machine-code firmware against deterministic device/OS services.

The instructions, branches, cursor copies and program-control flow come from the
actual assembled .asm. Only external services/devices are replaced here; their
RTL is independently covered by the hardware integration testbenches.
"""
from collections import deque
from pathlib import Path

from shamaos.assembler import AssemblyError, assemble
from shamaos.flashfs import FileType, ShamaFS
from shamaos.isa import SYSCALLS
from shamaos.simulator import CPUReference

ROOT = Path(__file__).resolve().parents[1]
GPU = 0x02000000


class FirmwareMachine(CPUReference):
    def __init__(self, app, fs=None):
        super().__init__()
        self.fs = fs or ShamaFS()
        self.events = deque()
        self.idle_count = 0
        self.gpu = bytearray(4096)
        self.draws = []
        self.views = []
        self.file_writes = []
        self.assembly_requests = []
        self.pending_app = None
        self.pending_binary = None
        self.load_app(app)

    def load_app(self, name):
        self.app = name
        self.owner = {'desktop':0,'editor':1,'files':2,'miner':3,'monitor':4,
                      'terminal':5,'calculator':6,'paint':7,'settings':8}[name]
        source = (ROOT / 'src/shamaos/firmware' / (name+'.asm')).read_text()
        result = assemble(source)
        self.labels = result.labels
        self.regs = [0]*16
        self.call_stack.clear()
        self.data_stack.clear()
        self.load_program(result.words)
        self.pending_app = None

    def read_memory(self, addr, size):
        if GPU <= addr < GPU+4096:
            return int.from_bytes(self.gpu[addr-GPU:addr-GPU+size], 'little')
        return super().read_memory(addr, size)

    def write_memory(self, addr, size, value):
        if GPU <= addr < GPU+4096:
            off = addr-GPU
            self.gpu[off:off+size] = (value & ((1 << size*8)-1)).to_bytes(size,'little')
            if off==40 and value:
                op = self.read_memory(GPU+4,4)
                args = [self.read_memory(GPU+8+4*i,4) for i in range(5)]
                data = bytes(self.gpu[0x400+args[0]:0x400+args[0]+args[1]]) if op==12 else b''
                self.draws.append((op,args,data))
            return
        return super().write_memory(addr,size,value)

    def cstring(self, ptr):
        end=self.memory.index(0,ptr,min(len(self.memory),ptr+256))
        return bytes(self.memory[ptr:end]).decode('ascii')

    def syscall(self, sid):
        args=self.regs[1:7]
        a,b,c,d,e,f=args
        r1=r2=0
        try:
            if sid==SYSCALLS['SYS_GET_EVENT']:
                r1=self.events.popleft() if self.events else 0
            elif sid==SYSCALLS['SYS_YIELD']:
                self.idle_count+=1
            elif sid in (SYSCALLS['SYS_EXIT'], SYSCALLS['SYS_APP_LAUNCH']):
                self.pending_app=0 if sid==SYSCALLS['SYS_EXIT'] else a
                self.halted=True
            elif sid==SYSCALLS['SYS_RUN_BUFFER']:
                self._memory_range(a,b)
                self.pending_binary=bytes(self.memory[a:a+b]);self.halted=True
            elif sid==SYSCALLS['SYS_APP_EVENT']:
                pass
            elif sid==SYSCALLS['SYS_FILE_CREATE']:
                name=self.cstring(a)
                self.fs.create_file(name,file_type=FileType(b))
                r1=self.fs.open_file(name,owner=self.owner)
            elif sid==SYSCALLS['SYS_FILE_OPEN']:
                r1=self.fs.open_file(self.cstring(a),owner=self.owner)
            elif sid==SYSCALLS['SYS_FILE_CLOSE']:
                self.fs.close_file(a,owner=self.owner)
            elif sid==SYSCALLS['SYS_FILE_TRANSFER']:
                self.fs.transfer_handle(a,b,owner=self.owner)
            elif sid==SYSCALLS['SYS_FILE_READ']:
                data=self.fs.read_handle(a,c,owner=self.owner)
                self._memory_range(b,len(data))
                self.memory[b:b+len(data)]=data;r1=len(data)
            elif sid==SYSCALLS['SYS_FILE_WRITE']:
                self._memory_range(b,c)
                data=bytes(self.memory[b:b+c])
                name=self.fs._handle_name(a,self.owner)
                self.fs.write_handle(a,data,owner=self.owner)
                self.file_writes.append((name,data));r1=len(data)
            elif sid==SYSCALLS['SYS_FILE_RENAME']:
                self.fs.rename(self.fs._handle_name(a,self.owner),self.cstring(b))
            elif sid==SYSCALLS['SYS_FILE_DELETE']:
                self.fs.delete(self.fs._handle_name(a,self.owner))
            elif sid==SYSCALLS['SYS_FILE_STAT']:
                ent=self.fs.stat(self.fs._handle_name(a,self.owner))
                r1=int(ent.file_type)|(len(ent.name.encode())<<8)|(ent.start_block<<16)
                r2=len(ent.data)
            elif sid==SYSCALLS['SYS_FILE_LIST']:
                entries=self.fs.list_files()
                if a<len(entries):
                    ent=entries[a];name=ent.name.encode()
                    self._memory_range(b,len(name))
                    self.memory[b:b+len(name)]=name
                    r1=int(ent.file_type)|(len(name)<<8)|(ent.start_block<<16)
                    r2=len(ent.data)
            elif sid==SYSCALLS['SYS_ASSEMBLE']:
                self._memory_range(a,b)
                self._memory_range(c,d)
                self.assembly_requests.append((a,b,c,d))
                try:
                    code=assemble(bytes(self.memory[a:a+b]).decode('ascii')).to_bytes()
                    if len(code)>d: raise AssemblyError('output full')
                    self.memory[c:c+len(code)]=code;r1=len(code)
                except AssemblyError:
                    r2=0x105
            elif 0x45 <= sid <= 0x4e:
                self.views.append((sid,tuple(args)))
            elif sid in (SYSCALLS['SYS_GPU_FENCE'],SYSCALLS['SYS_GPU_SUBMIT']):
                pass
            elif sid==SYSCALLS.get('SYS_LIFECYCLE_STATUS'):
                pass
            else:
                raise AssertionError(f'unimplemented harness syscall {sid:#x}')
        except (OSError,ValueError) as exc:
            r1=r2=0xffffffff
        self.regs[1]=r1;self.regs[2]=r2

    def settle(self, limit=400000):
        before=self.idle_count
        for _ in range(limit):
            if self.halted or (self.idle_count>before and not self.events):return
            self.step()
        raise AssertionError(f'firmware did not settle: pc={self.pc}, registers={self.regs}')

    def event(self, code, key=0):
        self.events.append((code<<10)|(key<<18));self.settle()

    def type(self, text):
        for ch in text:self.event(1,13 if ch=='\n' else ord(ch))

    def word(self, addr):
        return int.from_bytes(self.memory[addr:addr+4],'little')
