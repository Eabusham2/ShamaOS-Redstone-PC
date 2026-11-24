from bisect import bisect_left, bisect_right
from collections import Counter
import json
from pathlib import Path
import random

import pytest
from test_mchprs_runtime import run_case
from experiments.input_receiver_timing.passive_fixture import receiver


def transitions(signal, level=None):
    return [tick for tick in range(1,len(signal)) if signal[tick]!=signal[tick-1] and
            (level is None or signal[tick]==level)]


def audit_receiver(r, *, offsets=(0,)):
    # Recover measured phase from the actual production oscillator's startup,
    # then bind the final replay to the same sequence of physical clock edges.
    probe=run_case(r.blocks,r.inputs,{"oscillator":r.outputs["oscillator"]},
                   [{"drive":{"enable":True},"ticks":600}],initial_ticks=300,capture_trace=True)
    clock=transitions(probe["traces"]["oscillator"],True)
    assert len(clock)>=5 and all(b-a==70 for a,b in zip(clock,clock[1:]))
    first=clock[0]
    rng=random.Random(0x2d15)
    rows=r.words
    masks=(list(range(16)) if rows==4 else [0,(1<<rows)-1,*[1<<i for i in range(rows)]])
    masks=(masks+[rng.randrange(1<<rows) for _ in range(32)])*2
    samples=[(rng.getrandbits(1),rng.getrandbits(1),mask) for mask in masks]+[(0,0,0)]*3
    windows=[first+70*(6+i) for i in range(len(samples))]
    events={0:dict.fromkeys(r.inputs,False)|{"enable":True}}
    for index,(tick,(near,far,selected)) in enumerate(zip(windows,samples)):
        events[tick-20+offsets[index%len(offsets)]]={"near":bool(near),"far":bool(far)}|{
            f"s{i}":bool(selected & (1<<i)) for i in range(rows)}
    times=sorted(events)
    stop=windows[-1]+210
    steps=[{"drive":events[t],"ticks":(times[i+1] if i+1<len(times) else stop)-t} for i,t in enumerate(times)]
    out=run_case(r.blocks,r.inputs,r.outputs,steps,initial_ticks=300,capture_trace=True,timeout=90)
    tr=out["traces"]
    clk_names=["oscillator","root","near_C","far_C",*[f"sel{i}_C" for i in range(rows)],
               *[f"phase{i}" for i in range(rows)]]
    delays={}
    for name in clk_names:
        rises=transitions(tr[name],True)
        falls=transitions(tr[name],False)
        assert len(rises)>=len(samples)+6,name
        assert all(b-a==70 for a,b in zip(rises,rises[1:])),name
        assert all(f-e==35 for e,f in zip(rises,falls)),name
        delays[name]=rises[0]-first
    assert delays['oscillator']==0
    bits=(0,r.width-1)
    edges={name:transitions(values)for name,values in tr.items()}
    state=[[False,False]for _ in range(rows)]
    misses=[];unstable=[];apertures=[];timed=0;ready_margin=[];hmargin=[]
    for j,((near,far,selected),epoch) in enumerate(zip(samples[:-1],windows)):
        for name,expected in (("near",near),("far",far),*[(f"sel{i}",bool(selected&(1<<i)))for i in range(rows)]):
            e=epoch+delays[name+"_C"]
            assert all(v==bool(expected)for v in tr[name+"_D"][e-8:e+3]),(name,j)
            assert tr[name+"_Q"][e+7]==expected,(name,j)
        for i in range(rows):
            for n,(bit,val) in enumerate(zip(bits,(near,far))):
                bit_name=f"bit{i}_{bit}";lock_name=f"lock{i}_{bit}";data_name=f"data{i}_{bit}"
                lock=tr[lock_name];data=tr[data_name];q=tr[bit_name]
                # Constrain the interval relative to this row's real local
                # phase signal. Long clock distribution must not shift a
                # distant row into a different logical epoch.
                at=epoch+delays[f"phase{i}"]
                closures=[t for t in edges[lock_name][bisect_left(edges[lock_name],at+35):bisect_right(edges[lock_name],at+105)] if lock[t]]
                opening=[t for t in edges[lock_name][bisect_left(edges[lock_name],at+35):bisect_right(edges[lock_name],at+95)] if not lock[t]]
                check=at+102
                if selected&(1<<i):
                    state[i][n]=bool(val)
                    if len(opening)!=1 or len(closures)!=1 or not opening[0]<closures[0]:
                        misses.append((j,i,bit,"aperture",opening,closures));continue
                    start,end=opening[0],closures[0]
                    timed+=1;apertures.append(end-start)
                    if not all(x==bool(val)for x in data[start:end+2]):
                        unstable.append((j,i,bit,start,end))
                    pos=bisect_right(edges[data_name],end)
                    ready_margin.append(end-edges[data_name][pos-1] if pos else end)
                    hmargin.append(edges[data_name][pos]-end if pos<len(edges[data_name]) else stop-end)
                elif opening:
                    misses.append((j,i,bit,"unselected-open",opening))
                if q[check]!=state[i][n]:
                    misses.append((j,i,bit,"stored",q[check],state[i][n]))
    return {"engine":out["revision"],"period_rt":70,"word_bits":r.width,"physical_rows":rows,
            "sampled_payload_bits":bits,"epochs":len(samples)-1,"native_ticks_including_settle":out["ticks"],
            "placement_operations":len(r.blocks),"unique_placed_blocks":len({b.pos for b in r.blocks}),
            "source_to_pins_rt":delays,"monitored_selected_cell_writes":timed,
            "minimum_aperture_rt":min(apertures),"minimum_data_setup_at_lock_rt":min(ready_margin),
            "minimum_data_hold_at_lock_rt":min(hmargin),"storage_or_phase_errors":misses,
            "data_changes_while_transparent":unstable}


@pytest.mark.parametrize("words,width",[(4,8),(4,32)])
def test_four_rows_with_real_oscillator_and_registered_selections(tmp_path,words,width):
    report=audit_receiver(receiver(width,words))
    (tmp_path/"receipt.json").write_text(json.dumps(report,indent=2)+"\n")
    assert not report["storage_or_phase_errors"],report["storage_or_phase_errors"][:5]
    assert not report["data_changes_while_transparent"],report["data_changes_while_transparent"][:5]


def test_delayed_write_qualification_corrupts_real_far_cells(tmp_path):
    report=audit_receiver(receiver(32,4,guard_extra=6))
    (tmp_path/"receipt.json").write_text(json.dumps(report,indent=2)+"\n")
    assert report["data_changes_while_transparent"]
    assert any(error[3]=="stored" for error in report["storage_or_phase_errors"])
