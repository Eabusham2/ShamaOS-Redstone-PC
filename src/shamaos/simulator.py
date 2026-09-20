from __future__ import annotations

from dataclasses import dataclass, field

from .isa import Condition, decode_header
from .sha256 import (
    MASK32,
    bsig0,
    bsig1,
    ch,
    double_sha256,
    hash_meets_target,
    maj,
    sha256,
    ssig0,
    ssig1,
)


@dataclass
class Flags:
    z: bool = False
    c: bool = False
    n: bool = False
    v: bool = False
    valid: bool = False


@dataclass
class CPUReference:
    memory_size: int = 1 << 20
    regs: list[int] = field(default_factory=lambda: [0] * 16)
    pc: int = 0
    flags: Flags = field(default_factory=Flags)
    halted: bool = False
    call_stack: list[int] = field(default_factory=list)
    data_stack: list[int] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.memory = bytearray(self.memory_size)
        self.program: list[int] = []

    def load_program(self, words: list[int] | tuple[int, ...]) -> None:
        self.program = [w & MASK32 for w in words]
        self.pc = 0
        self.halted = False

    def _r(self, i: int) -> int:
        return 0 if i == 0 else self.regs[i] & MASK32

    def _w(self, i: int, value: int) -> None:
        if i != 0:
            self.regs[i] = value & MASK32

    def _set_zn(self, value: int) -> None:
        value &= MASK32
        self.flags.z = value == 0
        self.flags.n = bool(value & 0x80000000)

    def _binary_add(self, a: int, b: int, carry: int = 0) -> int:
        total = a + b + carry
        out = total & MASK32
        self.flags.c = total > MASK32
        self.flags.v = bool((~(a ^ b) & (a ^ out) & 0x80000000) != 0)
        self._set_zn(out)
        return out

    def _binary_sub(self, a: int, b: int, borrow: int = 0) -> int:
        total = a - b - borrow
        out = total & MASK32
        self.flags.c = a >= (b + borrow)
        self.flags.v = bool(((a ^ b) & (a ^ out) & 0x80000000) != 0)
        self._set_zn(out)
        return out

    def _cond(self, cond: int) -> bool:
        c = Condition(cond)
        signed_lt = self.flags.n != self.flags.v
        return {
            Condition.EQ: self.flags.z,
            Condition.NE: not self.flags.z,
            Condition.C: self.flags.c,
            Condition.NC: not self.flags.c,
            Condition.LT: signed_lt,
            Condition.LE: signed_lt or self.flags.z,
            Condition.GT: (not signed_lt) and (not self.flags.z),
            Condition.GE: not signed_lt,
            Condition.NEG: self.flags.n,
            Condition.POS: not self.flags.n,
            Condition.VALID: self.flags.valid,
            Condition.NVALID: not self.flags.valid,
        }[c]

    def _read_u32(self, addr: int) -> int:
        if not 0 <= addr <= len(self.memory) - 4:
            raise IndexError("memory read out of range")
        return int.from_bytes(self.memory[addr:addr+4], "little")

    def _write_u32(self, addr: int, value: int) -> None:
        if not 0 <= addr <= len(self.memory) - 4:
            raise IndexError("memory write out of range")
        self.memory[addr:addr+4] = (value & MASK32).to_bytes(4, "little")

    def step(self) -> None:
        if self.halted:
            return
        if not 0 <= self.pc < len(self.program):
            raise IndexError(f"PC out of program: {self.pc}")

        word = self.program[self.pc]
        spec, rd, ra, rb, imm = decode_header(word)
        next_pc = self.pc + 1
        ext = None
        if spec.words == 2:
            if next_pc >= len(self.program):
                raise IndexError("missing extension word")
            ext = self.program[next_pc]
            next_pc += 1

        a, b = self._r(ra), self._r(rb)
        m = spec.mnemonic

        if m == "NOP":
            pass
        elif m == "HLT":
            self.halted = True
        elif m == "MOV":
            self._w(rd, a)
        elif m == "LDI":
            self._w(rd, ext or 0)
        elif m == "LUI":
            self._w(rd, ((ext or 0) << 12) & MASK32)
        elif m == "ADD":
            self._w(rd, self._binary_add(a, b))
        elif m == "ADC":
            self._w(rd, self._binary_add(a, b, 1 if self.flags.c else 0))
        elif m == "SUB":
            self._w(rd, self._binary_sub(a, b))
        elif m == "SBC":
            self._w(rd, self._binary_sub(a, b, 0 if self.flags.c else 1))
        elif m == "MUL":
            out = (a * b) & MASK32
            self._w(rd, out); self._set_zn(out)
        elif m == "DIV":
            if b == 0: raise ZeroDivisionError("DIV by zero")
            out = a // b
            self._w(rd, out); self._set_zn(out)
        elif m == "MOD":
            if b == 0: raise ZeroDivisionError("MOD by zero")
            out = a % b
            self._w(rd, out); self._set_zn(out)
        elif m == "NEG":
            out = (-a) & MASK32
            self._w(rd, out); self._set_zn(out)
        elif m == "INC":
            out = self._binary_add(self._r(rd), 1)
            self._w(rd, out)
        elif m == "DEC":
            out = self._binary_sub(self._r(rd), 1)
            self._w(rd, out)
        elif m in {"AND","OR","XOR","NOR","NAND","XNOR"}:
            if m == "AND": out = a & b
            elif m == "OR": out = a | b
            elif m == "XOR": out = a ^ b
            elif m == "NOR": out = ~(a | b)
            elif m == "NAND": out = ~(a & b)
            else: out = ~(a ^ b)
            out &= MASK32
            self._w(rd, out); self._set_zn(out)
        elif m == "NOT":
            out = (~a) & MASK32
            self._w(rd, out); self._set_zn(out)
        elif m in {"SHL","SHR","SAR","ROL","ROR"}:
            n = b & 31
            if m == "SHL": out = (a << n) & MASK32
            elif m == "SHR": out = a >> n
            elif m == "SAR":
                signed = a if a < 0x80000000 else a - (1 << 32)
                out = (signed >> n) & MASK32
            elif m == "ROL":
                out = ((a << n) | (a >> (32 - n))) & MASK32 if n else a
            else:
                out = ((a >> n) | (a << (32 - n))) & MASK32 if n else a
            self._w(rd, out); self._set_zn(out)
        elif m == "CMP":
            self._binary_sub(self._r(rd), a)
        elif m == "TEST":
            self._set_zn(self._r(rd) & a)
        elif m == "JMP":
            next_pc = int(ext or 0)
        elif m == "BR":
            if self._cond(rd):
                next_pc = int(ext or 0)
        elif m == "CALL":
            self.call_stack.append(next_pc)
            next_pc = int(ext or 0)
        elif m == "RET":
            if not self.call_stack: raise RuntimeError("call stack underflow")
            next_pc = self.call_stack.pop()
        elif m == "PUSH":
            self.data_stack.append(self._r(rd))
        elif m == "POP":
            if not self.data_stack: raise RuntimeError("data stack underflow")
            self._w(rd, self.data_stack.pop())
        elif m in {"LDB","LDH","LDW","LEA"}:
            addr = (a + imm) & MASK32
            if m == "LEA": out = addr
            elif m == "LDB": out = self.memory[addr]
            elif m == "LDH": out = int.from_bytes(self.memory[addr:addr+2], "little")
            else: out = self._read_u32(addr)
            self._w(rd, out)
        elif m in {"STB","STH","STW"}:
            addr = (a + imm) & MASK32
            value = self._r(rb)
            if m == "STB": self.memory[addr] = value & 0xFF
            elif m == "STH": self.memory[addr:addr+2] = (value & 0xFFFF).to_bytes(2, "little")
            else: self._write_u32(addr, value)
        elif m == "CH":
            self._w(rd, ch(a, b, self._r(imm & 0xF)))
        elif m == "MAJ":
            self._w(rd, maj(a, b, self._r(imm & 0xF)))
        elif m == "BSIG0":
            self._w(rd, bsig0(a))
        elif m == "BSIG1":
            self._w(rd, bsig1(a))
        elif m == "SSIG0":
            self._w(rd, ssig0(a))
        elif m == "SSIG1":
            self._w(rd, ssig1(a))
        elif m == "SHA256":
            src = a
            dst = self._r(rd)
            self.memory[dst:dst+32] = sha256(bytes(self.memory[src:src+64]))
        elif m == "DSHA256":
            src = a
            dst = self._r(rd)
            self.memory[dst:dst+32] = double_sha256(bytes(self.memory[src:src+80]))
        elif m == "HASHCMP":
            hash_ptr = self._r(rd)
            target_ptr = a
            self.flags.valid = hash_meets_target(
                bytes(self.memory[hash_ptr:hash_ptr+32]),
                bytes(self.memory[target_ptr:target_ptr+32]),
            )
        elif m == "INCNONCE":
            ptr = self._r(rd) + 76
            nonce = int.from_bytes(self.memory[ptr:ptr+4], "little")
            self.memory[ptr:ptr+4] = ((nonce + 1) & MASK32).to_bytes(4, "little")
        elif m in {"SYS","IRET","FENCE","CFLUSH","RDTIME","RDPMC","SHAROUND"}:
            # Reference hooks are modeled at OS/timing layers.
            pass
        else:
            raise NotImplementedError(m)

        self.regs[0] = 0
        self.pc = next_pc

    def run(self, max_steps: int = 100000) -> int:
        steps = 0
        while not self.halted and steps < max_steps:
            self.step()
            steps += 1
        if not self.halted and steps >= max_steps:
            raise TimeoutError("reference CPU exceeded max_steps")
        return steps
