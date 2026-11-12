"""Bounded routing proofs; these do not certify or replace full-chip routing."""
from dataclasses import replace
from functools import lru_cache
import json
from pathlib import Path
import random
import shutil

import pytest

from shamaos.hardware.local_router import compact_tile, stack_tiles
from shamaos.hardware.logic import template_for
from shamaos.model import Vec3
from shamaos.synthesis import build_physical_netlist, synthesize_json
from test_mchprs_runtime import high_runs, run_case, west_driver


@pytest.fixture(scope="module")
def pipeline(tmp_path_factory):
    yosys=shutil.which("yosys") or shutil.which("yowasp-yosys")
    if not yosys:
        pytest.skip("real Yosys is required for the mapped local routing proof")
    directory=tmp_path_factory.mktemp("local-pipeline")
    source=directory/"pipeline.sv"
    source.write_text("""module local_pipeline(input wire clk, rst, en, d, output reg [1:0] q);
always @(posedge clk) begin
    if (rst) q <= 2'b00;
    else if (en) q <= {q[0], d};
end
endmodule
""")
    mapped=synthesize_json([source],top="local_pipeline",output_json=directory/"pipeline.json",yosys=yosys)
    return build_physical_netlist(mapped,top="local_pipeline",origin=Vec3(128,32,1000))


def register_groups(physical):
    """Split this fixture's two register cones, stopping at other registers."""
    fanin={name:[] for name in physical.cell_types}
    for net in physical.nets:
        if net.source.name in fanin:
            for sink in net.sinks:
                if sink.name in fanin:
                    fanin[sink.name].append(net.source.name)
    registers={name for name,kind in physical.cell_types.items() if template_for(kind).cell_type=="DFF"}
    roots=[next(net.source.name for net in physical.nets if any(sink.pos==pin for sink in net.sinks))
           for pin in physical.output_ports["q"]]
    owned=set();groups=[]
    for root in roots:
        members=set();pending=[root]
        while pending:
            name=pending.pop()
            if name in owned or name in members or (name in registers and name!=root):
                continue
            members.add(name);pending.extend(fanin[name])
        owned.update(members);groups.append(members)
    assert owned==set(physical.cell_types)
    return groups


@pytest.fixture(scope="module")
def local_pipeline(pipeline):
    tile=compact_tile(pipeline)
    tile.balance_clock_routes()
    return tile


@pytest.fixture(scope="module")
def stacked_pipeline(pipeline):
    return stack_tiles(pipeline,register_groups(pipeline))


def test_local_tracks_are_emitted_reuse_and_clock_matching_keeps_the_footprint(pipeline):
    tile=compact_tile(pipeline)
    original=list(tile.iter_placements())
    tile.balance_clock_routes()
    balanced=list(tile.iter_placements())
    report=tile.report(balanced)
    assert report["reused_tracks"]>0
    assert report["tracks"]<report["nets"]
    assert report["clock_repeater_overrides"]>0
    assert len(original)==len(balanced)
    assert {p.pos for p in original}=={p.pos for p in balanced}
    # Replanning the same clock must not cumulatively add delay. An impossible
    # tail match must not corrupt a previously usable plan either.
    old_overrides=dict(tile.overrides)
    tile.balance_clock_routes()
    assert tile.overrides==old_overrides
    clock=next(net for net in tile.physical.nets if net.bit in tile.clock_delays)
    clocks=[sink for sink in clock.sinks if sink.pin=="C"]
    with pytest.raises(ValueError,match="private branch capacity"):
        tile.balance_net(clock.bit,{sink.pos:(100000 if index==0 else 0) for index,sink in enumerate(clocks)})
    assert tile.overrides==old_overrides


def test_bounded_router_rejects_oversized_incomplete_and_overheight_partitions(pipeline):
    with pytest.raises(ValueError,match="cell budget"):
        compact_tile(pipeline,max_cells=1)
    groups=register_groups(pipeline)
    with pytest.raises(ValueError,match="exactly once"):
        stack_tiles(pipeline,[*groups,groups[0]])
    with pytest.raises(ValueError,match="exactly once"):
        stack_tiles(pipeline,groups[:1])
    with pytest.raises(ValueError,match="build height"):
        stack_tiles(replace(pipeline,origin=Vec3(128,300,1000)),groups)


def pipeline_trace(original,route,half_period):
    hierarchical=hasattr(route,"children")
    local_tiles=route.children if hierarchical else (route,)
    physical=route.parent.physical if hierarchical else route.physical
    blocks=list(route.iter_placements())
    inputs={name:west_driver(pins[0]) for name,pins in physical.input_ports.items()}
    outputs={f"q{index}":pin for index,pin in enumerate(physical.output_ports["q"])}
    registers=[]
    for child in local_tiles:
        for name,origin in child.physical.cell_origins.items():
            template=template_for(child.physical.cell_types[name])
            if template.cell_type=="DFF":
                registers.append(name)
                for pin,offset in template.pins.items():
                    outputs[f"{name}:{pin}"]=origin.offset(offset.x,offset.y,offset.z)
    q_drivers={index:next(net.source.name for net in original.nets if any(sink.pos==pin for sink in net.sinks))
               for index,pin in enumerate(original.output_ports["q"])}
    assert len(registers)==2
    cases=[(True,False,False,2),(False,True,True,4),(False,True,False,4),
           (False,False,True,3),(False,True,True,4),(True,False,True,3)]
    steps=[];expected=[];state=[False,False]
    for rst,en,data,cycles in cases:
        steps.append({"drive":{"clk":False,"rst":rst,"en":en,"d":data},"ticks":1000})
        for _ in range(cycles):
            if rst:
                state=[False,False]
            elif en:
                state=[data,state[0]]
            expected.append(state[:])
            steps.extend([{"drive":{"clk":True},"ticks":half_period},
                          {"drive":{"clk":False},"ticks":half_period}])
        steps.append({"ticks":1000,"expect":{f"q{index}":value for index,value in enumerate(state)}})
    result=run_case(blocks,inputs,outputs,steps,initial_ticks=256,capture_trace=True)
    clocks=[result["traces"][f"{name}:C"] for name in registers]
    assert all(trace==clocks[0] for trace in clocks),"register clock pins have real scheduled-engine skew"
    edges=[index for index,value in enumerate(clocks[0]) if value and (not index or not clocks[0][index-1])]
    assert len(edges)==len(expected)
    assert set(high_runs(clocks[0]))=={half_period}
    failures=[];setup=[];hold=[]
    for edge,wanted in zip(edges,expected):
        for index,name in q_drivers.items():
            trace=result["traces"][f"{name}:Q"]
            data=result["traces"][f"{name}:D"]
            # Observe the local register after its tested 12-RT transfer window;
            # a slow final-output check alone can miss a lost pipeline cycle.
            if trace[edge+12]!=wanted[index]:
                failures.append((edge,index,wanted[index],trace[edge+12]))
            setup.append(next((delta for delta in range(1,edge+1) if data[edge-delta]!=data[edge]),edge))
            hold.append(next((delta for delta in range(1,len(data)-edge) if data[edge+delta]!=data[edge]),len(data)-edge))
    return route.report() | {
        "half_period_redstone_ticks":half_period,"cycles":len(edges),"actual_ticks":result["ticks"],
        "clock_skew_ticks":0,"minimum_observed_setup_ticks":min(setup),
        "minimum_observed_hold_ticks":min(hold),"pipeline_failures":failures,
    }


@pytest.mark.parametrize("stacked,half_period,should_pass",[
    (False,70,True),(False,35,False),(True,90,True),(True,85,False),
])
def test_actual_local_and_stacked_pipeline_timing(
    pipeline,local_pipeline,stacked_pipeline,tmp_path,stacked,half_period,should_pass,
):
    report=pipeline_trace(pipeline,stacked_pipeline if stacked else local_pipeline,half_period)
    (tmp_path/"routing-proof.json").write_text(json.dumps(report,indent=2))
    if should_pass:
        assert not report["pipeline_failures"],report
        assert report["minimum_observed_setup_ticks"]>=template_for("DFF").setup_ticks,report
        assert report["minimum_observed_hold_ticks"]>=template_for("DFF").hold_ticks,report
    else:
        # A deliberate speed-up must expose a lost register transfer. This is
        # the negative control that keeps the timing proof from becoming a
        # mere static truth-table or a final settled-output check.
        assert report["pipeline_failures"],report


def test_actual_local_routing_of_a_64_cell_cut_from_the_mapped_assembler(tmp_path):
    source=Path(__file__).parent/"fixtures/asm_dma_data_cut.json"
    mapped=json.loads(source.read_text())
    module=mapped["modules"]["dma_data_bit0"]
    cells=module["cells"]
    assert len(cells)==64
    physical=build_physical_netlist(source,top="dma_data_bit0",origin=Vec3(128,32,1000))
    tile=compact_tile(physical)
    by_bit={bit:cell for cell in cells.values() for pin,bits in cell["connections"].items()
            if cell["port_directions"][pin]=="output" for bit in bits}
    input_bits=sorted(port["bits"][0] for port in module["ports"].values() if port["direction"]=="input")
    rng=random.Random(7919)
    vectors=[{bit:value for bit in input_bits} for value in (False,True)]
    vectors.extend({bit:bool(rng.getrandbits(1)) for bit in input_bits} for _ in range(22))
    steps=[]
    for vector in vectors:
        @lru_cache(None)
        def evaluate(bit):
            if not isinstance(bit,int):
                assert bit in ("0","1")
                return bit=="1"
            if bit in vector:
                return vector[bit]
            cell=by_bit[bit]
            a=evaluate(cell["connections"]["A"][0])
            if cell["type"]=="$_NOT_":
                return not a
            assert cell["type"]=="$_AND_"
            return a and evaluate(cell["connections"]["B"][0])
        steps.append({
            "drive":{name:vector[port["bits"][0]] for name,port in module["ports"].items() if port["direction"]=="input"},
            "ticks":1024,
            "expect":{name:evaluate(port["bits"][0]) for name,port in module["ports"].items() if port["direction"]=="output"},
        })
    result=run_case(tile.iter_placements(),
        {name:west_driver(pins[0]) for name,pins in tile.physical.input_ports.items()},
        {name:pins[0] for name,pins in tile.physical.output_ports.items()},steps,initial_ticks=256)
    report=tile.report() | {"provenance":mapped["proof_source"],"actual_runtime":result}
    (tmp_path/"assembler-cut-proof.json").write_text(json.dumps(report,indent=2))
