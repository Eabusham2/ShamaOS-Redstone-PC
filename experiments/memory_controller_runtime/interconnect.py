"""Wire the complete cache's exposed facade to all installed physical banks.

No controller response is supplied by this module. The selected-word nets run
from native memory through real reverse-flow external routes and facade wires.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import chain

from experiments.memory_controller_runtime.geometry import cache_route_contract, memory_nets
from experiments.memory_controller_runtime.scenarios import HOST_WIDTHS
from experiments.partition_placement.port_egress_interfaces import facade_port_vectors, facade_routing_interfaces
from shamaos.hardware.binary_row_decoder import iter_binary_row_decoder
from shamaos.hardware.external_router import ExternalNet, iter_external_router
from shamaos.hardware.interfaces import RoutingInterfaces
from shamaos.hardware.logic import REDSTONE_BLOCK
from shamaos.hardware.memory_backbone import fabric_tile_ports, iter_stacked_memory_tile
from shamaos.model import Placement, Vec3


@dataclass(frozen=True)
class ConnectedCache:
    bank_origin: Vec3
    nets: tuple[ExternalNet, ...]
    interfaces: RoutingInterfaces
    controller_ports: dict[tuple[str, int], Vec3]
    host_drive_pads: dict[tuple[str, int], Vec3]

    def iter_routes(self):
        yield from iter_external_router(self.nets, component="complete-cache-interconnect",
            branch_y=314, trunk_y=318, obstacles=self.interfaces.obstacles,
            access=self.interfaces.access, minimum_y=-60, maximum_y=319,
            endpoint_bounds=self.interfaces.endpoint_bounds,
            obstacle_bounds=self.interfaces.obstacle_bounds)


def connect_cache(graph, placed, fabric, production_origin: Vec3, *, gap=1024, capacity_bytes=8192) -> ConnectedCache:
    """Retain exact bank shape; translate it beside the complete controller.

    The global production X origin cannot overlap a newly generated complete
    controller. Its Y level, all installed banks, row pitch, decoder and read-return
    construction are unchanged. Physical route validation remains mandatory.
    A returned contract does not certify clocks or any completed runtime.
    """
    if type(gap) is not int or gap < 512:
        raise ValueError("external cache placement requires an explicit gap of at least 512 blocks")
    bank_count = (capacity_bytes + 4095) // 4096
    if (fabric.total_bytes, fabric.bank_count, fabric.bank.words, fabric.bank.word_bits,
            fabric.layers, fabric.layer_pitch, fabric.decoder_x_offset) != (capacity_bytes, bank_count, 1024, 32, bank_count, 12, -170):
        raise ValueError("connected cache must preserve all complete installed banks")
    inputs, outputs = facade_port_vectors(graph, placed)
    ports = {(name, bit): point for buses in (inputs, outputs)
             for name, pins in buses.items() for bit, point in enumerate(pins)}
    if len(ports) != 181 + bank_count or sum(HOST_WIDTHS.values()) != 72:
        raise ValueError("complete controller port inventory changed")
    host_pads = {(name, bit): ports[name, bit].offset(dz=1)
                 for name, width in HOST_WIDTHS.items() for bit in range(width)}
    if len(set(host_pads.values())) != 72 or any(p.z <= placed.bounds.max.z for p in host_pads.values()):
        raise ValueError("host drive pads must be distinct and outside the controller's actual facade")

    relative = fabric.bounds(Vec3(0, production_origin.y, 0))
    origin = Vec3(placed.bounds.max.x + gap - relative.min.x, production_origin.y, placed.bounds.min.z)
    memory_bounds = fabric.bounds(origin)
    if memory_bounds.min.x - placed.bounds.max.x != gap or memory_bounds.min.y != -60:
        raise ValueError("translated cache lost its exact separation or above-grass level")
    controller = facade_routing_interfaces(graph, placed)
    memory = cache_route_contract(origin, fabric, capacity_bytes=capacity_bytes)
    if set(controller.access) & set(memory.access):
        raise ValueError("controller and memory physical terminals overlap")
    # Reserve the real south-side driver blocks during routing. These are
    # obstacles only: initial saved pads remain AIR for the native harness.
    pads = tuple(Placement(point, REDSTONE_BLOCK, "reserved-host-drive-pad") for point in host_pads.values())
    interfaces = RoutingInterfaces(
        controller.access | memory.access,
        (*controller.obstacles, *memory.obstacles, *pads),
        controller.endpoint_bounds | memory.endpoint_bounds,
        (*controller.obstacle_bounds, *memory.obstacle_bounds),
        max(controller.minimum_y, memory.minimum_y),
    )
    nets = memory_nets(ports, fabric_tile_ports(origin, fabric)[0], bank_count=bank_count)
    if len(nets) != 76 + bank_count or sum(net.wired_or for net in nets) != 32:
        raise ValueError("cache external interconnect must retain every net and all 32 physical return paths")
    routed_points = {point for net in nets for point in (net.source, *net.sinks)}
    if len(routed_points) != 2 * (76 + bank_count) or not routed_points <= interfaces.access.keys():
        raise ValueError("external cache routes lack exact endpoint access contracts")
    return ConnectedCache(origin, nets, interfaces, ports, host_pads)


def iter_complete_banks(connection: ConnectedCache, fabric, initial: bytes, *, capacity_bytes=8192):
    """Emit all installed banks, their actual local gates/return and binary decoder."""
    if len(initial) != capacity_bytes or fabric.total_bytes != capacity_bytes or fabric.group_count != 1:
        raise ValueError("the native cache image and complete tile must contain the exact installed capacity")
    tile = fabric.tile_spec(0)
    yield from iter_stacked_memory_tile(connection.bank_origin, tile=tile, initial=initial,
        component="complete-cache-physical-memory")
    yield from iter_binary_row_decoder(connection.bank_origin, tile,
        decoder_x=connection.bank_origin.x + fabric.decoder_x_offset,
        component="complete-cache-binary-row-decoder")


def iter_connected_blocks(placed, connection: ConnectedCache, fabric, initial: bytes, *, capacity_bytes=8192):
    """The literal complete controller, original-I/O wires, banks and routes."""
    yield from chain(placed.iter_placements(), iter_complete_banks(connection, fabric, initial, capacity_bytes=capacity_bytes),
                     connection.iter_routes())
