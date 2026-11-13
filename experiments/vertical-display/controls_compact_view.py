"""Walkable 16×16 operating console with isolated stacked pulse circuitry."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from shamaos.model import BlockState, Box, Placement, Vec3
from shamaos.hardware.external_router import _wire_x, _wire_z
from shamaos.hardware.memory import DUST, SUPPORT, repeater
from shamaos.hardware.pulse import iter_pulse_stretcher
from shamaos.hardware.routing import iter_stair

BUTTON_FLOOR = BlockState.of("minecraft:stone_button", face="floor", facing="north", powered="false")
CONTROLLER_NAMES = ("UP","DOWN","LEFT","RIGHT","A","B","HOME","EXIT","EDITOR","FILES")
SIDE_NAMES = ("EDITOR","FILES","RESET","STEP","POWER","PAUSE","SHIFT")
CONTROL_SIZE = (156,44,144)
CONTROL_BOUNDS = ((2,-4,0),(170,39,132))
KEYBOARD_OFFSET = Vec3(60,0,52)
KEYBOARD_PULSE_TAPS = 26


@dataclass(frozen=True)
class ControlPorts:
    controller: tuple[Vec3,...]
    keyboard_rows: tuple[Vec3,...]
    keyboard_cols: tuple[Vec3,...]
    power: Vec3
    reset: Vec3
    pause: Vec3
    step: Vec3


def _keyboard_origin(origin: Vec3) -> Vec3:
    return origin.offset(KEYBOARD_OFFSET.x,KEYBOARD_OFFSET.y,KEYBOARD_OFFSET.z)


def control_bounds(origin: Vec3) -> Box:
    return Box(origin.offset(*CONTROL_BOUNDS[0]),origin.offset(*CONTROL_BOUNDS[1]))


def control_interaction_bounds(origin: Vec3) -> Box:
    kb=_keyboard_origin(origin)
    return Box(kb.offset(0,1,-1),kb.offset(15,2,14))


def control_actuators(origin: Vec3) -> dict[str,Vec3]:
    kb=_keyboard_origin(origin)
    return ({name:kb.offset(2*i,2,-1) for i,name in enumerate(CONTROLLER_NAMES[:8])}
            | {name:kb.offset(15,2,2*i+1) for i,name in enumerate(SIDE_NAMES)})


def controller_buttons(origin: Vec3) -> dict[str,Vec3]:
    controls=control_actuators(origin)
    return {name:controls[name] for name in (*CONTROLLER_NAMES,"RESET","STEP")}


def control_switches(origin: Vec3) -> dict[str,Vec3]:
    controls=control_actuators(origin)
    return {name:controls[name] for name in ("POWER","PAUSE")}


def keyboard_buttons(origin: Vec3) -> tuple[tuple[Vec3,...],...]:
    kb=_keyboard_origin(origin)
    return tuple(tuple(kb.offset(2*col,1,2*row) for col in range(8)) for row in range(8))


def control_interaction_descriptor(origin: Vec3) -> dict[str,object]:
    kb=_keyboard_origin(origin)
    bounds=control_interaction_bounds(origin)
    return {"keyboard_buttons":64,"perimeter_controls":15,"activation_count":79,
            "activation_bounds":{"min":vars(bounds.min),"max":vars(bounds.max)},
            "activation_span_xz":[bounds.size.x,bounds.size.z],
            "within_16_by_16_envelope":True,"deck_entry":vars(kb.offset(1,1,-1)),
            "deck_feet_y":kb.y+1,"stairs_lowest_support_y":kb.y-4,
            "dedicated_shift_duplicates_matrix_key":[7,7],
            "verification":"actual-engine all-input regression and connected two-block player-clearance deck test"}


def control_ports(origin: Vec3) -> ControlPorts:
    kb=_keyboard_origin(origin)
    momentary={name:kb.offset(2*i+3,5+4*i,-10) for i,name in enumerate(CONTROLLER_NAMES[:8])}
    momentary.update({name:kb.offset(24,5+4*i,2*i+4) for i,name in enumerate(SIDE_NAMES[:4])})
    return ControlPorts(tuple(momentary[name] for name in CONTROLLER_NAMES),
        tuple(kb.offset(-4,2+4*i,2*i-3) for i in range(8)),
        tuple(kb.offset(2*i-3+96,11+4*i,26) for i in range(8)),
        kb.offset(21,21,9),momentary["RESET"],kb.offset(21,25,11),momentary["STEP"])


def control_route_access(origin: Vec3) -> dict[Vec3,tuple[str,...]]:
    ports=control_ports(origin)
    return ({pin:("east",) for pin in ports.controller[:8]}
            | {pin:("wall-south",) for pin in (*ports.controller[8:],ports.reset,ports.step)}
            | {pin:("north",) for pin in ports.keyboard_rows}
            | {pin:("west",) for pin in ports.keyboard_cols}
            | {ports.power:("east",),ports.pause:("east",)})


def _wire(point: Vec3, component: str, state: BlockState = DUST) -> Iterator[Placement]:
    yield Placement(point.offset(dy=-1),SUPPORT,component)
    yield Placement(point,state,component)


def _tower(base: Vec3, height: int, component: str) -> Iterator[Placement]:
    """A target input and an even number of torch inversions; active-high top."""
    if height%4!=3:
        raise ValueError("console tower must preserve polarity")
    yield Placement(base,BlockState.of("minecraft:target",power="0"),component)
    for dy in range(1,height+1):
        state=SUPPORT if dy%2==0 else BlockState.of("minecraft:redstone_torch",lit="true" if dy%4==1 else "false")
        yield Placement(base.offset(dy=dy),state,component)


def iter_keyboard_matrix(origin: Vec3, *, component: str = "keyboard") -> Iterator[Placement]:
    """Two-block key pitch with clear walkways and an eight-block circuit roof.

    The row bus is below the deck and cannot back-power its key block above.
    A separate wall-torch input feeds each column through four inversions.
    """
    for x in range(16):
        for z in range(16):
            yield Placement(origin.offset(x,-2,z),SUPPORT,component)
            if x%2 or not z%2:
                yield Placement(origin.offset(x,0,z),SUPPORT,component)
    for row in range(8):
        z=2*row
        for x in range(-2,15):
            yield from _wire(origin.offset(x,-1,z),component,repeater("west") if x in (-1,3,11) else DUST)
        for col in range(8):
            x=2*col
            yield Placement(origin.offset(x,0,z),SUPPORT,component)
            yield Placement(origin.offset(x,1,z),BUTTON_FLOOR,component)
            yield Placement(origin.offset(x,0,z+1),BlockState.of("minecraft:redstone_wall_torch",facing="south",lit="true"),component)
            for y in range(1,8):
                state=SUPPORT if y%2 else BlockState.of("minecraft:redstone_torch",lit="true" if y==4 else "false")
                yield Placement(origin.offset(x,y,z+1),state,component)
    for col in range(8):
        for z in range(19):
            yield from _wire(origin.offset(2*col,8,z),component,repeater("south") if z in (4,12,17) else DUST)
    # Different heights let sixteen pulse circuits share horizontal space.
    for row in range(8):
        base=origin.offset(-3,-1,2*row);height=4*row+3;top=base.offset(dy=height)
        yield from _tower(base,height,component)
        yield from _wire(top.offset(dx=-1),component,repeater("west"))
        yield from _wire(top.offset(dx=-2),component)
        yield from iter_pulse_stretcher(top.offset(-3,-1,0),quarter_turns=3,taps=KEYBOARD_PULSE_TAPS,component=component)
    for col in range(8):
        base=origin.offset(2*col,8,19);height=4*col+3;top=base.offset(dy=height)
        yield from _tower(base,height,component)
        for z in range(1,6):
            yield from _wire(top.offset(dz=z),component,repeater("south"))
        # Keep long pulse circuits out of the screen sightline. The lateral
        # input lead is above the top ray leaving the south viewing lip.
        yield from _wire_x(top.offset(dz=6),top.offset(96,0,6),signal_forward=True,component=component)
        for z in (7,8):
            yield from _wire(top.offset(96,0,z),component)
        yield from iter_pulse_stretcher(top.offset(96,-1,9),quarter_turns=2,taps=KEYBOARD_PULSE_TAPS,component=component)


def iter_controller_panel(origin: Vec3, *, component: str = "controller") -> Iterator[Placement]:
    kb=_keyboard_origin(origin)
    raw={}
    for index,(name,button) in enumerate(control_actuators(origin).items()):
        front=index<8
        support=button.offset(dz=-1) if front else button.offset(dx=1)
        yield Placement(support,SUPPORT,component)
        yield Placement(button,BlockState.of("minecraft:lever" if name in ("POWER","PAUSE") else "minecraft:stone_button",
                         face="wall",facing="south" if front else "west",powered="false"),component)
        diode=support.offset(dz=-1) if front else support.offset(dx=1)
        pin=diode.offset(dz=-1) if front else diode.offset(dx=1)
        yield from _wire(diode,component,repeater("north" if front else "east"))
        yield from _wire(pin,component);raw[name]=pin
        if name=="SHIFT":
            yield from _wire(support.offset(dz=1),component,repeater("south"))
            yield from _wire(support.offset(dz=2),component);raw["SHIFT_COL"]=support.offset(dz=2)
    for index,name in enumerate(CONTROLLER_NAMES[:8]):
        base=raw[name].offset(dz=-1);height=4*index+3;top=base.offset(dy=height)
        yield from _tower(base,height,component)
        yield from _wire(top.offset(dz=-1),component,repeater("north"))
        # Keep the output four blocks north of the taller neighboring
        # actuator towers, leaving an unobstructed east-facing export leg.
        for distance in range(2,7):
            yield from _wire(top.offset(dz=-distance),component)
        yield from iter_pulse_stretcher(top.offset(0,-1,-7),component=component)
    for index,name in enumerate(SIDE_NAMES[:6]):
        base=raw[name].offset(dx=1);height=4*index+3;top=base.offset(dy=height)
        yield from _tower(base,height,component)
        yield from _wire(top.offset(dx=1),component,repeater("east"))
        yield from _wire(top.offset(dx=2),component)
        if name not in ("POWER","PAUSE"):
            for distance in range(3,7):
                yield from _wire(top.offset(dx=distance),component)
            yield from iter_pulse_stretcher(top.offset(7,-1,0),quarter_turns=1,component=component)
    # Independent diodes prevent ordinary matrix events back-feeding Shift.
    base=raw["SHIFT"].offset(dx=1);yield from _tower(base,27,component)
    start=base.offset(1,27);end=start.offset(dy=1,dz=3)
    yield from iter_stair(start,end,axis="z",signal_forward=True,component=component)
    target=Vec3(kb.x-6,end.y,end.z)
    yield from _wire_x(end,target,signal_forward=True,component=component,include_start=False)
    yield from _wire(target.offset(dz=-1),component,repeater("north"))
    yield from _wire(raw["SHIFT_COL"].offset(dz=1),component)
    yield from _wire(raw["SHIFT_COL"].offset(dz=2),component,repeater("south"))
    base=raw["SHIFT_COL"].offset(dz=3);yield from _tower(base,35,component)
    start=base.offset(1,35);end=start.offset(dx=2,dy=2)
    yield from iter_stair(start,end,axis="x",signal_forward=True,component=component)
    corner=end.offset(dz=4)
    yield from _wire_z(end,corner,signal_forward=True,component=component,include_start=False)
    last=corner.offset(dx=-3)
    yield from _wire_x(corner,last,signal_forward=True,component=component,include_start=False)
    yield from _wire(last.offset(dx=-1),component,repeater("west"))


def iter_controls(origin: Vec3, *, component: str = "input") -> Iterator[Placement]:
    kb=_keyboard_origin(origin)
    yield from iter_keyboard_matrix(kb,component=component)
    yield from iter_controller_panel(origin,component=component)
    for x in range(16):
        yield Placement(kb.offset(x,0,-1),SUPPORT,component)
        # Two rows beyond the keyboard roof form a walkable viewing lip.
        # Keep the previous odd-Z key-torch cells at z+15 untouched.
        for z in (16,17):
            yield Placement(kb.offset(x,0,z),SUPPORT,component)
    for distance in range(1,5):
        yield Placement(kb.offset(1,-distance,-1-distance),SUPPORT,component)
