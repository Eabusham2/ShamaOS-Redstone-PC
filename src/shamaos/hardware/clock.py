from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from ..model import BlockState, Placement, Vec3
from .memory import DUST, SUPPORT, redstone_wire, repeater
from .logic import emit_and, emit_dff, emit_nand, emit_not
from .routing import iter_stair


LAMP = BlockState.of("minecraft:redstone_lamp", lit="false")
CLOCK_HALF_PERIOD = 35
CLOCK_BOUNDS = ((0, 0, -30), (96, 10, 12))


def system_clock_origin(console_origin: Vec3) -> Vec3:
    """Keep the clock and its escapes north of the compact console volume."""
    return console_origin.offset(180, 0, -48)


@dataclass(frozen=True)
class ClockPorts:
    enable: Vec3
    hold: Vec3
    clock: Vec3
    indicator: Vec3


def clock_ports(origin: Vec3) -> ClockPorts:
    return ClockPorts(origin.offset(0,1,0),origin.offset(8,1,0),origin.offset(90,4,0),origin.offset(92,4,0))


def _clock_delay_line(start: Vec3, stages: int, *, idle_high: bool, component: str) -> Iterator[Placement]:
    """OR isolated copies of a pulse on one physical return bus.

    A four-tick repeater drives the input. Consecutive taps add three ticks
    on the outbound line and at most one tick on the return, so copies of
    even the shortest admitted pulse overlap. The output is two blocks
    west and three blocks south of the input.
    """
    def cell(dx, dz, state):
        point = start.offset(dx, 0, dz)
        yield Placement(point.offset(dy=-1), SUPPORT, component)
        yield Placement(point, state, component)

    tap_wire = redstone_wire(power=15) if idle_high else DUST
    tap_diode = repeater("south", powered=idle_high)
    delay = repeater("east", delay=3, powered=idle_high)
    for stage in range(stages + 1):
        x = 2 * stage
        yield from cell(x, 0, tap_wire)
        yield from cell(x, 1, tap_diode)
        yield from cell(x, 2, tap_wire)
        if stage < stages:
            yield from cell(x + 1, 0, delay)

    refresh = {-1, *range(9, 2 * stages + 1, 10)}
    strong = {x - 1 for x in refresh}
    for x in range(-2, 2 * stages + 1):
        if x in refresh:
            state = repeater("west", powered=idle_high)
        elif idle_high:
            # Each even tap's side wire supplies 14. A westbound repeater
            # instead supplies 15 to its first dust and 14 to its second.
            if x in strong:
                power = 15
            elif x % 2 == 0 or x + 1 in strong:
                power = 14
            else:
                power = 13
            state = redstone_wire(power=power)
        else:
            state = DUST
        yield from cell(x, 3, state)


def iter_clock(origin: Vec3, *, component: str = "system-clock") -> Iterator[Placement]:
    """Phase-safe clock with a separate clean-shutdown hold request.

    A NAND/repeater oscillator supplies 35-RT high and low phases.
    A DFF samples (enable OR hold) on the phase that starts the exported low
    interval. Its delayed, isolated Q reaches the output AND gate while that
    interval is still low. Power changes therefore admit or suppress complete
    clock pulses; a combinational enable on the oscillator itself would clip
    the last pulse and violate the storage cells' minimum high time.

    ``hold`` comes from the SoC powered latch and remains asserted until its
    storage shutdown completes. The oscillator remains enabled by the request
    OR the sampled enable. A two-stage timer keeps each change at the oscillator
    input stable long enough for its 35-tick feedback line to empty. This prevents
    a rapid second request from starting another wave in the same ring. The
    output gate withdraws on its safe phase. The output and oscillator settle,
    allowing a faithful snapshot without dropping scheduled work. On restart
    the gate suppresses the initial phase until a full pulse can be delivered.
    Actual MCHPRS tests cover every start/stop phase; distribution delay and
    skew beyond these local pins remain separate.
    """
    def wire(x,z,block=DUST):
        yield Placement(origin.offset(x,0,z),SUPPORT,component)
        yield Placement(origin.offset(x,1,z),block,component)

    # Isolated power/hold OR, followed by the gate-enable register's data path.
    for x in (0,8):
        yield from wire(x,0)
        yield from wire(x,-1,repeater("north"))
    for x in range(9):
        yield from wire(x,-2)
    for z in range(-3,-20,-1):
        yield from wire(0,z,repeater("north") if z in (-9,-17) else DUST)
    yield from emit_dff(origin.offset(0,0,-20),component)

    # Eight delay-four repeaters plus the NAND's three ticks give 35 RT.
    yield from emit_nand(origin.offset(16,0,0),component)
    for z in range(1,13):
        yield from wire(32,z,repeater("south",delay=4) if z in (1,3,5,7) else DUST)
    yield from wire(31,12,repeater("west",delay=4))
    for x in range(24,31):
        yield from wire(x,12)
    for z in range(11,0,-1):
        yield from wire(24,z,repeater("north",delay=4) if z in (11,9,7) else DUST)

    # NAND Y is the falling phase of the clock presented to the output gate.
    # Its route to the DFF clock is independent of both data and register Q.
    for z in range(-5,-15,-1):
        yield from wire(32,z,repeater("north") if z == -5 else DUST)
    for x in range(31,5,-1):
        yield from wire(x,-14,repeater("west") if x in (31,17) else DUST)
    for z in range(-15,-20,-1):
        yield from wire(6,z,repeater("north") if z == -15 else DUST)

    # Clock waveform to A of the output AND. Two directional repeaters isolate
    # the gate and preserve the complete 35-RT interval in either state.
    for x in (33,34,35):
        yield from wire(x,0)
    yield from emit_not(origin.offset(36,0,0),component)
    for x in range(41,64):
        yield from wire(x,0,repeater("east") if x in (48,60) else DUST)
    # Initialize the disabled AND gate consistently. A default-lit final
    # inverter otherwise emits a three-tick power-on glitch; a long route
    # delivers that glitch long after the local clock appears settled.
    for placement in emit_and(origin.offset(64,0,0),component):
        x=placement.pos.x-origin.x
        z=placement.pos.z-origin.z
        block=placement.block
        if block.name == "minecraft:redstone_wire" and (
            z == -4 or (x == 80 and -3 <= z <= 0) or (x == 81 and z == 0)
        ):
            block=redstone_wire(power=15)
        elif (x,z) in ((73,-4),(82,0)) and block.name == "minecraft:repeater":
            block=repeater("east",powered=True)
        elif (x,z) == (84,0) and block.name == "minecraft:redstone_wall_torch":
            block=BlockState.of("minecraft:redstone_wall_torch",facing="east",lit="false")
        yield Placement(placement.pos,block,component)

    # Registered enable reaches B from the south, outside the AND's internal
    # negative-Z circuitry. Its propagation is shorter than the low phase.
    for x in range(17,97):
        yield from wire(x,-20,repeater("east") if x in (26,38,50,62,74,86) else DUST)
    for z in range(-19,5):
        yield from wire(96,z,repeater("south") if z in (-19,-7,3) else DUST)
    for x in range(95,71,-1):
        yield from wire(x,4,repeater("west") if x in (95,83) else DUST)
    for z in (3,2,1):
        yield from wire(72,z,repeater("north") if z == 3 else DUST)

    yield from wire(86,0,repeater("east"))
    # Raise the exported signal above the enable return at z+4. A terminal on
    # y+1 looked correct alone but its standard +Z escape shorted that return.
    for y,block in (
        (1,SUPPORT),
        (2,BlockState.of("minecraft:redstone_torch",lit="true")),
        (3,SUPPORT),
        (4,BlockState.of("minecraft:redstone_torch",lit="false")),
    ):
        yield Placement(origin.offset(87,y,0),block,component)
    for x,block in ((88,DUST),(89,repeater("east")),(90,DUST),(91,repeater("east"))):
        yield Placement(origin.offset(x,3,0),SUPPORT,component)
        yield Placement(origin.offset(x,4,0),block,component)
    yield Placement(origin.offset(92,4,0),LAMP,component)

    # Keep the oscillator enabled until the registered output gate has
    # withdrawn on its safe phase. Either an active request or the last
    # registered enable sustains the loop. With both low it settles high at
    # NAND Y, so the exported waveform remains low with no pending ticks.
    def at(x, y, z, block=DUST):
        yield Placement(origin.offset(x, y - 1, z), SUPPORT, component)
        yield Placement(origin.offset(x, y, z), block, component)

    for x, z, facing in ((4, -1, "south"), (20, -21, "north")):
        yield from at(x, 1, z, repeater(facing))
        tower_z = 0 if x == 4 else -22
        for y in range(1, 9):
            block = (SUPPORT if y % 2 else BlockState.of("minecraft:redstone_torch",
                     lit="true" if y % 4 == 2 else "false"))
            yield Placement(origin.offset(x, y, tower_z), block, component)

    for x in range(5, 24):
        yield from at(x, 8, 0, repeater("east") if x in (6, 16, 22) else DUST)
    for z in (-1, -2, -3):
        yield from at(23, 8, z)
    for x in (24, 25, 26):
        yield from at(x, 8, -3)
    for x in range(21, 27):
        yield from at(x, 8, -22, repeater("east") if x == 22 else DUST)
    for z in range(-21, -3):
        yield from at(26, 8, z, repeater("south") if z in (-20, -10, -4) else DUST)

    # Hold active requests across 30 three-tick stages, then hold inactive
    # requests across 14 stages by stretching their inverted polarity. The
    # shorter line also delays turn-on, so the difference must exceed one
    # ring transit. Both retained intervals exceed 35 ticks. Short off/on
    # changes can no longer inject multiple transitions into the feedback.
    # Both lines lie above the existing clock and preserve its X/Z bounds.
    yield from at(27, 8, -3, repeater("east", delay=4))
    for x in (28, 29):
        yield from at(x, 8, -3)
    yield from _clock_delay_line(origin.offset(30, 8, -3), 30, idle_high=False, component=component)
    yield from at(28, 8, 1)
    yield from at(28, 8, 2, repeater("south"))
    yield from at(28, 8, 3, SUPPORT)
    yield Placement(origin.offset(28, 9, 3), BlockState.of("minecraft:redstone_torch", lit="true"), component)
    yield from at(29, 9, 3, repeater("east", delay=4, powered=True))
    yield from _clock_delay_line(origin.offset(30, 9, 3), 14, idle_high=True, component=component)
    yield from at(27, 9, 6, repeater("west", powered=True))
    yield from at(26, 9, 6, SUPPORT)
    yield Placement(origin.offset(26, 10, 6), BlockState.of("minecraft:redstone_torch", lit="false"), component)
    yield from iter_stair(origin.offset(25, 10, 6), origin.offset(16, 1, 6),
                          axis="x", signal_forward=True, component=component)
    for z in range(5, 0, -1):
        yield from at(16, 1, z, repeater("north") if z in (5, 1) else DUST)
