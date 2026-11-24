"""Physical launch channels and one-shot row token for the 16 x 32 input bank."""
from __future__ import annotations

from experiments.input_storage_bus.full_width import fixture as terminal_fixture
from shamaos.hardware.logic import TEMPLATES
from shamaos.hardware.memory import DUST, SUPPORT, repeater
from shamaos.model import BlockState, Placement, Vec3

LEVER = BlockState.of("minecraft:lever", face="floor", facing="north", powered="false")


def build(*, guard_extra=0):
    rows, width = 16, 32
    base, original_inputs, probes = terminal_fixture(rows, guard_extra)
    measured = {name: Vec3(*pos) for name, pos in probes.items()}
    solids = {p.pos: p for p in base}
    actuators = {}
    def place(pos, state, label):
        previous = solids.get(pos)
        if previous is not None and previous.block != state:
            raise ValueError(f"physical overlap at {pos}: {label} against {previous}")
        solids[pos] = Placement(pos, state, label)
    def wire(x, y, z, state=DUST, label="launch-route"):
        p = Vec3(x, y, z)
        place(p.offset(dy=-1), SUPPORT, label)
        place(p, state, label)
    def lever(label, pos):
        wire(pos.x, pos.y, pos.z, LEVER, label)
        actuators[label] = pos
    def external(name):
        return Vec3(*original_inputs[name]).offset(dz=1)
    lever("enable", external("enable"))
    lever("seed", external("s0"))
    for bit, label in ((0, "near"), (31, "far")):
        lever(f"word{bit}", external(label))
        for pin in ("C", "D", "Q"):
            measured[f"launch{bit}_{pin}"] = measured[f"{label}_{pin}"]
    # The clock departs from an existing dust tap on the actual oscillator line.
    # Place its below-register spine on its own plane. Every tap is directional.
    for x in range(132, 148):
        wire(x, 43, 52, repeater("east") if x in (132, 144) else DUST)
    for z in range(51, 29, -1):
        y = max(34, 43 - (52-z))
        wire(147,y,z, repeater("north") if z in (42,31) else DUST)
    first_terminal = measured["terminal0"]
    def tap(bit):
        return first_terminal.x + 5*bit - 8
    tap_positions = {tap(bit) for bit in range(1,31)}
    last = 147
    refreshers = []
    for x in range(148, max(tap_positions)+1):
        renew = x-last>=13 and x not in tap_positions
        wire(x,34,30,repeater("east") if renew else DUST)
        if renew:
            last = x
            refreshers.append(x)
    # The clock spine contributes one tick per crossed repeater. The highest
    # tier's data descent has one further repeater. Balance the combined
    # register-to-terminal paths by adding delay on existing clock risers.
    latency = {bit: sum(xx < tap(bit) for xx in refreshers) + int(bit % 4 == 3)
               for bit in range(1,31)}
    slowest = max(latency.values())
    for bit in range(1,31):
        terminal = measured[f"terminal{bit}"]
        x, y, z = terminal.x, 52 + 6*(bit%4), -20
        origin = Vec3(x-16,y-1,z)
        cell = TEMPLATES["DFF"]
        for b in cell.emitter(origin, f"launch-{bit}"):
            place(b.pos,b.block,b.component)
        for pin, delta in cell.pins.items():
            measured[f"launch{bit}_{pin}"] = origin.offset(delta.x,delta.y,delta.z)
        lever(f"word{bit}",origin.offset(-1,1,0))
        # A separate riser two blocks east of C clears neighboring Q routes.
        # Repeaters are only placed on level spans; dust conducts up/down stairs.
        extra = slowest-latency[bit]
        if not 0<=extra<=24: raise ValueError("launch trim outside available repeaters")
        for zz in range(29, z+2, -1):
            
            # Level landing steps in a long climb keep every dust span powered.
            steps = []
            for n in range(y-34):
                steps.append(26-n-n//8)
            yy = 34 + sum(zz <= k for k in steps)
            landing = zz in (29, 27, *(steps[n]-1 for n in (7,15,23,31) if n<len(steps))) or (zz < steps[-1]-1 and (zz+17)%9 == 0 and zz != z+3)
            delay = 1+min(3,extra) if landing else 1
            if landing: extra-=delay-1
            wire(tap(bit),yy,zz,repeater("north",delay=delay) if landing else DUST)
        if extra:
            raise ValueError("insufficient routed clock-delay capacity")
        for xx in range(x-10,x-7):
            wire(xx,y,z+2,DUST)
        wire(x-10,y,z+1,repeater("north"))
        # The original bank retains both height planes, row devices and columns.
        # Step the launched word down to the incoming bus before its first cell.
        for zz in range(z+1,94):
            upper_steps = [38+n+2*(n//7) for n in range(y-49)]
            lower_steps = [81+n+2*(n//6) for n in range(9)]
            yy = y - sum(zz >= k for k in upper_steps+lower_steps)
            landings = [upper_steps[n]+1 for n in range(6,len(upper_steps),7)] + [lower_steps[5]+1]
            refresh = zz in (-19,-9,1,11,21,31,67,77,92,*landings) or (zz > upper_steps[-1]+1 and zz in (46,56))
            wire(x,yy,zz,repeater("south") if refresh else DUST)
        measured[f"clocktap{bit}"] = Vec3(tap(bit),34,30)
    # Each row register advances its Q to the following D. The last has no
    # successor. A single seed pulse scans the rows exactly once on a running
    # oscillator; upstream event admission and bank handoff are separate.
    for r in range(15):
        now = measured[f"sel{r}_Q"]
        later = measured[f"sel{r+1}_D"]
        x,z = now.x+2, now.z
        for dz in range(1,8):
            wire(x,max(37,43-dz),z+dz, DUST, "row-token")
        for xx in range(x-1,132,-1):
            wire(xx,37,z+7,repeater("west") if xx in (x-1,x-12,x-22) else DUST, "row-token")
        for zz in range(z+8,later.z+1):
            wire(133,37,zz,repeater("south") if zz==z+10 else DUST, "row-token")
        for xx in range(134,later.x):
            wire(xx,min(43,37+xx-133),later.z,DUST,"row-token")
    return list(solids.values()), actuators, measured
