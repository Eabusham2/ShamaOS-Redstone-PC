from shamaos.layout import MachineGeometry, plan_machine
from shamaos.model import Vec3


def test_default_floorplan_has_no_overlaps():
    plan = plan_machine(MachineGeometry(
        origin=Vec3(0, 8, 0),
        ram_bytes=1 << 20,
        cache_bytes=16 << 10,
        flash_bytes=1 << 20,
        display_width=320,
        display_height=180,
    ))
    names = {c.name for c in plan.components}
    assert {"cpu","cache","ram","flash","gpu","input","display"} <= names
