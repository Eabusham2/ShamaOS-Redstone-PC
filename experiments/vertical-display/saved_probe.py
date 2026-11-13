import json
from pathlib import Path
from shamaos.generator import write_placement_stages
from shamaos.model import Placement, Vec3
from shamaos.worldio import NativeWorldReader
from test_mchprs_runtime import run_case
from prototype import build_panel, make_steps

directory = Path('/tmp/shama-vertical-display')
origin = Vec3(330, -54, 1050)
blocks, pins, outputs = build_panel(origin, 8, 180)
world = directory / 'saved-vertical-panel'
count, _, validation = write_placement_stages(world, [('vertical-display', blocks)],
                                             surface_y=-61, spawn=Vec3(64, -59, 64))
reader = NativeWorldReader(world)
reopened = [Placement(p.pos, reader.get_block(p.pos), p.component) for p in blocks]
assert reopened == blocks
assert reader.get_block(Vec3(origin.x, -61, origin.z)).name == 'minecraft:grass_block'
assert reader.get_block(Vec3(origin.x, -62, origin.z)).name == 'minecraft:dirt'
assert min(p.pos.y for p in reopened) == -60
assert max(p.pos.y for p in reopened) == 304
shift = lambda p: p.offset(dy=64)
runtime = run_case((Placement(shift(p.pos), p.block, p.component) for p in reopened),
                   {name: shift(p) for name, p in pins.items()},
                   {name: shift(p) for name, p in outputs.items()},
                   make_steps(8, 180, selected_rows=(0, 1, 2, 89, 90, 177, 178, 179), ticks=1024),
                   initial_ticks=1024)
runtime.pop('traces', None)
report = {'kind': 'saved-and-reopened-vertical-monitor', 'width': 8, 'height': 180,
          'blocks': count, 'min_y': -60, 'max_y': 304, 'surface_y': -61,
          'native_world_validation': validation, 'runtime': runtime}
(directory / 'saved-panel-runtime.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report), flush=True)
