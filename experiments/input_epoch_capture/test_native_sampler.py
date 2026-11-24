"""Two locally clocked production DFFs capture a real matrix A button."""
from dataclasses import replace
from pathlib import Path
import json

from shamaos.hardware.controls import KEYBOARD_OFFSET, control_ports, iter_keyboard_matrix, keyboard_buttons
from shamaos.hardware.logic import TEMPLATES
from shamaos.hardware.memory import DUST, SUPPORT, repeater
from shamaos.hardware.pulse import rotate_block, rotate_point
from shamaos.model import Placement, Vec3
from test_mchprs_runtime import coordinates, run_case


def test_physical_matrix_button_and_both_capture_registers_all_70_phases(tmp_path):
    origin=Vec3(32,16,32)
    keyboard=origin.offset(KEYBOARD_OFFSET.x, KEYBOARD_OFFSET.y, KEYBOARD_OFFSET.z)
    pins=control_ports(origin)
    rd,cd=pins.keyboard_rows[0],pins.keyboard_cols[0]
    matrix=list(iter_keyboard_matrix(keyboard))
    def route(point,block):
        matrix.extend((Placement(point.offset(dy=-1),SUPPORT,"sampler-route"),
                       Placement(point,block,"sampler-route")))
    template=TEMPLATES["DFF"]
    row_origin=rd.offset(0,-1,-13)
    for i in range(1,13):
        route(rd.offset(dz=-i),repeater("north") if i in (1,10) else DUST)
    matrix.extend(template.emitter(row_origin,"row-DFF"))
    col_origin=cd.offset(-10,-1,0)
    for i in range(1,10):
        route(cd.offset(dx=-i),repeater("west") if i in (1,8) else DUST)
    def col_location(local):
        p=rotate_point(local,2)
        return col_origin.offset(p.x,p.y,p.z)
    for cell in template.emitter(Vec3(0,0,0),"col-DFF"):
        matrix.append(replace(cell,pos=col_location(cell.pos),block=rotate_block(cell.block,2)))
    outputs={"row_source":rd,"col_source":cd}
    clocks={}
    physical_clock_pins={}
    for name,pin in template.pins.items():
        row=row_origin.offset(pin.x,pin.y,pin.z)
        col=col_location(pin)
        outputs.update({f"row_{name}":row,f"col_{name}":col})
        if name=="C":
            physical_clock_pins.update(row_C=row,col_C=col)
            # The native harness powers the neighbour SOUTH of the declared
            # drive reference. The column register is rotated 180 degrees.
            clocks.update(row_C=row,col_C=col.offset(dz=-2))
    occupied={placement.pos for placement in matrix}
    for name,reference in clocks.items():
        pad=reference.offset(dz=1)
        assert pad not in occupied
        adjacent={pad.offset(x,y,z) for x,y,z in ((1,0,0),(-1,0,0),(0,1,0),(0,-1,0),(0,0,1),(0,0,-1))}
        assert occupied & adjacent == {physical_clock_pins[name]}, name
    button=keyboard_buttons(origin)[0][0]
    stride=420
    switches={}
    for tick in range(0,stride*70,35):
        switches[tick]={"drive":dict.fromkeys(clocks,bool(tick//35%2))}
    for phase in range(70):
        switches.setdefault(phase*stride+70+phase,{})["use_blocks"]=[coordinates(button)]
    times=sorted(switches)
    steps=[switches[t]|{"ticks":(times[i+1] if i+1<len(times) else stride*70)-t} for i,t in enumerate(times)]
    result=run_case(matrix,clocks,outputs,steps,initial_ticks=128,capture_trace=True,timeout=90)
    traces=result["traces"]
    expected_clock=[bool(tick//35%2) for tick in range(stride*70)]
    assert traces["row_C"]==traces["col_C"]==expected_clock
    summaries=[]
    aperture_counts=[]
    for phase in range(70):
        start=phase*stride
        t={name:data[start:start+stride] for name,data in traces.items()}
        assert sum(t["row_source"])==sum(t["col_source"])==119,phase
        assert sum(t["row_D"])==sum(t["col_D"])==119,phase
        overlap=sum(r and c for r,c in zip(t["row_D"],t["col_D"]))
        captured=[a and b for a,b in zip(t["row_Q"],t["col_Q"])]
        assert not any(captured[:70+phase]) and not captured[-1] and any(captured),phase
        assert sum(v and not prev for prev,v in zip([False]+captured,captured))==1,phase
        # The runner samples after each tick. For every actual C rising
        # edge with valid D setup/hold on both rows, require both Qs high
        # by the measured local clock-to-Q time.
        rising=[tick for tick in range(1,stride) if t["row_C"][tick] and not t["row_C"][tick-1]]
        stable=[tick for tick in rising if tick>=8 and tick+7<stride and
                all(t["row_D"][tick-8:tick+3]) and all(t["col_D"][tick-8:tick+3])]
        assert stable and all(t["row_Q"][tick+7] and t["col_Q"][tick+7] for tick in stable),phase
        aperture_counts.append(len(stable))
        summaries.append((overlap,sum(captured)))
    (tmp_path/"result.json").write_text(json.dumps({
        "revision":result["revision"],"placement_operations":len(matrix),"phases":70,
        "row_and_col_width_rt":119,"minimum_simultaneous_data_rt":min(v[0] for v in summaries),
        "minimum_simultaneous_q_rt":min(v[1] for v in summaries),"initial_settle_rt":128,
        "setup_rt":8,"hold_rt":2,"clock_to_q_rt":7,
        "minimum_eligible_edges_per_phase":min(aperture_counts),
        "maximum_eligible_edges_per_phase":max(aperture_counts),
        "ticks":result["ticks"],"clock_scope":"External empty single-neighbor drive pads. Actual unmodified C pins traced every tick against 35/35."},indent=2)+"\n")
