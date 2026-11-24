"""Bound two same-register feedback loops under the production default global router."""
import argparse
import json
from pathlib import Path

from shamaos.hardware.logic import template_for
from shamaos.hardware.partition_planner import load_mapped_module
from shamaos.model import Vec3
from shamaos.resource_accounting import _minimum_escape_high_z


def audit(path):
    module, meta = load_mapped_module(path, "shama_soc")
    raw, = module["ports"]["clk"]["bits"]
    drivers = {}
    for name, cell in module["cells"].items():
        for pin, bits in cell["connections"].items():
            directions = cell.get("port_directions", {})
            if directions.get(pin, "output" if pin in ("Q", "Y") else "input") == "output":
                for bit in bits:
                    if isinstance(bit, int):
                        if bit in drivers:
                            raise ValueError(f"duplicate driver for bit {bit}")
                        drivers[bit] = (name, pin)
    # Given min_z=0, the nearest possible track has z=-4104 regardless
    # of the number of nonempty nets (4096 apron; spacing four). The
    # end of the diagonal from trunk to branch is four blocks south.
    last_track = -4096 - 2 * 4
    branch_z = last_track + 4
    least_high = min(_minimum_escape_high_z(Vec3(0, y, 0), 300,
        ("south", "north", "west"), None) for y in range(-63, 320))
    length = least_high - branch_z
    repeats = (length - 1) // 10
    loops = []
    for counter in ("time_counter", "core_div_counter"):
        q = module["netnames"][counter]["bits"][0]
        qname, qpin = drivers[q]
        ff = module["cells"][qname]
        d, = ff["connections"]["D"]
        gate_name, gate_pin = drivers[d]
        gate = module["cells"][gate_name]
        assert template_for(ff["type"]).cell_type == "DFF" and qpin == "Q"
        assert ff["connections"]["C"] == [raw]
        assert template_for(gate["type"]).cell_type == "NOR" and gate_pin == "Y"
        assert [q] in (gate["connections"]["A"], gate["connections"]["B"])
        loops.append({"register": counter+"[0]", "route": ["Q", "NOR", "D"],
                      "minimum_feedback_repeater_ticks": 4*repeats})
    return {"source_artifact_sha256": meta["container_sha256"], "top": "shama_soc",
            "default_shell_branch_y": 300, "default_shell_trunk_y": 304,
            "nearest_relative_trunk_z": last_track, "minimum_relative_escape_z": least_high,
            "minimum_endpoint_straight_blocks":length,"minimum_repeaters_per_end":repeats,
            "minimum_native_to_native_route_ticks": 2*repeats, "raw_period_ticks": 70,
            "same_clock_feedback": loops}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mapped_shell", type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.mapped_shell), indent=2))
