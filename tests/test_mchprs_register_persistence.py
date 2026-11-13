"""Persist nonzero native registers, including both stopped-clock levels."""
import hashlib
import json
import os
from pathlib import Path
import re

import pytest

from mchprs_snapshot_fixture import execute
from shamaos.hardware.logic import TEMPLATES
from shamaos.hardware.memory import SUPPORT
from shamaos.mchprs_import import export_world
from shamaos.mchprs_snapshot import restore_snapshot
from shamaos.model import BlockState, Placement, Vec3
from shamaos.worldio import NativeWorldReader, NativeWorldWriter


@pytest.mark.parametrize("clock_high", (False, True))
def test_actual_nonzero_registers_survive_native_reopen_and_next_edge(tmp_path, clock_high):
    runner = os.environ.get("SHAMA_MCHPRS_TERRAIN_RUNNER")
    if not runner or not Path(runner).is_file():
        pytest.skip("terrain-enabled actual MCHPRS runner unavailable; register persistence is unverified")
    runner = Path(runner)
    template = TEMPLATES["DFF"]
    origins = [Vec3(-48 + index * 48, -60, 64) for index in range(4)]
    positions, levers, outputs = [], {}, {}
    coord = lambda point: [point.x, point.y, point.z]
    lever_state = BlockState.of("minecraft:lever", face="floor", facing="north", powered="false")
    for index, origin in enumerate(origins):
        positions.extend(template.emitter(origin, f"register-{index}"))
        for name, offset in template.pins.items():
            point = origin.offset(offset.x, offset.y, offset.z)
            outputs[f"{name}{index}"] = coord(point)
            if name != "Q":
                lever = point.offset(dz=1)
                levers[f"{name}{index}"] = coord(lever)
                positions.extend((Placement(lever.offset(dy=-1), SUPPORT, "register-controls"),
                                  Placement(lever, lever_state, "register-controls")))
    native_states = {}
    for placement in positions:
        assert native_states.setdefault(placement.pos, placement.block) == placement.block
    assert min(point.y for point in native_states) == -60
    source = tmp_path / "source-world"
    with NativeWorldWriter(source, surface_y=-61, spawn=Vec3(-64, -59, 96), spawn_platform=False) as writer:
        for placement in positions:
            writer.set_block(placement)
        assert writer.validate()["status"] == "passed"
    source_stream = tmp_path / "source.jsonl"
    source_import = export_world(source, source_stream)
    phase = 64
    saved_bits = [True, False, True, False]
    next_bits = [not value for value in saved_bits]
    expected = lambda bits: {f"Q{index}": value for index, value in enumerate(bits)}
    all_clocks = [levers[f"C{index}"] for index in range(4)]
    high_data = [levers[f"D{index}"] for index, value in enumerate(saved_bits) if value]
    steps = [
        {"ticks": phase, "expect": expected([False] * 4), "continuous": True},
        {"use_blocks": high_data, "ticks": phase, "expect": expected([False] * 4)},
        {"use_blocks": all_clocks, "ticks": phase, "expect": expected(saved_bits)},
        # Clear D while the master latches are locked. Saved Q must therefore
        # come from the actual stored register state, not a high data input.
        {"use_blocks": high_data, "ticks": phase, "expect": expected(saved_bits), "continuous": True},
    ]
    if not clock_high:
        steps.append({"use_blocks": all_clocks, "ticks": phase,
                      "expect": expected(saved_bits), "continuous": True})
    scenario = {"outputs": outputs, "initial_ticks": phase, "steps": steps}
    first = execute(runner, source_stream, scenario, tmp_path, "write-registers")
    restored = tmp_path / "restored-world"
    restore = restore_snapshot(tmp_path / "write-registers-snapshot.jsonl", source, restored,
                               expected_sha256=first["snapshot"]["sha256"])
    reader = NativeWorldReader(restored)
    for index, (origin, value) in enumerate(zip(origins, saved_bits)):
        slave = reader.get_block(origin.offset(0, 1, -6))
        assert slave.name == "minecraft:repeater"
        assert dict(slave.properties)["powered"] == str(value).lower()
        assert dict(slave.properties)["locked"] == str(not clock_high).lower()
        output_state = reader.get_block(Vec3(*outputs[f"Q{index}"]))
        assert output_state.name == "minecraft:redstone_wire"
        assert (int(dict(output_state.properties)["power"]) > 0) == value
        assert dict(reader.get_block(Vec3(*levers[f"D{index}"])).properties)["powered"] == "false"
        assert dict(reader.get_block(Vec3(*levers[f"C{index}"])).properties)["powered"] == str(clock_high).lower()
    restored_stream = tmp_path / "restored.jsonl"
    restored_import = export_world(restored, restored_stream)
    hold = {"outputs": outputs, "initial_ticks": phase,
            "steps": [{"ticks": 2 * phase, "expect": expected(saved_bits), "continuous": True}]}
    held = execute(runner, restored_stream, hold, tmp_path, "reopen-registers")

    def snapshot_body(path):
        return [record for line in path.read_text().splitlines()
                if (record := json.loads(line))["kind"] not in ("snapshot_header", "snapshot_complete")]

    assert snapshot_body(tmp_path / "write-registers-snapshot.jsonl") == \
        snapshot_body(tmp_path / "reopen-registers-snapshot.jsonl")
    # An unchanged initial world cannot pass the same retained-state checks.
    rejected = execute(runner, source_stream, hold, tmp_path, "initial-world-negative",
                       snapshot=False, succeeds=False)
    assert re.search(r"step=0 tick=0 output=Q[02] block=RedstoneWire", rejected)
    assert "left: false" in rejected and "right: true" in rejected
    new_data = [levers[f"D{index}"] for index, value in enumerate(next_bits) if value]
    resume_steps = [{"use_blocks": new_data, "ticks": phase,
                     "expect": expected(saved_bits), "continuous": True}]
    if clock_high:
        resume_steps.append({"use_blocks": all_clocks, "ticks": phase,
                             "expect": expected(saved_bits), "continuous": True})
    resume_steps.append({"use_blocks": all_clocks, "ticks": phase, "expect": expected(next_bits)})
    resumed = execute(runner, restored_stream, {"outputs": outputs, "initial_ticks": phase,
                      "steps": resume_steps}, tmp_path, "resume-registers")
    report = {
        "kind": "native-register-quiescent-save-reopen", "status": "passed",
        "clock_stopped_high": clock_high, "native_registers": 4,
        "saved_bits": saved_bits, "next_edge_bits": next_bits,
        "native_register_bits_checked_before_reimport": 4,
        "held_snapshot_exactly_unchanged": True,
        "unchanged_initial_world_rejected": True,
        "circuit_unique_blocks": len(native_states),
        "source_chunks": source_import["chunks"], "restored_chunks": restored_import["chunks"],
        "restore": restore,
        "runtime": {name: {key: result[key] for key in ("engine_revision", "ticks", "assertions",
                     "pending_ticks_after_scenario", "imported_stream_sha256", "scenario_sha256")}
                    for name, result in (("write", first), ("hold", held), ("resume", resumed))},
        "source_sha256": {name: hashlib.sha256((Path(__file__).resolve().parents[1] / name).read_bytes()).hexdigest()
                          for name in ("tests/test_mchprs_register_persistence.py", "tests/mchprs_snapshot_fixture.py",
                                       "src/shamaos/hardware/logic.py", "src/shamaos/mchprs_snapshot.py",
                                       "src/shamaos/mchprs_import.py", "src/shamaos/worldio.py")},
        "runner_sha256": hashlib.sha256(runner.read_bytes()).hexdigest(),
        "complete_machine_runtime_verified": False,
        "scope": "Four independent physical registers with saved levers; no complete-machine or active-tick persistence claim",
    }
    directory = Path(os.environ.get("SHAMA_VALIDATION_REPORT_DIR", tmp_path))
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"native-register-persistence-{'high' if clock_high else 'low'}.json").write_text(
        json.dumps(report, indent=2) + "\n")
