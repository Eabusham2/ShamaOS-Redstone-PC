from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from ..model import BlockState, Box, Placement, Vec3
from .memory import DUST, SUPPORT, redstone_wire, repeater


LAMP_OFF = BlockState.of("minecraft:redstone_lamp", lit="false")
BLACK = BlockState.of("minecraft:black_concrete")
WALL_TORCH_E = BlockState.of(
    "minecraft:redstone_wall_torch", facing="east", lit="true"
)
TORCH_ON = BlockState.of("minecraft:redstone_torch", lit="true")
TORCH_OFF = BlockState.of("minecraft:redstone_torch", lit="false")


@dataclass(frozen=True)
class LampPanelSpec:
    width: int = 320
    height: int = 180
    pixel_pitch_x: int = 5
    pixel_pitch_y: int = 4

    @property
    def physical_width(self) -> int:
        return self.width * self.pixel_pitch_x

    @property
    def physical_height(self) -> int:
        # Row extent on world Z.
        return self.height * self.pixel_pitch_y

    @property
    def depth(self) -> int:
        # Vertical circuit/lamp thickness on world Y.
        return 6


@dataclass(frozen=True)
class LampPanelPorts:
    data_in: tuple[Vec3, ...]
    row_select: tuple[Vec3, ...]


def panel_ports(origin: Vec3, spec: LampPanelSpec) -> LampPanelPorts:
    data_y = origin.y + 1
    row_y = origin.y - 2

    data = tuple(
        Vec3(
            origin.x + col * spec.pixel_pitch_x,
            data_y,
            origin.z - 64,
        )
        for col in range(spec.width)
    )
    rows = tuple(
        Vec3(
            origin.x - 64,
            row_y,
            origin.z + row * spec.pixel_pitch_y - 4,
        )
        for row in range(spec.height)
    )
    return LampPanelPorts(data, rows)


def panel_bounds(origin: Vec3, spec: LampPanelSpec) -> Box:
    return Box(origin.offset(-64,-3,-64),origin.offset(
        (spec.width-1)*spec.pixel_pitch_x+4,2,(spec.height-1)*spec.pixel_pitch_y+2))


def iter_panel_terminal_keepouts(origin: Vec3, spec: LampPanelSpec) -> Iterator[Placement]:
    """Exact first four native cells, independent of the pixel count."""
    ports=panel_ports(origin,spec)
    for pin in ports.data_in:
        yield from _wire_z(pin.x,pin.y,pin.z,pin.z+3,component="display-terminal",facing="south",tap_mod=spec.pixel_pitch_y)
    for pin in ports.row_select:
        yield from _wire_x(pin.x,pin.x+3,pin.y,pin.z,component="display-terminal",facing="east")


def _wire_x(
    x0: int,
    x1: int,
    y: int,
    z: int,
    *,
    component: str,
    facing: str,
    tap_mod: int = 5,
    initial_powered: bool = False,
) -> Iterator[Placement]:
    step = 1 if x1 >= x0 else -1
    for n, x in enumerate(range(x0, x1 + step, step)):
        yield Placement(Vec3(x, y - 1, z), SUPPORT, component)
        # Keep repeaters between pixel tap coordinates.
        use_repeater = n and n % 10 == 3 and n % tap_mod != 0
        yield Placement(
            Vec3(x, y, z),
            repeater(facing, powered=initial_powered) if use_repeater else
            (redstone_wire(power=15) if initial_powered else DUST),
            component,
        )


def _wire_z(
    x: int,
    y: int,
    z0: int,
    z1: int,
    *,
    component: str,
    facing: str,
    tap_mod: int = 5,
) -> Iterator[Placement]:
    step = 1 if z1 >= z0 else -1
    for n, z in enumerate(range(z0, z1 + step, step)):
        yield Placement(Vec3(x, y - 1, z), SUPPORT, component)
        use_repeater = n and n % 10 == 3 and n % tap_mod != 0
        yield Placement(
            Vec3(x, y, z),
            repeater(facing) if use_repeater else DUST,
            component,
        )


def iter_lamp_panel(
    origin: Vec3,
    *,
    spec: LampPanelSpec = LampPanelSpec(),
    component: str = "display",
    backing: bool = True,
) -> Iterator[Placement]:
    """Emit a real 320x180 latched lamp display on a horizontal X/Z plane.

    The display is viewed from above. This orientation is intentional: every
    repeater/dust bus stays horizontal and therefore behaves as real Minecraft
    redstone.

    Per pixel:
      * one column data bus carries the row bit;
      * a data repeater drives a locked storage repeater;
      * a normally-powered side repeater locks the storage element;
      * the selected row's inverted lock bus releases all 320 pixels;
      * the storage repeater strongly powers a block directly below its lamp.

    Data buses are at y+1. Row-lock buses are at y-2, crossing underneath them
    without joining. Isolated two-torch risers bring each lock tap to its side
    repeater without replacing or interrupting the row bus.
    """
    if spec.width <= 0 or spec.height <= 0:
        raise ValueError("invalid panel geometry")
    if spec.pixel_pitch_x < 5 or spec.pixel_pitch_y < 4:
        raise ValueError("lamp-panel pitch must be at least 5 X blocks and 4 Z blocks")

    data_y = origin.y + 1
    row_y = origin.y - 2
    last_row_z = origin.z + (spec.height - 1) * spec.pixel_pitch_y

    # 320 broadcast data columns, front terminal -> full row depth.
    for col in range(spec.width):
        bx = origin.x + col * spec.pixel_pitch_x
        yield from _wire_z(
            bx,
            data_y,
            origin.z - 64,
            last_row_z + 2,
            component=component,
            facing="south",
            tap_mod=spec.pixel_pitch_y,
        )

    for row in range(spec.height):
        center_z = origin.z + row * spec.pixel_pitch_y
        row_bus_z = center_z - 4

        # External active-high select travels to a local inverter.
        yield from _wire_x(
            origin.x - 64,
            origin.x - 6,
            row_y,
            row_bus_z,
            component=component,
            facing="east",
        )
        yield Placement(
            Vec3(origin.x - 5, row_y, row_bus_z),
            SUPPORT,
            component,
        )
        yield Placement(
            Vec3(origin.x - 4, row_y, row_bus_z),
            WALL_TORCH_E,
            component,
        )

        # Torch output is normally high: row is locked until selected.
        row_end_x = (
            origin.x
            + (spec.width - 1) * spec.pixel_pitch_x
            + 4
        )
        yield from _wire_x(
            origin.x - 3,
            row_end_x,
            row_y,
            row_bus_z,
            component=component,
            facing="east",
            tap_mod=spec.pixel_pitch_x,
            initial_powered=True,
        )

        for col in range(spec.width):
            bx = origin.x + col * spec.pixel_pitch_x

            # Data tap -> storage repeater.
            yield Placement(
                Vec3(bx + 1, data_y - 1, center_z),
                SUPPORT,
                component,
            )
            yield Placement(
                Vec3(bx + 1, data_y, center_z),
                repeater("east"),
                component,
            )
            yield Placement(
                Vec3(bx + 2, data_y - 1, center_z),
                SUPPORT,
                component,
            )
            # Actual pixel memory.
            yield Placement(
                Vec3(
                    bx + 2,
                    data_y,
                    center_z,
                ),
                repeater(
                    "east",
                    powered=False,
                    locked=True,
                ),
                component,
            )

            # Normally-powered side-lock repeater, north of storage.
            yield Placement(
                Vec3(bx + 2, data_y - 1, center_z - 1),
                SUPPORT,
                component,
            )
            yield Placement(
                Vec3(bx + 2, data_y, center_z - 1),
                repeater("south", powered=True),
                component,
            )

            # Branch dust turns the E/W bus toward the solid riser block.
            # Without this branch, Java normalizes the bus to a straight wire
            # that does not weakly power the block beside it.
            yield Placement(Vec3(bx + 2,row_y - 1,center_z - 3),SUPPORT,component)
            yield Placement(Vec3(bx + 2,row_y,center_z - 3),redstone_wire(power=15),component)
            # Two inversions preserve the high=locked mask. Its initial high
            # state prevents spurious writes while the world settles.
            for dy, block in ((0,SUPPORT),(1,TORCH_OFF),(2,SUPPORT),(3,TORCH_ON)):
                yield Placement(Vec3(bx + 2,row_y + dy,center_z - 2),block,component)

            # Powered block + visible lamp above it.
            # Leave an air column before the next pixel's data wire. A
            # strongly powered block at x+4 would back-power that x+5 wire.
            lamp_block = Vec3(bx + 3, data_y, center_z)
            yield Placement(lamp_block, SUPPORT, component)
            yield Placement(
                lamp_block.offset(dy=1),
                LAMP_OFF,
                component,
            )

            if backing:
                # Black tile around the lit pixel at the visible plane.
                for dx, dz in (
                    (0, -1), (0, 1), (1, -1), (1, 1),
                    (2, -1), (2, 1), (3, -1), (3, 1),
                    (4, -1), (4, 1),
                ):
                    yield Placement(
                        Vec3(bx + dx, data_y + 1, center_z + dz),
                        BLACK,
                        component,
                    )
