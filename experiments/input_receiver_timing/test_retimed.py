"""Retimed flags must accompany the exact original packet at each valid epoch."""
from pathlib import Path
import shutil
import subprocess

import pytest

from shamaos.synthesis import synthesize_json
from experiments.input_epoch_capture import test_bank, test_capture

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
CAPTURE=r"""
module input_capture(input wire clk,rst,input wire[7:0] kb_rows,kb_cols,input wire[9:0]controller,
 input wire event_ack,observe,input wire[25:0]event_ack_data,
 output wire[9:0]levels,edges,output wire[5:0]key_index,
 output wire key_valid,key_shift,ack_rise,observed,sample_valid,output wire[25:0]ack_data);
 wire eventful,urgent_present;wire[1:0]winning_urgent;
 retimed_input_capture actual(.*);
 always @(negedge clk) if(sample_valid) begin
  if(eventful!==((|edges)||key_valid) || urgent_present!== (|edges[9:6]) ||
     winning_urgent!==(edges[8]?2'd2:edges[9]?2'd3:edges[7]?2'd1:2'd0))
     $fatal(1,"misaligned event classification");
 end
endmodule
"""
BANK=r"""
module input_window_bank(input wire clk,rst,active,begin_window,key_valid,key_shift,
 input wire[5:0]key_index,input wire[9:0]levels,edges,input wire[3:0]read_index,
 output wire[27:0]prefix_word,output wire[11:0]urgent_word,output wire[16:0]next_prefix,next_urgent,
 output wire lost);
 wire eventful=key_valid || (|edges);
 wire urgent_present=|edges[9:6];
 wire[1:0] winning_urgent=edges[8]?2'd2:edges[9]?2'd3:edges[7]?2'd1:2'd0;
 retimed_input_bank actual(.*);
endmodule
"""


@pytest.mark.skipif(any(shutil.which(t) is None for t in ("yosys","iverilog","vvp")),reason="HDL tools required")
@pytest.mark.parametrize("capture",[True,False])
def test_retimed_source_and_production_map(tmp_path,capture):
    if capture:
        top="retimed_input_capture"
        test_capture.write_vectors(tmp_path/"pins.hex")
        bench=CAPTURE+test_capture.BENCH
    else:
        top="retimed_input_bank"
        test_bank.vectors(tmp_path/"bank.hex")
        bench=BANK+test_bank.BENCH
    source=HERE/(top+".sv")
    synthesize_json([source],top=top,output_json=tmp_path/"mapped.json",repo_root=ROOT)
    subprocess.run(["yosys","-Qq","-p",f'read_liberty "{ROOT.as_posix()}/synth/redstone.lib"; '
                    "read_json mapped.json; write_verilog -noattr mapped.v"],cwd=tmp_path,check=True)
    (tmp_path/"bench.sv").write_text(bench)
    for target in (source,tmp_path/"mapped.v"):
        subprocess.run(["iverilog","-g2012","-s","test","-o","test",str(target),"bench.sv"],cwd=tmp_path,check=True)
        result=subprocess.run(["vvp","test"],cwd=tmp_path,capture_output=True,text=True)
        assert result.returncode==0,result.stdout+result.stderr
        assert "PASS" in result.stdout
