"""Execute the shipped calculator and inspect the decimal/error drawing bytes."""
import pytest

from firmware_machine import FirmwareMachine

B = 0x15


class CalculatorMachine(FirmwareMachine):
    def __init__(self):
        self.fenced_draw_counts = []
        super().__init__("calculator")

    def syscall(self, syscall_id):
        if syscall_id == 0x044:
            self.fenced_draw_counts.append(len(self.draws))
        super().syscall(syscall_id)


def rendered_value(machine):
    rows = [data for op, args, data in machine.draws if op == 12 and args[3] == 48]
    return rows[-1].decode("ascii").strip()


def calculate(expression):
    machine = CalculatorMachine()
    machine.settle()
    machine.type(expression)
    machine.event(1, 13)
    return machine


@pytest.mark.parametrize("expression,expected", [
    ("12+30", 42), ("2-3", -1), ("12*3", 36), ("7/3", 2), ("7%3", 1),
    ("-7/3", -2), ("7/-3", -2), ("-7/-3", 2),
    ("-7%3", -1), ("7%-3", 1), ("-7%-3", -1),
    ("-12*3", -36), ("12*-3", -36), ("-12*-3", 36),
    ("-2--3", 1), ("-2+-3", -5), ("-0", 0),
    ("2147483647", 2147483647), ("-2147483648", -2147483648),
    ("2147483647+0", 2147483647), ("-2147483648-0", -2147483648),
    ("-2147483648+2147483647", -1), ("-2147483648/1", -2147483648),
    ("-2147483648%-1", 0), ("-2147483648*-0", 0),
    ("-2147483648*1", -2147483648), ("-1073741824*2", -2147483648),
    ("46340*46340", 2147395600),
])
def test_signed_decimal_operations_and_boundaries(expression, expected):
    machine = calculate(expression)
    assert machine.regs[5] == 0
    assert machine.regs[8] == expected & 0xFFFFFFFF
    assert rendered_value(machine) == str(expected)
    assert machine.regs[11] == 2
    # Every final operand/result is actually presented and fenced.
    assert [op for op, args, data in machine.draws][-3:] == [0x13, 0x0C, 0x14]
    assert machine.fenced_draw_counts[-1] == len(machine.draws)


@pytest.mark.parametrize("expression,error,text", [
    ("7/0", 1, "DIVISION BY ZERO"), ("7%0", 1, "DIVISION BY ZERO"),
    ("0/0", 1, "DIVISION BY ZERO"),
    ("2147483648", 2, "OVERFLOW"), ("-2147483649", 2, "OVERFLOW"),
    ("2147483647+1", 2, "OVERFLOW"), ("-2147483648-1", 2, "OVERFLOW"),
    ("2147483647--1", 2, "OVERFLOW"), ("46341*46341", 2, "OVERFLOW"),
    ("-2147483648/-1", 2, "OVERFLOW"), ("-2147483648*-1", 2, "OVERFLOW"),
    ("1073741824*2", 2, "OVERFLOW"),
])
def test_invalid_arithmetic_has_visible_errors_and_clear_recovery(expression, error, text):
    machine = calculate(expression)
    assert machine.regs[5] == error
    assert rendered_value(machine) == text
    assert machine.views[-1][1][2] == error
    machine.event(B)
    assert machine.regs[5] == 0 and rendered_value(machine) == "0"
    machine.type("6/2")
    machine.event(1, 13)
    assert rendered_value(machine) == "3"


def test_rejected_input_digit_preserves_prefix_and_can_be_corrected():
    machine = CalculatorMachine()
    machine.settle()
    machine.type("2147483648")
    assert machine.regs[8] == 214748364 and rendered_value(machine) == "OVERFLOW"
    machine.event(1, 8)  # Dismiss error without deleting the accepted prefix.
    machine.type("7")
    machine.event(1, 13)
    assert rendered_value(machine) == "2147483647"


def test_negative_backspace_operator_display_and_zero_divisor_correction():
    machine = CalculatorMachine()
    machine.settle()
    machine.type("-123")
    machine.event(1, 8)
    assert rendered_value(machine) == "-12"
    machine.type("/0")
    assert rendered_value(machine) == "0  (/)"
    machine.event(1, 13)
    assert machine.regs[8] == (-12 & 0xFFFFFFFF)
    assert machine.regs[9] == 0 and rendered_value(machine) == "DIVISION BY ZERO"
    machine.event(1, 8)
    machine.type("5")
    machine.event(1, 13)
    assert rendered_value(machine) == "-2"


def test_operations_chain_left_to_right_and_digits_after_result_start_fresh():
    machine = calculate("2+3*4")
    assert rendered_value(machine) == "20"
    machine.type("-5")
    machine.event(1, 13)
    assert rendered_value(machine) == "15"
    machine.type("9=")
    assert rendered_value(machine) == "9"
