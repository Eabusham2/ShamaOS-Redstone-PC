from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterator

from ..model import BlockState, Placement, Vec3
from .memory import DUST, SUPPORT, repeater


WALL_TORCH_E = BlockState.of("minecraft:redstone_wall_torch", facing="east", lit="true")
WALL_TORCH_N = BlockState.of("minecraft:redstone_wall_torch", facing="north", lit="true")
REDSTONE_BLOCK = BlockState.of("minecraft:redstone_block")


@dataclass(frozen=True)
class LogicTemplate:
    cell_type: str
    width: int
    depth: int
    pins: dict[str, Vec3]
    emitter: Callable[[Vec3, str], Iterator[Placement]]
    # Redstone ticks at the local C/D pins. External route delays are extra.
    minimum_clock_half_period: int = 0
    setup_ticks: int = 0
    hold_ticks: int = 0


def _place(origin: Vec3, dx: int, dy: int, dz: int, block: BlockState, component: str) -> Placement:
    return Placement(origin.offset(dx, dy, dz), block, component)


def _dust(origin: Vec3, dx: int, dz: int, component: str, *, y: int = 1) -> Iterator[Placement]:
    yield _place(origin, dx, y - 1, dz, SUPPORT, component)
    yield _place(origin, dx, y, dz, DUST, component)


def _repeater(origin: Vec3, x: int, z: int, facing: str, component: str, *, delay: int = 1, locked: bool = False) -> Iterator[Placement]:
    yield _place(origin, x, 0, z, SUPPORT, component)
    yield _place(origin, x, 1, z, repeater(facing, delay=delay, locked=locked), component)


def emit_buf(origin: Vec3, component: str) -> Iterator[Placement]:
    yield from _dust(origin, 0, 0, component)
    yield from _repeater(origin, 1, 0, "east", component)
    yield from _dust(origin, 2, 0, component)
    yield from _dust(origin, 3, 0, component)


def emit_not(origin: Vec3, component: str) -> Iterator[Placement]:
    yield from _dust(origin, 0, 0, component)
    yield from _repeater(origin, 1, 0, "east", component)
    yield _place(origin, 2, 1, 0, SUPPORT, component)
    yield _place(origin, 3, 1, 0, WALL_TORCH_E, component)
    yield from _dust(origin, 4, 0, component)


def emit_nor(origin: Vec3, component: str) -> Iterator[Placement]:
    """Isolated A/B repeaters feed an OR bus followed by an inverter.

    Output x=16 is away from both inputs. The former compact layout joined
    input B and the output torch through adjacent dust.
    """
    for x in (0, 8):
        yield from _dust(origin, x, 0, component)
        yield from _repeater(origin, x, -1, "north", component)
    for x in range(9):
        yield from _dust(origin, x, -2, component)
    yield from _repeater(origin, 9, -2, "east", component)
    yield _place(origin, 10, 1, -2, SUPPORT, component)
    yield _place(origin, 11, 1, -2, WALL_TORCH_E, component)
    for x in range(12, 17):
        yield from _dust(origin, x, -2, component)
    for z in (-1, 0):
        yield from _dust(origin, 16, z, component)


def emit_nand(origin: Vec3, component: str) -> Iterator[Placement]:
    """NAND(A,B) = OR(NOT A, NOT B), with isolated input inverters."""
    for x in (0, 8):
        yield from _dust(origin, x, 0, component)
        yield from _repeater(origin, x, -1, "north", component)
        yield _place(origin, x, 1, -2, SUPPORT, component)
        yield _place(origin, x, 1, -3, WALL_TORCH_N, component)
    for x in range(9):
        yield from _dust(origin, x, -4, component)
    yield from _repeater(origin, 9, -4, "east", component)
    for x in range(10, 17):
        yield from _dust(origin, x, -4, component)
    for z in range(-3, 1):
        yield from _dust(origin, 16, z, component)


def emit_and(origin: Vec3, component: str) -> Iterator[Placement]:
    yield from emit_nand(origin, component)
    yield from emit_not(origin.offset(17, 0, 0), component)


def emit_dff(origin: Vec3, component: str) -> Iterator[Placement]:
    """Positive-edge master/slave storage with isolated, refreshed Q.

    The direct clock locks the master after one redstone tick. Its buffered
    inverter locks/unlocks the slave after three ticks. A delay-four repeater
    between latches prevents the falling-edge race: new data cannot reach the
    slave before its lock engages. Respect the template's setup/hold and clock
    high/low requirements, in addition to any external route delays.

    Q returns at z=-10, four blocks away from the slave-lock wiring. Both long
    legs have repeaters; Q never shares dust with either clock net.
    """
    yield from _dust(origin, 0, 0, component)  # D
    yield from _dust(origin, 6, 0, component)  # C
    yield from _dust(origin, 0, -1, component)
    yield from _repeater(origin, 0, -2, "north", component, locked=True)
    yield from _dust(origin, 0, -3, component)
    yield from _repeater(origin, 0, -4, "north", component, delay=4)
    yield from _dust(origin, 0, -5, component)
    yield from _repeater(origin, 0, -6, "north", component, locked=True)

    for x in range(2, 7):
        yield from _dust(origin, x, -1, component)
    yield from _dust(origin, 2, -2, component)
    yield from _repeater(origin, 1, -2, "west", component)

    yield from _repeater(origin, 6, -2, "north", component)
    yield _place(origin, 6, 1, -3, SUPPORT, component)
    yield _place(origin, 6, 1, -4, WALL_TORCH_N, component)
    for x in range(2, 7):
        yield from _dust(origin, x, -5, component)
    yield from _dust(origin, 2, -6, component)
    yield from _repeater(origin, 1, -6, "west", component)

    for z in range(-7, -11, -1):
        yield from _dust(origin, 0, z, component)
    for x in range(1, 17):
        if x == 8:
            yield from _repeater(origin, x, -10, "east", component)
        else:
            yield from _dust(origin, x, -10, component)
    for z in range(-9, 1):
        if z in (-5, -1):
            yield from _repeater(origin, 16, z, "south", component)
        else:
            yield from _dust(origin, 16, z, component)


TEMPLATES: dict[str, LogicTemplate] = {
    "BUF": LogicTemplate("BUF", 4, 2, {"A": Vec3(0, 1, 0), "Y": Vec3(3, 1, 0)}, emit_buf),
    "NOT": LogicTemplate("NOT", 5, 2, {"A": Vec3(0, 1, 0), "Y": Vec3(4, 1, 0)}, emit_not),
    "NAND": LogicTemplate("NAND", 17, 6, {"A": Vec3(0, 1, 0), "B": Vec3(8, 1, 0), "Y": Vec3(16, 1, 0)}, emit_nand),
    "NOR": LogicTemplate("NOR", 17, 4, {"A": Vec3(0, 1, 0), "B": Vec3(8, 1, 0), "Y": Vec3(16, 1, 0)}, emit_nor),
    "AND": LogicTemplate("AND", 22, 6, {"A": Vec3(0, 1, 0), "B": Vec3(8, 1, 0), "Y": Vec3(21, 1, 0)}, emit_and),
    "DFF": LogicTemplate(
        "DFF", 17, 12, {"D": Vec3(0, 1, 0), "C": Vec3(6, 1, 0), "Q": Vec3(16, 1, 0)}, emit_dff,
        minimum_clock_half_period=12, setup_ticks=8, hold_ticks=2,
    ),
}


def normalize_cell_type(cell_type: str) -> str:
    key = cell_type.lstrip("\\").upper()
    aliases = {
        "$_BUF_": "BUF", "$_NOT_": "NOT", "$_NAND_": "NAND",
        "$_NOR_": "NOR", "$_AND_": "AND", "$_DFF_P_": "DFF",
    }
    # Negative clocks/resets/enables must be semantically lowered before use.
    return aliases.get(key, key)


def template_for(cell_type: str) -> LogicTemplate:
    key = normalize_cell_type(cell_type)
    try:
        return TEMPLATES[key]
    except KeyError as exc:
        raise KeyError(f"unsupported physical redstone cell {cell_type!r}") from exc
