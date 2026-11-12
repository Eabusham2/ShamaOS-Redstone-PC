from collections import Counter, OrderedDict

import pytest

from shamaos.hardware.memory import MemoryBankSpec, iter_memory_bank
from shamaos.hardware.memory_streaming import iter_memory_bank_ordered
from shamaos.model import Vec3
from shamaos.worldio import NativeWorldWriter, NativeWorldReader


@pytest.mark.parametrize("words,bits,pitch", [(1, 8, 5), (4, 8, 8), (17, 32, 5), (17, 32, 8)])
def test_ordered_bank_preserves_every_placement_state_and_initial_bit(words, bits, pitch):
    spec = MemoryBankSpec(words=words, word_bits=bits, bit_pitch=pitch)
    image = bytes((index * 37 + 129) & 255 for index in range(spec.bytes))
    origin = Vec3(-17, 64, -33)
    original = Counter(iter_memory_bank(origin, spec=spec, initial=image))
    ordered = Counter(iter_memory_bank_ordered(origin, spec=spec, initial=image))
    assert ordered == original


def test_z_slab_order_reduces_chunk_reloads_without_changing_capacity():
    spec = MemoryBankSpec(words=128, word_bits=32)

    def reloads(iterator):
        cache = OrderedDict()
        misses = count = 0
        for placement in iterator:
            count += 1
            key = placement.pos.x // 16, placement.pos.z // 16
            if key not in cache:
                misses += 1
                cache[key] = True
                if len(cache) > 32:
                    cache.popitem(last=False)
            else:
                cache.move_to_end(key)
        return count, misses

    original_count, original_misses = reloads(iter_memory_bank(Vec3(0, 64, 0), spec=spec))
    ordered_count, ordered_misses = reloads(iter_memory_bank_ordered(Vec3(0, 64, 0), spec=spec))
    assert ordered_count == original_count
    assert ordered_misses < original_misses // 3


def test_ordered_world_readback_retains_full_physical_bank(tmp_path):
    spec = MemoryBankSpec(words=4, word_bits=8)
    image = b"\xa5\x12\x80\xff"
    path = tmp_path / "ordered-bank"
    with NativeWorldWriter(path, spawn_platform=False, max_cached_chunks=2) as writer:
        writer.set_blocks(iter_memory_bank_ordered(Vec3(0, 64, 0), spec=spec, initial=image))
        report = writer.validate()
    expected = {p.pos: p.block for p in iter_memory_bank(Vec3(0, 64, 0), spec=spec, initial=image)}
    actual = {p.pos: p.block for p in NativeWorldReader(path).iter_blocks()}
    assert actual == expected
    assert report["assigned_blocks"] == len(expected)
