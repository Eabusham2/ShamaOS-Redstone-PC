from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Iterator

from ..model import BlockState, Box, Placement, Vec3
from .routing import iter_stair
from .memory import (
    DUST,
    SUPPORT,
    MemoryFabricSpec,
    MemoryBankSpec,
    bank_ports,
    iter_memory_bank,
    repeater,
)
from .memory_streaming import iter_memory_bank_ordered


BINARY_ROW_X_OFFSET = -138


@dataclass(frozen=True)
class StackedMemoryTileSpec:
    """Complete banks sharing local selectors and an isolated read-return bus."""

    bank: MemoryBankSpec = MemoryBankSpec()
    banks: int = 2
    layer_pitch: int = 16
    tail_words: int | None = None

    def __post_init__(self) -> None:
        if not 1 <= self.banks <= 32:
            raise ValueError("a memory tile must contain 1..32 complete banks")
        if self.layer_pitch < 12 or self.layer_pitch % 4:
            raise ValueError("stacked layers need at least 12 blocks and four-block torch polarity alignment")
        if self.tail_words is not None and not 1 <= self.tail_words <= self.bank.words:
            raise ValueError("a partial last bank must contain 1..nominal rows")

    @property
    def bytes(self) -> int:
        return (self.banks - 1) * self.bank.bytes + self.bank_spec(self.banks - 1).bytes

    @property
    def words(self) -> int:
        return self.bank.words if self.banks > 1 or self.tail_words is None else self.tail_words

    @property
    def common_bank(self) -> MemoryBankSpec:
        return self.bank if self.words == self.bank.words else replace(self.bank, words=self.words)

    def bank_spec(self, index: int) -> MemoryBankSpec:
        if not 0 <= index < self.banks:
            raise IndexError("stacked bank index out of range")
        return replace(self.bank, words=self.tail_words) if index == self.banks - 1 and self.tail_words is not None else self.bank


@dataclass(frozen=True)
class StackedMemoryTilePorts:
    bank_select: tuple[Vec3, ...]
    row_select: tuple[Vec3, ...]
    read_enable: Vec3
    write_enable: Vec3
    write_data: tuple[Vec3, ...]
    selected_word: tuple[Vec3, ...]
    bank_read_data: tuple[tuple[Vec3, ...], ...]
    row_address: tuple[Vec3, ...] = ()


def stacked_bank_origin(origin: Vec3, tile: StackedMemoryTileSpec, index: int) -> Vec3:
    if not 0 <= index < tile.banks:
        raise IndexError("stacked bank index out of range")
    return origin.offset(dy=index * tile.layer_pitch)


def stacked_tile_ports(origin: Vec3, tile: StackedMemoryTileSpec) -> StackedMemoryTilePorts:
    lower_read = bank_ports(origin, tile.common_bank).data_out
    return StackedMemoryTilePorts(
        tuple(stacked_bank_origin(origin, tile, bank).offset(-125, 0, -16) for bank in range(tile.banks)),
        tuple(origin.offset((-56 if row % 2 == 0 else -88) - 1, 0, 3 + row * tile.bank.word_pitch) for row in range(tile.words)),
        origin.offset(-115, 4, -4), origin.offset(-115, 0, -10),
        tuple(origin.offset(bit * tile.bank.bit_pitch - 4, tile.bank.data_y - 3, -4) for bit in range(tile.bank.word_bits)),
        tuple(point.offset(2, 0, (tile.banks - 1) * _return_span(tile.layer_pitch) + 2) for point in lower_read),
        tuple(bank_ports(stacked_bank_origin(origin, tile, bank), tile.bank_spec(bank)).data_out for bank in range(tile.banks)),
    )


def _return_span(layer_pitch: int) -> int:
    return 2 + layer_pitch + 2 * (math.ceil(layer_pitch / 10) - 1)


@dataclass(frozen=True)
class StackedMemoryFabricSpec(MemoryFabricSpec):
    """Bank-major physical capacity, packed into independently selected tiles.

    A bank's logical index is unchanged: ``group, layer = divmod(index,
    layers)``. The final group contains only its actual complete banks.
    ``banks_per_row`` counts horizontal tile groups for this fabric.
    """

    layers: int = 32
    layer_pitch: int = 12
    binary_rows: bool = False
    decoder_x_offset: int = BINARY_ROW_X_OFFSET

    def __post_init__(self) -> None:
        StackedMemoryTileSpec(self.bank, self.layers, self.layer_pitch)
        if self.total_bytes <= 0 or self.banks_per_row <= 0:
            raise ValueError("a memory fabric needs positive capacity and columns")
        if self.bank_gap_x < 128 or self.bank_gap_z < 32:
            raise ValueError("stacked tile gaps must reserve local decoder and return routing")
        if self.binary_rows and self.bank_gap_x < 160:
            raise ValueError("binary row decoding needs at least 160 blocks of bank X spacing")
        _ = self.bank_count

    @property
    def group_count(self) -> int:
        return math.ceil(self.bank_count / self.layers)

    @property
    def group_stride_x(self) -> int:
        return self.bank.width + self.bank_gap_x

    @property
    def group_stride_z(self) -> int:
        # Every group reserves the tallest tile's descending return tail.
        # Its final landing must precede the next group's front controls.
        return self.bank.depth + (min(self.layers, self.bank_count) - 1) * _return_span(self.layer_pitch) + self.bank_gap_z

    def tile_spec(self, group: int) -> StackedMemoryTileSpec:
        if not 0 <= group < self.group_count:
            raise IndexError("memory tile group index out of range")
        banks = min(self.layers, self.bank_count - group * self.layers)
        last_words = self.bank_spec(group * self.layers + banks - 1).words
        return StackedMemoryTileSpec(self.bank, banks, self.layer_pitch,
                                     None if last_words == self.bank.words else last_words)

    def group_origin(self, origin: Vec3, group: int) -> Vec3:
        self.tile_spec(group)
        return origin.offset((group % self.banks_per_row) * self.group_stride_x,
                             0, (group // self.banks_per_row) * self.group_stride_z)

    def bank_origin(self, origin: Vec3, bank: int) -> Vec3:
        if not 0 <= bank < self.bank_count:
            raise IndexError("bank index outside unchanged physical capacity")
        group, layer = divmod(bank, self.layers)
        return self.group_origin(origin, group).offset(dy=layer * self.layer_pitch)

    def bounds(self, origin: Vec3, *, local_controls: bool = True) -> Box:
        boxes = [stacked_tile_bounds(self.group_origin(origin, group), self.tile_spec(group), local_controls=local_controls)
                 for group in range(self.group_count)]
        if local_controls and self.binary_rows:
            from .binary_row_decoder import binary_row_decoder_bounds
            boxes.extend(binary_row_decoder_bounds(self.group_origin(origin, group), self.tile_spec(group),
                                                    decoder_x=self.group_origin(origin, group).x + self.decoder_x_offset)
                         for group in range(self.group_count))
        return Box(Vec3(min(box.min.x for box in boxes), min(box.min.y for box in boxes), min(box.min.z for box in boxes)),
                   Vec3(max(box.max.x for box in boxes), max(box.max.y for box in boxes), max(box.max.z for box in boxes)))

    def collision_bounds(self, origin: Vec3) -> tuple[Box, ...]:
        """Conservative occupied volumes, preserving empty decoder headroom."""
        from .binary_row_decoder import binary_row_collision_bounds
        boxes = []
        for group in range(self.group_count):
            base, tile = self.group_origin(origin, group), self.tile_spec(group)
            boxes.append(stacked_tile_bounds(base, tile))
            if self.binary_rows:
                boxes.extend(binary_row_collision_bounds(base, tile, decoder_x=base.x + self.decoder_x_offset))
        return tuple(boxes)


def stacked_tile_bounds(origin: Vec3, tile: StackedMemoryTileSpec, *, local_controls: bool = True) -> Box:
    """Inclusive bounds of literal storage, local controls and read return."""
    outputs = bank_ports(origin, tile.common_bank).data_out
    minimum = origin.offset(-125 if local_controls else -6, -4 if local_controls else -1, -20 if local_controls else -2)
    maximum = Vec3(max(point.x for point in outputs) + (2 if local_controls else 0),
                   origin.y + (tile.banks - 1) * tile.layer_pitch + (7 if local_controls else tile.bank.read_y),
                   max(origin.z + tile.common_bank.depth,
                       max(point.z for point in outputs) + (tile.banks - 1) * _return_span(tile.layer_pitch) + 2)
                   if local_controls else max(point.z for point in outputs))
    if minimum.y < -64 or maximum.y > 319:
        raise ValueError("stacked memory tile exceeds Java 1.20.4 build height")
    return Box(minimum, maximum)


def iter_stacked_tile_terminal_keepouts(origin: Vec3, tile: StackedMemoryTileSpec) -> Iterator[Placement]:
    """Exact small neighborhoods for routing access, linear in terminal count.

    These are routing obstacles, not a replacement for the tile envelope or
    its literal placements. West approaches clear row/bank/enable targets;
    data enters from the north and shared read data leaves to the south.
    Only the bottom tap neighborhood of tall broadcast towers is needed.
    """
    pins = stacked_tile_ports(origin, tile)
    component = "stacked-terminal-keepout"
    for pin in pins.bank_select:
        yield from _tower(pin.offset(dx=1), 7, (3, 7), component)
    for pin in pins.row_select:
        yield from _tower(pin.offset(dx=1), 3, (), component)
        point = pin.offset(1, 3, -1)
        yield Placement(point.offset(dy=-1), SUPPORT, component)
        yield Placement(point, repeater("north"), component)
    for pin in (*pins.write_data, pins.write_enable, pins.read_enable):
        yield from _tower(pin.offset(dx=1), 3, (3,), component)
    # The final return descent is a translated two-bank suffix. Its very
    # last lane cell is always refreshed, independently of the earlier tail.
    # Restrict to the landing neighborhood where all block states coincide.
    suffix_banks = min(2, tile.banks)
    suffix_origin = origin.offset(dz=max(0, tile.banks - 2) * _return_span(tile.layer_pitch))
    suffix = StackedMemoryTileSpec(tile.bank, suffix_banks, tile.layer_pitch, tile.tail_words)
    near = {pin.offset(dx, dy, dz) for pin in pins.selected_word
            for dx in range(-2, 3) for dy in range(-1, 3) for dz in range(-1, 2)}
    for placement in _stacked_read_return(suffix_origin, suffix, component):
        if placement.pos in near:
            yield placement


def _stacked_read_return(origin: Vec3, tile: StackedMemoryTileSpec, component: str) -> Iterator[Placement]:
    """An isolated downward OR cascade, one narrow lane per data bit.

    The bank's local output follows a parallel Z lane two X blocks from the
    descending cascade. It joins across X only at the landing, so descending
    stair supports cannot overwrite the lower bank's horizontal conductor.
    Repeaters isolate every source and refresh every inter-layer descent.
    """
    span = _return_span(tile.layer_pitch)
    for bank in range(tile.banks - 1, -1, -1):
        bo = stacked_bank_origin(origin, tile, bank)
        distance = (tile.banks - 1 - bank) * span
        actual_sources = bank_ports(bo, tile.bank_spec(bank)).data_out
        common_sources = bank_ports(bo, tile.common_bank).data_out
        for actual, source in zip(actual_sources, common_sources):
            # A short final bank ends earlier in Z. Extend its isolated
            # output to the same return plane; no nonexistent cells appear.
            for step, z in enumerate(range(actual.z + 1, source.z + 1), start=1):
                point = Vec3(actual.x, actual.y, z)
                refresh = z != source.z and (step == 1 or step % 10 == 0 or z == source.z - 1)
                yield Placement(point.offset(dy=-1), SUPPORT, component)
                yield Placement(point, repeater("south") if refresh else DUST, component)
            node = source.offset(2, 0, distance)
            if distance == 0:
                yield Placement(source.offset(1, -1, 0), SUPPORT, component)
                yield Placement(source.offset(1, 0, 0), repeater("east"), component)
                yield Placement(node.offset(dy=-1), SUPPORT, component)
                yield Placement(node, DUST, component)
            else:
                for step in range(1, distance + 1):
                    point = source.offset(dz=step)
                    refresh = step < distance and (step == 1 or step % 10 == 0 or step == distance - 1)
                    yield Placement(point.offset(dy=-1), SUPPORT, component)
                    yield Placement(point, repeater("south") if refresh else DUST, component)
                bridge = source.offset(1, 0, distance)
                yield Placement(bridge.offset(dy=-1), SUPPORT, component)
                yield Placement(bridge, DUST, component)
                # The preceding stair owns the node's north=up block state.
            if bank:
                yield Placement(node.offset(0, -1, 1), SUPPORT, component)
                yield Placement(node.offset(dz=1), repeater("south"), component)
                yield from iter_stair(node.offset(dz=2), node.offset(0, -tile.layer_pitch, span), axis="z", signal_forward=True, component=component)
    # Export a plain, isolated terminal after the final descending landing.
    # Its north=up connection remains owned by the stair; the external router
    # can share this flat terminal without rewriting that required state.
    for source in bank_ports(origin, tile.common_bank).data_out:
        landing = source.offset(2, 0, (tile.banks - 1) * span)
        for dz, state in ((1, repeater("south")), (2, DUST)):
            point = landing.offset(dz=dz)
            yield Placement(point.offset(dy=-1), SUPPORT, component)
            yield Placement(point, state, component)


def _tower(base: Vec3, top: int, buffered_taps: tuple[int, ...], component: str) -> Iterator[Placement]:
    """Two inversions per four Y blocks; taps at 3 mod 4 preserve polarity."""
    yield Placement(base.offset(-1, -1, 0), SUPPORT, component)
    yield Placement(base.offset(-1, 0, 0), DUST, component)
    yield Placement(base, BlockState.of("minecraft:target", power="0"), component)
    for dy in range(1, top + 1):
        state = SUPPORT if dy % 2 == 0 else BlockState.of("minecraft:redstone_torch", lit="true" if dy % 4 == 1 else "false")
        yield Placement(base.offset(dy=dy), state, component)
    for dy in buffered_taps:
        if dy % 4 != 3:
            raise ValueError("tower tap would invert its signal")
        for dx, state in ((1, DUST), (2, repeater("east")), (3, DUST)):
            yield Placement(base.offset(dx, dy - 1, 0), SUPPORT, component)
            yield Placement(base.offset(dx, dy, 0), state, component)


def _local_x(start: Vec3, end_x: int, component: str) -> Iterator[Placement]:
    direction = 1 if end_x >= start.x else -1
    for index, x in enumerate(range(start.x, end_x + direction, direction)):
        point = Vec3(x, start.y, start.z)
        state = repeater("east" if direction > 0 else "west") if index and index % 10 == 0 and x != end_x else DUST
        yield Placement(point.offset(dy=-1), SUPPORT, component)
        yield Placement(point, state, component)


def _inactive_and(origin: Vec3, component: str) -> Iterator[Placement]:
    """AND gate initialized for A=B=0, avoiding a boot-time write pulse."""
    from .logic import emit_and
    for placement in emit_and(Vec3(0, 0, 0), component):
        properties = dict(placement.block.properties)
        x, z = placement.pos.x, placement.pos.z
        if placement.block.name == "minecraft:redstone_wire":
            power = 0
            if z == -4 and 0 <= x <= 8:
                power = 15 - min(x, 8 - x)
            elif z == -4 and 10 <= x <= 16:
                power = 25 - x
            elif x == 16 and -3 <= z <= 0:
                power = 5 - z
            elif x == 17 and z == 0:
                power = 4
            properties["power"] = str(power)
        elif placement.block.name == "minecraft:repeater" and (x, z) in ((9, -4), (18, 0)):
            properties["powered"] = "true"
        elif placement.block.name == "minecraft:redstone_wall_torch" and (x, z) == (20, 0):
            properties["lit"] = "false"
        yield Placement(origin.offset(placement.pos.x, placement.pos.y, placement.pos.z), BlockState.of(placement.block.name, **properties), component)


def _mirrored_and(origin: Vec3, component: str) -> Iterator[Placement]:
    for placement in _inactive_and(Vec3(0, 0, 0), component):
        properties = dict(placement.block.properties)
        if "facing" in properties:
            properties["facing"] = {"north": "south", "south": "north"}.get(properties["facing"], properties["facing"])
        if placement.block.name == "minecraft:redstone_wire":
            properties["north"], properties["south"] = properties["south"], properties["north"]
        yield Placement(origin.offset(placement.pos.x, placement.pos.y, -placement.pos.z), BlockState.of(placement.block.name, **properties), component)


def iter_stacked_memory_tile(
    origin: Vec3,
    *,
    tile: StackedMemoryTileSpec,
    initial: bytes | None = None,
    component: str = "stacked-memory-tile",
    interleave_layers: bool = True,
    include_storage: bool = True,
    include_backbone: bool = True,
    include_read_return: bool = True,
) -> Iterator[Placement]:
    """Emit capacity-preserving layered storage and a low-height local decoder.

    Shared write-data/row/global-enable signals use vertical buffered torch
    columns. Bank selection is local; two gate planes at Y+3/Y+7 qualify each
    row, instead of the legacy Y+116 control canopy. A descending, isolated
    OR cascade merges selected read data onto one word-wide bottom terminal.
    """
    stacked_tile_bounds(origin, tile)
    if initial is not None and len(initial) > tile.bytes:
        raise ValueError("initial image exceeds the complete stacked bank capacity")
    spec = tile.bank
    generators = [iter_memory_bank_ordered(
        stacked_bank_origin(origin, tile, bank), spec=tile.bank_spec(bank),
        initial=None if initial is None else initial[bank * spec.bytes:bank * spec.bytes + tile.bank_spec(bank).bytes],
        component=f"{component}:bank-{bank}",
    ) for bank in range(tile.banks)] if include_storage else []
    if interleave_layers:
        # Same X/Z cells across layers stay in one native chunk cache entry.
        # All literal placements are still emitted and independently read back.
        while generators:
            remaining = []
            for generator in generators:
                try:
                    yield next(generator)
                    remaining.append(generator)
                except StopIteration:
                    pass
            generators = remaining
    else:
        for generator in generators:
            yield from generator

    if not include_backbone:
        return

    taps = tuple(3 + bank * tile.layer_pitch for bank in range(tile.banks))
    for bit in range(spec.word_bits):
        base = origin.offset(bit * spec.bit_pitch - 3, spec.data_y - 3, -4)
        yield from _tower(base, taps[-1], taps, component)
        for bank in range(tile.banks):
            bo = stacked_bank_origin(origin, tile, bank)
            yield from _wire_z(bo.x + bit * spec.bit_pitch, bo.y + spec.data_y, bo.z - 4, bo.z - 1, facing="south", component=component)

    # Common row towers directly drive isolated repeaters into the two gate
    # planes. Alternating gate columns leave room for adjacent row towers.
    for row in range(tile.words):
        center = origin.z + 3 + row * spec.word_pitch
        gate_x = origin.x - (64 if row % 2 == 0 else 96)
        base = Vec3(gate_x + 8, origin.y, center)
        last_bank = max(bank for bank in range(tile.banks) if row < tile.bank_spec(bank).words)
        yield from _tower(base, 7 + last_bank * tile.layer_pitch, (), component)

    # Shared commit/read-enable spines, tapped once for each complete bank.
    yield from _tower(origin.offset(-114, 0, -10), taps[-1], taps, component)
    yield from _tower(origin.offset(-114, 4, -4), taps[-1], taps, component)
    for bank in range(tile.banks):
        bo = stacked_bank_origin(origin, tile, bank)
        bank_spec = tile.bank_spec(bank)
        bp = bank_ports(bo, bank_spec)
        yield from _tower(bo.offset(-124, 0, -16), 7, (3, 7), component)
        for gate_y, global_z in ((3, -10), (7, -4)):
            yield from _inactive_and(bo.offset(-119, gate_y - 1, -16), component)
            yield from _local_x(bo.offset(-121, gate_y, -16), bo.x - 119, component)
            yield from _wire_z(bo.x - 111, bo.y + gate_y, bo.z + global_z, bo.z - 16, facing="north", component=component)
            yield from _wire_z(bo.x - 98, bo.y + gate_y, bo.z - 16, bo.z + bank_spec.depth, facing="south", component=component)

        for row in range(bank_spec.words):
            center = bo.z + 3 + row * spec.word_pitch
            gate_x = bo.x - (64 if row % 2 == 0 else 96)
            for read, y, z, terminal in (
                (False, bo.y + 3, center - 2, bp.write_select[row]),
                (True, bo.y + 7, center + 2, bp.read_select[row]),
            ):
                emitter = _mirrored_and if read else _inactive_and
                yield from emitter(Vec3(gate_x, y - 1, z), component)
                # A side tap may be ten blocks from the trunk's previous
                # repeater. Refresh immediately before the long branch.
                yield Placement(Vec3(bo.x - 97, y - 1, z), SUPPORT, component)
                yield Placement(Vec3(bo.x - 97, y, z), repeater("east"), component)
                if gate_x - 2 >= bo.x - 96:
                    yield from _local_x(Vec3(bo.x - 96, y, z), gate_x - 2, component)
                yield Placement(Vec3(gate_x - 1, y - 1, z), SUPPORT, component)
                yield Placement(Vec3(gate_x - 1, y, z), repeater("east"), component)
                row_driver_z = center + 1 if read else center - 1
                yield Placement(Vec3(gate_x + 8, y - 1, row_driver_z), SUPPORT, component)
                yield Placement(Vec3(gate_x + 8, y, row_driver_z), repeater("south" if read else "north"), component)
                gate_out = Vec3(gate_x + 21, y, z)
                if read:
                    stair_start = Vec3(bo.x - 39, y, z)
                    yield from _local_x(gate_out, stair_start.x, component)
                    yield from iter_stair(stair_start, terminal, axis="x", signal_forward=True, component=component)
                else:
                    yield from _local_x(gate_out, terminal.x, component)
    if include_read_return:
        yield from _stacked_read_return(origin, tile, component)


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
    if isinstance(fabric, StackedMemoryFabricSpec):
        return fabric.bank_origin(origin, bank_index)
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
    yield from iter_stair(gate_out, stair_end, axis="x", signal_forward=True, component=component)
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
    if isinstance(fabric, StackedMemoryFabricSpec):
        if fabric.binary_rows:
            raise ValueError("binary row-address fabrics require fabric_tile_ports")
        if fabric.group_count != 1:
            raise ValueError("a multi-tile fabric has multiple row/data/return terminals; use fabric_tile_ports")
        tile = stacked_tile_ports(origin, fabric.tile_spec(0))
        return MemoryBackbonePorts(tile.bank_select, tile.row_select, tile.read_enable,
                                   tile.write_enable, tile.write_data, tile.selected_word)
    groups = math.ceil(fabric.bank_count / fabric.banks_per_row)
    selector_base_x = origin.x - (fabric.bank.words * 2 + 420)
    z_terminal = origin.z - 96
    row_spine_y = origin.y + 108

    row_select = tuple(
        Vec3(selector_base_x + row * 2, row_spine_y, z_terminal)
        for row in range(fabric.bank.words)
    )

    bank_selector_base_x = selector_base_x - fabric.bank_count * 2 - 128
    bank_select = tuple(
        Vec3(bank_selector_base_x + bank * 2, origin.y + 106, z_terminal)
        for bank in range(fabric.bank_count)
    )

    write_enable = Vec3(bank_selector_base_x - 48, origin.y + 114, z_terminal)
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
        bank_select,
        row_select,
        read_enable,
        write_enable,
        write_data,
        selected_word,
    )


def fabric_tile_ports(origin: Vec3, fabric: MemoryFabricSpec) -> tuple[MemoryBackbonePorts | StackedMemoryTilePorts, ...]:
    """Terminals in logical bank order, with one broadcast/return set per tile."""
    if isinstance(fabric, StackedMemoryFabricSpec):
        tiles = []
        for group in range(fabric.group_count):
            tile_origin, tile = fabric.group_origin(origin, group), fabric.tile_spec(group)
            ports = stacked_tile_ports(tile_origin, tile)
            if fabric.binary_rows:
                from .binary_row_decoder import binary_row_address_ports
                ports = replace(ports, row_select=(), row_address=binary_row_address_ports(
                    tile_origin, tile, decoder_x=tile_origin.x + fabric.decoder_x_offset))
            tiles.append(ports)
        return tuple(tiles)
    return (backbone_ports(origin, fabric),)


def iter_memory_backbone(
    origin: Vec3,
    *,
    fabric: MemoryFabricSpec,
    component: str = "memory-backbone",
) -> Iterator[Placement]:
    """Wire controller signals to complete regular memory banks.

    Stacked fabrics use local binary row decode when configured. The legacy
    flat path retains its one-hot interface:
      * one global 1,024-row selector bus,
      * one bank-select input/trunk per bank,
      * one read/write-enable trunk per bank,
      * 32 broadcast write-data signals,
      * 32 shared read-OR signals,
      * per-row 3-input gates:
          write = bank_select & row_select & write_commit
          read  = bank_select & row_select & read_enable
    """
    if isinstance(fabric, StackedMemoryFabricSpec):
        for group in range(fabric.group_count):
            tile_origin, tile = fabric.group_origin(origin, group), fabric.tile_spec(group)
            yield from iter_stacked_memory_tile(tile_origin, tile=tile,
                                                component=f"{component}:tile-{group:03d}", include_storage=False)
            if fabric.binary_rows:
                from .binary_row_decoder import iter_binary_row_decoder
                yield from iter_binary_row_decoder(tile_origin, tile, decoder_x=tile_origin.x + fabric.decoder_x_offset,
                                                    component=f"{component}:row-decoder-{group:03d}")
        return
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
            yield from iter_stair(
                Vec3(lane_x, row_spine_y, center - 4),
                Vec3(lane_x, row_branch_y, center),
                axis="z",
                signal_forward=True,
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
                yield from iter_stair(
                    Vec3(bp.x, y, branch_z),
                    bp,
                    axis="z",
                    signal_forward=True,
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
                bp = bank_ports(bo, fabric.bank_spec(bank)).data_out[bit]
                yield from iter_stair(
                    bp,
                    Vec3(bp.x, y, branch_z),
                    axis="z",
                    signal_forward=True,
                    component=component,
                )

    # ----- Per-bank selector/control trunks and row gates -----
    for bank in range(fabric.bank_count):
        bo = _bank_origin(origin, fabric, bank)
        bank_spec = fabric.bank_spec(bank)
        bp = bank_ports(bo, bank_spec)

        bank_x = bo.x - 170
        write_x = bo.x - 165
        read_x = bo.x - 160

        # Bank-select terminals are centralized left of the fabric. Each
        # bank gets a unique Z branch inside the 128-block inter-bank corridor,
        # then a local trunk runs the depth of that bank.
        bank_terminal = ports.bank_select[bank]
        column = bank % fabric.banks_per_row
        bank_branch_z = bo.z - 48 - column * 3
        yield from _wire_z(
            bank_terminal.x,
            bank_terminal.y,
            bank_terminal.z,
            bank_branch_z - 6,
            facing="south",
            component=component,
        )
        yield from iter_stair(
            Vec3(bank_terminal.x, bank_terminal.y, bank_branch_z - 6),
            Vec3(bank_terminal.x, bank_y, bank_branch_z),
            axis="z",
            signal_forward=True,
            component=component,
        )
        yield from _wire_x(
            bank_terminal.x,
            bank_x,
            bank_y,
            bank_branch_z,
            facing="east",
            component=component,
        )
        yield from _wire_z(
            bank_x,
            bank_y,
            bank_branch_z,
            bo.z + bank_spec.depth,
            facing="south",
            component=component,
        )

        # Global write/read enable branches feed these local trunks.
        yield from _wire_z(
            write_x,
            write_y,
            bo.z - 48,
            bo.z + bank_spec.depth,
            facing="south",
            component=component,
        )
        yield from _wire_z(
            read_x,
            read_y,
            bo.z - 48,
            bo.z + bank_spec.depth,
            facing="south",
            component=component,
        )

        for row in range(bank_spec.words):
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

            yield from iter_stair(
                Vec3(row_tap.x, row_branch_y, write_z),
                write_row,
                axis="x",
                signal_forward=True,
                component=component,
            )
            yield from iter_stair(
                Vec3(bank_x, bank_y, write_z),
                write_bank,
                axis="x",
                signal_forward=True,
                component=component,
            )
            yield from iter_stair(
                Vec3(write_x, write_y, write_z),
                write_en,
                axis="x",
                signal_forward=True,
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

            yield from iter_stair(
                Vec3(row_tap.x, row_branch_y, read_z),
                read_row,
                axis="x",
                signal_forward=True,
                component=component,
            )
            yield from iter_stair(
                Vec3(bank_x, bank_y, read_z),
                read_bank,
                axis="x",
                signal_forward=True,
                component=component,
            )
            yield from iter_stair(
                Vec3(read_x, read_y, read_z),
                read_en,
                axis="x",
                signal_forward=True,
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
