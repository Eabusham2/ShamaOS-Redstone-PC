"""An exterior facade must clear real circuit state, not just boxes."""
import importlib.util
from pathlib import Path
import sys

import pytest

from shamaos.hardware.memory import DUST, SUPPORT, redstone_wire
from shamaos.model import Placement, Vec3

PATH = Path(__file__).resolve().parents[1] / "experiments/partition_placement/audit_port_egress.py"
SPEC = importlib.util.spec_from_file_location("port_egress_audit", PATH)
AUDIT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = AUDIT
SPEC.loader.exec_module(AUDIT)


def block(point, state=DUST):
    return Placement(point, state, "test")


def corridor(entity=1, x=0, y=-59, z=0):
    return AUDIT.Corridor(entity, "input", Vec3(x, y, z), Vec3(x, y, 30))


def test_clear_corridor_retains_original_leaf_pin_and_internal_wire():
    selected = corridor()
    original = [block(selected.start), block(selected.start.offset(dz=-1)),
                block(selected.start.offset(dy=-1), SUPPORT)]
    report = AUDIT.audit_south_corridors((selected,), iter(original))
    assert report["status"] == "clearance-passed-runtime-unverified"
    assert report["scanned_emitted_blocks"] == 3
    assert report["runtime_verified"] is False
    assert report["exterior_ports"] == {"1": {"x": 0, "y": -59, "z": 30}}


@pytest.mark.parametrize("obstruction", [
    (Vec3(0, -59, 10), SUPPORT),
    (Vec3(0, -60, 10), DUST),
    (Vec3(1, -59, 10), DUST),
    (Vec3(0, -58, 10), DUST),
    (Vec3(0, -61, 10), redstone_wire(north="up")),
])
def test_actual_collision_coupling_or_climb_obstruction_blocks_egress(obstruction):
    selected = corridor()
    result = AUDIT.audit_south_corridors((selected,), [block(selected.start), block(*obstruction)])
    assert result["status"] == "blocked"
    assert result["blocked_ports"] == 1


def test_different_port_corridors_may_not_merge():
    first, second = corridor(), corridor(2, z=5)
    result = AUDIT.audit_south_corridors((first, second), [block(first.start), block(second.start)])
    assert result["blocked_ports"] == 2
    assert "electrical adjacency" in result["conflicts"]["1"][0]["reason"]


def test_missing_original_terminal_is_not_a_clearance_pass():
    assert AUDIT.audit_south_corridors((corridor(),), []) ["status"] == "blocked"


def test_exterior_map_is_separate_and_inventory_is_exact():
    pins = {1: Vec3(2, -59, 8)}
    entities = [{"id": 1, "kind": "port", "direction": "output"}]
    result = AUDIT.south_corridors(entities, pins, 100)
    assert pins == {1: Vec3(2, -59, 8)}
    assert result[0].end == Vec3(2, -59, 116)
    with pytest.raises(ValueError, match="inventory"):
        AUDIT.south_corridors(entities, {}, 100)
