"""Compact physical wire risers for bounded memory interface experiments."""
from __future__ import annotations

from typing import Iterator

from ..model import Placement, Vec3
from .memory import SUPPORT, repeater
from .routing import _wire_for_point


_PERIMETER = ((0, 0), (1, 0), (2, 0), (3, 0), (4, 0), (4, 1), (4, 2), (4, 3),
              (4, 4), (3, 4), (2, 4), (1, 4), (0, 4), (0, 3), (0, 2), (0, 1))


def helical_wire_points(start: Vec3, rise: int, *, quarter_turns: int = 0,
                       terminal_stub: bool = True) -> tuple[Vec3, ...]:
    """Rise around a 5×5 perimeter, with straight refreshed plateaus.

    Each full turn rises twelve blocks. The last two flat steps export an
    isolated, plain-wire terminal, so partial turns can extend two blocks
    outside the core footprint. A caller must reserve those actual bounds.
    """
    if rise < 1:
        raise ValueError("a helical riser needs a positive rise")
    points = [start]
    height = step = 0
    while height < rise:
        step += 1
        phase = step % len(_PERIMETER)
        if phase not in (1, 2, 9, 10):
            height += 1
        x, z = _PERIMETER[phase]
        for _ in range(quarter_turns % 4):
            x, z = -z, x
        points.append(start.offset(x, height, z))
    last, previous = points[-1], points[-2]
    dx, dz = last.x - previous.x, last.z - previous.z
    if terminal_stub:
        points.extend((last.offset(dx, 0, dz), last.offset(2 * dx, 0, 2 * dz)))
    return tuple(points)


def iter_helical_wire(start: Vec3, rise: int, *, signal_upward: bool,
                      quarter_turns: int = 0, terminal_stub: bool = True,
                      component: str = "helical-interface") -> Iterator[Placement]:
    """Emit a literal isolated stair with a diode at every straight plateau."""
    points = list(helical_wire_points(start, rise, quarter_turns=quarter_turns, terminal_stub=terminal_stub))
    directions = {(1, 0): "east", (-1, 0): "west", (0, 1): "south", (0, -1): "north"}
    repeaters = {i for i in range(1, len(points) - 1) if i % 16 in (1, 9)}
    if terminal_stub:
        repeaters.add(len(points) - 2)
    for index, point in enumerate(points):
        yield Placement(point.offset(dy=-1), SUPPORT, component)
        if index in repeaters:
            before, after = points[index - 1], points[index + 1]
            assert before.y == point.y == after.y
            dx, dz = point.x - before.x, point.z - before.z
            assert (after.x - point.x, after.z - point.z) == (dx, dz)
            if not signal_upward:
                dx, dz = -dx, -dz
            block = repeater(directions[(dx, dz)])
        else:
            block = _wire_for_point(points, index)
        yield Placement(point, block, component)
