"""Power up the mapped commit and bank-select cones beside preloaded native cells."""
import json
from pathlib import Path
import shutil

import pytest

from shamaos.hardware.binary_row_decoder import iter_binary_row_decoder
from shamaos.hardware.local_router import compact_tile
from shamaos.hardware.logic import template_for
from shamaos.hardware.memory import DUST, SUPPORT, MemoryBankSpec, bank_ports, repeater
from shamaos.hardware.memory_backbone import StackedMemoryTileSpec, iter_stacked_memory_tile, stacked_tile_ports
from shamaos.hardware.partition_planner import load_mapped_module
from shamaos.hardware.routing import iter_stair
from shamaos.model import Placement, Vec3
from shamaos.synthesis import build_physical_netlist, synthesize_json
from test_mchprs_runtime import run_case, west_driver


ROOT = Path(__file__).resolve().parents[1]
WORDS = (0xA55AC33C, 0x12345678, 0x11223344, 0xABCDEF01)


def _cold_cone(tmp_path, region, unguarded):
    top = f"shama_{region}_adapter"
    source = ROOT / "rtl/shama_memory_adapter.sv"
    if unguarded:
        # Fault control uses the former direct commit logic in the actual adapter.
        original = source.read_text()
        old = f".CAPACITY_BYTES(`SHAMA_{region.upper()}_BYTES),.GUARDED_WRITE(1)"
        assert original.count(old) == 1
        source = tmp_path / source.name
        source.write_text(original.replace(old, old.replace("GUARDED_WRITE(1)", "GUARDED_WRITE(0)")))
        for name in ("shama_boot_params.svh", "shama_native_cells.svh"):
            shutil.copyfile(ROOT / "rtl" / name, tmp_path / name)
    mapped = synthesize_json([source], top=top, output_json=tmp_path / "mapped.json", repo_root=ROOT)
    module, _ = load_mapped_module(mapped, top)
    drivers = {bits[0]: (name, cell) for name, cell in module["cells"].items()
               for pin, bits in cell["connections"].items() if pin in ("Q", "Y")}
    ports = {name: {"direction": "output", "bits": module["ports"][name]["bits"][:1]}
             for name in ("bank_select", "write_commit")}
    commit = drivers[ports["write_commit"]["bits"][0]][1]
    if not unguarded:
        assert commit["type"] == "WRITE_GUARD"
        assert commit["connections"]["B"] == module["ports"]["rst"]["bits"]
        register = drivers[commit["connections"]["A"][0]][1]
        assert template_for(register["type"]).cell_type == "DFF"
        assert register["connections"]["C"] == module["ports"]["clk"]["bits"]

    # Retain every gate and DFF from both outputs back to the register inputs.
    # A cold world has no clock edges. D inputs at the cut are initially low.
    selected = {}
    pending = [port["bits"][0] for port in ports.values()]
    while pending:
        bit = pending.pop()
        if bit not in drivers:
            continue
        name, cell = drivers[bit]
        if name in selected:
            continue
        selected[name] = cell
        if "Q" not in cell["connections"]:
            pending.extend(bits[0] for pin, bits in cell["connections"].items() if pin != "Y")
    cut = {bit for cell in selected.values() for pin, bits in cell["connections"].items()
           for bit in bits if pin not in ("Q", "Y") and isinstance(bit, int)
           and (bit not in drivers or drivers[bit][0] not in selected)}
    originals = {bit: name for name, port in module["ports"].items()
                 if port["direction"] == "input" for bit in port["bits"]}
    cut_names = {bit: originals.get(bit, f"cut_{bit}") for bit in cut}
    ports.update({name: {"bits": [bit], "direction": "input"} for bit, name in cut_names.items()})
    path = tmp_path / "cone.json"
    path.write_text(json.dumps({"modules": {"cold": {"cells": selected, "ports": ports}}}))
    tile = compact_tile(build_physical_netlist(path, top="cold", origin=Vec3(32, 32, 400)))
    pending_input = None if unguarded else cut_names[register["connections"]["D"][0]]
    return tile, pending_input


def _supported(point, state):
    yield Placement(point.offset(dy=-1), SUPPORT, "fixture-lead")
    yield Placement(point, state, "fixture-lead")


def _lead(source, destination, *, bank):
    # Route east on independent height planes, then enter each native pin from
    # the west. The longer commit delay lets bank selection and row decode settle.
    start = source
    stop_x = destination.x
    if bank:
        yield from _supported(source.offset(dz=1), repeater("south"))
        start = source.offset(0, 8, 12)
        yield from iter_stair(source.offset(dz=2), start, axis="z", signal_forward=True, component="fixture-lead")
        stop_x -= 12
    for z in range(start.z + 1, destination.z + 1):
        refresh = z < destination.z and (z - start.z) % 8 == 1
        yield from _supported(Vec3(start.x, start.y, z), repeater("south") if refresh else DUST)
    for x in range(start.x + 1, stop_x):
        refresh = (x - start.x) % 8 == 1 or x == stop_x - 1
        state = repeater("east", delay=1 if bank else 4) if refresh else DUST
        yield from _supported(Vec3(x, start.y, destination.z), state)
    if bank:
        yield from iter_stair(Vec3(stop_x, start.y, destination.z), destination.offset(dx=-2),
                              axis="x", signal_forward=True, component="fixture-lead")
        yield from _supported(destination.offset(dx=-1), repeater("east"))


@pytest.mark.parametrize("region,unguarded", [("ram", True), ("cache", False), ("ram", False),
                         ("flash", False), ("vram", False)],
                         ids=("previous-direct-ram", "cache", "ram", "flash", "vram"))
def test_mapped_cold_commit_retains_preloaded_rows(tmp_path, region, unguarded):
    if not shutil.which("yosys"):
        pytest.skip("mapped startup test requires Yosys")
    logic, pending_input = _cold_cone(tmp_path, region, unguarded)
    pins = {name: points[0] for name, points in logic.physical.output_ports.items()}
    origin = Vec3(max(pin.x for pin in pins.values()) + 240, pins["write_commit"].y, pins["write_commit"].z + 64)
    bank = MemoryBankSpec(words=2, word_bits=32)
    tile = StackedMemoryTileSpec(bank, 2)
    fabric = stacked_tile_ports(origin, tile)
    blocks = list(logic.iter_placements())
    blocks.extend(iter_stacked_memory_tile(origin, tile=tile, initial=b"".join(w.to_bytes(4, "little") for w in WORDS)))
    blocks.extend(iter_binary_row_decoder(origin, tile, decoder_x=origin.x - 170, address_bits=10))
    blocks.extend(_lead(pins["bank_select"], fabric.bank_select[0], bank=True))
    blocks.extend(_lead(pins["write_commit"], fabric.write_enable, bank=False))
    outputs = {"commit": pins["write_commit"], "bank0": fabric.bank_select[0], "row0": fabric.row_select[0],
               "tile_write": fabric.write_enable, "row_write": bank_ports(origin, bank).write_select[0]}
    for word in range(4):
        for bit in range(32):
            outputs[f"{word}:{bit}"] = origin.offset(bit * bank.bit_pitch + 2,
                bank.cell_y + word // 2 * tile.layer_pitch, 3 + word % 2 * bank.word_pitch)

    def storage(first):
        return {f"{i}:{bit}": bool(word & (1 << bit)) for i, word in enumerate((first, *WORDS[1:]))
                for bit in range(32)}

    inactive = {"commit": False, "tile_write": False, "row_write": False}
    selected = {"bank0": True, "row0": True}
    steps = [{"ticks": 2048, "expect": inactive | storage(0 if unguarded else WORDS[0]),
              "continuous": not unguarded}, {"ticks": 1, "expect": selected}]
    if unguarded:
        result = run_case(blocks, {}, outputs, steps, initial_ticks=0, capture_trace=True)
        assert all(any(result["traces"][pin]) for pin in inactive)
        return

    inputs = {name: west_driver(logic.physical.input_ports[port][0])
              for name, port in (("clk", "clk"), ("rst", "rst"), ("pending_d", pending_input))}
    # Capture under reset, cancel before release, then allow one captured write.
    def step(drive, first, writing=False):
        expected = dict.fromkeys(inactive, writing) | selected | storage(first)
        return {"drive": drive, "ticks": 1024, "expect": expected}

    steps.extend((step({"rst": True, "pending_d": True}, WORDS[0]), step({"clk": True}, WORDS[0]),
                  step({"clk": False, "pending_d": False}, WORDS[0]), step({"clk": True}, WORDS[0]),
                  step({"rst": False, "clk": False, "pending_d": True}, WORDS[0]),
                  step({"clk": True}, 0, writing=True), step({"rst": True}, 0)))
    run_case(blocks, inputs, outputs, steps, initial_ticks=0)
