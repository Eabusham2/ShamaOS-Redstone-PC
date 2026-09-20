from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterator

from ..model import BlockState, Placement, Vec3
from .memory import DUST, SUPPORT, repeater


WALL_TORCH_E = BlockState.of(
    "minecraft:redstone_wall_torch", facing="east", lit="true"
)
WALL_TORCH_N = BlockState.of(
    "minecraft:redstone_wall_torch", facing="north", lit="true"
)
REDSTONE_BLOCK = BlockState.of("minecraft:redstone_block")


@dataclass(frozen=True)
class LogicTemplate:
    cell_type: str
    width: int
    depth: int
    pins: dict[str, Vec3]
    emitter: Callable[[Vec3, str], Iterator[Placement]]


def _place(origin: Vec3, dx: int, dy: int, dz: int, block: BlockState, component: str) -> Placement:
    return Placement(origin.offset(dx, dy, dz), block, component)


def _dust(origin: Vec3, dx: int, dz: int, component: str, *, y: int = 1) -> Iterator[Placement]:
    yield _place(origin, dx, y - 1, dz, SUPPORT, component)
    yield _place(origin, dx, y, dz, DUST, component)


def emit_buf(origin: Vec3, component: str) -> Iterator[Placement]:
    yield from _dust(origin, 0, 0, component)
    yield _place(origin, 1, 0, 0, SUPPORT, component)
    yield _place(origin, 1, 1, 0, repeater("east"), component)
    yield from _dust(origin, 2, 0, component)
    yield from _dust(origin, 3, 0, component)


def emit_not(origin: Vec3, component: str) -> Iterator[Placement]:
    yield from _dust(origin, 0, 0, component)
    yield _place(origin, 1, 0, 0, SUPPORT, component)
    yield _place(origin, 1, 1, 0, repeater("east"), component)
    yield _place(origin, 2, 1, 0, SUPPORT, component)
    yield _place(origin, 3, 1, 0, WALL_TORCH_E, component)
    yield from _dust(origin, 4, 0, component)


def emit_nor(origin: Vec3, component: str) -> Iterator[Placement]:
    # Pins A/B are on the routing boundary z=0.  They feed opposite sides of
    # one powered block.  The wall torch is therefore !(A|B).
    yield from _dust(origin, 0, 0, component)
    yield from _dust(origin, 3, 0, component)

    for z in (-1, -2):
        yield from _dust(origin, 0, z, component)
        yield from _dust(origin, 3, z, component)

    yield _place(origin, 1, 0, -2, SUPPORT, component)
    yield _place(origin, 1, 1, -2, repeater("east"), component)
    yield _place(origin, 2, 1, -2, SUPPORT, component)
    yield _place(origin, 3, 1, -2, repeater("west"), component)

    yield _place(origin, 2, 1, -3, WALL_TORCH_N, component)

    for x in range(3, 7):
        yield from _dust(origin, x, -3, component)
    for z in range(-2, 1):
        yield from _dust(origin, 6, z, component)


def emit_nand(origin: Vec3, component: str) -> Iterator[Placement]:
    # Invert A and B first.
    for x in (0, 3):
        yield from _dust(origin, x, 0, component)
        yield _place(origin, x, 0, -1, SUPPORT, component)
        yield _place(origin, x, 1, -1, repeater("north"), component)
        yield _place(origin, x, 1, -2, SUPPORT, component)
        yield _place(origin, x, 1, -3, WALL_TORCH_N, component)

    # OR the inverted inputs on a dust line.
    for x in range(0, 5):
        yield from _dust(origin, x, -4, component)

    # !(~A | ~B) = A&B.
    yield _place(origin, 4, 0, -4, SUPPORT, component)
    yield _place(origin, 4, 1, -4, repeater("east"), component)
    yield _place(origin, 5, 1, -4, SUPPORT, component)
    yield _place(origin, 6, 1, -4, WALL_TORCH_E, component)

    # Invert once more -> NAND.
    yield from _dust(origin, 7, -4, component)
    yield _place(origin, 8, 0, -4, SUPPORT, component)
    yield _place(origin, 8, 1, -4, repeater("east"), component)
    yield _place(origin, 9, 1, -4, SUPPORT, component)
    yield _place(origin, 10, 1, -4, WALL_TORCH_E, component)

    for x in range(11, 13):
        yield from _dust(origin, x, -4, component)
    for z in range(-3, 1):
        yield from _dust(origin, 12, z, component)


def emit_dff(origin: Vec3, component: str) -> Iterator[Placement]:
    """Positive-edge master/slave DFF built from locked repeaters.

    Master is transparent while C=0 and locks on C=1.  Slave is locked while
    C=0 and becomes transparent on C=1, so Q updates from the master's held
    value on the rising edge.
    """
    yield from _dust(origin, 0, 0, component)  # D
    yield from _dust(origin, 3, 0, component)  # C

    # D path to master.
    yield from _dust(origin, 0, -1, component)
    yield _place(origin, 0, 0, -2, SUPPORT, component)
    yield _place(origin, 0, 1, -2, repeater("north", locked=True), component)

    # Direct C path to master lock (east side of master).
    yield from _dust(origin, 3, -1, component)
    yield from _dust(origin, 2, -1, component)
    yield from _dust(origin, 2, -2, component)
    yield _place(origin, 1, 0, -2, SUPPORT, component)
    yield _place(origin, 1, 1, -2, repeater("west"), component)

    # Master Q to slave D.
    yield from _dust(origin, 0, -3, component)
    yield from _dust(origin, 0, -4, component)
    yield _place(origin, 0, 0, -5, SUPPORT, component)
    yield _place(origin, 0, 1, -5, repeater("north", locked=True), component)

    # Inverted C for slave lock.
    yield _place(origin, 3, 1, -2, SUPPORT, component)
    yield _place(origin, 3, 1, -3, WALL_TORCH_N, component)
    yield from _dust(origin, 3, -4, component)
    yield from _dust(origin, 2, -4, component)
    yield from _dust(origin, 2, -5, component)
    yield _place(origin, 1, 0, -5, SUPPORT, component)
    yield _place(origin, 1, 1, -5, repeater("west"), component)

    # Slave Q routed back to the routing boundary.
    yield from _dust(origin, 0, -6, component)
    for x in range(1, 15):
        yield from _dust(origin, x, -6, component)
    for z in range(-5, 1):
        yield from _dust(origin, 14, z, component)


TEMPLATES: dict[str, LogicTemplate] = {
    "BUF": LogicTemplate(
        "BUF", 4, 2, {"A": Vec3(0, 1, 0), "Y": Vec3(3, 1, 0)}, emit_buf
    ),
    "NOT": LogicTemplate(
        "NOT", 5, 2, {"A": Vec3(0, 1, 0), "Y": Vec3(4, 1, 0)}, emit_not
    ),
    "NAND": LogicTemplate(
        "NAND",
        13,
        6,
        {"A": Vec3(0, 1, 0), "B": Vec3(3, 1, 0), "Y": Vec3(12, 1, 0)},
        emit_nand,
    ),
    "NOR": LogicTemplate(
        "NOR",
        7,
        5,
        {"A": Vec3(0, 1, 0), "B": Vec3(3, 1, 0), "Y": Vec3(6, 1, 0)},
        emit_nor,
    ),
    "DFF": LogicTemplate(
        "DFF",
        15,
        8,
        {"D": Vec3(0, 1, 0), "C": Vec3(3, 1, 0), "Q": Vec3(14, 1, 0)},
        emit_dff,
    ),
}


def normalize_cell_type(cell_type: str) -> str:
    return cell_type.lstrip("\").upper()


def template_for(cell_type: str) -> LogicTemplate:
    key = normalize_cell_type(cell_type)
    try:
        return TEMPLATES[key]
    except KeyError as exc:
        raise KeyError(f"unsupported physical redstone cell {cell_type!r}") from exc
