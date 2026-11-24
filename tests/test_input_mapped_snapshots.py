"""Replay the actual production JSON and the source against an independent FIFO oracle."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from shamaos.synthesis import synthesize_json
from test_input_eviction import Queue, write_cases

ROOT = Path(__file__).resolve().parents[1]
DEPTH = 16


def write_snapshot_cases(vectors):
    # Each controller snapshot persists after physical release and can be ACKed
    # by its full payload. Include the reset-reachable sixteen-A/Editor witness.
    with vectors.open("a") as stream:
        queue = Queue(DEPTH, stream)
        for occupancy in range(DEPTH + 1):
            for control in range(10):
                queue.edge(reset=True)
                for _ in range(occupancy):
                    queue.type_key(0)
                mask = 1 << control
                queue.edge(controller=mask)
                queue.edge()
                queue.edge(ack=True, payload=mask << 16 | (0x10 + control))
                queue.edge()
        return queue.edges


@pytest.mark.skipif(any(shutil.which(tool) is None for tool in ("yosys", "iverilog", "vvp")),
                    reason="Yosys and Icarus Verilog required for mapped input transactions")
def test_production_mapped_fifo_preserves_controller_snapshots_and_stored_records(tmp_path):
    source = ROOT / "rtl/shama_input.sv"
    bench = ROOT / "tests/rtl/input_eviction_tb.sv"
    mapped = synthesize_json([source], top="shama_input", output_json=tmp_path / "input.json", repo_root=ROOT)
    module = json.loads(mapped.read_bytes())["modules"]["shama_input"]
    names = [f"fifo[{i}]" for i in range(DEPTH)]
    assert all(len(module["netnames"][name]["bits"]) == 26 for name in names)
    probes = [f"assign observed[{37 + 26 * i}+:26]=dut.\\{name} ;" for i, name in enumerate(names)]
    (tmp_path / "fifo_slot_probes.svh").write_text("\n".join(probes) + "\n")

    # Yosys writes combinational primitives directly. Read the production Liberty
    # file so the generated Verilog also uses its actual positive-edge DFF model.
    subprocess.run(["yosys", "-Qq", "-p", f'read_liberty "{ROOT.as_posix()}/synth/redstone.lib"; '
                    "read_json input.json; write_verilog -noattr mapped.v"], cwd=tmp_path,
                   capture_output=True, text=True, check=True, timeout=60)
    vectors = tmp_path / "vectors.hex"
    edges = write_cases(vectors, DEPTH) + write_snapshot_cases(vectors)
    for variant, files in (("source", [str(source)]), ("mapped", ["-DSHAMA_INPUT_MAPPED", "mapped.v"])):
        subprocess.run(["iverilog", "-g2012", "-s", "input_eviction_tb", "-o", variant, *files, str(bench)],
                       cwd=tmp_path, capture_output=True, text=True, check=True, timeout=60)
        result = subprocess.run(["vvp", variant, "+VECTORS=vectors.hex"], cwd=tmp_path,
                                capture_output=True, text=True, timeout=180)
        assert result.returncode == 0, f"{variant}\n{result.stdout}{result.stderr}"
        assert f"FIFO TRANSACTIONS PASS depth={DEPTH} edges={edges}" in result.stdout
