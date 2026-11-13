"""Analytical limits for the emitted physical routing topology.

These bounds walk mapped nets and terminals. They do not allocate individual
wire blocks or enumerate the billions of chunks implied by long routes.
"""
from collections import Counter
from functools import lru_cache
from typing import Iterable, Mapping

from .hardware.logic import template_for
from .model import Box, Vec3


def _position(endpoint) -> Vec3:
    return endpoint if isinstance(endpoint, Vec3) else endpoint.pos


def _minimum_escape_high_z(point: Vec3, branch_y: int, modes: tuple[str, ...], box: Box | None,
                           minimum_clearance: int = 6, wall_run: int = 6) -> int:
    """Earliest possible start of the long Z branch in this router.

    Northbound searches retain 128 six-block lanes. Southbound searches may
    extend beyond those for tall terminal groups, which only lengthens the
    branch to global tracks and leaves its earliest possible start unchanged.
    The final small staircase ends six Z blocks north of the chosen escape.
    Component bounds can require a longer initial lead; include that clearance.
    """
    vertical = abs(branch_y - 4 - point.y)
    plateaus = max(0, (vertical + 9) // 10 - 1) * 2

    def clearance(mode: str) -> int:
        if box is None:
            return minimum_clearance
        distance = {"south": box.max.z - point.z + 3,
                    "north": point.z - box.min.z + 3,
                    "west": point.x - box.min.x + 3,
                    "east": box.max.x - point.x + 3}[mode]
        return max(minimum_clearance, ((distance + 5) // 6) * 6)

    possible = []
    for mode in modes:
        if mode == "south":
            possible.append(point.z + clearance(mode))
        elif mode == "north":
            possible.append(point.z - clearance(mode) - 6 * 127)
        elif mode in ("west", "east"):
            possible.append(point.z - vertical - plateaus - 6 * 127)
        elif mode == "wall-south":
            # Wall terminals have successively longer flat leads within a
            # row. The northward riser consumes the normal height/plateau run.
            possible.append(point.z + wall_run - vertical - plateaus)
        elif mode == "wall-north":
            # Unlike a south lead, both the row stagger and the latest lane
            # shorten the guaranteed long branch. Include the complete run.
            possible.append(point.z - wall_run - 6 * 127 - vertical - plateaus)
        else:
            raise ValueError(f"unknown routing access direction: {mode}")
    if not possible:
        raise ValueError("a route endpoint needs an access direction")
    return min(possible) - 6


def router_resource_lower_bound(
    nets: Iterable,
    *,
    branch_y: int = 314,
    trunk_y: int = 318,
    track_spacing: int = 8,
    access: Mapping[Vec3, tuple[str, ...]] | None = None,
    endpoint_bounds: Mapping[Vec3, Box] | None = None,
    track_base_z: int | None = None,
) -> dict[str, object]:
    """Rigorous lower bounds for the unique-global-track router.

    Every distinct endpoint receives a distinct X branch, separated from all
    other branch X coordinates by at least three blocks. Therefore one X/Z
    chunk can hold at most six such columns, or 96 guaranteed straight branch
    wires on the branch plane. A reused endpoint is counted only once.

    These are lower bounds, not a completed route or a disk-size prediction.
    They exclude trunk widths, stairs, short access leads and other hardware.
    """
    nets = tuple(nets)
    if trunk_y <= branch_y or track_spacing < 1:
        raise ValueError("invalid routing planes or track spacing")
    if not nets:
        return {
            "router_model": "unique-global-z-track-and-x-branch", "nets": 0, "unique_endpoints": 0,
            "guaranteed_unique_branch_wires": 0, "minimum_emitted_route_placements": 0,
            "minimum_route_chunks": 0, "minimum_route_region_bytes": 0,
            "maximum_single_net_delay_lower_bound_redstone_ticks": 0, "track_z_bounds": None,
            "exact_chunk_count": None, "route_validity_verified": False,
        }
    minimum_z = min(_position(endpoint).z for net in nets for endpoint in (net.source, *net.sinks))
    if track_base_z is not None and type(track_base_z) is not int:
        raise ValueError("an explicit route track base must be an integer")
    first_track = minimum_z - (len(nets) + 1) * track_spacing - 4096 if track_base_z is None else track_base_z
    transition = trunk_y - branch_y
    forced_levels: dict[tuple[int, str], set[int]] = {}
    wall_rows: dict[tuple[int, int], set[Vec3]] = {}
    for net in nets:
        for endpoint in (net.source, *net.sinks):
            point = _position(endpoint)
            modes = (access or {}).get(point, ())
            if len(modes) == 1 and modes[0] in ("south", "north"):
                forced_levels.setdefault((point.z, modes[0]), set()).add(point.y)
            elif modes in (("wall-south",), ("wall-north",)):
                wall_rows.setdefault((point.z, point.y), set()).add(point)
    level_runs = {(z, mode, y): 6 * (index + 1)
                  for (z, mode), levels in forced_levels.items()
                  for index, y in enumerate(sorted(levels, reverse=True))}
    # Match the emitter's descending-X order for terminals sharing a wall row.
    # Repeated endpoints still receive only one lead, as in _plan_escapes.
    wall_runs = {point: 6 * (index + 1)
                 for points in wall_rows.values()
                 for index, point in enumerate(sorted(points, key=lambda p: p.x, reverse=True))}
    unique_lengths: dict[Vec3, int] = {}
    emitted_long_wires = 0
    maximum_delay = 0
    critical_net = None
    endpoints = 0
    for index, net in enumerate(nets):
        track = first_track + index * track_spacing
        lengths = []
        for endpoint in (net.source, *net.sinks):
            point = _position(endpoint)
            modes = (access or {}).get(point, ("south", "north", "west"))
            high_z = _minimum_escape_high_z(point, branch_y, modes,
                                           (endpoint_bounds or {}).get(point),
                                           level_runs.get((point.z, modes[0], point.y), 6),
                                           wall_runs.get(point, 6))
            # Both ends of the long straight segment belong to its joined
            # stair sections and are excluded by _wire_z in the emitter.
            guaranteed = max(0, high_z - (track + transition) - 1)
            unique_lengths[point] = max(unique_lengths.get(point, 0), guaranteed)
            emitted_long_wires += guaranteed
            endpoints += 1
            lengths.append(guaranteed)
        # _wire_z installs a one-redstone-tick repeater at least every ten
        # blocks. Both directions (fanout or wired OR) traverse these legs.
        delay = lengths[0] // 10 + max((length // 10 for length in lengths[1:]), default=0)
        if delay > maximum_delay:
            maximum_delay = delay
            critical_net = str(getattr(net, "name", getattr(net, "bit", index)))
    guaranteed_wires = sum(unique_lengths.values())
    chunks = (guaranteed_wires + 95) // 96
    minimum_regions = (chunks + 1023) // 1024
    return {
        "router_model": "unique-global-z-track-and-x-branch",
        "nets": len(nets), "endpoint_uses": endpoints, "unique_endpoints": len(unique_lengths),
        "branch_y": branch_y, "trunk_y": trunk_y, "track_spacing": track_spacing,
        "track_z_bounds": [first_track, first_track + (len(nets) - 1) * track_spacing],
        "guaranteed_unique_branch_wires": guaranteed_wires,
        "minimum_emitted_route_placements": 2 * emitted_long_wires,
        "minimum_route_chunks": chunks, "minimum_route_region_bytes": chunks * 4096 + minimum_regions * 8192,
        "maximum_single_net_delay_lower_bound_redstone_ticks": maximum_delay,
        "delay_lower_bound_net": critical_net,
        "excluded": ["horizontal trunks", "stair and terminal leads", "logic cells", "other component routes", "compression growth beyond one sector per chunk"],
        "chunk_bound_method": "guaranteed distinct straight branch wires / at most96 per X/Z chunk",
        "exact_chunk_count": None, "route_validity_verified": False,
        "timing_note": "A lower bound on the slowest individual net; complete synchronous timing and clock skew remain unverified.",
    }


@lru_cache(maxsize=64)
def _cell_counts(cell_type: str) -> tuple[int, int]:
    blocks = list(template_for(cell_type).emitter(Vec3(0, 64, 0), "resource-profile"))
    return len(blocks), len({block.pos for block in blocks})


def physical_netlist_resource_lower_bound(physical) -> dict[str, object]:
    cell_types = Counter(physical.cell_types.values())
    emitted_cells = sum(_cell_counts(kind)[0] * count for kind, count in cell_types.items())
    unique_cell_blocks = sum(_cell_counts(kind)[1] * count for kind, count in cell_types.items())
    access = {endpoint.pos: (("south",) if endpoint.name.startswith("macro:") else ("north",))
              for net in physical.nets for endpoint in (net.source, *net.sinks)
              if endpoint.name.startswith(("port:", "macro:"))}
    routing = router_resource_lower_bound(physical.nets, branch_y=physical.routing_branch_y,
                                         trunk_y=physical.routing_trunk_y, track_spacing=physical.track_pitch,
                                         access=access)
    return {
        "top": physical.top, "cells": len(physical.cell_origins), "cell_types": dict(cell_types),
        "literal_cell_placements": emitted_cells, "literal_unique_cell_blocks": unique_cell_blocks,
        "terminal_support_placements": 2 * sum(len(pins) for ports in (physical.input_ports, physical.output_ports) for pins in ports.values()),
        "constant_blocks": len(physical.constant_high), "routing": routing,
    }


def prepared_system_resource_preflight(prepared, storage_report: dict[str, object]) -> dict[str, object]:
    """Include every mapped partition, shell and global net before emission."""
    from .bounded_system import BoundedSystemLogic, MeasuredBoundedLogic
    if isinstance(prepared.logic, (BoundedSystemLogic, MeasuredBoundedLogic)):
        return _bounded_system_resource_preflight(prepared, storage_report)
    parts = {name: physical_netlist_resource_lower_bound(physical) for name, physical in prepared.logic.partitions}
    parts["shell"] = physical_netlist_resource_lower_bound(prepared.logic.shell)
    global_routes = router_resource_lower_bound(prepared.nets, access=prepared.routing.access,
                                                endpoint_bounds=prepared.routing.endpoint_bounds)
    route_reports = [part["routing"] for part in parts.values()] + [global_routes]
    # Different stages may share X/Z chunks or may turn out to conflict. The
    # maximum individual lower bound is safe; summing them would overclaim a
    # proven union until the entire geometry has been placed without conflict.
    regular = storage_report.get("regular_fabrics", {})
    regular_chunks = int(regular.get("regular_fabric_union_chunks", storage_report["storage_chunks"]))
    regular_bytes = int(regular.get("minimum_regular_fabric_region_bytes", storage_report["minimum_storage_region_bytes"]))
    whole_chunk_lower = max(regular_chunks, *(int(report["minimum_route_chunks"]) for report in route_reports))
    whole_region_byte_lower = max(regular_bytes,
                                  *(int(report["minimum_route_region_bytes"]) for report in route_reports))
    available = int(storage_report["available_disk_bytes"])
    return {
        "kind": "mapped-whole-machine-resource-lower-bound", "mapped_partitions": parts, "global_routing": global_routes,
        "storage_chunks": storage_report["storage_chunks"], "whole_machine_chunk_lower_bound": whole_chunk_lower,
        "regular_fabrics": regular, "regular_fabric_union_chunks": regular_chunks,
        "whole_machine_exact_chunks": None, "minimum_whole_machine_region_bytes": whole_region_byte_lower,
        "available_disk_bytes": available, "disk_lower_bound_fits": whole_region_byte_lower < available,
        "minimum_emitted_route_placements": sum(int(report["minimum_emitted_route_placements"]) for report in route_reports),
        "literal_logic_cell_placements": sum(int(part["literal_cell_placements"]) for part in parts.values()),
        "maximum_single_net_delay_lower_bound_redstone_ticks": max(int(report["maximum_single_net_delay_lower_bound_redstone_ticks"]) for report in route_reports),
        "generation_gate": "blocked-by-proven-disk-lower-bound" if whole_region_byte_lower >= available else "lower-bound-only-full-resource-and-route-validation-still-required",
        "scope_note": "All mapped logic and global routes are included analytically; exact screen, controls, local memory decoders and shared-return footprints come from the regular-fabric profile. The exact whole-machine union still requires valid complete routing.",
        "complete_world_runtime_verified": False,
    }


def _bounded_system_resource_preflight(prepared, storage_report):
    """Keep real global/regular lower bounds; count bounded geometry exactly.

    Before measurement this returns the conservative rejection information
    needed by finalize_prepared_system. It never invents one flat routing
    plane for the bounded hierarchy or treats its enclosing rectangle as
    occupied chunks. Once measured, the actual child+shell union participates
    in the same disk gate. The whole machine still includes regular components
    and global wires and therefore has no exact union count here.
    """
    from .bounded_system import MeasuredBoundedLogic

    logic = prepared.logic
    measured = isinstance(logic, MeasuredBoundedLogic)
    geometry = logic.geometry if measured else None
    shell = logic.shell
    physical = shell.physical
    shell_routes = router_resource_lower_bound(physical.nets,
        branch_y=physical.routing_branch_y, trunk_y=physical.routing_trunk_y,
        track_spacing=physical.track_pitch, access=shell.routing.access,
        endpoint_bounds=shell.routing.endpoint_bounds, track_base_z=shell.track_base_z)
    global_routes = router_resource_lower_bound(prepared.nets, access=prepared.routing.access,
                                                endpoint_bounds=prepared.routing.endpoint_bounds)

    def cells(implementation):
        types = Counter(implementation.cell_types.values())
        return {"cells": len(implementation.cell_origins), "cell_types": dict(types),
                "literal_cell_placements": sum(_cell_counts(kind)[0] * count for kind, count in types.items()),
                "literal_unique_cell_blocks": sum(_cell_counts(kind)[1] * count for kind, count in types.items())}

    parts = {name: {**part.manifest(), **cells(part),
                   "routing_scope": "complete bounded domains; no single-plane substitute"}
             for name, part in logic.partitions}
    parts["shell"] = {**shell.manifest(), **cells(physical), "routing": shell_routes}
    regular = storage_report.get("regular_fabrics", {})
    regular_chunks = int(regular.get("regular_fabric_union_chunks", storage_report["storage_chunks"]))
    regular_bytes = int(regular.get("minimum_regular_fabric_region_bytes", storage_report["minimum_storage_region_bytes"]))
    logic_chunks = int(geometry["occupied_chunks"]) if measured else 0
    # At most 1,024 occupied chunks fit in one Anvil region. This is still a
    # lower bound on compressed files, never an allocation-size prediction.
    logic_bytes = logic_chunks * 4096 + ((logic_chunks + 1023) // 1024) * 8192
    route_reports = (shell_routes, global_routes)
    minimum_chunks = max(regular_chunks, logic_chunks, *(int(r["minimum_route_chunks"]) for r in route_reports))
    minimum_bytes = max(regular_bytes, logic_bytes, *(int(r["minimum_route_region_bytes"]) for r in route_reports))
    available = int(storage_report["available_disk_bytes"])
    fits = minimum_bytes < available
    gate = ("blocked-by-proven-disk-lower-bound" if not fits else
            "lower-bound-only-full-resource-and-route-validation-still-required" if measured else
            "bounded-logic-exact-measurement-required")
    return {"kind": "bounded-whole-machine-resource-lower-bound", "logic_backend": "bounded",
        "mapped_partitions": parts, "global_routing": global_routes,
        "bounded_logic_geometry": geometry,
        "bounded_logic_measurement_status": "exact-union-measured" if measured else "not-run-conservative-precheck",
        "bounded_logic_occupied_chunks": logic_chunks if measured else None,
        "minimum_bounded_logic_region_bytes": logic_bytes if measured else None,
        "exact_emitted_logic_placements": geometry["emitted_blocks"] if measured else None,
        "storage_chunks": storage_report["storage_chunks"], "regular_fabrics": regular,
        "regular_fabric_union_chunks": regular_chunks,
        "whole_machine_chunk_lower_bound": minimum_chunks, "whole_machine_exact_chunks": None,
        "minimum_whole_machine_region_bytes": minimum_bytes, "available_disk_bytes": available,
        "disk_lower_bound_fits": fits, "generation_gate": gate,
        "minimum_emitted_route_placements": sum(int(r["minimum_emitted_route_placements"]) for r in route_reports),
        "route_placement_bound_scope": "shell and global routes; every bounded child route is additionally counted in the exact complete logic union",
        "literal_logic_cell_placements": sum(int(part["literal_cell_placements"]) for part in parts.values()),
        "maximum_single_net_delay_lower_bound_redstone_ticks": max(int(r["maximum_single_net_delay_lower_bound_redstone_ticks"]) for r in route_reports),
        "timing_scope": "shell/global individual-net lower bounds; internal bounded clock/data timing and complete sequential timing remain unverified",
        "scope_note": "Regular-fabric and global-route bounds are retained. Bounded children and the shell are measured as one exact emitted union after the conservative rejection check. Whole-world overlap, compression, loading and runtime remain separate gates.",
        "complete_world_runtime_verified": False}
