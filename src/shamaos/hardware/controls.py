from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from ..model import BlockState, Placement, Vec3
from .memory import DUST, SUPPORT, repeater
from .pulse import iter_pulse_stretcher, pulse_ports


BUTTON_FLOOR = BlockState.of("minecraft:stone_button", face="floor", facing="north", powered="false")
LEVER = BlockState.of("minecraft:lever", face="floor", facing="north", powered="false")
PANEL = BlockState.of("minecraft:stone")
KEY = BlockState.of("minecraft:smooth_quartz")
TORCH = BlockState.of("minecraft:redstone_torch", lit="true")
TORCH_OFF = BlockState.of("minecraft:redstone_torch", lit="false")
CONTROLLER_NAMES = ("UP","DOWN","LEFT","RIGHT","A","B","HOME","EXIT","EDITOR","FILES")
CONTROL_SIZE = (156, 6, 144)
KEYBOARD_OFFSET = Vec3(112, 0, 100)


@dataclass(frozen=True)
class ControlPorts:
    controller: tuple[Vec3, ...]
    keyboard_rows: tuple[Vec3, ...]
    keyboard_cols: tuple[Vec3, ...]
    power: Vec3
    reset: Vec3
    pause: Vec3
    step: Vec3


def controller_buttons(origin: Vec3) -> dict[str, Vec3]:
    result = {name: origin.offset(index*8,1,46) for index,name in enumerate(CONTROLLER_NAMES)}
    result.update(RESET=origin.offset(80,1,46), STEP=origin.offset(110,1,46))
    return result


def keyboard_buttons(origin: Vec3) -> tuple[tuple[Vec3, ...], ...]:
    kb = origin.offset(KEYBOARD_OFFSET.x,KEYBOARD_OFFSET.y,KEYBOARD_OFFSET.z)
    return tuple(tuple(kb.offset(col*5,1,row*5) for col in range(8)) for row in range(8))


def _wire(origin: Vec3, x: int, y: int, z: int, block=DUST, *, component: str):
    yield Placement(origin.offset(x,y-1,z),SUPPORT,component)
    yield Placement(origin.offset(x,y,z),block,component)


def _rise_three(origin: Vec3, *, component: str):
    """Input dust at (0,1,0), output dust at (3,4,0), two inversions."""
    yield from _wire(origin,1,1,0,repeater("east"),component=component)
    yield Placement(origin.offset(2,1,0),SUPPORT,component)
    yield Placement(origin.offset(2,2,0),TORCH,component)
    yield Placement(origin.offset(2,3,0),SUPPORT,component)
    yield Placement(origin.offset(2,4,0),TORCH_OFF,component)
    yield from _wire(origin,3,4,0,component=component)


def iter_controller_panel(origin: Vec3, *, component: str = "controller") -> Iterator[Placement]:
    # Independent 8-block lanes. No button, output or delayed tap occupies the
    # neighboring channel; the old UP output overlapped the DOWN button.
    for name, button in controller_buttons(origin).items():
        yield Placement(button.offset(dy=-1),PANEL,component)
        yield Placement(button,BUTTON_FLOOR,component)
        yield Placement(button.offset(dy=-1,dz=-1),SUPPORT,component)
        yield Placement(button.offset(dz=-1),repeater("north"),component)
        pulse_origin = button.offset(dy=-1,dz=-2)
        yield from iter_pulse_stretcher(pulse_origin,component=f"{component}:{name}")

    # Maintained raw power and pause requests, independently isolated.
    for x,name in ((90,"power"),(100,"pause")):
        yield Placement(origin.offset(x,0,46),PANEL,component)
        yield Placement(origin.offset(x,1,46),LEVER,component)
        yield from _wire(origin,x+1,1,46,repeater("east"),component=component)
        for dx in (2,3):
            yield from _wire(origin,x+dx,1,46,component=component)
        yield from _wire(origin,x+3,1,47,component=component)


def iter_keyboard_matrix(origin: Vec3, *, component: str = "keyboard") -> Iterator[Placement]:
    """Single-key 8x8 matrix with stretched, isolated row/column outputs.

    Buses flow toward their terminals, with repeaters between (never on) key
    taps. Row outputs rise above the row circuits before routing out; columns
    occupy another plane. Physical 10-RT button pulses are stretched before
    the clocked event FIFO. Wait for release before another key in vanilla.
    """
    for row in range(8):
        z = row*5+2
        start_x = -18-row*6
        for x in range(start_x,42):
            yield from _wire(origin,x,1,z,repeater("west") if x % 10 == 3 else DUST,component=component)
        pulse_origin = origin.offset(start_x,0,z)
        yield from iter_pulse_stretcher(pulse_origin,quarter_turns=3,component=f"{component}:row-{row}")
        _,out = pulse_ports(pulse_origin,quarter_turns=3)
        yield from _rise_three(out.offset(dy=-1),component=component)

    for col in range(8):
        # A torch is a source, not a wire-through connection. Keep the bus
        # beside the risers so earlier unpressed keys cannot interrupt it.
        x = col*5+3
        for z in range(-20,42):
            yield from _wire(origin,x,4,z,repeater("north") if z % 10 == 3 else DUST,component=component)
        yield from iter_pulse_stretcher(origin.offset(x,3,-20),component=f"{component}:col-{col}")

    for row in range(8):
        for col in range(8):
            x,z=col*5,row*5
            yield Placement(origin.offset(x,0,z),KEY,component)
            yield Placement(origin.offset(x,1,z),BUTTON_FLOOR,component)
            yield from _wire(origin,x,1,z+1,repeater("south"),component=component)
            # Column output is the original two-torch non-inverting riser.
            yield from _wire(origin,x+1,1,z,repeater("east"),component=component)
            yield Placement(origin.offset(x+2,1,z),SUPPORT,component)
            yield Placement(origin.offset(x+2,2,z),TORCH,component)
            yield Placement(origin.offset(x+2,3,z),SUPPORT,component)
            yield Placement(origin.offset(x+2,4,z),TORCH_OFF,component)


def control_ports(origin: Vec3) -> ControlPorts:
    buttons=controller_buttons(origin)
    ports={name:pulse_ports(button.offset(dy=-1,dz=-2))[1] for name,button in buttons.items()}
    kb=origin.offset(KEYBOARD_OFFSET.x,KEYBOARD_OFFSET.y,KEYBOARD_OFFSET.z)
    rows=[]
    for row in range(8):
        pulse_origin=kb.offset(-18-row*6,0,row*5+2)
        _,out=pulse_ports(pulse_origin,quarter_turns=3)
        rows.append(out.offset(3,3,0))
    cols=tuple(pulse_ports(kb.offset(col*5+3,3,-20))[1] for col in range(8))
    return ControlPorts(
        tuple(ports[name] for name in CONTROLLER_NAMES), tuple(rows), cols,
        origin.offset(93,1,47),ports["RESET"],origin.offset(103,1,47),ports["STEP"],
    )


def iter_controls(origin: Vec3, *, component: str = "input") -> Iterator[Placement]:
    yield from iter_controller_panel(origin,component=component)
    yield from iter_keyboard_matrix(origin.offset(KEYBOARD_OFFSET.x,KEYBOARD_OFFSET.y,KEYBOARD_OFFSET.z),component=component)
