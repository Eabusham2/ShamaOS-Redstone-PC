"""Read-only guard reproduction; every fabricated record is explicitly synthetic."""
import json,sys
from pathlib import Path
from unittest.mock import patch

checkout=Path(sys.argv[1]).resolve();out=Path(sys.argv[2]).resolve()
sys.path[:0]=[str(checkout/'src'),str(checkout)]
from experiments.memory_controller_runtime import run_connected as helper
assert helper._sha(Path(helper.__file__))=='faccd37e13eab70a7eb8dd577501073b7f283e88ab10d081cc914f28100309d2'
out.mkdir(parents=True,exist_ok=False)
prepared_dir=out/'synthetic-prepared';prepared_dir.mkdir();(prepared_dir/'prepared.json').write_text('{}\n')
record_path=out/'synthetic-placeholder';record_path.write_text('guard test only; no accepted native evidence\n')
record=helper._record(record_path)
prepared={'stream':record,'runtime_helper_sources':helper._helper_sources()}
class WorkReached(Exception):pass
outcomes=[]
for label,cases in [('missing-both-cases',{}),('missing-negative-case',{'safe':{'stdout':record,'scenario':record,'half_period_ticks':8192,'clock_gate_passed':True}}),('requested-period-not-probed',{'safe':{'stdout':record,'scenario':record,'half_period_ticks':8192,'clock_gate_passed':True},'too-fast':{'stdout':record,'scenario':record,'half_period_ticks':1,'clock_gate_passed':False}})]:
 proof={'status':helper.CLOCKS_PASSED,'native_clock_gate_verified':True,'world_stream':record,'prepared':helper._record(prepared_dir/'prepared.json'),'runner':record,'cases':cases,'runtime_helper_sources':prepared['runtime_helper_sources']}
 path=out/(label+'.json');path.write_text(json.dumps(proof))
 with patch.object(helper,'_load',return_value=(prepared,{}, {}, {}, {})),patch.object(helper.scenarios,'functional_frames',side_effect=WorkReached('invalid clock gate reached functional work')):
  try:helper.transactions(prepared_dir,record_path,path,out/(label+'-attempt'),half_period=2,initial_ticks=1)
  except WorkReached:outcomes.append({'case':label,'accepted_by_current_clock_gate':True,'native_execution_run':False})
  except Exception as error:outcomes.append({'case':label,'accepted_by_current_clock_gate':False,'error_type':type(error).__name__,'error':str(error),'native_execution_run':False})
assert all(not result['accepted_by_current_clock_gate'] and result['error_type']=='ValueError' for result in outcomes)
assert 'both exact safe and too-fast' in outcomes[0]['error']
assert 'both exact safe and too-fast' in outcomes[1]['error']
assert 'half-period must equal' in outcomes[2]['error']
report={'scope':'Synthetic rejection-guard reproduction only; no physical runtime or source-world evidence is asserted','source':helper._record(Path(helper.__file__)),'harness':helper._record(Path(__file__)),'results':outcomes}
(out/'review.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
