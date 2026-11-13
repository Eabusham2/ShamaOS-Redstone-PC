"""Real exterior contacts require actual ancestor wires and a separate audit."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import gzip
import hashlib
import json

import pytest

from experiments.partition_placement.port_egress import audit_egress, place_with_egress
from experiments.partition_placement.port_egress_manifest import read_egress_manifest, write_egress_manifest, restore_egress_manifest
from experiments.partition_placement.port_egress_manifest import REQUIRED_SOURCE_BINDINGS
from experiments.partition_placement.raised_exports.replay_manifest import read_manifest, restore_manifest
from shamaos.hardware.partition_placement import PlacementError, _literal_states, audit_placement
from shamaos.hardware.partition_planner import Graph, build_plan
from shamaos.hardware.logic import template_for
from shamaos.model import Vec3
from test_mchprs_runtime import run_case, west_driver
from test_partition_placement import analyze_pipeline_trace, partition_pipeline_case


@pytest.fixture(scope="module")
def facade_pipeline():
    path = Path(__file__).resolve().parents[1] / "experiments/partition_placement/raised_exports/evidence/pipeline-mapped.json"
    graph = Graph.read(path, "pipeline")
    plan = build_plan(graph, cell_budget=1, boundary_budget=8, domain_budget=5, arity=4)
    return graph, plan, place_with_egress(graph, plan)


def test_original_graph_and_complete_facade_both_survive_exact_union(facade_pipeline):
    graph, plan, placed = facade_pipeline
    assert plan["summary"]["hierarchy_depth"] == 2
    assert audit_placement(graph, plan, placed)["shared_contacts"] == 18
    report = audit_egress(graph, placed)
    assert (report["original_port_bits"], report["facade_modules"], report["directed_handoffs"]) == (6, 6, 11)
    assert len(placed.all_modules()) == 16
    assert len(placed.all_facade_modules()) == 6
    assert len(_literal_states(placed.iter_placements())) == placed.unique_blocks == 26422
    assert set(placed.ports) == set(placed.exposed_ports)
    assert any(placed.ports[key] != placed.exposed_ports[key] for key in placed.ports)
    assert all(point.z == placed.bounds.max.z for point in placed.exposed_ports.values())
    assert report["electrical_route_isolation_verified"] is False
    assert "Original mapped input pin" in report["base_clock_matching_scope"]
    moved = placed.translated(Vec3(-23, 17, -46))
    assert audit_placement(graph, plan, moved)["original_port_bits"] == 6
    assert audit_egress(graph, moved)["directed_handoffs"] == 11
    assert len(_literal_states(moved.iter_placements())) == placed.unique_blocks


def test_extended_manifest_recovers_every_actual_facade_wire(facade_pipeline, tmp_path):
    graph, plan, placed = facade_pipeline
    path = tmp_path / "complete-io.json.gz"
    record = write_egress_manifest(path, graph, plan, placed, provenance={"scope": "test-only"})
    restored = read_egress_manifest(path, record["sha256"], graph, plan)
    assert record["facade_modules"] == 6
    assert record["facade_handoffs"] == 11
    assert restored.exposed_ports == placed.exposed_ports
    assert restored.facade_contacts == placed.facade_contacts
    assert _literal_states(restored.iter_placements()) == _literal_states(placed.iter_placements())
    with pytest.raises(ValueError, match="schema"):
        restore_manifest(read_manifest(path, record["sha256"]))


def _valid_facade_document(facade_pipeline, tmp_path):
    graph, plan, placed = facade_pipeline
    path = tmp_path / "bound-facade.json.gz"
    record = write_egress_manifest(path, graph, plan, placed, provenance={"scope": "test-only"})
    return read_manifest(path, record["sha256"])


@pytest.mark.parametrize("fault", ["changed-hash", "missing-binding", "relative-escape", "absolute-escape", "symlink-escape"])
def test_retained_facade_reader_validates_real_source_bindings(facade_pipeline, tmp_path, fault):
    graph, plan, _ = facade_pipeline
    document = _valid_facade_document(facade_pipeline, tmp_path)
    provenance = document["original_geometry"]["provenance"]
    bindings = provenance["source_hashes"]
    source_root = None
    if fault == "changed-hash":
        bindings[REQUIRED_SOURCE_BINDINGS[0]] = "0" * 64
    elif fault == "missing-binding":
        del bindings[REQUIRED_SOURCE_BINDINGS[0]]
    elif fault == "relative-escape":
        bindings["../outside.py"] = "0" * 64
    elif fault == "absolute-escape":
        bindings[str(tmp_path / "outside.py")] = "0" * 64
    else:
        source_root = tmp_path / "source-root"
        source_root.mkdir()
        external = tmp_path / "outside.py"
        external.write_text("outside the verified source root\n")
        (source_root / "outside.py").symlink_to(external)
        provenance["source_hashes"] = {"outside.py": hashlib.sha256(external.read_bytes()).hexdigest(), **bindings}
    # Digest integrity alone cannot turn wrong generator sources into proof.
    path = tmp_path / "modified-but-correctly-hashed.json.gz"
    encoded = gzip.compress(json.dumps(document).encode(), mtime=0)
    path.write_bytes(encoded)
    options = {} if source_root is None else {"source_root": source_root}
    with pytest.raises(ValueError, match="source binding|source root"):
        read_egress_manifest(path, hashlib.sha256(encoded).hexdigest(), graph, plan, **options)


@pytest.mark.parametrize("fault", ["mapped-identity", "plan-identity", "topology-identity", "nested-extra", "noncanonical-port", "noncanonical-track"])
def test_typed_facade_restore_rejects_false_identity_and_unknown_geometry(facade_pipeline, tmp_path, fault):
    graph, plan, _ = facade_pipeline
    document = _valid_facade_document(facade_pipeline, tmp_path)
    provenance = document["original_geometry"]["provenance"]
    if fault == "mapped-identity":
        provenance["mapped_source"]["sha256"] = "0" * 64
    elif fault == "plan-identity":
        provenance["plan_canonical_sha256"] = "0" * 64
    elif fault == "topology-identity":
        provenance["topology_sha256"] = "0" * 64
    elif fault == "nested-extra":
        document["original_geometry"]["omitted_extra_wires"] = [1]
    else:
        inventory = (next(cluster["exposed_ports"] for cluster in document["facade_clusters"] if cluster["exposed_ports"])
                     if fault == "noncanonical-port" else document["facade_modules"][0]["tracks"])
        key = next(iter(inventory))
        inventory["0" + key] = inventory.pop(key)
    with pytest.raises(ValueError):
        restore_egress_manifest(document, graph, plan)


def test_retained_facade_reader_rejects_duplicate_json_keys(facade_pipeline, tmp_path):
    graph, plan, _ = facade_pipeline
    document = _valid_facade_document(facade_pipeline, tmp_path)
    raw = json.dumps(document)
    repeated = '{"schema":' + json.dumps(document["schema"]) + ',' + raw[1:]
    encoded = gzip.compress(repeated.encode(), mtime=0)
    path = tmp_path / "duplicated-schema.json.gz"
    path.write_bytes(encoded)
    with pytest.raises(ValueError, match="duplicate JSON key"):
        read_egress_manifest(path, hashlib.sha256(encoded).hexdigest(), graph, plan)


@pytest.mark.parametrize("fault", ["extra-element", "boolean-group", "negative-group", "noncontiguous-group"])
def test_inmemory_facade_keys_are_exact(facade_pipeline, fault):
    graph, _, placed = deepcopy(facade_pipeline)
    key = next(iter(placed.facade_modules))
    tile = placed.facade_modules.pop(key)
    bad = {"extra-element": (*key, 123), "boolean-group": (*key[:2], True),
           "negative-group": (*key[:2], -1), "noncontiguous-group": (*key[:2], 100)}[fault]
    placed.facade_modules[bad] = tile
    with pytest.raises(PlacementError, match="module keys"):
        audit_egress(graph, placed)


@pytest.mark.parametrize("fault", ["missing-module", "duplicate-module", "missing-cluster", "missing-contact", "invented-contact", "wrong-owner"])
def test_extended_manifest_cannot_silently_drop_or_invent_facade_wires(facade_pipeline, tmp_path, fault):
    graph, plan, placed = facade_pipeline
    path = tmp_path / "complete-io.json.gz"
    record = write_egress_manifest(path, graph, plan, placed, provenance={"scope": "test-only"})
    document = read_manifest(path, record["sha256"])
    if fault == "missing-module":
        document["facade_modules"].pop()
    elif fault == "duplicate-module":
        document["facade_modules"].append(deepcopy(document["facade_modules"][0]))
    elif fault == "missing-cluster":
        document["facade_clusters"].pop()
    elif fault == "missing-contact":
        document["facade_contacts"].pop()
    elif fault == "invented-contact":
        extra = deepcopy(document["facade_contacts"][0])
        extra["key"] = [999, extra["entity"]]
        extra["parent_cluster"] = 999
        document["facade_contacts"].append(extra)
    else:
        owner = next(cluster for cluster in document["facade_clusters"] if cluster["own_module_keys"])
        owner["own_module_keys"][0][1] = 999
    with pytest.raises(ValueError):
        restore_egress_manifest(document, graph, plan)


@pytest.mark.parametrize("fault", ["outer-map", "missing-facade", "reversed-wire", "renamed-wire", "contact", "face", "constant", "leaf-moved"])
def test_facade_audit_rejects_lost_or_fake_delivery(facade_pipeline, fault):
    graph, _, original = facade_pipeline
    placed = deepcopy(original)
    tile = next(iter(placed.facade_modules.values()))
    net = tile.physical.nets[0]
    if fault == "outer-map":
        placed.exposed_ports[net.bit] = placed.exposed_ports[net.bit].offset(dz=-4)
    elif fault == "missing-facade":
        placed.facade_modules.pop(next(iter(placed.facade_modules)))
    elif fault == "reversed-wire":
        tile.physical = replace(tile.physical, nets=(replace(net, source=net.sinks[0], sinks=(net.source,)), *tile.physical.nets[1:]))
    elif fault == "renamed-wire":
        tile.physical = replace(tile.physical, nets=(replace(net, source=replace(net.source, name="invented")), *tile.physical.nets[1:]))
    elif fault == "contact":
        next(iter(placed.facade_contacts.values()))["entity"] = -1
    elif fault == "face":
        point = net.source.pos
        escape = tile.escapes[point]
        tile.escapes[point] = replace(escape, escape_z=point.z - (escape.escape_z - point.z))
    elif fault == "constant":
        tile.physical = replace(tile.physical, constant_high=(net.source.pos,))
    else:
        pending = [placed]
        while pending:
            item = pending.pop()
            if not item.children and item.ports:
                entity = next(iter(item.ports))
                item.exposed_ports[entity] = item.exposed_ports[entity].offset(dx=1)
                break
            pending.extend(item.children)
    with pytest.raises(PlacementError):
        audit_egress(graph, placed)


@pytest.mark.parametrize("half_period, should_pass", [(512, True), (20, False)])
def test_actual_exterior_io_pipeline_and_too_fast_negative(facade_pipeline, half_period, should_pass, tmp_path):
    graph, plan, placed = facade_pipeline
    translated, inputs, outputs, steps, expected, delta = partition_pipeline_case(
        graph, plan, placed, half_period, verify_outputs=should_pass)
    entities = {entity["id"]: entity for entity in graph.entities}
    for entity, point in translated.exposed_ports.items():
        record = entities[entity]
        name = record["port_name"]
        if name == "q":
            name += str(record["port_index"])
        (inputs if record["direction"] == "input" else outputs)[name] = west_driver(point) if record["direction"] == "input" else point
        outputs[f"original-pin:{entity}"] = translated.ports[entity]
    for key, contact in translated.facade_contacts.items():
        outputs[f"io-handoff:{key[0]}:{key[1]}"] = contact["position"]
    result = run_case(translated.iter_placements(), inputs, outputs, steps,
                      initial_ticks=1024, capture_trace=True)
    report = analyze_pipeline_trace(result, expected, half_period)
    report.update(topology_sha256=plan["summary"]["topology_sha256"],
                  exterior_original_ports=len(translated.exposed_ports),
                  facade=audit_egress(graph, translated))
    (tmp_path / "exterior-io-runtime.json").write_text(json.dumps(report, indent=2))
    if should_pass:
        assert not report["pipeline_failures"]
        assert report["minimum_setup_ticks"] >= template_for("DFF").setup_ticks
        assert report["minimum_hold_ticks"] >= template_for("DFF").hold_ticks
    else:
        assert report["pipeline_failures"]
