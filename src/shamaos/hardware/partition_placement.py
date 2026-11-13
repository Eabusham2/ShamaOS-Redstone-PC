"""Experimental complete-plan placement with bounded native routing domains.

Every native cell, original port and graph boundary keeps its planner identity.
Child routes use the north face of a boundary contact; enclosing routes use its
south face. Ancestor route tiers are reserved before stacking, and extra child
groups move horizontally instead of clipping against the world height limit.

This module is not selected by the production generator. Literal geometry,
structural conservation and scheduled-engine timing are separate gates.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass, field, replace
from itertools import combinations
from typing import Iterator, Mapping

from .external_router import _Escape, _nearby_wire_points
from .local_router import LocalTile, compact_tile, plan_bounded_domains
from .logic import template_for
from .memory import DUST, SUPPORT
from .partition_planner import Graph, audit_plan
from ..model import BlockState, Box, Placement, Vec3
from ..synthesis import Endpoint, PhysicalNetlist, RoutedNet


class PlacementError(ValueError):
    pass


def _translate(point: Vec3, delta: Vec3) -> Vec3:
    return point.offset(delta.x, delta.y, delta.z)


def _box(points) -> Box:
    points = tuple(points)
    if not points:
        raise PlacementError("an empty physical module has no placeable envelope")
    return Box(Vec3(*(min(getattr(p, axis) for p in points) for axis in ("x", "y", "z"))),
               Vec3(*(max(getattr(p, axis) for p in points) for axis in ("x", "y", "z"))))


def translate_tile(tile: LocalTile, delta: Vec3) -> LocalTile:
    """Translate already planned literal geometry; no state or route is replaced."""
    move = lambda point: _translate(point, delta)
    endpoint = lambda value: replace(value, pos=move(value.pos))
    old = tile.physical
    physical = replace(old, origin=move(old.origin),
        cell_origins={name: move(point) for name, point in old.cell_origins.items()},
        input_ports={name: tuple(map(move, pins)) for name, pins in old.input_ports.items()},
        output_ports={name: tuple(map(move, pins)) for name, pins in old.output_ports.items()},
        nets=tuple(replace(net, source=endpoint(net.source), sinks=tuple(map(endpoint, net.sinks)))
                   for net in old.nets),
        constant_high=tuple(map(move, old.constant_high)),
        north_constant_high=tuple(map(move, old.north_constant_high)))
    escapes = {move(point): _Escape(move(value.endpoint), value.escape_z + delta.z,
        value.branch_x + delta.x, None if value.escape_x is None else value.escape_x + delta.x,
        None if value.wall_lead_z is None else value.wall_lead_z + delta.z, value.branch_direction)
        for point, value in tile.escapes.items()}
    return LocalTile(physical, escapes, dict(tile.tracks), tile.track_base_z + delta.z,
        tile.branch_y + delta.y, tile.trunk_y + delta.y,
        {move(point): state for point, state in tile.overrides.items()},
        {bit: dict(record) for bit, record in tile.clock_delays.items()},
        {bit: set(map(move, points)) for bit, points in tile._balanced_positions.items()})


@dataclass
class PlacedCluster:
    cluster_id: int
    bounds: Box
    children: tuple[PlacedCluster, ...]
    modules: dict[tuple[str, int], LocalTile]
    terminals: dict[str, Vec3]
    ports: dict[int, Vec3]
    unique_blocks: int
    shared_contacts: dict[str, dict] = field(default_factory=dict)
    packing: dict = field(default_factory=dict)

    def iter_placements(self) -> Iterator[Placement]:
        for child in self.children:
            yield from child.iter_placements()
        for module in self.modules.values():
            yield from module.iter_placements()

    def all_modules(self) -> dict[tuple[str, int], LocalTile]:
        result = {}
        for child in self.children:
            child_modules = child.all_modules()
            if result.keys() & child_modules.keys():
                raise PlacementError("a physical module occurs more than once in the hierarchy")
            result.update(child_modules)
        if result.keys() & self.modules.keys():
            raise PlacementError("a parent duplicates a descendant physical module")
        result.update(self.modules)
        return result

    def translated(self, delta: Vec3) -> PlacedCluster:
        move = lambda point: _translate(point, delta)
        return replace(self,
            bounds=Box(move(self.bounds.min), move(self.bounds.max)),
            children=tuple(child.translated(delta) for child in self.children),
            modules={key: translate_tile(tile, delta) for key, tile in self.modules.items()},
            terminals={name: move(point) for name, point in self.terminals.items()},
            ports={entity: move(point) for entity, point in self.ports.items()},
            shared_contacts={name: dict(record, position=move(record["position"]))
                             for name, record in self.shared_contacts.items()})


class _Context:
    def __init__(self, plan: dict, surface_y: int, maximum_y: int):
        self.plan = plan
        self.entities = {record["id"]: record for record in plan["entities"]}
        self.clusters = {record["id"]: record for record in plan["clusters"]}
        self.signals = {record["bit"]: record for record in plan["signals"]}
        self.links = {link["id"]: dict(link, bit=signal["bit"])
                      for signal in plan["signals"] for link in signal["boundary_links"]}
        self.domains = defaultdict(list)
        for domain in plan["routing_domains"]:
            self.domains[domain["cluster_id"]].append(domain)
        self.minimum_y = surface_y + 1
        self.maximum_y = maximum_y

    def route_height(self, cluster_id: int) -> int:
        # Every bounded domain keeps its own tracks and terminal inventory on a
        # common crossing-safe plane pair. A large domain count expands into
        # horizontal track bands rather than consuming impossible Y tiers.
        return 18 if self.domains[cluster_id] else 0


def _literal_states(placements: Iterator[Placement]) -> dict[Vec3, BlockState]:
    states = {}
    for placement in placements:
        previous = states.setdefault(placement.pos, placement.block)
        if previous != placement.block:
            raise PlacementError(f"incompatible blocks at {placement.pos}: {previous} / {placement.block}")
    return states


def _wire_headroom(states: Mapping[Vec3, BlockState]) -> set[Vec3]:
    """Space above a rising dust connection must not acquire another support."""
    # A wire two above would put its support here and block the climb. A support
    # two above with air directly above the lower wire is valid: its own wire is
    # three above. Actual independent-circuit runtime tests cover both cases.
    return {point.offset(dy=1) for point, state in states.items()
            if state.name == "minecraft:redstone_wire" and "up" in dict(state.properties).values()}


def _check_domain_isolation(previous: Mapping[Vec3, BlockState], current: Mapping[Vec3, BlockState]) -> None:
    """Different bounded domains share no wire, contact or climbing headroom."""
    if not previous:
        return
    active = {point for point, state in previous.items() if state != SUPPORT}
    for point, state in current.items():
        if state != SUPPORT and any(near in active for near in _nearby_wire_points(point)):
            raise PlacementError(f"different bounded domains have adjacent active wires at {point}")
        if point in previous and previous[point] != state:
            raise PlacementError(f"parent routing domains collide at {point}")
    if _wire_headroom(previous).intersection(current) or _wire_headroom(current).intersection(previous):
        raise PlacementError("a bounded domain obstructs another domain's climbing headroom")


def _leaf(context: _Context, cluster_id: int) -> PlacedCluster:
    cluster = context.clusters[cluster_id]
    if cluster["children"]:
        raise PlacementError("a native leaf cannot own child clusters")
    origin = Vec3(0, context.minimum_y, 0)
    owned = set(cluster["cell_ids"]) | set(cluster["port_ids"])
    cells = {entity: context.entities[entity] for entity in cluster["cell_ids"]}
    origins = {entity["name"]: origin.offset(32 + 32 * index)
               for index, entity in enumerate(cells.values())}
    kinds = {entity["name"]: entity["cell_type"] for entity in cells.values()}
    inputs, outputs, slots, original_ports = {}, {}, {}, {}

    def port(name: str, direction: str) -> Endpoint:
        if name not in slots:
            slots[name] = origin.offset(4 * len(slots), 1, 1000)
            (inputs if direction == "input" else outputs)[name] = (slots[name],)
        return Endpoint(f"port:{name}[0]", name, slots[name], direction == "input")

    for entity_id in cluster["port_ids"]:
        entity = context.entities[entity_id]
        name = f"entity{entity_id}"
        port(name, entity["direction"])
        original_ports[entity_id] = name

    def native(record, source):
        entity = context.entities[record["entity"]]
        if entity["kind"] == "port":
            return port(f"entity{entity['id']}", entity["direction"])
        if record["index"] != 0:
            raise PlacementError("expanded native cell pins must be scalar")
        offset = template_for(entity["cell_type"]).pins[record["pin"]]
        return Endpoint(entity["name"], record["pin"],
                        _translate(origins[entity["name"]], offset), source)

    nets = []
    boundary_names = set()
    for signal in context.signals.values():
        local_source = signal["source"]["entity"] in owned
        local_sinks = [sink for sink in signal["sinks"] if sink["entity"] in owned]
        if not local_source and not local_sinks:
            continue
        source = native(signal["source"], True) if local_source else None
        sinks = [native(sink, False) for sink in local_sinks]
        entering = [link for link in signal["boundary_links"] if link["sink"]["cluster"] == cluster_id]
        leaving = [link for link in signal["boundary_links"] if link["source"]["cluster"] == cluster_id]
        if not local_source:
            if len(entering) != 1:
                raise PlacementError("native input is missing its unique directed boundary")
            name = entering[0]["sink"]["terminal_id"]
            source = port(name, "input")
            boundary_names.add(name)
        for link in leaving:
            if not local_source:
                raise PlacementError("a native leaf cannot silently replace an internal router")
            name = link["source"]["terminal_id"]
            sinks.append(port(name, "output"))
            boundary_names.add(name)
        if sinks:
            nets.append(RoutedNet(signal["bit"], source, tuple(sinks), 0))
    constants, north_constants = [], []
    for constant in context.plan["constants"]:
        if constant["endpoint"]["entity"] not in owned:
            continue
        if constant["value"] not in ("0", "1"):
            raise PlacementError("unknown constants cannot be silently grounded")
        if constant["value"] == "1":
            endpoint = native(constant["endpoint"], False)
            constants.append(endpoint.pos)
            if endpoint.name.startswith("port:"):
                north_constants.append(endpoint.pos)
    physical = PhysicalNetlist(f"leaf-{cluster_id}", origin, origins, kinds, inputs, outputs,
        tuple(nets), tuple(constants), minimum_build_y=context.minimum_y,
        north_constant_high=tuple(north_constants))
    if nets:
        tile = compact_tile(physical, max_cells=context.plan["budgets"]["native_cells_per_leaf"])
    else:
        # An unused original port or a cell driven exclusively by constants is
        # still physical hardware. It must not disappear merely for lacking a net.
        moved = {point: origin.offset(4 * index, 1, 48)
                 for index, point in enumerate(slots.values())}
        physical = replace(physical,
            input_ports={name: tuple(moved[point] for point in pins) for name, pins in inputs.items()},
            output_ports={name: tuple(moved[point] for point in pins) for name, pins in outputs.items()},
            constant_high=tuple(moved.get(point, point) for point in physical.constant_high),
            north_constant_high=tuple(moved[point] for point in physical.north_constant_high))
        tile = LocalTile(physical, {}, {}, 0, origin.y + 15, origin.y + 19)
    states = _literal_states(tile.iter_placements())
    terminals = {name: pins[0] for ports in (tile.physical.input_ports, tile.physical.output_ports)
                 for name, pins in ports.items() if name in boundary_names}
    ports = {entity: (tile.physical.input_ports | tile.physical.output_ports)[name][0]
             for entity, name in original_ports.items()}
    return PlacedCluster(cluster_id, _box(states), (), {("leaf", cluster_id): tile},
                         terminals, ports, len(states))


def _pack_children(children: tuple[PlacedCluster, ...], *, minimum_y: int,
                   maximum_y: int, vertical_gap: int = 4, horizontal_gap: int = 16,
                   horizontal_layout: str = "compact"):
    """Pack every safe vertical grouping, then choose its compact horizontal grid.

    The planner caps child arity, so exhaustive grouping is small. Using actual
    heights avoids wasting the space above short interface-only children.
    """
    if not children:
        raise PlacementError("parent placement needs children")
    if horizontal_layout not in ("compact", "row"):
        raise PlacementError("unknown horizontal child-packing layout")
    capacity = maximum_y - minimum_y + 1
    if any(child.bounds.size.y > capacity for child in children):
        raise PlacementError("child and reserved ancestor routes cannot fit above grass within build height")
    if len(children) > 8:
        raise PlacementError("exhaustive physical packing requires a bounded child arity of at most eight")
    groups = []

    def partition(index, current):
        if index == len(children):
            groups.append(tuple(tuple(stack) for stack in current))
            return
        height = children[index].bounds.size.y
        for stack in current:
            used = sum(children[item].bounds.size.y for item in stack) + vertical_gap * len(stack)
            if used + height <= capacity:
                stack.append(index)
                partition(index + 1, current)
                stack.pop()
        current.append([index])
        partition(index + 1, current)
        current.pop()

    partition(0, [])
    choices = []
    for stacks in groups:
        widths = [max(children[index].bounds.size.x for index in stack) for stack in stacks]
        depths = [max(children[index].bounds.size.z for index in stack) for stack in stacks]
        # The small fixed-arity grids are deterministic; no stochastic floorplan
        # can silently drop a child or claim a vertical tier that does not fit.
        for columns in range(1, len(stacks) + 1):
            if horizontal_layout == "row" and columns != len(stacks):
                continue
            rows = (len(stacks) + columns - 1) // columns
            column_widths = [max(widths[index] for index in range(column, len(stacks), columns))
                             for column in range(columns)]
            row_depths = [max(depths[row * columns:min(len(stacks), (row + 1) * columns)])
                          for row in range(rows)]
            x_offsets, z_offsets = [0], [0]
            for width in column_widths[:-1]:
                x_offsets.append(x_offsets[-1] + ((width + horizontal_gap + 15) // 16) * 16)
            for depth in row_depths[:-1]:
                z_offsets.append(z_offsets[-1] + ((depth + horizontal_gap + 15) // 16) * 16)
            width, depth = x_offsets[-1] + column_widths[-1], z_offsets[-1] + row_depths[-1]
            choices.append(((width * depth, max(width, depth), width + depth, len(stacks), stacks, columns),
                            stacks, columns, x_offsets, z_offsets))
    _, stacks, columns, x_offsets, z_offsets = min(choices, key=lambda item: item[0])
    result = []
    for stack_index, stack in enumerate(stacks):
        y = minimum_y
        for index in stack:
            child = children[index]
            base = Vec3(x_offsets[stack_index % columns], y, z_offsets[stack_index // columns])
            result.append(child.translated(Vec3(base.x - child.bounds.min.x,
                base.y - child.bounds.min.y, base.z - child.bounds.min.z)))
            y += child.bounds.size.y + vertical_gap
    for first, second in combinations(result, 2):
        if first.bounds.intersects(second.bounds):
            raise PlacementError("packed child envelopes overlap")
    return tuple(result), {"vertical_tiers": max(map(len, stacks)), "horizontal_stacks": len(stacks),
        "horizontal_columns": columns, "horizontal_layout": horizontal_layout,
        "child_heights": [child.bounds.size.y for child in children],
        "stacked_child_ids": [[children[index].cluster_id for index in stack] for stack in stacks],
        "reserved_maximum_child_y": maximum_y, "vertical_gap": vertical_gap}


def _parent(context: _Context, cluster_id: int, children: tuple[PlacedCluster, ...],
            maximum_y: int) -> PlacedCluster:
    height = context.route_height(cluster_id)
    attempts, seen = [], set()
    for layout in ("compact", "row"):
        packed, packing = _pack_children(children, minimum_y=context.minimum_y,
            maximum_y=maximum_y - height, horizontal_layout=layout)
        signature = tuple((child.cluster_id, child.bounds) for child in packed)
        if signature in seen:
            continue
        seen.add(signature)
        try:
            placed = _connect_parent(context, cluster_id, packed, maximum_y, packing)
            placed.packing["rejected_layouts"] = attempts
            return placed
        except PlacementError as error:
            attempts.append({"horizontal_layout": layout, "error": str(error)})
    raise PlacementError(f"cluster {cluster_id} has no routed child packing: {attempts}")


def _connect_parent(context: _Context, cluster_id: int, children: tuple[PlacedCluster, ...],
                    maximum_y: int, packing: dict) -> PlacedCluster:
    child_by_id = {child.cluster_id: child for child in children}
    boxes = [child.bounds for child in children]
    roof = max(box.max.y for box in boxes)
    own_links = [link for link in context.links.values()
                 if (link["source"]["cluster"] == cluster_id or link["sink"]["cluster"] == cluster_id)]
    external = []
    for link in own_links:
        own, other = (("source", "sink") if link["source"]["cluster"] == cluster_id else ("sink", "source"))
        if link[other]["cluster"] not in child_by_id:
            external.append((link, own))
    # Reserve actual bounded interface space. Every root contact remains on the
    # outer south face, permitting a higher parent to use its opposite face.
    front_z = max(box.max.z for box in boxes) + 6 * (len(external) + len(children) + 1) + 16
    external_points = {}
    for index, (link, end) in enumerate(external):
        external_points[link[end]["terminal_id"]] = Vec3(8 * index, context.minimum_y + 1, front_z)
    modules, parent_states, own_terminals, shared = {}, {}, {}, {}
    child_terminals = {name: point for child in children for name, point in child.terminals.items()}
    physical_domains = []
    access, endpoint_bounds = {}, {}

    for domain in context.domains[cluster_id]:
        inputs, outputs, nets = {}, {}, []

        def endpoint(link, end, source):
            own = link[end]
            other_end = "sink" if end == "source" else "source"
            other = link[other_end]
            name = own["terminal_id"]
            if other["cluster"] in child_by_id:
                point = child_terminals[other["terminal_id"]]
                access[point] = ("south",)
                endpoint_bounds[point] = child_by_id[other["cluster"]].bounds
                shared[link["id"]] = {"position": point, "bit": link["bit"],
                    "source_terminal": link["source"]["terminal_id"],
                    "sink_terminal": link["sink"]["terminal_id"],
                    "child_cluster": other["cluster"], "parent_cluster": cluster_id,
                    "child_access": "north", "parent_access": "south"}
            else:
                point = external_points[name]
                (inputs if source else outputs)[name] = (point,)
                access[point] = ("north",)
            own_terminals[name] = point
            return Endpoint(f"boundary:{name}", name, point, source)

        for segment in domain["segments"]:
            signal = context.signals[segment["net"]]
            links = [context.links[link["id"]] for link in signal["boundary_links"]]
            incoming = [link for link in links if link["sink"]["domain"] == domain["id"]]
            outgoing = [link for link in links if link["source"]["domain"] == domain["id"]]
            if len(incoming) != 1 or not outgoing:
                raise PlacementError("domain segment lost its directed source or sinks")
            source = endpoint(incoming[0], "sink", True)
            sinks = tuple(endpoint(link, "source", False) for link in outgoing)
            nets.append(RoutedNet(signal["bit"], source, sinks, 0))
        terminals = sum(1 + len(net.sinks) for net in nets)
        if terminals != domain["terminal_count"] or terminals > context.plan["budgets"]["routing_terminals_per_domain"]:
            raise PlacementError("domain exceeds or differs from its exact planned terminal budget")
        physical = PhysicalNetlist(f"domain-{domain['id']}", Vec3(0, context.minimum_y, front_z),
            {}, {}, inputs, outputs, tuple(nets), (), minimum_build_y=context.minimum_y)
        physical_domains.append((domain["id"], physical))
    try:
        tiles = plan_bounded_domains([physical for _, physical in physical_domains],
            branch_y=roof + 14, trunk_y=roof + 18,
            obstacles=(block for _, physical in physical_domains
                       for block in physical._iter_local_placements("domain-native")),
            access=access, endpoint_bounds=endpoint_bounds, obstacle_bounds=boxes,
            max_domain_terminals=context.plan["budgets"]["routing_terminals_per_domain"])
    except ValueError as error:
        raise PlacementError(f"cluster {cluster_id}, bounded parent escape prefixes: {error}") from error
    for (domain_id, _), tile in zip(physical_domains, tiles):
        states = _literal_states(tile.iter_placements())
        _check_domain_isolation(parent_states, states)
        for point, state in states.items():
            old = parent_states.setdefault(point, state)
            if old != state:
                raise PlacementError(f"parent routing domains collide at {point}")
        modules[("domain", domain_id)] = tile

    expected_shared = {record["position"]: DUST for record in shared.values()}
    expected_shared.update({point.offset(dy=-1): SUPPORT for point in tuple(expected_shared)})
    duplicates = 0
    for point, state in parent_states.items():
        if any(box.min.x <= point.x <= box.max.x and box.min.y <= point.y <= box.max.y
               and box.min.z <= point.z <= box.max.z for box in boxes):
            if expected_shared.get(point) != state:
                raise PlacementError(f"parent route intrudes into a child envelope at {point}")
            duplicates += 1
    bounds = _box([*(point for box in boxes for point in (box.min, box.max)), *parent_states])
    if bounds.min.y < context.minimum_y or bounds.max.y > maximum_y:
        raise PlacementError("connected hierarchy leaves the declared above-grass height envelope")
    terminals = {**child_terminals, **own_terminals}
    contacts = {name: record for child in children for name, record in child.shared_contacts.items()}
    contacts.update(shared)
    return PlacedCluster(cluster_id, bounds, children, modules, terminals,
        {entity: point for child in children for entity, point in child.ports.items()},
        sum(child.unique_blocks for child in children) + len(parent_states) - duplicates,
        contacts, packing)


def place_partition(graph: Graph, plan: dict, *, surface_y: int = -61,
                    maximum_y: int = 319, balance_clocks: bool = True) -> PlacedCluster:
    """Generate a complete bounded hierarchy or fail without dropping hardware."""
    if not plan.get("summary", {}).get("all_signal_records_retained"):
        raise PlacementError("physical placement requires a complete per-signal plan")
    audit_plan(graph, plan)
    if not -64 <= surface_y < maximum_y <= 319:
        raise PlacementError("invalid above-grass world height limits")
    context = _Context(plan, surface_y, maximum_y)

    def visit(cluster_id, limit):
        cluster = context.clusters[cluster_id]
        if not cluster["children"]:
            try:
                leaf = _leaf(context, cluster_id)
            except ValueError as error:
                raise PlacementError(f"native leaf {cluster_id}: {error}") from error
            if leaf.bounds.max.y > limit:
                raise PlacementError("native leaf and reserved ancestor routes exceed build height")
            return leaf
        child_limit = limit - context.route_height(cluster_id)
        children = tuple(visit(child, child_limit) for child in cluster["children"])
        return _parent(context, cluster_id, children, limit)

    placed = visit(plan["root_cluster"], maximum_y)
    audit_placement(graph, plan, placed)
    if balance_clocks:
        balance_partition_clocks(plan, placed)
    return placed


def balance_partition_clocks(plan: dict, placed: PlacedCluster) -> dict:
    """Match every planned clock leaf through the directed physical hierarchy.

    Matching consumes real private-branch repeater positions. Insufficient delay
    capacity is an error, and matching remains planning data until real runtime
    verifies every transfer and the too-fast negative control.
    """
    modules = placed.all_modules()
    saved = {key: (dict(tile.overrides), deepcopy(tile.clock_delays), deepcopy(tile._balanced_positions))
             for key, tile in modules.items()}
    try:
        return _balance_partition_clocks(plan, placed)
    except (ValueError, KeyError, StopIteration):
        for key, tile in modules.items():
            tile.overrides, tile.clock_delays, tile._balanced_positions = saved[key]
        raise


def _balance_partition_clocks(plan: dict, placed: PlacedCluster) -> dict:
    modules = placed.all_modules()
    entities = {entity["id"]: entity for entity in plan["entities"]}
    reports = {}
    for signal in plan["signals"]:
        clock_sinks = [sink for sink in signal["sinks"] if sink["pin"] == "C"
            and entities[sink["entity"]]["kind"] == "cell"
            and template_for(entities[sink["entity"]]["cell_type"]).cell_type == "DFF"]
        if not clock_sinks:
            continue
        bit = signal["bit"]
        links = {link["source"]["terminal_id"]: link for link in signal["boundary_links"]}
        key = lambda end: ("leaf", end["cluster"]) if end["domain"] is None else ("domain", end["domain"])
        completed, pending = {}, set()

        def tail(module_key):
            if module_key in completed:
                return completed[module_key]
            if module_key in pending:
                raise PlacementError("clock boundary routes form a cycle")
            pending.add(module_key)
            tile = modules[module_key]
            net = next(net for net in tile.physical.nets if net.bit == bit)
            tails = {}
            for sink in net.sinks:
                if sink.pin in links:
                    delay = tail(key(links[sink.pin]["sink"]))
                    if delay is not None:
                        tails[sink.pos] = delay
                elif sink.name in tile.physical.cell_types and sink.pin == "C" and template_for(tile.physical.cell_types[sink.name]).cell_type == "DFF":
                    tails[sink.pos] = 0
            if tails:
                tile.balance_net(bit, tails)
                completed[module_key] = tile.clock_delays[bit]["balanced_repeater_delay"]
            else:
                completed[module_key] = None
            pending.remove(module_key)
            return completed[module_key]

        source_leaf = plan["entity_leaf"][signal["source"]["entity"]]
        total = tail(("leaf", source_leaf))
        reports[bit] = {"balanced_repeater_delay": total, "clock_leaves": len(clock_sinks),
                        "visited_modules": len(completed), "runtime_verified": False}
    return reports


def audit_placement(graph: Graph, plan: dict, placed: PlacedCluster) -> dict:
    """Independently reconstruct every native endpoint and shared graph contact."""
    audit_plan(graph, plan)
    modules = placed.all_modules()
    clusters = {cluster["id"]: cluster for cluster in plan["clusters"]}
    domains = defaultdict(set)
    for domain in plan["routing_domains"]:
        domains[domain["cluster_id"]].add(("domain", domain["id"]))
    visited = set()

    def hierarchy(cluster):
        if cluster.cluster_id in visited or cluster.cluster_id not in clusters:
            raise PlacementError("physical hierarchy duplicated or invented a cluster")
        visited.add(cluster.cluster_id)
        expected = clusters[cluster.cluster_id]
        if sorted(child.cluster_id for child in cluster.children) != sorted(expected["children"]):
            raise PlacementError("physical hierarchy changed a complete child group")
        keys = domains[cluster.cluster_id] if expected["children"] else {("leaf", cluster.cluster_id)}
        if set(cluster.modules) != keys:
            raise PlacementError("physical hierarchy omitted or invented a leaf/domain module")
        for child in cluster.children:
            hierarchy(child)

    if placed.cluster_id != plan["root_cluster"]:
        raise PlacementError("physical hierarchy changed its planned root")
    hierarchy(placed)
    if visited != set(clusters):
        raise PlacementError("physical hierarchy omitted a planned cluster")
    expected_cells = {entity["name"]: entity["cell_type"] for entity in graph.entities if entity["kind"] == "cell"}
    actual_cells = {}
    for tile in modules.values():
        if set(tile.physical.cell_types) != set(tile.physical.cell_origins):
            raise PlacementError("native cell types and origins have different inventories")
        if len(set(tile.physical.cell_origins.values())) != len(tile.physical.cell_origins):
            raise PlacementError("distinct native cells occupy one physical origin")
        for name, kind in tile.physical.cell_types.items():
            if name in actual_cells:
                raise PlacementError("a native cell occurs in multiple physical modules")
            actual_cells[name] = kind
    if actual_cells != expected_cells:
        raise PlacementError("physical hierarchy changed the complete native cell inventory")
    expected_ports = {entity["id"] for entity in graph.entities if entity["kind"] == "port"}
    if set(placed.ports) != expected_ports:
        raise PlacementError("physical hierarchy changed an original top-level port bit")
    entities = {entity["id"]: entity for entity in graph.entities}
    expected_nets = defaultdict(dict)
    expected_ports_by_module = defaultdict(lambda: ({}, {}))
    for entity in graph.entities:
        if entity["kind"] == "port":
            key = ("leaf", plan["entity_leaf"][entity["id"]])
            direction = 0 if entity["direction"] == "input" else 1
            expected_ports_by_module[key][direction][f"entity{entity['id']}"] = (placed.ports[entity["id"]],)
    all_links = [link for signal in plan["signals"] for link in signal["boundary_links"]]
    if set(placed.terminals) != {end["terminal_id"] for link in all_links for end in (link["source"], link["sink"])}:
        raise PlacementError("physical hierarchy changed the exact boundary-terminal inventory")
    for link in all_links:
        for label, other_label in (("source", "sink"), ("sink", "source")):
            end, other = link[label], link[other_label]
            leaf = end["domain"] is None
            key = ("leaf", end["cluster"]) if leaf else ("domain", end["domain"])
            if leaf or clusters[end["cluster"]]["parent"] == other["cluster"]:
                direction = 0 if label == "sink" else 1
                expected_ports_by_module[key][direction][end["terminal_id"]] = (placed.terminals[end["terminal_id"]],)
    for key, tile in modules.items():
        incoming, outgoing = expected_ports_by_module[key]
        if tile.physical.input_ports != incoming or tile.physical.output_ports != outgoing:
            raise PlacementError("physical module changed its exact original/boundary port inventory")

    def native(endpoint, source):
        entity = entities[endpoint.entity]
        leaf = plan["entity_leaf"][endpoint.entity]
        tile = modules[("leaf", leaf)]
        if entity["kind"] == "port":
            name = f"entity{endpoint.entity}"
            ports = tile.physical.input_ports if entity["direction"] == "input" else tile.physical.output_ports
            if ports.get(name) != (placed.ports[endpoint.entity],):
                raise PlacementError("original port changed physical direction or location")
            return Endpoint(f"port:{name}[0]", name, placed.ports[endpoint.entity], source)
        offset = template_for(entity["cell_type"]).pins[endpoint.pin]
        return Endpoint(entity["name"], endpoint.pin,
                        _translate(tile.physical.cell_origins[entity["name"]], offset), source)

    def boundary(end, source, kind):
        name = end["terminal_id"]
        point = placed.terminals[name]
        return Endpoint(f"port:{name}[0]" if kind == "leaf" else f"boundary:{name}", name, point, source)

    records = {signal["bit"]: signal for signal in plan["signals"]}
    for bit, signal in graph.signals.items():
        record = records[bit]
        source_leaf = plan["entity_leaf"][signal.source.entity]
        local = defaultdict(list)
        for sink in signal.sinks:
            local[plan["entity_leaf"][sink.entity]].append(native(sink, False))
        local.setdefault(source_leaf, [])
        for leaf, sinks in local.items():
            incoming = [link for link in record["boundary_links"] if link["sink"]["cluster"] == leaf]
            outgoing = [link for link in record["boundary_links"] if link["source"]["cluster"] == leaf]
            source = native(signal.source, True) if leaf == source_leaf else boundary(incoming[0]["sink"], True, "leaf")
            sinks += [boundary(link["source"], False, "leaf") for link in outgoing]
            if sinks:
                expected_nets[("leaf", leaf)][bit] = (source, tuple(sinks))
        by_domain = defaultdict(lambda: [[], []])
        for link in record["boundary_links"]:
            if link["sink"]["domain"] is not None:
                by_domain[link["sink"]["domain"]][0].append(boundary(link["sink"], True, "domain"))
            if link["source"]["domain"] is not None:
                by_domain[link["source"]["domain"]][1].append(boundary(link["source"], False, "domain"))
        for domain, (sources, sinks) in by_domain.items():
            if len(sources) != 1 or not sinks:
                raise PlacementError("physical domain has no exact directed driver/sink inventory")
            expected_nets[("domain", domain)][bit] = (sources[0], tuple(sinks))
    endpoint_key = lambda endpoint: (endpoint.name, endpoint.pin, endpoint.pos, endpoint.is_source)
    for key, tile in modules.items():
        actual = {net.bit: (net.source, tuple(net.sinks)) for net in tile.physical.nets}
        if len(actual) != len(tile.physical.nets):
            raise PlacementError("physical module repeats a local signal identifier")
        wanted = expected_nets[key]
        if set(actual) != set(wanted):
            raise PlacementError("physical module dropped or invented a complete signal")
        for bit, (source, sinks) in wanted.items():
            actual_source, actual_sinks = actual[bit]
            if actual_source != source or sorted(map(endpoint_key, actual_sinks)) != sorted(map(endpoint_key, sinks)):
                raise PlacementError("physical module changed a native or boundary pin connection")
    expected_constants = defaultdict(list)
    expected_north = defaultdict(list)
    for value, endpoint in graph.constants:
        if value == "1":
            point = native(endpoint, False).pos
            key = ("leaf", plan["entity_leaf"][endpoint.entity])
            expected_constants[key].append(point)
            if entities[endpoint.entity]["kind"] == "port":
                expected_north[key].append(point)
    for key, tile in modules.items():
        if sorted(tile.physical.constant_high) != sorted(expected_constants[key]) or sorted(tile.physical.north_constant_high) != sorted(expected_north[key]):
            raise PlacementError("physical hierarchy changed a constant tie or its access face")
    if set(placed.shared_contacts) != {link["id"] for link in all_links}:
        raise PlacementError("physical hierarchy is missing a parent-child handoff")
    for signal in plan["signals"]:
        for link in signal["boundary_links"]:
            source = placed.terminals[link["source"]["terminal_id"]]
            sink = placed.terminals[link["sink"]["terminal_id"]]
            contact = placed.shared_contacts[link["id"]]
            if source != sink or contact["position"] != source or contact["bit"] != signal["bit"]:
                raise PlacementError("a boundary handoff moved or changed signal identity")
            if (contact["source_terminal"], contact["sink_terminal"]) != (link["source"]["terminal_id"], link["sink"]["terminal_id"]):
                raise PlacementError("shared-contact metadata changed its directed terminal identities")
            source_cluster, sink_cluster = link["source"]["cluster"], link["sink"]["cluster"]
            child, parent = (source_cluster, sink_cluster) if clusters[source_cluster]["parent"] == sink_cluster else (sink_cluster, source_cluster)
            if (contact["child_cluster"], contact["parent_cluster"]) != (child, parent):
                raise PlacementError("shared contact changed its owning child/parent cluster")
            if (contact["child_access"], contact["parent_access"]) != ("north", "south"):
                raise PlacementError("a shared boundary lacks distinct internal and enclosing faces")
            for end in (link["source"], link["sink"]):
                module_key = ("leaf", end["cluster"]) if end["domain"] is None else ("domain", end["domain"])
                escape = modules[module_key].escapes[source]
                expected_north = end["cluster"] == contact["child_cluster"]
                if escape.escape_x is not None or escape.wall_lead_z is not None or (escape.escape_z < source.z) != expected_north:
                    raise PlacementError("actual boundary route uses the wrong shared-contact face")
    occupied = {}
    for record in placed.shared_contacts.values():
        point, bit = record["position"], record["bit"]
        for nearby in _nearby_wire_points(point):
            if nearby in occupied and occupied[nearby] != bit:
                raise PlacementError("unrelated shared contacts have unintended electrical adjacency")
        occupied[point] = bit
    return {"native_cells": len(actual_cells), "original_port_bits": len(expected_ports),
            "shared_contacts": len(all_links), "native_sink_uses": len([sink for signal in graph.signals.values() for sink in signal.sinks]),
            "clock_leaf_uses": plan["summary"]["clock_leaf_uses"],
            "constant_terminals": len(graph.constants), "status": "structural-contacts-passed",
            "runtime_verified": False}
