"""Opt-in complete bounded partitions through the existing mapped top shell.

The default system backend remains unchanged. A bounded partition has no
flat ``nets`` or routing-plane compatibility view: the old resource preflight
must not mistake its hierarchy for one enormous flat routing domain.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
import json
from pathlib import Path
from typing import Mapping

from .hardware.bounded_facade import EgressCluster, audit_egress
from .hardware.bounded_interfaces import facade_port_vectors, facade_routing_interfaces
from .literal_union import literal_union
from shamaos.hardware.external_router import ExternalNet, iter_external_router
from shamaos.hardware.interfaces import RoutingInterfaces
from shamaos.hardware.logic import template_for
from shamaos.hardware.memory import DUST, SUPPORT
from shamaos.hardware.partition_placement import audit_placement, balance_partition_clocks
from shamaos.hardware.partition_planner import Graph, audit_plan, build_plan
from shamaos.model import Box, Placement, Vec3
from shamaos.synthesis import MacroInstance, PhysicalNetlist, build_physical_netlist, _expand_sequential_cells
from shamaos.worldio import validate_position


def _sha(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def _contains(box, point):
    return all(getattr(box.min, axis) <= getattr(point, axis) <= getattr(box.max, axis) for axis in "xyz")


def _verify_planned_clock_matching(plan, placed):
    """Reject missing or changed matching without changing accepted geometry.

    Clones share the immutable route/physical descriptions; only their small
    matching dictionaries are rebuilt. This compares the actual override
    inventory against the existing deterministic matcher. It does not prove
    scheduled-engine timing or include the facade's exterior common prefix.
    """
    def fresh(cluster):
        return replace(cluster,
            modules={key: replace(tile, overrides={}, clock_delays={}, _balanced_positions={})
                     for key, tile in cluster.modules.items()},
            children=tuple(fresh(child) for child in cluster.children))

    replay = fresh(placed)
    report = balance_partition_clocks(plan, replay)
    expected = replay.all_modules()
    for key, tile in placed.all_modules().items():
        other = expected[key]
        if (tile.overrides != other.overrides or tile.clock_delays != other.clock_delays
                or tile._balanced_positions != other._balanced_positions):
            raise ValueError("bounded partition lacks the exact planned clock matching on its physical routes")
    if any(tile.overrides or tile.clock_delays or tile._balanced_positions
           for tile in placed.all_facade_modules().values()):
        raise ValueError("bounded facade has unsupported route or clock overrides")
    return report


def merge_interfaces(*contracts: RoutingInterfaces) -> RoutingInterfaces:
    """Retain every actual keepout and reject conflicting endpoint ownership."""
    if not contracts:
        raise ValueError("at least one physical routing contract is required")
    access, owners, states = {}, {}, {}
    boxes = []
    for contract in contracts:
        for point, faces in contract.access.items():
            if point in access and access[point] != faces:
                raise ValueError("shared endpoint has incompatible access faces")
            access[point] = faces
        for point, owner in contract.endpoint_bounds.items():
            if point in owners and owners[point] != owner:
                raise ValueError("shared endpoint has incompatible component owners")
            owners[point] = owner
        for block in contract.obstacles:
            if block.pos in states and states[block.pos].block != block.block:
                raise ValueError("routing contracts contain conflicting literal obstacles")
            states[block.pos] = block
        boxes.extend(box for box in contract.obstacle_bounds if box not in boxes)
    return RoutingInterfaces(access, tuple(states.values()), owners, tuple(boxes),
                             max(contract.minimum_y for contract in contracts))


@dataclass(frozen=True)
class BoundedPartition:
    graph: Graph
    plan: dict
    placed: EgressCluster
    surface_y: int
    maximum_y: int

    @classmethod
    def from_placed(cls, mapped: Path, top: str, plan: dict, placed: EgressCluster, *,
                    origin: Vec3 | None = None, surface_y=-61, maximum_y=285):
        graph = Graph.read(mapped, top)
        if type(placed) is not EgressCluster:
            raise ValueError("a complete original-I/O facade implementation is required")
        if origin is not None:
            old = placed.bounds.min
            placed = placed.translated(Vec3(origin.x-old.x, origin.y-old.y, origin.z-old.z))
        result = cls(graph, plan, placed, surface_y, maximum_y)
        result.audit()
        return result

    def audit(self):
        if _sha(self.graph.source["path"]) != self.graph.source["container_sha256"]:
            raise ValueError("bounded partition mapped source changed")
        audit_plan(self.graph, self.plan)
        audit_placement(self.graph, self.plan, self.placed)
        audit_egress(self.graph, self.placed)
        _verify_planned_clock_matching(self.plan, self.placed)
        if not -64 <= self.surface_y < self.placed.bounds.min.y <= self.placed.bounds.max.y <= self.maximum_y <= 319:
            raise ValueError("bounded partition leaves its above-grass routing envelope")
        validate_position(self.placed.bounds.min)
        validate_position(self.placed.bounds.max)
        facade_routing_interfaces(self.graph, self.placed)

    @property
    def top(self): return self.graph.source["top"]
    @property
    def origin(self): return self.placed.bounds.min
    @property
    def bounds(self): return self.placed.bounds
    @property
    def input_ports(self): return facade_port_vectors(self.graph, self.placed)[0]
    @property
    def output_ports(self): return facade_port_vectors(self.graph, self.placed)[1]
    @property
    def cell_origins(self):
        return {name: point for tile in self.placed.all_modules().values() for name, point in tile.physical.cell_origins.items()}
    @property
    def cell_types(self):
        return {name: kind for tile in self.placed.all_modules().values() for name, kind in tile.physical.cell_types.items()}

    def manifest(self):
        modules = (*self.placed.all_modules().values(), *self.placed.all_facade_modules().values())
        return {"top": self.top, "cell_count": self.graph.cell_count,
                "net_count": sum(len(tile.physical.nets) for tile in modules),
                "mapped_signal_count": len(self.graph.signals),
                "routing_tracks": sum(len(set(tile.tracks.values())) for tile in modules),
                "strategy": "complete-bounded-original-io-facade", "bounds": asdict(self.bounds),
                "original_mapped_source": self.graph.source,
                "input_ports": {name: [asdict(p) for p in pins] for name, pins in self.input_ports.items()},
                "output_ports": {name: [asdict(p) for p in pins] for name, pins in self.output_ports.items()},
                "native_modules": len(self.placed.all_modules()),
                "facade_modules": len(self.placed.all_facade_modules()),
                "original_port_bits": len(self.placed.ports),
                "cache_layout_policy": self.placed.packing.get("cache_layout_policy"),
                "planned_clock_matching_scope": "Exact original-input/generated-source-to-register matching is required; exterior facade prefix and runtime timing remain unverified",
                "complete_machine_runtime_verified": False, "electrical_route_isolation_verified": False}

    def iter_placements(self, *, component=None):
        self.audit()
        component = component or f"bounded-{self.top}"
        for block in self.placed.iter_placements():
            if not _contains(self.bounds, block.pos) or not self.surface_y < block.pos.y <= self.maximum_y:
                raise ValueError("bounded emitted block leaves the declared above-grass envelope")
            validate_position(block.pos)
            yield replace(block, component=f"{component}:{block.component}")


def _strict_shell(path, top, partitions):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result: raise ValueError("mapped shell repeats a JSON field")
            result[key] = value
        return result
    raw = json.loads(Path(path).read_text(), object_pairs_hook=unique)
    module = raw["modules"][top]
    _expand_sequential_cells(module)
    claimed = set()
    for name, cell in module.get("cells", {}).items():
        key = name.lstrip("\\")
        part = partitions.get(key)
        connections = cell["connections"]
        if part is not None:
            if key in claimed or cell["type"].lstrip("\\") != part.top:
                raise ValueError("shell macro identity or module type differs from its bounded partition")
            claimed.add(key)
            expected = {**{pin: ("input", len(bits)) for pin, bits in part.input_ports.items()},
                        **{pin: ("output", len(bits)) for pin, bits in part.output_ports.items()}}
        else:
            template = template_for(cell["type"].upper())
            expected = {pin: ("output" if pin in ("Q", "Y") else "input", 1) for pin in template.pins}
        if set(connections) != set(expected):
            raise ValueError("mapped shell omits or invents a physical cell/macro port")
        for pin, bits in connections.items():
            direction, width = expected[pin]
            if len(bits) != width or cell.get("port_directions", {}).get(pin, direction) != direction:
                raise ValueError("mapped shell port width or direction differs from the complete implementation")
            _bits(bits, driving=direction == "output")
    if claimed != set(partitions):
        raise ValueError("bounded implementations must match every shell macro exactly once")
    for port in module.get("ports", {}).values():
        if port["direction"] not in ("input", "output"):
            raise ValueError("mapped shell has an unsupported port direction")
        _bits(port["bits"], driving=port["direction"] == "input")
    return module


def _bits(bits, *, driving):
    if not isinstance(bits, list) or not bits:
        raise ValueError("mapped bus must retain its nonempty original bit vector")
    for bit in bits:
        if type(bit) is int and bit >= 0: continue
        if bit in ("0", "1") and not driving: continue
        raise ValueError("mapped shell has an unknown or constant-valued driving bit")


def _front_ports(physical, front_z):
    moved = {p: Vec3(p.x, p.y, front_z) for ports in (physical.input_ports, physical.output_ports)
             for pins in ports.values() for p in pins}
    endpoint = lambda end: replace(end, pos=moved[end.pos]) if end.name.startswith("port:") else end
    return replace(physical,
        input_ports={name: tuple(moved[p] for p in pins) for name, pins in physical.input_ports.items()},
        output_ports={name: tuple(moved[p] for p in pins) for name, pins in physical.output_ports.items()},
        constant_high=tuple(moved.get(p, p) for p in physical.constant_high),
        north_constant_high=tuple(moved[p] for p in physical.north_constant_high),
        nets=tuple(replace(net, source=endpoint(net.source), sinks=tuple(endpoint(sink) for sink in net.sinks)) for net in physical.nets))


@dataclass(frozen=True)
class BoundedShell:
    physical: PhysicalNetlist
    routing: RoutingInterfaces
    source: dict
    maximum_y: int
    track_base_z: int | None

    @property
    def input_ports(self): return self.physical.input_ports
    @property
    def output_ports(self): return self.physical.output_ports

    def manifest(self):
        report = self.physical.manifest()
        report.update(strategy="mapped-shell-with-complete-bounded-component-keepouts",
                      source=self.source, track_base_z=self.track_base_z,
                      complete_machine_runtime_verified=False)
        return report

    def iter_placements(self, *, component="bounded-shell"):
        if _sha(self.source["path"]) != self.source["sha256"]:
            raise ValueError("mapped shell source changed after binding")
        for block in self.physical._iter_local_placements(component):
            validate_position(block.pos)
            if not self.physical.minimum_build_y <= block.pos.y <= self.maximum_y:
                raise ValueError("shell native block leaves the above-grass envelope")
            yield block
        nets = tuple(ExternalNet(f"shell-bit-{net.bit}", net.source.pos, tuple(p.pos for p in net.sinks)) for net in self.physical.nets)
        yield from iter_external_router(nets, component=f"{component}:routes",
            branch_y=self.physical.routing_branch_y, trunk_y=self.physical.routing_trunk_y,
            track_spacing=self.physical.track_pitch, minimum_y=self.physical.minimum_build_y,
            maximum_y=self.maximum_y, access=self.routing.access, obstacles=self.routing.obstacles,
            endpoint_bounds=self.routing.endpoint_bounds, obstacle_bounds=self.routing.obstacle_bounds,
            track_base_z=self.track_base_z)


@dataclass(frozen=True)
class BoundedSystemLogic:
    shell: BoundedShell
    partitions: tuple[tuple[str, BoundedPartition], ...]

    @property
    def input_ports(self): return self.shell.input_ports
    @property
    def output_ports(self): return self.shell.output_ports

    def manifest(self):
        parts = {name: part.manifest() for name, part in self.partitions}
        shell = self.shell.manifest()
        return {"top": shell["top"], "strategy": "experimental-bounded-partitions-and-mapped-shell",
                "cell_count": shell["cell_count"] + sum(p["cell_count"] for p in parts.values()),
                "net_count": shell["net_count"] + sum(p["net_count"] for p in parts.values()),
                "routing_tracks": shell["routing_tracks"] + sum(p["routing_tracks"] for p in parts.values()),
                "shell": shell, "partitions": parts,
                "input_ports": shell["input_ports"], "output_ports": shell["output_ports"],
                "complete_machine_runtime_verified": False, "complete_machine_release_eligible": False,
                "resource_scope": "Bounded resource preflight retains regular/global lower bounds and requires the exact emitted child-and-shell union; there is no substitute flat routing plane"}

    def iter_placements(self, *, component="shama-soc"):
        for name, part in self.partitions:
            yield from part.iter_placements(component=f"{component}:partition:{name}")
        yield from self.shell.iter_placements(component=f"{component}:shell")


def prepare_bounded_logic(shell_mapped, *, shell_top, partitions: Mapping[str, BoundedPartition],
                          shell_origin: Vec3, branch_y=300, trunk_y=304,
                          maximum_y=319, track_base_z=None, front_clearance=96):
    """Return the same ports/manifest/emitter seam consumed by system_build.

    All child artifacts must already be fully placed. Their original mapped
    bus vectors bind directly into the existing MacroInstance implementation.
    Only the shell's original top terminals move into a clear exterior strip;
    every associated endpoint and constant tie moves with the same vector.
    """
    partitions = dict(partitions)
    if not partitions or any(not name or name.startswith("\\") for name in partitions):
        raise ValueError("canonical complete shell partition bindings are required")
    if front_clearance < 16:
        raise ValueError("the shell exterior strip requires explicit clearance")
    for part in partitions.values(): part.audit()
    if len({part.surface_y for part in partitions.values()}) != 1:
        raise ValueError("complete system partitions must share one protected grass surface")
    minimum = max(part.surface_y + 1 for part in partitions.values())
    if shell_origin.y < minimum or branch_y < max(p.bounds.max.y for p in partitions.values()) + 14 or trunk_y <= branch_y or trunk_y > maximum_y:
        raise ValueError("shell routing planes do not clear every bounded partition above grass")
    boxes = [part.bounds for part in partitions.values()]
    if any(first.intersects(second) for i, first in enumerate(boxes) for second in boxes[i+1:]):
        raise ValueError("complete bounded partition envelopes overlap")
    source = {"path": str(Path(shell_mapped).resolve()), "sha256": _sha(shell_mapped), "top": shell_top}
    _strict_shell(shell_mapped, shell_top, partitions)
    macros = {name: MacroInstance(name, part.top, part) for name, part in partitions.items()}
    physical = build_physical_netlist(shell_mapped, top=shell_top, origin=shell_origin,
        macro_instances=macros, routing_branch_y=branch_y, routing_trunk_y=trunk_y, minimum_build_y=minimum)
    local = tuple(physical._iter_local_placements("shell-native"))
    front = max([*(box.max.z for box in boxes), *(block.pos.z for block in local)]) + front_clearance
    physical = _front_ports(physical, front)
    local = tuple(physical._iter_local_placements("shell-native"))
    if any(_contains(box, block.pos) for box in boxes for block in local):
        raise ValueError("shell native placement intrudes into a complete bounded partition")
    native_access = {p: ("north",) for ports in (physical.input_ports, physical.output_ports) for pins in ports.values() for p in pins}
    local_contract = RoutingInterfaces(native_access, local, {}, (), minimum)
    routing = merge_interfaces(*(facade_routing_interfaces(p.graph, p.placed) for p in partitions.values()), local_contract)
    if _sha(shell_mapped) != source["sha256"]:
        raise ValueError("mapped shell changed while binding complete partitions")
    return BoundedSystemLogic(BoundedShell(physical, routing, source, maximum_y, track_base_z), tuple(partitions.items()))


@dataclass(frozen=True)
class MeasuredBoundedLogic:
    logic: BoundedSystemLogic
    geometry: dict
    routing: RoutingInterfaces

    @property
    def shell(self): return self.logic.shell
    @property
    def partitions(self): return self.logic.partitions
    @property
    def input_ports(self): return self.logic.input_ports
    @property
    def output_ports(self): return self.logic.output_ports

    def manifest(self):
        return {**self.logic.manifest(), "exact_logic_geometry": self.geometry,
                "global_routing_keepout": asdict(self.routing.obstacle_bounds[0])}

    def iter_placements(self, *, component="shama-soc"):
        yield from self.logic.iter_placements(component=component)


def measure_logic(logic: BoundedSystemLogic, *, temporary_parent=None) -> MeasuredBoundedLogic:
    """Measure every emitted block before exposing a whole-component keepout.

    This is the exact geometry input needed by global interfaces/resource
    accounting. It does not certify route isolation, clock/data timing or boot.
    """
    geometry = literal_union(logic.iter_placements(), temporary_parent=temporary_parent)
    bounds = Box(Vec3(**geometry["bounds"]["min"]), Vec3(**geometry["bounds"]["max"]))
    pins = tuple(p for ports in (logic.input_ports, logic.output_ports) for values in ports.values() for p in values)
    if len(set(pins)) != len(pins) or any(p.z != bounds.max.z for p in pins):
        raise ValueError("the complete shell's original I/O strip is buried or aliased")
    obstacles = tuple(block for p in pins for block in (Placement(p.offset(dy=-1), SUPPORT, "system-original-io"), Placement(p, DUST, "system-original-io")))
    routing = RoutingInterfaces(dict.fromkeys(pins, ("south",)), obstacles, dict.fromkeys(pins, bounds), (bounds,), logic.shell.physical.minimum_build_y)
    geometry.update(complete_machine_runtime_verified=False, electrical_route_isolation_verified=False,
                    complete_machine_release_eligible=False)
    return MeasuredBoundedLogic(logic, geometry, routing)


def prepare_from_artifacts(partition_artifacts, shell_mapped, *, shell_top="shama_soc",
                           base_origin=Vec3(0, -60, 0), minimum_build_y=-60, options=None):
    """Place complete mapped children; whole-logic measurement happens later.

    The top-level generator must run the conservative resource check before
    calling measure_logic. Native leaf and parent collision checks remain
    mandatory during placement. No graph, port or route is truncated to fit.
    """
    from .hardware.bounded_facade import place_with_egress

    defaults = {"cell_budget": 64, "boundary_budget": 128, "domain_budget": 128,
                "arity": 4, "maximum_partition_y": 285, "component_gap": 128,
                "cache_compaction": 1,
                "shell_branch_y": 300, "shell_trunk_y": 304,
                "shell_front_clearance": 96, "shell_track_base_z": None}
    options = dict(options or {})
    if set(options) - defaults.keys():
        raise ValueError(f"unknown bounded logic options: {sorted(set(options) - defaults.keys())}")
    settings = {**defaults, **options}
    for name, value in settings.items():
        if name == "shell_track_base_z" and value is None: continue
        if type(value) is not int:
            raise ValueError(f"bounded logic option {name} must be an exact integer")
    if settings["component_gap"] < 16 or settings["maximum_partition_y"] > 285:
        raise ValueError("bounded components need separation and reserved shell/global routing height")
    if settings["cache_compaction"] not in (0, 1):
        raise ValueError("bounded cache_compaction must be zero or one")
    if minimum_build_y < -63 or settings["maximum_partition_y"] <= minimum_build_y:
        raise ValueError("bounded logic has no above-grass placement envelope")
    artifacts = tuple(partition_artifacts)
    names = [name for name, _, _ in artifacts]
    if not names or len(set(names)) != len(names):
        raise ValueError("complete shell partition instance names must be unique")

    partitions = {}
    right = base_origin.x - 1024
    front = base_origin.z - 1024
    for name, top, path in artifacts:
        graph = Graph.read(path, top)
        from .hardware.cache_layout import cache_policy, place_compact_cache
        policy = cache_policy(graph) if name == "u_cache" and settings["cache_compaction"] else None
        if policy is not None:
            if (settings["cell_budget"], settings["boundary_budget"], settings["arity"]) != (64, 128, 4):
                raise ValueError("compact cache policy requires cell/boundary/arity 64/128/4; use cache_compaction=0 for a different unverified layout")
            plan, placed = place_compact_cache(graph, surface_y=minimum_build_y-1,
                                               maximum_y=settings["maximum_partition_y"])
        else:
            plan = build_plan(graph, cell_budget=settings["cell_budget"], boundary_budget=settings["boundary_budget"],
                              domain_budget=settings["domain_budget"], arity=settings["arity"])
            placed = place_with_egress(graph, plan, surface_y=minimum_build_y-1,
                                      maximum_y=settings["maximum_partition_y"])
        offset = Vec3(right-placed.bounds.max.x, minimum_build_y-placed.bounds.min.y, front-placed.bounds.max.z)
        part = BoundedPartition(graph, plan, placed.translated(offset), minimum_build_y-1,
                                settings["maximum_partition_y"])
        part.audit()
        partitions[name] = part
        right = part.bounds.min.x - settings["component_gap"]

    # A metadata-only provisional shell determines the space needed by its
    # native cells/ports. Place it to the west of the complete child district,
    # without assuming fixed-size 260,000-block partition slots.
    _strict_shell(shell_mapped, shell_top, partitions)
    provisional = build_physical_netlist(shell_mapped, top=shell_top,
        origin=Vec3(0, minimum_build_y, front), minimum_build_y=minimum_build_y,
        macro_instances={name: MacroInstance(name, part.top, part) for name, part in partitions.items()})
    shell_origin = Vec3(right-int(provisional.manifest()["max_x"]), minimum_build_y, front)
    return prepare_bounded_logic(shell_mapped, shell_top=shell_top, partitions=partitions,
        shell_origin=shell_origin, branch_y=settings["shell_branch_y"], trunk_y=settings["shell_trunk_y"],
        track_base_z=settings["shell_track_base_z"], front_clearance=settings["shell_front_clearance"])
