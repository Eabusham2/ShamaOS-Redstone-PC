"""Experimental bounded routing; not selected by the machine generator.

Native cells are unchanged. Connected cells share a small placement domain,
endpoint escape allocation restarts in each tile, and electrically separated
intervals really share trunk tracks. A bounded hierarchy can stack tiles and
route their boundary terminals on a parent tier. Clock leaves can spend spare
private branch positions on delay matching without changing the footprint.

Repeater sums are planning data, not timing closure. The scheduled Minecraft
engine tests exercise complete register transfers, clock skew and setup/hold
margins for the tested circuits. Neither this API nor those small proofs imply
that the entire mapped SoC is routed or supports a faster production clock.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace
from typing import Iterable, Iterator, Mapping, Sequence

from .external_router import _Escape, _endpoint_path, _plan_escapes, _trunk
from .logic import template_for
from .memory import DUST, repeater
from .routing import iter_stair
from ..model import BlockState, Box, Placement, Vec3
from ..synthesis import Endpoint, PhysicalNetlist, RoutedNet, _assign_tracks


def connected_order(physical: PhysicalNetlist) -> list[str]:
    """Deterministic small-tile affinity order, ignoring global fanout nets."""
    adjacency={name:defaultdict(float) for name in physical.cell_types}
    for net in physical.nets:
        source=net.source.name
        sinks=[sink.name for sink in net.sinks if sink.name in adjacency]
        if source not in adjacency or len(sinks)>8:
            continue
        for sink in sinks:
            if source!=sink:
                adjacency[source][sink]+=1/max(1,len(sinks))
                adjacency[sink][source]+=1/max(1,len(sinks))
    remaining=set(adjacency);order=[]
    while remaining:
        recent=order[-8:]
        chosen=min(remaining,key=lambda name:(
            -sum(adjacency[name].get(other,0)/(len(recent)-index)
                 for index,other in enumerate(recent)),
            -sum(adjacency[name].values()),name))
        order.append(chosen);remaining.remove(chosen)
    return order


@dataclass
class LocalTile:
    physical: PhysicalNetlist
    escapes: dict[Vec3, _Escape]
    tracks: dict[int,int]
    track_base_z: int
    branch_y: int
    trunk_y: int
    overrides: dict[Vec3, BlockState] = field(default_factory=dict)
    clock_delays: dict[int, dict] = field(default_factory=dict)
    _balanced_positions: dict[int, set[Vec3]] = field(default_factory=dict, repr=False)

    def endpoint_route(self, net: RoutedNet, endpoint: Endpoint) -> tuple[Vec3, list[Placement]]:
        track_z=self.track_base_z+self.tracks[net.bit]*4
        escape=self.escapes[endpoint.pos]
        source=endpoint==net.source
        branch=Vec3(escape.branch_x,self.branch_y,track_z+4)
        trunk=Vec3(escape.branch_x,self.trunk_y,track_z)
        blocks=list(_endpoint_path(endpoint.pos,escape.escape_z,escape.branch_x,
            self.branch_y,branch.z,source=source,component=f"local-tile:net-{net.bit}",
            escape_x=escape.escape_x,wall_lead_z=escape.wall_lead_z))
        blocks+=list(iter_stair(branch,trunk,axis="z",signal_forward=True,component=f"local-tile:net-{net.bit}")) if source else list(iter_stair(trunk,branch,axis="z",signal_forward=True,component=f"local-tile:net-{net.bit}"))
        return trunk,blocks

    def balance_clock_routes(self) -> None:
        """Spend spare branch dust positions on delay, with no footprint growth.

        Only each clock leaf's private flat branch is modified. Shared trunk
        and source delays remain unchanged. A capacity shortfall fails rather
        than claiming matching. Actual scheduled-engine evidence is still
        required because repeater sums alone are not a hold-time proof.
        """
        for net in self.physical.nets:
            clocks=[sink for sink in net.sinks if sink.pin=="C"
                    and sink.name in self.physical.cell_types
                    and template_for(self.physical.cell_types[sink.name]).cell_type=="DFF"]
            if not clocks:continue
            self.balance_net(net.bit,{sink.pos:0 for sink in clocks})

    def balance_net(self, bit: int, leaf_tail_delays: Mapping[Vec3, int]) -> None:
        """Balance a clock including each child tile's planned repeater tail.

        Matching is transactional and may be repeated for the same net. If a
        private branch has insufficient delay capacity, retain the old plan
        and reject the new one instead of silently leaving a partial match.
        """
        net=next((net for net in self.physical.nets if net.bit==bit),None)
        if net is None:
            raise ValueError(f"unknown clock net {bit}")
        if not leaf_tail_delays or not set(leaf_tail_delays).issubset(s.pos for s in net.sinks):
            raise ValueError("clock tails must identify existing sink terminals")
        if any(not isinstance(delay,int) or delay<0 for delay in leaf_tail_delays.values()):
            raise ValueError("clock tail delays must be nonnegative integer ticks")
        clocks=[sink for sink in net.sinks if sink.pos in leaf_tail_delays]
        delay=lambda blocks:sum(int(dict(p.block.properties)["delay"])
                                for p in blocks if p.block.name=="minecraft:repeater")
        source_trunk,source_blocks=self.endpoint_route(net,net.source)
        leaves={sink:self.endpoint_route(net,sink) for sink in net.sinks}
        trunk_blocks=list(_trunk(source_trunk,tuple(trunk for trunk,_ in leaves.values()),component="clock"))
        arrivals={}
        for sink in clocks:
            trunk,blocks=leaves[sink]
            lo,hi=sorted((source_trunk.x,trunk.x))
            arrivals[sink]=delay(source_blocks)+delay(blocks)+delay(p for p in trunk_blocks if lo<p.pos.x<hi)+leaf_tail_delays[sink.pos]
        target=max(arrivals.values())
        updates={}
        for sink in clocks:
            _,blocks=leaves[sink]
            remaining=target-arrivals[sink]
            escape=self.escapes[sink.pos]
            track_z=self.track_base_z+self.tracks[net.bit]*4
            lo,hi=sorted((track_z+4,escape.escape_z-6))
            candidates=[p for p in blocks if p.pos.y==self.branch_y and lo<p.pos.z<hi
                        and (p.block==DUST or p.block.name=="minecraft:repeater")]
            for p in candidates:
                if not remaining:break
                current=int(dict(p.block.properties)["delay"]) if p.block.name=="minecraft:repeater" else 0
                extra=min(4-current,remaining)
                if extra:
                    updates[p.pos]=repeater("south",delay=current+extra)
                    remaining-=extra
            if remaining:
                raise ValueError(f"clock leaf {sink.name} needs {remaining} additional delay ticks beyond its private branch capacity")
        for point in self._balanced_positions.get(bit,()):
            self.overrides.pop(point,None)
        self.overrides.update(updates)
        self._balanced_positions[bit]=set(updates)
        self.clock_delays[net.bit]={"unbalanced_repeater_delay":{s.name:d for s,d in arrivals.items()},
                                    "balanced_repeater_delay":target}

    def iter_routes(self) -> Iterator[Placement]:
        for net in self.physical.nets:
            track_z=self.track_base_z+self.tracks[net.bit]*4
            comp=f"local-tile:net-{net.bit}"
            source_trunk=Vec3(self.escapes[net.source.pos].branch_x,self.trunk_y,track_z)
            sink_trunks=[]
            for endpoint in (net.source,*net.sinks):
                source=endpoint==net.source
                escape=self.escapes[endpoint.pos]
                branch=Vec3(escape.branch_x,self.branch_y,track_z+4)
                trunk=Vec3(escape.branch_x,self.trunk_y,track_z)
                yield from _endpoint_path(endpoint.pos,escape.escape_z,escape.branch_x,
                    self.branch_y,branch.z,source=source,component=comp,
                    escape_x=escape.escape_x,wall_lead_z=escape.wall_lead_z)
                if source:
                    yield from iter_stair(branch,trunk,axis="z",signal_forward=True,component=comp)
                else:
                    yield from iter_stair(trunk,branch,axis="z",signal_forward=True,component=comp)
                    sink_trunks.append(trunk)
            yield from _trunk(source_trunk,tuple(sink_trunks),component=comp)

    def iter_placements(self) -> Iterator[Placement]:
        yield from self.physical._iter_local_placements("local-tile")
        for p in self.iter_routes():
            yield replace(p,block=self.overrides.get(p.pos,p.block))

    def report(self, placements: Sequence[Placement] | None = None) -> dict:
        if placements is None:
            placements=list(self.iter_placements())
        points={p.pos for p in placements}
        lower={axis:min(getattr(p,axis) for p in points) for axis in ("x","y","z")}
        upper={axis:max(getattr(p,axis) for p in points) for axis in ("x","y","z")}
        counts=defaultdict(int)
        for track in self.tracks.values(): counts[track]+=1
        return {"scope":"one bounded experimental tile, not complete machine routing",
                "cells":len(self.physical.cell_types),"nets":len(self.physical.nets),
                "tracks":len(counts),"reused_tracks":sum(n>1 for n in counts.values()),
                "placements":len(placements),"unique_blocks":len(points),
                "occupied_chunks":len({(p.x//16,p.z//16) for p in points}),
                "bounds":{"min":lower,"max":upper},
                "branch_y":self.branch_y,"trunk_y":self.trunk_y,
                "clock_delay_matching":self.clock_delays,
                "clock_repeater_overrides":len(self.overrides)}


def compact_tile(physical: PhysicalNetlist, *, max_cells: int = 64) -> LocalTile:
    """Plan one local domain, explicitly bounded by a caller-visible budget."""
    if max_cells<1:
        raise ValueError("tile cell budget must be positive")
    if len(physical.cell_types)>max_cells:
        raise ValueError("experimental tile exceeds its bounded cell budget")
    if any(endpoint.name.startswith("macro:") for net in physical.nets
           for endpoint in (net.source,*net.sinks)):
        raise ValueError("compact_tile accepts native cells; use stack_tiles for boundary routing")
    order=connected_order(physical)
    stride=max([32,*(template_for(kind).width+8 for kind in physical.cell_types.values())])
    origin=physical.origin
    new_origins={name:origin.offset(32+index*stride) for index,name in enumerate(order)}
    positions={}
    for name,new in new_origins.items():
        old=physical.cell_origins[name]
        for offset in template_for(physical.cell_types[name]).pins.values():
            positions[old.offset(offset.x,offset.y,offset.z)]=new.offset(offset.x,offset.y,offset.z)
    pin_cells=defaultdict(list)
    for net in physical.nets:
        for endpoint in (net.source,*net.sinks):
            if endpoint.name in new_origins:
                old,new=physical.cell_origins[endpoint.name],new_origins[endpoint.name]
                positions[endpoint.pos]=endpoint.pos.offset(new.x-old.x,new.y-old.y,new.z-old.z)
        for endpoint in (net.source,*net.sinks):
            if endpoint.name.startswith("port:"):
                pin_cells[endpoint.pos].extend(positions[e.pos].x for e in (net.source,*net.sinks)
                                              if e.name in new_origins)
    # Boundary terminals stay in one clear front strip. Choose the nearest
    # free four-block slot to their connected cells instead of one remote I/O
    # apron, while preserving every named bus and bit index.
    used=set()
    all_ports=[p for group in (physical.input_ports,physical.output_ports)
               for pins in group.values() for p in pins]
    for pin in dict.fromkeys(all_ports):
        xs=sorted(pin_cells[pin]);target=xs[len(xs)//2] if xs else origin.x
        target=origin.x+4*max(0,round((target-origin.x)/4))
        candidates=(target+4*offset for distance in range(len(all_ports)+1)
                    for offset in ([0] if not distance else [-distance,distance]))
        x=next(x for x in candidates if x>=origin.x and x not in used)
        used.add(x);positions[pin]=Vec3(x,origin.y+1,origin.z+48)
    move=lambda endpoint:replace(endpoint,pos=positions[endpoint.pos])
    updated=replace(physical,cell_origins=new_origins,cell_stride=stride,
        input_ports={name:tuple(positions[p] for p in pins) for name,pins in physical.input_ports.items()},
        output_ports={name:tuple(positions[p] for p in pins) for name,pins in physical.output_ports.items()},
        nets=tuple(replace(net,source=move(net.source),sinks=tuple(map(move,net.sinks))) for net in physical.nets),
        constant_high=tuple(positions.get(p,p) for p in physical.constant_high),
        north_constant_high=tuple(positions.get(p,p) for p in physical.north_constant_high))
    native=list(updated._iter_local_placements("local-tile"))
    roof=max((p.pos.y for p in native),default=origin.y+1)
    branch_y=roof+14;trunk_y=branch_y+4
    if trunk_y>319:
        raise ValueError("tile-local planes exceed world build height")
    access={p:("north",) for p in positions.values() if p.z==origin.z+48}
    return plan_existing_layout(updated,branch_y=branch_y,trunk_y=trunk_y,
                                obstacles=native,access=access)


def plan_existing_layout(physical: PhysicalNetlist, *, branch_y: int, trunk_y: int,
                         obstacles: Iterable[Placement], access: Mapping[Vec3,tuple[str,...]],
                         endpoint_bounds: Mapping[Vec3,Box] | None = None,
                         obstacle_bounds: Iterable[Box] = (),
                         reserved_branch_x: Iterable[int] = (),
                         maximum_track_z: int | None = None) -> LocalTile:
    """Route a bounded native or macro layout without global endpoint lanes."""
    if trunk_y-branch_y!=4 or not physical.minimum_build_y+4<=branch_y<trunk_y<=319:
        raise ValueError("local routing needs four separated planes within build height")
    if not physical.nets:
        raise ValueError("local routing needs at least one connected net")
    if len({net.bit for net in physical.nets})!=len(physical.nets):
        raise ValueError("net identifiers must be unique within a tile")
    points=[e.pos for net in physical.nets for e in (net.source,*net.sinks)]
    escapes=_plan_escapes(points,branch_y,obstacles,access=access,minimum_y=physical.minimum_build_y,
        endpoint_bounds=endpoint_bounds,obstacle_bounds=obstacle_bounds,
        reserved_branch_x=reserved_branch_x)
    return _tile_from_escapes(physical,escapes,branch_y=branch_y,trunk_y=trunk_y,
                              maximum_track_z=maximum_track_z)


def _tile_from_escapes(physical: PhysicalNetlist, escapes: Mapping[Vec3,_Escape], *,
                       branch_y: int, trunk_y: int,
                       maximum_track_z: int | None = None) -> LocalTile:
    intervals=[]
    for net in physical.nets:
        xs=[escapes[e.pos].branch_x for e in (net.source,*net.sinks)]
        intervals.append((min(xs),max(xs),net.bit))
    tracks=_assign_tracks(intervals)
    # Every transition approaches from south, so all tracks lie north of all
    # endpoint landing turns. No arbitrary 4096-block apron is used.
    track_count=max(tracks.values(),default=-1)+1
    last_track=min(e.escape_z for e in escapes.values())-16
    if maximum_track_z is not None:
        # A sibling's complete track band stays south of this bounded router.
        # This changes only placement, never the set of nets or terminals.
        last_track=min(last_track,maximum_track_z)
    track_base_z=last_track-4*max(0,track_count-1)
    return LocalTile(physical,dict(escapes),tracks,track_base_z,branch_y,trunk_y)


def plan_bounded_domains(physicals: Sequence[PhysicalNetlist], *, branch_y: int, trunk_y: int,
                         obstacles: Iterable[Placement], access: Mapping[Vec3,tuple[str,...]],
                         endpoint_bounds: Mapping[Vec3,Box] | None = None,
                         obstacle_bounds: Iterable[Box] = (),
                         max_domain_terminals: int = 256,
                         max_shared_terminals: int = 4096,
                         north_lane_limit: int | None = None) -> tuple[LocalTile,...]:
    """Allocate parent escape prefixes in height order, then route bounded domains.

    Prefix scheduling must respect *all* higher terminals before any lower one:
    a low terminal in an earlier domain can otherwise wall off a later domain.
    No combined PhysicalNetlist is created. Each router retains its own complete
    bounded netlist, unique branch columns and a separate horizontal track band.
    Very large parents explicitly require a banked placement with real delivery
    paths; raising their vertical tier count cannot make them fit safely.
    """
    physicals=tuple(physicals)
    if not physicals:
        return ()
    if max_domain_terminals<1 or max_shared_terminals<1:
        raise ValueError("domain and shared prefix budgets must be positive")
    minimum_y=max(physical.minimum_build_y for physical in physicals)
    if trunk_y-branch_y!=4 or not minimum_y+4<=branch_y<trunk_y<=319:
        raise ValueError("bounded domains need four separated routing planes within build height")
    points=[];owned=[];seen=set()
    for physical in physicals:
        if not physical.nets or len({net.bit for net in physical.nets})!=len(physical.nets):
            raise ValueError("every bounded domain needs nonempty unique signal identifiers")
        terminals=[endpoint.pos for net in physical.nets for endpoint in (net.source,*net.sinks)]
        if len(terminals)>max_domain_terminals:
            raise ValueError("bounded domain exceeds its explicit terminal budget")
        if len(set(terminals))!=len(terminals) or seen.intersection(terminals):
            raise ValueError("bounded domains cannot alias distinct physical terminals")
        seen.update(terminals);points.extend(terminals);owned.append(terminals)
        if len(points)>max_shared_terminals:
            raise ValueError("parent prefix allocation exceeds its budget; explicit horizontal domain banks and delivery paths are required")
    # A bounded parent can contain more than 128 independent North-facing
    # exports. Give its declared workload enough search lanes, while leaving
    # the global/production router's default and analytical bounds unchanged.
    north_budget=max(128,len(points)) if north_lane_limit is None else north_lane_limit
    escapes=_plan_escapes(points,branch_y,obstacles,access=access,minimum_y=minimum_y,
        endpoint_bounds=endpoint_bounds,obstacle_bounds=obstacle_bounds,
        north_lane_limit=north_budget)
    tiles=[];maximum_track_z=None
    for physical,terminals in zip(physicals,owned):
        tile=_tile_from_escapes(physical,{point:escapes[point] for point in terminals},
            branch_y=branch_y,trunk_y=trunk_y,maximum_track_z=maximum_track_z)
        tiles.append(tile)
        maximum_track_z=tile.track_base_z-8
    return tuple(tiles)


@dataclass
class TileHierarchy:
    """Bounded child tiles and their routed upper boundary tier."""

    children: tuple[LocalTile,...]
    parent: LocalTile
    conservation: dict = field(default_factory=dict)

    def iter_placements(self) -> Iterator[Placement]:
        for child in self.children:
            yield from child.iter_placements()
        yield from self.parent.iter_placements()

    def report(self) -> dict:
        placements=list(self.iter_placements())
        return {
            "scope":"bounded experimental stacked tiles and one parent tier; not full-machine routing",
            "children":[child.report() for child in self.children],
            "parent":self.parent.report(),
            "total_placements":len(placements),
            "unique_blocks":len({p.pos for p in placements}),
            "occupied_chunks":len({(p.pos.x//16,p.pos.z//16) for p in placements}),
            "structural_conservation":self.conservation,
        }


def audit_hierarchy(original: PhysicalNetlist, hierarchy: TileHierarchy) -> dict:
    """Resolve boundary links back to every original driver and sink identity.

    This proves structural preservation, including clock leaves and constants.
    It does not claim collision, electrical or timing closure for the geometry.
    """
    children=hierarchy.children
    owners={}
    for index,child in enumerate(children):
        for name,kind in child.physical.cell_types.items():
            if name in owners or original.cell_types.get(name)!=kind:
                raise ValueError("hierarchy duplicated, replaced or invented a native cell")
            owners[name]=index
    if set(owners)!=set(original.cell_types):
        raise ValueError("hierarchy dropped a native cell")
    root=hierarchy.parent.physical
    for before,after in ((original.input_ports,root.input_ports),(original.output_ports,root.output_ports)):
        if {name:len(pins) for name,pins in before.items()}!={name:len(pins) for name,pins in after.items()}:
            raise ValueError("hierarchy changed an original port or bus width")
    root_positions={old:new for old_group,new_group in ((original.input_ports,root.input_ports),
                                                       (original.output_ports,root.output_ports))
                    for name,pins in old_group.items() for old,new in zip(pins,new_group[name])}
    child_nets=[{net.bit:net for net in child.physical.nets} for child in children]
    parent_nets={net.bit:net for net in root.nets}
    terminal_sources={pins[0]:(index,name) for index,child in enumerate(children)
                      for name,pins in child.physical.output_ports.items()}
    resolved={}

    def driver(bit: int, tile: int | None, pending: frozenset = frozenset()) -> tuple[str,str]:
        key=(tile,bit)
        if key in resolved:
            return resolved[key]
        if key in pending:
            raise ValueError("hierarchy boundary contains a driver cycle")
        nets=parent_nets if tile is None else child_nets[tile]
        if bit not in nets:
            raise ValueError("hierarchy boundary has no driver")
        source=nets[bit].source
        if source.name in owners:
            child=children[owners[source.name]].physical
            pin=template_for(child.cell_types[source.name]).pins[source.pin]
            if tile!=owners[source.name] or source.pos!=child.cell_origins[source.name].offset(pin.x,pin.y,pin.z):
                raise ValueError("hierarchy native driver terminal is misplaced")
            result=(source.name,source.pin)
        elif tile is not None:
            pin=children[tile].physical.input_ports.get(f"n{bit}")
            parent=parent_nets.get(bit)
            if pin!=(source.pos,) or parent is None or not any(sink.pos==source.pos for sink in parent.sinks):
                raise ValueError("hierarchy child input is disconnected")
            result=driver(bit,None,pending|{key})
        elif source.name.startswith("macro:"):
            binding=terminal_sources.get(source.pos)
            if binding is None or binding[1]!=f"n{bit}":
                raise ValueError("hierarchy parent has the wrong child output")
            result=driver(bit,binding[0],pending|{key})
        else:
            result=(source.name,source.pin)
            expected=next((net.source for net in original.nets if net.bit==bit),None)
            if expected is None or result!=(expected.name,expected.pin) or root_positions.get(expected.pos)!=source.pos:
                raise ValueError("hierarchy changed an original input terminal")
        resolved[key]=result
        return result

    actual=[]
    for index,child in enumerate(children):
        for net in child.physical.nets:
            for sink in net.sinks:
                if sink.name in owners:
                    pin=template_for(child.physical.cell_types[sink.name]).pins[sink.pin]
                    if owners[sink.name]!=index or sink.pos!=child.physical.cell_origins[sink.name].offset(pin.x,pin.y,pin.z):
                        raise ValueError("hierarchy native sink terminal is misplaced")
                    actual.append((net.bit,*driver(net.bit,index),sink.name,sink.pin))
    root_outputs={pos for pins in root.output_ports.values() for pos in pins}
    for net in root.nets:
        for sink in net.sinks:
            if sink.pos in root_outputs:
                actual.append((net.bit,*driver(net.bit,None),sink.name,sink.pin))
    expected=[(net.bit,net.source.name,net.source.pin,sink.name,sink.pin)
              for net in original.nets for sink in net.sinks]
    if sorted(actual)!=sorted(expected):
        raise ValueError("hierarchy changed or dropped an original signal sink")
    constants=[]
    for pos in original.constant_high:
        if pos in root_positions:
            constants.append(root_positions[pos])
        else:
            found=False
            for name,base in original.cell_origins.items():
                for pin in template_for(original.cell_types[name]).pins.values():
                    if pos==base.offset(pin.x,pin.y,pin.z):
                        child=children[owners[name]].physical
                        constants.append(child.cell_origins[name].offset(pin.x,pin.y,pin.z));found=True;break
                if found:break
            if not found:
                raise ValueError("hierarchy cannot identify a constant terminal")
    actual_constants=[*root.constant_high,*(point for child in children for point in child.physical.constant_high)]
    if sorted(constants)!=sorted(actual_constants):
        raise ValueError("hierarchy changed or dropped a constant terminal")
    return {"cells":len(owners),"signal_sink_uses":len(expected),"constant_one_terminals":len(constants),
            "clock_leaf_uses":sum(sink.pin=="C" and sink.name in original.cell_types and
                                  template_for(original.cell_types[sink.name]).cell_type=="DFF"
                                  for net in original.nets for sink in net.sinks),
            "original_port_bits":sum(len(pins) for ports in (original.input_ports,original.output_ports)
                                     for pins in ports.values()),
            "scope":"driver/sink, cell, port and constant conservation; timing and geometry require separate checks"}


def _placement_bounds(placements: Sequence[Placement]) -> Box:
    return Box(
        Vec3(*(min(getattr(p.pos,axis) for p in placements) for axis in ("x","y","z"))),
        Vec3(*(max(getattr(p.pos,axis) for p in placements) for axis in ("x","y","z"))),
    )


def stack_tiles(physical: PhysicalNetlist, groups: Sequence[Iterable[str]], *,
                layer_pitch: int = 24, max_cells: int = 64,
                max_boundary_terminals: int = 256) -> TileHierarchy:
    """Route an explicit, exhaustive cell partition on stacked local tiers.

    This intentionally accepts already selected small groups. It does not run
    a full-chip partitioner or imply that arbitrarily many stacked tiers fit.
    Every crossing net gets boundary terminals; native cells, constants, bus
    indices and all parent I/O remain part of the emitted circuit.
    """
    groups=tuple(frozenset(group) for group in groups)
    owned={name:index for index,group in enumerate(groups) for name in group}
    if not groups or any(not group for group in groups):
        raise ValueError("tile groups must be nonempty")
    if sum(map(len,groups))!=len(owned) or set(owned)!=set(physical.cell_types):
        raise ValueError("tile groups must partition every native cell exactly once")
    if any(len(group)>max_cells for group in groups):
        raise ValueError("experimental tile exceeds its bounded cell budget")
    if max_boundary_terminals<1:
        raise ValueError("tile boundary budget must be positive")
    if layer_pitch<22:
        raise ValueError("stacked local tiers require at least 22 blocks of vertical pitch")
    if any(e.name.startswith("macro:") for net in physical.nets for e in (net.source,*net.sinks)):
        raise ValueError("stack_tiles requires a native-cell netlist")
    # Reject impossible tall stacks before allocating any escape geometry.
    if physical.origin.y+(len(groups)-1)*layer_pitch+37>319:
        raise ValueError("stacked child and parent tiers exceed world build height")
    for net in physical.nets:
        clock_groups={owned[sink.name] for sink in net.sinks
                      if sink.name in owned and sink.pin=="C"
                      and template_for(physical.cell_types[sink.name]).cell_type=="DFF"}
        source_group=owned.get(net.source.name)
        if source_group in clock_groups and len(clock_groups)>1:
            raise ValueError("internally generated clock has local and cross-tile leaves; global clock closure is required")

    children=[]
    for index,members in enumerate(groups):
        incoming={};outgoing={};nets=[]
        origin=physical.origin.offset(dy=layer_pitch*index)
        for net in physical.nets:
            source_inside=net.source.name in members
            local_sinks=[sink for sink in net.sinks if sink.name in members]
            outside_sinks=[sink for sink in net.sinks if sink.name not in members]
            if not source_inside and not local_sinks:
                continue
            source=net.source
            if not source_inside:
                # Temporary distinct terminals are immediately repositioned by
                # compact_tile; they never become emitted remote aprons.
                position=Vec3(10000+8*net.bit,origin.y+1,20000)
                incoming[f"n{net.bit}"]=(position,)
                source=Endpoint(f"port:n{net.bit}[0]",f"n{net.bit}",position,True)
            if source_inside and outside_sinks:
                position=Vec3(10000+8*net.bit,origin.y+1,20004)
                outgoing[f"n{net.bit}"]=(position,)
                local_sinks.append(Endpoint(f"port:n{net.bit}[0]",f"n{net.bit}",position,False))
            if local_sinks:
                nets.append(RoutedNet(net.bit,source,tuple(local_sinks),0))
        cell_points={physical.cell_origins[name].offset(p.x,p.y,p.z)
                     for name in members for p in template_for(physical.cell_types[name]).pins.values()}
        child_physical=PhysicalNetlist(
            top=f"{physical.top}-tile{index}",origin=origin,
            cell_origins={name:physical.cell_origins[name] for name in sorted(members)},
            cell_types={name:physical.cell_types[name] for name in sorted(members)},
            input_ports=incoming,output_ports=outgoing,nets=tuple(nets),
            constant_high=tuple(p for p in physical.constant_high if p in cell_points),
            north_constant_high=tuple(p for p in physical.north_constant_high if p in cell_points),
            minimum_build_y=physical.minimum_build_y,
        )
        if len(incoming)+len(outgoing)>max_boundary_terminals:
            raise ValueError("child tile exceeds its boundary terminal budget")
        child=compact_tile(child_physical,max_cells=max_cells)
        child.balance_clock_routes()
        children.append(child)

    child_blocks=[];boxes=[];endpoint_bounds={};access={}
    for child in children:
        blocks=list(child.iter_placements());child_blocks.extend(blocks)
        bounds=_placement_bounds(blocks);boxes.append(bounds)
        for ports in (child.physical.input_ports,child.physical.output_ports):
            for pins in ports.values():
                for pin in pins:
                    access[pin]=("south",);endpoint_bounds[pin]=bounds
    terminal_count=sum(len(pins) for ports in (physical.input_ports,physical.output_ports)
                       for pins in ports.values())
    parent_terminals=terminal_count+sum(len(child.physical.input_ports)+len(child.physical.output_ports)
                                        for child in children)
    if parent_terminals>max_boundary_terminals:
        raise ValueError("parent tier exceeds its boundary terminal budget")
    # A fixed 120-block apron admits the two-register demonstration but can
    # trap larger parent interfaces between already allocated escape stairs
    # and the child envelopes. Reserve one six-block escape lane per root
    # terminal, based on the actual children, while keeping this tier bounded.
    root_port_z=max(physical.origin.z+120,
                    max(bounds.max.z for bounds in boxes)+6*(terminal_count+len(children)+1)+16)

    def root_ports(original: Mapping[str,tuple[Vec3,...]], first_slot: int):
        ports={};slot=first_slot
        for name,pins in original.items():
            ports[name]=tuple(Vec3(physical.origin.x+8*(slot+index),physical.origin.y+1,root_port_z)
                              for index in range(len(pins)))
            slot+=len(pins)
        return ports

    inputs=root_ports(physical.input_ports,0)
    outputs=root_ports(physical.output_ports,max(16,sum(map(len,inputs.values()))+1))
    root_positions={old:new for old_group,new_group in ((physical.input_ports,inputs),(physical.output_ports,outputs))
                    for name,old_pins in old_group.items() for old,new in zip(old_pins,new_group[name])}
    nets=[]
    for net in physical.nets:
        source=net.source
        if source.name in owned:
            group=owned[source.name];child=children[group]
            if f"n{net.bit}" not in child.physical.output_ports:
                continue
            source=Endpoint(f"macro:tile{group}",f"n{net.bit}",child.physical.output_ports[f"n{net.bit}"][0],True)
        else:
            source=replace(source,pos=root_positions[source.pos])
        sinks=[Endpoint(f"macro:tile{index}",f"n{net.bit}",child.physical.input_ports[f"n{net.bit}"][0],False)
               for index,child in enumerate(children) if f"n{net.bit}" in child.physical.input_ports]
        sinks.extend(replace(sink,pos=root_positions[sink.pos]) for sink in net.sinks if sink.pos in root_positions)
        if sinks:
            nets.append(RoutedNet(net.bit,source,tuple(sinks),0))
    parent_physical=PhysicalNetlist(
        top=f"{physical.top}-boundary",origin=physical.origin,cell_origins={},cell_types={},
        input_ports=inputs,output_ports=outputs,nets=tuple(nets),
        constant_high=tuple(root_positions[p] for p in physical.constant_high if p in root_positions),
        north_constant_high=tuple(root_positions[p] for p in physical.north_constant_high if p in root_positions),
        minimum_build_y=physical.minimum_build_y,
    )
    for ports in (inputs,outputs):
        for pins in ports.values():
            for pin in pins:
                access[pin]=("north",)
    roof=max(bounds.max.y for bounds in boxes)
    parent=plan_existing_layout(
        parent_physical,branch_y=roof+14,trunk_y=roof+18,
        obstacles=[*child_blocks,*parent_physical._iter_local_placements("tile-boundary")],
        access=access,endpoint_bounds=endpoint_bounds,obstacle_bounds=boxes,
    )
    for net in parent_physical.nets:
        tails={child.physical.input_ports[f"n{net.bit}"][0]:child.clock_delays[net.bit]["balanced_repeater_delay"]
               for child in children if net.bit in child.clock_delays and f"n{net.bit}" in child.physical.input_ports}
        if tails:
            parent.balance_net(net.bit,tails)
    hierarchy=TileHierarchy(tuple(children),parent)
    hierarchy.conservation=audit_hierarchy(physical,hierarchy)
    return hierarchy
