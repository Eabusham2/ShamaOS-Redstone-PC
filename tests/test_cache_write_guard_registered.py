"""Exercise a real commit register, write guard and preloaded storage together.

This fixture proves the final write path and reset cancellation. It does not
substitute for the complete mapped controller and its routed timing proof.
"""
from __future__ import annotations

from itertools import chain
import os
from pathlib import Path

import pytest

from mchprs_snapshot_fixture import execute
from shamaos.hardware.logic import TEMPLATES
from shamaos.hardware.memory import SUPPORT, MemoryBankSpec, bank_ports, iter_memory_bank, repeater
from shamaos.mchprs_import import export_world
from shamaos.mchprs_snapshot import restore_snapshot
from shamaos.model import Placement, Vec3
from shamaos.worldio import NativeWorldReader, NativeWorldWriter
from test_mchprs_runtime import run_case, west_driver


INITIAL_WORDS = (0xA55AC33C, 0x12345678)
FINAL_WORDS = (0x80000001, INITIAL_WORDS[1])
PHASE_TICKS = 64


def _stored(words):
    return {f"row{row}:{bit}": bool(word & (1 << bit))
            for row, word in enumerate(words) for bit in range(32)}


def _data(word):
    return {f"data:{bit}": bool(word & (1 << bit)) for bit in range(32)}


def _fixture():
    origin = Vec3(128, 32, 128)
    bank = MemoryBankSpec(words=2, word_bits=32)
    ports = bank_ports(origin, bank)
    guard = TEMPLATES["WRITE_GUARD"]
    register = TEMPLATES["DFF"]
    write_pin = ports.write_select[0]
    guard_origin = write_pin.offset(-guard.pins["Y"].x - 2, -1)
    guard_pins = {name: guard_origin.offset(pin.x, pin.y, pin.z)
                  for name, pin in guard.pins.items()}
    register_origin = guard_pins["A"].offset(-register.pins["Q"].x - 2, -1)
    register_pins = {name: register_origin.offset(pin.x, pin.y, pin.z)
                     for name, pin in register.pins.items()}
    links = []
    for destination in (guard_pins["A"], write_pin):
        links.extend((
            Placement(destination.offset(-1, -1), SUPPORT, "registered-write-link"),
            Placement(destination.offset(-1), repeater("east"), "registered-write-link"),
        ))
    blocks = list(chain(
        register.emitter(register_origin, "commit-register"),
        guard.emitter(guard_origin, "commit-write-guard"),
        links,
        iter_memory_bank(origin, spec=bank,
                         initial=b"".join(word.to_bytes(4, "little") for word in INITIAL_WORDS)),
    ))
    storage = {f"row{row}:{bit}": origin.offset(bit * bank.bit_pitch + 2, bank.cell_y,
                                               3 + row * bank.word_pitch)
               for row in range(2) for bit in range(32)}
    inputs = {
        "clock": register_pins["C"],
        "pending_d": register_pins["D"],
        "reset": guard_pins["B"],
        **{f"data:{bit}": west_driver(pin) for bit, pin in enumerate(ports.data_in)},
        **{f"read:{row}": west_driver(pin) for row, pin in enumerate(ports.read_select)},
    }
    outputs = {
        "pending_q": register_pins["Q"], "commit": write_pin,
        **storage,
        **{f"read_data:{bit}": pin for bit, pin in enumerate(ports.data_out)},
    }
    occupied = {}
    for placement in blocks:
        assert occupied.setdefault(placement.pos, placement.block) == placement.block
    # Both actual-engine harnesses drive one block south of each input anchor.
    # In particular, data drivers must not replace a memory bus or latch.
    drivers = {name: pin.offset(dz=1) for name, pin in inputs.items()}
    assert len(set(drivers.values())) == len(drivers)
    assert not set(drivers.values()) & occupied.keys()
    return blocks, inputs, outputs, storage


def _scenario():
    words = list(INITIAL_WORDS)
    steps = []

    def step(drive=None, *, pending=None, commit=False, continuous=True):
        expected = _stored(words) | {"commit": commit}
        if pending is not None:
            expected["pending_q"] = pending
        value = {"ticks": PHASE_TICKS, "expect": expected, "continuous": continuous}
        if drive:
            value["drive"] = drive
        steps.append(value)

    # The register and final comparator must both start inactive, including
    # every startup tick before reset has reached the final gate.
    step(pending=False)
    step({"reset": True}, pending=False)

    # Hold an actual captured commit high while reset masks it. Change all
    # data inputs and cancel that staged write before releasing reset.
    step({"pending_d": True, **_data(0x5AA53CC3)}, pending=False)
    step({"clock": True})
    step({**_data(0xC33CA55A)}, pending=True)
    step({"pending_d": False}, pending=True)
    step({"clock": False}, pending=True)
    step({"clock": True})
    step({"reset": False}, pending=False)
    step({"clock": False}, pending=False)

    for value in (0x5AA53CC3, 0, 0xFFFFFFFF, FINAL_WORDS[0]):
        step({"pending_d": True, **_data(value)}, pending=False)
        words[0] = value
        step({"clock": True}, pending=True, commit=True, continuous=False)
        # Reset stops an already captured commit without another clock edge.
        step({"reset": True}, pending=True, continuous=False)
        step({**_data(value ^ 0xFFFFFFFF)}, pending=True)
        step({"pending_d": False}, pending=True)
        step({"clock": False}, pending=True)
        step({"clock": True})
        step({"reset": False}, pending=False)
        step({"clock": False}, pending=False)

    assert tuple(words) == FINAL_WORDS
    step({**_data(0)}, pending=False)
    return steps


def _read_scenario():
    expected = _stored(FINAL_WORDS) | {"pending_q": False, "commit": False}
    steps = [{"ticks": PHASE_TICKS, "expect": expected, "continuous": True}]
    for row, word in enumerate(FINAL_WORDS):
        steps.extend((
            {"drive": {f"read:{row}": True}, "ticks": PHASE_TICKS,
             "expect": expected | {f"read_data:{bit}": bool(word & (1 << bit)) for bit in range(32)}},
            {"drive": {f"read:{row}": False}, "ticks": PHASE_TICKS,
             "expect": expected | {f"read_data:{bit}": False for bit in range(32)}},
        ))
    return steps


def test_actual_registered_guard_preserves_preload_and_cancels_reset_writes():
    blocks, inputs, outputs, _storage = _fixture()
    result = run_case(blocks, inputs, outputs, _scenario() + _read_scenario(),
                      initial_ticks=0, capture_trace=True)
    # Even during permitted writes, the adjacent row must never change.
    for bit in range(32):
        expected = bool(INITIAL_WORDS[1] & (1 << bit))
        assert all(value == expected for value in result["traces"][f"row1:{bit}"])


def test_saved_registered_guard_retains_nonzero_data_and_cancelled_commit(tmp_path):
    runner = os.environ.get("SHAMA_MCHPRS_TERRAIN_RUNNER")
    if not runner or not Path(runner).is_file():
        pytest.skip("terrain-aware MCHPRS runner unavailable")
    blocks, inputs, outputs, storage = _fixture()
    coords = lambda point: [point.x, point.y, point.z]
    world = tmp_path / "original"
    with NativeWorldWriter(world, surface_y=-61, spawn=Vec3(96, -59, 96), spawn_platform=False) as writer:
        for placement in blocks:
            writer.set_block(placement)
        assert writer.validate()["status"] == "passed"
    stream = tmp_path / "original.jsonl"
    export_world(world, stream)
    scenario = {"inputs": {name: coords(pin) for name, pin in inputs.items()},
                "outputs": {name: coords(pin) for name, pin in outputs.items()},
                "initial_ticks": 0, "steps": _scenario(), "capture_trace": True}
    report = execute(Path(runner), stream, scenario, tmp_path, "registered-write")
    for bit in range(32):
        expected = bool(INITIAL_WORDS[1] & (1 << bit))
        assert all(value == expected for value in report["traces"][f"row1:{bit}"])
    reopened = tmp_path / "reopened"
    restored = restore_snapshot(tmp_path / "registered-write-snapshot.jsonl", world, reopened,
                                expected_sha256=report["snapshot"]["sha256"])
    assert restored["status"] == "quiescent-snapshot-native-readback-passed"
    reader = NativeWorldReader(reopened)
    for name, expected in _stored(FINAL_WORDS).items():
        state = reader.get_block(storage[name])
        assert state.name == "minecraft:repeater"
        assert dict(state.properties)["powered"] == str(expected).lower()
        assert dict(state.properties)["locked"] == "true"
    reopened_stream = tmp_path / "reopened.jsonl"
    export_world(reopened, reopened_stream)
    scenario.update(steps=_read_scenario(), capture_trace=False)
    execute(Path(runner), reopened_stream, scenario, tmp_path, "registered-reopen", snapshot=False)
