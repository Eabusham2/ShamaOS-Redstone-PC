from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from ..model import BlockState, Placement, Vec3
from .memory import DUST, SUPPORT, comparator, repeater


LEVER = BlockState.of(
    "minecraft:lever", face="floor", facing="north", powered="false"
)
LAMP = BlockState.of("minecraft:redstone_lamp", lit="false")


@dataclass(frozen=True)
class ClockPorts:
    enable: Vec3
    clock: Vec3
    indicator: Vec3


def clock_ports(origin: Vec3) -> ClockPorts:
    return ClockPorts(
        enable=origin.offset(0,1,0),
        clock=origin.offset(16,1,0),
        indicator=origin.offset(18,1,0),
    )


def iter_clock(
    origin: Vec3,
    *,
    component: str = "system-clock",
) -> Iterator[Placement]:
    """Comparator-feedback oscillator gated by the physical power switch.

    The enable terminal feeds the comparator rear input. In subtract mode the
    comparator's output is fed back into a side input, producing a stable
    redstone clock while enabled. Turning power off removes the rear input and
    stops the oscillator. The clock output is isolated by repeaters.
    """
    # Enable input to comparator rear.
    for x in range(0,5):
        yield Placement(origin.offset(x,0,0),SUPPORT,component)
        yield Placement(
            origin.offset(x,1,0),
            repeater("east") if x==3 else DUST,
            component,
        )

    # Comparator points east. Rear input at x=4, output x=6.
    yield Placement(origin.offset(5,0,0),SUPPORT,component)
    yield Placement(
        origin.offset(5,1,0),
        comparator("east",powered=False),
        component,
    )

    # Output isolation and feedback loop around the north side.
    yield Placement(origin.offset(6,0,0),SUPPORT,component)
    yield Placement(origin.offset(6,1,0),repeater("east"),component)

    for x,z in (
        (7,0),(8,0),(8,-1),(8,-2),(7,-2),(6,-2),(5,-2),(4,-2),(4,-1)
    ):
        yield Placement(origin.offset(x,0,z),SUPPORT,component)
        yield Placement(origin.offset(x,1,z),DUST,component)

    # Strong side feedback into the comparator.
    yield Placement(origin.offset(5,0,-1),SUPPORT,component)
    yield Placement(origin.offset(5,1,-1),repeater("south"),component)

    # Clock output travels east through repeaters.
    for x in range(7,17):
        yield Placement(origin.offset(x,0,0),SUPPORT,component)
        if x in (8,15):
            yield Placement(origin.offset(x,1,0),repeater("east"),component)
        else:
            yield Placement(origin.offset(x,1,0),DUST,component)

    # Visual clock indicator.
    yield Placement(origin.offset(17,0,0),SUPPORT,component)
    yield Placement(origin.offset(17,1,0),repeater("east"),component)
    yield Placement(origin.offset(18,1,0),LAMP,component)
