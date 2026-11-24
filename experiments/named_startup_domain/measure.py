"""Measure actual emitted startup controls in the pinned terrain-aware engine."""
import argparse
from bisect import bisect_left, bisect_right
from collections import Counter
import hashlib
import itertools
import json
import os
from pathlib import Path
import subprocess

from shamaos.bounded_system import BoundedPartition, place_startup_control
from shamaos.hardware.logic import template_for
from shamaos.hardware.partition_planner import load_mapped_module
from shamaos.mchprs_import import export_world
from shamaos.synthesis import synthesize_json
from shamaos.system_build import partition_rtl_files
from shamaos.worldio import NativeWorldWriter

ROOT = Path(__file__).resolve().parents[2]
TOP = "shama_startup_control"
ROLES = ("clock_hold", "divided_clock_active", "reset_pulse_request", "reset_pulse_received")


def sha(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def stimulus(lead):
    steps = []
    def drive(ticks, **states):
        steps.append({"ticks": ticks, "drive": states})
    def cycles(n, **change):
        for index in range(n):
            drive(35, clk=True, **(change if index == 0 else {}))
            drive(35, clk=False)
    drive(2048)
    if lead is None:
        drive(2048, power_switch=True)
        cycles(50)
        cycles(32, reset_button=True)
        cycles(32, reset_button=False)
        cycles(40, power_switch=False)
        cycles(40, shutdown_ready=True)
        drive(2048)
        drive(2048, power_switch=True)
        cycles(32)
        cycles(40, shutdown_ready=False)
        drive(2048)  # The facade and private leaves still carry the final raw edge.
    else:
        drive(lead, power_switch=True, core_gate=True)
        cycles(95)
        drive(1024)
        drive(1024, divider_high=True)
        drive(1024, divider_high=False)
        drive(2048)
    steps[0]["expect"] = {"clock_hold": False, "rst": True, "core_clk": False}
    steps[-1]["expect"] = {"clock_hold": True, "rst": False, "core_clk": False}
    return steps


def actual_pins(part, module):
    def coords(point):
        return [point.x, point.y, point.z]
    inputs = {role: coords(pin) for role, (pin,) in part.input_ports.items()}
    outputs = {role: coords(pin) for role, (pin,) in part.output_ports.items()}
    for role in ROLES:
        q = module["netnames"][role]["bits"]
        name, = (n for n,c in module["cells"].items() if c["connections"].get("Q") == q
                 and template_for(c["type"]).cell_type == "DFF")
        anchor = part.cell_origins[name]
        for pin, off in template_for(part.cell_types[name]).pins.items():
            outputs[f"{role}_{pin}"] = coords(anchor.offset(off.x, off.y, off.z))
    return inputs, outputs


def grade(result, lead):
    wave = result["traces"]
    count = result["ticks"]
    assert all(len(trace) == count for trace in wave.values())
    assert result["status"] == "passed" and not result["pending_ticks_after_scenario"]
    registers = {}
    for role in ROLES:
        c, d = wave[f"{role}_C"], wave[f"{role}_D"]
        transitions = [t for t in range(1, count) if d[t] != d[t-1]]
        margins = []
        for t in range(2048, count-2):
            if c[t] and not c[t-1]:
                before, after = bisect_right(transitions,t), bisect_left(transitions,t)
                last = transitions[before-1] if before else 0
                following = transitions[after] if after < len(transitions) else count
                margins.append((t+1, t-last, following-t))
        assert margins, role
        registers[role] = {"edges": len(margins), "min_setup": min(m[1] for m in margins),
                           "min_hold": min(m[2] for m in margins),
                           "violations": [m for m in margins if m[1] < 8 or m[2] < 2]}
    durations = Counter()
    t = 0
    for high, group in itertools.groupby(wave["core_clk"]):
        length = sum(1 for _ in group)
        if high:
            durations[length] += 1
            assert all(bool(k) == (length == 35) for k in wave["rst"][t:t+length])
        t += length
    assert not wave["clock_hold"][2047] and wave["rst"][2047]
    if lead is None:
        assert set(durations) == {35}
        for tick, powered, running in ((7400,1,1),(9600,1,0),(11900,1,1),
                    (14600,1,1),(19000,0,0),(23600,1,0),(26700,1,1)):
            assert (wave["clock_hold"][tick-1], wave["rst"][tick-1]) == (bool(powered), not running)
    else:
        assert set(durations) == {35,1024} and durations[1024] == 1
        assert wave["clock_hold"][-1] and not wave["rst"][-1]
    return {"ticks": count, "core_output_pulse_widths": dict(durations), "registers": registers}


def run(out, runner, *, baseline):
    out.mkdir(parents=True, exist_ok=False)
    mapped = out/"startup.json"
    synthesize_json(partition_rtl_files(ROOT,TOP), top=TOP, output_json=mapped,
                    yosys="yosys", repo_root=ROOT)
    part = place_startup_control(mapped,surface_y=-61,maximum_y=285)
    module, _ = load_mapped_module(mapped,TOP)
    inputs, outputs = actual_pins(part,module)
    dut = BoundedPartition(part.graph,part.plan,part.placed,part.surface_y,part.maximum_y) if baseline else part
    with NativeWorldWriter(out/"world",surface_y=-61,spawn_platform=False) as writer:
        writer.set_blocks(dut.iter_placements())
        terrain = writer.validate()
        assert terrain["status"] == "passed" and terrain["terrain_preserved"]
    stream = out/"world.jsonl"
    export_world(out/"world",stream)
    report = {"baseline":baseline,"scope":"production startup partition, six driven input contacts",
              "source_sha256":{p:sha(ROOT/p) for p in ("rtl/shama_startup_control.sv",
                 "rtl/shama_native_cells.svh","src/shamaos/bounded_system.py","src/shamaos/hardware/logic.py",
                 "src/shamaos/system_build.py","experiments/named_startup_domain/measure.py")},
              "mapped_sha256":sha(mapped),"emitted_stream_sha256":sha(stream),"runner_sha256":sha(runner),
              "partition":dut.manifest(),"cases":{}}
    for lead in (None,12,55,95,157):
        name = "power-cycle" if lead is None else f"lead-{lead}"
        case = out/f"{name}.json"
        case.write_text(json.dumps({"inputs":inputs,"outputs":outputs,"initial_ticks":0,
                                    "capture_trace":True,"steps":stimulus(lead)}))
        raw = out/f"{name}-result.json"
        with raw.open("w") as output:
            subprocess.run([str(runner),str(stream),str(case),"1000"],check=True,stdout=output,timeout=60)
        result = json.loads(raw.read_bytes())
        graded = grade(result,lead)
        report["cases"][name] = {**graded,"scenario_sha256":sha(case),"result_sha256":sha(raw)}
    (out/"report.json").write_text(json.dumps(report,indent=2)+"\n")
    failures = [(n,r,v) for n,x in report["cases"].items() for r,y in x["registers"].items() for v in y["violations"]]
    if baseline:
        assert {r for _,r,_ in failures} == {"reset_pulse_request"}
        assert {n for n,_,_ in failures} == set(report["cases"]) and all(v[1]==5 for _,_,v in failures)
    else:
        assert not failures, failures


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out",type=Path)
    parser.add_argument("--baseline",action="store_true")
    args = parser.parse_args()
    run(args.out, Path(os.environ["SHAMA_MCHPRS_TERRAIN_RUNNER"]), baseline=args.baseline)
