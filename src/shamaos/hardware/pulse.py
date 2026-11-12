"""Physical tapped-delay pulse stretching for slow-clock user inputs."""
from __future__ import annotations

from typing import Iterator

from ..model import BlockState, Placement, Vec3
from .memory import DUST, SUPPORT, repeater


PULSE_TAPS = 20


def rotate_point(p: Vec3, quarter_turns: int) -> Vec3:
    x, z = p.x, p.z
    for _ in range(quarter_turns % 4):
        x, z = -z, x
    return Vec3(x, p.y, z)


def rotate_block(block: BlockState, quarter_turns: int) -> BlockState:
    directions = ("north", "east", "south", "west")
    turn = quarter_turns % 4
    def direction(name):
        return directions[(directions.index(name) + turn) % 4]
    properties = {}
    for key, value in block.properties:
        if key == "facing" and value in directions:
            value = direction(value)
        if key in directions:
            key = direction(key)
        properties[key] = value
    return BlockState.of(block.name, **properties)


def pulse_ports(origin: Vec3, *, quarter_turns: int = 0, output_inversions: int = 0) -> tuple[Vec3, Vec3]:
    if output_inversions < 0 or output_inversions % 2:
        raise ValueError("pulse output riser must use an even number of inversions")
    a = rotate_point(Vec3(0, 1, 0), quarter_turns)
    y = rotate_point(Vec3(3,2*output_inversions,3) if output_inversions else Vec3(3,1,2),quarter_turns)
    return origin.offset(a.x, a.y, a.z), origin.offset(y.x, y.y, y.z)


def iter_pulse_stretcher(origin: Vec3, *, quarter_turns: int = 0, taps: int = PULSE_TAPS, output_inversions: int = 0, component: str = "input-pulse") -> Iterator[Placement]:
    """OR overlapping delayed copies of a ten-RT stone-button pulse.

    A delay-four repeater separates each tap. An isolated repeater joins every
    tap to a regenerated return bus. Twenty taps extend a single physical
    press beyond one period of the raw oscillator; this is hardware, not a
    synthetic software event. The matrix remains single-key-at-a-time: release
    all stretched lines before the next key when running at vanilla speed.
    """
    if taps < 1:
        raise ValueError("pulse stretcher needs at least one delay tap")
    pulse_ports(origin,quarter_turns=quarter_turns,output_inversions=output_inversions)

    def place(x,y,z,block):
        local=rotate_point(Vec3(x,y,z),quarter_turns)
        return Placement(origin.offset(local.x,local.y,local.z),rotate_block(block,quarter_turns),component)

    def emit(x, z, block):
        for y, state in ((0, SUPPORT), (1, block)):
            yield place(x,y,z,state)

    for stage in range(taps + 1):
        z = -2 * stage
        yield from emit(0, z, DUST)
        yield from emit(1, z, repeater("east"))
        yield from emit(2, z, DUST)
        if stage < taps:
            yield from emit(0, z-1, repeater("north", delay=4))
    for z in range(-2*taps, 3):
        # Refresh the output itself as well: a nonzero but weak tail would
        # disappear before the first repeater on an external route.
        refresh = z == 1 or (z < 0 and z % 10 == 1)
        block=repeater("south") if refresh else DUST
        if z == 2 and output_inversions:
            block=SUPPORT
        yield from emit(3,z,block)
    if output_inversions:
        # The final repeater strongly drives the tower's base. Two inversions
        # preserve the pulse; four lift it above a second crossing plane.
        for inversion in range(output_inversions):
            y=2+2*inversion
            yield place(3,y,2,BlockState.of("minecraft:redstone_torch",lit=str(inversion % 2 == 0).lower()))
            if inversion+1 < output_inversions:
                yield place(3,y+1,2,SUPPORT)
        yield place(3,2*output_inversions-1,3,SUPPORT)
        yield place(3,2*output_inversions,3,DUST)
