from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Final


class Format(str):
    NONE = "none"
    R = "r"
    RR = "rr"
    RRR = "rrr"
    RRRR = "rrrr"
    RI32 = "ri32"
    J32 = "j32"
    BR32 = "br32"
    MEM = "mem"
    SYS = "sys"
    R_SYS = "r_sys"


@dataclass(frozen=True)
class InstructionSpec:
    mnemonic: str
    opcode: int
    fmt: str
    words: int = 1
    sets_flags: bool = False
    description: str = ""


class Condition(IntEnum):
    EQ = 0
    NE = 1
    C = 2
    NC = 3
    LT = 4
    LE = 5
    GT = 6
    GE = 7
    NEG = 8
    POS = 9
    VALID = 10
    NVALID = 11


CONDITION_ALIASES: Final[dict[str, Condition]] = {
    "EQ": Condition.EQ,
    "Z": Condition.EQ,
    "ZERO": Condition.EQ,
    "NE": Condition.NE,
    "NZ": Condition.NE,
    "NOTZERO": Condition.NE,
    "C": Condition.C,
    "CARRY": Condition.C,
    "NC": Condition.NC,
    "NOTCARRY": Condition.NC,
    "LT": Condition.LT,
    "LE": Condition.LE,
    "GT": Condition.GT,
    "GE": Condition.GE,
    "NEG": Condition.NEG,
    "POS": Condition.POS,
    "VALID": Condition.VALID,
    "NVALID": Condition.NVALID,
}


def _s(m: str, op: int, fmt: str, *, words: int = 1, flags: bool = False, d: str = "") -> InstructionSpec:
    return InstructionSpec(m, op, fmt, words, flags, d)


SPECS: Final[tuple[InstructionSpec, ...]] = (
    _s("NOP", 0x00, Format.NONE, d="No operation"),
    _s("HLT", 0x01, Format.NONE, d="Halt"),
    _s("MOV", 0x02, Format.RR, d="Copy register"),
    _s("LDI", 0x03, Format.RI32, words=2, d="Load 32-bit immediate"),
    _s("LUI", 0x04, Format.RI32, words=2, d="Load upper immediate"),

    _s("ADD", 0x10, Format.RRR, flags=True),
    _s("ADC", 0x11, Format.RRR, flags=True),
    _s("SUB", 0x12, Format.RRR, flags=True),
    _s("SBC", 0x13, Format.RRR, flags=True),
    _s("MUL", 0x14, Format.RRR, flags=True),
    _s("DIV", 0x15, Format.RRR, flags=True),
    _s("MOD", 0x16, Format.RRR, flags=True),
    _s("NEG", 0x17, Format.RR, flags=True),
    _s("INC", 0x18, Format.R, flags=True),
    _s("DEC", 0x19, Format.R, flags=True),

    _s("AND", 0x20, Format.RRR, flags=True),
    _s("OR", 0x21, Format.RRR, flags=True),
    _s("XOR", 0x22, Format.RRR, flags=True),
    _s("NOR", 0x23, Format.RRR, flags=True),
    _s("NAND", 0x24, Format.RRR, flags=True),
    _s("XNOR", 0x25, Format.RRR, flags=True),
    _s("NOT", 0x26, Format.RR, flags=True),
    _s("SHL", 0x27, Format.RRR, flags=True),
    _s("SHR", 0x28, Format.RRR, flags=True),
    _s("SAR", 0x29, Format.RRR, flags=True),
    _s("ROL", 0x2A, Format.RRR, flags=True),
    _s("ROR", 0x2B, Format.RRR, flags=True),

    _s("CMP", 0x30, Format.RR, flags=True),
    _s("TEST", 0x31, Format.RR, flags=True),

    _s("JMP", 0x40, Format.J32, words=2),
    _s("BR", 0x41, Format.BR32, words=2),
    _s("CALL", 0x42, Format.J32, words=2),
    _s("RET", 0x43, Format.NONE),
    _s("PUSH", 0x44, Format.R),
    _s("POP", 0x45, Format.R),

    _s("LDB", 0x50, Format.MEM),
    _s("LDH", 0x51, Format.MEM),
    _s("LDW", 0x52, Format.MEM),
    _s("STB", 0x53, Format.MEM),
    _s("STH", 0x54, Format.MEM),
    _s("STW", 0x55, Format.MEM),
    _s("LEA", 0x56, Format.MEM),

    _s("SYS", 0x60, Format.SYS),
    _s("IRET", 0x61, Format.NONE),
    _s("FENCE", 0x62, Format.NONE),
    _s("CFLUSH", 0x63, Format.NONE),
    _s("RDTIME", 0x64, Format.R),
    _s("RDPMC", 0x65, Format.R_SYS),

    _s("CH", 0x70, Format.RRRR),
    _s("MAJ", 0x71, Format.RRRR),
    _s("BSIG0", 0x72, Format.RR),
    _s("BSIG1", 0x73, Format.RR),
    _s("SSIG0", 0x74, Format.RR),
    _s("SSIG1", 0x75, Format.RR),
    _s("SHAROUND", 0x76, Format.NONE),
    _s("SHA256", 0x77, Format.RR),
    _s("DSHA256", 0x78, Format.RR),
    _s("HASHCMP", 0x79, Format.RR, flags=True),
    _s("INCNONCE", 0x7A, Format.R),
)

BY_NAME: Final[dict[str, InstructionSpec]] = {s.mnemonic: s for s in SPECS}
BY_OPCODE: Final[dict[int, InstructionSpec]] = {s.opcode: s for s in SPECS}

# Compatibility aliases inspired by the teaching computer.
ALIASES: Final[dict[str, str]] = {
    "CAL": "CALL",
    "LOD": "LDW",
    "STR": "STW",
    "RSH": "SHR",
}

SYSCALLS: Final[dict[str, int]] = {
    "SYS_EXIT": 0x001,
    "SYS_YIELD": 0x002,
    "SYS_GET_EVENT": 0x003,
    "SYS_GET_TIME": 0x004,
    "SYS_GET_COUNTER": 0x005,
    "SYS_APP_LAUNCH": 0x006,
    "SYS_APP_EXIT_FOREGROUND": 0x007,
    "SYS_BOOT_MOUNT": 0x008,
    "SYS_BOOT_LOAD_OS": 0x009,
    "SYS_APP_EVENT": 0x00A,

    "SYS_ALLOC": 0x010,
    "SYS_FREE": 0x011,
    "SYS_RAM_USAGE": 0x012,
    "SYS_CACHE_USAGE": 0x013,
    "SYS_CACHE_FLUSH_APP": 0x014,

    "SYS_FILE_CREATE": 0x020,
    "SYS_FILE_OPEN": 0x021,
    "SYS_FILE_CLOSE": 0x022,
    "SYS_FILE_READ": 0x023,
    "SYS_FILE_WRITE": 0x024,
    "SYS_FILE_TRUNCATE": 0x025,
    "SYS_FILE_RENAME": 0x026,
    "SYS_FILE_DELETE": 0x027,
    "SYS_FILE_STAT": 0x028,
    "SYS_FILE_LIST": 0x029,
    "SYS_FLASH_USAGE": 0x02A,

    "SYS_GET_KEY": 0x030,
    "SYS_GET_CONTROLLER": 0x031,

    "SYS_GPU_SUBMIT": 0x040,
    "SYS_DRAW_TEXT": 0x041,
    "SYS_DRAW_RECT": 0x042,
    "SYS_MESSAGE_BOX": 0x043,
    "SYS_GPU_FENCE": 0x044,
    "SYS_UI_REDRAW": 0x045,
    "SYS_UI_STATUSBAR": 0x046,
    "SYS_UI_FILE_LIST": 0x047,
    "SYS_UI_EDITOR_VIEW": 0x048,
    "SYS_UI_MINER_VIEW": 0x049,
    "SYS_UI_MONITOR_VIEW": 0x04A,
    "SYS_UI_TERMINAL_VIEW": 0x04B,
    "SYS_UI_PAINT_VIEW": 0x04C,
    "SYS_UI_SETTINGS_VIEW": 0x04D,

    "SYS_ASSEMBLE": 0x060,

    "SYS_MINER_LOG_RESULT": 0x050,
    "SYS_MINER_SAVE_STATE": 0x051,
    "SYS_MINER_LOAD_HISTORY": 0x052,
}


def canonical_mnemonic(name: str) -> str:
    name = name.upper()
    return ALIASES.get(name, name)


def spec_for(name: str) -> InstructionSpec:
    canonical = canonical_mnemonic(name)
    try:
        return BY_NAME[canonical]
    except KeyError as exc:
        raise KeyError(f"unknown instruction {name!r}") from exc


def encode_header(opcode: int, rd: int = 0, ra: int = 0, rb: int = 0, imm12: int = 0) -> int:
    for reg in (rd, ra, rb):
        if not 0 <= reg <= 15:
            raise ValueError(f"register out of range: r{reg}")
    if not -(1 << 11) <= imm12 < (1 << 12):
        raise ValueError(f"imm12 out of range: {imm12}")
    return (
        ((opcode & 0xFF) << 24)
        | ((rd & 0xF) << 20)
        | ((ra & 0xF) << 16)
        | ((rb & 0xF) << 12)
        | (imm12 & 0xFFF)
    )


def decode_header(word: int) -> tuple[InstructionSpec, int, int, int, int]:
    opcode = (word >> 24) & 0xFF
    spec = BY_OPCODE[opcode]
    rd = (word >> 20) & 0xF
    ra = (word >> 16) & 0xF
    rb = (word >> 12) & 0xF
    imm = word & 0xFFF
    if imm & 0x800:
        imm -= 0x1000
    return spec, rd, ra, rb, imm
