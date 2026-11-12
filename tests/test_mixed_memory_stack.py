"""Evidence for sharing unused height between independent memory fabrics.

This fixture does not enable production co-location: the full 1,024-row
external interfaces must also be routed without crossing another tile.
"""
from itertools import chain

from shamaos.hardware.memory import MemoryBankSpec
from shamaos.hardware.memory_backbone import (
    StackedMemoryTileSpec, iter_stacked_memory_tile, stacked_tile_bounds,
    stacked_tile_ports,
)
from shamaos.model import Placement, Vec3
from test_mchprs_runtime import run_case, west_driver


def test_actual_mixed_ram_cache_vram_stack_preserves_independent_control():
    spec = MemoryBankSpec(words=2, word_bits=32)
    origin = Vec3(256, -56, 128)
    # Exact remaining production RAM layers, followed by the approved complete
    # cache and VRAM bank counts. Uniform +64Y is only for MCHPRS TestWorld.
    counts = {"ram": 18, "cache": 2, "vram": 4}
    bases, tiles, pins = {}, {}, {}
    next_layer = 0
    for name, banks in counts.items():
        bases[name] = origin.offset(dy=12 * next_layer)
        tiles[name] = StackedMemoryTileSpec(spec, banks, 12)
        pins[name] = stacked_tile_ports(bases[name], tiles[name])
        next_layer += banks
    boxes = [stacked_tile_bounds(bases[name], tiles[name]) for name in counts]
    assert [(box.min.y, box.max.y) for box in boxes] == [(-60, 155), (156, 179), (180, 227)]

    shift = lambda p: p.offset(dy=64)
    inputs, outputs = {}, {}
    current = {}
    for fabric, count in counts.items():
        points = pins[fabric]
        for field, points_in_field in (("D", points.write_data), ("ROW", points.row_select), ("BANK", points.bank_select)):
            inputs.update({f"{fabric}:{field}{index}": west_driver(shift(point)) for index, point in enumerate(points_in_field)})
        inputs[f"{fabric}:READ"] = west_driver(shift(points.read_enable))
        inputs[f"{fabric}:WRITE"] = west_driver(shift(points.write_enable))
        outputs.update({f"{fabric}:OUT{bit}": shift(point) for bit, point in enumerate(points.selected_word)})
        current[fabric] = [[((bank + 7) * 0x9E3779B9 ^ (word + len(fabric)) * 0xA55AC33C) & 0xFFFFFFFF
                            for word in range(2)] for bank in range(count)]
        outputs.update({f"{fabric}:S{bank}:{word}:{bit}": shift(bases[fabric].offset(bit * spec.bit_pitch + 2, spec.cell_y + bank * 12, 3 + word * spec.word_pitch))
                        for bank in range(count) for word in range(2) for bit in range(32)})
    initial = {fabric: b"".join(value.to_bytes(4, "little") for words in current[fabric] for value in words)
               for fabric in counts}

    def stored():
        return {f"{fabric}:S{bank}:{word}:{bit}": bool(value >> bit & 1)
                for fabric, banks in current.items() for bank, words in enumerate(banks)
                for word, value in enumerate(words) for bit in range(32)}

    def read_values(**values):
        return {f"{fabric}:OUT{bit}": bool(values.get(fabric, 0) >> bit & 1)
                for fabric in counts for bit in range(32)}

    steps = [{"ticks": 512, "expect": stored() | read_values()}]
    for fabric, banks in counts.items():
        for bank in sorted({0, banks // 2, banks - 1}):
            for word in range(2):
                steps += [
                    {"drive": {f"{fabric}:BANK{bank}": True, f"{fabric}:ROW{word}": True, f"{fabric}:READ": True},
                     "ticks": 512, "expect": stored() | read_values(**{fabric: current[fabric][bank][word]})},
                    {"drive": {f"{fabric}:BANK{bank}": False, f"{fabric}:ROW{word}": False, f"{fabric}:READ": False},
                     "ticks": 512, "expect": stored() | read_values()},
                ]
        for bank, value in ((0, 0), (banks - 1, 0xFFFFFFFF)):
            steps.append({"drive": {f"{fabric}:D{bit}": bool(value >> bit & 1) for bit in range(32)},
                          "ticks": 512, "expect": stored() | read_values(), "continuous": True})
            current[fabric][bank][1] = value
            steps += [
                {"drive": {f"{fabric}:BANK{bank}": True, f"{fabric}:ROW1": True, f"{fabric}:WRITE": True},
                 "ticks": 512, "expect": stored() | read_values()},
                {"drive": {f"{fabric}:WRITE": False, f"{fabric}:READ": True},
                 "ticks": 512, "expect": stored() | read_values(**{fabric: value})},
                {"drive": {f"{fabric}:BANK{bank}": False, f"{fabric}:ROW1": False, f"{fabric}:READ": False},
                 "ticks": 512, "expect": stored() | read_values()},
            ]

    # Concurrent distinct values exercise independence, not only idle neighbors.
    values = {"ram": 0xA55A0FF0, "cache": 0x5AA5F00F, "vram": 0x12345678}
    steps.append({"drive": {f"{fabric}:D{bit}": bool(value >> bit & 1)
                             for fabric, value in values.items() for bit in range(32)},
                  "ticks": 512, "expect": stored() | read_values(), "continuous": True})
    for fabric, value in values.items():
        current[fabric][-1][0] = value
    steps += [
        {"drive": {key: True for fabric, banks in counts.items()
                    for key in (f"{fabric}:BANK{banks-1}", f"{fabric}:ROW0", f"{fabric}:WRITE")},
         "ticks": 512, "expect": stored() | read_values()},
        {"drive": {key: value for fabric in counts for key, value in ((f"{fabric}:WRITE", False), (f"{fabric}:READ", True))},
         "ticks": 512, "expect": stored() | read_values(**values)},
        {"drive": {key: False for fabric, banks in counts.items()
                    for key in (f"{fabric}:BANK{banks-1}", f"{fabric}:ROW0", f"{fabric}:READ")},
         "ticks": 512, "expect": stored() | read_values()},
    ]
    placements = chain.from_iterable(iter_stacked_memory_tile(bases[fabric], tile=tiles[fabric], initial=initial[fabric], component=fabric)
                                     for fabric in counts)
    result = run_case((Placement(shift(p.pos), p.block, p.component) for p in placements), inputs, outputs, steps, initial_ticks=512)
    assert result["status"] == "passed"
