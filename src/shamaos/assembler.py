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


_COMMENT_RE = re.compile(r"(?://|;).*$")


def _strip_comment(line: str) -> str:
    return _COMMENT_RE.sub("", line).strip()


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
        if len(body) != 1:
            raise AssemblyError(f"character literal must contain one character: {token!r}")
        return ord(body)
    try:
        return int(t, 0)
    except ValueError as exc:
        raise AssemblyError(f"unknown symbol/immediate {token!r}") from exc


def _tokenize_instruction(text: str) -> tuple[str, tuple[str, ...]]:
    text = text.replace(",", " ")
    parts = tuple(p for p in text.split() if p)
    if not parts:
        raise AssemblyError("empty instruction")
    return parts[0], parts[1:]


def parse_source(source: str) -> tuple[list[SourceLine], dict[str, int]]:
    parsed: list[SourceLine] = []
    definitions: dict[str, int] = {}

    for line_no, raw in enumerate(source.splitlines(), 1):
        text = _strip_comment(raw)
        if not text:
            continue

        parts = text.split()
        if parts[0].lower() == "define":
            if len(parts) != 3:
                raise AssemblyError(f"line {line_no}: define syntax is 'define NAME value'")
            name = parts[1].upper()
            definitions[name] = _int(parts[2], definitions)
            continue

        label = None
        if text.startswith("."):
            first, *rest = text.split(maxsplit=1)
            label = first.upper()
            text = rest[0] if rest else ""
            if not text:
                parsed.append(SourceLine(line_no, raw, label, None, ()))
                continue

        mnemonic, args = _tokenize_instruction(text)
        parsed.append(SourceLine(line_no, raw, label, mnemonic, args))

    return parsed, definitions


def _instruction_words(mnemonic: str) -> int:
    upper = mnemonic.upper()
    if upper.startswith("BR."):
        return 2
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
            pc += _instruction_words(line.mnemonic)
    return labels


def _expect(args: tuple[str, ...], count: int, line: SourceLine) -> None:
    if len(args) != count:
        raise AssemblyError(
            f"line {line.line_no}: {line.mnemonic} expects {count} argument(s), got {len(args)}"
        )


def _encode(line: SourceLine, symbols: dict[str, int]) -> list[int]:
    assert line.mnemonic is not None
    raw_mn = line.mnemonic.upper()

    if raw_mn.startswith("BR."):
        cond_name = raw_mn.split(".", 1)[1]
        try:
            cond = int(CONDITION_ALIASES[cond_name])
        except KeyError as exc:
            raise AssemblyError(f"line {line.line_no}: unknown branch condition {cond_name}") from exc
        _expect(line.args, 1, line)
        target = _int(line.args[0].upper(), symbols) & 0xFFFFFFFF
        spec = BY_NAME["BR"]
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
        value = _int(a[1].upper(), symbols) & 0xFFFFFFFF
        return [encode_header(spec.opcode, rd=_reg(a[0])), value]

    if spec.fmt == Format.J32:
        _expect(a, 1, line)
        target = _int(a[0].upper(), symbols) & 0xFFFFFFFF
        return [encode_header(spec.opcode), target]

    if spec.fmt == Format.BR32:
        raise AssemblyError(
            f"line {line.line_no}: use BR.<condition> target syntax"
        )

    if spec.fmt == Format.MEM:
        _expect(a, 3, line)
        r0 = _reg(a[0])
        r1 = _reg(a[1])
        off = _int(a[2].upper(), symbols)
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
        value = _int(a[1].upper(), symbols)
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
            if isinstance(exc, AssemblyError):
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
