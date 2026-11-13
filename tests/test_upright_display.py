"""Upright pixels retain logical row order and GPU data at the real lamps."""
import json
import os
from pathlib import Path
import subprocess

import pytest

from shamaos.generator import geometry_from_config, load_config, source_provenance, write_placement_stages
from shamaos.gpu import GPUOp
from shamaos.hardware.display import (
    LampPanelSpec, iter_lamp_panel, iter_panel_terminal_keepouts, panel_bounds,
    panel_pixel, panel_ports, panel_route_access, panel_row_inverted,
)
from shamaos.hardware.external_router import ExternalNet, _plan_escapes, iter_external_router
from shamaos.layout import default_origins
from shamaos.model import Placement, Vec3
from shamaos.physical_resources import panel_resources
from shamaos.worldio import NativeWorldReader
from shamaos.hardware.viewing import operator_obstacles, screen_projection, screen_visibility
from shamaos.hardware.clock import CLOCK_HALF_PERIOD
from test_gpu_execution import Scenario, gpu_binary, run as run_gpu
from test_mchprs_runtime import run_case, west_driver


def _report_directory(fallback):
    directory = Path(os.environ.get("SHAMA_VALIDATION_REPORT_DIR", fallback))
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def test_default_upright_full_resolution_bounds_and_terminal_contract():
    geometry = geometry_from_config(load_config("configs/default.toml"))
    origin = default_origins(geometry).display
    spec = LampPanelSpec(320, 180, 3, 2, "upright")
    assert origin == Vec3(489, -54, 512)
    assert panel_bounds(origin, spec).min == Vec3(489, -60, 512)
    assert panel_bounds(origin, spec).max == Vec3(1808, 304, 529)
    assert panel_pixel(origin, spec, 0, 0) == Vec3(489, 304, 512)
    assert panel_pixel(origin, spec, 179, 319) == Vec3(1446, -54, 512)
    assert spec.physical_width == 958 and spec.physical_height == 359 and spec.depth == 18
    assert [panel_row_inverted(spec, row) for row in (0, 1, 178, 179)] == [False, True, False, True]
    assert len(panel_route_access(origin, spec)) == 500
    assert set(panel_route_access(origin, spec).values()) == {("south",)}
    count, chunks = panel_resources(origin, spec)
    assert count == 2371162 and len(chunks) == 108


def test_operator_view_contains_every_pixel_with_native_console_and_thirty_exports(tmp_path):
    geometry = geometry_from_config(load_config("configs/default.toml"))
    view = screen_visibility(geometry, operator_obstacles(geometry))
    assert view["visible_pixels"] == 57600 and view["blocked_pixels"] == 0
    projection = screen_projection(geometry)
    assert projection["screen_inside_viewport"]
    assert projection["lamp_horizontal_width_px_range"][0] > 1
    report = {"visibility": view, "projection": projection, "complete_machine_runtime_verified": False}
    _report_directory(tmp_path).joinpath("operator-view-geometry.json").write_text(json.dumps(report, indent=2) + "\n")


@pytest.mark.parametrize("height", (3, 4, 180))
def test_upright_keepouts_match_native_states_and_top_to_bottom_rows(height):
    origin = Vec3(489, -54, 512)
    spec = LampPanelSpec(8, height, 3, 2, "upright")
    native = {p.pos: p.block for p in iter_lamp_panel(origin, spec=spec)}
    keepouts = list(iter_panel_terminal_keepouts(origin, spec))
    assert len(keepouts) == 4 * spec.width + 6 * height
    assert all(native[p.pos] == p.block for p in keepouts)
    assert [pin.y for pin in panel_ports(origin, spec).row_select] == sorted(
        (panel_pixel(origin, spec, row, 0).y for row in range(height)), reverse=True)


def test_full_upright_500_terminal_escapes_clear_the_native_envelope():
    origin = Vec3(489, -54, 512)
    spec = LampPanelSpec(320, 180, 3, 2, "upright")
    access = panel_route_access(origin, spec)
    bounds = panel_bounds(origin, spec)
    escapes = _plan_escapes(access, 314, iter_panel_terminal_keepouts(origin, spec),
                            access=access, minimum_y=-60,
                            endpoint_bounds=dict.fromkeys(access, bounds), obstacle_bounds=(bounds,))
    assert len(escapes) == 500
    assert all(escape.escape_z > bounds.max.z for escape in escapes.values())


@pytest.fixture(scope="module")
def gpu_bridge_updates(gpu_binary, tmp_path_factory):
    """Use real GPU commands and the production bridge, including stalls."""
    directory = tmp_path_factory.mktemp("upright-gpu-bridge")
    spec = LampPanelSpec(320, 180, 3, 2, "upright")
    rows = (0, 1, 2, 89, 90, 177, 178, 179)
    scenario = Scenario(320, 180)
    # The reset frame is fully presented and checked by RTL before exercising
    # skipped clean rows. The physical panel also starts with every latch off.
    scenario.command(GPUOp.PUSH_DIRTY).check()
    logical_updates = []
    for values in ([col % 2 == 0 for col in range(320)], [True] * 320, [False] * 320):
        scenario.command(GPUOp.COPY_BUFFER).resource(0, bytes(values), sprite=True)
        for row in rows:
            scenario.command(GPUOp.BLIT, 0, 0, row, 320, 1)
            logical_updates.append((row, values.copy()))
        scenario.command(GPUOp.SWAP_BUFFER).check()
    stdout = run_gpu(gpu_binary, directory, scenario, bridge=True, capture_rows=True)
    commits = [(int(line.split()[1]), int(line.split()[2], 16))
               for line in stdout.splitlines() if line.startswith("BRIDGE_ROW ")]
    assert len(commits) == 180 + 24
    for row, word in commits[:180]:
        assert word == ((1 << 320) - 1 if panel_row_inverted(spec, row) else 0)
    assert [row for row, _ in commits[:180]] == list(range(180))
    for (row, word), (expected_row, values) in zip(commits[180:], logical_updates):
        assert row == expected_row
        decoded = [bool((word >> col) & 1) ^ panel_row_inverted(spec, row) for col in range(320)]
        assert decoded == values
    _report_directory(directory).joinpath("gpu-upright-bridge.log").write_text(stdout)
    return spec, list(zip(commits[180:], logical_updates))


def test_gpu_bridge_serial_encoding_preserves_dirty_rows_and_backpressure(gpu_bridge_updates):
    _, updates = gpu_bridge_updates
    assert len(updates) == 24


def _panel_steps(spec, updates, ticks=1024):
    outputs = {f"P{row}:{col}" for row in range(spec.height) for col in range(spec.width)}
    expected = dict.fromkeys(outputs, False)
    steps = [{"ticks": ticks, "expect": expected.copy()}]
    for (row, word), (_, values) in updates:
        steps.append({"drive": {f"D{col}": bool(word >> col & 1) for col in range(spec.width)},
                      "ticks": ticks, "expect": expected.copy()})
        expected.update({f"P{row}:{col}": value for col, value in enumerate(values)})
        steps += [{"drive": {f"R{row}": True}, "ticks": ticks, "expect": expected.copy()},
                  {"drive": {f"R{row}": False}, "ticks": ticks, "expect": expected.copy()}]
    return steps


def test_actual_complete_upright_panel_replays_real_gpu_bridge_rows(tmp_path, gpu_bridge_updates):
    runner = os.environ.get("SHAMA_MCHPRS_RUNNER")
    if not runner or not Path(runner).is_file():
        pytest.skip("actual MCHPRS runner unavailable; full upright display is unverified")
    spec, updates = gpu_bridge_updates
    origin = Vec3(489, -54, 512)
    translation = Vec3(-457, 64, -480)
    coord = lambda p: [p.x + translation.x, p.y + translation.y, p.z + translation.z]
    pins = panel_ports(origin, spec)
    inputs = {f"D{col}": coord(p) for col, p in enumerate(pins.data_in)}
    inputs.update({f"R{row}": coord(p) for row, p in enumerate(pins.row_select)})
    outputs = {f"P{row}:{col}": coord(panel_pixel(origin, spec, row, col))
               for row in range(spec.height) for col in range(spec.width)}
    steps = _panel_steps(spec, updates)
    # Preserve an expensive case across other pytest runs, which rotate the
    # shared temporary root. A passing report is small; remove the case then.
    path = _report_directory(tmp_path) / "full-upright-panel-case.json"
    states, seen, chunks = {}, {}, set()
    count = lamps = storage = 0
    with path.open("w") as handle:
        handle.write('{"blocks":[')
        for placement in iter_lamp_panel(origin, spec=spec):
            p = placement.pos
            assert -60 <= p.y <= 319
            key = (p.x << 32) | ((p.y + 64) << 16) | p.z
            assert key not in seen, f"duplicate upright block at {p}"
            seen[key] = states.setdefault(placement.block, len(states))
            chunks.add((p.x // 16, p.z // 16))
            properties = dict(placement.block.properties)
            lamps += placement.block.name == "minecraft:redstone_lamp"
            storage += placement.block.name == "minecraft:repeater" and properties.get("locked") == "true"
            if count:
                handle.write(",")
            handle.write(json.dumps({"pos": coord(p), "name": placement.block.name, "properties": properties}, separators=(",", ":")))
            count += 1
        handle.write("]")
        for key, value in {"inputs": inputs, "outputs": outputs, "initial_ticks": 1024, "steps": steps}.items():
            handle.write("," + json.dumps(key) + ":")
            json.dump(value, handle, separators=(",", ":"))
        handle.write("}")
    del seen
    assert lamps == storage == len(outputs) == 57600
    assert (count, chunks) == panel_resources(origin, spec)
    with path.open() as handle:
        result = subprocess.run([runner], stdin=handle, text=True, capture_output=True, timeout=600)
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
    runtime = json.loads(result.stdout)
    runtime.pop("traces", None)
    assert runtime["status"] == "passed"
    report = {"kind": "full-320x180-upright-production-panel-gpu-bridge-runtime",
              "unique_blocks": count, "horizontal_chunks": len(chunks), "lamps": lamps,
              "storage_repeaters": storage, "pixels_checked_after_each_step": len(outputs),
              "gpu_rtl_rows_replayed": len(updates), "gpu_initial_zero_rows_checked_in_rtl": 180,
              "runtime": runtime, "runtime_coordinate_translation": vars(translation),
              "source": source_provenance(), "complete_machine_runtime_verified": False}
    _report_directory(tmp_path).joinpath("full-upright-display-runtime.json").write_text(json.dumps(report, indent=2) + "\n")
    path.unlink()


@pytest.mark.parametrize("height", (3, 4, 180))
def test_saved_upright_panel_preserves_polarity_row_order_and_ground(tmp_path, height):
    origin = Vec3(489, -54, 512)
    spec = LampPanelSpec(8, height, 3, 2, "upright")
    world = tmp_path / "upright-panel"
    blocks = list(iter_lamp_panel(origin, spec=spec))
    _, _, validation = write_placement_stages(world, [("display", blocks)], surface_y=-61)
    assert validation["status"] == "passed" and validation["terrain_preserved"]
    reader = NativeWorldReader(world)
    translation = Vec3(-457, 64, -480)
    shift = lambda p: p.offset(translation.x, translation.y, translation.z)
    reopened = [Placement(shift(p.pos), reader.get_block(p.pos), "saved-upright") for p in blocks]
    assert all(p.block == actual.block for p, actual in zip(blocks, reopened))
    for pos in (panel_bounds(origin, spec).min, panel_bounds(origin, spec).max):
        assert reader.get_block(Vec3(pos.x, -61, pos.z)).name == "minecraft:grass_block"
    pins = panel_ports(shift(origin), spec)
    inputs = {f"D{i}": p for i, p in enumerate(pins.data_in)} | {f"R{i}": p for i, p in enumerate(pins.row_select)}
    outputs = {f"P{r}:{c}": panel_pixel(shift(origin), spec, r, c) for r in range(height) for c in range(8)}
    updates = []
    for row in sorted({0, 1, height - 2, height - 1}):
        values = [bool((col + row) % 2) for col in range(8)]
        word = sum((value ^ panel_row_inverted(spec, row)) << col for col, value in enumerate(values))
        updates.append(((row, word), (row, values)))
    runtime = run_case(reopened, inputs, outputs, _panel_steps(spec, updates), initial_ticks=1024)
    report = {"kind": "saved-upright-panel-runtime", "width": 8, "height": height,
              "world_validation": validation, "runtime": runtime, "complete_machine_runtime_verified": False}
    _report_directory(tmp_path).joinpath(f"saved-upright-{height}-runtime.json").write_text(json.dumps(report, indent=2) + "\n")


def test_actual_upright_routed_inputs_write_top_and_bottom_without_crosstalk():
    origin = Vec3(128, 32, 5000)
    spec = LampPanelSpec(8, 4, 3, 2, "upright")
    pins = panel_ports(origin, spec)
    inputs = {f"D{i}": p for i, p in enumerate(pins.data_in)} | {f"R{i}": p for i, p in enumerate(pins.row_select)}
    sources = {name: Vec3(32 + 4 * i, 32, 5200) for i, name in enumerate(inputs)}
    bounds = panel_bounds(origin, spec)
    cell = list(iter_lamp_panel(origin, spec=spec))
    nets = [ExternalNet(name, sources[name], (pin,)) for name, pin in inputs.items()]
    blocks = cell + list(iter_external_router(nets, branch_y=96, trunk_y=100, obstacles=cell,
                    access=panel_route_access(origin, spec), endpoint_bounds=dict.fromkeys(inputs.values(), bounds),
                    obstacle_bounds=(bounds,)))
    outputs = {f"P{r}:{c}": panel_pixel(origin, spec, r, c) for r in range(4) for c in range(8)}
    updates = []
    for row, value in ((0, 0xA5), (3, 0x5A), (1, 0x81), (2, 0x18), (0, 0), (3, 0xFF)):
        values = [bool(value >> col & 1) for col in range(8)]
        word = value ^ (0xFF if panel_row_inverted(spec, row) else 0)
        updates.append(((row, word), (row, values)))
    run_case(blocks, {name: west_driver(pin) for name, pin in sources.items()}, outputs,
             _panel_steps(spec, updates, ticks=1800), initial_ticks=1024)


@pytest.mark.parametrize("width,height", ((8,180),(320,4)))
def test_native_clock_setup_and_commit_pulse_latch_full_height_and_width(tmp_path, width, height):
    origin = Vec3(489,-54,512)
    spec = LampPanelSpec(width,height,3,2,"upright")
    shift = lambda p: p.offset(-457,64,-480)
    pins = panel_ports(shift(origin),spec)
    inputs = {f"D{i}":p for i,p in enumerate(pins.data_in)} | {f"R{i}":p for i,p in enumerate(pins.row_select)}
    outputs = {f"P{r}:{c}":panel_pixel(shift(origin),spec,r,c) for r in range(height) for c in range(width)}
    expected = dict.fromkeys(outputs,False)
    rows = sorted({0,1,2,height//2-1,height//2,height-3,height-2,height-1})
    steps=[]
    for pattern in range(3):
        for row in rows:
            values = [col%2==0 if pattern==0 else bool(pattern==1) for col in range(width)]
            steps.append({"drive":{f"D{c}":value ^ panel_row_inverted(spec,row) for c,value in enumerate(values)},
                          "ticks":2*CLOCK_HALF_PERIOD,"expect":expected.copy()})
            steps.append({"drive":{f"R{row}":True},"ticks":2*CLOCK_HALF_PERIOD})
            expected.update({f"P{row}:{c}":value for c,value in enumerate(values)})
            # Allow the native panel to settle after the real one-cycle
            # commit pulse. The GPU takes much longer to buffer its next row.
            steps.append({"drive":{f"R{row}":False},"ticks":512,"expect":expected.copy()})
    blocks=(Placement(shift(p.pos),p.block,p.component) for p in iter_lamp_panel(origin,spec=spec))
    runtime=run_case(blocks,inputs,outputs,steps,initial_ticks=1024)
    runtime.pop("traces",None)
    report={"kind":"upright-native-clock-pulse-runtime","width":width,"height":height,
            "data_setup_rt":2*CLOCK_HALF_PERIOD,"commit_pulse_rt":2*CLOCK_HALF_PERIOD,
            "runtime":runtime,"complete_machine_runtime_verified":False,
            "scope":"native panel ports; full-machine route skew remains a separate gate"}
    _report_directory(tmp_path).joinpath(f"upright-clock-{width}x{height}.json").write_text(json.dumps(report,indent=2)+"\n")
