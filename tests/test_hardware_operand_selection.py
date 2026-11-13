"""Check complete immediate operands through the real hardware assembler."""
import pytest

from test_hardware_assembler import rtl_assembler, run_case


# These instructions consume an immediate at three different source positions.
# Registers and branch conditions must keep their independent source positions.
FORMS = (
    "LDI r3 {value}",
    "JMP {value}",
    "BR.EQ {value}",
    "BRH NZ {value}",
    "LDW r4 r5 {value}",
    "STW r6 r7 {value}",
    "SYS {value}",
    "RDPMC r8 {value}",
)


@pytest.mark.parametrize("same_line_labels", (False, True))
def test_each_immediate_position_resolves_full_values_and_symbols(
    rtl_assembler, tmp_path, same_line_labels
):
    lines = ["define VALUE 37", "define ALIAS VALUE"]
    for index, form in enumerate(FORMS):
        for kind, value in enumerate(("19", "ALIAS", "target", "SYS_GPU_SUBMIT")):
            label = f"prefix{index}_{kind}: " if same_line_labels else ""
            lines.append(label + form.format(value=value))
    # This is a forward label and stays within the memory/system immediate
    # ranges; every expected machine word comes from the host assembler.
    lines.extend(("target: NOP", "HLT"))
    run_case(rtl_assembler, tmp_path, "\n".join(lines) + "\n")


@pytest.mark.parametrize("form", FORMS)
@pytest.mark.parametrize("failure", ("missing", "extra", "unknown"))
def test_bad_immediate_operand_reports_the_original_line_and_code(
    rtl_assembler, tmp_path, form, failure
):
    if failure == "missing":
        instruction = form.format(value="").rstrip()
    elif failure == "extra":
        instruction = form.format(value="23") + " 41"
    else:
        instruction = form.format(value="missing_symbol")
    # A preceding valid line fills token slots. Missing fields must not use
    # those stale values, and the optional label moves the instruction start.
    source = "LDI r1 7\ncurrent: " + instruction + "\nHLT\n"
    run_case(rtl_assembler, tmp_path, source, error=6, line=2)
