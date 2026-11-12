"""Full-resolution runtime evidence for the five-by-four lamp panel."""
import json
import os
from pathlib import Path
import subprocess

import pytest

from shamaos.generator import source_provenance
from shamaos.hardware.display import LampPanelSpec, iter_lamp_panel, panel_bounds, panel_ports
from shamaos.model import Vec3
from shamaos.physical_resources import panel_resources
from test_mchprs_runtime import west_driver


def test_actual_complete_320_by_180_dense_panel_latches_and_isolates_every_pixel(tmp_path):
    runner = os.environ.get("SHAMA_MCHPRS_RUNNER")
    if not runner or not Path(runner).is_file():
        pytest.skip("actual MCHPRS runner unavailable; full dense display is unverified")
    origin = Vec3(128, 32, 128)
    spec = LampPanelSpec(width=320, height=180, pixel_pitch_x=5, pixel_pitch_y=4)
    pins = panel_ports(origin, spec)
    coord = lambda point: [point.x, point.y, point.z]
    inputs = {f"D{col}": coord(west_driver(point)) for col, point in enumerate(pins.data_in)}
    inputs.update({f"R{row}": coord(west_driver(point)) for row, point in enumerate(pins.row_select)})
    outputs = {f"P{row}:{col}": coord(origin.offset(col * 5 + 3, 2, row * 4))
               for row in range(180) for col in range(320)}
    expected = dict.fromkeys(outputs, False)
    steps = [{"ticks": 1024, "expect": expected.copy()}]
    rows = (0, 1, 89, 90, 178, 179, 0)
    for index, row in enumerate(rows):
        values = [(col + row) % 2 == 0 if index % 3 == 0 else (index % 3 == 1) for col in range(320)]
        if index == len(rows) - 1:
            values = [False] * 320
        steps.append({"drive": {f"D{col}": value for col, value in enumerate(values)},
                      "ticks": 1024, "expect": expected.copy()})
        expected.update({f"P{row}:{col}": value for col, value in enumerate(values)})
        steps.extend([
            {"drive": {f"R{row}": True}, "ticks": 1024, "expect": expected.copy()},
            {"drive": {f"R{row}": False}, "ticks": 1024, "expect": expected.copy()},
        ])
    path = tmp_path / "dense-full-panel.json"
    seen, states, chunks = {}, {}, set()
    count = lamps = storage = 0
    bounds = panel_bounds(origin, spec)
    with path.open("w") as handle:
        handle.write('{"blocks":[')
        for placement in iter_lamp_panel(origin, spec=spec):
            point = placement.pos
            assert bounds.min.x <= point.x <= bounds.max.x and bounds.min.y <= point.y <= bounds.max.y and bounds.min.z <= point.z <= bounds.max.z
            key = point.x << 40 | point.y << 24 | point.z
            state = states.setdefault(placement.block, len(states))
            assert seen.setdefault(key, state) == state, f"conflicting dense panel block at {point}"
            chunks.add((point.x // 16, point.z // 16))
            properties = dict(placement.block.properties)
            lamps += placement.block.name == "minecraft:redstone_lamp"
            storage += placement.block.name == "minecraft:repeater" and properties.get("locked") == "true"
            if count:
                handle.write(",")
            handle.write(json.dumps({"pos": coord(point), "name": placement.block.name, "properties": properties}, separators=(",", ":")))
            count += 1
        handle.write("]")
        for key, value in {"inputs": inputs, "outputs": outputs, "initial_ticks": 1024, "steps": steps}.items():
            handle.write("," + json.dumps(key) + ":")
            json.dump(value, handle, separators=(",", ":"))
        handle.write("}")
    unique = len(seen)
    del seen
    assert lamps == storage == len(outputs) == 57600
    assert (count, chunks) == panel_resources(origin, spec)
    assert count == 2482200 and len(chunks) == 5084
    with path.open() as handle:
        result = subprocess.run([runner], stdin=handle, capture_output=True, text=True, timeout=600)
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
    runtime = json.loads(result.stdout)
    runtime.pop("traces", None)
    assert runtime["status"] == "passed"
    report = {"kind": "full-320x180-dense-panel-runtime", "width": 320, "height": 180,
              "pixel_pitch_x": 5, "pixel_pitch_z": 4, "lamps": lamps, "storage_repeaters": storage,
              "pixels_checked_after_each_step": len(outputs), "selected_rows": list(rows),
              "emitted_placements": count, "unique_blocks": unique, "horizontal_chunks": len(chunks),
              "runtime": runtime, "source": source_provenance(), "complete_machine_runtime_verified": False}
    destination = Path(os.environ.get("SHAMA_VALIDATION_REPORT_DIR", tmp_path))
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "full-dense-display-runtime.json").write_text(json.dumps(report, indent=2) + "\n")


def test_three_block_row_pitch_is_rejected_before_emission():
    with pytest.raises(ValueError, match="4 Z blocks"):
        next(iter_lamp_panel(Vec3(0, 8, 0), spec=LampPanelSpec(pixel_pitch_y=3)))
