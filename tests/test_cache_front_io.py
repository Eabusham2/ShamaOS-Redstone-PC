"""The guarded cache's front I/O row must deliver clocks and register data."""
from pathlib import Path

import pytest

from shamaos.hardware.bounded_facade import place_with_egress
from shamaos.hardware.logic import template_for
from shamaos.hardware.partition_placement import _literal_states
from shamaos.hardware.partition_planner import Graph, build_plan
from test_port_egress_saved_world import check_saved_facade


@pytest.fixture(scope="module")
def front_row_pipeline():
    mapped = Path(__file__).resolve().parents[1] / "experiments/partition_placement/raised_exports/evidence/pipeline-mapped.json"
    graph = Graph.read(mapped, "pipeline")
    plan = build_plan(graph, cell_budget=2, boundary_budget=8, domain_budget=5, arity=4, ports_at_root=True)
    placed = place_with_egress(graph, plan, front_io_row=True, local_data_tracks=True)
    assert placed.packing["horizontal_layout"] == "front-io-row"
    return graph, plan, placed


def test_port_leaves_face_the_core_and_shorten_the_same_pipeline(front_row_pipeline):
    graph, plan, placed = front_row_pipeline
    previous = place_with_egress(graph, plan)
    core, *ports = placed.children
    assert all(child.bounds.min.z > core.bounds.max.z for child in ports)
    assert len({child.bounds.max.z for child in ports}) == 1
    assert len({point.z for point in placed.exposed_ports.values()}) == 1
    assert len(_literal_states(placed.iter_placements())) == placed.unique_blocks < previous.unique_blocks


@pytest.mark.parametrize("half_period, succeeds", [(512, True), (20, True), (8, False)])
def test_front_io_pipeline_after_native_save_and_reopen(front_row_pipeline, tmp_path, half_period, succeeds):
    report = check_saved_facade(front_row_pipeline, tmp_path, half_period, succeeds)
    if succeeds:
        register = template_for("DFF")
        assert report["minimum_setup_ticks"] >= register.setup_ticks
        assert report["minimum_hold_ticks"] >= register.hold_ticks
    else:
        assert report["pipeline_failures"]
