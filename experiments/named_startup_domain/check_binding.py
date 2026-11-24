"""Check every startup pin and every raw/core consumer in an actual shell map."""
import argparse
import hashlib
import json
from pathlib import Path
from shamaos.system_build import PARTITION_SPECS
from experiments.named_startup_domain.audit_shell_feedback import audit


def check(path):
    module = json.loads(path.read_bytes())["modules"]["shama_soc"]
    cells, nets = module["cells"], module["netnames"]
    spec = dict(PARTITION_SPECS)
    assert {n:c["type"] for n,c in cells.items() if n.startswith("u_")} == spec
    raw, core, reset = (nets[n]["bits"] for n in ("clk", "core_clk", "rst"))
    startup = cells["u_startup"]["connections"]
    assert set(startup) == {"clk","clock_hold","core_clk","core_gate","divider_high",
                            "power_switch","reset_button","rst","shutdown_ready"}
    assert all(startup[n] == nets[n]["bits"] for n in startup if n != "divider_high")
    assert startup["divider_high"] == [nets["core_div_counter"]["bits"][-1]]
    for name in spec.keys()-{"u_startup"}:
        ports = cells[name]["connections"]
        assert ports["rst"] == reset and ports["clk"] == (raw if name=="u_input" else core)
    return {"shell_sha256":hashlib.sha256(path.read_bytes()).hexdigest(),
            "shell_cells":len(cells),"entire_shell_startup_pin_binding":startup,
            "clock_sinks":{n:("raw" if n=="u_input" else "generated_core") for n in sorted(spec.keys()-{"u_startup"})},
            "default_shell_feedback":audit(path)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("shell",type=Path)
    print(json.dumps(check(parser.parse_args().shell),indent=2))
