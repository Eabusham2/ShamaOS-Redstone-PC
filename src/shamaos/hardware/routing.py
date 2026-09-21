from __future__ import annotations

from typing import Iterator

from ..model import Placement, Vec3
from .memory import SUPPORT, redstone_wire, repeater


def _connection_name(dx: int, dz: int) -> str:
    if dx == 1 and dz == 0:
        return "east"
    if dx == -1 and dz == 0:
        return "west"
    if dx == 0 and dz == 1:
        return "south"
    if dx == 0 and dz == -1:
        return "north"
    raise ValueError(f"not a cardinal neighbor delta: {(dx, dz)}")


def _wire_for_point(points: list[Vec3], index: int):
    p = points[index]
    con = {
        "north": "side",
        "east": "side",
        "south": "side",
        "west": "side",
    }
    for j in (index - 1, index + 1):
        if not 0 <= j < len(points):
            continue
        n = points[j]
        dx = n.x - p.x
        dz = n.z - p.z
        if abs(dx) + abs(dz) != 1:
            raise ValueError(f"non-adjacent stair points: {p} -> {n}")
        direction = _connection_name(dx, dz)
        # The lower wire explicitly climbs the neighboring support block.
        if n.y == p.y + 1:
            con[direction] = "up"
        elif abs(n.y - p.y) > 1:
            raise ValueError(f"stair rises more than one block: {p} -> {n}")
    return redstone_wire(**con)


def _flat_candidate(points: list[Vec3], index: int) -> bool:
    if not 0 < index < len(points) - 1:
        return False
    a, b, c = points[index - 1], points[index], points[index + 1]
    return a.y == b.y == c.y


def _repeater_indices(
    points: list[Vec3],
    *,
    signal_forward: bool,
    max_run: int = 12,
) -> set[int]:
    """Choose flat positions so no signal segment exceeds max_run blocks."""
    if len(points) <= max_run + 1:
        return set()

    order = list(range(len(points)))
    if not signal_forward:
        order.reverse()

    # Position in signal-order rather than original list index.
    result: set[int] = set()
    last_refresh_pos = 0
    last_pos = len(order) - 1

    while last_pos - last_refresh_pos > max_run:
        ideal = min(last_refresh_pos + 10, last_pos - 1)

        chosen_pos = None
        # Search around the ideal, never past max_run from the prior refresh.
        for delta in range(0, max_run):
            for candidate_pos in (ideal + delta, ideal - delta):
                if not (last_refresh_pos < candidate_pos < last_pos):
                    continue
                if candidate_pos - last_refresh_pos > max_run:
                    continue
                original_index = order[candidate_pos]
                if _flat_candidate(points, original_index):
                    chosen_pos = candidate_pos
                    break
            if chosen_pos is not None:
                break

        if chosen_pos is None:
            raise ValueError(
                "stair route has no flat repeater plateau before signal "
                f"strength expires (segment starts at {points[order[last_refresh_pos]]})"
            )

        result.add(order[chosen_pos])
        last_refresh_pos = chosen_pos

    return result


def _build_stair_points(
    start: Vec3,
    end: Vec3,
    *,
    axis: str,
    vertical_group: int = 10,
) -> list[Vec3]:
    if axis == "x":
        if start.z != end.z:
            raise ValueError("X stair requires constant Z")
        horizontal = end.x - start.x
        make = lambda h, y: Vec3(h, y, start.z)
        h0, h1 = start.x, end.x
    elif axis == "z":
        if start.x != end.x:
            raise ValueError("Z stair requires constant X")
        horizontal = end.z - start.z
        make = lambda h, y: Vec3(start.x, y, h)
        h0, h1 = start.z, end.z
    else:
        raise ValueError("axis must be x or z")

    dy = end.y - start.y
    sh = 1 if horizontal >= 0 else -1
    sy = 1 if dy >= 0 else -1
    horizontal_abs = abs(horizontal)
    vertical_abs = abs(dy)

    groups = (vertical_abs + vertical_group - 1) // vertical_group
    plateau_blocks = max(0, groups - 1) * 2
    minimum_run = vertical_abs + plateau_blocks
    if horizontal_abs < minimum_run:
        raise ValueError(
            f"{axis.upper()} stair needs {minimum_run} horizontal blocks "
            f"for {vertical_abs} vertical blocks with signal refresh, "
            f"but only {horizontal_abs} are available"
        )

    points = [start]
    h = h0
    y = start.y
    remaining = vertical_abs

    while remaining:
        take = min(vertical_group, remaining)
        for _ in range(take):
            h += sh
            y += sy
            points.append(make(h, y))
        remaining -= take

        if remaining:
            # Two same-height steps create A-B-C; B is a legal repeater spot.
            h += sh
            points.append(make(h, y))
            h += sh
            points.append(make(h, y))

    while h != h1:
        h += sh
        points.append(make(h, y))

    if points[-1] != end:
        raise ValueError(f"stair endpoint mismatch: {points[-1]} != {end}")
    return points


def iter_stair(
    start: Vec3,
    end: Vec3,
    *,
    axis: str,
    signal_forward: bool,
    component: str,
) -> Iterator[Placement]:
    """Emit a strength-safe redstone stair with explicit wire states.

    Every vertical step is represented by a wire connection with the lower
    side marked `up`. Long climbs/descents contain two-block flat plateaus
    often enough to install directional repeaters without breaking a step.
    """
    points = _build_stair_points(start, end, axis=axis)
    repeaters = _repeater_indices(points, signal_forward=signal_forward)

    if axis == "x":
        step_positive = end.x >= start.x
        facing_forward = "east" if step_positive else "west"
        facing_reverse = "west" if step_positive else "east"
    else:
        step_positive = end.z >= start.z
        facing_forward = "south" if step_positive else "north"
        facing_reverse = "north" if step_positive else "south"

    facing = facing_forward if signal_forward else facing_reverse

    for index, point in enumerate(points):
        yield Placement(point.offset(dy=-1), SUPPORT, component)
        if index in repeaters:
            yield Placement(point, repeater(facing), component)
        else:
            yield Placement(point, _wire_for_point(points, index), component)
