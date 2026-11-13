"""Read-only series-repeater inventory for the frozen connected cache routes.

Historical source/input paths are deliberate. This is a measured route-only
delay inventory, not a controller, memory or electrical-runtime acceptance.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import resource
import signal
import struct
import sys
import time

FROZEN = Path("/workspace/scratch/fb0eaf1c89fe/ShamaOS-cache-native-f711")
BUILD = Path("/workspace/scratch/fb0eaf1c89fe/experiments/cache-controller/connected-native-domain4-672-v1")
ROOT = Path("/workspace/scratch/fb0eaf1c89fe/ShamaOS-bounded")
OUTPUT = ROOT / "experiments/partition_placement/evidence/cache-connected-route-delays.json"
sys.path[:0] = [str(FROZEN / "src"), str(FROZEN)]
resource.setrlimit(resource.RLIMIT_AS, (512 << 20, 512 << 20))
signal.alarm(180)

from experiments.memory_controller_runtime import fixture, interconnect
from experiments.partition_placement.port_egress_manifest import read_egress_manifest


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    start = time.monotonic()
    live = json.loads((BUILD / "connected-build-report.json").read_text())
    for name, expected in live["source_hashes"].items():
        assert sha(FROZEN / name) == expected, name
    accepted = FROZEN / "build/cache-native-frozen/accepted-facade"
    report_path = accepted / "report.json"
    report = json.loads(report_path.read_text())
    assert sha(report_path) == live["facade_report_sha256"]
    plan_path = accepted / "inputs/plan.json"
    assert sha(plan_path) == report["plan_sha256"] == live["plan_sha256"]
    manifest_path = accepted / "report-balanced.json.gz"
    assert sha(manifest_path) == report["balanced_manifest"]["sha256"] == live["facade_manifest"]["sha256"]
    graph, _, fabric, production_origin = fixture.graph_and_fabric()
    placed = read_egress_manifest(manifest_path, live["facade_manifest"]["sha256"], graph,
                                 json.loads(plan_path.read_text()))
    connection = interconnect.connect_cache(graph, placed, fabric, production_origin)
    assert len(connection.nets) == 78 and all(len(net.sinks) == 1 for net in connection.nets)
    assert json.loads(json.dumps([asdict(net) for net in connection.nets])) == json.loads((BUILD / "external-nets.json").read_text())

    # A net is emitted as a contiguous sequence. Keeping one net's repeaters is
    # enough to remove identical seam emissions without retaining 4M positions.
    per_net = {}
    name, repeaters, counts, emitted = None, {}, Counter(), 0
    ordered_digest, palette = hashlib.sha256(), {}

    def finish_net():
        if name is None:
            return
        assert name not in per_net, "interleaved net emission invalidates bounded collector"
        facing_counts = Counter(dict(state.properties)["facing"] for state in repeaters.values())
        delay_counts = Counter(int(dict(state.properties)["delay"]) for state in repeaters.values())
        per_net[name] = {
            "emitted_operations": emitted,
            "emitted_state_name_counts": dict(sorted(counts.items())),
            "unique_repeaters": len(repeaters),
            "repeater_facing_counts": dict(sorted(facing_counts.items())),
            "repeater_delay_counts": dict(sorted(delay_counts.items())),
            "series_repeater_delay_redstone_ticks": sum(k * n for k, n in delay_counts.items()),
        }

    total = 0
    for item in connection.iter_routes():
        current = item.component.removeprefix("complete-cache-interconnect:")
        if current != name:
            finish_net()
            name, repeaters, counts, emitted = current, {}, Counter(), 0
        assert item.block.name in {"minecraft:stone", "minecraft:redstone_wire", "minecraft:repeater"}
        if item.block.name == "minecraft:repeater":
            previous = repeaters.setdefault(item.pos, item.block)
            assert previous == item.block
            assert 1 <= int(dict(item.block.properties)["delay"]) <= 4
        counts[item.block.name] += 1
        emitted += 1
        total += 1
        p = item.pos
        ordered_digest.update(struct.pack(">iiiI", p.x, p.y, p.z, palette.setdefault(item.block, len(palette))))
    finish_net()
    previous_audit = json.loads((ROOT / "experiments/partition_placement/evidence/cache-connected-exterior-independent-review.json").read_text())
    assert total == previous_audit["route_emitted_operations"]
    assert ordered_digest.hexdigest() == previous_audit["ordered_route_operations_sha256"]
    for net in connection.nets:
        entry = per_net[net.name]
        source, sink = (net.sinks[0], net.source) if net.wired_or else (net.source, net.sinks[0])
        entry.update({"physical_source": asdict(source), "physical_sink": asdict(sink),
                      "reverse_selected_word": net.wired_or})
    assert set(per_net) == {net.name for net in connection.nets}
    assert sum(item["reverse_selected_word"] for item in per_net.values()) == 32
    for name, expected in live["source_hashes"].items():
        assert sha(FROZEN / name) == expected, name
    result = {
        "schema": "connected-cache-external-route-delay-inventory-v1",
        "status": "all-78-route-series-repeater-inventories-passed",
        "scope": "One physical source/sink per route. Sum of unique actual repeater delays along that route; no invented signal values. Excludes controller/facade/memory internal response, dust event-order effects and setup/hold margins. Actual native execution remains required.",
        "source_hashes": live["source_hashes"],
        "facade_manifest_sha256": live["facade_manifest"]["sha256"],
        "facade_report_sha256": live["facade_report_sha256"],
        "plan_sha256": live["plan_sha256"],
        "external_nets_sha256": sha(BUILD / "external-nets.json"),
        "audit_source_sha256": sha(__file__),
        "route_emitted_operations": total,
        "ordered_route_operations_sha256": ordered_digest.hexdigest(),
        "exact_route_stream_matches_prior_exterior_audit": True,
        "per_net": per_net,
        "maximum_series_repeater_delay_redstone_ticks": max(v["series_repeater_delay_redstone_ticks"] for v in per_net.values()),
        "minimum_series_repeater_delay_redstone_ticks": min(v["series_repeater_delay_redstone_ticks"] for v in per_net.values()),
        "native_runtime_verified": False,
        "seconds": time.monotonic() - start,
        "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "memory_limit_mib": 512,
    }
    OUTPUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({key: result[key] for key in ("status", "route_emitted_operations", "minimum_series_repeater_delay_redstone_ticks", "maximum_series_repeater_delay_redstone_ticks", "seconds", "peak_rss_kib")}))


if __name__ == "__main__":
    main()
