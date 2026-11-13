import json
from pathlib import Path
from shamaos.model import Vec3
from test_mchprs_runtime import run_case
from prototype3 import build_panel, make_steps

results = []
for width, height in ((4, 4), (5, 5), (8, 180), (320, 4)):
    try:
        blocks, inputs, outputs = build_panel(Vec3(32, 8, 32), width, height)
        rows = None if height < 10 else (0, 1, 2, 89, 90, 177, 178, 179)
        runtime = run_case(blocks, inputs, outputs, make_steps(width, height, selected_rows=rows, ticks=1024), initial_ticks=1024)
        runtime.pop('traces', None)
        result = {'width': width, 'height': height, 'blocks': len(blocks), 'runtime': runtime}
    except Exception as error:
        result = {'width': width, 'height': height, 'error': str(error)[:3500]}
    results.append(result)
    print(json.dumps(result), flush=True)
    if 'error' in result:
        break
Path('/tmp/shama-vertical-display/pitch3-probe.json').write_text(json.dumps(results, indent=2) + '\n')
