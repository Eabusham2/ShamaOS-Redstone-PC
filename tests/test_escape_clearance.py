"""An indexed prefix search must preserve the literal electrical routes."""
from dataclasses import asdict
from pathlib import Path

import pytest

from experiments.partition_planner.audit_prefix_precheck import cases, load_module
from shamaos.hardware import external_router
from shamaos.model import Vec3


@pytest.fixture(scope="module")
def reference():
    root = Path(__file__).resolve().parents[1]
    return load_module("shamaos.hardware._baseline_escape_clearance",
        root / "experiments/partition_placement/prefix_precheck/baseline_external_router.py")


@pytest.mark.parametrize("args,kwargs", [pytest.param(args, kwargs, id=name)
    for name, args, kwargs, _, _ in cases()])
def test_retained_clearance_cases_keep_original_paths_or_rejection(reference, args, kwargs):
    def solve(router):
        try:
            return {p: asdict(e) for p, e in router._plan_escapes(*args, **kwargs).items()}
        except ValueError as error:
            return str(error)
    assert solve(external_router) == solve(reference)


@pytest.mark.parametrize("height", [-65, -64, -1, 0, 63, 64, 319])
def test_wire_column_sections_include_exact_electrical_neighbors(height):
    point = Vec3(-17, height, 13)
    columns = external_router._WireColumns([point])
    allowed = set(external_router._nearby_wire_points(point))
    for dx in range(-2, 3):
        for dz in range(-2, 3):
            for dy in range(-2, 3):
                other = point.offset(dx, dy, dz)
                assert columns.nearby(other) == (other in allowed)
    columns.discard(point)
    assert not any(columns.nearby(q) for q in allowed)
