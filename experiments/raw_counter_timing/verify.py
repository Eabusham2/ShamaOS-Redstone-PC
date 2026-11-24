"""Measure exact-cycle raw counters with the production mapper and SAT induction."""
from collections import Counter
from graphlib import TopologicalSorter
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

from shamaos.synthesis import synthesize_json
from shamaos.hardware.logic import template_for
from shamaos.model import Vec3


def timing(path, top):
    module = json.loads(path.read_bytes())["modules"][top]
    gates, registers, counts = {}, {}, Counter()
    local_delay = {"$_NAND_": 3, "$_NOR_": 3, "$_NOT_": 2}
    for name, cell in module["cells"].items():
        kind, pins = cell["type"], cell["connections"]
        counts[kind] += 1
        if kind == "DFF":
            registers[pins["Q"][0]] = (pins["D"][0], name)
        else:
            gates[pins["Y"][0]] = (local_delay[kind],
                                    [pins[k][0] for k in ("A", "B") if k in pins])
    arrivals = {q: 0 for q in registers}
    graph = {q: [i for i in ins if i in gates] for q, (_, ins) in gates.items()}
    for bit in TopologicalSorter(graph).static_order():
        delay, ins = gates[bit]
        reached = [arrivals[i] for i in ins if i in arrivals]
        if reached:
            arrivals[bit] = max(reached) + delay
    template_counts = {kind: sum(1 for _ in template_for(kind).emitter(Vec3(0, 0, 0), "counter"))
                       for kind in counts}
    return {"cells": sum(counts.values()), "by_kind": dict(sorted(counts.items())),
            "cell_template_placements": sum(counts[kind] * template_counts[kind] for kind in counts),
            "longest_local_gate_path_rt": max(arrivals[d] for d, _ in registers.values() if d in arrivals),
            "mapped_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def formal(here, output, width, step, *, corrupt=False):
    candidate = here / "predicted_counter.sv"
    if corrupt:
        candidate = output / "corrupt_counter.sv"
        candidate.write_text((here / "predicted_counter.sv").read_text().replace(
            "{{(BASE-1){1'b1}}, 1'b0}", "{BASE{1'b1}}"))
    script = (f"read_verilog -formal -sv {here / 'reference_counter.sv'} {candidate} {here / 'miter.sv'}; "
              f"chparam -set WIDTH {width} -set SLICE {step} counter_miter; "
              "prep -top counter_miter; flatten; opt_clean; "
              "sat -seq 3 -tempinduct -maxsteps 8 -set-init-zero -prove-asserts -verify")
    name = f"formal-{width}-{step}{'-negative' if corrupt else ''}"
    run = subprocess.run(["yosys", "-Q", "-T", "-p", script], capture_output=True, text=True, timeout=90)
    log = run.stdout + run.stderr
    (output / f"{name}.log").write_text(log)
    if corrupt:
        if run.returncode == 0 or "proof did fail" not in log.lower():
            raise RuntimeError("The off-by-one prediction must fail the equivalence proof")
    elif run.returncode or "Induction step proven: SUCCESS!" not in log:
        raise RuntimeError(f"Counter equivalence was not proved; see {name}.log")
    return {"width": width, "slice": step, "negative_control": corrupt,
            "returncode": run.returncode, "log_sha256": hashlib.sha256(log.encode()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    here = Path(__file__).resolve().parent
    root = here.parents[1]
    results = {"schema": "raw-counter-structural-comparison-v1",
               "source_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                 for p in here.glob("*.sv")},
               "formal": [], "mapping": [],
               "scope": "Cycle-equivalent isolated RTL and zero-wire mapped gate paths, not routed clock closure.",
               "production_promoted": False, "native_timing_verified": False}
    for width, step in ((1, 8), (7, 8), (8, 8), (9, 8), (18, 8), (64, 8), (3, 1), (17, 4)):
        result = formal(here, args.output, width, step)
        results["formal"].append(result)
        print("formal", width, step, "passed", flush=True)
    results["formal"].append(formal(here, args.output, 3, 1, corrupt=True))
    for width in (18, 64):
        for kind in ("reference", "predicted"):
            top = f"{kind}_{width}"
            wrapper = args.output / f"{top}.sv"
            wrapper.write_text(f"module {top}(input clk,rst,output [{width-1}:0] value); "
                               f"{kind}_counter #(.WIDTH({width})) impl(clk,rst,value); endmodule\n")
            mapped = args.output / f"{top}.json"
            synthesize_json([here / f"{kind}_counter.sv", wrapper], top=top,
                            output_json=mapped, repo_root=root)
            measured = {"width": width, "kind": kind, **timing(mapped, top)}
            results["mapping"].append(measured)
            print(json.dumps(measured), flush=True)
    bound_sources = [root / "src/shamaos/synthesis.py", root / "src/shamaos/hardware/logic.py",
                     *sorted((root / "synth").glob("*"))]
    results["mapper_sources"] = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in bound_sources if p.is_file()}
    results["yosys"] = subprocess.check_output(["yosys", "-V"], text=True).strip()
    (args.output / "comparison.json").write_text(json.dumps(results, indent=2) + "\n")


if __name__ == "__main__":
    main()
