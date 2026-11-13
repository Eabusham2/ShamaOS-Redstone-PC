"""Isolated literal row-tiling feasibility study; no production geometry change."""
from __future__ import annotations

from array import array
from collections import Counter
from dataclasses import replace
import hashlib
from itertools import chain
import json
from pathlib import Path
import resource
import time

import numpy as np

from shamaos.hardware.binary_row_decoder import binary_row_address_ports, iter_binary_row_decoder
from shamaos.hardware.memory import DUST, SUPPORT, MemoryBankSpec, _bus_x, repeater
from shamaos.hardware.memory_backbone import StackedMemoryTileSpec, iter_stacked_memory_tile, stacked_tile_ports
from shamaos.hardware.routing import iter_stair
from shamaos.hardware.vertical_wiring import helical_wire_points, iter_helical_wire
from shamaos.model import BlockState, Placement, Vec3


ORIGIN = Vec3(256, -56, 512)
CAPACITY_WORDS = 1024
WORD_BITS = 32
STRIDE_X = 328


def flat_z(start, end, component):
    assert start.x == end.x and start.y == end.y and start.z <= end.z
    for index, z in enumerate(range(start.z, end.z + 1)):
        point = Vec3(start.x, start.y, z)
        buffered = z not in (start.z, end.z) and (index == 1 or index % 10 == 0 or z == end.z - 1)
        yield Placement(point.offset(dy=-1), SUPPORT, component)
        yield Placement(point, repeater("south") if buffered else DUST, component)


def cells(tiles, *, include_connections=True):
    assert CAPACITY_WORDS % tiles == 0
    words = CAPACITY_WORDS // tiles
    bank = MemoryBankSpec(words=words, word_bits=WORD_BITS)
    tile = StackedMemoryTileSpec(bank, banks=1, layer_pitch=12)
    bases = [ORIGIN.offset(dx=index * STRIDE_X) for index in range(tiles)]
    ports = [stacked_tile_ports(base, tile) for base in bases]
    values = [((word + 1) * 0x9E3779B9 ^ 0xA53C96F0) & 0xFFFFFFFF for word in range(CAPACITY_WORDS)]
    initial = b"".join(value.to_bytes(4, "little") for value in values)
    for index, base in enumerate(bases):
        yield from iter_stacked_memory_tile(base, tile=tile,
            initial=initial[index * bank.bytes:(index + 1) * bank.bytes], component=f"local-{index}")
        decoder_x = base.x - 138
        # Retain the complete 10-bit physical comparison at every row. A later
        # independent high-bit qualifier could save gates, but is not assumed.
        for placement in iter_binary_row_decoder(base, tile, decoder_x=decoder_x,
                                                  address_bits=10, component=f"decoder-{index}"):
            row_name = placement.component.rsplit(":row", 1)
            if len(row_name) == 2:
                local_row = int(row_name[1])
                address = index * words + local_row
                bit, remainder = divmod(placement.pos.y - base.y, 12)
                if (remainder == 0 and 0 <= bit < 10 and address >> bit & 1
                        and not (local_row >> bit & 1)):
                    if placement.pos.x == decoder_x + 6:
                        placement = replace(placement, block=SUPPORT)
                    elif placement.pos.x == decoder_x + 5:
                        placement = replace(placement, block=BlockState.of(
                            "minecraft:redstone_wall_torch", facing="west", lit="true"))
            yield placement
    if tiles == 1 or not include_connections:
        return

    west = ORIGIN.x - 160
    # Each write-data bit has one horizontal bus and isolated downward spirals.
    # The lower legs share no wire with other bits. All supports stay above grass.
    for bit in range(WORD_BITS):
        taps = []
        for pin in (p.write_data[bit] for p in ports):
            base = Vec3(pin.x, pin.y, ORIGIN.z - 32 - 8 * bit)
            top = helical_wire_points(base, 18)[-1]
            taps.append(top)
            yield from iter_helical_wire(base, 18, signal_upward=False, component=f"write-fanout:{bit}")
            yield from flat_z(base, pin, f"write-fanout:{bit}")
        yield from _bus_x(west, taps[-1].x, taps[0].y, taps[0].z, facing="east",
                         taps=frozenset(p.x for p in taps), component=f"write-bus:{bit}")

    # Each selected local word injects through an upward spiral's final diode
    # into one common westbound bit bus, preserving isolated OR semantics.
    for bit in range(WORD_BITS):
        taps = []
        for pin in (p.selected_word[bit] for p in ports):
            base = Vec3(pin.x, pin.y, ORIGIN.z + words * bank.word_pitch + 32 + 8 * bit)
            top = helical_wire_points(base, 6)[-1]
            taps.append(top)
            yield from flat_z(pin, base, f"read-return:{bit}")
            yield from iter_helical_wire(base, 6, signal_upward=True, component=f"read-return:{bit}")
        yield from _bus_x(west, taps[-1].x, taps[0].y, taps[0].z, facing="west",
                         taps=frozenset(p.x for p in taps), component=f"read-bus:{bit}")

    # Ten independently separated binary address buses retain the exact global
    # logical word number, including the three tile-index bits.
    addresses = [binary_row_address_ports(base, tile, decoder_x=base.x - 138, address_bits=10)
                 for base in bases]
    for bit in range(10):
        pins = [a[bit] for a in addresses]
        z = ORIGIN.z - 320
        for pin in pins:
            yield from flat_z(Vec3(pin.x, pin.y, z), pin, f"address-legs:{bit}")
        yield from _bus_x(west, pins[-1].x, pins[0].y, z, facing="east",
                         taps=frozenset(p.x for p in pins), component=f"address-bus:{bit}")

    # Independent enable planes pass between the binary-address levels. Each
    # drops only after it clears the front data buses.
    for name, height, pins in (
        ("bank", 6, [p.bank_select[0] for p in ports]),
        ("write", 9, [p.write_enable for p in ports]),
        ("read", 21, [p.read_enable for p in ports]),
    ):
        z = ORIGIN.z - 336
        y = ORIGIN.y + height
        taps = []
        for pin in pins:
            # Stay west of the native two gate planes while descending.
            drop_x = pin.x - {"bank": 0, "write": 13, "read": 21}[name]
            taps.append(drop_x)
            turn = Vec3(drop_x, y, ORIGIN.z - 23)
            landing = Vec3(drop_x, pin.y, pin.z)
            yield from flat_z(Vec3(drop_x, y, z), turn, f"enable-legs:{name}")
            yield from iter_stair(turn, landing, axis="z", signal_forward=True, component=f"enable-drop:{name}")
            if drop_x != pin.x:
                yield from _bus_x(drop_x + 1, pin.x, pin.y, pin.z, facing="east", component=f"enable-final:{name}")
        yield from _bus_x(west, taps[-1], y, z, facing="east",
                         taps=frozenset(taps), component=f"enable-bus:{name}")


def profile(tiles, *, include_connections=True):
    started = time.monotonic()
    packed = array("Q")
    states = array("H")
    state_ids = {}
    state_by_id = []
    counts = Counter()
    chunks = set()
    lower, upper = [10**9] * 3, [-10**9] * 3
    for p in cells(tiles, include_connections=include_connections):
        x, y, z = p.pos.x, p.pos.y, p.pos.z
        assert y >= -60, (p.component, p.pos)
        assert y <= 319, (p.component, p.pos)
        key = ((x + (1 << 20)) << 31) | ((z + (1 << 20)) << 10) | (y + 64)
        packed.append(key)
        if p.block not in state_ids:
            state_ids[p.block] = len(state_ids)
            state_by_id.append(p.block)
        states.append(state_ids[p.block])
        counts[p.component.split(":", 1)[0].split("-", 1)[0]] += 1
        chunks.add((x // 16, z // 16))
        for axis, value in enumerate((x, y, z)):
            lower[axis] = min(lower[axis], value)
            upper[axis] = max(upper[axis], value)
    coords = np.frombuffer(packed, dtype=np.uint64)
    kinds = np.frombuffer(states, dtype=np.uint16)
    order = np.argsort(coords, kind="stable")
    sorted_coords, sorted_kinds = coords[order], kinds[order]
    duplicate = sorted_coords[1:] == sorted_coords[:-1]
    mismatched = np.flatnonzero(duplicate & (sorted_kinds[1:] != sorted_kinds[:-1]))
    examples = []
    for i in mismatched[:30]:
        value = int(sorted_coords[i])
        x = (value >> 31) - (1 << 20)
        z = ((value >> 10) & ((1 << 21) - 1)) - (1 << 20)
        y = (value & 1023) - 64
        a, b = state_by_id[int(sorted_kinds[i])], state_by_id[int(sorted_kinds[i + 1])]
        examples.append({"position": [x, y, z], "first": str(a), "last": str(b)})
    envelope = [(upper[axis] // 16 - lower[axis] // 16 + 1) for axis in (0, 2)]
    return {"tiles": tiles, "rows_per_tile": 1024 // tiles, "capacity_bytes": 4096,
            "stored_bits": 32768, "shared_connections_emitted": include_connections and tiles > 1,
            "emitted_placements": len(packed), "unique_positions": len(packed) - int(np.sum(duplicate)),
            "coordinate_state_conflicts": len(mismatched), "conflict_examples": examples,
            "components_emitted": dict(counts), "occupied_horizontal_chunks": len(chunks),
            "bounds": {"minimum": lower, "maximum": upper},
            "horizontal_dimensions_blocks": [upper[i] - lower[i] + 1 for i in (0, 2)],
            "horizontal_chunk_envelope": envelope, "envelope_chunks": envelope[0] * envelope[1],
            "elapsed_seconds": time.monotonic() - started,
            "process_peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "actual_runtime_verified": False, "complete_machine_runtime_verified": False}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tiles", type=int, default=8)
    parser.add_argument("--without-connections", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = profile(args.tiles, include_connections=not args.without_connections)
    report["experiment_source_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
