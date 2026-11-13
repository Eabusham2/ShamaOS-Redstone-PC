"""Actual facade paths must survive native save/reopen with all traces intact."""
from pathlib import Path
import json

import pytest

from shamaos.hardware.partition_placement import _literal_states
from shamaos.mchprs_import import export_world
from shamaos.model import Vec3
from shamaos.worldio import NativeWorldReader, NativeWorldWriter
from test_mchprs_runtime import west_driver
from test_mchprs_sparse_runtime import _compare_dense, _run_sparse, _runner
from test_partition_placement import analyze_pipeline_trace, partition_pipeline_case
from test_port_egress_placement import facade_pipeline


@pytest.mark.parametrize("half_period, should_pass", [(512, True), (20, False)])
def test_saved_facade_matches_dense_engine_at_every_original_and_handoff_pin(
        facade_pipeline, tmp_path, half_period, should_pass):
    graph, plan, placed = facade_pipeline
    _, dense_inputs, dense_outputs, steps, expected, delta = partition_pipeline_case(
        graph, plan, placed, half_period, verify_outputs=should_pass)
    native = lambda point: point.offset(-delta.x, -delta.y, -delta.z)
    inputs = {name: native(point) for name, point in dense_inputs.items()}
    outputs = {name: native(point) for name, point in dense_outputs.items()}
    entities = {entity["id"]: entity for entity in graph.entities}
    for entity, point in placed.exposed_ports.items():
        record = entities[entity]
        name = record["port_name"]
        if name == "q":
            name += str(record["port_index"])
        (inputs if record["direction"] == "input" else outputs)[name] = west_driver(point) if record["direction"] == "input" else point
        outputs[f"original-pin:{entity}"] = placed.ports[entity]
    for key, record in placed.facade_contacts.items():
        outputs[f"io-handoff:{key[0]}:{key[1]}"] = record["position"]
    world = tmp_path / "world"
    with NativeWorldWriter(world, surface_y=-61, spawn=Vec3(-16, -59, -16), spawn_platform=False) as writer:
        for block in placed.iter_placements():
            writer.set_block(block)
        assert writer.validate()["status"] == "passed"
    reader = NativeWorldReader(world)
    original_states = _literal_states(placed.iter_placements())
    for point, state in original_states.items():
        assert reader.get_block(point) == state
    encode = lambda point: [point.x, point.y, point.z]
    scenario = {"inputs": {name: encode(point) for name, point in inputs.items()},
                "outputs": {name: encode(point) for name, point in outputs.items()},
                "initial_ticks": 1024, "steps": steps, "capture_trace": True}
    stream = tmp_path / "world.jsonl"
    manifest = export_world(world, stream)
    sparse = _run_sparse(_runner("SHAMA_MCHPRS_TERRAIN_RUNNER"), stream, scenario, tmp_path, limit=100000)
    compared = _compare_dense(world, scenario, sparse, runner=_runner("SHAMA_MCHPRS_DENSE_TERRAIN_RUNNER"))
    report = analyze_pipeline_trace(sparse, expected, half_period)
    assert bool(report["pipeline_failures"]) != should_pass
    report.update(status="saved-facade-native-and-dense-traces-match", compared_trace_samples=compared,
                  exact_saved_circuit_blocks=len(original_states), imported_chunks=manifest["chunks"],
                  imported_stream_sha256=sparse["imported_stream_sha256"], scenario_sha256=sparse["scenario_sha256"],
                  original_port_bits=len(placed.ports), facade_handoffs=len(placed.facade_contacts),
                  complete_machine_runtime_verified=False)
    (tmp_path / "saved-facade-runtime.json").write_text(json.dumps(report, indent=2) + "\n")
