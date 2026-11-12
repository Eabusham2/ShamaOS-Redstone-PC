"""Compact storage must retain its real read/write behavior and bit isolation."""
import random

import pytest

from shamaos.hardware.memory import MemoryBankSpec, bank_ports, iter_memory_bank
from shamaos.model import Vec3
from test_mchprs_runtime import run_case, west_driver


@pytest.mark.parametrize("pitch,words,width", [(5,8,8),(6,8,8),(8,8,8),(5,32,32)])
def test_compact_storage_walking_bits_random_writes_and_neighbor_isolation(pitch, words, width):
    origin = Vec3(128,32,128)
    spec = MemoryBankSpec(words=words,word_bits=width,bit_pitch=pitch)
    rng = random.Random(72109 + words)
    mask = (1 << width) - 1
    values = [rng.getrandbits(width) for _ in range(words)]
    initial = b"".join(value.to_bytes(width // 8,"little") for value in values)
    pins = bank_ports(origin,spec)
    inputs = {f"D{i}":west_driver(p) for i,p in enumerate(pins.data_in)}
    inputs |= {f"W{i}":west_driver(p) for i,p in enumerate(pins.write_select)}
    inputs |= {f"R{i}":west_driver(p) for i,p in enumerate(pins.read_select)}
    outputs = {f"Q{i}":p for i,p in enumerate(pins.data_out)}
    outputs |= {f"S{word}:{bit}":origin.offset(bit*pitch+2,3,3+word*6)
                for word in range(words) for bit in range(width)}

    def read_bits(value):
        return {f"Q{bit}":bool((value >> bit) & 1) for bit in range(width)}

    def stored_bits():
        return {f"S{word}:{bit}":bool((value >> bit) & 1)
                for word,value in enumerate(values) for bit in range(width)}

    steps = [{"ticks":96,"expect":stored_bits(),"continuous":True}]
    patterns = [0,mask]
    patterns += [value for bit in range(width) for value in (1 << bit,mask ^ (1 << bit))]
    patterns += [rng.getrandbits(width) for _ in range(32)]
    for value in patterns:
        word = rng.randrange(words)
        steps.append({"drive":{f"D{i}":bool((value >> i) & 1) for i in range(width)},
                      "ticks":96,"expect":stored_bits(),"continuous":True})
        values[word] = value
        steps += [
            {"drive":{f"W{word}":True},"ticks":96,"expect":stored_bits()},
            {"drive":{f"W{word}":False},"ticks":96,"expect":stored_bits(),"continuous":True},
        ]
        for selected in (word,(word - 1) % words,(word + 1) % words):
            steps += [
                {"drive":{f"R{selected}":True},"ticks":96,"expect":read_bits(values[selected])},
                {"drive":{f"R{selected}":False},"ticks":96,"expect":read_bits(0)},
            ]
    placements = list(iter_memory_bank(origin,spec=spec,initial=initial))
    seen = {}
    for placement in placements:
        assert placement.pos not in seen, f"overlapping placement at {placement.pos}"
        seen[placement.pos] = placement.block
    run_case(placements,inputs,outputs,steps)


def test_compact_bank_uses_fewer_than_62_blocks_per_stored_bit():
    spec = MemoryBankSpec(words=64,word_bits=32)
    placements = list(iter_memory_bank(Vec3(0,64,0),spec=spec))
    assert spec.bit_pitch == 5
    assert len(placements) / (spec.words * spec.word_bits) < 62
    assert any(p.block.name == "minecraft:target" for p in placements)


def test_insufficient_cell_pitch_is_rejected_instead_of_creating_crosstalk():
    with pytest.raises(ValueError,match="isolate"):
        list(iter_memory_bank(Vec3(0,64,0),spec=MemoryBankSpec(words=1,word_bits=8,bit_pitch=4)))
