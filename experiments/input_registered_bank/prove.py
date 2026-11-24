"""Reproduce the registered 16x32 bank experiment using only saved actuators."""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from experiments.input_registered_bank.fixture import build
from experiments.input_storage_bus import full_width as cell
from experiments.input_storage_bus.capture import checked_stage, retain
from shamaos.mchprs_import import digest, export_world
from shamaos.mchprs_snapshot import restore_snapshot
from shamaos.release_io import file_inventory
from shamaos.worldio import NativeWorldReader, NativeWorldWriter

ROWS, BITS = 16, 32
MASK = (1 << BITS)-1


def events_and_words():
    rng = random.Random(0x1651232)
    words = [rng.getrandbits(32) for _ in range(ROWS)]
    events = []
    alternating = [MASK if row%2 else 0 for row in range(ROWS)]
    scans = [alternating, [w^MASK for w in alternating], words, [w^MASK for w in words]]
    for scan in scans:
        events.extend((1<<row, word) for row, word in enumerate(scan))
        events.extend((0, rng.getrandbits(32)) for _ in range(4))
    # Distinguish all 496 pairs of physical bit positions in both directions.
    for left in range(BITS):
        for right in range(left):
            if {(w>>left&1,w>>right&1) for mask,w in events if mask} != {(0,0),(0,1),(1,0),(1,1)}:
                raise ValueError("column pair not sensitized by all four input patterns")
    return events, scans[-1]


def scenario_steps(first, controls, events):
    epochs = [first+(6+i)*70 for i in range(len(events))]
    updates = {0:["enable"]}
    previous_word = 0
    previous_seed = False
    for epoch,(mask,word) in zip(epochs,events):
        switches = [f"word{b}" for b in range(BITS) if (word^previous_word)>>b&1]
        seed = mask == 1
        if seed != previous_seed: switches.append("seed")
        updates[epoch-20] = switches
        previous_seed,previous_word = seed,word
    stop = epochs[-1]+210
    updates[stop] = ["enable"]
    times = sorted(updates)
    steps=[]
    for i,t in enumerate(times):
        next_time = times[i+1] if i+1<len(times) else stop+550
        steps.append({"use_blocks":[controls[n] for n in updates[t]],"ticks":next_time-t})
    return epochs,steps


def check_launch(wave, first, epochs, events, bit):
    clock, data, q = (wave[f"launch{bit}_{pin}"] for pin in "CDQ")
    terminal = wave[f"terminal{bit}"]
    rises, falls = cell.changes(clock,True), cell.changes(clock,False)
    delta = rises[0]-first
    if rises != [t+delta for t in cell.changes(wave["oscillator"],True)] or falls != [t+35 for t in rises]:
        raise ValueError(f"launch clock {bit} lost an edge or altered its width")
    changed = []
    last = False
    for t,(_,word) in zip(epochs,events):
        value = bool(word>>bit&1)
        if any(x!=value for x in data[t+delta-8:t+delta+3]):
            raise ValueError(f"launch D {bit} outside its local setup/hold contract")
        if value!=last: changed.append(t)
        last = value
    q_edges, terminal_edges = cell.changes(q), cell.changes(terminal)
    if q_edges != [t+delta+7 for t in changed] or len(terminal_edges)!=len(changed):
        raise ValueError(f"launch {bit} skipped a transition or produced an extra transition")
    delays = {edge-t for edge,t in zip(terminal_edges,changed)}
    if len(delays)!=1 or terminal[0] or q[0] or terminal[-1]!=last or q[-1]!=last:
        raise ValueError(f"launch {bit} changed arrival or polarity")
    latency = delays.pop()
    if 0<bit<31 and not 34<=latency<=49:
        raise ValueError(f"launch {bit} is outside the measured bus-terminal window")
    return {"clock_rt":delta,"q_rt":delta+7,"terminal_rt":latency,"word_transitions":len(changed)}


def prove(directory,runner,*,guard_extra=0):
    directory.mkdir(parents=True,exist_ok=False)
    runner=runner.resolve()
    if digest(runner)!=cell.PINNED_RUNNER_SHA256:
        raise ValueError("pinned terrain runner required")
    paths=cell.source_paths()
    for name in ("input_registered_bank/fixture.py","input_registered_bank/prove.py",
                 "input_storage_bus/capture.py"):
        path=cell.ROOT/"experiments"/name
        paths[path.relative_to(cell.ROOT).as_posix()]=path
    paths["native-engine"]=runner
    fixed=cell.frozen(paths)
    retain(directory/"sources.json",{k:v[1] for k,v in fixed.items()})
    blocks,controls,ports=build(guard_extra=guard_extra)
    pins={k:cell.coord(v) for k,v in ports.items()}
    controls={k:cell.coord(v) for k,v in controls.items()}
    retain(directory/"probes.json",pins)
    retain(directory/"actuators.json",controls)
    with NativeWorldWriter(directory/"world",surface_y=-61,spawn_platform=False) as writer:
        writer.set_blocks(blocks)
        readback=writer.validate()
    if readback["status"]!="passed":
        raise ValueError("failed saved-world readback")
    imported=export_world(directory/"world",directory/"world.jsonl")
    retain(directory/"import.json",imported)
    source_world=file_inventory(directory/"world")
    def execute(name,names,steps,*,trace=True,snapshot=False):
        if cell.frozen(paths)!=fixed or digest(directory/"world.jsonl")!=imported["stream_sha256"]:
            raise ValueError("source or native execution input changed")
        case={"inputs":{},"outputs":{n:pins[n] for n in names},
              "initial_ticks":300,"capture_trace":trace,"steps":steps}
        result,_=checked_stage(directory,name,case,imported,runner,snapshot=snapshot)
        return result
    origin=execute("origin",["oscillator"],[{"use_blocks":[controls["enable"]],"ticks":650}])
    first=cell.changes(origin["traces"]["oscillator"],True)[0]
    events,final=events_and_words()
    epochs,steps=scenario_steps(first,controls,events)
    core=["oscillator","root","near_C","far_C",*[f"phase{r}" for r in range(ROWS)]]
    signals=[*core,*[f"sel{r}_{p}" for r in range(ROWS) for p in "CDQ"],
             *[f"guard{r}_{p}" for r in range(ROWS) for p in "AY"],*[f"write{r}" for r in range(ROWS)]]
    control=execute("controls",signals,steps)["traces"]
    strobes=cell.audit_strobes(control,first,epochs,events,ROWS)
    reports=[]
    launches={}
    for b in range(0,BITS,2):
        bits=(b,b+1)
        names=[*core,*[f"launch{k}_{p}" for k in bits for p in "CDQ"],*[f"terminal{k}" for k in bits],
               *[f"{pin}{r}_{k}" for r in range(ROWS) for k in bits for pin in ("bit","data","lock")]]
        wave=execute(f"lanes-{b:02}",names,steps)["traces"]
        if any(wave[n]!=control[n] for n in core):
            raise ValueError("fresh native imports disagree about oscillator or row qualifier")
        for k in bits:
            launches[k]=check_launch(wave,first,epochs,events,k)
        analysis=cell.analyze(control|wave,first,epochs,events,ROWS,bits)
        reports.append(analysis)
        retain(directory/f"capture-{b:02}.json",analysis)
        print(f"lanes {b:02}-{b+1:02} completed: {analysis['faults']}",flush=True)
    expected={f"bit{r}_{b}":bool(word>>b&1) for r,word in enumerate(final) for b in range(BITS)}
    cold=execute("cold",expected,steps+[{"ticks":1,"expect":expected}],trace=False,snapshot=True)
    restored=directory/"restored"
    receipt=restore_snapshot(directory/"cold-snapshot.jsonl",directory/"world",restored,
                             expected_sha256=cold["snapshot"]["sha256"])
    if receipt["parent_imported_stream_sha256"]!=imported["stream_sha256"] or receipt["parent_scenario_sha256"]!=cold["scenario_sha256"]:
        raise ValueError("restored world has a different parent")
    reader=NativeWorldReader(restored)
    for n,value in expected.items():
        state=reader.get_block(ports[n])
        if (state.name!="minecraft:repeater" or dict(state.properties).get("locked")!="true"
                or (dict(state.properties).get("powered")=="true")!=value):
            raise ValueError(f"restored native storage differs at {n}")
    fresh=directory/"fresh";fresh.mkdir()
    fresh_import=export_world(restored,fresh/"world.jsonl")
    retain(fresh/"import.json",fresh_import)
    final_case={"inputs":{},"outputs":{n:pins[n] for n in expected},"initial_ticks":300,"capture_trace":False,
                "steps":[{"ticks":700,"expect":expected}]}
    checked_stage(fresh,"readback",final_case,fresh_import,runner)
    if file_inventory(directory/"world")!=source_world or cell.frozen(paths)!=fixed:
        raise ValueError("original world, source or engine changed during proof")
    failures=sum(f["count"] for r in reports for f in r["faults"].values())
    minima={k:min(r["minimum_rt"][k] for r in reports) for k in ("setup","hold","aperture")}
    report={"scope":"32 externally set saved lever data inputs through native launch registers and a one-shot 16-row token",
            "guard_extra_rt":guard_extra,"assigned_blocks":len(blocks),"native_chunks":imported["chunks"],
            "actuators":len(controls),"host_drive_pads":0,"shared_oscillator_first_rise_rt":first,"launches":launches,
            "epochs":len(epochs),"scans":4,"row_control_strobe_edges_checked":strobes,"faults":failures,
            "measured":{k:sum(r["measured"][k] for r in reports) for k in reports[0]["measured"]},
            "minimum_rt":minima,"restored_locked_bits":len(expected),"fresh_process_assertions":len(expected),
            "receiver_passed":not failures and minima["hold"]>=2 and minima["aperture"]==35,
            "sparse_event_admission_verified":False,"classifier_fanout_verified":False,"cdc_verified":False,
            "complete_machine_runtime_verified":False}
    retain(directory/"report.json",report)
    return report


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output",type=Path)
    parser.add_argument("--runner",type=Path,required=True)
    parser.add_argument("--guard-extra",type=int,choices=(0,6),default=0)
    args=parser.parse_args()
    report=prove(args.output.resolve(),args.runner,guard_extra=args.guard_extra)
    print(json.dumps({k:v for k,v in report.items() if k!="launches"},indent=2))
    raise SystemExit(0 if report["receiver_passed"] else 1)
