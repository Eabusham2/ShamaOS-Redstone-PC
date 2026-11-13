from collections import deque

import pytest

from shamaos.hardware.controls import (
    KEYBOARD_OFFSET, control_actuators, control_bounds, control_collision_bounds, control_interaction_bounds,
    control_interaction_descriptor, control_ports, control_route_access,
    iter_controls, keyboard_buttons,
)
from shamaos.hardware.memory import DUST
from shamaos.model import Vec3


@pytest.mark.parametrize("surface",(-61,63))
def test_all_79_controls_are_reachable_on_one_connected_deck_above_grass(surface):
    origin=Vec3(900,surface+5,-8)
    kb=origin.offset(KEYBOARD_OFFSET.x,KEYBOARD_OFFSET.y,KEYBOARD_OFFSET.z)
    blocks={}
    placements=list(iter_controls(origin))
    for placement in placements:
        assert blocks.setdefault(placement.pos,placement.block)==placement.block
    solid={point for point,state in blocks.items()
           if state.name in ("minecraft:stone","minecraft:target")}
    # A standing player's two-block space is clear above every connected
    # deck position; buttons/levers have no collision box. The slim torch
    # columns occupy the deliberately omitted floor cells between keys.
    deck={kb.offset(x,1,z) for x in range(16) for z in range(-1,21)
          if kb.offset(x,0,z) in solid
          and kb.offset(x,1,z) not in solid and kb.offset(x,2,z) not in solid}
    reached={kb.offset(1,1,-1)}
    pending=deque(reached)
    while pending:
        current=pending.popleft()
        for dx,dz in ((1,0),(-1,0),(0,1),(0,-1)):
            neighbor=current.offset(dx=dx,dz=dz)
            if neighbor in deck and neighbor not in reached:
                reached.add(neighbor);pending.append(neighbor)
    assert reached==deck
    assert kb.offset(7,1,20) in reached
    buttons=[point for row in keyboard_buttons(origin) for point in row]
    actuators=[*buttons,*control_actuators(origin).values()]
    assert len(set(actuators))==79
    assert all(Vec3(point.x,kb.y+1,point.z) in reached for point in actuators)
    for distance in range(1,5):
        feet=kb.offset(1,1-distance,-1-distance)
        assert feet.offset(dy=-1) in solid
        assert feet not in solid and feet.offset(dy=1) not in solid
    envelope=control_bounds(origin)
    assert envelope.min==Vec3(min(p.x for p in blocks),min(p.y for p in blocks),min(p.z for p in blocks))
    assert envelope.max==Vec3(max(p.x for p in blocks),max(p.y for p in blocks),max(p.z for p in blocks))
    assert envelope.min.y==surface+1
    collision_boxes=control_collision_bounds(origin)
    assert all(any(all(getattr(box.min,axis)<=getattr(p,axis)<=getattr(box.max,axis)
                       for axis in ("x","y","z")) for box in collision_boxes) for p in blocks)
    interaction=control_interaction_bounds(origin)
    assert interaction.size.x==interaction.size.z==16
    assert interaction.min==Vec3(min(p.x for p in actuators),min(p.y for p in actuators),min(p.z for p in actuators))
    assert interaction.max==Vec3(max(p.x for p in actuators),max(p.y for p in actuators),max(p.z for p in actuators))
    descriptor=control_interaction_descriptor(origin)
    assert descriptor["activation_count"]==79
    assert descriptor["stairs_lowest_support_y"]==surface+1
    assert len({(point.x//16,point.z//16) for point in blocks})==47


def test_exported_console_ports_and_access_contract_match_literal_backend():
    origin=Vec3(900,-56,-8)
    blocks={p.pos:p.block for p in iter_controls(origin)}
    ports=control_ports(origin)
    pins=(*ports.controller,*ports.keyboard_rows,*ports.keyboard_cols,
          ports.power,ports.reset,ports.pause,ports.step)
    assert len(set(pins))==30
    assert all(blocks[pin]==DUST for pin in pins)
    assert set(control_route_access(origin))==set(pins)
