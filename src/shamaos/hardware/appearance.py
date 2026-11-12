"""Footprint-neutral colors applied after all electrical placement and routing.

Only existing plain stone is replaced, with opaque full-cube concrete. Those
blocks are still electrical supports, including strongly powered torch blocks;
they must never be replaced with glass, slabs or other non-equivalent shapes.
Color is a pure function of world coordinates, so an identical support emitted
by two different components remains an identical placement at the world writer.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Iterable, Iterator, Mapping

from ..model import BlockState, Box, Placement, Vec3
from .controls import CONTROLLER_NAMES, KEYBOARD_OFFSET, control_actuators, keyboard_buttons
from .memory import SUPPORT

if TYPE_CHECKING:
    from ..layout import MachineGeometry
    from ..synthesis import PhysicalNetlist


CONCRETE_COLORS = (
    "white", "orange", "magenta", "light_blue", "yellow", "lime", "pink", "gray",
    "light_gray", "cyan", "purple", "blue", "brown", "green", "red", "black",
)
CONCRETE = MappingProxyType({color: BlockState.of(f"minecraft:{color}_concrete")
                             for color in CONCRETE_COLORS})

# Component body / narrow alignment stripes. Routing outside a component is
# neutral, making the functional districts legible without extra walls/floors.
PALETTE = MappingProxyType({
    "cpu": ("red", "pink"),
    "gpu": ("blue", "light_blue"),
    "kernel": ("magenta", "pink"),
    "services": ("light_blue", "white"),
    "assembler": ("purple", "magenta"),
    "filesystem": ("orange", "yellow"),
    "cache": ("yellow", "white"),
    "ram": ("green", "lime"),
    "flash": ("orange", "yellow"),
    "vram": ("cyan", "light_blue"),
    "input": ("purple", "magenta"),
    "display": ("gray", "cyan"),
    "debug-panel": ("gray", "magenta"),
    "clock": ("red", "orange"),
    "routing": ("gray", "light_gray"),
})
PARTITION_PALETTE = MappingProxyType({
    "u_cpu": "cpu", "u_gpu": "gpu", "u_kernel": "kernel", "u_services": "services",
    "u_asm": "assembler", "u_fs": "filesystem", "u_cache": "cache", "u_ram": "ram",
    "u_flash": "flash", "u_vram": "vram", "u_input": "input", "u_display_bridge": "display",
})
CONTROL_COLORS = MappingProxyType({
    "UP": "light_blue", "DOWN": "light_blue", "LEFT": "light_blue", "RIGHT": "light_blue",
    "A": "lime", "B": "red", "HOME": "white", "EXIT": "orange",
    "EDITOR": "magenta", "FILES": "yellow", "RESET": "red", "STEP": "orange",
    "POWER": "lime", "PAUSE": "yellow", "SHIFT": "cyan",
})


def _contains(box: Box, pos: Vec3) -> bool:
    return (box.min.x <= pos.x <= box.max.x and box.min.y <= pos.y <= box.max.y
            and box.min.z <= pos.z <= box.max.z)


def _stripe(pos: Vec3, anchor: Vec3, palette: str) -> BlockState:
    body, accent = PALETTE[palette]
    marked = (pos.x - anchor.x) % 64 < 2 or (pos.z - anchor.z) % 64 < 2
    return CONCRETE[accent if marked else body]


@dataclass(frozen=True)
class AppearanceRegion:
    name: str
    box: Box
    palette: str

    def __post_init__(self) -> None:
        if self.palette not in PALETTE:
            raise ValueError(f"unknown appearance palette: {self.palette}")


@dataclass(frozen=True)
class AppearancePlan:
    regions: tuple[AppearanceRegion, ...] = ()
    console_origin: Vec3 | None = None
    _markers: Mapping[Vec3, BlockState] = field(init=False, repr=False, compare=False)
    _keyboard_origin: Vec3 | None = field(init=False, repr=False, compare=False)
    _x_starts: tuple[int, ...] = field(init=False, repr=False, compare=False)
    _y_starts: tuple[int, ...] = field(init=False, repr=False, compare=False)
    _regions_xy: tuple[tuple[tuple[AppearanceRegion, ...], ...], ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        markers: dict[Vec3, BlockState] = {}
        if self.console_origin is not None:
            for row, buttons in enumerate(keyboard_buttons(self.console_origin)):
                for col, button in enumerate(buttons):
                    color = "white" if row % 2 == 0 else "light_gray"
                    if row == 4 and col >= 4:
                        color = ("light_blue", "lime", "orange", "cyan")[col - 4]
                    elif row == 7:
                        color = ("light_blue", "light_blue", "light_blue", "light_blue",
                                 "orange", "red", "light_gray", "cyan")[col]
                    markers[button.offset(dy=-1)] = CONCRETE[color]
            for name, button in control_actuators(self.console_origin).items():
                backing = button.offset(dz=-1) if name in CONTROLLER_NAMES[:8] else button.offset(dx=1)
                markers[backing] = CONCRETE[CONTROL_COLORS[name]]
        object.__setattr__(self, "_markers", MappingProxyType(markers))
        object.__setattr__(self, "_keyboard_origin", None if self.console_origin is None else
                           self.console_origin.offset(KEYBOARD_OFFSET.x, KEYBOARD_OFFSET.y, KEYBOARD_OFFSET.z))
        # Index the few region boundaries instead of walking every component
        # for each of the hundreds of millions of electrical support blocks.
        # Intervals depend on boundaries, never on enormous routed world area.
        x_starts = tuple(sorted({x for r in self.regions for x in (r.box.min.x, r.box.max.x + 1)}))
        y_starts = tuple(sorted({y for r in self.regions for y in (r.box.min.y, r.box.max.y + 1)}))
        object.__setattr__(self, "_x_starts", x_starts)
        object.__setattr__(self, "_y_starts", y_starts)
        object.__setattr__(self, "_regions_xy", tuple(tuple(
            tuple(r for r in self.regions if r.box.min.x <= x <= r.box.max.x
                  and r.box.min.y <= y <= r.box.max.y)
            for y in y_starts) for x in x_starts))

    def support_at(self, pos: Vec3) -> BlockState:
        marker = self._markers.get(pos)
        if marker is not None:
            return marker
        if self._keyboard_origin is not None:
            kb = self._keyboard_origin
            # The existing walkable deck and entry stair receive a dark base;
            # function keys above it retain their separate colored backings.
            if (kb.x <= pos.x <= kb.x + 15 and kb.z - 1 <= pos.z <= kb.z + 15
                    and pos.y == kb.y):
                return CONCRETE["gray"]
            if pos.x == kb.x + 1 and 1 <= kb.y - pos.y <= 4 and pos.z == kb.z - 1 - (kb.y - pos.y):
                return CONCRETE["light_gray"]
        xi, yi = bisect_right(self._x_starts, pos.x) - 1, bisect_right(self._y_starts, pos.y) - 1
        for region in self._regions_xy[xi][yi] if xi >= 0 and yi >= 0 else ():
            if region.box.min.z <= pos.z <= region.box.max.z:
                return _stripe(pos, region.box.min, region.palette)
        return _stripe(pos, Vec3(0, 0, 0), "routing")

    def apply(self, placement: Placement) -> Placement:
        if placement.block != SUPPORT:
            return placement
        return Placement(placement.pos, self.support_at(placement.pos), placement.component)

    def placements(self, placements: Iterable[Placement]) -> Iterator[Placement]:
        for placement in placements:
            yield self.apply(placement)

    def manifest(self) -> dict[str, object]:
        controls = []
        if self.console_origin is not None:
            for name, button in control_actuators(self.console_origin).items():
                controls.append({"label": name, "button": vars(button), "color": CONTROL_COLORS[name]})
        return {
            "style": "component-concrete-v1", "applied_after_routing": True,
            "replaced_block": SUPPORT.name, "added_blocks": 0, "moved_blocks": 0,
            "electrical_states_unchanged": True, "coordinate_deterministic": True,
            "stripe_period_blocks": 64, "stripe_width_blocks": 2,
            "region_lookup": "bounded coordinate intervals; independent of routed world area",
            "legend": {name: {"body": body, "accent": accent} for name, (body, accent) in PALETTE.items()},
            "regions": [{"name": r.name, "palette": r.palette, "min": vars(r.box.min), "max": vars(r.box.max)}
                        for r in self.regions],
            "controls": controls,
            "labels": "coordinate/color legend; no decorative signs or additional blocks",
        }


def _logic_region(name: str, physical: PhysicalNetlist) -> AppearanceRegion:
    from .logic import template_for

    lo = physical.origin
    hi = physical.origin

    def include(point: Vec3) -> None:
        nonlocal lo, hi
        lo = Vec3(min(lo.x, point.x), min(lo.y, point.y), min(lo.z, point.z))
        hi = Vec3(max(hi.x, point.x), max(hi.y, point.y), max(hi.z, point.z))

    for cell, origin in physical.cell_origins.items():
        template = template_for(physical.cell_types[cell])
        include(origin.offset(0, -1, -template.depth))
        include(origin.offset(template.width, 3, template.depth))
    for ports in (physical.input_ports, physical.output_ports):
        for pins in ports.values():
            for pin in pins:
                include(pin.offset(dy=-1))
                include(pin)
    return AppearanceRegion(name, Box(lo, hi), PARTITION_PALETTE.get(name, "routing"))


def appearance_for_system(
    geometry: MachineGeometry,
    *,
    partitions: Iterable[tuple[str, PhysicalNetlist]] = (),
) -> AppearancePlan:
    """Use actual native collision volumes, followed by synthesized cell bounds.

    Co-located cache/RAM/VRAM therefore retain distinct colors by height and
    decoder column. Reserved logic zones are deliberately not color regions:
    the real mapped partitions can differ from those generous reservations.
    """
    from ..layout import default_origins, plan_machine

    regions = []
    for component in plan_machine(geometry).components:
        if component.kind == "synthesized-logic":
            continue
        for index, box in enumerate(component.collision_boxes or (component.box,)):
            regions.append(AppearanceRegion(f"{component.name}:{index}", box, component.name))
    regions.extend(_logic_region(name, physical) for name, physical in partitions)
    return AppearancePlan(tuple(regions), default_origins(geometry).input)
