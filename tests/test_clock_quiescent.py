"""Stop the actual oscillator without discarding work; reopen and restart it."""
from itertools import groupby
import os
from random import Random
from pathlib import Path

import pytest

from mchprs_snapshot_fixture import execute
from shamaos.hardware.clock import CLOCK_BOUNDS, CLOCK_HALF_PERIOD, clock_ports, iter_clock
from shamaos.hardware.memory import SUPPORT
from shamaos.mchprs_import import export_world
from shamaos.mchprs_snapshot import restore_snapshot
from shamaos.model import BlockState, Placement, Vec3
from shamaos.worldio import NativeWorldReader, NativeWorldWriter


def coords(point):
    return [point.x, point.y, point.z]


@pytest.fixture(scope="module")
def clock_world(tmp_path_factory):
    runner = os.environ.get("SHAMA_MCHPRS_TERRAIN_RUNNER")
    if not runner or not Path(runner).is_file():
        pytest.skip("terrain-aware actual redstone runner unavailable")
    directory = tmp_path_factory.mktemp("stoppable-clock")
    world, stream = directory / "world", directory / "world.jsonl"
    origin = Vec3(64, -60, 96)
    pins = clock_ports(origin)
    blocks = list(iter_clock(origin))
    occupied = {}
    for placement in blocks:
        assert occupied.setdefault(placement.pos, placement.block) == placement.block
        relative = placement.pos.offset(-origin.x, -origin.y, -origin.z)
        assert all(low <= getattr(relative, axis) <= high
                   for axis, low, high in zip("xyz", *CLOCK_BOUNDS))
    levers = {name: getattr(pins, name).offset(dz=1) for name in ("enable", "hold")}
    for point in levers.values():
        assert point not in occupied
        blocks.extend((Placement(point.offset(dy=-1), SUPPORT, "fixture"),
                       Placement(point, BlockState.of("minecraft:lever", face="floor",
                           facing="north", powered="false"), "fixture")))
    with NativeWorldWriter(world, surface_y=-61, spawn_platform=False) as writer:
        writer.set_blocks(blocks)
        report = writer.validate()
        assert report["status"] == "passed" and report["terrain_preserved"]
    export_world(world, stream)
    return Path(runner), world, stream, pins, levers


def scenario(pins, levers, phase, *, hold=False):
    steps = [{"ticks": 350 + phase, "expect": {"clock": False}, "continuous": True},
             {"use_blocks": [coords(levers["enable"])], "ticks": 350 + phase}]
    if hold:
        steps.extend(({"use_blocks": [coords(levers["hold"])], "ticks": 140},
                      {"use_blocks": [coords(levers["enable"])], "ticks": 350},
                      {"use_blocks": [coords(levers["hold"])], "ticks": 450,
                       "expect": {"clock": False}}))
    else:
        steps.append({"use_blocks": [coords(levers["enable"])], "ticks": 450,
                      "expect": {"clock": False}})
    steps.append({"ticks": 2 * CLOCK_HALF_PERIOD, "expect": {"clock": False}, "continuous": True})
    return {"inputs": {}, "outputs": {"clock": coords(pins.clock)}, "initial_ticks": 0,
            "capture_trace": True, "steps": steps}


def assert_whole_clock_pulses(trace):
    runs = [(level, sum(1 for _ in ticks)) for level, ticks in groupby(trace)]
    assert not runs[0][0] and not runs[-1][0]
    assert len(runs) >= 7
    for level, duration in runs[1:-1]:
        if level:
            assert duration == CLOCK_HALF_PERIOD
        else:
            assert duration >= CLOCK_HALF_PERIOD


@pytest.mark.parametrize("phase", range(2 * CLOCK_HALF_PERIOD))
def test_stopped_clock_reopens_and_restarts_with_shutdown_hold(clock_world, tmp_path, phase):
    runner, world, stream, pins, levers = clock_world
    first = execute(runner, stream, scenario(pins, levers, phase), tmp_path, "first")
    assert_whole_clock_pulses(first["traces"]["clock"])
    assert first["pending_ticks_after_scenario"] == 0
    restored = tmp_path / "restored-world"
    result = restore_snapshot(tmp_path / "first-snapshot.jsonl", world, restored,
                              expected_sha256=first["snapshot"]["sha256"])
    assert result["status"] == "quiescent-snapshot-native-readback-passed"
    reader = NativeWorldReader(restored)
    for point in levers.values():
        assert dict(reader.get_block(point).properties)["powered"] == "false"
    reopened = tmp_path / "reopened.jsonl"
    export_world(restored, reopened)
    resumed = execute(runner, reopened, scenario(pins, levers, phase, hold=True), tmp_path, "resumed")
    trace = resumed["traces"]["clock"]
    assert_whole_clock_pulses(trace)
    held_start = (350 + phase) * 2 + 140
    assert sum(trace[held_start:held_start + 350]) == 5 * CLOCK_HALF_PERIOD
    assert resumed["pending_ticks_after_scenario"] == 0


def test_active_clock_still_refuses_to_discard_pending_work(clock_world, tmp_path):
    runner, _, stream, pins, levers = clock_world
    active = scenario(pins, levers, 0)
    active["steps"] = active["steps"][:2]
    error = execute(runner, stream, active, tmp_path, "active", succeeds=False)
    assert "snapshot requires zero pending scheduled ticks" in error

def run_lever_changes(clock_world, tmp_path, changes):
    runner, _, stream, pins, levers = clock_world
    states = {"enable": False, "hold": False}
    steps = [{"ticks": 180, "expect": {"clock": False}, "continuous": True}]
    for ticks, requested in changes:
        toggles = [coords(levers[name]) for name, value in requested.items() if states[name] != value]
        states.update(requested)
        steps.append({"ticks": ticks, "use_blocks": toggles})
    assert not any(states.values())
    steps.append({"ticks": 400, "expect": {"clock": False}})
    steps.append({"ticks": 70, "expect": {"clock": False}, "continuous": True})
    start = pins.enable.offset(16)  # Actual A input of the NAND oscillator.
    response = execute(runner, stream, {"inputs": {}, "initial_ticks": 0, "steps": steps,
        "outputs": {"clock": coords(pins.clock), "armed": coords(start)}, "capture_trace": True},
        tmp_path, "lever-changes")
    assert response["pending_ticks_after_scenario"] == 0
    assert_whole_clock_pulses(response["traces"]["clock"])
    levels = [(level, sum(1 for _ in ticks)) for level, ticks in groupby(response["traces"]["armed"])]
    assert not levels[0][0] and not levels[-1][0]
    assert len(levels) >= 3
    assert all(duration >= CLOCK_HALF_PERIOD for _, duration in levels[1:-1])


@pytest.mark.parametrize("first_on,gap", [(10, 11), (15, 16)])
@pytest.mark.parametrize("restart", ["enable", "hold"])
def test_rapid_native_lever_restart_cannot_inject_a_second_clock_wave(clock_world, tmp_path, first_on, gap, restart):
    # An unconditioned ring produced 4- to 16-tick pulses even after the
    # second request was left on indefinitely. These are physical lever uses.
    run_lever_changes(clock_world, tmp_path, [
        (first_on, {"enable": True}), (gap, {"enable": False}),
        (350, {restart: True}), (1, {restart: False}),
    ])


def test_native_mixed_requests_and_timer_boundaries_preserve_clock_period(clock_world, tmp_path):
    rng = Random(51)
    changes = []
    for _ in range(100):
        ticks = rng.choice((1, 2, 3, 4, 6, 8, 12, 16, 23, 28, 33, 36, 39, 47, 62, 70))
        changes.append((ticks, {"enable": bool(rng.getrandbits(1)), "hold": bool(rng.getrandbits(1))}))
    # Exercise both sides of the longer line's release delay. Real power
    # and hold levers can hand off without a quiet interval between them.
    changes.append((220, {"enable": False, "hold": False}))
    for i, gap in enumerate(range(75, 130, 3)):
        source = "enable" if i % 2 else "hold"
        changes.extend(((1 + i % 10, {source: True}), (gap, {source: False})))
    changes.extend(((350, {"enable": True}), (1, {"enable": False})))
    run_lever_changes(clock_world, tmp_path, changes)
