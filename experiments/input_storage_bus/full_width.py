"""Exercise all 16 x 32 storage pins of the passive input receiver in saved Anvil."""
from __future__ import annotations

import argparse
import gzip
import shutil
from bisect import bisect_left, bisect_right
from hashlib import sha256
import json
import os
from pathlib import Path
import random
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
from experiments.input_receiver_timing.passive_fixture import receiver, west_driver
from shamaos.hardware.logic import TEMPLATES
from shamaos.hardware.memory import MemoryBankSpec, bank_ports, repeater
from shamaos.mchprs_import import digest, export_world
from shamaos.mchprs_snapshot import ENGINE_REVISION, TERRAIN_PATCH, restore_snapshot
from shamaos.model import Vec3
from shamaos.worldio import NativeWorldReader, NativeWorldWriter, validate_native_world


PINNED_RUNNER_SHA256 = "c8032089622501cb57f9edbb0ea7e3be24e3a23fd0b2b80653b0ed4f07045610"
SEED = 0x1632512


def stamp(path):
    st = path.stat()
    return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns


def source_paths():
    files = [Path(__file__).resolve(), ROOT / "experiments/input_receiver_timing/passive_fixture.py"]
    for folder in ("src/shamaos", "tests/mchprs_sparse_runtime"):
        for path in (ROOT / folder).rglob("*"):
            if path.suffix in (".py", ".rs", ".toml", ".patch"):
                files.append(path)
    return {path.relative_to(ROOT).as_posix(): path for path in sorted(files)}


def frozen(paths):
    return {name: (stamp(path), digest(path)) for name, path in paths.items()}


def limit_native_memory():
    # Offline geometry and captured-waveform review also run on Windows.
    # A real native execution still requires this POSIX resource limiter.
    import resource

    # This harness is single-threaded. Splitting observations bounds the memory
    # required by Rust to serialize and print the JSON trace.
    _, hard = resource.getrlimit(resource.RLIMIT_AS)
    limit = 256 * 1024**2 if hard < 0 else min(hard, 256 * 1024**2)
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))


def write(path, value):
    with path.open("x") as f:
        json.dump(value, f, separators=(",", ":"))


def coord(p):
    return [p.x, p.y, p.z]


def changes(wave, level=None):
    return [i for i in range(1, len(wave)) if wave[i] != wave[i-1] and (level is None or wave[i] == level)]


def fixture(rows, extra):
    result = receiver(32, rows, guard_extra=extra)
    # Locate the unchanged native bank from the independently observed row-zero storage pin.
    origin = result.outputs["bit0_0"].offset(-2, -3, -3)
    spec = MemoryBankSpec(words=rows, word_bits=32, bit_pitch=5, word_pitch=16)
    ports = bank_ports(origin, spec)
    blocks = {b.pos: b.block for b in result.blocks}
    inputs = dict(result.inputs)
    outputs = dict(result.outputs)
    for row, strobe in enumerate(ports.write_select):
        b = TEMPLATES["WRITE_GUARD"].pins["B"]
        origin_gate = outputs[f"phase{row}"].offset(-b.x, -b.y, -b.z)
        for pin, relative in TEMPLATES["WRITE_GUARD"].pins.items():
            outputs[f"guard{row}_{pin}"] = origin_gate.offset(relative.x, relative.y, relative.z)
        outputs[f"write{row}"] = strobe
    for bit, terminal in enumerate(ports.data_in):
        outputs[f"terminal{bit}"] = terminal
        if 0 < bit < 31:
            inputs[f"bus{bit}"] = west_driver(terminal)
        for row in range(rows):
            q = origin.offset(2+bit*spec.bit_pitch, spec.cell_y, 3+row*spec.word_pitch)
            if blocks[q] != repeater("east", locked=True):
                raise ValueError(f"Missing the real locked storage cell at {q}")
            outputs[f"bit{row}_{bit}"] = q
            outputs[f"lock{row}_{bit}"] = q.offset(dz=-1)
            outputs[f"data{row}_{bit}"] = q.offset(dx=-1)
    pads = [p.offset(dz=1) for p in inputs.values()]
    if len(pads) != len(set(pads)) or any(p in blocks for p in pads):
        raise ValueError("Stimulus would overlap a saved physical component or another driver")
    return result.blocks, {n:coord(p) for n,p in inputs.items()}, {n:coord(p) for n,p in outputs.items()}


def vectors(rows, seed):
    # First force both edges on every cell, then make single-row replacement,
    # broadcast, simultaneous nonadjacent selection and complete inhibition visible.
    rng = random.Random(seed)
    mask = (1 << rows)-1
    words = [rng.getrandbits(32) for _ in range(rows)]
    events = [(0, 0), (mask, 0xffffffff), (mask, 0)]
    for i, word in enumerate(words):
        events.extend([(1 << i, word), (0, ~word & 0xffffffff)])
    events.extend((1 << i, ~words[i] & 0xffffffff) for i in reversed(range(rows)))
    for _ in range(20):
        events.append((rng.getrandbits(rows), rng.getrandbits(32)))
    final = [rng.getrandbits(32) for _ in range(rows)]
    events.extend((1 << i, word) for i, word in enumerate(final))
    events.extend([(0, 0), (0, 0xffffffff), (0, 0), (0, 0xffffffff)])
    return events, final


def run_native(runner, directory, name, scenario, imported, *, snapshot=False):
    case = directory/(name+".json")
    raw = directory/(name+"-result.json")
    write(case, scenario)
    command = [str(runner), str(directory/"world.jsonl"), str(case), str(imported["chunks"]+128)]
    snap = directory/(name+"-snapshot.jsonl")
    if snapshot:
        command.extend(["--snapshot",str(snap)])
    with raw.open("xb") as out, (directory/(name+"-stderr.log")).open("xb") as err:
        subprocess.run(command, stdout=out, stderr=err, check=True, timeout=150, preexec_fn=limit_native_memory,
                       env={**os.environ, "SHAMA_NATIVE_PROGRESS": "1"})
    result = json.loads(raw.read_bytes())
    expected_ticks=scenario["initial_ticks"]+sum(s["ticks"] for s in scenario["steps"])
    expected = {"kind":"streamed-native-anvil-sparse-mchprs-runtime",
                "source":imported["source"],"decoded_nonair_blocks":imported["nonair"],
                "comparator_entities":imported["comparators"],"engine_revision":ENGINE_REVISION,"engine_registry_patch_sha256":TERRAIN_PATCH,
                "imported_stream_sha256":imported["stream_sha256"],"scenario_sha256":digest(case),
                "ticks":expected_ticks,"loaded_chunks":imported["chunks"],"complete_machine_runtime_verified":False}
    checks=sum(len(step.get("expect",{})) for step in scenario["steps"])
    expected.update(status="passed" if checks else "executed-trace-only",assertions=checks)
    if any(result.get(k)!=v for k,v in expected.items()):
        raise ValueError("Native result does not match the physical scenario and stream")
    if snapshot and (result["snapshot"]["sha256"] != digest(snap) or result["pending_ticks_after_scenario"] != 0):
        raise ValueError("Missing actual quiescent snapshot")
    return result


def plan(first, events, inputs, rows, offset):
    epochs = [first+70*(6+i) for i in range(len(events))]
    schedule = {0:dict.fromkeys(inputs,False) | {"enable":True}}
    for epoch,(selected, word) in zip(epochs,events):
        schedule[epoch-20] = {"near":bool(word&1),"far":bool(word>>31&1)} | {
            f"s{row}":bool(selected>>row&1) for row in range(rows)}
        # These 30 columns receive a settled external word at the real bank
        # terminals; the two edge columns keep their physical clocked launch DFFs.
        schedule[epoch+offset] = {f"bus{b}":bool(word>>b&1) for b in range(1,31)}
    stop = epochs[-1]+210
    schedule[stop] = {"enable":False}
    times = sorted(schedule)
    steps = [{"drive":schedule[t],"ticks":(times[i+1] if i+1<len(times) else stop+550)-t}
             for i,t in enumerate(times)]
    return epochs,steps


def directed_word_case(first, inputs, ports, rows, offset):
    """Distinguish every pair of physical bit columns in both directions in each row."""
    patterns=[0,0xffffffff]
    for shift in range(5):
        word=sum(((b>>shift)&1)<<b for b in range(32))
        patterns.extend([word,word^0xffffffff])
    events=[]
    for word in patterns:
        events.extend([((1<<rows)-1,word),(0,word^0xffffffff),(0,word)])
    epochs, steps=plan(first,events,inputs,rows,offset)
    checks={epochs[3*i]+150:word for i,word in enumerate(patterns)}
    split=[];time=0
    for step in steps:
        end=time+step["ticks"]
        change=step.get("drive",{})
        for tick in sorted(t for t in checks if time<t<=end):
            word=checks[tick]
            split.append({"drive":change,"ticks":tick-time,
                          "expect":{f"bit{r}_{b}":bool(word>>b&1) for r in range(rows) for b in range(32)}})
            change={};time=tick
        if end>time:
            split.append({"drive":change,"ticks":end-time});time=end
    return {"inputs":inputs,"outputs":{n:p for n,p in ports.items() if n.startswith("bit")},
            "initial_ticks":300,"capture_trace":False,"steps":split}


def audit_strobes(wave, first, epochs, events, rows):
    visits=0
    for row in range(rows):
        phase=changes(wave[f"phase{row}"],True)[0]-first
        for name in (f"guard{row}_Y",f"write{row}"):
            rise,fall=changes(wave[name],True),changes(wave[name],False)
            for epoch,(mask,word) in zip(epochs,events):
                at=epoch+phase
                selected=bool(mask>>row&1)
                if wave[f"guard{row}_A"][at+25]!=selected:
                    raise ValueError(f"Row {row} has the wrong comparator qualifier")
                starts=rise[bisect_left(rise,at+30):bisect_left(rise,at+100)]
                ends=fall[bisect_left(fall,at+60):bisect_left(fall,at+130)]
                if len(starts)!=int(selected) or len(ends)!=int(selected) or selected and ends[0]-starts[0]!=35:
                    raise ValueError(f"Missing, unexpected or split high on {name} at epoch {epoch}")
                visits+=int(selected)
    return visits


def analyze(tr, first, epochs, events, rows, bits):
    edges = {k:changes(v) for k,v in tr.items()}
    clocks = ["oscillator","root","near_C","far_C",
              *[f"sel{r}_C" for r in range(rows)],*[f"phase{r}" for r in range(rows)]]
    delays = {}
    for name in clocks:
        rises, falls = changes(tr[name],True), changes(tr[name],False)
        if len(rises)!=len(falls) or len(rises)<len(events)+6 or any(
            b-a!=70 for a,b in zip(rises,rises[1:])) or any(b-a!=35 for a,b in zip(rises,falls)):
            raise ValueError(f"Unexpected native pulse width/count at {name}")
        delays[name] = rises[0]-first
    faults = {}
    def fault(kind, example):
        data=faults.setdefault(kind,{"count":0,"examples":[]})
        data["count"]+=1
        if len(data["examples"])<5:
            data["examples"].append(example)
    counts = dict(writes=0, retained=0, q_rises=0, q_falls=0)
    minimum = {"setup":None,"hold":None,"aperture":None}
    worst = {}
    phase_window = {bit: [None, None] for bit in bits if 0 < bit < 31}
    for row in range(rows):
        dff, phase = f"sel{row}", f"phase{row}"
        for epoch,(selected,word) in zip(epochs,events):
            t=epoch+delays[dff+"_C"]; sel=bool(selected>>row&1)
            if any(v!=sel for v in tr[dff+"_D"][t-8:t+3]) or tr[dff+"_Q"][t+7]!=sel:
                fault("row-select capture",[row,t])
        for bit in bits:
            q=f"bit{row}_{bit}"; data=f"data{row}_{bit}"; lock=f"lock{row}_{bit}"
            state=False
            openings,closures=changes(tr[lock],False),changes(tr[lock],True)
            allowed=[]
            for episode,(epoch,(selected,word)) in enumerate(zip(epochs,events)):
                at=epoch+delays[phase]; selected=bool(selected>>row&1); value=bool(word>>bit&1)
                starts=openings[bisect_left(openings,at+35):bisect_right(openings,at+95)]
                ends=closures[bisect_left(closures,at+35):bisect_right(closures,at+105)]
                key=[episode,row,bit]
                if selected:
                    counts["writes"]+=1
                    if len(starts)!=1 or len(ends)!=1 or not starts[0]<ends[0]:
                        fault("selection aperture",key)
                    else:
                        opened,closed=starts[0],ends[0]
                        allowed.append((opened,closed))
                        if any(v!=value for v in tr[data][opened:closed+2]):
                            fault("unstable/incorrect data during aperture",key)
                        index=bisect_right(edges[data],closed)
                        before=edges[data][index-1] if index else 0
                        after=edges[data][index] if index<len(edges[data]) else len(tr[q])
                        for label,margin in (("setup",closed-before),("hold",after-closed),("aperture",closed-opened)):
                            if minimum[label] is None or margin<minimum[label]:
                                minimum[label]=margin;worst[label]=key
                        if bit in phase_window:
                            # Translate the full D waveform relative to fixed physical
                            # lock edges. Require D stable one tick before opening
                            # and still stable one tick after closing (next edge >= +2).
                            cut = bisect_right(edges[data], opened)
                            limits = phase_window[bit]
                            if cut:
                                latest = opened - edges[data][cut - 1] - 1
                                limits[1] = latest if limits[1] is None else min(limits[1], latest)
                            if cut < len(edges[data]):
                                earliest = 2 - (edges[data][cut] - closed)
                                limits[0] = earliest if limits[0] is None else max(limits[0], earliest)
                        transitions_in_window = [t for t in edges[q] if opened <= t <= closed+1]
                        if len(transitions_in_window) != int(value != state) or tr[q][closed+1] != value:
                            fault("unexpected Q pulse count or close state",key)
                    state=value
                else:
                    counts["retained"]+=1
                    if starts or ends:
                        fault("unselected aperture",key)
                if tr[q][at+102]!=state:
                    fault("wrong retained word",key)
            # Every Q transition must follow a selected physical opening. An
            # unwanted pulse that happens to disappear before the endpoint fails.
            for edge in edges[q]:
                if not any(start<=edge<=end+1 for start,end in allowed):
                    fault("out-of-aperture Q transition",[row,bit,edge])
                counts["q_rises" if tr[q][edge] else "q_falls"]+=1
            if not changes(tr[q],True) or not changes(tr[q],False):
                fault("unsensitized cell",[row,bit])
    return {"source_to_clock_rt":delays,"measured":counts,"minimum_rt":minimum,"worst_epoch_row_bit":worst,"faults":faults,
            "interior_bus_phase_adjustment_window_rt":phase_window}


def run(directory,runner,rows,extra,offset,seed,columns_per_run=2):
    directory=directory.resolve()
    runner = runner.resolve()
    if digest(runner) != PINNED_RUNNER_SHA256:
        raise ValueError("The native executable differs from the reviewed terrain runner")
    inputs_to_freeze = {**source_paths(), "native-executable": runner}
    before = frozen(inputs_to_freeze)
    directory.mkdir(parents=True,exist_ok=False)
    write(directory / "source-sha256.json", {name: item[1] for name, item in before.items()})
    blocks,inputs,ports=fixture(rows,extra)
    world=directory/"world"
    with NativeWorldWriter(world,surface_y=-61,spawn_platform=False) as writer:
        writer.set_blocks(blocks)
    del writer,blocks
    rb=validate_native_world(world)
    imported=export_world(world,directory/"world.jsonl")
    physical_files = {str(p):p for p in [directory/"world.jsonl", world/"level.dat", *sorted((world/"region").glob("*.mca"))]}
    physical_before = frozen(physical_files)
    write(directory/"import.json",imported)
    write(directory/"readback.json",rb)
    runner_hash=digest(runner)
    probe=run_native(runner,directory,"origin",{"inputs":inputs,"outputs":{"oscillator":ports["oscillator"]},
        "initial_ticks":300,"capture_trace":True,"steps":[{"drive":{"enable":True},"ticks":600}]},imported)
    first=changes(probe["traces"]["oscillator"],True)[0]
    events,final=vectors(rows,seed)
    epochs,steps=plan(first,events,inputs,rows,offset)
    core=["oscillator","root","near_C","far_C",*[f"phase{r}" for r in range(rows)]]
    selects=[f"sel{r}_{p}" for r in range(rows) for p in ("C","D","Q")]
    qualifier=[f"write{r}" for r in range(rows)] + [f"guard{r}_{p}" for r in range(rows) for p in ("A","Y")]
    pins=core+selects+qualifier
    controls=run_native(runner,directory,"controls",{"inputs":inputs,"outputs":{n:ports[n] for n in pins},
        "initial_ticks":300,"capture_trace":True,"steps":steps},imported)["traces"]
    strobes=audit_strobes(controls,first,epochs,events,rows)
    shared_sha=sha256(b"".join(bytes(controls[n]) for n in core)).hexdigest()
    # Store large JSON traces as lossless gzip after native verification.
    def compact(name):
        source=directory/(name+"-result.json")
        with source.open("rb") as file, gzip.open(str(source)+".gz","wb",compresslevel=5) as dest:
            shutil.copyfileobj(file,dest)
        source.unlink()
    compact("controls")
    reports=[]
    for start in range(0,32,columns_per_run):
        bits=range(start,start+columns_per_run)
        names=core+[f"terminal{b}" for b in bits]+[
            f"{n}{r}_{b}" for r in range(rows) for b in bits for n in ("bit","lock","data")]
        scenario={"inputs":inputs,"outputs":{n:ports[n] for n in names},"initial_ticks":300,
                  "capture_trace":True,"steps":steps}
        name=f"columns-{start:02d}"
        actual=run_native(runner,directory,name,scenario,imported)
        tr=actual["traces"]
        if any(tr[n]!=controls[n] for n in core):
            raise ValueError("Shared oscillator/row phases differed between native imports")
        report=analyze({**controls,**tr},first,epochs,events,rows,bits)
        report.update(columns=list(bits),scenario_sha256=actual["scenario_sha256"],result_sha256=digest(directory/(name+"-result.json")))
        reports.append(report)
        write(directory/(name+"-measurements.json"),report)
        compact(name)
        del actual,tr
    directed=run_native(runner,directory,"directed-word",directed_word_case(first,inputs,ports,rows,offset),imported)
    state={f"bit{r}_{b}":bool(final[r]>>b&1) for r in range(rows) for b in range(32)}
    case={"inputs":inputs,"outputs":{n:ports[n] for n in state},"initial_ticks":300,"capture_trace":False,
          "steps":[*steps[:-1],steps[-1]|{"expect":state}]}
    actual=run_native(runner,directory,"quiescent",case,imported,snapshot=True)
    restored=directory/"restored-world"
    restoration=restore_snapshot(directory/"quiescent-snapshot.jsonl",world,restored,expected_sha256=actual["snapshot"]["sha256"])
    reader=NativeWorldReader(restored)
    saved=[]
    for r in range(rows):
        word=0
        for b in range(32):
            state=reader.get_block(Vec3(*ports[f"bit{r}_{b}"]))
            if state not in (repeater("east",locked=True),repeater("east",locked=True,powered=True)):
                raise ValueError("Invalid quiescent storage repeater")
            word|=int(dict(state.properties)["powered"]=="true")<<b
        saved.append(word)
    write(directory/"restored-storage.json",{"actual_words":saved,"expected_words":final,"restore":restoration})
    if digest(runner)!=runner_hash or digest(directory/"world.jsonl")!=imported["stream_sha256"]:
        raise ValueError("Inputs changed during verification")
    if frozen(inputs_to_freeze) != before or frozen(physical_files) != physical_before:
        raise ValueError("The source, runner, original world or streamed image changed during verification")
    windows = {bit: [offset + value for value in limits]
               for measurement in reports
               for bit, limits in measurement["interior_bus_phase_adjustment_window_rt"].items()}
    common_window = [max(limits[0] for limits in windows.values()),
                     min(limits[1] for limits in windows.values())]
    report={"rows":rows,"word_bits":32,"storage_pins":rows*32,"epochs":len(events),"seed":seed,
            "bus_launch_after_oscillator_rise_rt":offset,"extra_strobe_rt":extra,"real_launch_register_columns":[0,31],
            "runner_sha256":runner_hash,"stream_sha256":imported["stream_sha256"],"shared_trace_sha256":shared_sha,
            "observed_guard_and_terminal_strobes":strobes,
            "source_hashes_sha256":digest(directory/"source-sha256.json"),
            "interior_phase_intersection_from_measured_transitions_rt":common_window,"directed_column_pair_assertions":directed["assertions"],
            "readback":rb,"measurements":reports,"saved_matches_all_rows":saved==final,
            "receiver_passed":saved==final and not any(r["faults"] for r in reports),
            "classifier_fanout_verified":False,"cdc_verified":False,"complete_machine_runtime_verified":False}
    write(directory/"report.json",report)
    print(json.dumps({k:report[k] for k in ["rows","epochs","receiver_passed","saved_matches_all_rows"]}))
    return report


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output",type=Path)
    parser.add_argument("--runner",required=True,type=Path)
    parser.add_argument("--rows",type=int,choices=(4,16),default=16)
    parser.add_argument("--extra-strobe",type=int,choices=(0,6),default=0)
    parser.add_argument("--columns-per-run",type=int,choices=(1,2,4),default=2)
    parser.add_argument("--bus-offset",type=int,choices=range(0,60),metavar="0..59",default=41)
    args=parser.parse_args()
    result=run(args.output,args.runner,args.rows,args.extra_strobe,args.bus_offset,SEED,args.columns_per_run)
    raise SystemExit(0 if result["receiver_passed"] else 1)
