"""Exact bounded cuts of a mapped module for routing and timing experiments.

A cut is a real subset of the supplied mapped cells. Every incoming signal and
every selected output used outside the subset becomes a boundary terminal.
This does not replace, optimize or prove the remaining parent module.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from typing import Iterable


def mapped_cell_cut(module: dict, selected: Iterable[str], *, name: str) -> dict:
    """Keep selected cells unchanged and expose their complete cut boundary.

    The boundary report retains parent cell/pin and port/bit identities even
    though the cut's scalar terminals use stable ``i<bit>``/``o<bit>`` names.
    Constants remain constants in the original cell connections. Selection is
    explicit: no fanout or clock leaf is silently pruned to meet a size budget.
    """
    selected = frozenset(selected)
    cells = module.get("cells", {})
    if not selected or not selected.issubset(cells):
        raise ValueError("routing cut must name a nonempty subset of mapped cells")
    chosen = {cell: deepcopy(cells[cell]) for cell in sorted(selected)}
    produced: dict[int, dict] = {}
    consumed = defaultdict(list)
    for cell_name, cell in chosen.items():
        for pin, bits in cell["connections"].items():
            direction = cell["port_directions"].get(pin)
            if direction not in ("input", "output"):
                raise ValueError("routing cuts require explicit input/output pin directions")
            for index, bit in enumerate(bits):
                if not isinstance(bit, int):
                    if bit not in ("0", "1"):
                        raise ValueError("routing cuts cannot silently ground unknown constants")
                    continue
                endpoint = {"cell": cell_name, "pin": pin, "index": index}
                if direction == "output":
                    if bit in produced:
                        raise ValueError("routing cut has multiple drivers for one signal")
                    produced[bit] = endpoint
                else:
                    consumed[bit].append(endpoint)
    incoming = set(consumed).difference(produced)
    outside = defaultdict(list)
    for cell_name, cell in cells.items():
        if cell_name in selected:
            continue
        for pin, bits in cell["connections"].items():
            if cell["port_directions"].get(pin) == "input":
                for index, bit in enumerate(bits):
                    if bit in produced:
                        outside[bit].append({"cell": cell_name, "pin": pin, "index": index})
    parent_ports = defaultdict(list)
    for port_name, port in module.get("ports", {}).items():
        for index, bit in enumerate(port["bits"]):
            if bit in incoming or bit in produced:
                endpoint = {"port": port_name, "index": index, "direction": port["direction"]}
                parent_ports[bit].append(endpoint)
                if port["direction"] == "output" and bit in produced:
                    outside[bit].append(endpoint)
    outgoing = set(outside)
    ports = {f"i{bit}": {"direction": "input", "bits": [bit]} for bit in sorted(incoming)}
    ports.update({f"o{bit}": {"direction": "output", "bits": [bit]} for bit in sorted(outgoing)})
    return {
        "modules": {name: {"ports": ports, "cells": chosen, "netnames": {}}},
        "cut_boundary": {
            "selected_cells": len(chosen),
            "incoming": {
                str(bit): {"selected_consumers": consumed[bit], "parent_ports": parent_ports[bit]}
                for bit in sorted(incoming)
            },
            "outgoing": {
                str(bit): {"selected_driver": produced[bit], "outside_consumers": outside[bit],
                           "parent_ports": parent_ports[bit]}
                for bit in sorted(outgoing)
            },
            "scope": "complete boundary of selected mapped cells; not a complete parent implementation",
        },
    }
