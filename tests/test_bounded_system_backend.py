"""Opt-in bounded production seam, using a complete small mapped test system."""
from dataclasses import replace
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from shamaos import system_build
from shamaos.bounded_system import BoundedPartition, BoundedSystemLogic, MeasuredBoundedLogic
from shamaos.hardware.external_router import ExternalNet
from shamaos.hardware.interfaces import RoutingInterfaces
from shamaos.hardware.logic import template_for
from shamaos.hardware.memory import DUST, SUPPORT
from shamaos.hardware.partition_placement import _literal_states
from shamaos.mchprs_import import export_world
from shamaos.model import Placement, Vec3
from shamaos.resource_accounting import prepared_system_resource_preflight, router_resource_lower_bound
from shamaos.worldio import NativeWorldReader, NativeWorldWriter
from test_mchprs_sparse_runtime import _run_sparse, _runner
from test_mchprs_runtime import west_driver
from test_partition_placement import analyze_pipeline_trace

ROOT = Path(__file__).resolve().parents[1]
CHILD = ROOT / "experiments/partition_placement/raised_exports/evidence/pipeline-mapped.json"
SHELL = ROOT / "experiments/system_bounded_integration/fixtures/pipeline-shell-mapped.json"


def storage_report(available=1 << 40):
    return {"storage_chunks": 3, "minimum_storage_region_bytes": 20_480,
            "available_disk_bytes": available,
            "regular_fabrics": {"regular_fabric_union_chunks": 7, "minimum_regular_fabric_region_bytes": 36_864}}


@pytest.fixture(scope="module")
def bounded_prepared(tmp_path_factory):
    build = tmp_path_factory.mktemp("production-bounded-small-top")
    calls = []
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(system_build, "PARTITION_SPECS", (("u_pipeline", "pipeline"),))
        def mapped_fixture(files, *, top, output_json, **kwargs):
            calls.append((tuple(files), top, kwargs))
            raw = json.loads((CHILD if top == "pipeline" else SHELL).read_text())
            if top == "shama_soc": raw["modules"][top] = raw["modules"].pop("pipeline_system")
            Path(output_json).write_text(json.dumps(raw))
            return output_json
        patch.setattr(system_build, "synthesize_json", mapped_fixture)
        logic = system_build._prepare_partitioned_logic(root=ROOT, build=build,
            base_origin=Vec3(0, -60, 0), yosys="fixture-mapping-only", minimum_build_y=-60,
            logic_backend="bounded", bounded_options={"cell_budget": 1, "boundary_budget": 8,
                "domain_budget": 5, "shell_branch_y": 183, "shell_trunk_y": 187,
                "shell_track_base_z": -1500})
    assert isinstance(logic, BoundedSystemLogic)
    assert [call[1] for call in calls] == ["pipeline", "shama_soc"]
    assert calls[0][2]["mapping_mode"] == "abc"
    assert calls[1][2]["blackbox_modules"] == ("pipeline",)
    assert all(call[0] == tuple(ROOT / name for name in system_build.SOC_RTL_FILES) for call in calls)
    stations = {name: pins[0].offset(dz=80) for name, pins in logic.input_ports.items()}
    obstacles = tuple(block for pin in stations.values() for block in (
        Placement(pin.offset(dy=-1), SUPPORT, "test-station"), Placement(pin, DUST, "test-station")))
    access = {pin: ("south",) for ports in (logic.input_ports, logic.output_ports) for pins in ports.values() for pin in pins}
    access.update(dict.fromkeys(stations.values(), ("south",)))
    routing = RoutingInterfaces(access, obstacles, {}, (), -60)
    nets = tuple(ExternalNet(name, stations[name], pins) for name, pins in logic.input_ports.items())
    prepared = system_build.PreparedSystem(logic, nets, Vec3(-32, -60, -32), Vec3(-48, -59, -32),
                                          {"logic": logic.manifest(), "small_complete_test_system": True}, routing)
    return prepared, stations, build


def test_opt_in_retains_complete_shell_macro_bus_and_constants(bounded_prepared):
    prepared, _, _ = bounded_prepared
    logic = prepared.logic
    assert logic.manifest()["cell_count"] == 4
    child = dict(logic.partitions)["u_pipeline"]
    assert child.graph.cell_count == 3
    assert len(child.graph.constants) == 3
    nets = {net.bit: net for net in logic.shell.physical.nets}
    assert nets[7].source.pos == child.output_ports["q"][0]
    assert nets[6].source.pos == child.output_ports["q"][1]
    assert nets[7].sinks[0].pos == logic.output_ports["q"][1]
    assert nets[6].sinks[0].pos == logic.output_ports["q"][0]
    assert not hasattr(child, "nets")
    assert list(system_build.iter_logic(prepared)) == list(logic.iter_placements())


@pytest.mark.parametrize("fault", ["matching-not-run", "matching-repeater-removed", "facade-override"])
def test_reused_placed_partition_requires_real_clock_matching_without_mutation(bounded_prepared, fault):
    part = dict(bounded_prepared[0].logic.partitions)["u_pipeline"]
    placed = deepcopy(part.placed)
    modules = placed.all_modules()
    if fault == "matching-not-run":
        for tile in modules.values():
            tile.overrides.clear(); tile.clock_delays.clear(); tile._balanced_positions.clear()
    elif fault == "matching-repeater-removed":
        tile = next(tile for tile in modules.values() if tile.overrides)
        tile.overrides.pop(next(iter(tile.overrides)))
    else:
        tile = next(iter(placed.all_facade_modules().values()))
        tile.overrides[next(iter(tile.escapes))] = DUST
    before = deepcopy(placed)
    with pytest.raises(ValueError, match="clock matching|unsupported route or clock overrides"):
        BoundedPartition.from_placed(Path(part.graph.source["path"]), part.top, part.plan, placed,
                                     surface_y=part.surface_y, maximum_y=part.maximum_y)
    assert placed == before


def test_full_union_is_not_started_after_conservative_disk_rejection(bounded_prepared, monkeypatch, tmp_path):
    from shamaos import bounded_system
    prepared, _, _ = bounded_prepared
    def forbidden(*args, **kwargs): raise AssertionError("expensive exact union ran after a proven resource rejection")
    monkeypatch.setattr(bounded_system, "measure_logic", forbidden)
    unchanged, report = system_build.finalize_prepared_system(prepared, storage_report(1), temporary_parent=tmp_path)
    assert unchanged is prepared
    assert report["generation_gate"] == "blocked-by-proven-disk-lower-bound"
    assert report["bounded_logic_geometry"] is None
    assert report["regular_fabric_union_chunks"] == 7
    assert report["global_routing"] == router_resource_lower_bound(prepared.nets, access=prepared.routing.access, endpoint_bounds=prepared.routing.endpoint_bounds)
    assert (tmp_path / "bounded-resource-preflight-before-union.json").is_file()


def test_unmeasured_bounded_global_emission_is_blocked(bounded_prepared):
    with pytest.raises(ValueError, match="measured whole-component keepout"):
        list(system_build.iter_interconnect(bounded_prepared[0]))


@pytest.fixture(scope="module")
def finalized(bounded_prepared):
    prepared, stations, build = bounded_prepared
    bound, report = system_build.finalize_prepared_system(prepared, storage_report(), temporary_parent=build)
    return bound, report, stations, build


def test_exact_union_and_all_lower_bounds_survive_finalization(finalized, bounded_prepared):
    prepared, report, _, _ = finalized
    before = prepared_system_resource_preflight(bounded_prepared[0], storage_report())
    assert isinstance(prepared.logic, MeasuredBoundedLogic)
    assert report["bounded_logic_measurement_status"] == "exact-union-measured"
    states = _literal_states(system_build.iter_logic(prepared))
    assert report["bounded_logic_geometry"]["literal_unique_blocks"] == len(states)
    assert report["bounded_logic_occupied_chunks"] == len({(p.x // 16, p.z // 16) for p in states})
    assert report["global_routing"] == before["global_routing"]
    assert report["regular_fabrics"] == before["regular_fabrics"]
    assert report["minimum_whole_machine_region_bytes"] >= before["minimum_whole_machine_region_bytes"]
    assert report["whole_machine_exact_chunks"] is None
    assert report["complete_world_runtime_verified"] is False
    assert report["literal_logic_cell_placements"] > 0
    assert prepared.logic.routing.obstacle_bounds[0] in prepared.routing.obstacle_bounds
    for ports in (prepared.logic.input_ports, prepared.logic.output_ports):
        for pins in ports.values():
            for pin in pins: assert prepared.routing.endpoint_bounds[pin] == prepared.logic.routing.obstacle_bounds[0]


def test_exact_union_can_strengthen_disk_rejection(finalized):
    prepared, full, _, _ = finalized
    logic_minimum = full["minimum_bounded_logic_region_bytes"]
    report = prepared_system_resource_preflight(replace(prepared, nets=()), storage_report(logic_minimum))
    assert report["generation_gate"] == "blocked-by-proven-disk-lower-bound"
    assert report["bounded_logic_occupied_chunks"] == full["bounded_logic_occupied_chunks"]


def test_production_global_router_uses_complete_logic_volume(finalized):
    prepared, _, _, _ = finalized
    bounds = prepared.logic.routing.obstacle_bounds[0]
    allowed = {point for ports in (prepared.logic.input_ports, prepared.logic.output_ports)
               for pins in ports.values() for pin in pins for point in (pin, pin.offset(dy=-1))}
    routes = list(system_build.iter_interconnect(prepared))
    _literal_states(iter([*system_build.iter_logic(prepared), *prepared.routing.obstacles, *routes]))
    for block in routes:
        inside = all(getattr(bounds.min, axis) <= getattr(block.pos, axis) <= getattr(bounds.max, axis) for axis in "xyz")
        assert not inside or block.pos in allowed
        assert -60 <= block.pos.y <= 318


def test_default_backend_stays_flat_and_unknown_choice_fails_before_synthesis(tmp_path, monkeypatch):
    import inspect
    assert inspect.signature(system_build._prepare_partitioned_logic).parameters["logic_backend"].default == "flat"
    def forbidden(*args, **kwargs): raise AssertionError("invalid backend started synthesis")
    monkeypatch.setattr(system_build, "synthesize_json", forbidden)
    with pytest.raises(ValueError, match="unknown physical logic backend"):
        system_build._prepare_partitioned_logic(root=ROOT, build=tmp_path, base_origin=Vec3(0, -60, 0),
                                               yosys="unused", logic_backend="invented")


def test_core_backend_imports_without_repository_experiments(tmp_path):
    import os
    environment = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    result = subprocess.run([sys.executable, "-c", "from shamaos.bounded_system import prepare_from_artifacts; from shamaos.hardware.bounded_facade import place_with_egress; print('installed-core-imports')"],
                            cwd=tmp_path, env=environment, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "installed-core-imports"


@pytest.mark.parametrize("configured,requested,expected", [
    (None, None, "flat"), ("bounded", None, "bounded"),
    (None, "bounded", "bounded"), ("bounded", "flat", "flat"),
])
def test_generate_cli_preserves_config_and_explicit_backend_selection(tmp_path, monkeypatch, configured, requested, expected):
    from shamaos import cli
    config = {"generator": {} if configured is None else {"logic_backend": configured}}
    monkeypatch.setattr(cli, "load_config", lambda _: config)
    monkeypatch.setattr(cli, "resolve_world_surface", lambda given, _: given)
    observed = []
    def plan(given):
        observed.append(given["generator"].get("logic_backend", "flat"))
        return object()
    def write(world, given, prepared):
        assert given["generator"].get("logic_backend", "flat") == expected
        return SimpleNamespace(world_validation={"status": "passed"}, written_blocks=0,
                               system_metadata={"logic_backend": expected}, stage_counts={})
    monkeypatch.setattr(cli, "build_plan", plan)
    monkeypatch.setattr(cli, "write_available_placements", write)
    monkeypatch.setattr(cli, "manifest", lambda *_: {})
    destination = tmp_path / "manifest.json"
    args = ["generate", "--world", str(tmp_path / "world"), "--manifest", str(destination)]
    if requested is not None: args += ["--logic-backend", requested]
    assert cli.main(args) == 0
    assert observed == [expected]
    report = json.loads(destination.read_text())
    assert report["system_build"]["logic_backend"] == expected
    assert report["runtime_validation"] == {"status": "not-run"}


@pytest.fixture(scope="module")
def saved_bounded_system(finalized):
    prepared, _, stations, build = finalized
    world = build / "world"
    blocks = [*system_build.iter_logic(prepared), *prepared.routing.obstacles, *system_build.iter_interconnect(prepared)]
    states = _literal_states(iter(blocks))
    with NativeWorldWriter(world, surface_y=-61, spawn=Vec3(-16, -59, -16), spawn_platform=False) as writer:
        for block in blocks: writer.set_block(block)
        assert writer.validate()["status"] == "passed"
    reader = NativeWorldReader(world)
    for point, state in states.items(): assert reader.get_block(point) == state
    stream = build / "world.jsonl"
    native = export_world(world, stream)
    return prepared, stations, stream, native


@pytest.mark.parametrize("half_period, succeeds", [(512, True), (20, False)])
def test_opt_in_complete_top_and_global_inputs_run_after_native_reopen(saved_bounded_system, tmp_path, half_period, succeeds):
    prepared, stations, stream, native = saved_bounded_system
    part = dict(prepared.logic.partitions)["u_pipeline"]
    outputs = {}
    for name, pins in prepared.logic.output_ports.items():
        for index, point in enumerate(pins): outputs[name+str(index) if len(pins)>1 else name] = point
    for name, point in part.cell_origins.items():
        if template_for(part.cell_types[name]).cell_type == "DFF":
            for pin, offset in template_for("DFF").pins.items(): outputs[f"{name}:{pin}"] = point.offset(offset.x, offset.y, offset.z)
    constants = {"one": True, "zero": False, "tied_one": True, "tied_zero": False}
    steps, expected, state = [], [], [False, False]
    for data in (False, True, False, True):
        steps.append({"drive": {"clk": False, "d": data}, "ticks": 4096, "expect": constants, "continuous": True})
        for _ in range(3):
            state = [data, state[0]]; expected.append(tuple(state))
            steps.extend([{"drive": {"clk": True}, "ticks": half_period, "expect": constants, "continuous": True},
                          {"drive": {"clk": False}, "ticks": half_period, "expect": constants, "continuous": True}])
        final = dict(constants)
        if succeeds: final.update(q0=state[1], q1=state[0])
        steps.append({"ticks": 4096, "expect": final})
    encode = lambda p: [p.x, p.y, p.z]
    scenario = {"inputs": {name: encode(west_driver(pin)) for name, pin in stations.items()},
                "outputs": {name: encode(pin) for name, pin in outputs.items()},
                "initial_ticks": 4096, "steps": steps, "capture_trace": True}
    runner = _runner("SHAMA_MCHPRS_TERRAIN_RUNNER")
    result = _run_sparse(runner, stream, scenario, tmp_path, limit=4000)
    report = analyze_pipeline_trace(result, expected, half_period)
    assert bool(report["pipeline_failures"]) != succeeds
    report.update(status="opt-in-bounded-complete-top-with-global-inputs", loaded_chunks=result["loaded_chunks"],
                  native_sections=native["sections"], mapped_cell_count=prepared.logic.manifest()["cell_count"],
                  imported_stream_sha256=result["imported_stream_sha256"], scenario_sha256=result["scenario_sha256"],
                  runner_sha256=hashlib.sha256(Path(runner).read_bytes()).hexdigest(),
                  complete_machine_runtime_verified=False)
    (tmp_path / "bounded-backend-runtime.json").write_text(json.dumps(report, indent=2)+"\n")
