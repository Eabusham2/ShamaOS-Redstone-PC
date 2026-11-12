"""Actual selected-bank read data through the shared global return fabric."""
from shamaos.hardware.external_router import ExternalNet, iter_external_router
from shamaos.hardware.memory import MemoryBankSpec
from shamaos.hardware.memory_backbone import StackedMemoryFabricSpec, fabric_tile_ports, iter_stacked_memory_tile, stacked_tile_bounds
from shamaos.model import Vec3
from test_mchprs_runtime import run_case, west_driver


def test_actual_three_tile_global_return_bank_selection_and_no_backfeed():
    bank = MemoryBankSpec(words=2, word_bits=8)
    fabric = StackedMemoryFabricSpec(9 * bank.bytes, bank, 3, layers=3)
    origin = Vec3(512, 64, 6000)
    tiles = fabric_tile_ports(origin, fabric)
    values = [[(index * 29 + row * 91 + 0x35) & 255 for row in range(2)] for index in range(9)]
    image = bytes(value for words in values for value in words)
    blocks = []
    inputs = {}
    outputs = {}
    for group, pins in enumerate(tiles):
        tile = fabric.tile_spec(group)
        base = fabric.group_origin(origin, group)
        blocks.extend(iter_stacked_memory_tile(base, tile=tile, initial=image[group * tile.bytes:(group + 1) * tile.bytes]))
        inputs |= {f"D{group}:{bit}": west_driver(point) for bit, point in enumerate(pins.write_data)}
        inputs |= {f"R{group}:{row}": west_driver(point) for row, point in enumerate(pins.row_select)}
        inputs |= {f"B{group * fabric.layers + layer}": west_driver(point) for layer, point in enumerate(pins.bank_select)}
        inputs |= {f"READ{group}": west_driver(pins.read_enable), f"WRITE{group}": west_driver(pins.write_enable)}
        outputs |= {f"T{group}:{bit}": point for bit, point in enumerate(pins.selected_word)}
    outputs |= {f"S{index}:{row}:{bit}": fabric.bank_origin(origin, index).offset(bit * bank.bit_pitch + 2, bank.cell_y, 3 + row * bank.word_pitch)
                for index in range(9) for row in range(2) for bit in range(8)}
    central = tuple(Vec3(32 + bit * 8, 64, 5500) for bit in range(8))
    outputs |= {f"OUT{bit}": point for bit, point in enumerate(central)}
    nets = tuple(ExternalNet(f"return-{bit}", central[bit], tuple(tile.selected_word[bit] for tile in tiles), wired_or=True) for bit in range(8))
    access = {point: ("north",) for point in central}
    access |= {point: ("south",) for tile in tiles for point in tile.selected_word}
    boxes = tuple(stacked_tile_bounds(fabric.group_origin(origin, group), fabric.tile_spec(group)) for group in range(fabric.group_count))
    endpoint_bounds = {point: box for pins, box in zip(tiles, boxes) for point in pins.selected_word}
    routes = list(iter_external_router(nets, obstacles=blocks, access=access, minimum_y=0,
                                      endpoint_bounds=endpoint_bounds, obstacle_bounds=boxes))

    def stored():
        return {f"S{index}:{row}:{bit}": bool(value >> bit & 1)
                for index, words in enumerate(values) for row, value in enumerate(words) for bit in range(8)}

    def selected(index, value):
        return ({f"OUT{bit}": bool(value >> bit & 1) for bit in range(8)} |
                {f"T{group}:{bit}": bool(value >> bit & 1) if group == index // fabric.layers else False
                 for group in range(3) for bit in range(8)})

    def controls(index, row, active):
        return {f"B{index}": active} | {f"R{group}:{row}": active for group in range(3)} | {f"READ{group}": active for group in range(3)}

    steps = [{"ticks": 2048, "expect": stored() | selected(0, 0)}]
    for index in range(9):
        row = index % 2
        steps.extend([
            {"drive": controls(index, row, True), "ticks": 2048, "expect": stored() | selected(index, values[index][row])},
            {"drive": controls(index, row, False), "ticks": 2048, "expect": stored() | selected(index, 0)},
        ])
    for index, value in ((0, 0), (4, 0xFF), (8, 0xA6)):
        steps.append({"drive": {f"D{group}:{bit}": bool(value >> bit & 1) for group in range(3) for bit in range(8)},
                      "ticks": 2048, "expect": stored(), "continuous": True})
        values[index][1] = value
        write = {f"B{index}": True} | {f"R{group}:1": True for group in range(3)} | {f"WRITE{group}": True for group in range(3)}
        steps.extend([
            {"drive": write, "ticks": 2048, "expect": stored()},
            {"drive": {f"WRITE{group}": False for group in range(3)} | {f"READ{group}": True for group in range(3)},
             "ticks": 2048, "expect": stored() | selected(index, value)},
            {"drive": controls(index, 1, False), "ticks": 2048, "expect": stored() | selected(index, 0)},
        ])
    run_case(blocks + routes, inputs, outputs, steps, initial_ticks=512)
