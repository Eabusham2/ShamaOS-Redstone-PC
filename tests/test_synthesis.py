import json
from pathlib import Path

from shamaos.model import Vec3
from shamaos.synthesis import build_physical_netlist


def test_physical_netlist_from_mapped_json(tmp_path: Path):
    net = {
        "modules": {
            "tiny": {
                "ports": {
                    "a": {"direction": "input", "bits": [2]},
                    "b": {"direction": "input", "bits": [3]},
                    "y": {"direction": "output", "bits": [5]},
                },
                "cells": {
                    "n1": {
                        "type": "NAND",
                        "port_directions": {"A": "input", "B": "input", "Y": "output"},
                        "connections": {"A": [2], "B": [3], "Y": [4]},
                    },
                    "n2": {
                        "type": "NOT",
                        "port_directions": {"A": "input", "Y": "output"},
                        "connections": {"A": [4], "Y": [5]},
                    },
                },
            }
        }
    }
    p = tmp_path / "tiny.json"
    p.write_text(json.dumps(net))

    physical = build_physical_netlist(p, top="tiny", origin=Vec3(0, 64, 0))
    assert physical.manifest()["cell_count"] == 2
    assert physical.manifest()["net_count"] == 4
    assert physical.track_count >= 1

    first = []
    for idx, placement in enumerate(physical.iter_placements()):
        first.append(placement)
        if idx > 500:
            break
    assert any(p.block.name == "minecraft:repeater" for p in first)
    assert any("torch" in p.block.name for p in first)


def test_enabled_dff_is_lowered_to_supported_physical_cells(tmp_path: Path):
    net = {
        "modules": {
            "tiny_dffe": {
                "ports": {
                    "d": {"direction": "input", "bits": [2]},
                    "clk": {"direction": "input", "bits": [3]},
                    "en_n": {"direction": "input", "bits": [4]},
                    "q": {"direction": "output", "bits": [5]},
                },
                "cells": {
                    "ff": {
                        "type": "$_DFFE_PN_",
                        "port_directions": {
                            "C": "input", "D": "input", "E": "input", "Q": "output"
                        },
                        "connections": {"C": [3], "D": [2], "E": [4], "Q": [5]},
                    },
                },
            }
        }
    }
    p = tmp_path / "dffe.json"
    p.write_text(json.dumps(net))

    physical = build_physical_netlist(
        p, top="tiny_dffe", origin=Vec3(0, 64, 0)
    )

    # NOT(E) + three NANDs for the feedback mux + a normal edge DFF.
    assert physical.manifest()["cell_count"] == 5
    assert set(physical.cell_types.values()) <= {
        "$_NOT_", "$_NAND_", "$_DFF_P_"
    }

    placements = []
    for idx, placement in enumerate(physical.iter_placements()):
        placements.append(placement)
        if idx > 2000:
            break
    assert any(p.block.name == "minecraft:repeater" for p in placements)
    assert any("torch" in p.block.name for p in placements)
