"""Independently check restored streams, persisted actuator levels, 128 bits and local native traces."""
from bisect import bisect_left,bisect_right
from collections import defaultdict
from itertools import groupby
from pathlib import Path
import hashlib,json,sys
from restore_continuations import digest

def read(path):
    return json.loads(path.read_bytes())

def bitmap(stream,coordinates):
    look=defaultdict(dict)
    for label,(x,y,z) in coordinates.items():
        look[(x//16,z//16,y//16)][(x%16)+16*(z%16)+256*(y%16)]=label
    result={}
    with stream.open('rb') as f:
        for line in f:
            value=json.loads(line)
            if value['kind'] not in ('section','uniform_section'):continue
            key=(*value['chunk'],value['y'])
            for i,label in look.get(key,{}).items():
                if value['kind']=='uniform_section':block=value['state']
                else:
                    b=value['bits'];n=64//b
                    block=value['palette'][(value['data'][i//n]>>((i%n)*b))&((1<<b)-1)]
                result[label]=block
    assert set(result)==set(coordinates)
    return result

def runs(w):
    end=0;ans=[]
    for state,g in groupby(w):
        n=sum(1 for x in g)
        if state:ans.append((end+1,end+n))
        end+=n
    return ans

def apertures(c,d,first):
    t=[i for i in range(1,len(d)) if d[i]!=d[i-1]]
    o=[]
    for i in range(first,len(c)-2):
        if c[i] and not c[i-1]:
            l,r=bisect_right(t,i),bisect_left(t,i)
            o.append([i+1,i-(t[l-1] if l else 0),(t[r] if r<len(t) else len(d))-i])
    assert o
    return min(s for _,s,h in o),min(h for _,s,h in o),len(o),[v for v in o if v[1]<8 or v[2]<2]

def verify(root,ref):
    p=root/'lever/persistence';ready=[5345,-45,448]
    shell=('clock_hold','divided_clock_active','reset_pulse_request','reset_pulse_received')
    original=read(ref/'retimed/receiver-idle-graded.json')['receiver_suite']['idle']['leaves']
    cases=(
       (root/'static/static-stale-ack.json',root/'static/static-stale-ack-result.json',ref/'retimed/persistence/reopened.jsonl'),
       (p/'off.json',p/'off-result.json',root/'lever/world.jsonl'),
       (p/'reopen.json',p/'reopen-result.json',p/'reopened.jsonl'))
    for case,result,stream in cases:
        c,w=read(case),read(result)
        assert w['status']=='passed' and w['scenario_sha256']==digest(case) and w['imported_stream_sha256']==digest(stream)
        assert w['engine_revision']=='f9e10d2572116bf5dcb9a36a305f28e40641a61a'
        assert w['ticks']==sum(x['ticks'] for x in c['steps']) and w['assertions']==sum(len(x.get('expect',{})) for x in c['steps'])
    old=read(root/'static/static-stale-ack-result.json');tr=old['traces'];st=read(root/'static/static-stale-ack-graded.json')
    assert old['pending_ticks_after_scenario']==0 and st['verified'] and not st['problems']
    assert all(tr['shutdown_ready_input']) and all(tr['core_gate_input']) and all(tr['reset_Y'])
    assert len(runs(tr['receiver_clock_hold']))==1 and all(b-a+1==35 for a,b in runs(tr['receiver_core_clk'])) and len(runs(tr['receiver_core_clk']))==32
    for reg in shell+tuple(original):
        scope=('timing_' if reg in shell else 'receiver_')+reg+'_'
        s,h,n,low=apertures(tr[scope+'C'],tr[scope+'D'],2060)
        expected=st['all_ten_registers'][reg]
        assert (s,h,n,low)==(expected['minimum_setup'],expected['minimum_hold'],expected['edges'],expected['violations'])
        assert not low
    geo=read(root/'lever/actuator-geometry.json')
    assert geo['records_compared']==109719 and geo['reference_stream_sha256']==digest(ref/'retimed/world.jsonl')
    assert geo['actuator_stream_sha256']==digest(root/'lever/world.jsonl')
    assert {tuple(v['position']) for v in geo['additions']}=={tuple(ready),(5345,-46,448)}
    off=read(p/'off-result.json');assert off['pending_ticks_after_scenario']==0
    assert off['snapshot']['sha256']==digest(p/'off-snapshot.jsonl') and off['snapshot']['status']=='quiescent-snapshot-written'
    resto=read(p/'restoration.json');assert resto['status']=='quiescent-snapshot-native-readback-passed'
    assert resto['pending_scheduled_ticks']==0 and resto['snapshot_sha256']==off['snapshot']['sha256']
    assert all(resto[k]==off['snapshot'][k] for k in ('chunks','sections','comparators','nonair'))
    trial=read(p/'reopen.json');result=read(p/'reopen-result.json')
    summary=read(p/'summary.json');wave=result['traces']
    assert set(trial['inputs'])=={'clk','power_switch'} and result['ticks']==19800
    for filename,expected_toggles,initial in [('off',1,False),('reopen',3,True)]:
        scene=read(p/(filename+'.json'));result0=read(p/(filename+'-result.json'))
        expected=initial;time=0;toggles=0
        for step in scene['steps']:
            for pos in step.get('use_blocks',[]):
                assert pos==ready
                toggles+=1;expected=not expected
            values=result0['traces']['ack_lever_contact'][time:time+step['ticks']]
            assert all(v==expected for v in values)
            time+=step['ticks']
        assert toggles==expected_toggles and time==result0['ticks']
    pins=read(root/'lever/probes.json')
    coords={k:v for k,v in pins.items() if k.startswith('stored_')}
    coords['ack']=ready;coords['floor']=[5345,-46,448]
    observed=bitmap(p/'reopened.jsonl',coords);words=[0]*4
    for r in range(4):
        for b in range(32):
            q=observed[f'stored_{r}_{b}'];assert q['name']=='minecraft:repeater'
            words[r]|=(q['properties']['powered']=='true')<<b
    assert words==[0,0x12345678,0x11223344,0xABCDEF01]==summary['modified_words_reopened_from_anvil']
    assert observed['ack']=={'name':'minecraft:lever','properties':{'face':'floor','facing':'north','powered':'true'}}
    assert observed['floor']['name']=='minecraft:stone'
    core=runs(wave['core_clk_Y'])
    assert core and all(b-a+1==35 and all(wave['reset_Y'][a-1:b]) for a,b in core)
    assert runs(wave['gate_shell_raw_clock_path_Y']) and not any(wave['gate_shell_divided_clock_path_Y'])
    assert not any(wave['row_write']) and not any(wave['pending_Q'])
    intervals=[]
    for m in summary['on_off_checkpoints']:
        n=m['tick']-1
        assert m['expected']==m['actual']==[wave['clock_hold_Q'][n],wave['reset_Y'][n]]
    for r in shell+tuple(original):
        scope=('timing_' if r in shell else 'receiver_')+r+'_'
        s,h,n,bad=apertures(wave[scope+'C'],wave[scope+'D'],2060)
        actual=summary['local_margins'][r]
        assert (s,h,n,bad)==(actual['setup'],actual['hold'],actual['edges'],actual['below'])
        assert not bad
        if r not in original:continue
        edges=runs(wave[scope+'C'])
        assert len(edges)==len(core) and all(b-a+1==35 for a,b in edges)
        assert not any(wave[scope+'Q'])
        assert all(all(all(wave[label][a-1:b]) for label in original[r]['reset_qualifiers']) for a,b in edges)
    print('Verified static retained-ACK and repeated real-lever restart, restored 128 bits, all checkpoints and 10 local register timing apertures.')

if __name__=='__main__':verify(Path(sys.argv[1]),Path(sys.argv[2]))
