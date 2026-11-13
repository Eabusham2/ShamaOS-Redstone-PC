import pytest

from shamaos.hardware.display import LampPanelSpec, iter_lamp_panel, iter_panel_terminal_keepouts
from shamaos.hardware.external_router import ExternalNet, iter_external_router
from shamaos.hardware.external_router import _plan_escapes
from shamaos.hardware.debug import debug_ports, iter_debug_panel
from shamaos.hardware.controls import control_bounds, control_route_access, iter_controls
from shamaos.hardware.clock import clock_ports, iter_clock, system_clock_origin
from shamaos.hardware.interfaces import system_routing_interfaces
from shamaos.layout import MachineGeometry, default_origins, memory_specs
from shamaos.hardware.memory_backbone import fabric_tile_ports
from shamaos.model import Vec3


def test_display_keepouts_are_exact_native_block_states():
    origin=Vec3(0,8,0)
    spec=LampPanelSpec(8,4)
    native={p.pos:p.block for p in iter_lamp_panel(origin,spec=spec)}
    keepouts=list(iter_panel_terminal_keepouts(origin,spec))
    assert len(keepouts)==8*(spec.width+spec.height)
    assert all(native[p.pos]==p.block for p in keepouts)


@pytest.mark.parametrize("surface",(-61,63))
def test_interface_contracts_are_derived_from_surface_and_native_tiles(surface):
    geometry=MachineGeometry(Vec3(0,surface+5,0),8,8,8,8,4,
                             vram_bytes=8,bank_words=4,bank_word_bits=8,memory_layers=2,surface_y=surface)
    origins=default_origins(geometry)
    shell_pin=Vec3(10000,surface+6,0)
    interfaces=system_routing_interfaces(geometry,{"a":(shell_pin,)},{},
        clock_origin=system_clock_origin(origins.input),constant_high=origins.input.offset(300,1))
    assert interfaces.minimum_y==surface+1
    assert min(p.pos.y for p in interfaces.obstacles)>=interfaces.minimum_y
    assert interfaces.access[shell_pin]==("south",)
    pins=fabric_tile_ports(origins.cache,memory_specs(geometry)[0])[0]
    assert not pins.row_select
    assert all(interfaces.access[pin]==("north",) for pin in (*pins.bank_select,*pins.row_address,pins.write_enable))
    assert interfaces.access[pins.read_enable]==("west",)
    assert all(interfaces.access[pin]==("north",) for pin in pins.write_data)
    assert all(interfaces.access[pin]==("south",) for pin in pins.selected_word)
    assert len(interfaces.obstacle_bounds)==18  # Four tiles + two address buses/body each, console and display.


def test_router_rejects_any_terminal_support_at_or_below_protected_grass():
    with pytest.raises(ValueError,match="support would violate build height"):
        list(iter_external_router([ExternalNet("bad",Vec3(0,64,0),(Vec3(20,65,0),))],minimum_y=64))


def test_complete_console_export_clears_its_stacked_backend():
    origin=Vec3(64,32,5000)
    access=control_route_access(origin)
    bounds=control_bounds(origin)
    escapes=_plan_escapes(access,96,iter_controls(origin),access=access,
        endpoint_bounds=dict.fromkeys(access,bounds),obstacle_bounds=(bounds,))
    assert len(escapes)==30
    for pin,escape in escapes.items():
        if access[pin]==("wall-south",):
            assert escape.branch_x>=bounds.max.x+3


def test_clock_and_all_console_exports_clear_the_wider_native_console():
    origin=Vec3(900,-56,-8)
    access=control_route_access(origin)
    console_pins=tuple(access)
    clock_base=system_clock_origin(origin)
    clock=clock_ports(clock_base)
    access.update(dict.fromkeys((clock.enable,clock.hold,clock.clock),("south",)))
    bounds=control_bounds(origin)
    obstacles=[*iter_controls(origin),*iter_clock(clock_base)]
    escapes=_plan_escapes(access,314,obstacles,access=access,minimum_y=-60,
        endpoint_bounds=dict.fromkeys(console_pins,bounds),obstacle_bounds=(bounds,))
    assert len(escapes)==33


def test_complete_debug_wall_escape_geometry_clears_console_and_clock():
    origin=Vec3(900,-56,4776)
    pins=tuple(pin for group in debug_ports(origin).values() for pin in group)
    obstacles=(*iter_debug_panel(origin),*iter_controls(origin.offset(dz=224)),
               *iter_clock(origin.offset(180,0,224)))
    escapes=_plan_escapes(pins,314,obstacles,access=dict.fromkeys(pins,("wall-south",)),minimum_y=-60)
    assert len(escapes)==sum(len(group) for group in debug_ports(origin).values())==993
    assert max(escape.wall_lead_z-pin.z for pin,escape in escapes.items())==192
    assert all(escape.branch_x>max(pin.x for pin in pins) for escape in escapes.values())
