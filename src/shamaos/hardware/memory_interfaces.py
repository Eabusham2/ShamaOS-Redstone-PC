"""Experimental compact row interfaces; production promotion is separately gated."""
from __future__ import annotations

from typing import Iterator

from ..model import Placement, Vec3
from .memory import DUST, SUPPORT, repeater
from .memory_backbone import StackedMemoryTileSpec, stacked_tile_ports
from .vertical_wiring import helical_wire_points, iter_helical_wire


def memory_row_interface_ports(origin: Vec3, tile: StackedMemoryTileSpec, *,
                               riser_x: int, plane_y: int = 306) -> tuple[Vec3, ...]:
    return tuple(helical_wire_points(Vec3(riser_x, pin.y, pin.z), plane_y - pin.y, quarter_turns=2)[-1]
                 for pin in stacked_tile_ports(origin, tile).row_select)


def iter_memory_row_interfaces(origin: Vec3, tile: StackedMemoryTileSpec, *,
                               riser_x: int, plane_y: int = 306,
                               component: str = "memory-row-interface") -> Iterator[Placement]:
    """Carry independent row signals down isolated small spiral columns.

    Each row keeps its native six-block Z pitch. Lower fabrics must use
    farther-west columns than fabrics placed above them: upper flat access
    legs then avoid crossing a lower fabric's continuing vertical riser.
    """
    if riser_x > origin.x - 132:
        raise ValueError("row risers must remain outside the local-control envelope")
    for row, pin in enumerate(stacked_tile_ports(origin, tile).row_select):
        start = Vec3(riser_x, pin.y, pin.z)
        row_component = f"{component}:{row}"
        yield from iter_helical_wire(start, plane_y - pin.y, signal_upward=False,
                                    quarter_turns=2, component=row_component)
        for distance, x in enumerate(range(start.x + 1, pin.x + 1), start=1):
            point = Vec3(x, pin.y, pin.z)
            yield Placement(point.offset(dy=-1), SUPPORT, row_component)
            yield Placement(point, repeater("east") if distance % 10 == 0 and x != pin.x else DUST,
                            row_component)
