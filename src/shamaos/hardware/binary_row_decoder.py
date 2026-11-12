"""Literal compact binary row decode, independent of generic gate-grid routes.

Address buses and isolated mismatch columns feed each native row input.
"""
from __future__ import annotations

from typing import Iterator

from ..model import BlockState, Box, Placement, Vec3
from .memory import DUST, SUPPORT, repeater
from .memory_backbone import StackedMemoryTileSpec, stacked_tile_ports
from .routing import iter_stair
from .vertical_wiring import iter_helical_wire


def binary_row_address_ports(origin: Vec3, tile: StackedMemoryTileSpec, *,
                             decoder_x: int, address_bits: int | None = None) -> tuple[Vec3, ...]:
    required = max(1, (tile.bank.words - 1).bit_length())
    bits = required if address_bits is None else address_bits
    if bits < required:
        raise ValueError("address width cannot represent every physical row")
    return tuple(Vec3(decoder_x + 8, origin.y + bit * 12, origin.z - 8) for bit in range(bits))


def binary_row_decoder_bounds(origin: Vec3, tile: StackedMemoryTileSpec, *, decoder_x: int) -> Box:
    address = binary_row_address_ports(origin, tile, decoder_x=decoder_x)
    rows = stacked_tile_ports(origin, tile).row_select
    bounds = Box(Vec3(decoder_x - (4 if len(address) > 1 else 0), origin.y - 1, origin.z - 8),
                 Vec3(max(pin.x for pin in rows), max(address[-1].y, origin.y + 4), rows[-1].z + 4))
    if bounds.min.y < -64 or bounds.max.y > 307:
        raise ValueError("binary row decoder exceeds its Java routing height")
    return bounds


def binary_row_collision_bounds(origin: Vec3, tile: StackedMemoryTileSpec, *, decoder_x: int) -> tuple[Box, Box]:
    """Separate tall decode columns from low output bridges for packing.

    A single enclosing box incorrectly reserves the empty space above the
    bridges. These two conservative volumes contain every literal block,
    while allowing another fabric above a bridge and east of its columns.
    """
    bounds = binary_row_decoder_bounds(origin, tile, decoder_x=decoder_x)
    return (Box(bounds.min, Vec3(decoder_x + 8, bounds.max.y, bounds.max.z)),
            Box(Vec3(decoder_x, origin.y - 1, origin.z + 3),
                Vec3(bounds.max.x, origin.y + 4, bounds.max.z)))


def binary_row_obstacle_bounds(origin: Vec3, tile: StackedMemoryTileSpec, *, decoder_x: int) -> tuple[Box, ...]:
    """Separate the narrow front buses from the dense decoder body.

    Keeping the gaps between address levels explicit allows enable leads to
    leave the native front controls without passing through a false solid wall.
    """
    address = binary_row_address_ports(origin, tile, decoder_x=decoder_x)
    rows = stacked_tile_ports(origin, tile).row_select
    body = binary_row_decoder_bounds(origin, tile, decoder_x=decoder_x)
    body = Box(Vec3(body.min.x, body.min.y, rows[0].z - (4 if len(address) > 1 else 0)), body.max)
    return (body, *(Box(pin.offset(dy=-1), Vec3(pin.x, pin.y, rows[-1].z)) for pin in address))


def iter_binary_row_terminal_keepouts(origin: Vec3, tile: StackedMemoryTileSpec, *,
                                      decoder_x: int) -> Iterator[Placement]:
    for bit, pin in enumerate(binary_row_address_ports(origin, tile, decoder_x=decoder_x)):
        for dz in range(4):
            point = pin.offset(dz=dz)
            yield Placement(point.offset(dy=-1), SUPPORT, f"binary-address{bit}")
            yield Placement(point, DUST, f"binary-address{bit}")


def iter_binary_row_decoder(origin: Vec3, tile: StackedMemoryTileSpec, *,
                            decoder_x: int, address_bits: int | None = None,
                            component: str = "binary-row-decoder") -> Iterator[Placement]:
    """Decode every row with an isolated wired-OR of its mismatching bits.

    Ten vertically separated address buses serve 1,024 physical rows. Each
    row has a small downward spiral that merges isolated mismatch tests.
    One final inverter selects that row only when every address bit matches.
    """
    rows = stacked_tile_ports(origin, tile).row_select
    if decoder_x + 20 >= min(pin.x for pin in rows):
        raise ValueError("binary decoder needs its output bridge west of the row input")
    address = binary_row_address_ports(origin, tile, decoder_x=decoder_x, address_bits=address_bits)
    if address[-1].y > 307 or origin.y - 1 < -64:
        raise ValueError("binary row decoder exceeds its Java routing height")

    def cell(point: Vec3, block, name: str) -> Iterator[Placement]:
        yield Placement(point.offset(dy=-1), SUPPORT, name)
        yield Placement(point, block, name)

    for bit, pin in enumerate(address):
        for z in range(pin.z, rows[-1].z + 1):
            point = Vec3(pin.x, pin.y, z)
            refresh = z != pin.z and z != rows[-1].z and (z - origin.z) % 12 == 0
            yield from cell(point, repeater("south") if refresh else DUST, f"{component}:address{bit}")

    for row, pin in enumerate(rows):
        name = f"{component}:row{row}"
        start = Vec3(decoder_x, origin.y, pin.z)
        if len(address) > 1:
            yield from iter_helical_wire(start, (len(address) - 1) * 12, signal_upward=False,
                                        quarter_turns=2, terminal_stub=False, component=name)
        else:
            yield from cell(start, DUST, name)
        for bit, bus in enumerate(address):
            # Bus x+8 -> isolated inverted/direct mismatch -> x+1 diode -> OR.
            for dx in range(1, 8):
                point = Vec3(decoder_x + dx, bus.y, pin.z)
                if dx in (1, 7):
                    state = repeater("west")
                elif row >> bit & 1 and dx == 6:
                    state = SUPPORT
                elif row >> bit & 1 and dx == 5:
                    state = BlockState.of("minecraft:redstone_wall_torch", facing="west", lit="true")
                else:
                    state = DUST
                yield from cell(point, state, name)
        # Invert mismatch, cross above address bit zero, then return to the
        # native row-input plane before entering any local bank gate.
        for dz, state in ((1, repeater("south")), (2, SUPPORT),
                          (3, BlockState.of("minecraft:redstone_wall_torch", facing="south", lit="true"))):
            yield from cell(start.offset(dz=dz), state, name)
        bridge = start.offset(dz=4)
        yield from iter_stair(bridge, bridge.offset(6, 4, 0), axis="x", signal_forward=True, component=name)
        for dx in range(7, 12):
            yield from cell(bridge.offset(dx, 4, 0), repeater("east") if dx == 8 else DUST, name)
        yield from iter_stair(bridge.offset(12, 4, 0), bridge.offset(18, 0, 0), axis="x", signal_forward=True, component=name)
        for x in range(decoder_x + 19, pin.x + 1):
            refresh = x != pin.x and ((x - decoder_x - 19) % 10 == 0 or x == pin.x - 1)
            yield from cell(Vec3(x, origin.y, bridge.z), repeater("east") if refresh else DUST, name)
        for z in range(pin.z, bridge.z):
            yield from cell(Vec3(pin.x, pin.y, z), repeater("north") if z == pin.z + 1 else DUST, name)
