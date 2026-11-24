"""Compare every FIFO state bit and public output against the original RTL."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

EVIDENCE = Path(__file__).with_name("evidence")
FROZEN_HASHES = {
    "baseline": "50c552be1af19dbc9da2b2d3e93fb9b94d3e420a4618cd2a21514a9c71c27b4e",
    "optimized": "6b1d99501b1f487b8f17dd904dfc875a60d7b1b82f201ecbba5ed233c2fa9262",
}
SIMULATION_DEPTHS = (1, 2, 3, 5, 7, 8, 15, 16, 17, 31)
PROOF_DEPTHS = (1, 3, 5, 8, 16)


def read_frozen(name: str) -> bytes:
    raw = gzip.decompress((EVIDENCE / f"{name}.sv.gz").read_bytes())
    if hashlib.sha256(raw).hexdigest() != FROZEN_HASHES[name]:
        raise ValueError(f"Archived {name} RTL does not match its recorded SHA-256")
    return raw


def rename_module(source: str, name: str) -> str:
    return replace_once(source, "module shama_input #(", f"module {name} #(")


def replace_once(source: str, old: str, new: str) -> str:
    if source.count(old) != 1:
        raise ValueError(f"Cannot instrument unrecognized RTL anchor {old!r}")
    return source.replace(old, new)


def state_fields(depth: int) -> list[tuple[str, int]]:
    count_bits = max(1, (depth - 1).bit_length()) + 1
    return [(f"fifo[{i}]", 26) for i in range(depth)] + [
        ("prev_rows", 8), ("prev_cols", 8), ("prev_controller", 10), ("prev_event_ack", 1),
        ("keyboard_shift", 1), ("input_overflow", 1), ("count", count_bits),
    ]


def instrument(source: str, fields: list[tuple[str, int]]) -> str:
    """Add a common symbolic state-load edge and observe all real state bits."""
    width = sum(bits for _, bits in fields)
    anchor = "output logic       keyboard_shift\n"
    source = replace_once(source, anchor, anchor.rstrip() + ",\n    input wire load_state,\n"
                          f"    input wire [{width-1}:0] pre_state, output wire [{width-1}:0] post_state\n")
    loads, taps, offset = [], [], 0
    for signal, bits in fields:
        part = f"[{offset}+:{bits}]"
        loads.append(f"{signal}<=pre_state{part};")
        taps.append(f"assign post_state{part}={signal};")
        offset += bits
    source = replace_once(source, "if(rst) begin", "if(load_state) begin\n" + "\n".join(loads) + "\nend else if(rst) begin")
    return replace_once(source, "endmodule", "\n".join(taps) + "\nendmodule")


def miter(depth: int, width: int) -> str:
    units = []
    for kind, tag in (("baseline", "a"), ("candidate", "b")):
        units.append(f"""{kind} #(.FIFO_DEPTH({depth})) {tag}(.clk,.rst,.load_state,.pre_state,.event_ack,.event_ack_data,.kb_rows,.kb_cols,.controller,
    .post_state({tag}_state),.event_valid({tag}_out[0]),.event_code({tag}_out[8:1]),.key_code({tag}_out[16:9]),
    .controller_latched({tag}_out[26:17]),.input_overflow({tag}_out[27]),.queued_count({tag}_out[35:28]),.keyboard_shift({tag}_out[36]));""")
    return f"""module miter(input clk,rst,load_state,event_ack,input [25:0] event_ack_data,
    input [7:0] kb_rows,kb_cols,input [9:0] controller,input [{width-1}:0] pre_state,output mismatch);
wire [{width-1}:0] a_state,b_state;
wire [36:0] a_out,b_out;
{chr(10).join(units)}
assign mismatch=(a_state!=b_state)||(a_out!=b_out);
endmodule\n"""


def run(command, directory, logfile, *, timeout):
    with (directory / logfile).open("w") as log:
        proc = subprocess.run(command, cwd=directory, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
    if proc.returncode:
        raise RuntimeError(f"Exit {proc.returncode} from {command[0]}. See {directory / logfile}")
    return (directory / logfile).read_text()


def prove(directory, sources, depth):
    directory.mkdir()
    fields = state_fields(depth)
    width = sum(bits for _, bits in fields)
    for name, source in sources.items():
        (directory / f"{name}.sv").write_text(instrument(source, fields))
    (directory / "miter.sv").write_text(miter(depth, width))
    (directory / "proof.ys").write_text("""read_verilog -sv baseline.sv candidate.sv miter.sv
hierarchy -check -top miter
proc
flatten
memory_map
opt -full
sat -seq 3 -set-at 1 load_state 1 -set-at 2 load_state 0 -set-at 3 load_state 0 -prove-skip 2 -prove mismatch 0 -timeout 120 -verify
""")
    log = run(["yosys", "-Q", "-T", "-s", "proof.ys"], directory, "proof.log", timeout=600)
    if "SAT proof finished - no model found: SUCCESS!" not in log:
        raise RuntimeError(f"Missing SAT success at depth {depth}")
    return {"fifo_depth": depth, "compared_register_bits": width, "compared_output_bits": 37,
            "all_boolean_prestates_and_inputs": True}


def simulate(directory, sources, depth, cycles):
    directory.mkdir()
    for name, source in sources.items():
        (directory / f"{name}.sv").write_text(source)
    bench = Path(__file__).with_name("tb_input_equivalence.sv")
    run(["iverilog", "-g2012", "-s", "tb", f"-Ptb.D={depth}", "-o", "sim", "baseline.sv", "candidate.sv", str(bench)],
        directory, "compile.log", timeout=60)
    log = run(["vvp", "sim", f"+cycles={cycles}"], directory, "simulation.log", timeout=120)
    matches = [line for line in log.splitlines() if line.startswith("MATCH ")]
    if len(matches) != 1:
        raise RuntimeError(f"Missing comparison and coverage result at depth {depth}")
    return {key: int(value) for key, value in re.findall(r"(\w+)=(\d+)", matches[0])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="Fresh directory for exact inputs, bench logs and result JSON")
    parser.add_argument("--baseline", type=Path, help="Reference RTL override (defaults to the frozen original)")
    parser.add_argument("--candidate", type=Path, help="Candidate RTL override (defaults to the frozen matching optimization)")
    parser.add_argument("--simulate-depths", type=int, nargs="*", default=SIMULATION_DEPTHS)
    parser.add_argument("--prove-depths", type=int, nargs="*", default=PROOF_DEPTHS)
    parser.add_argument("--cycles", type=int, default=14000)
    args = parser.parse_args()
    if not (args.simulate_depths or args.prove_depths) or args.cycles < 1 or any(d < 1 for d in [*args.simulate_depths, *args.prove_depths]):
        parser.error("Supply positive depths and a positive random cycle count")
    for tool in (["iverilog", "vvp"] if args.simulate_depths else []) + (["yosys"] if args.prove_depths else []):
        if not shutil.which(tool):
            parser.error(f"Missing required tool {tool}")
    data = {"baseline": args.baseline.read_bytes() if args.baseline else read_frozen("baseline"),
            "candidate": args.candidate.read_bytes() if args.candidate else read_frozen("optimized")}
    sources = {name: rename_module(raw.decode(), name) for name, raw in data.items()}
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    for name, raw in data.items():
        (output / f"{name}-original.sv").write_bytes(raw)
    report = {"scope": "RTL input FIFO transition and simulation comparison", "raw_clock_timing_verified": False,
              "source_sha256": {name: hashlib.sha256(raw).hexdigest() for name, raw in data.items()},
              "simulation": [], "proof": []}
    for depth in args.simulate_depths:
        report["simulation"].append(simulate(output / f"simulation-{depth}", sources, depth, args.cycles))
    for depth in args.prove_depths:
        report["proof"].append(prove(output / f"proof-{depth}", sources, depth))
    (output / "result.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
