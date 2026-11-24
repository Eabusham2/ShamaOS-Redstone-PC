"""Check the compact hardware bank's payloads, finite capacities, and reuse."""
from pathlib import Path
import random
import shutil
import subprocess

import pytest
from shamaos.synthesis import synthesize_json

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]

BENCH=r"""
module test;
reg clk=0,rst,active,begin_window,key_valid,key_shift;reg[5:0]key_index;
reg[9:0]levels,edges;reg[3:0]read_index;wire[27:0]prefix_word;wire[11:0]urgent_word;
wire[16:0]next_prefix,next_urgent;wire lost;
input_window_bank dut(.*);
reg[30:0]pins;reg[16:0]p,u;reg over;reg[639:0]data;reg[27:0]a;reg[11:0]b;integer f,n=0;
initial begin
 f=$fopen("bank.hex","r");
 while(!$feof(f)) begin
  if($fscanf(f,"%h %h %h %h %h\n",pins,p,u,over,data)!=5) $fatal(1);
  {rst,active,begin_window,key_valid,key_shift,key_index,levels,edges}=pins;
  #1;clk=1;#1;
  if(next_prefix!==p || next_urgent!==u || lost!==over) $fatal(1,"pointer/overflow mismatch %d",n);
  for(integer i=0;i<16;i=i+1) begin
   read_index=i;#1;a=data[28*i+:28];b=data[448+12*i+:12];
   if((1<<i)<p && prefix_word!==a) $fatal(1,"prefix mismatch %d lane %d",n,i);
   if((1<<i)<u && urgent_word!==b) $fatal(1,"urgent mismatch %d lane %d",n,i);
  end
  clk=0;#1;n=n+1;
 end
 $display("BANK PASS %d",n); $finish;
end
endmodule
"""


def vectors(path):
    rng=random.Random(386)
    prefix=[];urgent=[];lost=False
    with path.open("w") as f:
        for i in range(1600):
            rst=i in (0,430,975)
            active=bool(rng.randrange(6))
            begin=active and i % 71 == 0
            key=bool(rng.getrandbits(1));shift=rng.getrandbits(1);index=rng.randrange(64)
            levels=rng.randrange(1024);edges=levels & rng.randrange(1024)
            if rst or begin:
                prefix.clear();urgent.clear();lost=False
            if not rst and active and (key or edges):
                if len(prefix)<16:
                    prefix.append(levels<<18|edges<<8|key<<7|index<<1|shift)
                else:
                    lost=True
                    first=next((j for j in (8,9,7,6) if edges & (1<<j)),None)
                    if first is not None and len(urgent)<16: urgent.append(levels<<2|first-6)
            pins=rst<<30|active<<29|begin<<28|key<<27|shift<<26|index<<20|levels<<10|edges
            records=sum(v<<(28*i) for i,v in enumerate(prefix))
            records|=sum(v<<(448+12*i) for i,v in enumerate(urgent))
            f.write(f"{pins:x} {1<<len(prefix):x} {1<<len(urgent):x} {int(lost):x} {records:x}\n")


@pytest.mark.skipif(any(shutil.which(t) is None for t in ("yosys","iverilog","vvp")),reason="HDL tools required")
def test_source_and_actual_mapped_bank(tmp_path):
    source=HERE/"input_window_bank.sv"
    synthesize_json([source],top="input_window_bank",output_json=tmp_path/"input.json",repo_root=ROOT)
    subprocess.run(["yosys","-Qq","-p",f'read_liberty "{ROOT.as_posix()}/synth/redstone.lib"; '
                    "read_json input.json; write_verilog -noattr mapped.v"],cwd=tmp_path,check=True)
    vectors(tmp_path/"bank.hex")
    (tmp_path/"test.sv").write_text(BENCH)
    for name,filepath in (("source",source),("mapped",tmp_path/"mapped.v")):
        subprocess.run(["iverilog","-g2012","-s","test","-o",name,str(filepath),"test.sv"],cwd=tmp_path,check=True)
        result=subprocess.run(["vvp",name],cwd=tmp_path,capture_output=True,text=True)
        assert result.returncode==0,result.stdout
        assert "BANK PASS" in result.stdout
