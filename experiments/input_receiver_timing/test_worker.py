"""Compare every occupied physical-slot payload in chronological order against the original queue oracle."""
from pathlib import Path
import random
import shutil
import subprocess

import pytest
from shamaos.synthesis import synthesize_json
from test_input_eviction import Queue, KEYS

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]


class LastLine:
    def write(self,line):
        self.result=int(line.split()[1],16)


def vectors(path, depth):
    sink=LastLine()
    reference=Queue(depth,sink)
    rng=random.Random(91817+depth)
    count=0
    with path.open("w") as out:
        def send(rows=0,cols=0,ctl=0,ack=False,payload=0,rst=False):
            nonlocal count
            key=0
            if rows.bit_count()==cols.bit_count()==1 and (rows,cols)!=(reference.prev_rows,reference.prev_cols):
                index=(rows.bit_length()-1)*8 + cols.bit_length()-1
                if index!=63: key=KEYS[64*reference.shift+index]
            edges=ctl & ~reference.prev_controller
            rise=ack and not reference.prev_ack
            reference.edge(reset=rst,rows=rows,cols=cols,controller=ctl,ack=ack,payload=payload)
            pins=(int(rst)<<57|int(rise)<<56|payload<<30|ctl<<20|edges<<10|
                  int(bool(key))<<9|key<<1|int(reference.shift))
            out.write(f"{pins:x} {sink.result:x}\n")
            count+=1

        for pattern in ("ordinary","mixed","urgent"):
            for flags in range(16):
                send(rst=True)
                for i in range(depth):
                    if pattern=="urgent" or (pattern=="mixed" and i&1):send(ctl=1<<(6+i%4))
                    else:send(1,1)
                    send()
                payload=reference.records[(flags*3)%depth] if flags&1 else 0x3feffff
                send(2,8,(flags<<6)|0x23,True,payload)
                send(ack=True,payload=0xdade)
                send()
                while reference.records:
                    send(ack=True,payload=reference.records[-1])
                    send()
        history=[]
        for i in range(900):
            if reference.records:history=(history+[rng.choice(reference.records)])[-12:]
            r,c=rng.choice(((0,0),(1,1),(128,128),(3,3),(1<<rng.randrange(8),1<<rng.randrange(8))))
            send(r,c,rng.randrange(1024),i%7<3,rng.choice([0x3ffffff,*history]),rst=i%113==0)
    return count


BENCH=r"""
module test;
 parameter DEPTH=16;
 localparam IDX=DEPTH<2?1:$clog2(DEPTH);
 reg clk=0,rst=0,cmd_valid=0,resp_ready=0;reg[1:0]cmd;
 reg[25:0]cmd_ack_data;reg[9:0]cmd_levels,cmd_edges;reg cmd_key_valid,cmd_shift;reg[7:0]cmd_key;
 wire cmd_ready,resp_valid;wire[36:0]snapshot;wire mem_valid,mem_write;
 wire[IDX-1:0]mem_addr;wire[25:0]mem_wdata;reg[25:0]ram[0:DEPTH-1];
 integer ticks=0,reads=0,writes=0,commands=0;wire mem_ready=ticks%7!=1 && ticks%7!=2;
 wire[25:0]mem_rdata=ram[mem_addr];
 `ifdef MAPPED
 input_slot_worker dut(.*);
 `else
 input_slot_worker #(.DEPTH(DEPTH)) dut(.*);
 `endif
 wire[IDX-1:0]ordered[0:DEPTH-1];
 `ifdef MAPPED
 `include "probe.svh"
 `else
 for(genvar i=0;i<DEPTH;i=i+1)assign ordered[i]=dut.order[i];
 `endif
 reg stalled=0,was_write;reg[IDX-1:0]prior_addr;reg[25:0]prior_data;
 task tick;
  begin
   #1;
   if(stalled && (!mem_valid || mem_write!==was_write || mem_addr!==prior_addr ||
       (was_write && mem_wdata!==prior_data))) $fatal(1,"unstable memory request");
   stalled=mem_valid && !mem_ready;was_write=mem_write;prior_addr=mem_addr;prior_data=mem_wdata;
   if(mem_valid && mem_ready)begin
    if(mem_write) begin ram[mem_addr]<=mem_wdata;writes=writes+1;end
    else reads=reads+1;
   end
   clk=1;#1;clk=0;#1;ticks=ticks+1;
  end
 endtask
 task issue(input[1:0]op);
  integer duration;
  begin
   if(!cmd_ready)$fatal(1,"worker not idle");
   cmd=op;cmd_valid=1;tick();cmd_valid=0;duration=0;
   while(!resp_valid && duration<32*DEPTH+128)begin tick();duration=duration+1;end
   if(!resp_valid)$fatal(1,"stalled operation %d",op);
   if(commands%11==0)begin tick();if(!resp_valid)$fatal(1,"unaccepted response lost");end
   resp_ready=1;tick();resp_ready=0;commands=commands+1;
  end
 endtask
 reg[57:0]pins;reg[36+26*DEPTH:0]expected;reg reset_pin,ack;integer f,n=0;
 initial begin
  f=$fopen("worker.hex","r");
  while(!$feof(f))begin
   if($fscanf(f,"%h %h\n",pins,expected)!=2)$fatal(1);
   {reset_pin,ack,cmd_ack_data,cmd_levels,cmd_edges,cmd_key_valid,cmd_key,cmd_shift}=pins;
   if(reset_pin)begin rst=1;tick();rst=0;end
   else begin
    if(ack)issue(1);
    if((|cmd_edges)||cmd_key_valid)issue(2);
   end
   issue(0);
   if(snapshot!==expected[36:0])$fatal(1,"public mismatch raw=%d got=%h expected=%h",n,snapshot,expected[36:0]);
   for(integer i=0;i<snapshot[35:28];i=i+1)
    if(ram[ordered[i]]!==expected[37+26*i+:26])$fatal(1,"payload mismatch raw=%d rank=%d",n,i);
   n=n+1;
  end
  if(reads==0 || writes==0)$fatal(1);
  $display("WORKER PASS %0d samples %0d commands %0d reads %0d writes %0d ticks",n,commands,reads,writes,ticks);
  $finish;
 end
endmodule
"""


@pytest.mark.skipif(any(shutil.which(t) is None for t in ("iverilog","vvp","yosys")),reason="HDL tools required")
@pytest.mark.parametrize("depth,mapped",[(1,False),(2,False),(3,False),(4,False),(16,False),(16,True)])
def test_fixed_slot_worker(tmp_path,depth,mapped):
    source=HERE/"input_slot_worker.sv"
    if mapped:
        synthesize_json([source],top="input_slot_worker",output_json=tmp_path/"worker.json",repo_root=ROOT)
        subprocess.run(["yosys","-Qq","-p",f'read_liberty "{ROOT.as_posix()}/synth/redstone.lib"; '
                        "read_json worker.json; write_verilog -noattr mapped.v"],cwd=tmp_path,check=True)
        source=tmp_path/"mapped.v"
        (tmp_path/"probe.svh").write_text("\n".join(
            f"assign ordered[{i}]=dut.\\order[{i}] ;" for i in range(depth))+"\n")
    count=vectors(tmp_path/"worker.hex",depth)
    (tmp_path/"worker_tb.sv").write_text(BENCH)
    subprocess.run(["iverilog","-g2012","-s","test",f"-Ptest.DEPTH={depth}",
                    *(["-DMAPPED"] if mapped else []),"-o","sim",str(source),"worker_tb.sv"],
                   cwd=tmp_path,check=True)
    p=subprocess.run(["vvp","sim"],cwd=tmp_path,capture_output=True,text=True,timeout=180)
    (tmp_path/"simulation.log").write_text(p.stdout+p.stderr)
    assert p.returncode==0,p.stdout+p.stderr
    assert f"WORKER PASS {count} samples" in p.stdout
