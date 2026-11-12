"""Behavioral truth tables for Yosys scalar synchronous-register lowering."""
from copy import deepcopy
from itertools import product

import pytest

from shamaos.synthesis import SynthesisError, _expand_sequential_cells
from shamaos.hardware.logic import template_for


def source_module(kind):
    ports = {"C": 2, "D": 3, "E": 4, "R": 5, "Q": 6}
    return {
        "ports": {name: {"direction": "output" if name == "Q" else "input", "bits": [bit]} for name, bit in ports.items()},
        "cells": {"ff": {"type": kind, "connections": {name: [bit] for name, bit in ports.items()}}},
    }


def evaluate_combinational(module, c, d, e, r, q):
    values = {"0": 0, "1": 1, 2: c, 3: d, 4: e, 5: r, 6: q}
    pending = [cell for cell in module["cells"].values() if cell["type"] != "$_DFF_P_"]
    while pending:
        progressed = False
        for cell in pending[:]:
            pins = {pin: bits[0] for pin, bits in cell["connections"].items()}
            if any(bit not in values for pin, bit in pins.items() if pin != "Y"):
                continue
            if cell["type"] == "$_NOT_":
                result = 1 - values[pins["A"]]
            elif cell["type"] == "$_NAND_":
                result = 1 - (values[pins["A"]] & values[pins["B"]])
            else:
                raise AssertionError(f"unexpected lowered combinational cell: {cell['type']}")
            values[pins["Y"]] = result
            pending.remove(cell)
            progressed = True
        assert progressed, "unresolved input or accidental combinational loop"
    return values


VARIANTS = []
for clock in "PN":
    VARIANTS.append((f"$_DFF_{clock}_", "DFF", clock, "P", 0, "P"))
    for enable in "PN":
        VARIANTS.append((f"$_DFFE_{clock}{enable}_", "DFFE", clock, "P", 0, enable))
    for reset, value in product("PN", (0, 1)):
        VARIANTS.append((f"$_SDFF_{clock}{reset}{value}_", "SDFF", clock, reset, value, "P"))
        for enable in "PN":
            for family in ("SDFFE", "SDFFCE"):
                VARIANTS.append((f"$_{family}_{clock}{reset}{value}{enable}_", family, clock, reset, value, enable))


@pytest.mark.parametrize("kind,family,clock,reset,value,enable", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_every_control_combination_and_clock_transition(kind, family, clock, reset, value, enable):
    module = source_module(kind)
    _expand_sequential_cells(module)
    flops = [c for c in module["cells"].values() if c["type"] == "$_DFF_P_"]
    assert len(flops) == 1
    pins = {pin: bits[0] for pin, bits in flops[0]["connections"].items()}
    for old_c, new_c, d, e, r, q in product((0, 1), repeat=6):
        old = evaluate_combinational(module, old_c, d, e, r, q)
        new = evaluate_combinational(module, new_c, d, e, r, q)
        actual = new[pins["D"]] if old[pins["C"]] == 0 and new[pins["C"]] == 1 else q
        edge = (old_c, new_c) == ((0, 1) if clock == "P" else (1, 0))
        enabled = e == (1 if enable == "P" else 0)
        resetting = r == (1 if reset == "P" else 0)
        if not edge:
            expected = q
        elif family == "DFF":
            expected = d
        elif family == "DFFE":
            expected = d if enabled else q
        elif family == "SDFF":
            expected = value if resetting else d
        elif family == "SDFFE":
            expected = value if resetting else (d if enabled else q)
        else:  # Yosys SDFFCE: enable takes precedence over synchronous reset.
            expected = (value if resetting else d) if enabled else q
        assert actual == expected, (kind, old_c, new_c, d, e, r, q)


@pytest.mark.parametrize("kind", ["$_DFF_PP0_", "$_DFFE_PP0P_", "$_DFFSR_PPP_", "$_DLATCH_P_", "$_SDFF_PP2_"])
def test_unsupported_sequential_semantics_fail_explicitly(kind):
    with pytest.raises(SynthesisError, match="unsupported sequential cell"):
        _expand_sequential_cells(source_module(kind))


def test_negative_edge_is_never_a_positive_physical_alias():
    with pytest.raises(KeyError, match="unsupported physical"):
        template_for("$_DFF_N_")


def test_malformed_scalar_pin_is_not_silently_truncated():
    module = source_module("$_SDFFE_PP0P_")
    module["cells"]["ff"]["connections"]["R"] = [5, 7]
    with pytest.raises(SynthesisError, match="R must be scalar"):
        _expand_sequential_cells(module)


def test_lowering_does_not_overwrite_an_existing_cell_name():
    module = source_module("$_DFFE_PP_")
    existing = {"type": "$_NOT_", "connections": {"A": [2], "Y": [50]}}
    module["cells"]["ff$enable_mux$not"] = deepcopy(existing)
    _expand_sequential_cells(module)
    assert module["cells"]["ff$enable_mux$not"] == existing
    assert len(module["cells"]) == 6
