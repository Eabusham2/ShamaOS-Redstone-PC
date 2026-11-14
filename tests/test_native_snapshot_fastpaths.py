"""Exact native output and strict section validation for snapshot fast paths."""
from array import array
import hashlib
import io

import pytest

from shamaos import mchprs_snapshot as snapshot
from shamaos import worldio
from shamaos.model import BlockState


AIR = worldio.AIR
STONE = BlockState.of("minecraft:stone")
OFF = worldio.canonical_block_state(BlockState.of("minecraft:comparator", powered="false"))
ON = worldio.canonical_block_state(BlockState.of("minecraft:comparator", powered="true"))


def state_record(state):
    return {"name": state.name, "properties": dict(state.properties)}


@pytest.mark.parametrize("state", (AIR, STONE, OFF, ON,
    worldio.canonical_block_state(BlockState.of("minecraft:repeater", powered="true"))))
def test_uniform_section_keeps_exact_state_indices_and_empty_ownership(state):
    count = 0 if state == AIR else 4096
    section, actual = snapshot._section({"kind": "uniform_section", "state": state_record(state), "nonair": count})
    assert actual == count
    assert section.palette == [state]
    assert section.indices == array("H", [0]) * 4096
    assert section.assigned == bytearray(512)
    assert section.lookup == {state: 0}


@pytest.mark.parametrize("state,nonair", (
    (state_record(AIR), 1), (state_record(STONE), 0), (state_record(AIR), True),
    (state_record(STONE), False), (state_record(STONE), 4097), (state_record(AIR), -1),
    ({"name": "minecraft:command_block", "properties": {}}, 4096),
    ({"name": "minecraft:air", "properties": {"unknown": "true"}}, 0),
    ({"name": "minecraft:comparator", "properties": {}}, 4096),
    ({"name": "minecraft:comparator", "properties": {"powered": True}}, 4096),
    ({"name": "minecraft:stone"}, 4096),
    ({"name": 1, "properties": {}}, 4096),
))
def test_uniform_section_fast_path_keeps_strict_state_and_count_validation(state, nonair):
    with pytest.raises(worldio.WorldWriteError):
        snapshot._section({"kind": "uniform_section", "state": state, "nonair": nonair})


def chunk_case(name):
    chunk = worldio._new_chunk(-7, 11, -61)
    if name == "empty":
        return worldio._Chunk(-7, 11)
    section = worldio._Section()
    if name == "owned_air":
        section.assigned[0] = 1
    elif name in ("uniform_stone", "unused_invalid_comparator"):
        unused = OFF if name == "uniform_stone" else BlockState.of("minecraft:comparator", powered="invalid")
        section.palette = [STONE, unused]
        section.assigned[0] = 255
    elif name == "sparse":
        section.palette = [AIR, STONE, OFF]
        for index in (0, 7, 129, 4095):
            section.indices[index] = 1
            section.assigned[index >> 3] |= 1 << (index & 7)
        section.assigned[32] |= 1  # Explicit ownership of air is preserved.
    elif name == "comparators":
        section.palette = [AIR, OFF, ON, STONE]
        for index, value in ((0, 1), (255, 2), (256, 3), (4095, 2)):
            section.indices[index] = value
            section.assigned[index >> 3] |= 1 << (index & 7)
    elif name == "uniform_comparators":
        section.palette = [ON]
        section.assigned[:] = b"\xff" * 512
    else:
        raise AssertionError(name)
    chunk.sections[4] = section
    return chunk


def nbt_bytes(root):
    stream = io.BytesIO()
    root.write(stream)
    return stream.getvalue()


# Serialized NBT hashes captured from the actual pre-fast-path f7ae2e4 method.
GOLDEN_NBT = {
    "empty": "ece5b49c15da56070eef2052e7cd1867df291597fdd21697dcfea493012fd589",
    "owned_air": "65bce79d5654a6f37829b3930e64c9ad5bfb27ab55fc1c212a25b820eed7a97b",
    "uniform_stone": "283308853942aec00d4b44876bb58f83565210e3bc744ddc8d562faecd0529f4",
    "unused_invalid_comparator": "283308853942aec00d4b44876bb58f83565210e3bc744ddc8d562faecd0529f4",
    "sparse": "c37fab04c4854d8e683380a46a71f93b57c7a132e8eccc5fa59d2ced0aac60e3",
    "comparators": "62c4bdb393ba8c7e0872dcb11eb0ea86e24503df3087a2a64509fbbd382dd6fe",
    "uniform_comparators": "886ef18117b335d08c7bba9349931d268dc29ed7098510982acc8dbc6d3046c9",
}


@pytest.mark.parametrize("name", GOLDEN_NBT)
def test_fast_paths_preserve_historical_native_bytes_and_ownership(name):
    chunk = chunk_case(name)
    root = worldio._chunk_nbt(chunk)
    assert hashlib.sha256(nbt_bytes(root)).hexdigest() == GOLDEN_NBT[name]
    assert worldio._decode_chunk(root, chunk.x, chunk.z).fingerprint() == chunk.fingerprint()
    entities = root["block_entities"]
    assert len(entities) == (4096 if name == "uniform_comparators" else 3 if name == "comparators" else 0)
    if name == "comparators":
        assert [(int(e["x"]), int(e["y"]), int(e["z"]), int(e["OutputSignal"])) for e in entities] == [
            (-112, 64, 176, 0), (-97, 64, 191, 15), (-97, 79, 191, 15)]


@pytest.mark.parametrize("index", (1, 65535))
def test_no_comparator_fast_path_still_rejects_invalid_used_palette_index(index):
    chunk = worldio._Chunk(0, 0, {0: worldio._Section()})
    chunk.sections[0].indices[4095] = index
    with pytest.raises(IndexError):
        worldio._chunk_nbt(chunk)


def test_used_comparator_still_requires_its_powered_property():
    section = worldio._Section(palette=[BlockState.of("minecraft:comparator")])
    with pytest.raises(KeyError, match="powered"):
        worldio._chunk_nbt(worldio._Chunk(0, 0, {0: section}))
