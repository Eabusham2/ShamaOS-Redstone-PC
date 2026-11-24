"""The real startup child retains its measured domain through CI/generation."""
from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace
import json
import shutil

import pytest

from shamaos.bounded_system import STARTUP_NATIVE_GEOMETRY_SHA256, StartupPartition, place_startup_control
from shamaos.hardware.logic import TEMPLATES
from shamaos.model import Vec3
from shamaos.resource_accounting import physical_netlist_resource_lower_bound, prepared_system_resource_preflight
from shamaos.synthesis import synthesize_json
from shamaos.system_build import partition_rtl_files, place_flat_partition


ROOT = Path(__file__).resolve().parents[1]
TOP = "shama_startup_control"


@pytest.fixture(scope="module")
def startup_mapping(tmp_path_factory):
    if not shutil.which("yosys"):
        pytest.skip("Yosys is required to map the real startup child")
    output = tmp_path_factory.mktemp("startup-binding") / "mapped.json"
    synthesize_json(partition_rtl_files(ROOT, TOP), top=TOP, output_json=output,
                    repo_root=ROOT)
    return output


def test_flat_child_factory_retains_all_blocks_ports_and_private_clock(startup_mapping):
    original = place_startup_control(startup_mapping, surface_y=-61, maximum_y=285)
    origin = Vec3(-20_000, -60, 8_000)
    actual = place_flat_partition(startup_mapping, top=TOP, origin=origin,
                                  minimum_build_y=-60)
    assert isinstance(actual, StartupPartition)
    assert actual.native_geometry_fingerprint() == STARTUP_NATIVE_GEOMETRY_SHA256
    assert actual.bounds.min == origin
    offset = Vec3(origin.x-original.bounds.min.x, origin.y-original.bounds.min.y,
                  origin.z-original.bounds.min.z)
    moved = lambda p: p.offset(offset.x, offset.y, offset.z)
    expected = {moved(block.pos): block.block for block in original.iter_placements()}
    emitted = {block.pos: block.block for block in actual.iter_placements()}
    assert emitted == expected
    assert len(actual.cell_types) == len(original.cell_types) > 0
    assert set(actual.input_ports) == {"clk", "power_switch", "reset_button", "shutdown_ready",
                                       "divider_high", "core_gate"}
    assert set(actual.output_ports) == {"clock_hold", "core_clk", "rst"}
    for direction in ("input_ports", "output_ports"):
        assert getattr(actual, direction) == {
            name: tuple(moved(pin) for pin in pins)
            for name, pins in getattr(original, direction).items()
        }
    point, before, after = actual.request_clock_repeater()
    assert before.name == after.name == "minecraft:repeater"
    assert dict(before.properties)["delay"] == "1"
    assert dict(after.properties)["delay"] == "4"
    assert emitted[point] == after
    report = physical_netlist_resource_lower_bound(actual)
    assert report["cells"] == len(actual.cell_types)
    assert 0 < report["literal_unique_cell_blocks"] <= report["literal_cell_placements"]
    assert 0 < report["exact_child_route_chunks"] <= len({(p.x//16, p.z//16) for p in emitted})


def test_flat_shell_resource_gate_includes_the_measured_local_child(startup_mapping):
    part = place_flat_partition(startup_mapping, top=TOP, origin=Vec3(0, -60, 0),
                               minimum_build_y=-60)
    shell = SimpleNamespace(top="shama_soc", cell_types={}, cell_origins={}, input_ports={},
                            output_ports={}, constant_high=(), nets=(), routing_branch_y=286,
                            routing_trunk_y=290, track_pitch=4)
    prepared = SimpleNamespace(logic=SimpleNamespace(partitions=(("u_startup", part),), shell=shell),
                               nets=(), routing=SimpleNamespace(access={}, endpoint_bounds={}))
    result = prepared_system_resource_preflight(prepared, {
        "storage_chunks": 1, "minimum_storage_region_bytes": 4096, "available_disk_bytes": 0,
    })
    assert result["mapped_partitions"]["u_startup"]["cells"] == 29
    assert result["whole_machine_chunk_lower_bound"] > 1
    assert result["literal_logic_cell_placements"] > 0
    assert result["generation_gate"] == "blocked-by-proven-disk-lower-bound"
    assert result["complete_world_runtime_verified"] is False


def test_startup_factory_rejects_translation_above_routing_ceiling(startup_mapping):
    with pytest.raises(ValueError, match="above-grass routing envelope"):
        place_flat_partition(startup_mapping, top=TOP, origin=Vec3(0, 280, 0),
                             minimum_build_y=-60)


def test_name_only_remapping_preserving_geometry_keeps_the_native_contract(startup_mapping, tmp_path):
    mapped = json.loads(startup_mapping.read_bytes())
    module = mapped["modules"][TOP]
    module["cells"] = {"legal_renamed_"+name: value for name, value in reversed(module["cells"].items())}
    renamed = tmp_path / "renamed.json"
    renamed.write_text(json.dumps(mapped))
    part = place_startup_control(renamed, surface_y=-61, maximum_y=285)
    assert part.native_geometry_fingerprint() == STARTUP_NATIVE_GEOMETRY_SHA256


def test_same_logical_graph_cannot_silently_accept_changed_physical_cells(startup_mapping, monkeypatch):
    from shamaos.model import BlockState

    original = TEMPLATES["CLOCK_OR"]
    def changed(origin, component):
        altered = False
        for block in original.emitter(origin, component):
            if not altered and block.block.name == "minecraft:repeater":
                properties = dict(block.block.properties)
                properties["delay"] = str(int(properties["delay"])+1)
                block = replace(block, block=BlockState.of(block.block.name, **properties))
                altered = True
            yield block
        assert altered
    monkeypatch.setitem(TEMPLATES, "CLOCK_OR", replace(original, emitter=changed))
    with pytest.raises(ValueError, match="differs from the measured native geometry"):
        place_startup_control(startup_mapping, surface_y=-61, maximum_y=285)
