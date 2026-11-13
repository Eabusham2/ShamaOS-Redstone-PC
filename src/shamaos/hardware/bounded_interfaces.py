"""Expose only verified outer facade contacts to the existing external router."""
from __future__ import annotations

from .bounded_facade import audit_egress
from shamaos.hardware.interfaces import RoutingInterfaces
from shamaos.hardware.memory import DUST, SUPPORT
from shamaos.model import Placement


def facade_port_vectors(graph, placed):
    audit_egress(graph, placed)
    groups = {"input": {}, "output": {}}
    for entity in graph.entities:
        if entity["kind"] != "port":
            continue
        group = groups[entity["direction"]].setdefault(entity["port_name"], {})
        bit = entity["port_index"]
        if bit in group:
            raise ValueError("facade original bus repeats a bit index")
        group[bit] = placed.exposed_ports[entity["id"]]
    result = []
    for direction in ("input", "output"):
        buses = {}
        for name, pins in groups[direction].items():
            if sorted(pins) != list(range(len(pins))):
                raise ValueError("facade original bus dropped a bit index")
            buses[name] = tuple(pins[bit] for bit in range(len(pins)))
        result.append(buses)
    return tuple(result)


def facade_routing_interfaces(graph, placed):
    """Conservative full-volume keepout; only actual outer contacts are open.

    Every inner facade route approaches its outer contact from North. External
    routes use South, outside the full partition volume. All original contacts,
    including pins not in this interconnect, remain literal obstacles.
    """
    audit_egress(graph, placed)
    pins = tuple(placed.exposed_ports.values())
    if len(set(pins)) != len(pins) or any(point.z != placed.bounds.max.z for point in pins):
        raise ValueError("facade external contacts are aliased or inside the component")
    obstacles = tuple(block for point in pins for block in (
        Placement(point.offset(dy=-1), SUPPORT, "original-io-terminal"),
        Placement(point, DUST, "original-io-terminal")))
    return RoutingInterfaces(dict.fromkeys(pins, ("south",)), obstacles,
                             dict.fromkeys(pins, placed.bounds), (placed.bounds,), placed.bounds.min.y)
