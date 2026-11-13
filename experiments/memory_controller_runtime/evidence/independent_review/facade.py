"""Re-audit frozen complete facade metadata without emitting its large geometry."""
from copy import deepcopy
import gzip,hashlib,json,resource,sys,time
from pathlib import Path
checkout=Path(sys.argv[1]).resolve();output=Path(sys.argv[2]).resolve();sys.path[:0]=[str(checkout/'src'),str(checkout)]
from experiments.memory_controller_runtime import fixture,save_connected
from experiments.partition_placement.port_egress import audit_egress
from experiments.partition_placement.port_egress_manifest import read_egress_manifest
from shamaos.hardware.partition_placement import audit_placement,balance_partition_clocks
from shamaos.hardware.partition_planner import audit_plan
from shamaos.model import Vec3
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
base=checkout/'build/cache-native-frozen/accepted-facade';receipt=json.loads((base/'copy-receipt.json').read_text())
for name,digest in receipt['files'].items():assert sha(base/name)==digest,name
for name,digest in receipt['generator_source_hashes'].items():assert sha(checkout/name)==digest,name
started=time.monotonic();graph,_,fabric,_=fixture.graph_and_fabric();plan_path=base/'inputs/plan.json';plan=json.loads(plan_path.read_text())
report,record,original_manifest=save_connected._accepted_facade(base/'report.json',plan_path,graph)
assert sha(base/'report-balanced.json.gz')==record['sha256']==sha(original_manifest)
placed=read_egress_manifest(base/'report-balanced.json.gz',record['sha256'],graph,plan)
planned=audit_plan(graph,plan);structural=audit_placement(graph,plan,placed);facade=audit_egress(graph,placed)
modules=placed.all_modules();before={key:(dict(tile.overrides),deepcopy(tile.clock_delays),deepcopy(tile._balanced_positions)) for key,tile in modules.items()}
matching=balance_partition_clocks(plan,placed)
assert matching=={int(k):v for k,v in report['clock_matching'].items()}
assert all((tile.overrides,tile.clock_delays,tile._balanced_positions)==before[key] for key,tile in modules.items())
assert len(placed.exposed_ports)==183==len(set(placed.exposed_ports.values()))
assert all(p.z==placed.bounds.max.z for p in placed.exposed_ports.values())
assert (structural['native_cells'],structural['clock_leaf_uses'],structural['original_port_bits'])==(1144,115,183)
assert (len(modules),len(placed.all_facade_modules()),len(placed.facade_contacts))==(33,5,494)
assert (fabric.total_bytes,fabric.bank_count,fabric.bank.words,fabric.bank.word_bits)==(8192,2,1024,32)
assert report['runtime_verified'] is False and report['whole_computer_verified'] is False
union=report['literal_union']
assert union['literal_unique_blocks']==report['computed_unique_blocks']==placed.unique_blocks
assert union['bounds']==report['bounds']
assert union['emitted_blocks']-union['literal_unique_blocks']==union['identical_duplicate_blocks']
for name,digest in receipt['generator_source_hashes'].items():assert sha(checkout/name)==digest,name
summary={'status':'passed','scope':'Independent frozen-input/source/complete graph/facade inventory and exact saved clock matching; large literal union is retained reported evidence and is not re-emitted here',
 'report':{'path':str(base/'report.json'),'sha256':sha(base/'report.json')},'manifest':{'path':str(base/'report-balanced.json.gz'),'sha256':sha(base/'report-balanced.json.gz')},'harness':{'path':str(Path(__file__).resolve()),'sha256':sha(__file__)},
 'frozen_files_verified':len(receipt['files']),'generator_sources_verified':len(receipt['generator_source_hashes']), 'complete_graph_audit':structural,'facade_audit':facade,'matching':matching,'saved_clock_overrides_unchanged':True,
 'retained_geometry_counts':{key:union[key] for key in ('emitted_blocks','literal_unique_blocks','identical_duplicate_blocks','occupied_chunks','bounds','sorted_unique_record_sha256')},
 'seconds':time.monotonic()-started,'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
 'large_literal_union_independently_reemitted':False,'external_clock_runtime_verified':False,'connected_cache_runtime_verified':False,'complete_machine_runtime_verified':False}
output.write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps({k:summary[k] for k in ('status','seconds','peak_rss_kib','generator_sources_verified','complete_graph_audit','matching')},indent=2))
