from __future__ import annotations

from .model import BlockState, Placement, Vec3


AIR = BlockState.of("minecraft:air")
STONE = BlockState.of("minecraft:smooth_stone")
REDSTONE = BlockState.of("minecraft:redstone_wire")
LAMP = BlockState.of("minecraft:redstone_lamp", lit="false")
LEVER = BlockState.of("minecraft:lever", face="floor", facing="north", powered="false")
BUTTON = BlockState.of("minecraft:stone_button", face="wall", facing="north", powered="false")


def line(
    start: Vec3,
    length: int,
    *,
    axis: str,
    block: BlockState = REDSTONE,
    component: str,
) -> list[Placement]:
    if length < 0:
        raise ValueError("length must be non-negative")
    delta = {
        "x": (1, 0, 0),
        "y": (0, 1, 0),
        "z": (0, 0, 1),
    }.get(axis)
    if delta is None:
        raise ValueError("axis must be x/y/z")
    dx, dy, dz = delta
    return [
        Placement(start.offset(dx * i, dy * i, dz * i), block, component)
        for i in range(length)
    ]


def lamp_panel(
    origin: Vec3,
    width: int,
    height: int,
    *,
    component: str = "display",
) -> list[Placement]:
    """Build a vertical X/Y lamp plane one block thick."""
    if width <= 0 or height <= 0:
        raise ValueError("invalid panel size")
    return [
        Placement(origin.offset(x, y, 0), LAMP, component)
        for y in range(height)
        for x in range(width)
    ]


def floor(origin: Vec3, width: int, depth: int, *, component: str) -> list[Placement]:
    return [
        Placement(origin.offset(x, 0, z), STONE, component)
        for z in range(depth)
        for x in range(width)
    ]
