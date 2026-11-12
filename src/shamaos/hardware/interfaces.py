"""Physical terminal access contracts without enumerating literal memory bits."""
from dataclasses import dataclass
from typing import Mapping

from ..layout import MachineGeometry, default_origins, memory_specs, vram_spec
from ..model import Box, Placement, Vec3
from .clock import clock_ports, iter_clock
from .controls import control_ports, iter_controls
from .debug import debug_ports, iter_debug_panel
from .display import LampPanelSpec, iter_panel_terminal_keepouts, panel_bounds, panel_ports
from .memory import DUST
from .logic import REDSTONE_BLOCK
from .memory_backbone import fabric_tile_ports, iter_stacked_tile_terminal_keepouts, stacked_tile_bounds


@dataclass(frozen=True)
class RoutingInterfaces:
    access: dict[Vec3,tuple[str,...]]
    obstacles: tuple[Placement,...]
    endpoint_bounds: dict[Vec3,Box]
    obstacle_bounds: tuple[Box,...]
    minimum_y: int


def system_routing_interfaces(
    geometry: MachineGeometry,
    inputs: Mapping[str,tuple[Vec3,...]],
    outputs: Mapping[str,tuple[Vec3,...]],
    *,
    clock_origin: Vec3,
    constant_high: Vec3,
) -> RoutingInterfaces:
    """Declare native access faces and full-height component envelopes.

    Small panels are retained literally. Storage contributes only terminal
    neighborhoods and one bounding volume per tile, so a route cannot climb
    through unenumerated banks or their neighboring tile's occupied volume.
    """
    origins=default_origins(geometry)
    access={pin:("south",) for ports in (inputs,outputs) for pins in ports.values() for pin in pins}
    controls=control_ports(origins.input)
    for pin in (*controls.controller,*controls.keyboard_rows,*controls.keyboard_cols,
                controls.power,controls.reset,controls.pause,controls.step):
        access[pin]=("south",)
    clock=clock_ports(clock_origin)
    for pin in (clock.enable,clock.hold,clock.clock):
        access[pin]=("south",)
    diagnostics=debug_ports(origins.input.offset(0,0,-224))
    for pins in diagnostics.values():
        for pin in pins:
            access[pin]=("wall-south",)
    access[constant_high]=("south",)
    obstacles=[*iter_controls(origins.input),*iter_clock(clock_origin),
               *iter_debug_panel(origins.input.offset(0,0,-224)),
               Placement(constant_high.offset(dy=-1),REDSTONE_BLOCK,"constant"),
               Placement(constant_high,DUST,"constant")]
    endpoint_bounds={}
    obstacle_bounds=[]
    fabrics=(*memory_specs(geometry),vram_spec(geometry))
    for origin,fabric in zip((origins.cache,origins.ram,origins.flash,origins.vram),fabrics):
        for group,pins in enumerate(fabric_tile_ports(origin,fabric)):
            tile_origin=fabric.group_origin(origin,group)
            tile=fabric.tile_spec(group)
            bounds=stacked_tile_bounds(tile_origin,tile)
            obstacle_bounds.append(bounds)
            obstacles.extend(iter_stacked_tile_terminal_keepouts(tile_origin,tile))
            groups=(((*pins.bank_select,*pins.row_select,pins.read_enable,pins.write_enable),"west"),
                    (pins.write_data,"north"),(pins.selected_word,"south"))
            for terminals,direction in groups:
                for pin in terminals:
                    access[pin]=(direction,)
                    endpoint_bounds[pin]=bounds
    panel=LampPanelSpec(geometry.display_width,geometry.display_height,
                       geometry.pixel_pitch_x,geometry.pixel_pitch_y)
    display=panel_ports(origins.display,panel)
    bounds=panel_bounds(origins.display,panel)
    obstacle_bounds.append(bounds)
    obstacles.extend(iter_panel_terminal_keepouts(origins.display,panel))
    for terminals,direction in ((display.data_in,"north"),(display.row_select,"west")):
        for pin in terminals:
            access[pin]=(direction,)
            endpoint_bounds[pin]=bounds
    return RoutingInterfaces(access,tuple(obstacles),endpoint_bounds,tuple(obstacle_bounds),geometry.minimum_build_y)
