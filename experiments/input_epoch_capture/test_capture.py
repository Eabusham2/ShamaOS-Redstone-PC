"""Source and production-mapped pipeline against an independent raw-pin decoder."""
import gzip
import hashlib
from pathlib import Path
import random
import shutil
import subprocess

import pytest

from shamaos.synthesis import synthesize_json

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def write_vectors(path):
    rng = random.Random(317)
    pins = [(True, 0, 0, 0, False, 0)]
    for index in range(64):
        pins += [(False, 1 << (index // 8), 1 << (index % 8), 0x300, True, 0x3000018)] * 3
        pins.append((False, 0, 0, 0, False, 0))
    for i in range(9000):
        rows, cols = rng.choice(((0, 0), (1, 1), (3, 3), (128, 128),
                                 (1 << rng.randrange(8), 1 << rng.randrange(8))))
        pins.append((i % 523 == 0, rows, cols, rng.randrange(1024), rng.getrandbits(1), rng.randrange(1 << 26)))
    raw = gzip.decompress((HERE / "evidence/native-console.hex.gz").read_bytes())
    assert hashlib.sha256(raw).hexdigest() == "6adc6845fa22b9d4c272bdc191a1eb30699a7050b04ccc5ffbdcd797391f0aca"
    trace = [int(line, 16) for line in raw.splitlines()]
    assert len(trace) == 77 * 300
    for phase in range(70):
        pins.append((True, 0, 0, 0, False, 0))
        seen = []
        prev_rows = prev_cols = prev_ctrl = 0
        for word in trace[phase::70]:
            rows, cols, control = word & 255, (word >> 8) & 255, word >> 16
            pins.append((False, rows, cols, control, False, 0))
            if rows.bit_count() == cols.bit_count() == 1 and (rows, cols) != (prev_rows, prev_cols):
                seen.append(("key", (rows.bit_length() - 1) * 8 + cols.bit_length() - 1))
            for i in range(10):
                if control & ~prev_ctrl & (1 << i): seen.append(("controller", i))
            prev_rows, prev_cols, prev_ctrl = rows, cols, control
        assert seen == ([("key", i) for i in range(64)] +
                        [("controller", i) for i in range(10)] + [("key", 63)]), phase
    pins.extend([(False, 0, 0, 0, False, 0)] * 3)
    pending = [None, None]
    prev = (0, 0, 0, 0)
    shifted = False
    with path.open("w") as stream:
        for edge, (rst, rows, cols, control, ack, data) in enumerate(pins):
            observe = edge % 11 in (0, 1)
            stim = observe << 54 | rst << 53 | rows << 45 | cols << 37 | control << 27 | ack << 26 | data
            if rst:
                shifted = False
                prev = (0, 0, 0, 0)
                pending = [None, None]
                output = None
            else:
                pr, pc, prev_control, prev_ack = prev
                changed = (rows, cols) != (pr, pc)
                valid = changed and rows.bit_count() == cols.bit_count() == 1
                index = ((rows.bit_length() - 1) * 8 + cols.bit_length() - 1) if valid else 0
                key_valid = valid and index != 63
                # Only meaningful fields are compared. Reset invalidates the pipeline.
                bits = 1 << 54 | (control << 44) | ((control & ~prev_control) << 34)
                bits |= (index if key_valid else 0) << 28 | (key_valid << 27) | (shifted << 26)
                bits |= (data if ack and not prev_ack else 0)
                output = pending.pop(0)
                pending.append((bits, int(ack and not prev_ack), int(observe)))
                prev = (rows, cols, control, ack)
                if valid and index == 63: shifted = not shifted
            bits, rising, seen = output or (0, 0, 0)
            stream.write(f"{stim:x} {bits:x} {rising:x} {seen:x}\n")
    return len(pins)


BENCH = r"""
module test;
 reg clk=0,rst,event_ack,observe;reg[7:0]kb_rows,kb_cols;reg[9:0]controller;reg[25:0]event_ack_data;
 wire[9:0]levels,edges;wire[5:0]key_index;wire key_valid,key_shift,ack_rise,observed,sample_valid;wire[25:0]ack_data;
 input_capture dut(.*);
 reg[54:0] pins;reg[54:0]expected;reg expected_rise,expected_observe;integer f,n=0;
 wire[54:0] packed_result=sample_valid ? {1'b1,levels,edges,(key_valid?key_index:6'd0),key_valid,key_shift,(ack_rise?ack_data:26'd0)}:55'd0;
 initial begin
  f=$fopen("pins.hex","r");
  while(!$feof(f)) begin
   if($fscanf(f,"%h %h %h %h\n",pins,expected,expected_rise,expected_observe)!=4) $fatal(1);
   {observe,rst,kb_rows,kb_cols,controller,event_ack,event_ack_data}=pins;
   #1;clk=1;#1;
   if(packed_result!==expected || (sample_valid && (ack_rise!==expected_rise || observed!==expected_observe)))
    $fatal(1,"capture mismatch at edge %d got %h expected %h",n,packed_result,expected);
   clk=0;#1;n=n+1;
  end
  $display("CAPTURE PASS %0d",n);$finish;
 end
endmodule
"""


@pytest.mark.skipif(any(shutil.which(x) is None for x in ("yosys","iverilog","vvp")),reason="HDL tools required")
def test_capture_source_and_mapped(tmp_path):
    source = HERE / "input_capture.sv"
    synthesize_json([source],top="input_capture",output_json=tmp_path/"input.json",repo_root=ROOT)
    subprocess.run(["yosys","-Qq","-p",f'read_liberty "{ROOT.as_posix()}/synth/redstone.lib"; '
                    "read_json input.json; write_verilog -noattr mapped.v"],cwd=tmp_path,check=True)
    count = write_vectors(tmp_path / "pins.hex")
    (tmp_path / "test.sv").write_text(BENCH)
    for name, files in (("source",source),("mapped",tmp_path/"mapped.v")):
        subprocess.run(["iverilog","-g2012","-s","test","-o",name,str(files),"test.sv"],cwd=tmp_path,check=True)
        result = subprocess.run(["vvp",name],cwd=tmp_path,capture_output=True,text=True)
        assert result.returncode == 0,result.stdout
        assert f"CAPTURE PASS {count}" in result.stdout
