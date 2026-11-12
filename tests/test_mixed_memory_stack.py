"""Evidence for sharing unused height between independent memory fabrics.

This fixture does not enable production co-location: the full 1,024-row
external interfaces must also be routed without crossing another tile.
"""
from itertools import chain
import json
import os
from pathlib import Path
import pytest

from shamaos.hardware.memory import MemoryBankSpec
from shamaos.hardware.memory_backbone import (
    StackedMemoryTileSpec, iter_stacked_memory_tile, stacked_tile_bounds,
    stacked_tile_ports,
)
from shamaos.hardware.memory_interfaces import iter_memory_row_interfaces, memory_row_interface_ports
from shamaos.hardware.binary_row_decoder import binary_row_address_ports, iter_binary_row_decoder
from shamaos.model import Placement, Vec3
from test_mchprs_runtime import run_case, west_driver


@pytest.mark.parametrize("row_interface", ["direct", "helical", "binary", "binary-saved"])
def test_actual_mixed_ram_cache_vram_stack_preserves_independent_control(row_interface, tmp_path):
    binary = row_interface.startswith("binary")
    spec = MemoryBankSpec(words=2, word_bits=32)
    origin = Vec3(256, -56, 128)
    # All 96 KiB RAM, 8 KiB cache and 16 KiB VRAM banks fit one horizontal
    # tile when the small fabrics go first. Uniform +64Y is only for the
    # MCHPRS TestWorld; the saved fixture retains its real above-grass Y.
    counts = {"cache": 2, "vram": 4, "ram": 24}
    bases, tiles, pins = {}, {}, {}
    next_layer = 0
    for fabric_index, (name, banks) in enumerate(counts.items()):
        bases[name] = origin.offset(dy=12 * next_layer + (4 * fabric_index if row_interface != "direct" else 0))
        tiles[name] = StackedMemoryTileSpec(spec, banks, 12)
        pins[name] = stacked_tile_ports(bases[name], tiles[name])
        next_layer += banks
    boxes = [stacked_tile_bounds(bases[name], tiles[name]) for name in counts]
    assert [(box.min.y, box.max.y) for box in boxes] == (
        [(-60, -37), (-32, 15), (20, 307)] if row_interface != "direct"
        else [(-60, -37), (-36, 11), (12, 299)])
    riser_x = {fabric: origin.x - 160 + 12 * index for index, fabric in enumerate(counts)}
    decoder_x = {fabric: origin.x - 170 + 16 * index for index, fabric in enumerate(counts)}

    shift = lambda p: p.offset(dy=64)
    inputs, outputs = {}, {}
    current = {}
    for fabric, count in counts.items():
        points = pins[fabric]
        rows = memory_row_interface_ports(bases[fabric], tiles[fabric], riser_x=riser_x[fabric]) if row_interface == "helical" else points.row_select
        if binary:
            rows = ()
            address = binary_row_address_ports(bases[fabric], tiles[fabric], decoder_x=decoder_x[fabric], address_bits=10)
            inputs.update({f"{fabric}:A{bit}": shift(point.offset(dz=-2)) for bit, point in enumerate(address)})
        for field, points_in_field in (("D", points.write_data), ("ROW", rows), ("BANK", points.bank_select)):
            inputs.update({f"{fabric}:{field}{index}": shift(point.offset(dz=-2)) if binary and field in ("D", "BANK") else west_driver(shift(point))
                           for index, point in enumerate(points_in_field)})
        inputs[f"{fabric}:READ"] = west_driver(shift(points.read_enable))
        inputs[f"{fabric}:WRITE"] = shift(points.write_enable.offset(dz=-2)) if binary else west_driver(shift(points.write_enable))
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
    if row_interface == "helical":
        placements = chain(placements, chain.from_iterable(
            iter_memory_row_interfaces(bases[fabric], tiles[fabric], riser_x=riser_x[fabric], component=f"{fabric}-rows")
            for fabric in counts))
    if binary:
        placements = chain(placements, chain.from_iterable(
            iter_binary_row_decoder(bases[fabric], tiles[fabric], decoder_x=decoder_x[fabric], address_bits=10, component=f"{fabric}-decode")
            for fabric in counts))
        decoded_steps = []
        for step in steps:
            drive = step.get("drive", {})
            address_drive = {}
            remaining = {}
            for key, value in drive.items():
                fabric, field = key.split(":")
                if field.startswith("ROW"):
                    if value:
                        row = int(field[3:])
                        address_drive.update({f"{fabric}:A{bit}": bool(row >> bit & 1) for bit in range(10)})
                else:
                    remaining[key] = value
            if address_drive:
                # The controller must disable access while a binary address
                # propagates; committing during the transition is unsafe.
                decoded_steps.append({"drive": address_drive, "ticks": 512})
            decoded_steps.append(step | {"drive": remaining})
        steps = decoded_steps
    verification = None
    if row_interface == "binary-saved":
        runner = os.environ.get("SHAMA_MCHPRS_RUNNER")
        if not runner or not Path(runner).is_file():
            pytest.skip("actual MCHPRS runner not available; saved mixed binary memory is unverified")
        from shamaos.generator import source_provenance, write_placement_stages
        from shamaos.worldio import NativeWorldReader

        blocks = list(placements)
        report_directory = os.environ.get("SHAMA_VALIDATION_REPORT_DIR")
        world = Path(report_directory) / "mixed-binary-memory-world" if report_directory else tmp_path / "mixed-world"
        _, _, verification = write_placement_stages(world, [("mixed-binary-memory", blocks)],
                                                    surface_y=-61, spawn=Vec3(64, -59, 64))
        reader = NativeWorldReader(world)
        positions = sorted({placement.pos for placement in blocks})
        assert min(point.y for point in positions) == -60
        assert max(point.y for point in positions) == 307
        for point in (positions[0], positions[-1], origin):
            assert reader.get_block(Vec3(point.x, -61, point.z)).name == "minecraft:grass_block"
        placements = (Placement(point, reader.get_block(point), "reopened-mixed-binary-memory") for point in positions)
    result = run_case((Placement(shift(p.pos), p.block, p.component) for p in placements), inputs, outputs, steps, initial_ticks=512)
    assert result["status"] == "passed"
    if verification is not None:
        result.pop("traces", None)
        report = {"kind": "saved-mixed-binary-memory-runtime", "complete_machine_runtime_verified": False,
                  "banks_by_fabric": counts, "words_per_bank": 2, "word_bits": 32,
                  "world_validation": verification, "runtime": result, "unique_circuit_blocks": len(positions),
                  "surface_y": -61, "minimum_circuit_y": -60, "maximum_circuit_y": 307,
                  "runtime_translation_y": 64, "source": source_provenance()}
        (world / "shamaos" / "mixed-memory-runtime.json").write_text(json.dumps(report, indent=2) + "\n")
