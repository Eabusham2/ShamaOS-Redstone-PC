"""Bind the targeted clock reservation to the complete guarded CI graph."""
import gzip
import hashlib
import json
from pathlib import Path
import zipfile

from shamaos.hardware.cache_layout import cache_policy
from shamaos.hardware.partition_planner import Graph, audit_plan, build_plan


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "experiments/memory_controller_runtime/guarded_clock_reservation/evidence"


def test_retained_guarded_geometry_binds_the_complete_graph_and_real_clock_group(tmp_path):
    receipt = json.loads((EVIDENCE / "receipt.json").read_text())
    bundle = ROOT / receipt["bundle"]["path"]
    assert hashlib.sha256(bundle.read_bytes()).hexdigest() == receipt["bundle"]["sha256"]
    with zipfile.ZipFile(bundle) as archive:
        assert len(archive.namelist()) == len(set(archive.namelist())) == len(receipt["inventory"])
        assert set(archive.namelist()) == set(receipt["inventory"])
        contents = {name: archive.read(name) for name in archive.namelist()}
    for name, data in contents.items():
        assert hashlib.sha256(data).hexdigest() == receipt["inventory"][name]
    document = json.loads(gzip.decompress(contents["controller.json.gz"]))
    provenance = document["original_geometry"]["provenance"]
    for name, digest in provenance["source_hashes"].items():
        assert hashlib.sha256(contents["source/" + name]).hexdigest() == digest

    mapped = tmp_path / "mapped.json"
    mapped.write_bytes(contents["inputs/mapped.json"])
    graph = Graph.read(mapped, "shama_cache_adapter")
    retained = json.loads(contents["inputs/plan.json"])
    assert audit_plan(graph, retained)["status"] == "passed"
    policy = cache_policy(graph)
    actual = build_plan(graph, cell_budget=policy["native_cells_per_leaf"],
                        boundary_budget=policy["boundary_terminals_per_leaf"],
                        domain_budget=policy["routing_terminals_per_domain"],
                        arity=policy["children_per_cluster"], ports_at_root=True)
    for field in ("summary", "budgets", "entities", "clusters", "entity_leaf",
                  "leaf_boundaries", "routing_domains", "signals", "constants", "root_cluster"):
        assert json.loads(json.dumps(actual[field])) == retained[field]
    clocks = {r["bit"] for r in actual["signals"] if any(s["pin"] == "C" for s in r["sinks"])}
    domain = next(d for d in actual["routing_domains"] if d["id"] == 6)
    assert clocks == {2}
    assert any(segment["net"] == 2 for segment in domain["segments"])
    assert policy["clock_padding_blocks"] == 160
    assert policy["clock_domain_padding_blocks"] == {6: 176}

    report = json.loads(contents["report.json"])
    placement = report["placement_audit"]
    assert (placement["native_cells"], placement["original_port_bits"], placement["clock_leaf_uses"]) == (1147, 182, 116)
    assert report["unique_blocks"] == receipt["unique_controller_blocks"] == 17_597_778
    groups = report["packing"]["cache_layout_policy"]["clock_reservation_groups"]
    targeted = [group for group in groups if group["targeted_domains"]]
    assert targeted == [{"sibling_modules": ["domain-6", "domain-8"],
                         "absolute_padding_blocks": 176, "targeted_domains": [6]}]
    assert report["clock_matching"]["2"]["clock_leaves"] == 116
    assert report["clock_matching"]["2"]["balanced_repeater_delay"] == 3163
    assert not any(receipt[key] for key in ("controller_runtime_verified",
        "connected_cache_runtime_verified", "complete_machine_runtime_verified"))
    assert not report["clock_matching"]["2"]["runtime_verified"]
