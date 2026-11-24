"""Physical flash cell addressing and offline program file inspection."""
from copy import deepcopy
from pathlib import Path
import hashlib
import json
import struct

import nbtlib
import pytest

from shamaos import native_program_flash as probe
from shamaos.flashfs import FileType, JOURNAL_OFFSET, ShamaFS
from shamaos.generator import build_plan, geometry_from_config, load_config, manifest
from shamaos.hardware.memory import MemoryBankSpec, iter_memory_bank, repeater
from shamaos.layout import default_origins, memory_specs
from shamaos.model import BlockState, Placement, Vec3
from shamaos.worldio import NativeWorldWriter, WorldWriteError, _Region


def test_installed_flash_indices_match_production_bank_origins():
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "configs/default.toml")
    metadata = manifest(config, build_plan(config))
    g = geometry_from_config(config)
    flash = memory_specs(g)[2]
    assert probe._flash_banks(metadata) == [flash.bank_origin(default_origins(g).flash, i) for i in range(50)]
    bad = deepcopy(metadata)
    tiles = [entry for entry in bad["physical_sections"] if entry["name"].startswith("flash-tile-")]
    tiles[1]["origin"] = tiles[0]["origin"]
    with pytest.raises(WorldWriteError, match="distinct"):
        probe._flash_banks(bad)
    tiles[1]["origin"] = dict(tiles[0]["origin"], x=tiles[0]["origin"]["x"] + 5)
    with pytest.raises(WorldWriteError, match="alias"):
        probe._flash_banks(bad)


@pytest.mark.parametrize("broken", [False, True])
def test_read_actual_saved_flash_storage_with_vanilla_light_and_pending_tick(tmp_path, broken):
    world = tmp_path / "world"
    origin = Vec3(-20, -37, -7)
    bank = MemoryBankSpec(words=2)
    expected = bytes.fromhex("01100003fe0180af")
    first = origin.offset(2, bank.cell_y, 3)
    with NativeWorldWriter(world, spawn_platform=False, surface_y=-61) as writer:
        for p in iter_memory_bank(origin, spec=bank, initial=expected):
            if broken and p.pos == first:
                p = Placement(first, repeater("east", locked=False), p.component)
            writer.set_block(p)
    cx, cz = first.x // 16, first.z // 16
    region = _Region(world / "region" / f"r.{cx // 32}.{cz // 32}.mca")
    slot = (cx & 31) + (cz & 31) * 32
    chunk = region.read(slot)
    chunk["sections"].append(nbtlib.Compound({"Y": nbtlib.Byte(-5), "SkyLight": nbtlib.ByteArray([0] * 2048)}))
    chunk["block_ticks"].append(nbtlib.Compound({"i": nbtlib.String("minecraft:repeater"),
        "x": nbtlib.Int(first.x), "y": nbtlib.Int(first.y), "z": nbtlib.Int(first.z),
        "t": nbtlib.Int(3), "p": nbtlib.Int(0)}))
    region.write(slot, chunk)
    reader = probe._FlashReader(world)
    if broken:
        with pytest.raises(WorldWriteError, match="locked flash"):
            probe._read_flash_bank(reader, origin, bank)
    else:
        assert probe._read_flash_bank(reader, origin, bank) == expected
        reader.check_files()
        region.write(slot, chunk)
        with pytest.raises(WorldWriteError, match="changed"):
            reader.check_files()


@pytest.mark.parametrize("bad", [None, "missing", "existing", "wrong-binary", "wrong-source", "wrong-type", "journal", "powered", "bitmap"])
def test_compare_only_actual_parsed_flash_files(tmp_path, monkeypatch, bad):
    before, after = tmp_path / "candidate/world", tmp_path / "after"
    fresh = ShamaFS()
    if bad == "existing":
        fresh.create_file("Z.BIN", b"old", FileType.BIN)
    original = fresh.serialize()
    saved = ShamaFS()
    saved.create_file("Z.ASM", b"HLT\n" if bad == "wrong-source" else probe.PROGRAM_SOURCE, FileType.ASM)
    if bad != "missing":
        saved.create_file("Z.BIN", bytes(12) if bad == "wrong-binary" else probe.PROGRAM_BINARY,
                          FileType.TXT if bad == "wrong-type" else FileType.BIN)
    actual = bytearray(saved.serialize())
    if bad == "journal":
        struct.pack_into("<I", actual, JOURNAL_OFFSET, 1)
    if bad == "bitmap":
        block = saved.stat("Z.ASM").start_block
        actual[saved.bitmap_start * saved.block_size + block // 8] &= ~(1 << (block % 8))
    images = {before: original, after: actual}
    # Exercise actual ShamaFS decoding over complete images; the smaller Anvil
    # test above separately covers the physical repeater-to-byte reader.
    class Reader:
        def __init__(self, world):
            self.world = world

        def get_block(self, _):
            return BlockState.of("minecraft:lever", powered="true" if bad == "powered" else "false")

        def check_files(self):
            pass

    monkeypatch.setattr(probe, "_FlashReader", Reader)
    monkeypatch.setattr(probe, "_flash_banks", lambda _: range(50))
    monkeypatch.setattr(probe, "_read_flash_bank", lambda reader, i, bank: images[reader.world][i*4096:(i+1)*4096])
    if bad:
        with pytest.raises(WorldWriteError):
            probe.inspect_saved_program(before, after, {}, Vec3(0, 0, 0))
    else:
        result = probe.inspect_saved_program(before, after, {}, Vec3(0, 0, 0))
        assert result["complete_machine_runtime_verified"] is False
        assert result["original_flash_sha256"] == hashlib.sha256(original).hexdigest()
        assert result["saved_flash_sha256"] == hashlib.sha256(actual).hexdigest()
        assert bytes.fromhex(result["files"]["Z.ASM"]["hex"]) == b"LDI R1 42\nHLT\n"
        assert bytes.fromhex(result["files"]["Z.BIN"]["hex"]) == struct.pack("<3I", 0x03100000, 42, 0x01000000)
        assert result["files"]["Z.BIN"]["start_block"] != result["files"]["Z.ASM"]["start_block"]
        from shamaos.native_console_scenario import main
        before.parent.mkdir()
        (before.parent / "generated-manifest.json").write_text(json.dumps({"physical_sections": [
            {"kind": "keyboard-controller", "name": "input", "origin": {"x": 0, "y": 0, "z": 0}}]}))
        output = tmp_path / "flash-observations.json"
        assert main(["--inspect-saved-program", str(before.parent), str(after), str(output)]) == 0
        assert json.loads(output.read_text()) == result
