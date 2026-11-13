"""The existing global router must connect actual facade pins without overlap."""
from dataclasses import replace

import pytest

from experiments.partition_placement.port_egress_interfaces import facade_port_vectors, facade_routing_interfaces
from shamaos.hardware.external_router import ExternalNet, iter_external_router
from shamaos.hardware.memory import DUST, SUPPORT
from shamaos.hardware.partition_placement import _literal_states
from shamaos.model import Placement, Vec3
from test_mchprs_runtime import run_case, west_driver
from test_partition_placement import analyze_pipeline_trace, partition_pipeline_case
from test_port_egress_placement import facade_pipeline


def test_facade_contract_retains_all_original_bus_bits_and_full_component_keepout(facade_pipeline):
    graph, _, placed = facade_pipeline
    inputs, outputs = facade_port_vectors(graph, placed)
    assert {name: len(pins) for name, pins in inputs.items()} == {"clk": 1, "d": 1}
    assert {name: len(pins) for name, pins in outputs.items()} == {"one": 1, "q": 2, "zero": 1}
    contract = facade_routing_interfaces(graph, placed)
    assert len(contract.access) == len(contract.endpoint_bounds) == 6
    assert len(contract.obstacles) == 12
    assert contract.obstacle_bounds == (placed.bounds,)
    assert all(face == ("south",) for face in contract.access.values())
    assert all(box == placed.bounds for box in contract.endpoint_bounds.values())


def test_actual_external_wires_join_the_facade_without_entering_its_volume(facade_pipeline):
    graph, plan, placed = facade_pipeline
    contract = facade_routing_interfaces(graph, placed)
    entities = {entity["id"]: entity for entity in graph.entities if entity["kind"] == "port"}
    stations = {entity: point.offset(dz=80) for entity, point in placed.exposed_ports.items()}
    nets = tuple(ExternalNet(f"external:{entity}", stations[entity], (point,))
                 if entities[entity]["direction"] == "input" else ExternalNet(f"external:{entity}", point, (stations[entity],))
                 for entity, point in placed.exposed_ports.items())
    station_blocks = [block for point in stations.values() for block in (
        Placement(point.offset(dy=-1), SUPPORT, "external-fixture"), Placement(point, DUST, "external-fixture"))]
    access = dict(contract.access)
    access.update(dict.fromkeys(stations.values(), ("south",)))
    routes = list(iter_external_router(nets, branch_y=placed.bounds.max.y + 14, trunk_y=placed.bounds.max.y + 18,
        obstacles=(*contract.obstacles, *station_blocks), access=access, minimum_y=contract.minimum_y,
        endpoint_bounds=contract.endpoint_bounds, obstacle_bounds=contract.obstacle_bounds,
        track_base_z=placed.bounds.min.z - 128))
    blocks = [*placed.iter_placements(), *station_blocks, *routes]
    states = _literal_states(iter(blocks))
    allowed = {point for pin in placed.exposed_ports.values() for point in (pin, pin.offset(dy=-1))}
    box = placed.bounds
    assert all(not (box.min.x <= p.pos.x <= box.max.x and box.min.y <= p.pos.y <= box.max.y
                   and box.min.z <= p.pos.z <= box.max.z) or p.pos in allowed for p in routes)
    _, base_inputs, base_outputs, steps, expected, old_delta = partition_pipeline_case(graph, plan, placed, 512)
    native = lambda point: point.offset(-old_delta.x, -old_delta.y, -old_delta.z)
    inputs = {name: native(point) for name, point in base_inputs.items()}
    outputs = {name: native(point) for name, point in base_outputs.items()}
    for entity, point in stations.items():
        record = entities[entity]
        name = record["port_name"] + (str(record["port_index"]) if record["port_name"] == "q" else "")
        (inputs if record["direction"] == "input" else outputs)[name] = west_driver(point) if record["direction"] == "input" else point
    delta = Vec3(32 - min(p.x for p in states), 32 - min(p.y for p in states), 32 - min(p.z for p in states))
    move = lambda point: point.offset(delta.x, delta.y, delta.z)
    result = run_case((replace(p, pos=move(p.pos)) for p in blocks),
                      {name: move(point) for name, point in inputs.items()},
                      {name: move(point) for name, point in outputs.items()}, steps,
                      initial_ticks=1024, capture_trace=True)
    assert not analyze_pipeline_trace(result, expected, 512)["pipeline_failures"]
