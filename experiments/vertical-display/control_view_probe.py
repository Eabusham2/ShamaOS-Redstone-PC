"""Unvalidated correction candidate: keep every actuator and clear the view."""
import json
from pathlib import Path
import numpy as np
import controls_compact_view as controls
import sightlines as s
from shamaos.model import Vec3
import test_mchprs_runtime as engine

origin = Vec3(900, -56, -8)
blocks = list(controls.iter_controls(origin))
by_point = {}
for p in blocks:
    assert by_point.setdefault(p.pos, p.block) == p.block, (p.pos, by_point[p.pos], p.block)
s.blocks = {(p.x, p.y, p.z): state.name for p, state in by_point.items()}
s.full = {p for p, name in s.blocks.items() if name in ('minecraft:stone','minecraft:target','minecraft:redstone_lamp')}
report = {'blocks': len(blocks), 'unique_blocks': len(by_point),
          'chunks': len({(p.x//16,p.z//16) for p in by_point}), 'views': []}
for x, z in ((967.5, 60.5), (967.5, 61.5), (961.5, 60.5), (973.5, 60.5)):
    eye = np.array((x, -53.38, z))
    result = {'eye': eye.tolist(), 'hard': s.visibility(eye, s.full), 'conservative': s.visibility(eye, set(s.blocks))}
    report['views'].append(result)
    print(json.dumps(result), flush=True)
# The input names and expected input traces are unchanged. Only the eight
# literal column output endpoints have moved with their pulse circuitry.
engine.control_ports = controls.control_ports
engine.iter_controls = controls.iter_controls
shifted_origin = origin.offset(dy=64)
outputs, steps, selected = engine.compact_console_case(shifted_origin)
runtime = engine.run_case(controls.iter_controls(shifted_origin), {}, outputs, steps, initial_ticks=128, capture_trace=True)
minimum=2*engine.CLOCK_HALF_PERIOD+engine.TEMPLATES['DFF'].setup_ticks+engine.TEMPLATES['DFF'].hold_ticks
overlaps=[]
for index,(signals,_,width) in enumerate(selected):
    traces={name:trace[index*300:(index+1)*300] for name,trace in runtime['traces'].items()}
    assert all(engine.high_runs(trace)==([width] if name in signals else []) for name,trace in traces.items()),signals
    if len(signals)==2:
        first,second=sorted(signals)
        overlap=sum(a and b for a,b in zip(traces[first],traces[second]))
        assert overlap>=minimum,(signals,overlap)
        overlaps.append(overlap)
report['minimum_row_column_pulse_overlap']=min(overlaps)
report['required_row_column_pulse_overlap']=minimum
runtime.pop('traces', None)
report['runtime'] = runtime
Path('/tmp/shama-vertical-display/control-view-runtime.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report), flush=True)
