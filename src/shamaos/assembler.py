from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import Path
from typing import Iterable

from .isa import (
    BY_NAME,
    CONDITION_ALIASES,
    Format,
    SYSCALLS,
    canonical_mnemonic,
    encode_header,
    spec_for,
)


class AssemblyError(ValueError):
    pass


@dataclass(frozen=True)
class SourceLine:
    line_no: int
    text: str
    label: str | None
    mnemonic: str | None
    args: tuple[str, ...]


@dataclass(frozen=True)
class AssemblyResult:
    words: tuple[int, ...]
    labels: dict[str, int]
    definitions: dict[str, int]
    listing: tuple[str, ...]

    def to_bytes(self) -> bytes:
        return b"".join(w.to_bytes(4, "little", signed=False) for w in self.words)


def _strip_comment(line: str) -> str:
    quote = None
    escaped = False
    for index, char in enumerate(line):
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char in "'\"":
            quote = char
        elif char == ";" or line[index:index + 2] == "//":
            return line[:index].strip()
    return line.strip()


def _reg(token: str) -> int:
    t = token.strip().lower().rstrip(",")
    if not t.startswith("r"):
        raise AssemblyError(f"expected register, got {token!r}")
    try:
        n = int(t[1:], 10)
    except ValueError as exc:
        raise AssemblyError(f"bad register {token!r}") from exc
    if not 0 <= n <= 15:
        raise AssemblyError(f"register out of range: {token!r}")
    return n


def _int(token: str, symbols: dict[str, int]) -> int:
    t = token.strip().rstrip(",")
    key = t.upper()
    if key in symbols:
        return symbols[key]
    if len(t) >= 3 and t[0] in "'\"" and t[-1] == t[0]:
        body = t[1:-1]
        escapes = {"n":10,"r":13,"t":9,"0":0,"'":39,'"':34,"\\":92}
        if len(body) == 1 and ord(body) < 128 and body != "\\":
            return ord(body)
        if body.startswith("\\"):
            escape = body[1:]
            if escape in escapes:
                return escapes[escape]
            if re.fullmatch(r"x[0-9a-fA-F]{2}",escape):
                return int(escape[1:],16)
            if re.fullmatch(r"[0-7]{1,3}",escape) and int(escape,8) <= 255:
                return int(escape,8)
        raise AssemblyError(f"invalid byte character literal: {token!r}")
    try:
        # Decimal is decimal even when zero-padded. Reject Python-only numeric
        # syntax so host and in-world assemblers accept the same language.
        if not re.fullmatch(r"[+-]?(?:0[xX][0-9a-fA-F]+|0[bB][01]+|[0-9]+)", t):
            raise ValueError(t)
        unsigned = t.lstrip("+-")
        return int(t, 16 if unsigned.lower().startswith("0x") else 2 if unsigned.lower().startswith("0b") else 10)
    except ValueError as exc:
        raise AssemblyError(f"unknown symbol/immediate {token!r}") from exc


def _tokenize_instruction(text: str) -> tuple[str, tuple[str, ...]]:
    # Keep quotes and escapes for the literal parser. shlex(posix=False) ends
    # a token at an escaped quote, unlike the in-world assembler.
    parts = []
    token = []
    quote = None
    escaped = False
    for char in text:
        if quote is not None:
            token.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char.isspace() or char == ",":
            if token:
                parts.append("".join(token))
                token = []
        else:
            token.append(char)
            if char in "'\"":
                quote = char
    if quote is not None:
        raise AssemblyError("unterminated character literal")
    if token:
        parts.append("".join(token))
    if not parts:
        raise AssemblyError("empty instruction")
    return parts[0], tuple(parts[1:])


def parse_source(source: str) -> tuple[list[SourceLine], dict[str, int]]:
    parsed: list[SourceLine] = []
    definitions: dict[str, int] = {}
    declared_labels: set[str] = set()

    for line_no, raw in enumerate(source.splitlines(), 1):
        text = _strip_comment(raw)
        if not text:
            continue

        label = None
        colon_label = re.match(r"^([A-Za-z_.][A-Za-z_0-9.]*)\s*:\s*(.*)$", text)
        if colon_label:
            label = colon_label.group(1).upper()
            text = colon_label.group(2)
        elif text.startswith("."):
            first, *rest = text.split(maxsplit=1)
            label = first.upper()
            text = rest[0] if rest else ""
        if label is not None:
            if label in declared_labels or label in definitions or label in SYSCALLS:
                raise AssemblyError(f"line {line_no}: duplicate/reserved label {label}")
            declared_labels.add(label)
            if not text:
                parsed.append(SourceLine(line_no, raw, label, None, ()))
                continue

        try:
            mnemonic, args = _tokenize_instruction(text)
        except AssemblyError as exc:
            raise AssemblyError(f"line {line_no}: {exc}") from exc
        if mnemonic.lower() == "define":
            if len(args) != 2:
                raise AssemblyError(f"line {line_no}: define syntax is 'define NAME value'")
            name = args[0].upper()
            if name[0] in "0123456789+-'\"":
                raise AssemblyError(f"line {line_no}: a definition name cannot be a literal: {name}")
            if name in definitions or name in declared_labels or name in SYSCALLS:
                raise AssemblyError(f"line {line_no}: duplicate/reserved definition {name}")
            try:
                # Definitions are resolved in source order. A label address is
                # not a constant declaration; syscall constants and earlier
                # definitions are available in both assembler implementations.
                value = _int(args[1], {**SYSCALLS, **definitions})
                if not -(1 << 31) <= value <= 0xFFFFFFFF:
                    raise AssemblyError("definition value out of 32-bit range")
                definitions[name] = value
            except AssemblyError as exc:
                raise AssemblyError(f"line {line_no}: {exc}") from exc
            if label is not None:
                parsed.append(SourceLine(line_no, raw, label, None, ()))
            continue

        if mnemonic.upper() == "BRH" or (mnemonic.upper() == "BR" and len(args) == 2):
            if len(args) != 2:
                raise AssemblyError(f"line {line_no}: BRH requires condition and target")
            mnemonic, args = "BR." + args[0], (args[1],)
        elif mnemonic.upper() == "BR" and len(args) == 1:
            mnemonic = "JMP"
        elif mnemonic.upper() == "BRS" and len(args) == 2:
            mnemonic, args = "BRS." + args[0], (args[1],)
        elif mnemonic.upper() == "RSH" and len(args) == 3:
            mnemonic = "SHR"
        parsed.append(SourceLine(line_no, raw, label, mnemonic, args))

    return parsed, definitions


def _instruction_words(mnemonic: str) -> int:
    upper = mnemonic.upper()
    if upper.startswith("BR."):
        return 2
    if upper.startswith("BRS."):
        return 1
    return spec_for(upper).words


def _resolve_layout(lines: list[SourceLine]) -> dict[str, int]:
    labels: dict[str, int] = {}
    pc = 0
    for line in lines:
        if line.label:
            if line.label in labels:
                raise AssemblyError(f"line {line.line_no}: duplicate label {line.label}")
            labels[line.label] = pc
        if line.mnemonic:
            try:
                pc += _instruction_words(line.mnemonic)
            except KeyError as exc:
                raise AssemblyError(f"line {line.line_no}: unknown instruction {line.mnemonic}") from exc
    return labels


def _expect(args: tuple[str, ...], count: int, line: SourceLine) -> None:
    if len(args) != count:
        raise AssemblyError(
            f"line {line.line_no}: {line.mnemonic} expects {count} argument(s), got {len(args)}"
        )


def _encode(line: SourceLine, symbols: dict[str, int]) -> list[int]:
    assert line.mnemonic is not None
    raw_mn = line.mnemonic.upper()

    if raw_mn.startswith(("BR.", "BRS.")):
        short = raw_mn.startswith("BRS.")
        cond_name = raw_mn.split(".", 1)[1]
        try:
            cond = int(CONDITION_ALIASES[cond_name])
        except KeyError as exc:
            raise AssemblyError(f"line {line.line_no}: unknown branch condition {cond_name}") from exc
        _expect(line.args, 1, line)
        target = _int(line.args[0], symbols)
        if not 0 <= target <= (0xFFFFF if short else 0xFFFFFFFF):
            raise AssemblyError(f"line {line.line_no}: branch target out of range")
        spec = BY_NAME["BRS" if short else "BR"]
        if short:
            return [encode_header(spec.opcode, rd=cond) | target]
        return [encode_header(spec.opcode, rd=cond), target]

    mn = canonical_mnemonic(raw_mn)
    spec = spec_for(mn)
    a = line.args

    if spec.fmt == Format.NONE:
        _expect(a, 0, line)
        return [encode_header(spec.opcode)]

    if spec.fmt == Format.R:
        _expect(a, 1, line)
        return [encode_header(spec.opcode, rd=_reg(a[0]))]

    if spec.fmt == Format.RR:
        _expect(a, 2, line)
        return [encode_header(spec.opcode, rd=_reg(a[0]), ra=_reg(a[1]))]

    if spec.fmt == Format.RRR:
        _expect(a, 3, line)
        return [
            encode_header(spec.opcode, rd=_reg(a[0]), ra=_reg(a[1]), rb=_reg(a[2]))
        ]

    if spec.fmt == Format.RRRR:
        _expect(a, 4, line)
        # fourth register occupies low nibble of imm field
        return [
            encode_header(
                spec.opcode,
                rd=_reg(a[0]),
                ra=_reg(a[1]),
                rb=_reg(a[2]),
                imm12=_reg(a[3]),
            )
        ]

    if spec.fmt == Format.RI32:
        _expect(a, 2, line)
        value = _int(a[1], symbols)
        if not -(1 << 31) <= value <= 0xFFFFFFFF:
            raise AssemblyError(f"line {line.line_no}: immediate out of 32-bit range")
        value &= 0xFFFFFFFF
        return [encode_header(spec.opcode, rd=_reg(a[0])), value]

    if spec.fmt == Format.RI20:
        _expect(a, 2, line)
        value = _int(a[1], symbols)
        # Accept either signed spelling or its full 32-bit two's-complement
        # representation, as with the existing immediate instructions.
        if 0x80000000 <= value <= 0xFFFFFFFF:
            value -= 1 << 32
        if not -(1 << 19) <= value < (1 << 19):
            raise AssemblyError(f"line {line.line_no}: immediate out of signed 20-bit range")
        return [encode_header(spec.opcode, rd=_reg(a[0])) | (value & 0xFFFFF)]

    if spec.fmt in (Format.J32, Format.J24):
        _expect(a, 1, line)
        target = _int(a[0], symbols)
        if not 0 <= target <= (0xFFFFFF if spec.fmt == Format.J24 else 0xFFFFFFFF):
            raise AssemblyError(f"line {line.line_no}: target out of range")
        if spec.fmt == Format.J24:
            return [encode_header(spec.opcode) | target]
        return [encode_header(spec.opcode), target]

    if spec.fmt in (Format.BR32, Format.BR20):
        raise AssemblyError(
            f"line {line.line_no}: use {mn}.<condition> target syntax"
        )

    if spec.fmt == Format.MEM:
        _expect(a, 3, line)
        r0 = _reg(a[0])
        r1 = _reg(a[1])
        off = _int(a[2], symbols)
        if not -2048 <= off <= 2047:
            raise AssemblyError(f"line {line.line_no}: memory offset out of range")
        # Loads/LEA use rd,base,off. Stores use base,src,off.
        if mn.startswith("ST"):
            return [encode_header(spec.opcode, rd=0, ra=r0, rb=r1, imm12=off)]
        return [encode_header(spec.opcode, rd=r0, ra=r1, imm12=off)]

    if spec.fmt == Format.SYS:
        _expect(a, 1, line)
        key = a[0].upper()
        value = SYSCALLS.get(key)
        if value is None:
            value = _int(key, symbols)
        if not 0 <= value <= 0xFFF:
            raise AssemblyError(f"line {line.line_no}: syscall id out of range")
        return [encode_header(spec.opcode, imm12=value)]

    if spec.fmt == Format.R_SYS:
        _expect(a, 2, line)
        value = _int(a[1], symbols)
        if not 0 <= value <= 0xFFF:
            raise AssemblyError(f"line {line.line_no}: counter id out of range")
        return [encode_header(spec.opcode, rd=_reg(a[0]), imm12=value)]

    raise AssemblyError(f"line {line.line_no}: unsupported format {spec.fmt}")


def assemble(source: str) -> AssemblyResult:
    lines, defs = parse_source(source)
    labels = _resolve_layout(lines)
    symbols = {**defs, **labels, **{k: v for k, v in SYSCALLS.items()}}

    words: list[int] = []
    listing: list[str] = []

    for line in lines:
        if not line.mnemonic:
            continue
        try:
            emitted = _encode(line, symbols)
        except (AssemblyError, KeyError, ValueError) as exc:
            if isinstance(exc, AssemblyError) and str(exc).startswith("line "):
                raise
            raise AssemblyError(f"line {line.line_no}: {exc}") from exc
        addr = len(words)
        words.extend(emitted)
        listing.append(
            f"{addr:08x}: "
            + " ".join(f"{w:08x}" for w in emitted)
            + f"    ; line {line.line_no}: {line.text.strip()}"
        )

    return AssemblyResult(tuple(words), labels, defs, tuple(listing))


def assemble_file(path: str | Path) -> AssemblyResult:
    return assemble(Path(path).read_text(encoding="utf-8"))


def write_binary(result: AssemblyResult, path: str | Path) -> None:
    Path(path).write_bytes(result.to_bytes())


def write_listing(result: AssemblyResult, path: str | Path) -> None:
    Path(path).write_text("\n".join(result.listing) + "\n", encoding="utf-8")
