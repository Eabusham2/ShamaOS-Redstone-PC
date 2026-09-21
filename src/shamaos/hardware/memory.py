from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Iterator

from ..model import BlockState, Placement, Vec3


SUPPORT = BlockState.of("minecraft:smooth_stone")


def redstone_wire(
    *,
    north: str = "side",
    east: str = "side",
    south: str = "side",
    west: str = "side",
    power: int = 0,
) -> BlockState:
    for value in (north, east, south, west):
        if value not in {"none", "side", "up"}:
            raise ValueError(f"invalid redstone-wire connection {value!r}")
    if not 0 <= power <= 15:
        raise ValueError("redstone-wire power must be 0..15")
    return BlockState.of(
        "minecraft:redstone_wire",
        north=north,
        east=east,
        south=south,
        west=west,
        power=str(power),
    )


# Horizontal buses/circuit junctions default to a valid four-way wire state.
# Empty directions do not create electrical power paths without a neighbor,
# while existing adjacent dust/repeater terminals connect deterministically.
DUST = redstone_wire()
TORCH = BlockState.of("minecraft:redstone_torch", lit="true")
WALL_TORCH_E = BlockState.of("minecraft:redstone_wall_torch", facing="east", lit="true")


def repeater(
    facing: str,
    *,
    powered: bool = False,
    locked: bool = False,
    delay: int = 1,
) -> BlockState:
    if not 1 <= delay <= 4:
        raise ValueError("repeater delay must be 1..4")
    return BlockState.of(
        "minecraft:repeater",
        delay=str(delay),
        facing=facing,
        powered=str(powered).lower(),
        locked=str(locked).lower(),
    )


def comparator(facing: str, *, powered: bool = False) -> BlockState:
    return BlockState.of(
        "minecraft:comparator",
        facing=facing,
        mode="subtract",
        powered=str(powered).lower(),
    )


@dataclass(frozen=True)
class MemoryBankSpec:
    words: int = 1024
    word_bits: int = 32
    bit_pitch: int = 8
    word_pitch: int = 6
    data_y: int = 0
    cell_y: int = 3
    read_y: int = 6

    @property
    def bytes(self) -> int:
        return self.words * self.word_bits // 8

    @property
    def width(self) -> int:
        return self.word_bits * self.bit_pitch + 8

    @property
    def depth(self) -> int:
        return self.words * self.word_pitch + 8

    @property
    def height(self) -> int:
        return self.read_y + 1


@dataclass(frozen=True)
class MemoryBankPorts:
    data_in: tuple[Vec3, ...]
    data_out: tuple[Vec3, ...]
    write_select: tuple[Vec3, ...]
    read_select: tuple[Vec3, ...]


def _same_polarity_riser(
    x: int,
    y: int,
    z: int,
    *,
    component: str,
) -> Iterator[Placement]:
    """Raise a digital signal three blocks using two torch inversions.

    Input is expected to power the lower block from the west.  The torch pair
    restores polarity at y+3.
    """
    yield Placement(Vec3(x, y, z), SUPPORT, component)
    yield Placement(Vec3(x, y + 1, z), TORCH, component)
    yield Placement(Vec3(x, y + 2, z), SUPPORT, component)
    yield Placement(Vec3(x, y + 3, z), TORCH, component)


def _bus_z(
    x: int,
    y: int,
    z0: int,
    z1: int,
    *,
    facing: str,
    component: str,
    repeater_stride: int = 12,
) -> Iterator[Placement]:
    if z1 < z0:
        z0, z1 = z1, z0
    for idx, z in enumerate(range(z0, z1 + 1)):
        yield Placement(Vec3(x, y - 1, z), SUPPORT, component)
        if idx and idx % repeater_stride == 0:
            yield Placement(Vec3(x, y, z), repeater(facing), component)
        else:
            yield Placement(Vec3(x, y, z), DUST, component)


def _bus_x(
    x0: int,
    x1: int,
    y: int,
    z: int,
    *,
    facing: str,
    component: str,
    repeater_stride: int = 12,
) -> Iterator[Placement]:
    if x1 < x0:
        x0, x1 = x1, x0
    for idx, x in enumerate(range(x0, x1 + 1)):
        yield Placement(Vec3(x, y - 1, z), SUPPORT, component)
        if idx and idx % repeater_stride == 0:
            yield Placement(Vec3(x, y, z), repeater(facing), component)
        else:
            yield Placement(Vec3(x, y, z), DUST, component)


def _initial_bit(data: bytes | None, word: int, bit: int, word_bits: int) -> int:
    if not data:
        return 0
    absolute_bit = word * word_bits + bit
    byte_index = absolute_bit // 8
    if byte_index >= len(data):
        return 0
    return (data[byte_index] >> (absolute_bit & 7)) & 1


def bank_ports(origin: Vec3, spec: MemoryBankSpec) -> MemoryBankPorts:
    data_in = []
    data_out = []
    for bit in range(spec.word_bits):
        bx = origin.x + bit * spec.bit_pitch
        data_in.append(Vec3(bx, origin.y + spec.data_y, origin.z - 1))
        data_out.append(Vec3(bx + 6, origin.y + spec.read_y, origin.z + spec.words * spec.word_pitch + 4))

    write_select = []
    read_select = []
    for word in range(spec.words):
        cz = origin.z + 3 + word * spec.word_pitch
        write_select.append(Vec3(origin.x - 6, origin.y + spec.cell_y, cz - 2))
        read_select.append(Vec3(origin.x - 6, origin.y + spec.cell_y, cz + 2))

    return MemoryBankPorts(
        tuple(data_in), tuple(data_out), tuple(write_select), tuple(read_select)
    )


def iter_memory_bank(
    origin: Vec3,
    *,
    spec: MemoryBankSpec = MemoryBankSpec(),
    initial: bytes | None = None,
    component: str = "memory-bank",
) -> Iterator[Placement]:
    """Emit a regular writable/readable locked-repeater memory bank.

    Per stored bit:
      * a broadcast data column feeds a two-torch non-inverting riser;
      * an east-facing repeater is the actual locked storage element;
      * the row write line powers a side repeater, locking all non-written rows;
      * a subtract-mode comparator gates reads using an independently inverted
        row-select mask;
      * the selected value rises to a northbound read bus.

    External decode/control logic drives active-high write_select/read_select
    terminals.  Local wall-torch inverters make storage locked/read-masked by
    default, which is important during boot and chunk loading.
    """
    if spec.words <= 0 or spec.word_bits <= 0 or spec.word_bits % 8:
        raise ValueError("memory bank geometry must be positive and byte aligned")
    if initial is not None and len(initial) > spec.bytes:
        raise ValueError("initial image is larger than memory bank")

    oy = origin.y
    z_bus_start = origin.z
    z_bus_end = origin.z + spec.words * spec.word_pitch + 4
    x_end = origin.x + (spec.word_bits - 1) * spec.bit_pitch + 6

    # Long data input and read output trunks.
    for bit in range(spec.word_bits):
        bx = origin.x + bit * spec.bit_pitch
        yield from _bus_z(
            bx,
            oy + spec.data_y,
            z_bus_start - 2,
            z_bus_end,
            facing="south",
            component=component,
        )
        yield from _bus_z(
            bx + 6,
            oy + spec.read_y,
            z_bus_start - 2,
            z_bus_end,
            facing="north",
            component=component,
        )

    for word in range(spec.words):
        cz = origin.z + 3 + word * spec.word_pitch
        write_z = cz - 2
        read_z = cz + 2

        # Active-high external select -> torch inversion -> normally-high
        # lock/mask lines.
        for select_z in (write_z, read_z):
            yield Placement(Vec3(origin.x - 5, oy + spec.cell_y, select_z), SUPPORT, component)
            yield Placement(Vec3(origin.x - 4, oy + spec.cell_y, select_z), WALL_TORCH_E, component)
            yield from _bus_x(
                origin.x - 3,
                x_end,
                oy + spec.cell_y,
                select_z,
                facing="east",
                component=component,
            )

        for bit in range(spec.word_bits):
            bx = origin.x + bit * spec.bit_pitch
            bit_value = bool(_initial_bit(initial, word, bit, spec.word_bits))

            # Data column -> same-polarity vertical riser.
            yield from _same_polarity_riser(
                bx + 1, oy + spec.data_y, cz, component=component
            )

            # Storage repeater.  It is initialized locked and with the requested
            # powered state so flash/RAM images can be preloaded directly.
            yield Placement(Vec3(bx + 2, oy + spec.cell_y - 1, cz), SUPPORT, component)
            yield Placement(
                Vec3(bx + 2, oy + spec.cell_y, cz),
                repeater("east", powered=bit_value, locked=True),
                component,
            )

            # Side lock repeater: write mask line -> south into storage.
            yield Placement(Vec3(bx + 2, oy + spec.cell_y - 1, cz - 1), SUPPORT, component)
            yield Placement(
                Vec3(bx + 2, oy + spec.cell_y, cz - 1),
                repeater("south", powered=True),
                component,
            )

            # Subtract comparator gates read value.  Side input is normally 15
            # (masked); selected row drops it to zero.
            yield Placement(Vec3(bx + 3, oy + spec.cell_y - 1, cz), SUPPORT, component)
            yield Placement(
                Vec3(bx + 3, oy + spec.cell_y, cz),
                comparator("east", powered=bit_value),
                component,
            )
            yield Placement(Vec3(bx + 3, oy + spec.cell_y - 1, cz + 1), SUPPORT, component)
            yield Placement(
                Vec3(bx + 3, oy + spec.cell_y, cz + 1),
                repeater("north", powered=True),
                component,
            )

            # Comparator output -> non-inverting riser -> read trunk.
            yield from _same_polarity_riser(
                bx + 5, oy + spec.cell_y, cz, component=component
            )

    # Port marker supports (actual redstone terminal dust is supplied by buses).
    ports = bank_ports(origin, spec)
    for p in (*ports.write_select, *ports.read_select):
        yield Placement(Vec3(p.x, p.y - 1, p.z), SUPPORT, component)
        yield Placement(p, DUST, component)


@dataclass(frozen=True)
class MemoryFabricSpec:
    total_bytes: int
    bank: MemoryBankSpec = MemoryBankSpec()
    banks_per_row: int = 16
    bank_gap_x: int = 192
    bank_gap_z: int = 128

    @property
    def bank_count(self) -> int:
        if self.total_bytes % self.bank.bytes:
            raise ValueError("memory size must be an integer number of banks")
        return self.total_bytes // self.bank.bytes


def iter_memory_fabric(
    origin: Vec3,
    *,
    fabric: MemoryFabricSpec,
    initial: bytes | None = None,
    component: str = "memory",
) -> Iterator[Placement]:
    count = fabric.bank_count
    if initial is not None and len(initial) > fabric.total_bytes:
        raise ValueError("initial image exceeds fabric capacity")

    for bank_index in range(count):
        grid_x = bank_index % fabric.banks_per_row
        grid_z = bank_index // fabric.banks_per_row
        bank_origin = Vec3(
            origin.x + grid_x * (fabric.bank.width + fabric.bank_gap_x),
            origin.y,
            origin.z + grid_z * (fabric.bank.depth + fabric.bank_gap_z),
        )
        start = bank_index * fabric.bank.bytes
        bank_data = None if initial is None else initial[start:start + fabric.bank.bytes]
        yield from iter_memory_bank(
            bank_origin,
            spec=fabric.bank,
            initial=bank_data,
            component=f"{component}-bank-{bank_index:03d}",
        )
