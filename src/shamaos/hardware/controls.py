from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from ..model import BlockState, Placement, Vec3
from .memory import DUST, SUPPORT, repeater


BUTTON_FLOOR = BlockState.of(
    "minecraft:stone_button", face="floor", facing="north", powered="false"
)
BUTTON_WALL_S = BlockState.of(
    "minecraft:stone_button", face="wall", facing="south", powered="false"
)
LEVER = BlockState.of(
    "minecraft:lever", face="floor", facing="north", powered="false"
)
PANEL = BlockState.of("minecraft:polished_deepslate")
KEY = BlockState.of("minecraft:smooth_quartz")


CONTROLLER_NAMES = (
    "UP", "DOWN", "LEFT", "RIGHT", "A", "B", "HOME", "EXIT", "EDITOR", "FILES"
)


@dataclass(frozen=True)
class ControlPorts:
    controller: tuple[Vec3, ...]
    keyboard_rows: tuple[Vec3, ...]
    keyboard_cols: tuple[Vec3, ...]
    power: Vec3
    reset: Vec3


def iter_controller_panel(
    origin: Vec3,
    *,
    component: str = "controller",
) -> Iterator[Placement]:
    # Compact 5x2 button bank.  Each button sits on a powered pickup block and
    # has an isolated repeater output behind it.
    positions = (
        (2,0), (2,2), (1,1), (3,1),
        (6,0), (7,1), (9,0), (10,1), (12,0), (13,1),
    )
    for idx,(x,z) in enumerate(positions):
        yield Placement(origin.offset(x,0,z), PANEL, component)
        yield Placement(origin.offset(x,1,z), BUTTON_FLOOR, component)
        yield Placement(origin.offset(x,0,z+1), SUPPORT, component)
        yield Placement(origin.offset(x,1,z+1), repeater("south"), component)
        yield Placement(origin.offset(x,0,z+2), SUPPORT, component)
        yield Placement(origin.offset(x,1,z+2), DUST, component)

    # Power + reset are deliberately separated from app controls.
    yield Placement(origin.offset(16,0,0), PANEL, component)
    yield Placement(origin.offset(16,1,0), LEVER, component)
    yield Placement(origin.offset(16,0,1), SUPPORT, component)
    yield Placement(origin.offset(16,1,1), repeater("south"), component)
    yield Placement(origin.offset(16,0,2), SUPPORT, component)
    yield Placement(origin.offset(16,1,2), DUST, component)
    yield Placement(origin.offset(18,0,0), PANEL, component)
    yield Placement(origin.offset(18,1,0), BUTTON_FLOOR, component)
    yield Placement(origin.offset(18,0,1), SUPPORT, component)
    yield Placement(origin.offset(18,1,1), repeater("south"), component)
    yield Placement(origin.offset(18,0,2), SUPPORT, component)
    yield Placement(origin.offset(18,1,2), DUST, component)


def iter_keyboard_matrix(
    origin: Vec3,
    *,
    component: str = "keyboard",
) -> Iterator[Placement]:
    """Emit an 8x8 keyboard contact matrix.

    A floor button powers its key block.  East/west repeaters feed one of eight
    row buses at layer y+1.  A two-torch riser feeds one of eight column buses
    at y+4 so row/column traces cross without joining.  The input RTL converts
    row+column one-hot activity to ASCII/control codes.
    """
    key_pitch = 5

    # Row buses at y+1, extending across all eight columns.
    for row in range(8):
        z = row * key_pitch + 2
        for x in range(-2, 8*key_pitch + 2):
            yield Placement(origin.offset(x,0,z), SUPPORT, component)
            if x >= 0 and x % 12 == 0:
                yield Placement(origin.offset(x,1,z), repeater("east"), component)
            else:
                yield Placement(origin.offset(x,1,z), DUST, component)

    # Column buses at y+4.
    for col in range(8):
        x = col * key_pitch + 2
        for z in range(-2, 8*key_pitch + 2):
            yield Placement(origin.offset(x,3,z), SUPPORT, component)
            if z >= 0 and z % 12 == 0:
                yield Placement(origin.offset(x,4,z), repeater("south"), component)
            else:
                yield Placement(origin.offset(x,4,z), DUST, component)

    for row in range(8):
        for col in range(8):
            x = col * key_pitch
            z = row * key_pitch
            # Key block and button.
            yield Placement(origin.offset(x,0,z), KEY, component)
            yield Placement(origin.offset(x,1,z), BUTTON_FLOOR, component)

            # Row pickup to the row trace at z+2.
            yield Placement(origin.offset(x,0,z+1), SUPPORT, component)
            yield Placement(origin.offset(x,1,z+1), repeater("south"), component)

            # Column pickup, routed through a non-inverting 2-torch riser to
            # avoid joining the row layer.
            yield Placement(origin.offset(x+1,0,z), SUPPORT, component)
            yield Placement(origin.offset(x+1,1,z), repeater("east"), component)
            yield Placement(origin.offset(x+2,1,z), SUPPORT, component)
            yield Placement(
                origin.offset(x+2,2,z),
                BlockState.of("minecraft:redstone_torch", lit="true"),
                component,
            )
            yield Placement(origin.offset(x+2,3,z), SUPPORT, component)
            yield Placement(
                origin.offset(x+2,4,z),
                BlockState.of("minecraft:redstone_torch", lit="true"),
                component,
            )


def control_ports(origin: Vec3) -> ControlPorts:
    ctrl_positions = (
        (2,2), (2,4), (1,3), (3,3),
        (6,2), (7,3), (9,2), (10,3), (12,2), (13,3),
    )
    controller = tuple(origin.offset(x,1,z) for x,z in ctrl_positions)

    kb = origin.offset(0,0,16)
    rows = tuple(kb.offset(-2,1,row*5+2) for row in range(8))
    cols = tuple(kb.offset(col*5+2,4,-2) for col in range(8))
    return ControlPorts(
        controller,
        rows,
        cols,
        origin.offset(16,1,2),
        origin.offset(18,1,2),
    )


def iter_controls(origin: Vec3, *, component: str = "input") -> Iterator[Placement]:
    yield from iter_controller_panel(origin, component=component)
    yield from iter_keyboard_matrix(origin.offset(0,0,16), component=component)
