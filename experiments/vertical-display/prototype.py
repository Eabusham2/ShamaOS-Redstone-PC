"""Temporary real-redstone vertical lamp panel; no production changes.

Logical pixels retain one latch/lamp each. Rows use two Y blocks, columns
four X blocks. Data rails invert on alternate physical rows, so the driver
must compensate column data before selecting that row.
"""
from shamaos.model import BlockState, Placement, Vec3
from shamaos.hardware.memory import SUPPORT, DUST, redstone_wire, repeater


def build_panel(origin, width, height, *, emit=None):
    cells, inputs, outputs = {}, {}, {}
    states = {}
    high = redstone_wire(power=15)
    black = BlockState.of('minecraft:black_concrete')
    latch_z = origin.z + 5
    row_z = latch_z + 6
    inverse_z = row_z + 5

    def put(point, block, component):
        if emit is None:
            previous = cells.setdefault(point, Placement(point, block, component))
            assert previous.block == block, (point, previous, block, component)
        else:
            assert 0 <= point.x < 2**22 and -512 <= point.y < 512 and 0 <= point.z < 2**22
            key = (point.x << 32) | ((point.y + 512) << 22) | point.z
            state = states.setdefault(block, len(states))
            previous = cells.setdefault(key, state)
            assert previous == state, (point, previous, block, component)
            if previous == state and key not in emitted:
                emitted.add(key)
                emit(Placement(point, block, component))

    emitted = set()

    def supported(point, block, component):
        put(point.offset(dy=-1), SUPPORT, component)
        put(point, block, component)

    # Two extra torch stages place the input/support above the real grass
    # while keeping all active pixel levels at an even two-block row pitch.
    for col in range(width):
        x = origin.x + col * 4
        base = Vec3(x, origin.y - 5, latch_z + 2)
        supported(base, BlockState.of('minecraft:target', power='0'), f'data{col}')
        for y in range(base.y + 1, origin.y + 2 * (height - 1) + 1):
            dy = y - base.y
            block = SUPPORT if dy % 2 == 0 else BlockState.of('minecraft:redstone_torch', lit=str(((dy - 1) // 2) % 2 == 0).lower())
            put(Vec3(x, y, base.z), block, f'data{col}')
        port = base.offset(dz=1)
        supported(port, DUST, f'data{col}')
        inputs[f'D{col}'] = port

    for row in range(height):
        physical_row = height - 1 - row
        y = origin.y + 2 * physical_row
        control_x = origin.x + 4 * (width - 1) + 4 + 2 * physical_row
        gate = Vec3(control_x, y, inverse_z)
        supported(gate, BlockState.of('minecraft:target', power='0'), f'row{row}')
        supported(gate.offset(dz=-1), BlockState.of('minecraft:redstone_wall_torch', facing='north', lit='true'), f'row{row}')
        port = gate.offset(dz=1)
        supported(port, DUST, f'row{row}')
        inputs[f'R{row}'] = port
        for z in range(row_z, inverse_z - 1):
            supported(Vec3(control_x, y, z), high, f'row{row}')
        for x in range(origin.x + 2, control_x + 1):
            refresh = x != control_x and (x - origin.x) % 8 == 0
            supported(Vec3(x, y, row_z), repeater('west', powered=True) if refresh else high, f'row{row}')

        for col in range(width):
            x = origin.x + 4 * col
            name = f'pixel{row}:{col}'
            supported(Vec3(x, y, latch_z + 1), repeater('north'), name)
            supported(Vec3(x, y, latch_z), repeater('north', locked=True), name)
            supported(Vec3(x + 1, y, latch_z), repeater('west', powered=True), name)
            for z in range(latch_z, row_z + 1):
                state = repeater('north', powered=True) if z == row_z - 1 else high
                supported(Vec3(x + 2, y, z), state, name)
            for z in range(origin.z + 1, latch_z):
                supported(Vec3(x, y, z), repeater('north') if z == origin.z + 1 else DUST, name)
            lamp = Vec3(x, y, origin.z)
            put(lamp, BlockState.of('minecraft:redstone_lamp', lit='false'), name)
            outputs[f'P{row}:{col}'] = lamp

    for dx in range(4 * (width - 1) + 1):
        for dy in range(2 * (height - 1) + 1):
            if dx % 4 or dy % 2:
                put(origin.offset(dx, dy, 0), black, 'face')
    return [] if emit is not None else list(cells.values()), inputs, outputs


def make_steps(width, height, *, selected_rows=None, ticks=512):
    expected = {f'P{row}:{col}':False for row in range(height) for col in range(width)}
    steps = [{'ticks':ticks, 'expect':expected.copy()}]
    selected_rows = range(height) if selected_rows is None else selected_rows
    for mode in range(3):
        for row in selected_rows:
            physical_row = height - 1 - row
            correction = physical_row % 2 == 0
            desired = [(col + row) % 2 == 0 if mode == 0 else mode == 1 for col in range(width)]
            data = {f'D{col}':value ^ correction for col, value in enumerate(desired)}
            steps.append({'drive':data, 'ticks':ticks, 'expect':expected.copy()})
            expected.update({f'P{row}:{col}':value for col, value in enumerate(desired)})
            steps.extend([
                {'drive':{f'R{row}':True}, 'ticks':ticks, 'expect':expected.copy()},
                {'drive':{f'R{row}':False}, 'ticks':ticks, 'expect':expected.copy()},
            ])
    return steps
