"""Real redstone propagation through compact memory interface risers."""
from itertools import product

import pytest

from shamaos.hardware.vertical_wiring import helical_wire_points, iter_helical_wire
from shamaos.model import Vec3
from test_mchprs_runtime import run_case


@pytest.mark.parametrize("rise", [1, 2, 6, 10, 12, 118, 146, 366])
@pytest.mark.parametrize("rotation", [0, 2])
def test_actual_helical_risers_refresh_both_directions_and_adjacent_isolation(rise, rotation):
    blocks, inputs, outputs = [], {}, {}
    starts = [Vec3(64, 8, 64 + 6 * index) for index in range(4)]
    for index, start in enumerate(starts):
        points = helical_wire_points(start, rise, quarter_turns=rotation)
        upward = index % 2 == 0
        blocks.extend(iter_helical_wire(start, rise, signal_upward=upward, quarter_turns=rotation, component=f"riser{index}"))
        if upward:
            first, following = points[0], points[1]
            source = first.offset(first.x - following.x, 0, first.z - following.z)
            inputs[f"D{index}"] = source.offset(dz=-1)
            outputs[f"Q{index}"] = points[-1]
        else:
            tip, previous = points[-1], points[-2]
            source = tip.offset(tip.x - previous.x, 0, tip.z - previous.z)
            inputs[f"D{index}"] = source.offset(dz=-1)
            outputs[f"Q{index}"] = points[0]
    occupied = {block.pos: block.block for block in blocks}
    assert len(occupied) == len(blocks), "neighboring helical interfaces overlap"
    for start in starts:
        points = helical_wire_points(start, rise, quarter_turns=rotation)
        for first, second in zip(points, points[1:]):
            if first.y != second.y:
                lower = first if first.y < second.y else second
                assert lower.offset(dy=1) not in occupied, "a support covers the rising wire"
    assert all(anchor.offset(dz=1) not in occupied for anchor in inputs.values())
    steps = [{"drive": {f"D{index}": value for index, value in enumerate(values)}, "ticks": 256,
              "expect": {f"Q{index}": value for index, value in enumerate(values)}}
             for values in product((False, True), repeat=4)]
    steps.append({"drive": dict.fromkeys(inputs, False), "ticks": 256, "expect": dict.fromkeys(outputs, False)})
    run_case(blocks, inputs, outputs, steps, initial_ticks=256)
