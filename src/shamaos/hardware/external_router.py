from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Iterator

from ..model import Placement, Vec3
from .memory import DUST, SUPPORT, repeater
from .routing import _build_stair_points, iter_stair


@dataclass(frozen=True)
class ExternalNet:
    name: str
    source: Vec3
    sinks: tuple[Vec3, ...]


@dataclass(frozen=True)
class _Escape:
    endpoint: Vec3
    escape_z: int
    branch_x: int


def _escape_points(endpoint: Vec3, escape_z: int, branch_x: int, branch_y: int) -> list[Vec3]:
    """Geometry shared by input/output routes; only diode flow differs."""
    local=Vec3(endpoint.x,endpoint.y,escape_z)
    landing=Vec3(branch_x,branch_y-4,escape_z)
    plateau=landing.offset(dz=-2)
    high=Vec3(branch_x,branch_y,escape_z-6)
    return (
        [endpoint.offset(dz=z) for z in range(escape_z-endpoint.z+1)]
        + _build_stair_points(local,landing,axis="x")
        + [landing.offset(dz=-1),plateau]
        + _build_stair_points(plateau,high,axis="z")
    )


def _nearby_wire_points(p: Vec3):
    for dx,dz in ((0,0),(-1,0),(1,0),(0,-1),(0,1)):
        for dy in range(-1,2):
            yield p.offset(dx,dy,dz)


def _climb_headroom(points: list[Vec3]) -> set[Vec3]:
    # These positions must not become another wire: its support would cover
    # the lower stair point and prevent dust from climbing. Flat wires can
    # share X/Z at two-block vertical pitch when their climbs are separated.
    heads=set()
    for a,b in zip(points,points[1:]):
        if abs(a.y-b.y) == 1 and abs(a.x-b.x)+abs(a.z-b.z) == 1:
            heads.add((a if a.y < b.y else b).offset(dy=2))
    return heads


def _plan_escapes(points: Iterable[Vec3], branch_y: int, obstacles: Iterable[Placement] = ()) -> dict[Vec3,_Escape]:
    points=sorted(set(points),key=lambda p:(p.x,-p.y,p.z))
    terminals=set(points)
    placed={p.pos:p.block for p in obstacles}
    active={pos for pos,block in placed.items() if block != SUPPORT}
    reserved:set[Vec3]=set()
    reserved_heads:set[Vec3]=set()
    used_branch_x:set[int]=set()
    result={}
    for p in points:
        vertical=abs(branch_y-4-p.y)
        plateaus=max(0,(vertical+9)//10-1)
        candidate=p.x+vertical+plateaus*2
        while any(candidate+dx in used_branch_x for dx in range(-2,3)):
            candidate+=4
        used_branch_x.add(candidate)
        for lane in range(128):
            escape_z=p.z+6+6*lane
            geometry=_escape_points(p,escape_z,candidate,branch_y)
            heads=_climb_headroom(geometry)
            conflict=False
            if heads & (reserved|terminals) or set(geometry) & reserved_heads:
                continue
            for q in geometry:
                if q == p:
                    continue
                if q in placed or (q.offset(dy=-1) in placed and placed[q.offset(dy=-1)] != SUPPORT) or (q.offset(dy=2) in heads and q.offset(dy=1) in placed):
                    conflict=True
                    break
                for near in _nearby_wire_points(q):
                    if near in reserved or (near in terminals and near != p) or (near in active and near != p):
                        conflict=True
                        break
                if conflict:
                    break
            if not conflict:
                result[p]=_Escape(p,escape_z,candidate)
                reserved.update(geometry)
                reserved_heads.update(heads)
                break
        else:
            raise ValueError(
                f"cannot isolate physical endpoint {p}; dense endpoint placement "
                "needs a wider access corridor or a different placement"
            )
    return result


def _wire_x(
    start: Vec3,
    end: Vec3,
    *,
    signal_forward: bool,
    component: str,
) -> Iterator[Placement]:
    if start.y != end.y or start.z != end.z:
        raise ValueError("X wire requires equal Y/Z")
    step = 1 if end.x >= start.x else -1
    facing = "east" if (step > 0) == signal_forward else "west"
    length=abs(end.x-start.x)
    for n, x in enumerate(range(start.x, end.x + step, step)):
        p = Vec3(x, start.y, start.z)
        yield Placement(p.offset(dy=-1), SUPPORT, component)
        yield Placement(
            p,
            repeater(facing) if 0 < n < length and (n % 10 == 0 or n in (1,length-1)) else DUST,
            component,
        )


def _wire_z(
    start: Vec3,
    end: Vec3,
    *,
    signal_forward: bool,
    component: str,
    include_start: bool = True,
    include_end: bool = True,
) -> Iterator[Placement]:
    if start.x != end.x or start.y != end.y:
        raise ValueError("Z wire requires equal X/Y")
    step = 1 if end.z >= start.z else -1
    facing = "south" if (step > 0) == signal_forward else "north"
    length=abs(end.z-start.z)
    for n, z in enumerate(range(start.z, end.z + step, step)):
        if (n == 0 and not include_start) or (n == length and not include_end):
            continue
        p = Vec3(start.x, start.y, z)
        yield Placement(p.offset(dy=-1), SUPPORT, component)
        yield Placement(
            p,
            repeater(facing) if 0 < n < length and (n % 10 == 0 or n in (1,length-1)) else DUST,
            component,
        )


def _route_endpoint_to_branch(
    endpoint: Vec3,
    escape: _Escape,
    branch_y: int,
    track_z: int,
    *,
    source: bool,
    component: str,
) -> tuple[Vec3, Iterator[Placement]]:
    raise RuntimeError("helper is a type marker and is not called directly")


def _endpoint_path(
    endpoint: Vec3,
    escape_z: int,
    branch_x: int,
    branch_y: int,
    track_z: int,
    *,
    source: bool,
    component: str,
) -> Iterator[Placement]:
    """Source: endpoint -> branch plane -> track. Sink: reverse direction."""
    local_z = Vec3(endpoint.x, endpoint.y, escape_z)
    landing = Vec3(branch_x, branch_y-4, escape_z)
    plateau = Vec3(branch_x, branch_y-4, escape_z-2)
    high = Vec3(branch_x, branch_y, escape_z-6)
    track = Vec3(branch_x, branch_y, track_z)

    if source:
        yield from _wire_z(endpoint, local_z, signal_forward=True, component=component,include_end=False)
        yield from iter_stair(local_z, landing, axis="x", signal_forward=True, component=component)
        yield from _wire_z(landing, plateau, signal_forward=True, component=component,include_start=False,include_end=False)
        yield from iter_stair(plateau, high, axis="z", signal_forward=True, component=component)
        yield from _wire_z(high, track, signal_forward=True, component=component,include_start=False,include_end=False)
    else:
        yield from _wire_z(track, high, signal_forward=True, component=component,include_start=False,include_end=False)
        yield from iter_stair(high, plateau, axis="z", signal_forward=True, component=component)
        yield from _wire_z(plateau, landing, signal_forward=True, component=component,include_start=False,include_end=False)
        # Use the same physical stair as a source, reversing only diode flow.
        # Reversing its endpoints also moves any extra flat run to the bottom,
        # where it crosses adjacent cell terminals.
        yield from iter_stair(local_z, landing, axis="x", signal_forward=False, component=component)
        yield from _wire_z(local_z, endpoint, signal_forward=True, component=component,include_start=False)


def _trunk(
    source: Vec3,
    sinks: tuple[Vec3, ...],
    *,
    component: str,
) -> Iterator[Placement]:
    if any(p.y != source.y or p.z != source.z for p in sinks):
        raise ValueError("trunk points must share Y/Z")
    xs=[source.x,*[p.x for p in sinks]]
    lo,hi=min(xs),max(xs)
    endpoints=set(xs)

    # Plan directional repeaters before emitting, so each coordinate has one
    # block state. Dust-then-repeater overlays hid real collisions in writers.
    repeaters={}
    x=source.x+1
    while x<hi:
        while x in endpoints and x<hi:
            x+=1
        if x<hi:
            repeaters[x]=repeater("east")
            x+=10
    x=source.x-1
    while x>lo:
        while x in endpoints and x>lo:
            x-=1
        if x>lo:
            repeaters[x]=repeater("west")
            x-=10
    # Regenerate just before a descending sink transition. A route that is
    # individually under 15 dust blocks can still die across several joined
    # legs unless the junction budgets include the vertical stair.
    for sink in sinks:
        if sink.x != source.x:
            x=sink.x-(1 if sink.x>source.x else -1)
            if x not in endpoints:
                repeaters[x]=repeater("east" if sink.x>source.x else "west")
    for x in range(lo,hi+1):
        p=Vec3(x,source.y,source.z)
        yield Placement(p.offset(dy=-1),SUPPORT,component)
        yield Placement(p,repeaters.get(x,DUST),component)


def iter_external_router(
    nets: tuple[ExternalNet, ...] | list[ExternalNet],
    *,
    component: str = "system-interconnect",
    branch_y: int = 314,
    trunk_y: int = 318,
    track_spacing: int = 8,
    obstacles: Iterable[Placement] = (),
) -> Iterator[Placement]:
    """Route arbitrary long-distance nets without redstone crossings.

    Endpoint escape segments live on branch_y. Net trunks live on trunk_y.
    Every net gets a unique Z track, so branch lines cross trunks four blocks
    below them instead of electrically joining.
    """
    if trunk_y <= branch_y:
        raise ValueError("trunk_y must be above branch_y")
    nets=tuple(nets)
    if not nets:
        return

    all_points=[p for net in nets for p in (net.source,*net.sinks)]
    min_z=min(p.z for p in all_points)
    track_base=min_z-(len(nets)+1)*track_spacing-4096

    # Escape every endpoint a short fixed distance in Z before it rises
    # onto the branch plane. Logic-cell rows are spaced farther apart than
    # this, so different 2-D placement rows cannot share an escape wire.
    # Vertically stacked endpoints (such as display rows) remain isolated by
    # Y until their individually assigned X branch.
    escapes=_plan_escapes(all_points,branch_y,obstacles)

    for net_index,net in enumerate(nets):
        track_z=track_base+net_index*track_spacing
        comp=f"{component}:{net.name}"

        transition_run=trunk_y-branch_y
        branch_track_z=track_z+transition_run
        source_branch=Vec3(
            escapes[net.source].branch_x,
            branch_y,
            branch_track_z,
        )
        yield from _endpoint_path(
            net.source,
            escapes[net.source].escape_z,
            source_branch.x,
            branch_y,
            branch_track_z,
            source=True,
            component=comp,
        )

        # Approach the horizontal trunk along Z. Descending along X would put
        # the sink's first dust steps directly under the trunk's solid support
        # row, which overwrote them and disconnected even a single net.
        source_trunk=Vec3(
            source_branch.x,
            trunk_y,
            track_z,
        )
        yield from iter_stair(
            source_branch,
            source_trunk,
            axis="z",
            signal_forward=True,
            component=comp,
        )

        sink_trunks=[]
        for sink in net.sinks:
            sink_branch=Vec3(escapes[sink].branch_x,branch_y,branch_track_z)
            sink_trunk=Vec3(
                sink_branch.x,
                trunk_y,
                track_z,
            )
            sink_trunks.append(sink_trunk)

            # Signal runs source trunk -> sink trunk -> branch -> endpoint.
            yield from iter_stair(
                sink_trunk,
                sink_branch,
                axis="z",
                signal_forward=True,
                component=comp,
            )
            yield from _endpoint_path(
                sink,
                escapes[sink].escape_z,
                sink_branch.x,
                branch_y,
                branch_track_z,
                source=False,
                component=comp,
            )

        yield from _trunk(source_trunk,tuple(sink_trunks),component=comp)
