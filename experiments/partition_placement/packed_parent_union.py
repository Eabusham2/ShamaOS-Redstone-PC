"""Exact parent-domain checks with one compact host-side state inventory.

This collector changes neither emitted blocks nor routing. A fresh collector is
owned by one parent attempt and is unusable after a rejected domain. Original
within-domain literal equality, cross-domain active adjacency, climbing
headroom, child-envelope exceptions, duplicate counts and bounds are retained.

Ordinary Minecraft integer coordinates use a reversible integer key. Values
outside that packing range use exact integer triples instead of wrapping. Each
domain/state pair shares one immutable value, so a long wire does not allocate
an additional ownership tuple for every block.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Iterator, Mapping

from shamaos.hardware.memory import SUPPORT
from shamaos.hardware.partition_placement import PlacementError
from shamaos.model import BlockState, Box, Placement, Vec3


_XZ_OFFSET = 1 << 25
_XZ_MASK = (1 << 26) - 1
_X_STRIDE = 1 << 35
_Z_STRIDE = 1 << 9
_NEIGHBOR_OFFSETS = tuple(dx * _X_STRIDE + dz * _Z_STRIDE + dy
    for dx, dz in ((0, 0), (-1, 0), (1, 0), (0, -1), (0, 1))
    for dy in (-1, 0, 1))


def coordinate_key(x: int, y: int, z: int):
    """Pack integer coordinates injectively; do not clip or alias large values."""
    if not isinstance(x, int) or not isinstance(y, int) or not isinstance(z, int):
        raise TypeError("literal parent geometry requires integer coordinates")
    if -_XZ_OFFSET <= x < _XZ_OFFSET and -_XZ_OFFSET <= z < _XZ_OFFSET and -64 <= y < 448:
        return ((x + _XZ_OFFSET) << 35) | ((z + _XZ_OFFSET) << 9) | (y + 64)
    return x, y, z


def coordinate_from_key(key) -> Vec3:
    if isinstance(key, int):
        return Vec3((key >> 35) - _XZ_OFFSET, (key & 511) - 64,
                    ((key >> 9) & _XZ_MASK) - _XZ_OFFSET)
    return Vec3(*key)


def _neighbor_keys(x, y, z, key):
    # Addition cannot carry into an adjacent packed coordinate at these strict
    # interior positions. Boundary coordinates use the fully checked codec.
    if (isinstance(key, int) and -_XZ_OFFSET < x < _XZ_OFFSET - 1
            and -_XZ_OFFSET < z < _XZ_OFFSET - 1 and -64 < y < 447):
        for offset in _NEIGHBOR_OFFSETS:
            yield key + offset
    else:
        for dx, dz in ((0, 0), (-1, 0), (1, 0), (0, -1), (0, 1)):
            for dy in (-1, 0, 1):
                yield coordinate_key(x + dx, y + dy, z + dz)


@dataclass(frozen=True)
class ParentUnionSummary:
    unique_blocks: int
    child_duplicates: int
    bounds: Box
    domains: int
    emitted_operations: int


class PackedParentUnion:
    """Disposable exact inventory for a single parent placement attempt.

    ``add_domain`` consumes a complete domain iterator. A later rejection may
    leave partial state internally, so all later operations fail explicitly.
    The caller must discard this collector before trying another layout.
    """

    def __init__(self, child_boxes: Iterable[Box] = (),
                 expected_shared: Mapping[Vec3, BlockState] | None = None):
        self._states = {}
        self._headroom = {}
        self._boxes = tuple(child_boxes)
        self._box_coordinates = tuple((box.min.x, box.min.y, box.min.z,
                                       box.max.x, box.max.y, box.max.z) for box in self._boxes)
        self._shared = {coordinate_key(point.x, point.y, point.z): state
                        for point, state in (expected_shared or {}).items()}
        self._extrema = None
        for box in self._boxes:
            self._extend(box.min.x, box.min.y, box.min.z)
            self._extend(box.max.x, box.max.y, box.max.z)
        self._duplicates = 0
        self._domains = 0
        self._operations = 0
        self._failed = False
        self._finished = False

    def _extend(self, x, y, z):
        values = self._extrema
        if values is None:
            self._extrema = [x, y, z, x, y, z]
            return
        if x < values[0]: values[0] = x
        if y < values[1]: values[1] = y
        if z < values[2]: values[2] = z
        if x > values[3]: values[3] = x
        if y > values[4]: values[4] = y
        if z > values[5]: values[5] = z

    def _require_usable(self, *, writing=False):
        if self._failed:
            raise PlacementError("a rejected parent union cannot be reused")
        if writing and self._finished:
            raise PlacementError("a finished parent union cannot accept another domain")

    def add_domain(self, placements: Iterable[Placement]) -> None:
        self._require_usable(writing=True)
        self._domains += 1
        domain = self._domains
        entries = {}
        states, headroom = self._states, self._headroom
        try:
            for placement in placements:
                self._operations += 1
                point, state = placement.pos, placement.block
                x, y, z = point.x, point.y, point.z
                key = coordinate_key(x, y, z)
                entry = entries.get(state)
                if entry is None:
                    active = state != SUPPORT
                    # Match the old guard's exact dict(last value) semantics,
                    # including unusual duplicate block-property names.
                    climbing = (state.name == "minecraft:redstone_wire"
                                and "up" in dict(state.properties).values())
                    entry = entries[state] = (domain, state, active, climbing)
                previous = states.get(key)
                if previous is not None and previous[1] != state:
                    raise PlacementError(f"incompatible parent blocks at {point}: {previous[1]} / {state}")
                if entry[2]:
                    for near in _neighbor_keys(x, y, z, key):
                        other = states.get(near)
                        if other is not None and other[0] != domain and other[2]:
                            raise PlacementError(f"different bounded domains have adjacent active wires at {point}")
                reservation = headroom.get(key)
                if reservation is not None and reservation != domain:
                    raise PlacementError("a bounded domain obstructs another domain's climbing headroom")
                if entry[3]:
                    head_key = coordinate_key(x, y + 1, z)
                    over = states.get(head_key)
                    if over is not None and over[0] != domain:
                        raise PlacementError("a bounded domain obstructs another domain's climbing headroom")
                    headroom[head_key] = domain
                if previous is not None:
                    # Identical supports shared by separate domains retain
                    # their first owner, so later climbing checks still see
                    # their presence in the preceding-domain union.
                    continue
                for min_x, min_y, min_z, max_x, max_y, max_z in self._box_coordinates:
                    if min_x <= x <= max_x and min_y <= y <= max_y and min_z <= z <= max_z:
                        if self._shared.get(key) != state:
                            raise PlacementError(f"graph/facade route intrudes into a child envelope at {point}")
                        self._duplicates += 1
                        break
                states[key] = entry
                self._extend(x, y, z)
        except BaseException:
            self._failed = True
            raise

    def finish(self, *, minimum_y: int, maximum_y: int) -> ParentUnionSummary:
        self._require_usable()
        try:
            if self._extrema is None:
                raise PlacementError("an empty physical module has no placeable envelope")
            minimum = Vec3(*self._extrema[:3])
            maximum = Vec3(*self._extrema[3:])
            if minimum.y < minimum_y or maximum.y > maximum_y:
                raise PlacementError("facade hierarchy exceeds its above-grass height envelope")
            result = ParentUnionSummary(len(self._states), self._duplicates, Box(minimum, maximum),
                                        self._domains, self._operations)
            self._finished = True
            return result
        except BaseException:
            self._failed = True
            raise

    def iter_states(self) -> Iterator[tuple[Vec3, BlockState]]:
        """Decode lazily for exact small-fixture comparison; no second inventory."""
        self._require_usable()
        for key, entry in self._states.items():
            yield coordinate_from_key(key), entry[1]
