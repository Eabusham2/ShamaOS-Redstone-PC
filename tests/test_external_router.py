from shamaos.hardware.external_router import ExternalNet, iter_external_router
from shamaos.model import Vec3


def test_external_router_handles_stacked_endpoints_and_ceiling():
    src0=Vec3(0,64,0)
    src1=Vec3(4,64,0)

    # Two sink stacks deliberately share X/Z but not Y, like display row ports.
    a0=Vec3(100,-41,1000)
    a1=Vec3(100,-39,1000)
    b0=Vec3(120,67,2000)
    b1=Vec3(120,69,2000)

    nets=[
        ExternalNet("n0",src0,(a0,b0)),
        ExternalNet("n1",src1,(a1,b1)),
    ]
    placements=list(iter_external_router(nets))
    assert placements
    assert max(p.pos.y for p in placements) <= 318
    assert min(p.pos.y for p in placements) >= -64
    assert any(p.block.name=="minecraft:repeater" for p in placements)


def test_external_router_tracks_are_electrically_separated():
    nets=[
        ExternalNet("a",Vec3(0,64,0),(Vec3(100,64,100),)),
        ExternalNet("b",Vec3(4,64,0),(Vec3(104,64,100),)),
        ExternalNet("c",Vec3(8,64,0),(Vec3(108,64,100),)),
    ]
    placements=list(iter_external_router(nets))
    # At the trunk plane every signal owns one unique Z track.
    trunk_dust={
        (p.pos.x,p.pos.z)
        for p in placements
        if p.pos.y==318 and p.block.name in {"minecraft:redstone_wire","minecraft:repeater"}
    }
    zs={z for _,z in trunk_dust}
    assert len(zs) >= 3
