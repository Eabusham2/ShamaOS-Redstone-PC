from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from ..model import BlockState, Placement, Vec3
from .memory import DUST, SUPPORT, TORCH, repeater


LAMP_OFF = BlockState.of("minecraft:redstone_lamp", lit="false")
BLACK = BlockState.of("minecraft:black_concrete")


@dataclass(frozen=True)
class LampPanelSpec:
    width: int = 192
    height: int = 108
    pixel_pitch_x: int = 4
    pixel_pitch_y: int = 2

    @property
    def physical_width(self) -> int:
        return self.width * self.pixel_pitch_x

    @property
    def physical_height(self) -> int:
        return self.height * self.pixel_pitch_y

    @property
    def depth(self) -> int:
        return 5


@dataclass(frozen=True)
class LampPanelPorts:
    data_in: tuple[Vec3, ...]
    row_select: tuple[Vec3, ...]


def _map(origin: Vec3, vx: int, vy_depth: int, vz_row: int) -> Vec3:
    """Virtual x/z matrix -> vertical world x/y, with circuit depth on world z."""
    return Vec3(origin.x + vx, origin.y + vz_row, origin.z + vy_depth)


def _place_virtual(
    origin: Vec3,
    vx: int,
    vy: int,
    vz: int,
    block: BlockState,
    component: str,
) -> Placement:
    return Placement(_map(origin, vx, vy, vz), block, component)


def _same_polarity_depth_riser(
    origin: Vec3,
    vx: int,
    vz: int,
    *,
    component: str,
) -> Iterator[Placement]:
    # Same two-torch non-inverting riser used by memory, rotated so "height"
    # becomes panel depth.  Data buses live at virtual depth 0 and the latch
    # plane at virtual depth 3.
    yield _place_virtual(origin, vx, 0, vz, SUPPORT, component)
    yield _place_virtual(origin, vx, 1, vz, TORCH, component)
    yield _place_virtual(origin, vx, 2, vz, SUPPORT, component)
    yield _place_virtual(origin, vx, 3, vz, TORCH, component)


def panel_ports(origin: Vec3, spec: LampPanelSpec) -> LampPanelPorts:
    data = tuple(
        _map(origin, col * spec.pixel_pitch_x, 0, -2)
        for col in range(spec.width)
    )
    rows = tuple(
        _map(origin, -6, 3, 1 + row * spec.pixel_pitch_y - 2)
        for row in range(spec.height)
    )
    return LampPanelPorts(data, rows)


def iter_lamp_panel(
    origin: Vec3,
    *,
    spec: LampPanelSpec = LampPanelSpec(),
    component: str = "display",
    backing: bool = True,
) -> Iterator[Placement]:
    """Emit a vertical lamp screen with real per-pixel storage latches.

    192 column data buses are behind the screen.  A row-select pulse turns off
    that row's normally-on lock line; every pixel's locked repeater samples its
    column bit and then re-locks.  The repeater output directly powers the lamp.

    The panel therefore only needs WIDTH data wires + HEIGHT row selects, not a
    unique long wire for every pixel.  shama_display_bridge emits matching
    parallel row commits from the GPU serial frame stream.
    """
    if spec.width <= 0 or spec.height <= 0:
        raise ValueError("invalid panel geometry")

    row_end = 1 + (spec.height - 1) * spec.pixel_pitch_y

    # Column input buses at back depth 0, flowing upward.  Repeaters refresh
    # signal strength every 12 virtual blocks.
    for col in range(spec.width):
        vx = col * spec.pixel_pitch_x
        for vz in range(-2, row_end + 3):
            yield _place_virtual(origin, vx, -1, vz, SUPPORT, component)
            if vz >= 0 and vz % 12 == 0:
                yield _place_virtual(
                    origin, vx, 0, vz, repeater("south"), component
                )
            else:
                yield _place_virtual(origin, vx, 0, vz, DUST, component)

    for row in range(spec.height):
        center = 1 + row * spec.pixel_pitch_y
        lock_line = center - 2

        # External active-high row_select powers the inverter block; the torch
        # goes dark and the horizontal lock line releases only this row.
        yield _place_virtual(origin, -5, 3, lock_line, SUPPORT, component)
        yield _place_virtual(
            origin,
            -4,
            3,
            lock_line,
            BlockState.of("minecraft:redstone_wall_torch", facing="east", lit="true"),
            component,
        )

        x_end = (spec.width - 1) * spec.pixel_pitch_x + 2
        for vx in range(-3, x_end + 1):
            yield _place_virtual(origin, vx, 2, lock_line, SUPPORT, component)
            if vx >= 0 and vx % 12 == 0:
                yield _place_virtual(
                    origin, vx, 3, lock_line, repeater("east"), component
                )
            else:
                yield _place_virtual(origin, vx, 3, lock_line, DUST, component)

        for col in range(spec.width):
            vx = col * spec.pixel_pitch_x

            # Column signal rises from depth 0 to the latch plane depth 3.
            yield from _same_polarity_depth_riser(
                origin, vx + 1, center, component=component
            )

            # Storage repeater and side-lock repeater.
            yield _place_virtual(origin, vx + 2, 2, center, SUPPORT, component)
            yield _place_virtual(
                origin,
                vx + 2,
                3,
                center,
                repeater("east", powered=False, locked=True),
                component,
            )
            yield _place_virtual(origin, vx + 2, 2, center - 1, SUPPORT, component)
            yield _place_virtual(
                origin,
                vx + 2,
                3,
                center - 1,
                repeater("south", powered=True),
                component,
            )

            # Lamp is on the same visible plane as the latch output; black
            # backing fills unused pixel pitch so the screen reads as a panel.
            yield _place_virtual(origin, vx + 3, 3, center, LAMP_OFF, component)

        if backing:
            for vx in range(0, spec.physical_width):
                # One-block backing immediately behind/around the display row.
                # Do not overwrite active circuit cells: use depth 4 as frame.
                yield _place_virtual(origin, vx, 4, center, BLACK, component)

    # Explicit terminal dust for row-select inputs.
    ports = panel_ports(origin, spec)
    for p in ports.row_select:
        yield Placement(Vec3(p.x, p.y, p.z - 1), SUPPORT, component)
        yield Placement(p, DUST, component)
