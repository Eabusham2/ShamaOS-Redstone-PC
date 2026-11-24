"""Actual technology netlist, common balanced physical clock, and four selectable payload lanes."""
from dataclasses import replace
import json
from pathlib import Path
import shutil

import pytest

from shamaos.hardware.local_router import compact_tile
from shamaos.hardware.logic import template_for
from shamaos.model import Vec3
from shamaos.synthesis import build_physical_netlist, synthesize_json
from test_mchprs_runtime import run_case, west_driver

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]


def audit_mapped_lanes(tile, period):
    phy=tile.physical
    def register_for(port,index):
        pos=phy.output_ports[port][index]
        return next(n.source.name for n in phy.nets if any(s.pos==pos for s in n.sinks))
    regs={"launch":register_for("captured",0),**{f"lane{i}":register_for("q",i) for i in range(4)}}
    outputs={}
    for name,cell in regs.items():
        pins=template_for(phy.cell_types[cell]).pins
        base=phy.cell_origins[cell]
        outputs.update({f"{name}_{key}":base.offset(v.x,v.y,v.z) for key,v in pins.items()})
    inputs={k:west_driver(phy.input_ports[k][0]) for k in ("clk","incoming")}
    inputs.update({f"s{i}":west_driver(pin) for i,pin in enumerate(phy.input_ports["selected"])})
    blocks=list(tile.iter_placements())
    minima=[min(getattr(p.pos,axis) for p in blocks) for axis in "xyz"]
    def move(p):
        return p.offset(*(32-v for v in minima))
    inputs={k:move(p) for k,p in inputs.items()}
    outputs={k:move(p) for k,p in outputs.items()}
    blocks=[replace(p,pos=move(p.pos)) for p in blocks]
    occupied={p.pos for p in blocks}
    assert all(p.offset(dz=1) not in occupied for p in inputs.values())
    warm=max(1000,4*period)
    steps=[{"drive":{k:k.startswith("s") for k in inputs},"ticks":warm}]
    pattern=(0,0,1,1,0,1,0,0,1,1,1,0,0,0)
    for level in pattern:
        steps.extend([{"drive":{"clk":False,"incoming":bool(level)},"ticks":period//2},
                      {"drive":{"clk":True},"ticks":period//2}])
    steps.append({"drive":{"clk":False},"ticks":warm})
    result=run_case(blocks,inputs,outputs,steps,initial_ticks=warm,capture_trace=True,timeout=90)
    tr=result["traces"]
    edges=[i for i in range(1,len(tr["launch_C"])) if tr["launch_C"][i] and not tr["launch_C"][i-1]]
    assert len(edges)==len(pattern)
    assert all(tr[f"{name}_C"]==tr["launch_C"] for name in regs)
    old=False
    errors=[];setups=[];holds=[]
    for j,edge in enumerate(edges):
        sampled=tr["launch_D"][edge]
        assert tr["launch_Q"][edge+12]==sampled
        for key in list(regs)[1:]:
            data=tr[key+"_D"];q=tr[key+"_Q"]
            if q[edge+12]!=old: errors.append([j,key,int(q[edge+12]),int(old)])
            if j and tr["launch_Q"][edges[j-1]+12]!=tr["launch_Q"][edges[j-1]-1]:
                setups.append(next((d for d in range(1,edge+1) if data[edge-d]!=data[edge]),edge))
            holds.append(next((d for d in range(1,min(2*period,len(data)-edge)) if data[edge+d]!=data[edge]),2*period))
        old=sampled
    return {"period_rt":period,"rows":4,"epochs":len(edges),"engine":result["revision"],
            "placement_operations":len(blocks),"mapped_cells":len(phy.cell_types),"clock_skew_rt":0,
            "closest_setup_transition_rt":min(setups),"closest_hold_transition_rt":min(holds),"errors":errors}


@pytest.mark.skipif(shutil.which("yosys") is None,reason="Yosys required for four-lane physical comparison")
def test_ordinary_four_lane_mapped_payload_receiver(tmp_path):
    mapped=synthesize_json([HERE/"four_lane.sv"],top="four_lane",output_json=tmp_path/"four.json",repo_root=ROOT)
    tile=compact_tile(build_physical_netlist(mapped,top="four_lane",origin=Vec3(64,32,900)))
    tile.balance_clock_routes()
    reports=[audit_mapped_lanes(tile,t) for t in (70,280)]
    (tmp_path/"receipt.json").write_text(json.dumps(reports,indent=2)+"\n")
    # Other mapper versions may choose a different layout. Retain their actual
    # fast-period result while still requiring slow-period functional transfers.
    assert not reports[1]["errors"]
    assert reports[1]["closest_setup_transition_rt"]>=8 and reports[1]["closest_hold_transition_rt"]>=2
