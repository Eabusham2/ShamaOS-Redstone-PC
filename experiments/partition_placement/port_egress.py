"""Reserve actual original-I/O delivery paths while placing every ancestor.

Original mapped cells, leaf ports and graph boundary routes keep their existing
audit identities. Additional, separately audited wire-only facade domains carry
each original pin to a real outer South contact. This experimental builder does
not change production placement or assert runtime acceptance.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from experiments.partition_placement.packed_parent_union import PackedParentUnion
from shamaos.hardware import partition_placement as base
from shamaos.hardware.local_router import LocalTile
from shamaos.hardware.memory import DUST, SUPPORT
from shamaos.hardware.partition_planner import Graph, audit_plan
from shamaos.model import Vec3
from shamaos.synthesis import Endpoint, PhysicalNetlist, RoutedNet


@dataclass
class EgressCluster(base.PlacedCluster):
    facade_modules: dict[tuple[str, int, int], LocalTile] = field(default_factory=dict)
    exposed_ports: dict[int, Vec3] = field(default_factory=dict)
    facade_contacts: dict[tuple[int, int], dict] = field(default_factory=dict)

    def iter_placements(self):
        yield from super().iter_placements()
        for tile in self.facade_modules.values():
            yield from tile.iter_placements()

    def translated(self, delta: Vec3):
        original = super().translated(delta)
        move = lambda point: point.offset(delta.x, delta.y, delta.z)
        return replace(original,
            facade_modules={key: base.translate_tile(tile, delta) for key, tile in self.facade_modules.items()},
            exposed_ports={entity: move(point) for entity, point in self.exposed_ports.items()},
            facade_contacts={key: dict(record, position=move(record["position"]))
                             for key, record in self.facade_contacts.items()})

    def all_facade_modules(self):
        result = {}
        for child in self.children:
            child_modules = child.all_facade_modules()
            if result.keys() & child_modules.keys():
                raise base.PlacementError("a facade module appears in multiple child hierarchies")
            result.update(child_modules)
        if result.keys() & self.facade_modules.keys():
            raise base.PlacementError("a facade parent duplicates a child module")
        result.update(self.facade_modules)
        return result


class _Context(base._Context):
    def __init__(self, plan, surface_y, maximum_y):
        super().__init__(plan, surface_y, maximum_y)
        self.port_ancestors = set()
        for entity in plan["entities"]:
            if entity["kind"] != "port":
                continue
            ancestor = self.clusters[plan["entity_leaf"][entity["id"]]]["parent"]
            while ancestor is not None:
                self.port_ancestors.add(ancestor)
                ancestor = self.clusters[ancestor]["parent"]

    def route_height(self, cluster_id):
        return 18 if self.domains[cluster_id] or cluster_id in self.port_ancestors else 0


def _parent(context, cluster_id, children, maximum_y):
    attempts, seen = [], set()
    for layout in ("compact", "row"):
        packed, packing = base._pack_children(children, minimum_y=context.minimum_y,
            maximum_y=maximum_y - context.route_height(cluster_id), horizontal_layout=layout)
        signature = tuple((child.cluster_id, child.bounds) for child in packed)
        if signature in seen:
            continue
        seen.add(signature)
        try:
            result = _connect_parent(context, cluster_id, packed, maximum_y, packing)
            result.packing["rejected_layouts"] = attempts
            return result
        except base.PlacementError as error:
            attempts.append({"horizontal_layout": layout, "error": str(error)})
    raise base.PlacementError(f"cluster {cluster_id} has no routed packing with original-I/O egress: {attempts}")


def _connect_parent(context, cluster_id, children, maximum_y, packing):
    child_ids = {child.cluster_id for child in children}
    external = [link for link in context.links.values()
                if (link["source"]["cluster"] == cluster_id and link["sink"]["cluster"] not in child_ids)
                or (link["sink"]["cluster"] == cluster_id and link["source"]["cluster"] not in child_ids)]
    port_count = sum(len(child.exposed_ports) for child in children)
    apron = 6 * (len(external) + len(children) + port_count + 1) + 16
    attempts = []
    for _ in range(8):
        placed = _connect_attempt(context, cluster_id, children, maximum_y, packing, apron)
        own = [point for tile in (*placed.modules.values(), *placed.facade_modules.values())
               for ports in (tile.physical.input_ports, tile.physical.output_ports)
               for points in ports.values() for point in points]
        if not own or all(point.z == placed.bounds.max.z for point in own):
            placed.packing.update(export_apron=apron, export_apron_attempts=attempts,
                                  original_io_egress_ports=port_count)
            return placed
        front = min(point.z for point in own)
        if any(point.z != front for point in own):
            raise base.PlacementError("original-I/O and graph exports lost their common South face")
        attempts.append({"apron": apron, "export_z": front, "actual_maximum_z": placed.bounds.max.z})
        apron += placed.bounds.max.z - front + 4
    raise base.PlacementError(f"original-I/O facade remained buried after eight measured attempts: {attempts}")


def _connect_attempt(context, cluster_id, children, maximum_y, packing, apron):
    child_by_id = {child.cluster_id: child for child in children}
    boxes = [child.bounds for child in children]
    roof = max(box.max.y for box in boxes)
    front_z = max(box.max.z for box in boxes) + apron
    links = [link for link in context.links.values()
             if link["source"]["cluster"] == cluster_id or link["sink"]["cluster"] == cluster_id]
    external = []
    for link in links:
        own, other = ("source", "sink") if link["source"]["cluster"] == cluster_id else ("sink", "source")
        if link[other]["cluster"] not in child_by_id:
            external.append((link, own))
    external_points = {link[end]["terminal_id"]: Vec3(8 * index, context.minimum_y + 1, front_z)
                       for index, (link, end) in enumerate(external)}
    child_terminals = {name: point for child in children for name, point in child.terminals.items()}
    access, endpoint_bounds, physicals, keys = {}, {}, [], []
    own_terminals, shared = {}, {}

    for domain in context.domains[cluster_id]:
        inputs, outputs, nets = {}, {}, []

        def endpoint(link, end, source):
            own = link[end]
            other = link["sink" if end == "source" else "source"]
            name = own["terminal_id"]
            if other["cluster"] in child_by_id:
                point = child_terminals[other["terminal_id"]]
                access[point] = ("south",)
                endpoint_bounds[point] = child_by_id[other["cluster"]].bounds
                shared[link["id"]] = {"position": point, "bit": link["bit"],
                    "source_terminal": link["source"]["terminal_id"], "sink_terminal": link["sink"]["terminal_id"],
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
            signal_links = [context.links[link["id"]] for link in signal["boundary_links"]]
            incoming = [link for link in signal_links if link["sink"]["domain"] == domain["id"]]
            outgoing = [link for link in signal_links if link["source"]["domain"] == domain["id"]]
            if len(incoming) != 1 or not outgoing:
                raise base.PlacementError("graph domain lost its exact directed source or sinks")
            nets.append(RoutedNet(signal["bit"], endpoint(incoming[0], "sink", True),
                                  tuple(endpoint(link, "source", False) for link in outgoing), 0))
        if sum(1 + len(net.sinks) for net in nets) != domain["terminal_count"]:
            raise base.PlacementError("graph domain changed its planned terminal count")
        physicals.append(PhysicalNetlist(f"domain-{domain['id']}", Vec3(0, context.minimum_y, front_z),
            {}, {}, inputs, outputs, tuple(nets), (), minimum_build_y=context.minimum_y))
        keys.append(("domain", domain["id"]))

    child_ports = {entity: (child, point) for child in children for entity, point in child.exposed_ports.items()}
    if len(child_ports) != sum(len(child.exposed_ports) for child in children):
        raise base.PlacementError("different children claim the same original I/O entity")
    exposed = {entity: Vec3(8 * (len(external) + index), context.minimum_y + 1, front_z)
               for index, entity in enumerate(sorted(child_ports))}
    port_limit = context.plan["budgets"]["routing_terminals_per_domain"] // 2
    if child_ports and port_limit < 1:
        raise base.PlacementError("facade domains need room for both ends of an original I/O bit")
    facade_shared = {}
    port_ids = sorted(child_ports)
    for group, offset in enumerate(range(0, len(port_ids), max(1, port_limit))):
        inputs, outputs, nets = {}, {}, []
        for entity in port_ids[offset:offset + port_limit]:
            child, contact = child_ports[entity]
            outer = exposed[entity]
            inward = context.entities[entity]["direction"] == "input"
            name = f"original-io:{entity}"
            (inputs if inward else outputs)[name] = (outer,)
            exterior_endpoint = Endpoint(f"facade:{name}", name, outer, inward)
            child_endpoint = Endpoint(f"facade-child:{child.cluster_id}:{name}", name, contact, not inward)
            source, sink = (exterior_endpoint, child_endpoint) if inward else (child_endpoint, exterior_endpoint)
            nets.append(RoutedNet(entity, source, (sink,), 0))
            access[outer], access[contact] = ("north",), ("south",)
            endpoint_bounds[contact] = child.bounds
            facade_shared[cluster_id, entity] = {"position": contact, "entity": entity,
                "child_cluster": child.cluster_id, "parent_cluster": cluster_id,
                "direction": context.entities[entity]["direction"], "child_access": "north", "parent_access": "south"}
        physicals.append(PhysicalNetlist(f"original-io-{cluster_id}-{group}", Vec3(0, context.minimum_y, front_z),
            {}, {}, inputs, outputs, tuple(nets), (), minimum_build_y=context.minimum_y))
        keys.append(("egress", cluster_id, group))

    try:
        tiles = base.plan_bounded_domains(physicals, branch_y=roof + 14, trunk_y=roof + 18,
            obstacles=(block for physical in physicals for block in physical._iter_local_placements("domain-native")),
            access=access, endpoint_bounds=endpoint_bounds, obstacle_bounds=boxes,
            max_domain_terminals=context.plan["budgets"]["routing_terminals_per_domain"])
    except ValueError as error:
        raise base.PlacementError(f"cluster {cluster_id}, shared graph/facade escape prefixes: {error}") from error
    expected_shared = {record["position"]: DUST for record in (*shared.values(), *facade_shared.values())}
    expected_shared.update({point.offset(dy=-1): SUPPORT for point in tuple(expected_shared)})
    modules, facade_modules = {}, {}
    parent_union = PackedParentUnion(boxes, expected_shared)
    for key, tile in zip(keys, tiles, strict=True):
        parent_union.add_domain(tile.iter_placements())
        (facade_modules if key[0] == "egress" else modules)[key] = tile
    union = parent_union.finish(minimum_y=context.minimum_y, maximum_y=maximum_y)
    bounds = union.bounds
    contacts = {name: record for child in children for name, record in child.shared_contacts.items()}
    contacts.update(shared)
    all_facade_contacts = {key: record for child in children for key, record in child.facade_contacts.items()}
    all_facade_contacts.update(facade_shared)
    return EgressCluster(cluster_id, bounds, children, modules, {**child_terminals, **own_terminals},
        {entity: point for child in children for entity, point in child.ports.items()},
        sum(child.unique_blocks for child in children) + union.unique_blocks - union.child_duplicates,
        contacts, packing, facade_modules, exposed, all_facade_contacts)


def place_with_egress(graph: Graph, plan: dict, *, surface_y=-61, maximum_y=319,
                      balance_clocks=True, progress=None) -> EgressCluster:
    audit_plan(graph, plan)
    if not -64 <= surface_y < maximum_y <= 319:
        raise base.PlacementError("invalid above-grass facade height limits")
    context = _Context(plan, surface_y, maximum_y)

    def visit(cluster_id, limit):
        cluster = context.clusters[cluster_id]
        if cluster["children"]:
            children = tuple(visit(child, limit - context.route_height(cluster_id)) for child in cluster["children"])
            result = _parent(context, cluster_id, children, limit)
        else:
            original = base._leaf(context, cluster_id)
            result = EgressCluster(**vars(original), exposed_ports=dict(original.ports))
            if result.bounds.max.y > limit:
                raise base.PlacementError("original I/O egress leaves insufficient reserved leaf height")
        if progress:
            progress(result)
        return result

    placed = visit(plan["root_cluster"], maximum_y)
    base.audit_placement(graph, plan, placed)
    audit_egress(graph, placed)
    if balance_clocks:
        base.balance_partition_clocks(plan, placed)
    return placed


def audit_egress(graph: Graph, placed: EgressCluster) -> dict:
    entities = {entity["id"]: entity for entity in graph.entities if entity["kind"] == "port"}
    expected_contacts, module_count, links = {}, 0, 0

    def visit(cluster):
        nonlocal module_count, links
        if not isinstance(cluster, EgressCluster):
            raise base.PlacementError("original-I/O facade lost a child implementation")
        if not cluster.children:
            if cluster.exposed_ports != cluster.ports or cluster.facade_modules or cluster.facade_contacts:
                raise base.PlacementError("facade leaf relocated or invented an original mapped pin")
            return
        child_ports = {entity: (child, point) for child in cluster.children for entity, point in child.exposed_ports.items()}
        if set(cluster.exposed_ports) != set(child_ports):
            raise base.PlacementError("facade lost or invented an exterior original port")
        expected_keys = {("egress", cluster.cluster_id, group) for group in range(len(cluster.facade_modules))}
        if (any(type(key) is not tuple or len(key) != 3 or key[0] != "egress"
                or any(type(value) is not int for value in key[1:]) for key in cluster.facade_modules)
                or set(cluster.facade_modules) != expected_keys):
            raise base.PlacementError("facade module keys must retain exact ancestor and contiguous group identities")
        actual = {}
        for key, tile in cluster.facade_modules.items():
            module_count += 1
            if key[:2] != ("egress", cluster.cluster_id) or tile.physical.cell_types or tile.physical.cell_origins:
                raise base.PlacementError("a facade module changed ownership or introduced logic cells")
            if tile.physical.constant_high or tile.physical.north_constant_high:
                raise base.PlacementError("a facade module substituted a constant for an original signal")
            if not tile.physical.nets:
                raise base.PlacementError("a facade module has no actual original-port delivery path")
            expected_inputs, expected_outputs = {}, {}
            for net in tile.physical.nets:
                entity = net.bit
                if entity in actual or entity not in child_ports or len(net.sinks) != 1:
                    raise base.PlacementError("facade wires duplicate, drop or invent an original I/O bit")
                child, contact = child_ports[entity]
                outer = cluster.exposed_ports[entity]
                if outer.z != cluster.bounds.max.z:
                    raise base.PlacementError("an exported original I/O contact remains buried")
                inward = entities[entity]["direction"] == "input"
                source, sink = (outer, contact) if inward else (contact, outer)
                name = f"original-io:{entity}"
                exterior_endpoint = Endpoint(f"facade:{name}", name, outer, inward)
                child_endpoint = Endpoint(f"facade-child:{child.cluster_id}:{name}", name, contact, not inward)
                expected_source, expected_sink = (exterior_endpoint, child_endpoint) if inward else (child_endpoint, exterior_endpoint)
                if net.source != expected_source or net.sinks != (expected_sink,):
                    raise base.PlacementError("facade reverses, renames or disconnects an original signal")
                (expected_inputs if inward else expected_outputs)[name] = (outer,)
                for point, north in ((outer, True), (contact, False)):
                    escape = tile.escapes.get(point)
                    if escape is None or escape.escape_x is not None or escape.wall_lead_z is not None or (escape.escape_z < point.z) != north:
                        raise base.PlacementError("facade uses the wrong original-I/O contact face")
                expected_contacts[cluster.cluster_id, entity] = {"position": contact, "entity": entity,
                    "child_cluster": child.cluster_id, "parent_cluster": cluster.cluster_id,
                    "direction": entities[entity]["direction"], "child_access": "north", "parent_access": "south"}
                actual[entity] = tile
                links += 1
            if tile.physical.input_ports != expected_inputs or tile.physical.output_ports != expected_outputs:
                raise base.PlacementError("facade physical port inventory changed")
        if set(actual) != set(child_ports):
            raise base.PlacementError("a facade omitted an entire original-port delivery path")
        for child in cluster.children:
            visit(child)

    visit(placed)
    if set(placed.exposed_ports) != set(entities) or placed.facade_contacts != expected_contacts:
        raise base.PlacementError("facade original-port or handoff inventory is incomplete")
    return {"status": "original-io-facade-structure-passed-runtime-unverified", "original_port_bits": len(entities),
            "facade_modules": module_count, "directed_handoffs": links, "runtime_verified": False,
            "electrical_route_isolation_verified": False,
            "scope": "Directed endpoint/contact conservation only; fresh placement isolation guards or replayed runtime remain separate evidence",
            "base_clock_matching_scope": "Original mapped input pin to registers; facade input delivery adds an uncounted common prefix and requires exterior runtime timing"}
