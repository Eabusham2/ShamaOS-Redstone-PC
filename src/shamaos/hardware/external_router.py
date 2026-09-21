from __future__ import annotations

from dataclasses import dataclass
from collections import defaultdict
from typing import Iterator

from ..model import Placement, Vec3
from .memory import DUST, SUPPORT, repeater
from .routing import iter_stair


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
    for n, x in enumerate(range(start.x, end.x + step, step)):
        p = Vec3(x, start.y, start.z)
        yield Placement(p.offset(dy=-1), SUPPORT, component)
        yield Placement(
            p,
            repeater(facing) if n and n % 10 == 0 else DUST,
            component,
        )


def _wire_z(
    start: Vec3,
    end: Vec3,
    *,
    signal_forward: bool,
    component: str,
) -> Iterator[Placement]:
    if start.x != end.x or start.y != end.y:
        raise ValueError("Z wire requires equal X/Y")
    step = 1 if end.z >= start.z else -1
    facing = "south" if (step > 0) == signal_forward else "north"
    for n, z in enumerate(range(start.z, end.z + step, step)):
        p = Vec3(start.x, start.y, z)
        yield Placement(p.offset(dy=-1), SUPPORT, component)
        yield Placement(
            p,
            repeater(facing) if n and n % 10 == 0 else DUST,
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
    high = Vec3(branch_x, branch_y, escape_z)
    track = Vec3(branch_x, branch_y, track_z)

    if source:
        yield from _wire_z(endpoint, local_z, signal_forward=True, component=component)
        yield from iter_stair(local_z, high, axis="x", signal_forward=True, component=component)
        yield from _wire_z(high, track, signal_forward=True, component=component)
    else:
        yield from _wire_z(track, high, signal_forward=True, component=component)
        yield from iter_stair(high, local_z, axis="x", signal_forward=True, component=component)
        yield from _wire_z(local_z, endpoint, signal_forward=True, component=component)


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

    # Place dust first.
    for x in range(lo,hi+1):
        p=Vec3(x,source.y,source.z)
        yield Placement(p.offset(dy=-1),SUPPORT,component)
        yield Placement(p,DUST,component)

    # Overlay directional repeaters moving away from the source. The world
    # writer applies later placements last for the same coordinate.
    x=source.x+10
    while x<hi:
        while x in endpoints and x<hi:
            x+=1
        if x<hi:
            yield Placement(Vec3(x,source.y,source.z),repeater("east"),component)
            x+=10
    x=source.x-10
    while x>lo:
        while x in endpoints and x>lo:
            x-=1
        if x>lo:
            yield Placement(Vec3(x,source.y,source.z),repeater("west"),component)
            x-=10


def iter_external_router(
    nets: tuple[ExternalNet, ...] | list[ExternalNet],
    *,
    component: str = "system-interconnect",
    branch_y: int = 314,
    trunk_y: int = 318,
    track_spacing: int = 8,
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
    escape_z_by_point: dict[Vec3,int]={}
    used_branch_x:set[int]=set()
    branch_x_by_point:dict[Vec3,int]={}

    unique_points=sorted(set(all_points),key=lambda p:(p.x,p.y,p.z))
    for idx,p in enumerate(unique_points):
        escape_z=p.z+12
        escape_z_by_point[p]=escape_z

        run=abs(branch_y-p.y)+32
        candidate=p.x+run+(idx%8)*4
        while candidate in used_branch_x:
            candidate+=4
        used_branch_x.add(candidate)
        branch_x_by_point[p]=candidate

    for net_index,net in enumerate(nets):
        track_z=track_base+net_index*track_spacing
        comp=f"{component}:{net.name}"

        source_branch=Vec3(
            branch_x_by_point[net.source],
            branch_y,
            track_z,
        )
        yield from _endpoint_path(
            net.source,
            escape_z_by_point[net.source],
            source_branch.x,
            branch_y,
            track_z,
            source=True,
            component=comp,
        )

        # Rise four blocks at the track to the trunk plane. Track Z is unique,
        # so these vertical transitions cannot collide with another net.
        source_trunk=Vec3(
            source_branch.x+(trunk_y-branch_y)+4,
            trunk_y,
            track_z,
        )
        yield from iter_stair(
            source_branch,
            source_trunk,
            axis="x",
            signal_forward=True,
            component=comp,
        )

        sink_trunks=[]
        for sink in net.sinks:
            sink_branch=Vec3(branch_x_by_point[sink],branch_y,track_z)
            sink_trunk=Vec3(
                sink_branch.x+(trunk_y-branch_y)+4,
                trunk_y,
                track_z,
            )
            sink_trunks.append(sink_trunk)

            # Signal runs source trunk -> sink trunk -> branch -> endpoint.
            yield from iter_stair(
                sink_trunk,
                sink_branch,
                axis="x",
                signal_forward=True,
                component=comp,
            )
            yield from _endpoint_path(
                sink,
                escape_z_by_point[sink],
                sink_branch.x,
                branch_y,
                track_z,
                source=False,
                component=comp,
            )

        yield from _trunk(source_trunk,tuple(sink_trunks),component=comp)
