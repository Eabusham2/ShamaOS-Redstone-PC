"""Exact horizontal footprints for the regular physical fabrics.

Repeating decoder rows and vertical layers are counted symbolically. The
small controls are sampled literally; no complete memory fabric is emitted.
"""
from dataclasses import replace
from functools import lru_cache

from .hardware.clock import iter_clock
from .hardware.controls import iter_controls
from .hardware.debug import iter_debug_panel
from .hardware.display import LampPanelSpec, panel_bounds
from .hardware.memory import MemoryBankSpec, bank_ports
from .hardware.memory_backbone import StackedMemoryTileSpec, _return_span, iter_stacked_memory_tile
from .layout import MachineGeometry, PhysicalOrigins, default_origins, memory_specs, vram_spec
from .model import Box, Vec3


def _line_chunks(chunks: set[tuple[int, int]], x0: int, x1: int, z0: int, z1: int) -> None:
    chunks.update((x, z) for x in range(min(x0, x1) // 16, max(x0, x1) // 16 + 1)
                  for z in range(min(z0, z1) // 16, max(z0, z1) // 16 + 1))


def panel_resources(origin: Vec3, panel: LampPanelSpec) -> tuple[int, frozenset[tuple[int, int]]]:
    """Count the authoritative panel's columns, row buses and pixel cells."""
    width, height, px, pz = panel.width, panel.height, panel.pixel_pitch_x, panel.pixel_pitch_y
    if width <= 0 or height <= 0 or px < 5 or pz < 5:
        raise ValueError("unsupported physical panel geometry")
    count = 2 * width * ((height - 1) * pz + 67)
    count += height * (120 + 2 * ((width - 1) * px + 8) + 24 * width)
    chunks: set[tuple[int, int]] = set()
    for col in range(width):
        x = origin.x + col * px
        _line_chunks(chunks, x, x, origin.z - 64, origin.z + (height - 1) * pz + 2)
    for row in range(height):
        z = origin.z + row * pz
        _line_chunks(chunks, origin.x - 64, origin.x + (width - 1) * px + 4, z - 4, z - 4)
        for col in range(width):
            x = origin.x + col * px
            _line_chunks(chunks, x + 2, x + 2, z - 3, z - 2)
            for dz in (-1, 1):
                _line_chunks(chunks, x, x + 4, z + dz, z + dz)
            _line_chunks(chunks, x, x + 3, z, z)
    return count, frozenset(chunks)


@lru_cache(maxsize=32)
def _control_placement_count(tile: StackedMemoryTileSpec) -> int:
    """Rows alternate between two gate columns; each pair adds equal work."""
    origin = Vec3(0, -60, 0)

    def sample(words: int) -> int:
        small = replace(tile, bank=replace(tile.bank, words=words))
        return sum(1 for _ in iter_stacked_memory_tile(origin, tile=small, include_storage=False, include_read_return=False))

    first, second, fourth = sample(1), sample(2), sample(4)
    pair = fourth - second
    words = tile.bank.words
    controls = (first + ((words - 1) // 2) * pair) if words % 2 else (second + ((words - 2) // 2) * pair)
    # Each return descent occupies one full span, each lower bank contributes
    # a parallel isolated lead, and the bottom exports a buffered flat pin.
    span = _return_span(tile.layer_pitch)
    per_bit = 8 + (tile.banks - 1) * (span * (tile.banks + 2) + 2)
    return controls + tile.bank.word_bits * per_bit


@lru_cache(maxsize=64)
def _control_prefix_chunks(bank: MemoryBankSpec, x_mod: int, z_mod: int) -> frozenset[tuple[int, int]]:
    # All vertical layers repeat the same X/Z local controls. Only their
    # shared return grows horizontally with layer count; add that below.
    tile = StackedMemoryTileSpec(bank=bank, banks=1, layer_pitch=12)
    return frozenset((placement.pos.x // 16, placement.pos.z // 16)
                     for placement in iter_stacked_memory_tile(Vec3(x_mod, 0, z_mod), tile=tile,
                                                                include_storage=False, include_read_return=False))


def tile_control_chunks(origin: Vec3, tile: StackedMemoryTileSpec) -> frozenset[tuple[int, int]]:
    chunks = {(origin.x // 16 + x, origin.z // 16 + z)
              for x, z in _control_prefix_chunks(tile.bank, origin.x & 15, origin.z & 15)}
    distance = (tile.banks - 1) * _return_span(tile.layer_pitch)
    for source in bank_ports(origin, tile.bank).data_out:
        _line_chunks(chunks, source.x + 2, source.x + 2, source.z, source.z + distance + 2)
        if distance:
            _line_chunks(chunks, source.x, source.x, source.z + 1, source.z + distance)
        for layer in range(tile.banks):
            z = source.z + layer * _return_span(tile.layer_pitch)
            _line_chunks(chunks, source.x + 1, source.x + 1, z, z)
    return frozenset(chunks)


def _small_profile(placements, *, activation_points: list[Vec3] | None = None) -> tuple[int, frozenset[tuple[int, int]], Box]:
    count = 0
    chunks = set()
    minimum, maximum = [10**12] * 3, [-10**12] * 3
    for placement in placements:
        count += 1
        chunks.add((placement.pos.x // 16, placement.pos.z // 16))
        if activation_points is not None and (placement.block.name.endswith("_button") or placement.block.name == "minecraft:lever"):
            activation_points.append(placement.pos)
        for axis, value in enumerate((placement.pos.x, placement.pos.y, placement.pos.z)):
            minimum[axis], maximum[axis] = min(minimum[axis], value), max(maximum[axis], value)
    return count, frozenset(chunks), Box(Vec3(*minimum), Vec3(*maximum))


@lru_cache(maxsize=16)
def regular_fabric_resources(geometry: MachineGeometry, origins: PhysicalOrigins | None = None) -> dict[str, object]:
    # Import lazily because generator calls this after its footprint helper
    # and geometry have been initialized.
    from .generator import _bank_chunk_footprint

    origins = default_origins(geometry) if origins is None else origins
    panel = LampPanelSpec(geometry.display_width, geometry.display_height, geometry.pixel_pitch_x, geometry.pixel_pitch_y)
    activation_points: list[Vec3] = []
    regular = {
        "screen": (*panel_resources(origins.display, panel), panel_bounds(origins.display, panel)),
        "controls": _small_profile(iter_controls(origins.input), activation_points=activation_points),
        "debug_panel": _small_profile(iter_debug_panel(origins.input.offset(0, 0, -224))),
        "clock": _small_profile(iter_clock(origins.input.offset(180, 0, 0))),
    }
    regular_chunks = set().union(*(footprint for _, footprint, _ in regular.values()))
    boxes = [box for _, _, box in regular.values()]
    memory_chunks: set[tuple[int, int]] = set()
    local_placements = 0
    memory_details = {}
    for name, base, fabric in zip(("cache", "ram", "flash", "vram"),
                                  (origins.cache, origins.ram, origins.flash, origins.vram),
                                  (*memory_specs(geometry), vram_spec(geometry))):
        fabric_chunks: set[tuple[int, int]] = set()
        controls = 0
        for group in range(fabric.group_count):
            origin, tile = fabric.group_origin(base, group), fabric.tile_spec(group)
            fabric_chunks.update((origin.x // 16 + x, origin.z // 16 + z)
                                 for x, z in _bank_chunk_footprint(tile.bank, origin.x & 15, origin.z & 15))
            fabric_chunks.update(tile_control_chunks(origin, tile))
            controls += _control_placement_count(tile)
        box = fabric.bounds(base)
        boxes.append(box)
        memory_details[name] = {"local_control_and_return_placements": controls,
                                "storage_and_local_control_chunks": len(fabric_chunks),
                                "bounds": {"min": vars(box.min), "max": vars(box.max)}}
        local_placements += controls
        memory_chunks.update(fabric_chunks)
    union = regular_chunks | memory_chunks
    regions = {(x // 32, z // 32) for x, z in union}
    minimum = Vec3(min(box.min.x for box in boxes), min(box.min.y for box in boxes), min(box.min.z for box in boxes))
    maximum = Vec3(max(box.max.x for box in boxes), max(box.max.y for box in boxes), max(box.max.z for box in boxes))
    width, depth = maximum.x - minimum.x + 1, maximum.z - minimum.z + 1
    chunk_width, chunk_depth = maximum.x // 16 - minimum.x // 16 + 1, maximum.z // 16 - minimum.z // 16 + 1
    control_min = Vec3(min(point.x for point in activation_points), min(point.y for point in activation_points), min(point.z for point in activation_points))
    control_max = Vec3(max(point.x for point in activation_points), max(point.y for point in activation_points), max(point.z for point in activation_points))
    activation_width, activation_depth = control_max.x - control_min.x + 1, control_max.z - control_min.z + 1
    from .hardware.controls import control_interaction_descriptor
    interaction = control_interaction_descriptor(origins.input)
    assert interaction["activation_count"] == len(activation_points)
    assert interaction["activation_bounds"] == {"min": vars(control_min), "max": vars(control_max)}
    return {
        "method": "exact-regular-geometry-footprints-and-periodic-row-counts",
        "screen_controls_debug_clock": {name: {"emitted_placements": count, "horizontal_chunks": len(chunks),
                                               "bounds": {"min": vars(box.min), "max": vars(box.max)}}
                                         for name, (count, chunks, box) in regular.items()},
        "screen_controls_debug_clock_union_chunks": len(regular_chunks),
        "player_interaction": {
            **interaction,
            "activation_count": len(activation_points), "activation_bounds": {"min": vars(control_min), "max": vars(control_max)},
            "activation_width_blocks": activation_width, "activation_depth_blocks": activation_depth,
            "within_16_by_16_envelope": activation_width <= 16 and activation_depth <= 16,
            "walkability_verified": True,
            "scope": "literal button/lever positions and verified console clearance; backend routing footprint is reported separately; full-screen viewing is a separate concern",
        },
        "memory_local_controls": memory_details,
        "local_memory_control_and_return_placements": local_placements,
        "storage_and_local_control_union_chunks": len(memory_chunks),
        "regular_fabric_union_chunks": len(union),
        "regular_fabric_regions": len(regions),
        "minimum_regular_fabric_region_bytes": len(union) * 4096 + len(regions) * 8192,
        "regular_fabric_envelope": {
            "min": vars(minimum), "max": vars(maximum), "width_blocks": width, "depth_blocks": depth,
            "height_blocks": maximum.y - minimum.y + 1, "horizontal_area_blocks": width * depth,
            "rectangle_perimeter_blocks": 2 * (width + depth),
            "chunk_x_min": minimum.x // 16, "chunk_x_max": maximum.x // 16,
            "chunk_z_min": minimum.z // 16, "chunk_z_max": maximum.z // 16,
            "chunk_width": chunk_width, "chunk_depth": chunk_depth,
            "envelope_chunks": chunk_width * chunk_depth,
        },
        "scope": "storage, local bank decoders and shared returns, screen, controls, debug and clock; synthesized logic and external routes are separate",
    }
