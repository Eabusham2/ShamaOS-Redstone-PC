"""Run the retained named startup island through shutdown, saved reload and restart."""
import argparse
from itertools import groupby
import json
from pathlib import Path
import subprocess
import tarfile

from experiments.named_startup_domain import measure
from shamaos.bounded_system import place_startup_control
from shamaos.hardware.logic import TEMPLATES
from shamaos.mchprs_import import digest, export_world
from shamaos.mchprs_snapshot import ENGINE_REVISION, TERRAIN_PATCH, restore_snapshot
from shamaos.release_io import file_inventory
from shamaos.worldio import NativeWorldReader

PINNED = "c8032089622501cb57f9edbb0ea7e3be24e3a23fd0b2b80653b0ed4f07045610"
ROOT = Path(__file__).resolve().parents[2]
ARCHIVE = ROOT / "experiments/named_startup_domain/evidence/native.tar.xz"


def write(path, value):
    with path.open("x") as dest:
        json.dump(value, dest, separators=(",", ":"))


def recover_original(directory):
    original = directory / "original"
    original.mkdir()
    with tarfile.open(ARCHIVE) as saved:
        for member in saved.getmembers():
            name = Path(member.name)
            if member.isfile() and name.parts[0] == "positive" and (
                    name.parts[1] == "world" or name.name in ("world.jsonl", "startup.json", "power-cycle.json", "report.json")):
                if name.is_absolute() or ".." in name.parts:
                    raise ValueError("unexpected path in named-startup recording")
                dest = original / name.relative_to("positive")
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(saved.extractfile(member).read())
    report = json.loads((original / "report.json").read_bytes())
    case = json.loads((original / "power-cycle.json").read_bytes())
    if (digest(original / "world.jsonl") != report["emitted_stream_sha256"]
            or digest(original / "startup.json") != report["mapped_sha256"]
            or digest(original / "power-cycle.json") != report["cases"]["power-cycle"]["scenario_sha256"]
            or report["runner_sha256"] != PINNED):
        raise ValueError("retained startup world, map, scenario or runner identity changed")
    # Shell orchestration may change after this native island was captured.
    # Require its original child, cell semantics and harness identity here;
    # below, compare every current emitted physical state with the saved Anvil.
    for name in ("rtl/shama_startup_control.sv", "rtl/shama_native_cells.svh",
                 "src/shamaos/hardware/logic.py", "experiments/named_startup_domain/measure.py"):
        if digest(ROOT / name) != report["source_sha256"][name]:
            raise ValueError(f"original named-startup producer changed at {name}")
    return original, case, report


def probe_map(original, case):
    mapped = original / "startup.json"
    part = place_startup_control(mapped, surface_y=-61, maximum_y=285)
    module = json.loads(mapped.read_bytes())["modules"][measure.TOP]
    inputs, outputs = measure.actual_pins(part, module)
    if inputs != case["inputs"] or outputs != case["outputs"]:
        raise ValueError("generated probes do not match the actual previously measured island")
    name, = [n for n, c in module["cells"].items() if c["type"] == "CLOCK_GUARD_ACK"]
    cell = module["cells"][name]
    if cell["connections"]["Q"] != module["netnames"]["raw_clock_inhibited"]["bits"]:
        raise ValueError("raw-clock acknowledgment does not drive the inhibit receipt")
    origin = part.cell_origins[name]
    points = {label: origin.offset(relative.x, relative.y, relative.z)
              for label, relative in TEMPLATES["CLOCK_GUARD_ACK"].pins.items()}
    points["held_mask"] = origin.offset(8,1,-4)
    points["mask_lock"] = origin.offset(7,1,-4)
    states = {b.pos:b.block for b in TEMPLATES["CLOCK_GUARD_ACK"].emitter(origin, "verify")}
    reader = NativeWorldReader(original / "world")
    all_states = {p.pos:p.block for p in part.iter_placements()}
    for point, block in sorted(all_states.items(),key=lambda item:(item[0].x//16,item[0].z//16)):
        if reader.get_block(point) != block:
            raise ValueError(f"generated named startup island differs from the retained native world at {point}")
    for label, point in points.items():
        if reader.get_block(point) != states[point]:
            raise ValueError(f"saved guard point differs from native template at {label}")
        outputs["raw_" + label] = [point.x, point.y, point.z]
    return inputs, outputs


def states(**kwargs):
    return dict(kwargs)


def pulses(wave):
    t = 0
    runs = []
    for high, bucket in groupby(wave):
        length = sum(1 for _ in bucket)
        if high:
            runs.append((t,length))
        t += length
    return runs


def verify_waves(result, *, restart):
    trace = result["traces"]
    held, ack, request = (trace["raw_" + role] for role in ("held_mask", "Q", "B"))
    if ack[1:] != held[:-1]:
        raise ValueError("clock inhibit acknowledgment does not follow the actual held mask by one tick")
    if not any(a != b for a,b in zip(ack,request)):
        raise ValueError("inhibit request and physically held acknowledgment were never distinguished")
    if any(held[i]!=held[i-1] and trace["raw_mask_lock"][i-1] and trace["raw_mask_lock"][i]
           for i in range(1,len(held))):
        raise ValueError("raw inhibit storage changed during an established clock lock")
    edges = pulses(trace["core_clk"])
    widths = [width for _,width in edges]
    if not edges or widths.count(1024) != int(restart) or any(width not in (35,1024) for width in widths):
        raise ValueError("generated reset or divided clock lost a pulse")
    if any(any(level != (width == 35) for level in trace["rst"][t:t+width]) for t,width in edges):
        raise ValueError("reset was not asserted through all raw-through-core highs at the island facade")
    releases = [i for i in range(1,len(ack)) if trace["rst"][i-1] and not trace["rst"][i]]
    if not releases or not all(ack[i] for i in releases):
        raise ValueError("facade reset deasserted without a true physical inhibit receipt")
    return {"reset_release_samples_with_held_inhibit":releases,"ticks":result["ticks"], "complete_reset_pulses":widths.count(35),
            "complete_divided_pulses":widths.count(1024), "logical_assertions":result["assertions"],
            "held_mask_transitions":len(pulses(held))*2-int(held[-1])-int(held[0]),
            "ack_differs_from_requested_mask_ticks":sum(a!=b for a,b in zip(ack,request)),
            "zero_pending":result["pending_ticks_after_scenario"]==0}


def prove(directory, runner):
    directory.mkdir(parents=True, exist_ok=False)
    if digest(runner)!=PINNED:
        raise ValueError("wrong native engine")
    matched = [Path(__file__).resolve(),
               *[p for p in (ROOT / "src/shamaos").rglob("*.py")],
               ROOT/"experiments/named_startup_domain/measure.py", ARCHIVE, runner]
    def freeze():
        return {str(p): (p.stat().st_dev,p.stat().st_ino,p.stat().st_size,p.stat().st_mtime_ns,
                         p.stat().st_ctime_ns,digest(p)) for p in matched}
    frozen = freeze()
    write(directory/"review-source-hashes.json", {
        str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else "native-runner":frozen[str(p)][-1] for p in matched})
    original, power_case, baseline = recover_original(directory)
    source_inventory = file_inventory(original / "world")
    inputs, outputs = probe_map(original, power_case)
    off = states(clock_hold=False, rst=True, core_clk=False, raw_Q=False)
    waiting = states(clock_hold=True, rst=True, core_clk=False)
    run = states(clock_hold=True, rst=False, core_clk=False, raw_Q=True)
    def execute(name, stream, scenario, *, snapshot=False):
        case = directory / (name+".json")
        raw = directory / (name+"-result.json")
        write(case, scenario)
        command=[str(runner),str(stream),str(case),"1000"]
        if snapshot:
            command += ["--snapshot",str(directory / "off-snapshot.jsonl")]
        with raw.open("xb") as stdout, (directory/(name+"-stderr.log")).open("xb") as stderr:
            subprocess.run(command, stdout=stdout, stderr=stderr, check=True, timeout=90)
        result=json.loads(raw.read_bytes())
        if (result["status"]!="passed" or result["engine_revision"]!=ENGINE_REVISION
                or result["engine_registry_patch_sha256"]!=TERRAIN_PATCH
                or result["scenario_sha256"]!=digest(case) or result["imported_stream_sha256"]!=digest(stream)
                or result["pending_ticks_after_scenario"] or result["complete_machine_runtime_verified"] is not False
                or result["ticks"]!=sum(s["ticks"] for s in scenario["steps"])+scenario["initial_ticks"]
                or result["assertions"]!=sum(len(s.get("expect",{})) for s in scenario["steps"])
                or set(result["traces"])!=set(outputs)):
            raise ValueError("native output differs from the required world, schedule or final quiescence")
        return result
    # Use the original full cold/manual-reset/shutdown sequence. Once the
    # machine is physically off, withdraw the ACK contact before saving.
    index, = [i for i,s in enumerate(power_case["steps"]) if s.get("drive",{}).get("shutdown_ready") is True]
    rest = power_case["steps"][index+1:]
    next_action = next(i for i,s in enumerate(rest) if s["ticks"]>35)
    cold_steps = power_case["steps"][:index+1+next_action+1]
    cold_steps += [{"drive":{"shutdown_ready":False},"ticks":2048,"expect":off}]
    def scenario(steps):
        return {"inputs":inputs,"outputs":outputs,"initial_ticks":0,"capture_trace":True,"steps":steps}
    cold = execute("cold",original/"world.jsonl",scenario(cold_steps),snapshot=True)
    cold_summary = verify_waves(cold,restart=False)
    snapshot = directory/"off-snapshot.jsonl"
    if cold["snapshot"]["sha256"] != digest(snapshot):
        raise ValueError("shutdown snapshot differs from the native process")
    world=directory/"restored"
    restored=restore_snapshot(snapshot,original/"world",world,expected_sha256=digest(snapshot))
    if (restored["pending_scheduled_ticks"]!=0 or restored["parent_imported_stream_sha256"]!=digest(original/"world.jsonl")
            or restored["parent_scenario_sha256"]!=digest(directory/"cold.json")):
        raise ValueError("restored island has a different electrical parent")
    fresh=directory/"restored.jsonl"
    imported=export_world(world,fresh)
    write(directory/"restored-import.json",imported)
    def cycles(count):
        return [{"drive":{"clk":value},"ticks":35} for _ in range(count) for value in (True,False)]
    fresh_steps=[{"ticks":2048,"expect":off},
                 {"drive":{"shutdown_ready":True,"core_gate":True},"ticks":1024},
                 {"drive":{"power_switch":True},"ticks":157},*cycles(40),
                 {"ticks":1024,"expect":waiting},{"drive":{"shutdown_ready":False},"ticks":256},
                 *cycles(76),{"ticks":2048,"expect":run},
                 {"drive":{"divider_high":True},"ticks":1024},
                 {"drive":{"divider_high":False},"ticks":2048,"expect":run}]
    reopened=execute("reopened",fresh,scenario(fresh_steps))
    fresh_summary=verify_waves(reopened,restart=True)
    if file_inventory(original/"world")!=source_inventory or freeze()!=frozen:
        raise ValueError("original Anvil or engine changed across restore")
    report={"scope":"same named 29-cell startup island, six external contacts, all attached-machine routes unmeasured",
            "source_sha256":digest(Path(__file__)),"review_source_inventory_sha256":digest(directory/"review-source-hashes.json"),"original_evidence_archive_sha256":digest(ARCHIVE),
            "original_report_sha256":digest(original/"report.json"),"original_stream_sha256":baseline["emitted_stream_sha256"],
            "runner_sha256":PINNED,"cold":cold_summary,"reopened":fresh_summary,"snapshot_sha256":digest(snapshot),
            "reopened_stream_sha256":digest(fresh),"restored_chunks":restored["chunks"],
            "recordings":{name:digest(directory/name) for name in ("cold.json","cold-result.json","reopened.json","reopened-result.json")},
            "whole_machine_verified":False}
    write(directory/"review.json",report)
    return report


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output",type=Path)
    parser.add_argument("--runner",type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(prove(args.output.resolve(),args.runner.resolve()),indent=2))
