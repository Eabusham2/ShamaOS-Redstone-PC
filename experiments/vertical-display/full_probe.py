import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

from shamaos.model import Vec3
from prototype import build_panel, make_steps

dest = Path('/tmp/shama-vertical-display')
origin = Vec3(330, -54, 1050)
path = dest / 'full-panel-case.json'
# The MCHPRS fixture runner uses nonnegative internal coordinates; translating
# every circuit block and pin equally keeps the real-world bounds below.
coord = lambda p: [p.x - 298, p.y + 64, p.z - 1018]
count = lamps = storage = 0
low = [10**9] * 3
high = [-10**9] * 3
chunks = set()
start = time.monotonic()
with path.open('w') as handle:
    handle.write('{"blocks":[')
    def emit(p):
        global count, lamps, storage
        point = [p.pos.x, p.pos.y, p.pos.z]
        for axis, value in enumerate(point):
            low[axis] = min(low[axis], value)
            high[axis] = max(high[axis], value)
        assert -60 <= p.pos.y <= 319, p
        chunks.add((p.pos.x // 16, p.pos.z // 16))
        properties = dict(p.block.properties)
        lamps += p.block.name == 'minecraft:redstone_lamp'
        storage += p.block.name == 'minecraft:repeater' and properties.get('locked') == 'true'
        if count:
            handle.write(',')
        handle.write(json.dumps({'pos': coord(p.pos), 'name': p.block.name, 'properties': properties}, separators=(',', ':')))
        count += 1
    _, pins, outputs = build_panel(origin, 320, 180, emit=emit)
    inputs = {name: coord(p) for name, p in pins.items()}
    steps = make_steps(320, 180, selected_rows=(0, 1, 2, 89, 90, 177, 178, 179), ticks=1024)
    handle.write(']')
    for key, value in {'inputs': inputs, 'outputs': {name: coord(p) for name, p in outputs.items()}, 'initial_ticks': 1024, 'steps': steps}.items():
        handle.write(',' + json.dumps(key) + ':')
        json.dump(value, handle, separators=(',', ':'))
    handle.write('}')
build_seconds = time.monotonic() - start
assert lamps == storage == len(outputs) == 57600
print(json.dumps({'stage': 'case_ready', 'blocks': count, 'bounds': {'min': low, 'max': high}, 'chunks': len(chunks), 'generation_seconds': build_seconds}), flush=True)
start = time.monotonic()
with path.open() as handle:
    result = subprocess.run([os.environ['SHAMA_MCHPRS_RUNNER']], stdin=handle, text=True, capture_output=True, timeout=600)
print(result.stdout[-4000:], result.stderr[-4000:], flush=True)
assert result.returncode == 0
runtime = json.loads(result.stdout)
runtime.pop('traces', None)
assert runtime['status'] == 'passed'
report = {
    'kind': 'temporary-vertical-320x180-monitor-prototype',
    'production': False,
    'pixel_pitch_x': 4, 'pixel_pitch_y': 2,
    'lamps': lamps, 'storage_repeaters': storage,
    'pixels_checked_after_each_step': len(outputs),
    'selected_rows': [0, 1, 2, 89, 90, 177, 178, 179],
    'unique_blocks': count, 'horizontal_chunks': len(chunks),
    'bounds': {'min': low, 'max': high},
    'generation_seconds': build_seconds, 'runtime_seconds': time.monotonic() - start,
    'prototype_sha256': hashlib.sha256((dest / 'prototype.py').read_bytes()).hexdigest(),
    'runtime_coordinate_translation': [-298, 64, -1018],
    'driver': 'Each selected row needs its 320 column values XORed with (physical_row % 2 == 0); active-high row input unlocks that row.',
    'runtime': runtime,
    'complete_machine_runtime_verified': False,
}
(dest / 'full-panel-runtime.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report), flush=True)
