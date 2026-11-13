"""Analytical route bounds are checked against literal small emitters."""
from types import SimpleNamespace

import pytest

from shamaos.hardware.controls import control_route_access
from shamaos.hardware.external_router import ExternalNet, iter_external_router
from shamaos.hardware.interfaces import system_routing_interfaces
from shamaos.layout import MachineGeometry, default_origins
from shamaos.model import Box, Vec3
from shamaos.resource_accounting import router_resource_lower_bound, prepared_system_resource_preflight


def test_route_block_chunk_and_delay_bounds_do_not_exceed_actual_geometry():
    nets = (
        ExternalNet("normal", Vec3(0, 64, 0), (Vec3(120, 32, 200), Vec3(240, 96, 400))),
        ExternalNet("merged", Vec3(8, 64, 0), (Vec3(128, 32, 200), Vec3(248, 96, 400)), wired_or=True),
    )
    report = router_resource_lower_bound(nets)
    blocks = list(iter_external_router(nets))
    branch_wires = {p.pos for p in blocks if p.pos.y == 314 and p.block.name in ("minecraft:redstone_wire", "minecraft:repeater")}
    actual_chunks = {(p.pos.x // 16, p.pos.z // 16) for p in blocks}
    assert 0 < report["guaranteed_unique_branch_wires"] <= len(branch_wires)
    assert 0 < report["minimum_emitted_route_placements"] <= len(blocks)
    assert 0 < report["minimum_route_chunks"] <= len(actual_chunks)
    assert report["maximum_single_net_delay_lower_bound_redstone_ticks"] > 600
    assert report["exact_chunk_count"] is None
    assert report["route_validity_verified"] is False


def test_component_clearance_and_reused_endpoints_are_counted_conservatively():
    common = Vec3(0, 64, 100)
    nets = (ExternalNet("a", common, (Vec3(100, 64, 200),)),
            ExternalNet("b", common, (Vec3(200, 64, 400),)))
    base = router_resource_lower_bound(nets)
    wider = router_resource_lower_bound(nets, access={common: ("north",)},
                                        endpoint_bounds={common: Box(Vec3(-2, 60, -900), Vec3(2, 70, 110))})
    assert base["endpoint_uses"] == 4
    assert base["unique_endpoints"] == 3
    assert base["minimum_emitted_route_placements"] > 2 * base["guaranteed_unique_branch_wires"]
    assert wider["guaranteed_unique_branch_wires"] < base["guaranteed_unique_branch_wires"]


def test_large_track_count_proves_disk_failure_without_emitting_blocks():
    # Pure analytical data: no Placement or chunk is constructed for these
    # long routes. Use enough nets to exceed the stated destination budget.
    nets = tuple(ExternalNet(str(index), Vec3(index * 8, 64, 0), (Vec3(index * 8 + 4, 64, 0),)) for index in range(1000))
    physical = SimpleNamespace(top="test", cell_types={}, cell_origins={}, input_ports={}, output_ports={},
                               constant_high=(), nets=(), routing_branch_y=286, routing_trunk_y=290, track_pitch=4)
    prepared = SimpleNamespace(logic=SimpleNamespace(partitions=(), shell=physical), nets=nets,
                               routing=SimpleNamespace(access={}, endpoint_bounds={}))
    report = prepared_system_resource_preflight(prepared, {
        "storage_chunks": 10, "minimum_storage_region_bytes": 40960, "available_disk_bytes": 1 << 20,
    })
    assert report["whole_machine_chunk_lower_bound"] > 100_000
    assert report["minimum_whole_machine_region_bytes"] > 400_000_000
    assert report["generation_gate"] == "blocked-by-proven-disk-lower-bound"
    assert not report["disk_lower_bound_fits"]
    assert report["whole_machine_exact_chunks"] is None


@pytest.mark.parametrize("source_mode,sink_mode", [
    ("wall-south", "west"), ("wall-north", "east"),
    ("east", "wall-north"), ("wall-north", "wall-north"),
])
def test_bounded_wall_and_staggered_escape_modes_have_valid_bounds(source_mode, sink_mode):
    source, sink = Vec3(0, 64, 0), Vec3(120, 32, 200)
    nets = (ExternalNet("wall", source, (sink,)),)
    access = {source: (source_mode,), sink: (sink_mode,)}
    report = router_resource_lower_bound(nets, access=access)
    blocks = list(iter_external_router(nets, access=access))
    branch_wires = {p.pos for p in blocks if p.pos.y == 314 and p.block.name in ("minecraft:redstone_wire", "minecraft:repeater")}
    assert 0 < report["guaranteed_unique_branch_wires"] <= len(branch_wires)
    assert report["minimum_emitted_route_placements"] <= len(blocks)
    assert report["minimum_route_chunks"] <= len({(p.pos.x // 16, p.pos.z // 16) for p in blocks})


def test_staggered_north_wall_bounds_cover_every_terminal_in_a_row():
    sources = tuple(Vec3(index * 16, 64, 0) for index in range(4))
    nets = tuple(ExternalNet(f"wall-{index}", source, (Vec3(240 + index * 24, 32, 200),))
                 for index, source in enumerate(sources))
    access = dict.fromkeys(sources, ("wall-north",))
    report = router_resource_lower_bound(nets, access=access)
    blocks = list(iter_external_router(nets, access=access))
    branch_wires = {p.pos for p in blocks if p.pos.y == 314
                    and p.block.name in ("minecraft:redstone_wire", "minecraft:repeater")}
    assert 0 < report["guaranteed_unique_branch_wires"] <= len(branch_wires)


def test_prepared_preflight_accepts_actual_console_terminal_access():
    geometry = MachineGeometry(Vec3(0, -56, 0), 8, 8, 8, 8, 4,
                               vram_bytes=8, bank_words=4, bank_word_bits=8, memory_layers=2)
    origins = default_origins(geometry)
    control_access = control_route_access(origins.input)
    assert any("east" in modes for modes in control_access.values())
    routing = system_routing_interfaces(geometry, {}, {},
        clock_origin=origins.input.offset(180), constant_high=origins.input.offset(300, 1))
    nets = tuple(ExternalNet(f"control-{index}", point, (Vec3(20000 + index * 8, 64, 2000),))
                 for index, point in enumerate(control_access))
    physical = SimpleNamespace(top="test", cell_types={}, cell_origins={}, input_ports={},
                               output_ports={}, constant_high=(), nets=(), routing_branch_y=286,
                               routing_trunk_y=290, track_pitch=4)
    prepared = SimpleNamespace(logic=SimpleNamespace(partitions=(), shell=physical),
                               nets=nets, routing=routing)
    report = prepared_system_resource_preflight(prepared, {
        "storage_chunks": 10, "minimum_storage_region_bytes": 40960, "available_disk_bytes": 0,
    })
    assert report["global_routing"]["nets"] == len(control_access)
    assert report["global_routing"]["minimum_emitted_route_placements"] > 0
    assert report["generation_gate"] == "blocked-by-proven-disk-lower-bound"
