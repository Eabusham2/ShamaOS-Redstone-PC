"""Exact voxel ray checks for temporary monitor/real console geometry.

Treating every occupied cell as an opaque cube is a conservative visual bound:
it overestimates the area of dust, buttons, torches and repeaters. Full solids
provide a separate hard-occlusion count. This is not a Minecraft screenshot.

Rescue revision: derive the exit bounds from the actual supplied blocks.
The original fixed bounds were too small for the proposed moved pulse circuits.
"""
from collections import Counter
import json
import math
from pathlib import Path
import numpy as np

from shamaos.hardware.controls import iter_controls
from shamaos.model import Vec3

blocks = {tuple((p.pos.x, p.pos.y, p.pos.z)): p.block.name for p in iter_controls(Vec3(900, -56, -8))}
full = {p for p, name in blocks.items() if name in ('minecraft:stone', 'minecraft:target', 'minecraft:redstone_lamp') or name.endswith('_concrete')}
encode = lambda x, y, z: (x.astype(np.int64) << 32) | ((y.astype(np.int64) + 512) << 22) | z.astype(np.int64)
endpoints = np.array([(489 + 3*col + 0.5, -54 + 2*(179-row) + 0.5, 512.0)
                      for row in range(180) for col in range(320)])

def visibility(eye, occupied):
    pts = np.array(list(occupied), dtype=np.int64)
    lower, upper = pts.min(axis=0), pts.max(axis=0)
    keys = np.sort(encode(pts[:, 0], pts[:, 1], pts[:, 2]))
    directions = endpoints - eye
    step = np.sign(directions).astype(np.int64)
    voxel = np.tile(np.floor(eye).astype(np.int64), (len(endpoints), 1))
    with np.errstate(divide='ignore', invalid='ignore'):
        delta = np.where(directions != 0, np.abs(1/directions), np.inf)
        boundary = np.where(step > 0, voxel + 1, voxel)
        until = np.where(directions != 0, (boundary - eye) / directions, np.inf)
    blocked = np.zeros(len(endpoints), dtype=bool)
    active = np.ones(len(endpoints), dtype=bool)
    obstacles = Counter()
    for _ in range(400):
        ids = np.flatnonzero(active)
        if not len(ids):
            break
        xyz = voxel[ids]
        q = encode(xyz[:, 0], xyz[:, 1], xyz[:, 2])
        index = np.searchsorted(keys, q)
        present = (index < len(keys)) & (keys[np.minimum(index, len(keys)-1)] == q)
        hits = ids[present]
        for p in voxel[hits]:
            obstacles[blocks[tuple(p)]] += 1
        blocked[hits] = True
        active[hits] = False
        ids = np.flatnonzero(active)
        axis = np.argmin(until[ids], axis=1)
        voxel[ids, axis] += step[ids, axis]
        until[ids, axis] += delta[ids, axis]
        active[ids[(voxel[ids, 2] > upper[2]) | (voxel[ids, 0] < lower[0]) | (voxel[ids, 0] > upper[0])
                   | (voxel[ids, 1] < lower[1]) | (voxel[ids, 1] > upper[1])]] = False
    assert not active.any()
    return {'visible_pixels': int((~blocked).sum()), 'blocked_pixels': int(blocked.sum()),
            'visible_fraction': float((~blocked).mean()), 'first_obstacles': dict(obstacles)}

if __name__ == '__main__':
    report = []
    for x, z in ((967.5, z) for z in (59.5, 60.5, 62.5, 64.5, 66.5, 68.5, 70.5, 72.5, 74.5, 80.5)):
        eye = np.array((x, -53.38, z))
        result = {'eye': eye.tolist(), 'assumed_standing_eye_height': 1.62,
                  'hard_full_blocks': visibility(eye, full),
                  'conservative_all_occupied_cells': visibility(eye, set(blocks))}
        report.append(result)
        print(json.dumps(result), flush=True)
    Path('/tmp/shama-vertical-display/sightlines.json').write_text(json.dumps(report, indent=2) + '\n')
