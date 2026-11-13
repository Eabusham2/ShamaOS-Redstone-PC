"""Check actual blocks for a proposed straight South original-I/O facade.

This is a clearance diagnostic, not a route or runtime acceptance. It retains
the original leaf ports and tests a separate possible exterior port row. No
literal block dictionary or broad ancestor-envelope exemption is used.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Iterable

from shamaos.hardware.memory import SUPPORT
from shamaos.model import Placement, Vec3


@dataclass(frozen=True)
class Corridor:
    entity: int
    direction: str
    start: Vec3
    end: Vec3


def south_corridors(entities: Iterable[dict], ports: dict[int, Vec3], maximum_z: int,
                    *, clearance: int = 16) -> tuple[Corridor, ...]:
    if type(clearance) is not int or clearance < 4:
        raise ValueError("exterior clearance must be an integer of at least four blocks")
    original = {entity["id"]: entity for entity in entities if entity["kind"] == "port"}
    if set(original) != set(ports):
        raise ValueError("original port inventory differs from placed leaf contacts")
    result = []
    for entity, point in sorted(ports.items()):
        direction = original[entity]["direction"]
        if direction not in ("input", "output") or point.z > maximum_z:
            raise ValueError("invalid original port direction or claimed maximum Z")
        result.append(Corridor(entity, direction, point, Vec3(point.x, point.y, maximum_z + clearance)))
    return tuple(result)


def audit_south_corridors(corridors: tuple[Corridor, ...], placements: Iterable[Placement],
                         *, examples_per_port: int = 3) -> dict:
    """Stream all original blocks and reject collision, coupling or climb loss."""
    if type(examples_per_port) is not int or examples_per_port < 1:
        raise ValueError("at least one conflict example must be retained per port")
    by_axis = defaultdict(list)
    by_entity = {}
    errors = defaultdict(list)
    terminal_seen = set()
    for corridor in corridors:
        if corridor.entity in by_entity:
            raise ValueError("an original entity has multiple egress corridors")
        if corridor.start.x != corridor.end.x or corridor.start.y != corridor.end.y or corridor.start.z >= corridor.end.z:
            raise ValueError("South egress requires one positive-Z flat line per original pin")
        by_entity[corridor.entity] = corridor
        by_axis[corridor.start.x, corridor.start.y].append(corridor)

    def failure(corridor, reason, point, other=None):
        retained = errors[corridor.entity]
        item = {"reason": reason, "position": asdict(point)}
        if other is not None:
            item["other_entity"] = other
        if len(retained) < examples_per_port and item not in retained:
            retained.append(item)

    # Different original bits must not become one wire just because their
    # selected leaf columns happen to line up after recursive placement.
    for index, a in enumerate(corridors):
        for b in corridors[index + 1:]:
            dx, dy = abs(a.start.x - b.start.x), abs(a.start.y - b.start.y)
            if dx > 1 or dy > 1:
                continue
            gap = 1 if dx == 0 else 0
            if max(a.start.z, b.start.z) <= min(a.end.z, b.end.z) + gap:
                failure(a, "candidate corridors have electrical adjacency", b.start, b.entity)
                failure(b, "candidate corridors have electrical adjacency", a.start, a.entity)
    emitted = 0
    for block in placements:
        emitted += 1
        point, state = block.pos, block.block
        x, y, z = point.x, point.y, point.z
        for corridor in by_axis.get((x, y), ()):
            if point == corridor.start and state.name == "minecraft:redstone_wire":
                terminal_seen.add(corridor.entity)
            if corridor.start.z < z <= corridor.end.z:
                failure(corridor, "existing block occupies proposed wire", point)
        if state != SUPPORT:
            for corridor in by_axis.get((x, y + 1), ()):
                if corridor.start.z <= z <= corridor.end.z:
                    failure(corridor, "existing block occupies proposed support", point)
            for dx in (-1, 0, 1):
                radius = 1 if dx == 0 else 0
                for dy in (-1, 0, 1):
                    for corridor in by_axis.get((x + dx, y + dy), ()):
                        if corridor.start.z - radius <= z <= corridor.end.z + radius:
                            # The preserved terminal and its immediately
                            # northern internal wire are the intended same-net
                            # handoff. No surrounding volume is exempted.
                            own_handoff = (x == corridor.start.x and y == corridor.start.y
                                           and corridor.start.z - 1 <= z <= corridor.start.z)
                            if not own_handoff:
                                failure(corridor, "existing active block couples to proposed wire", point)
            if state.name == "minecraft:redstone_wire" and "up" in dict(state.properties).values():
                for corridor in by_axis.get((x, y + 2), ()):
                    if corridor.start.z <= z <= corridor.end.z:
                        failure(corridor, "proposed support blocks existing climbing headroom", point.offset(dy=1))
    for corridor in corridors:
        if corridor.entity not in terminal_seen:
            failure(corridor, "original wire terminal absent from emitted circuit", corridor.start)
    return {
        "scope": "Straight South original-I/O clearance diagnostic; no routes emitted and no runtime claim",
        "status": "blocked" if errors else "clearance-passed-runtime-unverified",
        "original_ports": len(corridors), "blocked_ports": len(errors),
        "scanned_emitted_blocks": emitted, "runtime_verified": False,
        "exterior_ports": {str(c.entity): asdict(c.end) for c in corridors},
        "conflicts": {str(entity): records for entity, records in sorted(errors.items())},
    }
