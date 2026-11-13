from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Iterator, Mapping

from ..model import Box, Placement, Vec3
from .memory import DUST, SUPPORT, repeater
from .routing import _build_stair_points, iter_stair


@dataclass(frozen=True)
class ExternalNet:
    name: str
    source: Vec3
    sinks: tuple[Vec3, ...]
    # In wired-OR mode `source` is the central receiving terminal and `sinks`
    # are independently isolated leaf drivers. All route flow is reversed.
    wired_or: bool = False


@dataclass(frozen=True)
class _Escape:
    endpoint: Vec3
    escape_z: int
    branch_x: int
    escape_x: int | None = None
    wall_lead_z: int | None = None
    branch_direction: int = -1


def _escape_points(endpoint: Vec3, escape_z: int, branch_x: int, branch_y: int, escape_x: int | None = None, wall_lead_z: int | None = None, branch_direction: int = -1) -> list[Vec3]:
    """Geometry shared by input/output routes; only diode flow differs."""
    landing=Vec3(branch_x,branch_y-4,escape_z)
    plateau=landing.offset(dz=branch_direction*2)
    high=Vec3(branch_x,branch_y,escape_z+branch_direction*6)
    if wall_lead_z is not None:
        corner=Vec3(branch_x,endpoint.y,wall_lead_z)
        direction=1 if wall_lead_z >= endpoint.z else -1
        prefix=([endpoint.offset(dz=z) for z in range(0,wall_lead_z-endpoint.z+direction,direction)]
                + [Vec3(x,endpoint.y,wall_lead_z) for x in range(endpoint.x+1,branch_x+1)]
                + _build_stair_points(corner,landing,axis="z")[1:])
    elif escape_x is None:
        local=Vec3(endpoint.x,endpoint.y,escape_z)
        direction=1 if escape_z >= endpoint.z else -1
        prefix=([endpoint.offset(dz=z) for z in range(0,escape_z-endpoint.z+direction,direction)]
                + _build_stair_points(local,landing,axis="x"))
    else:
        local=Vec3(escape_x,endpoint.y,endpoint.z)
        corner=Vec3(escape_x,branch_y-4,escape_z)
        outward=1 if escape_x >= endpoint.x else -1
        across=1 if branch_x >= escape_x else -1
        prefix=([Vec3(x,endpoint.y,endpoint.z) for x in range(endpoint.x,escape_x+outward,outward)]
                + _build_stair_points(local,corner,axis="z")
                + [Vec3(x,corner.y,corner.z) for x in range(escape_x,branch_x+across,across)])
    return (
        prefix
        + [landing.offset(dz=branch_direction),plateau]
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


class _VolumeIndex:
    """Compact X/Z index for component envelopes, never literal storage bits."""

    def __init__(self, boxes: Iterable[Box]):
        self.buckets: dict[tuple[int,int], list[Box]] = {}
        for box in boxes:
            for x in range((box.min.x-1)//128, (box.max.x+1)//128+1):
                for z in range((box.min.z-1)//128, (box.max.z+1)//128+1):
                    self.buckets.setdefault((x,z),[]).append(box)

    def blocking(self, point: Vec3, *, own: Box | None, endpoint: Vec3,
                 flat_leg_z: int | None = None) -> bool:
        for box in self.buckets.get((point.x//128,point.z//128), ()):
            if not (box.min.x-1 <= point.x <= box.max.x+1
                    and box.min.z-1 <= point.z <= box.max.z+1
                    and box.min.y-1 <= point.y <= box.max.y+2):
                continue
            # Only the declared terminal's flat access leg may enter its
            # native component envelope. Its stair must rise outside it.
            if box == own and point.y == endpoint.y and (
                (point.x == endpoint.x) or (point.z == endpoint.z)
                or (flat_leg_z is not None and point.z == flat_leg_z)
            ):
                continue
            return True
        return False


def _plan_escapes(
    points: Iterable[Vec3], branch_y: int, obstacles: Iterable[Placement] = (), *,
    access: Mapping[Vec3,tuple[str,...]] | None = None,
    minimum_y: int = -64,
    maximum_y: int = 319,
    endpoint_bounds: Mapping[Vec3,Box] | None = None,
    obstacle_bounds: Iterable[Box] = (),
    track_targets: Mapping[Vec3,int] | None = None,
    transition_run: int = 4,
    reserved_branch_x: Iterable[int] = (),
    north_lane_limit: int = 128,
) -> dict[Vec3,_Escape]:
    if isinstance(north_lane_limit,bool) or not isinstance(north_lane_limit,int) or north_lane_limit<1:
        raise ValueError("North escape search budget must be a positive integer")
    # Upper terminals escape first. A lower stair can then pass behind their
    # short escape legs instead of cutting through a higher pin's future leg.
    is_wall=lambda p:(access or {}).get(p) in (("wall-south",),("wall-north",))
    points=sorted(set(points),key=lambda p:(-p.y,-p.x if is_wall(p) else p.x,p.z))
    terminals=set(points)
    placed={p.pos:p.block for p in obstacles}
    active={pos for pos,block in placed.items() if block != SUPPORT}
    reserved:set[Vec3]=set()
    reserved_heads:set[Vec3]=set()
    # Bounded sibling domains can share the same routing planes while retaining
    # separate net/track budgets. Their already emitted columns remain reserved;
    # callers also supply their literal blocks as obstacles below.
    used_branch_x:set[int]=set(reserved_branch_x)
    result={}
    volumes=_VolumeIndex(obstacle_bounds)
    # A vertical wall must not greedily put a few lower-row stairs into
    # every higher row's corridor. Reserve successively deeper Z escapes
    # for its lower Y levels; same-height terminals can share that depth.
    forced_levels: dict[tuple[int,str],set[int]]={}
    for point in points:
        modes=(access or {}).get(point,())
        if len(modes)==1 and modes[0] in ("south","north"):
            forced_levels.setdefault((point.z,modes[0]),set()).add(point.y)
    level_runs={(z,mode,y):6*(index+1)
                for (z,mode),ys in forced_levels.items()
                for index,y in enumerate(sorted(ys,reverse=True))}
    wall_runs={}
    wall_columns={point.z:max(q.x for q in points if q.z==point.z and is_wall(q))+8
                  for point in points if is_wall(point)}
    for z,y in {(point.z,point.y) for point in points if is_wall(point)}:
        row=[point for point in points if point.z==z and point.y==y and is_wall(point)]
        wall_runs.update({point:6*(index+1) for index,point in enumerate(row)})
    # Tall components can consume more than 128 staggered corridors before
    # their lowest data pins reach the common branch plane. Keep the existing
    # northbound limit (and its analytical branch-length bound), but let south
    # escapes search one corridor per endpoint when necessary: an earlier
    # endpoint can occupy a distinct stair/landing lane at the common height.
    # These extra lanes extend away from the global trunks, so they cannot
    # shorten a guaranteed long branch or cross behind its target track.
    south_lane_limit=max(128,len(points))
    for p in points:
        if p.y-1 < minimum_y or p.y > maximum_y:
            raise ValueError(f"endpoint {p} support would violate build height {minimum_y}..{maximum_y}")
        modes=(access or {}).get(p,("south","north","west"))
        if not modes or any(mode not in ("south","north","west","east","wall-south","wall-north") for mode in modes):
            raise ValueError(f"invalid access directions for endpoint {p}: {modes}")
        vertical=abs(branch_y-4-p.y)
        plateaus=max(0,(vertical+9)//10-1)
        own=(endpoint_bounds or {}).get(p)
        candidate=wall_columns[p.z] if is_wall(p) else p.x+vertical+plateaus*2
        if is_wall(p) and own is not None:
            # Wall access turns at the native terminal height, then climbs
            # beyond the whole backend. Literal obstacles validate both
            # flat legs; the envelope forbids a riser through higher rows.
            candidate=max(candidate,own.max.x+3)
        while any(candidate+dx in used_branch_x for dx in range(-2,3)):
            candidate+=4
        used_branch_x.add(candidate)
        if is_wall(p):
            wall_columns[p.z]=candidate+4
        def clearance(mode: str) -> int:
            minimum_run=level_runs.get((p.z,mode,p.y),6)
            if own is None:
                return minimum_run
            distance={"south":own.max.z-p.z+3,"north":p.z-own.min.z+3,
                      "west":p.x-own.min.x+3,"east":own.max.x-p.x+3}[mode]
            return max(minimum_run,((distance+5)//6)*6)
        alternatives=(
            (p.z+(1 if mode=="wall-south" else -1)*(wall_runs[p]+6*lane)-vertical-plateaus*2,
             None,p.z+(1 if mode=="wall-south" else -1)*(wall_runs[p]+6*lane))
            if mode in ("wall-south","wall-north") else
            (p.z+(1 if mode == "south" else -1)*(clearance(mode)+6*lane),None,None)
            # West-facing stacked terminals can have incompatible stair
            # plateau phases at the same landing Z. Move the landing north
            # as well as the riser west when searching another corridor.
            if mode not in ("west","east") else
            (p.z-vertical-plateaus*2-6*lane,p.x+(1 if mode=="east" else -1)*(clearance(mode)+6*lane),None)
            for lane in range(max(south_lane_limit,north_lane_limit)) for mode in modes
            if lane<(south_lane_limit if mode in ("south","wall-south")
                     else north_lane_limit if mode in ("north","wall-north") else 128)
        )
        for escape_z,escape_x,wall_lead_z in alternatives:
            branch_direction=-1
            if track_targets is not None:
                distance=track_targets[p]-escape_z
                if abs(distance)<6+transition_run:
                    continue
                branch_direction=1 if distance>0 else -1
            geometry=_escape_points(p,escape_z,candidate,branch_y,escape_x,wall_lead_z,branch_direction)
            heads=_climb_headroom(geometry)
            conflict=False
            # Nearby local tracks can reverse the final turn. Reject a
            # resulting U-turn that revisits its own dust or puts a support
            # in its own climbing headroom; different block states at such
            # points are a real short, even when no other net is nearby.
            distinct=[]
            for point in geometry:
                if not distinct or point!=distinct[-1]:
                    distinct.append(point)
            own_wires=set(distinct)
            if len(own_wires)!=len(distinct) or heads.intersection(own_wires):
                continue
            if any(head in reserved or head in terminals for head in heads) or any(q in reserved_heads for q in geometry):
                continue
            for q in geometry:
                if q == p:
                    continue
                if q.y-1 < minimum_y or q.y > maximum_y:
                    conflict=True
                    break
                if volumes.blocking(q,own=own,endpoint=p,flat_leg_z=wall_lead_z):
                    conflict=True
                    break
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
                result[p]=_Escape(p,escape_z,candidate,escape_x,wall_lead_z,branch_direction)
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
    include_start: bool = True,
    include_end: bool = True,
) -> Iterator[Placement]:
    if start.y != end.y or start.z != end.z:
        raise ValueError("X wire requires equal Y/Z")
    step = 1 if end.x >= start.x else -1
    facing = "east" if (step > 0) == signal_forward else "west"
    length=abs(end.x-start.x)
    for n, x in enumerate(range(start.x, end.x + step, step)):
        if (n == 0 and not include_start) or (n == length and not include_end):
            continue
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
    escape_x: int | None = None,
    wall_lead_z: int | None = None,
    branch_direction: int = -1,
) -> Iterator[Placement]:
    """Source: endpoint -> branch plane -> track. Sink: reverse direction."""
    landing = Vec3(branch_x, branch_y-4, escape_z)
    plateau = Vec3(branch_x, branch_y-4, escape_z+branch_direction*2)
    high = Vec3(branch_x, branch_y, escape_z+branch_direction*6)
    track = Vec3(branch_x, branch_y, track_z)

    def lower_path(forward: bool):
        if wall_lead_z is not None:
            local=Vec3(endpoint.x,endpoint.y,wall_lead_z)
            corner=Vec3(branch_x,endpoint.y,wall_lead_z)
            yield from _wire_z(endpoint,local,signal_forward=forward,component=component,include_end=False)
            yield from _wire_x(local,corner,signal_forward=forward,component=component,include_end=False)
            yield from iter_stair(corner,landing,axis="z",signal_forward=forward,component=component)
        elif escape_x is None:
            local=Vec3(endpoint.x,endpoint.y,escape_z)
            yield from _wire_z(endpoint,local,signal_forward=forward,component=component,include_end=False)
            yield from iter_stair(local,landing,axis="x",signal_forward=forward,component=component)
        else:
            local=Vec3(escape_x,endpoint.y,endpoint.z)
            corner=Vec3(escape_x,branch_y-4,escape_z)
            yield from _wire_x(endpoint,local,signal_forward=forward,component=component,include_end=False)
            yield from iter_stair(local,corner,axis="z",signal_forward=forward,component=component)
            yield from _wire_x(corner,landing,signal_forward=forward,component=component,include_start=False)

    if source:
        yield from lower_path(True)
        yield from _wire_z(landing, plateau, signal_forward=True, component=component,include_start=False,include_end=False)
        yield from iter_stair(plateau, high, axis="z", signal_forward=True, component=component)
        yield from _wire_z(high, track, signal_forward=True, component=component,include_start=False,include_end=False)
    else:
        yield from _wire_z(track, high, signal_forward=True, component=component,include_start=False,include_end=False)
        yield from iter_stair(high, plateau, axis="z", signal_forward=True, component=component)
        yield from _wire_z(plateau, landing, signal_forward=True, component=component,include_start=False,include_end=False)
        # Use the same physical geometry as a source, reversing only diodes.
        # Rebuilding a reversed stair moves its flat tail across cell pins.
        yield from lower_path(False)


def _trunk(
    source: Vec3,
    sinks: tuple[Vec3, ...],
    *,
    component: str,
    toward_source: bool = False,
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
            repeaters[x]=repeater("west" if toward_source else "east")
            x+=10
    x=source.x-1
    while x>lo:
        while x in endpoints and x>lo:
            x-=1
        if x>lo:
            repeaters[x]=repeater("east" if toward_source else "west")
            x-=10
    # Regenerate just before a descending sink transition. A route that is
    # individually under 15 dust blocks can still die across several joined
    # legs unless the junction budgets include the vertical stair.
    for sink in sinks:
        if sink.x != source.x:
            x=sink.x-(1 if sink.x>source.x else -1)
            if x not in endpoints:
                direction="east" if sink.x>source.x else "west"
                if toward_source:
                    direction="west" if direction == "east" else "east"
                repeaters[x]=repeater(direction)
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
    access: Mapping[Vec3,tuple[str,...]] | None = None,
    minimum_y: int = -64,
    maximum_y: int = 319,
    endpoint_bounds: Mapping[Vec3,Box] | None = None,
    obstacle_bounds: Iterable[Box] = (),
    track_base_z: int | None = None,
) -> Iterator[Placement]:
    """Route arbitrary long-distance nets without redstone crossings.

    Endpoint escape segments live on branch_y. Net trunks live on trunk_y.
    Every net gets a unique Z track, so branch lines cross trunks four blocks
    below them instead of electrically joining.
    """
    if trunk_y <= branch_y:
        raise ValueError("trunk_y must be above branch_y")
    if branch_y-5 < minimum_y or trunk_y > maximum_y:
        raise ValueError(f"routing planes exceed build height {minimum_y}..{maximum_y}")
    nets=tuple(nets)
    if not nets:
        return

    all_points=[p for net in nets for p in (net.source,*net.sinks)]
    min_z=min(p.z for p in all_points)
    track_base=min_z-(len(nets)+1)*track_spacing-4096 if track_base_z is None else track_base_z
    transition_run=trunk_y-branch_y
    track_targets=None
    if track_base_z is not None:
        track_targets={}
        for index,net in enumerate(nets):
            for point in (net.source,*net.sinks):
                target=track_base+index*track_spacing
                if point in track_targets and track_targets[point]!=target:
                    raise ValueError(f"endpoint {point} belongs to multiple local tracks; combine its fanout explicitly")
                track_targets[point]=target

    # Escape every endpoint a short fixed distance in Z before it rises
    # onto the branch plane. Logic-cell rows are spaced farther apart than
    # this, so different 2-D placement rows cannot share an escape wire.
    # Vertically stacked endpoints (such as display rows) remain isolated by
    # Y until their individually assigned X branch.
    escapes=_plan_escapes(all_points,branch_y,obstacles,access=access,minimum_y=minimum_y,
                         maximum_y=maximum_y,endpoint_bounds=endpoint_bounds,
                         obstacle_bounds=obstacle_bounds,track_targets=track_targets,
                         transition_run=transition_run)

    for net_index,net in enumerate(nets):
        track_z=track_base+net_index*track_spacing
        comp=f"{component}:{net.name}"
        fanout=not net.wired_or

        branch_track_z=track_z-escapes[net.source].branch_direction*transition_run
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
            source=fanout,
            component=comp,
            escape_x=escapes[net.source].escape_x,
            wall_lead_z=escapes[net.source].wall_lead_z,
            branch_direction=escapes[net.source].branch_direction,
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
            signal_forward=fanout,
            component=comp,
        )

        sink_trunks=[]
        for sink in net.sinks:
            sink_track_z=track_z-escapes[sink].branch_direction*transition_run
            sink_branch=Vec3(escapes[sink].branch_x,branch_y,sink_track_z)
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
                signal_forward=fanout,
                component=comp,
            )
            yield from _endpoint_path(
                sink,
                escapes[sink].escape_z,
                sink_branch.x,
                branch_y,
                sink_track_z,
                source=not fanout,
                component=comp,
                escape_x=escapes[sink].escape_x,
                wall_lead_z=escapes[sink].wall_lead_z,
                branch_direction=escapes[sink].branch_direction,
            )

        yield from _trunk(source_trunk,tuple(sink_trunks),component=comp,toward_source=net.wired_or)
