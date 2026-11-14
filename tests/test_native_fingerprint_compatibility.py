"""Preserve the existing saved-world digest contract while reducing its cost."""
from array import array
from copy import deepcopy

import pytest

from shamaos.hardware.memory import redstone_wire, repeater
from shamaos.model import BlockState, Vec3
from shamaos.worldio import AIR, _Chunk, _Section, _new_chunk


def sample_chunk(kind):
    chunk = _Chunk(-3, 7) if kind in ("air", "unused", "owned_air") else _new_chunk(-3, 7, -61)
    for sy in range(-4, 20):
        chunk.sections.setdefault(sy, _Section())
    if kind == "unused":
        chunk.sections[4].palette += [BlockState.of("minecraft:stone"), AIR]
    elif kind == "owned_air":
        chunk.sections[4].assigned[0] = 1
        chunk.sections[4].assigned[-1] = 128
    elif kind in ("sparse", "dense", "permuted"):
        states = [BlockState.of("minecraft:stone"), repeater("east", delay=3),
                  redstone_wire(power=7), BlockState.of("minecraft:redstone_lamp", lit="true")]
        positions = range(4096) if kind == "dense" else range(0, 4096, 37)
        section = chunk.sections[4]
        for index in positions:
            point = Vec3(chunk.x * 16 + (index & 15), 64 + (index >> 8), chunk.z * 16 + ((index >> 4) & 15))
            section.put(index, states[index % len(states)], point, "compatibility-fixture")
        if kind == "permuted":
            old = list(section.palette)
            section.palette = list(reversed(old)) + [old[1], BlockState.of("minecraft:dirt")]
            remap = {index: len(old) - 1 - index for index in range(len(old))}
            section.indices = array("H", (remap[index] for index in section.indices))
            # The duplicate palette entry is used, while dirt remains unused.
            section.indices[0] = len(old)
    return chunk


# Generated once with the unchanged fingerprint method from public 184faa9.
# These values protect compatibility with saved ledgers independently of the
# optimized method, including ownership, negative chunk coordinates and air.
GOLDEN = {'air': ('bac861d11b2c3eead872b083486a8199c64c7d4e749828e4b42b6494a59fd7df', 0),
 'unused': ('bac861d11b2c3eead872b083486a8199c64c7d4e749828e4b42b6494a59fd7df', 0),
 'owned_air': ('0510738df4a836ecc9e31874e1db49b7ea90c8826d98919d167207a4e66d8c65', 2),
 'terrain': ('bcbee12ea91ad3d94dd0bf74a65c16a95464ab7c80e89f9661d3167fef35d10c', 0),
 'sparse': ('5f6b3f1e0956c765b7a0f0eccd98a7ab32653af7107f50925f4cfde7fac62858', 111),
 'dense': ('f115130f0b69ac4e247b0b589b439e1a7c2006f260f6779ca9ae2f3b252d872f', 4096),
 'permuted': ('5f6b3f1e0956c765b7a0f0eccd98a7ab32653af7107f50925f4cfde7fac62858', 111)}


@pytest.mark.parametrize("kind", ["air", "unused", "owned_air", "terrain", "sparse", "dense", "permuted"])
def test_fingerprints_preserve_previous_saved_ledger_bytes(kind):
    assert sample_chunk(kind).fingerprint() == GOLDEN[kind]


def test_palette_reordering_duplicates_and_unused_states_preserve_semantics():
    assert sample_chunk("sparse").fingerprint() == sample_chunk("permuted").fingerprint()
    assert sample_chunk("air").fingerprint() == sample_chunk("unused").fingerprint()


@pytest.mark.parametrize("change", ["block", "ownership", "terrain", "position"])
def test_one_changed_semantic_value_changes_the_digest(change):
    chunk = sample_chunk("sparse")
    original = chunk.fingerprint()
    changed = deepcopy(chunk)
    if change == "block":
        changed.sections[4].indices[0] = 2
    elif change == "ownership":
        changed.sections[4].assigned[0] ^= 1
    elif change == "terrain":
        changed.sections[-4].indices[0] = 2
    else:
        changed.x += 1
    assert changed.fingerprint()[0] != original[0]


def test_invalid_index_is_not_hidden_by_uniform_air_palette():
    chunk = sample_chunk("air")
    chunk.sections[10].indices[0] = 1
    with pytest.raises(IndexError):
        chunk.fingerprint()
