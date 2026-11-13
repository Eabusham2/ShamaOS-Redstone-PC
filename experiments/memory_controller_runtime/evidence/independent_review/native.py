from copy import deepcopy
import hashlib,json,sys
from pathlib import Path

checkout=Path(sys.argv[1]).resolve()
out=Path(sys.argv[2]).resolve()
sys.path[:0]=[str(checkout/'src'),str(checkout)]
from experiments.memory_controller_runtime import run_connected as helper
from shamaos.mchprs_snapshot import restore_snapshot
from shamaos.mchprs_import import export_world
from shamaos.worldio import NativeWorldReader
from shamaos.model import Vec3

out.mkdir(parents=True,exist_ok=False)
source=helper._record(Path(helper.__file__))
assert source['sha256']=='faccd37e13eab70a7eb8dd577501073b7f283e88ab10d081cc914f28100309d2'
root=Path('/workspace/scratch/fb0eaf1c89fe')
record=json.loads((root/'ShamaOS-bounded/experiments/memory_controller_runtime/evidence/connected-cache-runtime-wrapper-review.json').read_text())
for name in ('runner','stream','scenario'):helper._same(record[name])
# The original helper has been superseded; its historical digest remains in the retained baseline evidence.
assert record['source']['sha256']=='c985ff3b3ced489f22e7fbd745df1ed702d47f7a5099470c21d6db6bc7a5f7d9'
helpers=helper._helper_sources()
helper._check_helpers(helpers)
for name in ('stdout','stderr','snapshot_file'):helper._same(record['process'][name])
runner,stream,scenario=(Path(record[name]['path']) for name in ('runner','stream','scenario'))
scenario_doc=json.loads(scenario.read_text())
with stream.open() as f:
 header=json.loads(next(f))
 for line in f: footer=json.loads(line)
assert header['kind']=='header' and footer['kind']=='complete'
imported={**{k:v for k,v in footer.items() if k!='kind'},'source':header['source'],'stream_sha256':helper._sha(stream)}
steps=len(scenario_doc['steps']);ticks=scenario_doc['initial_ticks']+sum(s['ticks'] for s in scenario_doc['steps']);assertions=sum(len(s.get('expect',{})) for s in scenario_doc['steps'])
result=helper.execute(runner,stream,scenario,out,'memory',imported['chunks']+128,memory_limit_mib=512,wall_seconds=60,snapshot=True)
assert result['exit_code']==0,result
actual=json.loads(Path(result['stdout']['path']).read_text())
helper._validate_result(actual,imported,helper._record(scenario),steps,ticks,assertions)
baseline=json.loads(Path(record['process']['stdout']['path']).read_text())
def semantic(r):
 r=deepcopy(r)
 for key in ('import_seconds','initialization_seconds','runtime_seconds','peak_rss_kib'):r.pop(key)
 r['snapshot'].pop('seconds');r['snapshot'].pop('path')
 return r
assert semantic(actual)==semantic(baseline)
assert result['snapshot_file']['sha256']==record['process']['snapshot_file']['sha256']
rejected=[]
for field in ('loaded_chunks','decoded_nonair_blocks','initialized_nonair_blocks','comparator_entities','steps','ticks','assertions'):
 bad=deepcopy(actual);bad[field]+=1
 try:helper._validate_result(bad,imported,helper._record(scenario),steps,ticks,assertions)
 except ValueError:rejected.append(field)
 else:raise AssertionError('accepted changed '+field)
for field in ('imported_stream_sha256','scenario_sha256'):
 bad=deepcopy(actual);bad[field]='0'*64
 try:helper._validate_result(bad,imported,helper._record(scenario),steps,ticks,assertions)
 except ValueError:rejected.append(field)
 else:raise AssertionError('accepted changed '+field)
restored=restore_snapshot(Path(result['snapshot_file']['path']),Path(imported['source']['native_world']),out/'reopened-world',expected_sha256=result['snapshot_file']['sha256'])
assert restored['pending_scheduled_ticks']==0
assert restored['parent_imported_stream_sha256']==imported['stream_sha256']
assert restored['parent_scenario_sha256']==helper._sha(scenario)
reader=NativeWorldReader(out/'reopened-world',max_cached_chunks=1)
expected=scenario_doc['steps'][-1]['expect']
stored=0
for name,point in scenario_doc['outputs'].items():
 if not name.startswith('S'):continue
 state=reader.get_block(Vec3(*point));p=dict(state.properties)
 assert state.name=='minecraft:repeater' and p['locked']=='true' and p['powered']==str(expected[name]).lower(),name
 stored+=1
assert stored==192
reopened_import=export_world(out/'reopened-world',out/'reopened-world.jsonl')
continuation={'inputs':{},'outputs':scenario_doc['outputs'],'initial_ticks':256,'steps':[{'ticks':256,'expect':expected,'continuous':True}],'capture_trace':False}
next_scenario=out/'continuation.json';next_scenario.write_text(json.dumps(continuation)+'\n')
next_process=helper.execute(runner,out/'reopened-world.jsonl',next_scenario,out,'continuation',reopened_import['chunks']+128,memory_limit_mib=512,wall_seconds=60,snapshot=True)
assert next_process['exit_code']==0,next_process
next_actual=json.loads(Path(next_process['stdout']['path']).read_text())
helper._validate_result(next_actual,reopened_import,helper._record(next_scenario),1,512,len(expected))
helper._same(source)
helper._check_helpers(helpers)
proof={'status':'passed','scope':'Independent exact-wrapper small native memory comparison, complete small-fixture snapshot readback and fresh-process observation; not full cache acceptance',
 'source':source,'runtime_helper_sources':helpers,'historical_wrapper_source':record['source'],'harness':helper._record(Path(__file__)),'runner':helper._record(runner),'stream':helper._record(stream),'scenario':helper._record(scenario),
 'all_semantic_fields_and_traces_equal':True,'ignored_fields':['import_seconds','initialization_seconds','runtime_seconds','peak_rss_kib','snapshot.seconds','snapshot.path'],
 'trace_samples_compared':sum(map(len,actual['traces'].values())),'declared_assertions':assertions,'process':result,'snapshot_bytes_equal':True,'native_restore':restored,
 'stored_bits_read_before_fresh_engine':stored,'fresh_process_continuation':next_process,'fresh_process_assertions':len(expected),'mutations_rejected':rejected,
 'connected_cache_runtime_verified':False,'complete_machine_runtime_verified':False}
(out/'review.json').write_text(json.dumps(proof,indent=2)+'\n')
print(json.dumps({'status':proof['status'],'traces':proof['trace_samples_compared'],'declared_assertions':assertions,'mutations_rejected':rejected,'peak_rss_kib':max(result['peak_rss_kib'],next_process['peak_rss_kib']),'snapshot_identical':True,'stored_bits_before_fresh_engine':stored},indent=2))
