"""Keep host and physical-assembler constant declarations consistent."""
import pytest

from shamaos.assembler import AssemblyError, assemble
from shamaos.isa import SYSCALLS
from test_hardware_assembler import rtl_assembler, run_case


INVALID_DECLARATIONS = (
    ("origin: NOP\ndefine VALUE origin\nHLT\n", 4, 2),
    ("define VALUE later\nlater: HLT\n", 4, 1),
    ("define VALUE NEXT\ndefine NEXT 7\nHLT\n", 4, 1),
    ("define SYS_EXIT 37\nHLT\n", 4, 1),
    ("SYS_EXIT: NOP\nHLT\n", 3, 1),
    ("define VALUE 3\nVALUE: HLT\n", 3, 2),
    ("VALUE: NOP\ndefine VALUE 3\nHLT\n", 4, 2),
    ("define VALUE 3\ndefine VALUE 4\nHLT\n", 4, 2),
    ("same: define same 3\nHLT\n", 4, 1),
    ("same: define VALUE same\nHLT\n", 4, 1),
    ("define VALUE VALUE\nHLT\n", 4, 1),
    ("define VALUE 0x100000000\nHLT\n", 2, 1),
    ("define VALUE -2147483649\nHLT\n", 2, 1),
    ("define VALUE 1 2\nHLT\n", 4, 1),
    ("define 1 2\nLDI r1 1\nHLT\n", 4, 1),
    ("define 'A' 5\nLDI r1 'A'\nHLT\n", 4, 1),
    ("define -1 2\nLDI r1 -1\nHLT\n", 4, 1),
)


@pytest.mark.parametrize("source,code,line", INVALID_DECLARATIONS)
def test_host_rejects_ambiguous_or_invalid_declarations(source, code, line):
    with pytest.raises(AssemblyError, match=rf"^line {line}:"):
        assemble(source)


@pytest.mark.parametrize("source,code,line", INVALID_DECLARATIONS)
def test_hardware_rejects_ambiguous_or_invalid_declarations(
    rtl_assembler, tmp_path, source, code, line
):
    run_case(rtl_assembler, tmp_path, source, error=code, line=line)


@pytest.mark.parametrize("same_line_labels", (False, True))
def test_all_builtin_syscall_aliases_and_definition_chains_match(
    rtl_assembler, tmp_path, same_line_labels
):
    lines = []
    for index, (name, value) in enumerate(sorted(SYSCALLS.items())):
        prefix = f"decl{index}: " if same_line_labels else ""
        lines.extend((prefix + f"define ALIAS{index} {name}", f"SYS ALIAS{index}"))
    lines.extend((
        "define NEGATIVE -2147483648",
        "define NEGATIVE_COPY NEGATIVE",
        "define UNSIGNED 0xffffffff",
        "define UNSIGNED_COPY UNSIGNED",
        "LDI r1 NEGATIVE_COPY",
        "LDI r2 UNSIGNED_COPY",
        "HLT",
    ))
    source = "\n".join(lines) + "\n"
    result = assemble(source)
    for index, (name, value) in enumerate(sorted(SYSCALLS.items())):
        assert result.definitions[f"ALIAS{index}"] == value
        if same_line_labels:
            assert result.labels[f"DECL{index}"] == index
    assert result.definitions["NEGATIVE_COPY"] == -(1 << 31)
    assert result.definitions["UNSIGNED_COPY"] == 0xFFFFFFFF
    run_case(rtl_assembler, tmp_path, source)


@pytest.mark.parametrize("source", (
    "LDI r0 7\nloop: define EXIT SYS_EXIT\n.define: define VALUE '\\x41'\n"
    "LDI r1 VALUE\nJMP loop\nSYS EXIT\nHLT\n",
    "Define alpha -0x80000000 // signed\nentry: DEFINE beta alpha\nLDI r1 bEtA\n"
    "second: define EX sys_exit ; alias\nSYS EX\nHLT\n",
    "value: define A ';'\ndefine B '/'\nLDI r1 A\nLDI r2 B\nJMP value\nHLT\n",
))
def test_same_line_definitions_keep_address_case_and_quoted_literals(
    rtl_assembler, tmp_path, source
):
    run_case(rtl_assembler, tmp_path, source)
