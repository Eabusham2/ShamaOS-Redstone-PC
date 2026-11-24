"""Exercise the final physical cache write gate next to real stored bits."""
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


def test_guarded_cache_rows_survive_delayed_reset_and_save_reopen(tmp_path):
    runner = os.environ.get("SHAMA_MCHPRS_TERRAIN_RUNNER")
    if not runner or not Path(runner).is_file():
        pytest.skip("terrain-aware MCHPRS runner unavailable")
    runner = Path(runner)
    origin = Vec3(128, -56, 128)
    bank = MemoryBankSpec(words=2, word_bits=8)
    ports = bank_ports(origin, bank)
    gate = TEMPLATES["WRITE_GUARD"]
    write = ports.write_select[0]
    gate_origin = write.offset(dx=-gate.pins["Y"].x - 2, dy=-1)
    pins = {name: gate_origin.offset(point.x, point.y, point.z) for name, point in gate.pins.items()}

    def coords(point):
        return [point.x, point.y, point.z]

    outputs = {"commit": coords(write)}
    storage = {f"row{row}:{bit}": origin.offset(bit * bank.bit_pitch + 2, bank.cell_y,
                                              3 + row * bank.word_pitch)
               for row in range(2) for bit in range(8)}
    outputs.update({name: coords(point) for name, point in storage.items()})
    inputs = {name: coords(pins[name]) for name in ("A", "B")}

    def stored(first):
        return {f"row{row}:{bit}": bool(word & (1 << bit))
                for row, word in enumerate((first, 0x5A)) for bit in range(8)}

    world = tmp_path / "original"
    with NativeWorldWriter(world, surface_y=-61, spawn=Vec3(96, -59, 96), spawn_platform=False) as writer:
        connection = (
            Placement(write.offset(dx=-1, dy=-1), SUPPORT, "write-guard-link"),
            Placement(write.offset(dx=-1), repeater("east"), "write-guard-link"),
        )
        for blocks in (gate.emitter(gate_origin, "write-guard"), connection,
                       iter_memory_bank(origin, spec=bank, initial=bytes((0xA5, 0x5A)))):
            for placement in blocks:
                writer.set_block(placement)
        assert writer.validate()["status"] == "passed"
    stream = tmp_path / "original.jsonl"
    export_world(world, stream)
    unchanged = {"commit": False, **stored(0xA5)}
    steps = [
        {"ticks": 64, "expect": unchanged, "continuous": True},
        {"drive": {"B": True}, "ticks": 64, "expect": unchanged, "continuous": True},
        {"drive": {"A": True}, "ticks": 64, "expect": unchanged, "continuous": True},
        {"drive": {"B": False}, "ticks": 64, "expect": {"commit": True, **stored(0)}},
        {"drive": {"A": False}, "ticks": 64, "expect": {"commit": False, **stored(0)}},
    ]
    report = execute(runner, stream, {"inputs": inputs, "outputs": outputs, "initial_ticks": 0,
                     "steps": steps}, tmp_path, "guard-write")
    reopened = tmp_path / "reopened"
    restore_snapshot(tmp_path / "guard-write-snapshot.jsonl", world, reopened,
                     expected_sha256=report["snapshot"]["sha256"])
    reader = NativeWorldReader(reopened)
    for name, expected in stored(0).items():
        bit = reader.get_block(storage[name])
        assert bit.name == "minecraft:repeater"
        assert dict(bit.properties)["powered"] == str(expected).lower()
        assert dict(bit.properties)["locked"] == "true"
    reopened_stream = tmp_path / "reopened.jsonl"
    export_world(reopened, reopened_stream)
    execute(runner, reopened_stream, {"outputs": outputs, "initial_ticks": 0, "steps": [
        {"ticks": 64, "expect": {"commit": False, **stored(0)}, "continuous": True},
    ]}, tmp_path, "guard-reopen", snapshot=False)
