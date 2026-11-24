"""Weight the exact, unrouted production-mapped register-to-register cone."""
from collections import Counter
from graphlib import TopologicalSorter
import hashlib
import json
from pathlib import Path
import sys


def analyze(mapped, top):
    module = json.loads(mapped.read_bytes())["modules"][top]
    names = {}
    for name, net in module["netnames"].items():
        if not net["hide_name"]:
            for i, bit in enumerate(net["bits"]):
                if isinstance(bit, int):
                    names.setdefault(bit, f"{name}[{i}]")
    gates, registers = {}, {}
    local_rt = {"$_NAND_": 3, "$_NOR_": 3, "$_NOT_": 2}
    counts = Counter()
    for cell in module["cells"].values():
        pins, kind = cell["connections"], cell["type"]
        counts[kind] += 1
        if kind == "DFF":
            registers[pins["Q"][0]] = pins["D"][0]
        else:
            gates[pins["Y"][0]] = (local_rt[kind], [pins[k][0] for k in ("A","B") if k in pins])
    dag = {out: [i for i in ins if i in gates] for out, (_, ins) in gates.items()}

    def maximum(starts, ends):
        paths = {src: (0, 0, src) for src in starts}
        for bit in TopologicalSorter(dag).static_order():
            ticks, inputs = gates[bit]
            arrivals = [paths[i] for i in inputs if i in paths]
            if arrivals:
                old, length, source = max(arrivals)
                paths[bit] = (old + ticks, length + 1, source)
        delay, depth, start, end = max((*paths[dst], label) for dst, label in ends if dst in paths)
        return {"source": names.get(start,str(start)), "sink": end, "gate_count":depth,"local_rt":delay}

    reg_ends = [(d,names.get(q,str(q))) for q,d in registers.items()]
    inputs = [b for p in module["ports"].values() if p["direction"]=="input" for b in p["bits"]]
    return {"top":top, "mapped_sha256":hashlib.sha256(mapped.read_bytes()).hexdigest(),"cells":sum(counts.values()),
            "by_kind":dict(sorted(counts.items())), "register_to_register":maximum(registers,reg_ends),
            "input_to_register":maximum(inputs,reg_ends),
            "scope":"Zero-wire combinational structural budgets weighted with local native cell delays."}


if __name__ == "__main__":
    print(json.dumps(analyze(Path(sys.argv[1]),sys.argv[2]),indent=2))
