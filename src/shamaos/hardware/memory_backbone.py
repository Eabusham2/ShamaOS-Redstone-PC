from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterator

from ..model import BlockState, Placement, Vec3
from .memory import (
    DUST,
    SUPPORT,
    MemoryFabricSpec,
    bank_ports,
    repeater,
)


WALL_TORCH_E = BlockState.of(
    "minecraft:redstone_wall_torch", facing="east", lit="true"
)
WALL_TORCH_N = BlockState.of(
    "minecraft:redstone_wall_torch", facing="north", lit="true"
)
WALL_TORCH_S = BlockState.of(
    "minecraft:redstone_wall_torch", facing="south", lit="true"
)


@dataclass(frozen=True)
class MemoryBackbonePorts:
    bank_select: tuple[Vec3, ...]
    row_select: tuple[Vec3, ...]
    read_enable: Vec3
    write_enable: Vec3
    write_data: tuple[Vec3, ...]
    selected_word: tuple[Vec3, ...]


def _bank_origin(
    origin: Vec3,
    fabric: MemoryFabricSpec,
    bank_index: int,
) -> Vec3:
    gx = bank_index % fabric.banks_per_row
    gz = bank_index // fabric.banks_per_row
    return Vec3(
        origin.x + gx * (fabric.bank.width + fabric.bank_gap_x),
        origin.y,
        origin.z + gz * (fabric.bank.depth + fabric.bank_gap_z),
    )


def _wire_x(
    x0: int,
    x1: int,
    y: int,
    z: int,
    *,
    facing: str,
    component: str,
    stride: int = 10,
) -> Iterator[Placement]:
    step = 1 if x1 >= x0 else -1
    facing_dir = facing
    for n, x in enumerate(range(x0, x1 + step, step)):
        yield Placement(Vec3(x, y - 1, z), SUPPORT, component)
        if n and n % stride == 0:
            yield Placement(Vec3(x, y, z), repeater(facing_dir), component)
        else:
            yield Placement(Vec3(x, y, z), DUST, component)


def _wire_z(
    x: int,
    y: int,
    z0: int,
    z1: int,
    *,
    facing: str,
    component: str,
    stride: int = 10,
) -> Iterator[Placement]:
    step = 1 if z1 >= z0 else -1
    for n, z in enumerate(range(z0, z1 + step, step)):
        yield Placement(Vec3(x, y - 1, z), SUPPORT, component)
        if n and n % stride == 0:
            yield Placement(Vec3(x, y, z), repeater(facing), component)
        else:
            yield Placement(Vec3(x, y, z), DUST, component)


def _stair_x(
    start: Vec3,
    end: Vec3,
    *,
    component: str,
) -> Iterator[Placement]:
    """Dust staircase along X; |dx| must be >= |dy| and Z is constant."""
    if start.z != end.z:
        raise ValueError("X staircase requires a constant Z")
    dx = end.x - start.x
    dy = end.y - start.y
    steps = abs(dx)
    if steps < abs(dy):
        raise ValueError("not enough horizontal run for X staircase")
    sx = 1 if dx >= 0 else -1
    sy = 1 if dy >= 0 else -1
    remaining_y = abs(dy)
    y = start.y
    for n in range(steps + 1):
        x = start.x + sx * n
        if n and remaining_y and (steps - n + 1) >= remaining_y:
            y += sy
            remaining_y -= 1
        yield Placement(Vec3(x, y - 1, start.z), SUPPORT, component)
        yield Placement(Vec3(x, y, start.z), DUST, component)


def _stair_z(
    start: Vec3,
    end: Vec3,
    *,
    component: str,
) -> Iterator[Placement]:
    """Dust staircase along Z; |dz| must be >= |dy| and X is constant."""
    if start.x != end.x:
        raise ValueError("Z staircase requires a constant X")
    dz = end.z - start.z
    dy = end.y - start.y
    steps = abs(dz)
    if steps < abs(dy):
        raise ValueError("not enough horizontal run for Z staircase")
    sz = 1 if dz >= 0 else -1
    sy = 1 if dy >= 0 else -1
    remaining_y = abs(dy)
    y = start.y
    for n in range(steps + 1):
        z = start.z + sz * n
        if n and remaining_y and (steps - n + 1) >= remaining_y:
            y += sy
            remaining_y -= 1
        yield Placement(Vec3(start.x, y - 1, z), SUPPORT, component)
        yield Placement(Vec3(start.x, y, z), DUST, component)


def _and3_to_target(
    a: Vec3,
    b: Vec3,
    c: Vec3,
    target: Vec3,
    *,
    output_side: int,
    component: str,
) -> Iterator[Placement]:
    """Three-input AND implemented as DeMorgan torch logic.

    Inputs arrive as dust atop support blocks on a common Y/Z plane:
    !( !A OR !B OR !C ) = A & B & C.
    """
    if not (a.y == b.y == c.y and a.z == b.z == c.z):
        raise ValueError("AND3 inputs must share a plane")
    gate_y = a.y
    gate_z = a.z
    inputs = (a, b, c)
    torch_z = gate_z + output_side
    or_z = gate_z + output_side * 2

    torch_state = WALL_TORCH_S if output_side > 0 else WALL_TORCH_N

    for p in inputs:
        yield Placement(Vec3(p.x, gate_y - 1, gate_z), SUPPORT, component)
        yield Placement(Vec3(p.x, gate_y - 1, torch_z), torch_state, component)

    x0 = min(p.x for p in inputs)
    x1 = max(p.x for p in inputs) + 2
    yield from _wire_x(
        x0,
        x1,
        gate_y - 1,
        or_z,
        facing="east",
        component=component,
    )

    # Strongly power an inverter block from the OR line.
    inv_x = x1 + 1
    yield Placement(Vec3(inv_x, gate_y - 2, or_z), SUPPORT, component)
    yield Placement(
        Vec3(inv_x, gate_y - 1, or_z),
        repeater("east"),
        component,
    )
    yield Placement(Vec3(inv_x + 1, gate_y - 1, or_z), SUPPORT, component)
    yield Placement(Vec3(inv_x + 2, gate_y - 1, or_z), WALL_TORCH_E, component)

    gate_out = Vec3(inv_x + 3, gate_y - 1, or_z)
    yield Placement(Vec3(gate_out.x, gate_out.y - 1, gate_out.z), SUPPORT, component)
    yield Placement(gate_out, DUST, component)

    # The 192-block bank corridor leaves plenty of run to descend to the
    # bank's row-select terminal without entering the bit-cell area.
    stair_end = Vec3(target.x - 4, target.y, gate_out.z)
    yield from _stair_x(gate_out, stair_end, component=component)
    yield from _wire_z(
        stair_end.x,
        target.y,
        stair_end.z,
        target.z,
        facing="south" if target.z >= stair_end.z else "north",
        component=component,
    )
    yield from _wire_x(
        stair_end.x,
        target.x,
        target.y,
        target.z,
        facing="east",
        component=component,
    )


def backbone_ports(
    origin: Vec3,
    fabric: MemoryFabricSpec,
) -> MemoryBackbonePorts:
    groups = math.ceil(fabric.bank_count / fabric.banks_per_row)
    selector_base_x = origin.x - (fabric.bank.words * 2 + 420)
    z_terminal = origin.z - 96
    row_spine_y = origin.y + 108

    row_select = tuple(
        Vec3(selector_base_x + row * 2, row_spine_y, z_terminal)
        for row in range(fabric.bank.words)
    )

    bank_select = []
    for bank in range(fabric.bank_count):
        bo = _bank_origin(origin, fabric, bank)
        bank_select.append(Vec3(bo.x - 170, origin.y + 112, bo.z - 48))

    write_enable = Vec3(selector_base_x - 48, origin.y + 114, z_terminal)
    read_enable = Vec3(selector_base_x - 56, origin.y + 116, z_terminal)

    write_data = tuple(
        Vec3(selector_base_x - 80, origin.y + bit, z_terminal)
        for bit in range(fabric.bank.word_bits)
    )
    selected_word = tuple(
        Vec3(selector_base_x - 88, origin.y + 40 + bit, z_terminal)
        for bit in range(fabric.bank.word_bits)
    )

    return MemoryBackbonePorts(
        tuple(bank_select),
        row_select,
        read_enable,
        write_enable,
        write_data,
        selected_word,
    )


def iter_memory_backbone(
    origin: Vec3,
    *,
    fabric: MemoryFabricSpec,
    component: str = "memory-backbone",
) -> Iterator[Placement]:
    """Wire one-hot controller signals to all regular memory banks.

    The backbone is deliberately large but avoids an N×M binary decoder:
      * one global 1,024-row selector bus,
      * one bank-select input/trunk per bank,
      * one read/write-enable trunk per bank,
      * 32 broadcast write-data signals,
      * 32 shared read-OR signals,
      * per-row 3-input gates:
          write = bank_select & row_select & write_commit
          read  = bank_select & row_select & read_enable
    """
    spec = fabric.bank
    ports = backbone_ports(origin, fabric)
    groups = math.ceil(fabric.bank_count / fabric.banks_per_row)
    selector_base_x = ports.row_select[0].x
    z_terminal = ports.row_select[0].z

    row_spine_y = origin.y + 108
    row_branch_y = origin.y + 110
    bank_y = origin.y + 112
    write_y = origin.y + 114
    read_y = origin.y + 116
    gate_y = origin.y + 100

    fabric_last_bank = _bank_origin(origin, fabric, fabric.bank_count - 1)
    last_group_start_z = fabric_last_bank.z
    fabric_max_x = (
        origin.x
        + min(fabric.banks_per_row, fabric.bank_count)
        * (spec.width + fabric.bank_gap_x)
    )

    # ----- Row selector spines and per-grid-row horizontal branches -----
    for row in range(spec.words):
        lane_x = selector_base_x + row * 2
        final_center = (
            last_group_start_z + 3 + row * spec.word_pitch
        )
        yield from _wire_z(
            lane_x,
            row_spine_y,
            z_terminal,
            final_center - 4,
            facing="south",
            component=component,
        )

        for group in range(groups):
            group_start = origin.z + group * (spec.depth + fabric.bank_gap_z)
            center = group_start + 3 + row * spec.word_pitch

            # Raise the selector by two blocks before the horizontal branch so
            # branches cross other selector spines without joining them.
            yield from _stair_z(
                Vec3(lane_x, row_spine_y, center - 4),
                Vec3(lane_x, row_branch_y, center),
                component=component,
            )
            yield from _wire_x(
                lane_x,
                fabric_max_x,
                row_branch_y,
                center,
                facing="east",
                component=component,
            )

    # ----- Global read/write enables; branch once per grid row -----
    for terminal, y, local_x_offset in (
        (ports.write_enable, write_y, -165),
        (ports.read_enable, read_y, -160),
    ):
        last_z = last_group_start_z + spec.depth
        yield from _wire_z(
            terminal.x,
            y,
            terminal.z,
            last_z,
            facing="south",
            component=component,
        )
        for group in range(groups):
            group_start = origin.z + group * (spec.depth + fabric.bank_gap_z)
            branch_z = group_start - 48
            yield from _wire_x(
                terminal.x,
                fabric_max_x,
                y,
                branch_z,
                facing="east",
                component=component,
            )

    # ----- 32-bit write broadcast buses (front of every bank group) -----
    for bit, terminal in enumerate(ports.write_data):
        y = origin.y + bit
        last_z = last_group_start_z - 32
        yield from _wire_z(
            terminal.x,
            y,
            terminal.z,
            last_z,
            facing="south",
            component=component,
        )
        for group in range(groups):
            group_start = origin.z + group * (spec.depth + fabric.bank_gap_z)
            branch_z = group_start - 32
            yield from _wire_x(
                terminal.x,
                fabric_max_x,
                y,
                branch_z,
                facing="east",
                component=component,
            )
            first_bank = group * fabric.banks_per_row
            last_bank = min(
                fabric.bank_count,
                first_bank + fabric.banks_per_row,
            )
            for bank in range(first_bank, last_bank):
                bo = _bank_origin(origin, fabric, bank)
                bp = bank_ports(bo, spec).data_in[bit]
                yield from _stair_z(
                    Vec3(bp.x, y, branch_z),
                    bp,
                    component=component,
                )

    # ----- 32-bit shared read buses (back of every bank group) -----
    for bit, terminal in enumerate(ports.selected_word):
        y = origin.y + 40 + bit
        final_z = last_group_start_z + spec.depth + 64
        # Selected signal travels back toward the controller terminal.
        yield from _wire_z(
            terminal.x,
            y,
            final_z,
            terminal.z,
            facing="north",
            component=component,
        )
        for group in range(groups):
            group_start = origin.z + group * (spec.depth + fabric.bank_gap_z)
            branch_z = group_start + spec.depth + 64
            yield from _wire_x(
                fabric_max_x,
                terminal.x,
                y,
                branch_z,
                facing="west",
                component=component,
            )
            first_bank = group * fabric.banks_per_row
            last_bank = min(
                fabric.bank_count,
                first_bank + fabric.banks_per_row,
            )
            for bank in range(first_bank, last_bank):
                bo = _bank_origin(origin, fabric, bank)
                bp = bank_ports(bo, spec).data_out[bit]
                yield from _stair_z(
                    bp,
                    Vec3(bp.x, y, branch_z),
                    component=component,
                )

    # ----- Per-bank selector/control trunks and row gates -----
    for bank in range(fabric.bank_count):
        bo = _bank_origin(origin, fabric, bank)
        bp = bank_ports(bo, spec)

        bank_x = bo.x - 170
        write_x = bo.x - 165
        read_x = bo.x - 160

        # Bank-select arrives directly from the SoC's one-hot bank output.
        bank_terminal = ports.bank_select[bank]
        yield from _wire_z(
            bank_x,
            bank_y,
            bank_terminal.z,
            bo.z + spec.depth,
            facing="south",
            component=component,
        )

        # Global write/read enable branches feed these local trunks.
        yield from _wire_z(
            write_x,
            write_y,
            bo.z - 48,
            bo.z + spec.depth,
            facing="south",
            component=component,
        )
        yield from _wire_z(
            read_x,
            read_y,
            bo.z - 48,
            bo.z + spec.depth,
            facing="south",
            component=component,
        )

        for row in range(spec.words):
            center = bo.z + 3 + row * spec.word_pitch
            write_z = center - 2
            read_z = center + 2

            # The global row branch is available at x=bo.x-180, y=row_branch_y.
            row_tap = Vec3(bo.x - 180, row_branch_y, center)
            yield from _wire_z(
                row_tap.x,
                row_branch_y,
                write_z,
                read_z,
                facing="south",
                component=component,
                stride=99,
            )

            # Bring row/bank/enable signals to the common gate plane.
            write_row = Vec3(bo.x - 160, gate_y, write_z)
            write_bank = Vec3(bo.x - 148, gate_y, write_z)
            write_en = Vec3(bo.x - 134, gate_y, write_z)

            yield from _stair_x(
                Vec3(row_tap.x, row_branch_y, write_z),
                write_row,
                component=component,
            )
            yield from _stair_x(
                Vec3(bank_x, bank_y, write_z),
                write_bank,
                component=component,
            )
            yield from _stair_x(
                Vec3(write_x, write_y, write_z),
                write_en,
                component=component,
            )
            yield from _and3_to_target(
                write_row,
                write_bank,
                write_en,
                bp.write_select[row],
                output_side=-1,
                component=component,
            )

            read_row = Vec3(bo.x - 160, gate_y, read_z)
            read_bank = Vec3(bo.x - 148, gate_y, read_z)
            read_en = Vec3(bo.x - 132, gate_y, read_z)

            yield from _stair_x(
                Vec3(row_tap.x, row_branch_y, read_z),
                read_row,
                component=component,
            )
            yield from _stair_x(
                Vec3(bank_x, bank_y, read_z),
                read_bank,
                component=component,
            )
            yield from _stair_x(
                Vec3(read_x, read_y, read_z),
                read_en,
                component=component,
            )
            yield from _and3_to_target(
                read_row,
                read_bank,
                read_en,
                bp.read_select[row],
                output_side=1,
                component=component,
            )
