from dataclasses import replace
import pytest

from shamaos.layout import MachineGeometry, default_origins, memory_specs, plan_machine, vram_spec
from shamaos.model import Vec3


def test_default_floorplan_has_no_overlaps():
    plan = plan_machine(MachineGeometry(
        origin=Vec3(0, 8, 0),
        ram_bytes=192 << 10,
        cache_bytes=8 << 10,
        flash_bytes=500 << 10,
        display_width=320,
        display_height=180,
    ))
    names = {c.name for c in plan.components}
    assert {"cpu","cache","ram","flash","gpu","input","display"} <= names


@pytest.mark.parametrize("surface,base,layers", [(-61, -56, 30), (63, 68, 20)])
def test_auto_stacking_preserves_surface_and_routing_canopy(surface, base, layers):
    geometry = MachineGeometry(Vec3(0, surface + 5, 0), 192 << 10, 8 << 10, 500 << 10, 320, 180, surface_y=surface)
    assert geometry.storage_origin_y == base
    assert geometry.resolved_memory_layers == layers
    origins = default_origins(geometry)
    fabrics = (*memory_specs(geometry), vram_spec(geometry))
    for fabric, origin in zip(fabrics, (origins.cache, origins.ram, origins.flash, origins.vram)):
        bounds = fabric.bounds(origin)
        assert bounds.min.y == surface + 1
        assert bounds.max.y <= 307
    assert [fabric.bank_count for fabric in fabrics] == [2, 48, 125, 4]
    assert [fabric.layers for fabric in fabrics] == [2, layers, layers, 4]
    with pytest.raises(ValueError, match="above-ground"):
        replace(geometry, memory_layers=layers + 1)
    with pytest.raises(ValueError, match="below the grass"):
        replace(geometry, memory_base_y=surface + 4)
    plan_machine(geometry)
