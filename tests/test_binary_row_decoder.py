"""Actual truth tables for the compact literal row-address decoder."""
import json
import os
from pathlib import Path
import subprocess
import time
from itertools import chain

import pytest

from shamaos.hardware.binary_row_decoder import (
    binary_row_address_ports, binary_row_collision_bounds, binary_row_obstacle_bounds, iter_binary_row_decoder,
    iter_binary_row_terminal_keepouts,
)
from shamaos.hardware.memory import MemoryBankSpec
from shamaos.hardware.memory_backbone import (
    StackedMemoryTileSpec, iter_stacked_tile_terminal_keepouts, stacked_tile_bounds, stacked_tile_ports,
)
from shamaos.model import Vec3
from test_mchprs_runtime import run_case, west_driver


@pytest.mark.parametrize("rows", [2, 8, 16, 32])
def test_actual_binary_row_decoder_selects_every_row_without_neighbor_activity(rows):
    origin = Vec3(400, 8, 128)
    tile = StackedMemoryTileSpec(MemoryBankSpec(words=rows, word_bits=32), 1, 12)
    decoder_x = origin.x - 138
    inputs = {f"A{bit}": point.offset(dz=-2) for bit, point in enumerate(binary_row_address_ports(origin, tile, decoder_x=decoder_x))}
    outputs = {f"R{row}": point for row, point in enumerate(stacked_tile_ports(origin, tile).row_select)}
    steps = [{"drive": {f"A{bit}": bool(address >> bit & 1) for bit in range(len(inputs))},
              "ticks": 256, "expect": {f"R{row}": row == address for row in range(rows)}}
             for address in [*(index ^ index >> 1 for index in range(rows)), 0]]
    run_case(iter_binary_row_decoder(origin, tile, decoder_x=decoder_x), inputs, outputs, steps, initial_ticks=256)


def run_full_row_decoder_case(work_path: Path, runner: str, *, decoder_x_offset: int = -138, tail_words: int | None = None) -> dict:
    """Check all 1,024 outputs for every binary address on the real long buses."""
    from shamaos.generator import source_provenance

    work_path.mkdir(parents=True, exist_ok=True)
    origin = Vec3(400, 8, 128)
    tile = StackedMemoryTileSpec(MemoryBankSpec(words=1024, word_bits=32), 1, 12, tail_words)
    decoder_x = origin.x + decoder_x_offset
    occupied_bounds = binary_row_collision_bounds(origin, tile, decoder_x=decoder_x)
    coord = lambda point: [point.x, point.y, point.z]
    inputs = {f"A{bit}": coord(point.offset(dz=-2)) for bit, point in enumerate(binary_row_address_ports(origin, tile, decoder_x=decoder_x))}
    outputs = {f"R{row}": coord(point) for row, point in enumerate(stacked_tile_ports(origin, tile).row_select)}
    addresses = [*(index ^ index >> 1 for index in range(1024)), 1023, 0, 511, 512, 341, 682, 0]
    steps = [{"drive": {f"A{bit}": bool(address >> bit & 1) for bit in range(10)}, "ticks": 1024,
              "expect": {f"R{row}": row == address for row in range(tile.words)}} for address in addresses]
    case_path = work_path / "case.json"
    count = 0
    seen = {}
    states = {}
    started = time.perf_counter()
    with case_path.open("w", encoding="utf-8") as handle:
        handle.write('{"blocks":[')
        for placement in iter_binary_row_decoder(origin, tile, decoder_x=decoder_x):
            point = placement.pos
            assert any(box.min.x <= point.x <= box.max.x and box.min.y <= point.y <= box.max.y
                       and box.min.z <= point.z <= box.max.z for box in occupied_bounds)
            key = point.x << 32 | point.y << 24 | point.z
            state = states.setdefault(placement.block, len(states))
            previous = seen.setdefault(key, state)
            assert previous == state, f"decoder block conflict at {point}"
            if count:
                handle.write(",")
            json.dump({"pos": coord(point), "name": placement.block.name, "properties": dict(placement.block.properties)}, handle, separators=(",", ":"))
            count += 1
        handle.write("]")
        for key, value in {"inputs": inputs, "outputs": outputs, "initial_ticks": 1024, "steps": steps}.items():
            handle.write("," + json.dumps(key) + ":")
            json.dump(value, handle, separators=(",", ":"))
        handle.write("}")
    unique_blocks = len(seen)
    del seen
    prepared = time.perf_counter()
    with case_path.open("r", encoding="utf-8") as handle:
        result = subprocess.run([runner], stdin=handle, capture_output=True, text=True, timeout=600)
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
    runtime = json.loads(result.stdout)
    runtime.pop("traces", None)
    assert runtime["status"] == "passed"
    report = {"kind": "literal-binary-decoder-runtime", "rows": tile.words, "address_bits": 10,
              "decoder_x_offset": decoder_x_offset,
              "collision_volumes_cover_every_placement": True,
              "distinct_addresses_tested": len(set(addresses)), "outputs_checked_after_each_step": tile.words,
              "emitted_placements": count, "unique_blocks": unique_blocks,
              "case_json_bytes": case_path.stat().st_size, "case_generation_seconds": prepared - started,
              "runtime_seconds": time.perf_counter() - prepared, "runtime": runtime,
              "source": source_provenance(), "complete_machine_runtime_verified": False}
    (work_path / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    destination = os.environ.get("SHAMA_VALIDATION_REPORT_DIR")
    if destination:
        Path(destination).mkdir(parents=True, exist_ok=True)
        name = "binary-row-decoder-runtime.json" if tail_words is None else "partial-binary-row-decoder-runtime.json"
        (Path(destination) / name).write_text(json.dumps(report, indent=2) + "\n")
    return report


def test_actual_full_1024_row_binary_decoder_all_addresses(tmp_path):
    runner = os.environ.get("SHAMA_MCHPRS_RUNNER")
    if not runner or not Path(runner).is_file():
        pytest.skip("actual MCHPRS runner not available; full binary row decoding is unverified")
    report = run_full_row_decoder_case(tmp_path, runner)
    assert report["distinct_addresses_tested"] == 1024
    assert report["runtime"]["steps"] == 1031


def test_actual_half_bank_decoder_tests_all_1024_addresses_without_aliasing(tmp_path):
    runner = os.environ.get("SHAMA_MCHPRS_RUNNER")
    if not runner or not Path(runner).is_file():
        pytest.skip("actual MCHPRS runner not available; partial decoding is unverified")
    report = run_full_row_decoder_case(tmp_path, runner, tail_words=512)
    assert report["rows"] == 512
    assert report["distinct_addresses_tested"] == 1024
    assert report["outputs_checked_after_each_step"] == 512


@pytest.mark.parametrize("decoder_x_offset", [-156, -138])
def test_production_binary_fabric_preserves_all_words_across_tiles(decoder_x_offset):
    """Exercise the real section/backbone factories and their exposed ABI."""
    from shamaos.generator import _memory_sections
    from shamaos.hardware.memory_backbone import (
        StackedMemoryFabricSpec, fabric_tile_ports, iter_memory_backbone,
    )

    origin = Vec3(256, 8, 128)
    bank = MemoryBankSpec(words=4, word_bits=32)
    fabric = StackedMemoryFabricSpec(total_bytes=5 * bank.bytes, bank=bank,
                                    banks_per_row=2, layers=3, binary_rows=True,
                                    decoder_x_offset=decoder_x_offset)
    ports = fabric_tile_ports(origin, fabric)
    assert [len(tile.bank_select) for tile in ports] == [3, 2]
    assert all(not tile.row_select and len(tile.row_address) == 2 for tile in ports)
    current = [[((bank_index + 1) * 0x9E3779B9 ^ row * 0x10204081) & 0xFFFFFFFF
                for row in range(bank.words)] for bank_index in range(fabric.bank_count)]
    initial = b"".join(value.to_bytes(4, "little") for words in current for value in words)
    sections = tuple(_memory_sections(name="binary-runtime", origin=origin, fabric=fabric, initial=initial))
    assert [section.metadata["first_bank_index"] for section in sections] == [0, 3]
    blocks = chain(chain.from_iterable(section.factory() for section in sections),
                   iter_memory_backbone(origin, fabric=fabric))
    inputs, outputs = {}, {}
    for group, tile in enumerate(ports):
        for field, points in (("A", tile.row_address), ("B", tile.bank_select), ("D", tile.write_data)):
            inputs.update({f"{group}:{field}{bit}": point.offset(dz=-2) for bit, point in enumerate(points)})
        inputs[f"{group}:READ"] = west_driver(tile.read_enable)
        inputs[f"{group}:WRITE"] = tile.write_enable.offset(dz=-2)
        outputs.update({f"{group}:OUT{bit}": point for bit, point in enumerate(tile.selected_word)})
    for bank_index in range(fabric.bank_count):
        base = fabric.bank_origin(origin, bank_index)
        outputs.update({f"S{bank_index}:{row}:{bit}": base.offset(bit * bank.bit_pitch + 2, bank.cell_y, 3 + row * bank.word_pitch)
                        for row in range(bank.words) for bit in range(32)})

    def stored():
        return {f"S{bank_index}:{row}:{bit}": bool(value >> bit & 1)
                for bank_index, words in enumerate(current) for row, value in enumerate(words)
                for bit in range(32)}

    def read_values(group=None, value=0):
        return {f"{tile}:OUT{bit}": bool(value >> bit & 1) if tile == group else False
                for tile in range(len(ports)) for bit in range(32)}

    def address(row):
        return {f"{tile}:A{bit}": bool(row >> bit & 1) for tile in range(len(ports)) for bit in range(2)}

    steps = [{"ticks": 512, "expect": stored() | read_values()}]
    for bank_index in range(fabric.bank_count):
        group, layer = divmod(bank_index, fabric.layers)
        for row in range(bank.words):
            steps.extend([
                {"drive": address(row), "ticks": 512, "expect": stored() | read_values()},
                {"drive": {f"{group}:B{layer}": True, **{f"{tile}:READ": True for tile in range(len(ports))}},
                 "ticks": 512, "expect": stored() | read_values(group, current[bank_index][row])},
                {"drive": {f"{group}:B{layer}": False, **{f"{tile}:READ": False for tile in range(len(ports))}},
                 "ticks": 512, "expect": stored() | read_values()},
            ])
    for bank_index, row, value in ((0, 0, 0), (2, 3, 0xFFFFFFFF), (3, 0, 0xA55A0FF0), (4, 3, 0x5AA5F00F)):
        group, layer = divmod(bank_index, fabric.layers)
        steps.append({"drive": address(row) | {f"{tile}:D{bit}": bool(value >> bit & 1)
                                               for tile in range(len(ports)) for bit in range(32)},
                      "ticks": 512, "expect": stored() | read_values()})
        current[bank_index][row] = value
        steps.extend([
            {"drive": {f"{group}:B{layer}": True, **{f"{tile}:WRITE": True for tile in range(len(ports))}},
             "ticks": 512, "expect": stored() | read_values()},
            {"drive": {**{f"{tile}:WRITE": False for tile in range(len(ports))},
                       **{f"{tile}:READ": True for tile in range(len(ports))}},
             "ticks": 512, "expect": stored() | read_values(group, value)},
            {"drive": {f"{group}:B{layer}": False, **{f"{tile}:READ": False for tile in range(len(ports))}},
             "ticks": 512, "expect": stored() | read_values()},
        ])
    result = run_case(blocks, inputs, outputs, steps, initial_ticks=512)
    assert result["status"] == "passed"


def test_full_1024_row_mixed_fabric_external_access_clears_neighboring_tile():
    from shamaos.generator import geometry_from_config, load_config
    from shamaos.hardware.external_router import _plan_escapes
    from shamaos.layout import default_origins, memory_specs, vram_spec

    geometry = geometry_from_config(load_config("configs/default.toml"))
    origins = default_origins(geometry)
    cache, ram, _ = memory_specs(geometry)
    vram = vram_spec(geometry)
    origin, bank = origins.cache, cache.bank
    assert bank.words == 1024
    assert origins.cache.x == origins.vram.x == origins.ram.x
    cases = tuple((base, fabric.bank_count, base.x + fabric.decoder_x_offset)
                  for base, fabric in ((origins.cache, cache), (origins.vram, vram), (origins.ram, ram)))
    points, access, endpoint_bounds, boxes, obstacles = [], {}, {}, [], []
    for base, count, decoder_x in cases:
        tile = StackedMemoryTileSpec(bank, count, 12)
        pins = stacked_tile_ports(base, tile)
        box = stacked_tile_bounds(base, tile)
        boxes.append(box)
        obstacles.extend(iter_stacked_tile_terminal_keepouts(base, tile))
        obstacles.extend(iter_binary_row_terminal_keepouts(base, tile, decoder_x=decoder_x))
        decoder_boxes = binary_row_obstacle_bounds(base, tile, decoder_x=decoder_x)
        boxes.extend(decoder_boxes)
        groups = ((pins.bank_select, "north"), ((pins.write_enable,), "north"),
                  ((pins.read_enable,), "west"), (pins.write_data, "north"), (pins.selected_word, "south"))
        for group, direction in groups:
            for point in group:
                points.append(point)
                access[point] = (direction,)
                endpoint_bounds[point] = box
        for point, bus_box in zip(binary_row_address_ports(base, tile, decoder_x=decoder_x), decoder_boxes[1:]):
            points.append(point)
            access[point] = ("north",)
            endpoint_bounds[point] = bus_box
    boxes.append(stacked_tile_bounds(origin.offset(dx=-360), StackedMemoryTileSpec(bank, 30, 12)))
    routes = _plan_escapes(points, 314, obstacles, access=access, endpoint_bounds=endpoint_bounds,
                           obstacle_bounds=boxes, minimum_y=-60)
    assert len(routes) == len(points) == 258
