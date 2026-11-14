"""Complete cache interface and native observables; no substituted memory."""
from __future__ import annotations

from experiments.partition_placement.raised_exports.geometry_manifest import write_manifest
from experiments.partition_placement.raised_exports.replay_manifest import read_manifest, restore_manifest
from experiments.memory_controller_runtime.scenarios import HOST_WIDTHS, host, port
from shamaos.hardware.binary_row_decoder import binary_row_obstacle_bounds, iter_binary_row_terminal_keepouts
from shamaos.hardware.external_router import ExternalNet
from shamaos.hardware.interfaces import RoutingInterfaces
from shamaos.hardware.logic import template_for
from shamaos.hardware.memory_backbone import (
    fabric_tile_ports, iter_stacked_tile_terminal_keepouts, stacked_tile_bounds,
)
from shamaos.model import Vec3


def original_ports(graph, placed, *, bank_count=2) -> dict[tuple[str, int], Vec3]:
    result = {(entity["port_name"], entity["port_index"]): placed.ports[entity["id"]]
              for entity in graph.entities if entity["kind"] == "port"}
    if len(result) != 181 + bank_count or len(placed.ports) != 181 + bank_count:
        raise ValueError("complete cache interface must retain every original pin")
    return result


def memory_nets(ports: dict[tuple[str, int], Vec3], fabric_ports, *, bank_count=2) -> tuple[ExternalNet, ...]:
    """Production interconnect convention: read return is a wired-OR hub.

    For wired_or=True, ExternalNet.source is the receiving controller hub;
    sinks are the physically driving memory leaves. The router reverses the
    repeater directions accordingly. No memory response is supplied by Python.
    """
    expected = {"bank_select": bank_count, "row_address": 10, "write_data": 32, "selected_word": 32}
    for name, width in expected.items():
        if len(getattr(fabric_ports, name)) != width:
            raise ValueError(f"full cache fabric has an incorrect {name} width")
    nets = []
    for name in ("bank_select", "row_address", "write_data"):
        for bit, target in enumerate(getattr(fabric_ports, name)):
            nets.append(ExternalNet(f"cache:{name}:{bit}", ports[name, bit], (target,)))
    for name, target in (("read_enable", fabric_ports.read_enable), ("write_commit", fabric_ports.write_enable)):
        nets.append(ExternalNet(f"cache:{name}:0", ports[name, 0], (target,)))
    for bit, source in enumerate(fabric_ports.selected_word):
        nets.append(ExternalNet(f"cache:selected_word:{bit}", ports["selected_word", bit], (source,), wired_or=True))
    if len(nets) != 76 + bank_count or len({net.name for net in nets}) != 76 + bank_count:
        raise ValueError("cache interconnect must preserve every named net")
    return tuple(nets)


def cache_route_contract(origin, fabric, *, capacity_bytes=8192) -> RoutingInterfaces:
    """Exact cache slice of system_routing_interfaces, without other fabrics.

    These conservative volumes and literal terminal keepouts are route
    obstacles only. They do not replace any emitted bank or decoder block.
    Controller obstacles must additionally be supplied for connected routing.
    """
    bank_count = (capacity_bytes + 4095) // 4096
    if (fabric.total_bytes != capacity_bytes or fabric.bank_count != bank_count
            or fabric.group_count != 1 or not fabric.binary_rows):
        raise ValueError("cache contract requires the exact installed banks and the real binary decoder")
    pins = fabric_tile_ports(origin, fabric)[0]
    tile = fabric.tile_spec(0)
    bounds = stacked_tile_bounds(origin, tile)
    decoder_x = origin.x + fabric.decoder_x_offset
    decoder_boxes = binary_row_obstacle_bounds(origin, tile, decoder_x=decoder_x)
    access, endpoint_bounds = {}, {}
    for pin, box in zip(pins.row_address, decoder_boxes[1:], strict=True):
        access[pin], endpoint_bounds[pin] = ("north",), box
    groups = (((*pins.bank_select, pins.write_enable), "north"), ((pins.read_enable,), "west"),
              (pins.write_data, "north"), (pins.selected_word, "south"))
    for terminals, direction in groups:
        for pin in terminals:
            access[pin], endpoint_bounds[pin] = (direction,), bounds
    obstacles = (*iter_stacked_tile_terminal_keepouts(origin, tile),
                 *iter_binary_row_terminal_keepouts(origin, tile, decoder_x=decoder_x))
    if len(access) != 76 + bank_count:
        raise ValueError("cache routing contract lost a physical terminal")
    return RoutingInterfaces(access, obstacles, endpoint_bounds, (bounds, *decoder_boxes), -60)


def interface_maps(graph, placed, origin, fabric, *, capacity_bytes=8192):
    """Drive original host pins; observe every original pin, cell and stored bit."""
    bank_count = (capacity_bytes + 4095) // 4096
    if fabric.total_bytes != capacity_bytes or fabric.bank_count != bank_count:
        raise ValueError("storage observable capacity differs from the physical cache")
    ports = original_ports(graph, placed, bank_count=bank_count)
    inputs = {host(name, bit): ports[name, bit] for name, width in HOST_WIDTHS.items() for bit in range(width)}
    expected_driven = {(name, bit) for name, width in HOST_WIDTHS.items() for bit in range(width)}
    actual_driven = {(entity["port_name"], entity["port_index"]) for entity in graph.entities
                     if entity["kind"] == "port" and entity["direction"] == "input"
                     and entity["port_name"] != "selected_word"}
    if actual_driven != expected_driven or len(inputs) != 72:
        raise ValueError("original host input direction/width inventory changed")
    outputs = {port(name, bit): point for (name, bit), point in ports.items()}
    dffs = set()
    for tile in placed.all_modules().values():
        for name, origin_ in tile.physical.cell_origins.items():
            template = template_for(tile.physical.cell_types[name])
            if template.cell_type != "DFF":
                continue
            if name in dffs:
                raise ValueError("a register occurs in more than one physical module")
            dffs.add(name)
            for pin, offset in template.pins.items():
                outputs[f"register:{name}:{pin}"] = origin_.offset(offset.x, offset.y, offset.z)
    expected_dffs = {entity["name"] for entity in graph.entities if entity["kind"] == "cell"
                     and template_for(entity["cell_type"]).cell_type == "DFF"}
    if dffs != expected_dffs:
        raise ValueError("every original register must remain observable exactly once")
    outputs.update({f"contact:{name}": value["position"] for name, value in placed.shared_contacts.items()})
    for bank in range(bank_count):
        base, spec = fabric.bank_origin(origin, bank), fabric.bank_spec(bank)
        if (spec.words, spec.word_bits) != (min(1024, (capacity_bytes - bank * 4096) // 4), 32):
            raise ValueError("storage observables require every physical bit in all installed banks")
        for row in range(spec.words):
            for bit in range(spec.word_bits):
                outputs[f"storage:{bank}:{row}:{bit}"] = base.offset(
                    bit * spec.bit_pitch + 2, spec.cell_y, 3 + row * spec.word_pitch)
    if sum(name.startswith("storage:") for name in outputs) != capacity_bytes * 8:
        raise ValueError("full storage observable inventory changed")
    pins = fabric_tile_ports(origin, fabric)[0]
    for name in ("bank_select", "row_address", "write_data", "selected_word"):
        outputs.update({f"fabric:{name}:{bit}": point for bit, point in enumerate(getattr(pins, name))})
    outputs["fabric:read_enable:0"] = pins.read_enable
    outputs["fabric:write_commit:0"] = pins.write_enable
    return ({name: [point.x, point.y, point.z] for name, point in inputs.items()},
            {name: [point.x, point.y, point.z] for name, point in outputs.items()})
