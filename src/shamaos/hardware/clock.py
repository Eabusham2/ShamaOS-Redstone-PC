from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from ..model import BlockState, Placement, Vec3
from .memory import DUST, SUPPORT, comparator, repeater


LAMP = BlockState.of("minecraft:redstone_lamp", lit="false")


@dataclass(frozen=True)
class ClockPorts:
    enable: Vec3
    clock: Vec3
    indicator: Vec3


def clock_ports(origin: Vec3) -> ClockPorts:
    return ClockPorts(
        enable=origin.offset(0,1,0),
        clock=origin.offset(18,1,0),
        indicator=origin.offset(20,1,0),
    )


def iter_clock(
    origin: Vec3,
    *,
    component: str = "system-clock",
) -> Iterator[Placement]:
    """Comparator-feedback main clock with safe delayed power-off.

    The physical power signal splits in two:
      * a direct isolated repeater starts the clock immediately;
      * a long delay-4 repeater path holds the clock enable high for many
        redstone ticks after the power lever drops.

    During that holdover, shama_soc sees power_switch=0 and receives multiple
    synchronous reset clocks. Only after reset has settled does the oscillator
    stop. This prevents stale-state resume on the next power-on.
    """
    # Power input terminal.
    yield Placement(origin.offset(0,0,0),SUPPORT,component)
    yield Placement(origin.offset(0,1,0),DUST,component)

    # Immediate path into the enable OR junction.
    yield Placement(origin.offset(1,0,0),SUPPORT,component)
    yield Placement(origin.offset(1,1,0),repeater("east"),component)
    yield Placement(origin.offset(2,0,0),SUPPORT,component)
    yield Placement(origin.offset(2,1,0),DUST,component)

    # Delayed falling-edge path: north eight delay-4 repeaters.
    for z in range(-1,-9,-1):
        yield Placement(origin.offset(0,0,z),SUPPORT,component)
        yield Placement(
            origin.offset(0,1,z),
            repeater("north",delay=4),
            component,
        )

    # Turn east outside the direct path.
    for x in range(1,3):
        yield Placement(origin.offset(x,0,-9),SUPPORT,component)
        yield Placement(origin.offset(x,1,-9),DUST,component)

    # Return south through eight more delay-4 repeaters; the last one feeds
    # the same OR junction at x=2,z=0.
    for z in range(-8,0):
        yield Placement(origin.offset(2,0,z),SUPPORT,component)
        yield Placement(
            origin.offset(2,1,z),
            repeater("south",delay=4),
            component,
        )

    # OR-junction output to the comparator rear input.
    for x in range(3,6):
        yield Placement(origin.offset(x,0,0),SUPPORT,component)
        yield Placement(
            origin.offset(x,1,0),
            repeater("east") if x==4 else DUST,
            component,
        )

    # Comparator oscillator.
    yield Placement(origin.offset(6,0,0),SUPPORT,component)
    yield Placement(
        origin.offset(6,1,0),
        comparator("east",powered=False),
        component,
    )
    yield Placement(origin.offset(7,0,0),SUPPORT,component)
    yield Placement(origin.offset(7,1,0),repeater("east"),component)

    # Feedback loop on the north side.
    for x,z in (
        (8,0),(9,0),(9,-1),(9,-2),(8,-2),(7,-2),(6,-2),(5,-2),(5,-1)
    ):
        yield Placement(origin.offset(x,0,z),SUPPORT,component)
        yield Placement(origin.offset(x,1,z),DUST,component)

    yield Placement(origin.offset(6,0,-1),SUPPORT,component)
    yield Placement(origin.offset(6,1,-1),repeater("south"),component)

    # Isolated main clock output.
    for x in range(8,19):
        yield Placement(origin.offset(x,0,0),SUPPORT,component)
        if x in (10,17):
            yield Placement(origin.offset(x,1,0),repeater("east"),component)
        else:
            yield Placement(origin.offset(x,1,0),DUST,component)

    yield Placement(origin.offset(19,0,0),SUPPORT,component)
    yield Placement(origin.offset(19,1,0),repeater("east"),component)
    yield Placement(origin.offset(20,1,0),LAMP,component)
