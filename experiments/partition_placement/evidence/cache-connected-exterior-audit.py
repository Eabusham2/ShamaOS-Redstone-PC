"""Independent, read-only exterior contract audit against a frozen checkout."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import resource
import signal
import struct
import sys
import time


FROZEN = Path('/workspace/scratch/fb0eaf1c89fe/ShamaOS-cache-native-f711')
BUILD = Path('/workspace/scratch/fb0eaf1c89fe/experiments/cache-controller/connected-native-domain4-672-v1')
OUTPUT = Path('/workspace/scratch/fb0eaf1c89fe/ShamaOS-bounded/build/connected-cache-bounds-independent.json')
sys.path[:0] = [str(FROZEN / 'src'), str(FROZEN)]
resource.setrlimit(resource.RLIMIT_AS, (512 << 20, 512 << 20))
signal.alarm(180)

from dataclasses import asdict
from experiments.memory_controller_runtime import fixture, interconnect, scenarios
from experiments.partition_placement.port_egress_manifest import read_egress_manifest
from shamaos.hardware.memory import DUST, SUPPORT
from shamaos.hardware.memory_backbone import fabric_tile_ports
from shamaos.model import Vec3


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def inside(point, bounds):
    return (bounds.min.x <= point.x <= bounds.max.x
            and bounds.min.y <= point.y <= bounds.max.y
            and bounds.min.z <= point.z <= bounds.max.z)


def main():
    start = time.monotonic()
    live = json.loads((BUILD / 'connected-build-report.json').read_text())
    for name, expected in live['source_hashes'].items():
        assert sha(FROZEN / name) == expected, name
    accepted = FROZEN / 'build/cache-native-frozen/accepted-facade'
    report_path = accepted / 'report.json'
    report = json.loads(report_path.read_text())
    assert sha(report_path) == live['facade_report_sha256']
    plan_path = accepted / 'inputs/plan.json'
    assert sha(plan_path) == report['plan_sha256'] == live['plan_sha256']
    manifest_path = accepted / 'report-balanced.json.gz'
    assert sha(manifest_path) == report['balanced_manifest']['sha256'] == live['facade_manifest']['sha256']
    graph, _, fabric, production_origin = fixture.graph_and_fabric()
    plan = json.loads(plan_path.read_text())
    placed = read_egress_manifest(manifest_path, report['balanced_manifest']['sha256'], graph, plan)
    connection = interconnect.connect_cache(graph, placed, fabric, production_origin)

    graph_ports = {(entry['port_name'], entry['port_index']): entry
                   for entry in graph.entities if entry['kind'] == 'port'}
    expected_ports = {key: placed.exposed_ports[entry['id']] for key, entry in graph_ports.items()}
    assert expected_ports == connection.controller_ports and len(expected_ports) == 183
    assert len(set(expected_ports.values())) == 183
    expected_hosts = {key for key, entry in graph_ports.items()
                      if entry['direction'] == 'input' and key[0] != 'selected_word'}
    assert expected_hosts == {(name, bit) for name, width in scenarios.HOST_WIDTHS.items()
                             for bit in range(width)}
    assert len(expected_hosts) == 72
    assert {key: expected_ports[key].offset(dz=1) for key in expected_hosts} == connection.host_drive_pads
    for point in expected_ports.values():
        assert point.z == placed.bounds.max.z
        assert connection.interfaces.access[point] == ('south',)
        assert connection.interfaces.endpoint_bounds[point] == placed.bounds

    memory_bounds = fabric.bounds(connection.bank_origin)
    assert memory_bounds.min.x - placed.bounds.max.x == 1024
    assert memory_bounds.min.y == placed.bounds.min.y == -60
    assert connection.bank_origin.y == production_origin.y == -56
    assert (fabric.total_bytes, fabric.bank_count, fabric.layers, fabric.layer_pitch,
            fabric.decoder_x_offset, fabric.group_count) == (8192, 2, 2, 12, -170, 1)
    pins = fabric_tile_ports(connection.bank_origin, fabric)[0]
    expected_nets = {}
    for name, width in [('bank_select', 2), ('row_address', 10), ('write_data', 32)]:
        for bit in range(width):
            assert graph_ports[name, bit]['direction'] == 'output'
            expected_nets[f'cache:{name}:{bit}'] = (expected_ports[name, bit], (getattr(pins, name)[bit],), False)
    for name, point in [('read_enable', pins.read_enable), ('write_commit', pins.write_enable)]:
        assert graph_ports[name, 0]['direction'] == 'output'
        expected_nets[f'cache:{name}:0'] = (expected_ports[name, 0], (point,), False)
    for bit in range(32):
        assert graph_ports['selected_word', bit]['direction'] == 'input'
        expected_nets[f'cache:selected_word:{bit}'] = (expected_ports['selected_word', bit], (pins.selected_word[bit],), True)
    actual_nets = {net.name: (net.source, net.sinks, net.wired_or) for net in connection.nets}
    assert actual_nets == expected_nets and len(actual_nets) == 78
    assert json.loads(json.dumps([asdict(net) for net in connection.nets])) == json.loads((BUILD / 'external-nets.json').read_text())
    endpoints = {point for net in connection.nets for point in (net.source, *net.sinks)}
    assert len(endpoints) == 156

    all_host_pads = set(connection.host_drive_pads.values())
    offsets = ((0, 0, 0), (1, 0, 0), (-1, 0, 0), (0, 1, 0),
               (0, -1, 0), (0, 0, 1), (0, 0, -1))
    protected = {pad.offset(dx, dy, dz) for pad in all_host_pads for dx, dy, dz in offsets}
    for key, pad in connection.host_drive_pads.items():
        assert not inside(pad, placed.bounds) and not inside(pad, memory_bounds)
        assert pad.z == placed.bounds.max.z + 1
        assert not any(pad.offset(dx, dy, dz) in all_host_pads
                       for dx, dy, dz in offsets if dx or dy or dz)
        assert expected_ports[key] in protected
    unused_contacts = set(expected_ports.values()) - endpoints
    protected.update(unused_contacts)
    protected.update(point.offset(dy=-1) for point in unused_contacts)
    assert not (protected & endpoints)
    expected_contact_states = {point: DUST for point in endpoints}
    expected_contact_states.update({point.offset(dy=-1): SUPPORT for point in endpoints})
    allowed_controller_entries = {point: state for point, state in expected_contact_states.items()
                                  if inside(point, placed.bounds)}
    assert len(allowed_controller_entries) == 156

    obstacle_states = {}
    for block in connection.interfaces.obstacles:
        previous = obstacle_states.setdefault(block.pos, block.block)
        assert previous == block.block, ('conflicting-contract-obstacles', block.pos)
    for pad in all_host_pads:
        assert obstacle_states[pad].name == 'minecraft:redstone_block'
    for point in expected_ports.values():
        assert obstacle_states[point] == DUST and obstacle_states[point.offset(dy=-1)] == SUPPORT

    minimum, maximum = [10**20] * 3, [-10**20] * 3
    count = 0
    palette, chunks, observed_contacts, per_net = {}, set(), {}, {}
    digest = hashlib.sha256()
    for item in connection.iter_routes():
        point, state = item.pos, item.block
        assert -60 <= point.y <= 319, ('height', point)
        assert point not in protected, ('host-pad-or-unrelated-contact', point, item.component)
        if inside(point, placed.bounds):
            assert allowed_controller_entries.get(point) == state, ('controller-envelope-entry', point)
        if point in expected_contact_states:
            assert expected_contact_states[point] == state
            observed_contacts[point] = state
        for axis, value in enumerate((point.x, point.y, point.z)):
            minimum[axis] = min(minimum[axis], value)
            maximum[axis] = max(maximum[axis], value)
        chunks.add((point.x // 16, point.z // 16))
        palette_id = palette.setdefault(state, len(palette))
        digest.update(struct.pack('>iiiI', point.x, point.y, point.z, palette_id))
        name = item.component.removeprefix('complete-cache-interconnect:')
        assert name in expected_nets
        per_net[name] = per_net.get(name, 0) + 1
        count += 1
    assert observed_contacts == expected_contact_states
    assert set(per_net) == set(expected_nets)
    for name, expected in live['source_hashes'].items():
        assert sha(FROZEN / name) == expected, name
    result = {
        'schema': 'connected-cache-exterior-bounds-independent-v1',
        'status': 'contract-and-complete-emitted-external-route-checks-passed',
        'scope': 'Independent graph bit/direction contract, actual emitted exterior route bounds and host pad clearance. Complete native save/readback and electrical runtime are separate gates.',
        'frozen_checkout': str(FROZEN),
        'audit_source_sha256': sha(__file__),
        'source_hashes': live['source_hashes'],
        'facade_manifest_sha256': report['balanced_manifest']['sha256'],
        'facade_report_sha256': sha(report_path),
        'plan_sha256': report['plan_sha256'],
        'external_nets_sha256': sha(BUILD / 'external-nets.json'),
        'controller_bounds': asdict(placed.bounds),
        'memory_bounds': asdict(memory_bounds),
        'bank_origin': asdict(connection.bank_origin),
        'minimum_horizontal_component_gap': 1024,
        'original_ports': 183,
        'host_inputs': 72,
        'nets': 78,
        'physical_return_nets': 32,
        'external_endpoints': 156,
        'verified_endpoint_and_support_states': len(observed_contacts),
        'all_bus_indices_and_directions_match_graph': True,
        'all_72_host_pads_reserved_as_obstacles': True,
        'all_host_pads_and_their_touching_faces_clear_of_routes': True,
        'unrouted_controller_contacts_clear_of_routes': len(unused_contacts),
        'controller_envelope_entries_only_exact_declared_terminals_and_supports': True,
        'route_emitted_operations': count,
        'route_occupied_chunks': len(chunks),
        'route_bounds': {'min': dict(zip(('x', 'y', 'z'), minimum)), 'max': dict(zip(('x', 'y', 'z'), maximum))},
        'ordered_route_operations_sha256': digest.hexdigest(),
        'operation_digest_encoding': 'big-endian signed 32-bit X,Y,Z and unsigned 32-bit palette index, emitted order',
        'palette': [asdict(state) for state in palette],
        'per_net_emitted_operations': per_net,
        'source_and_input_bindings_unchanged': True,
        'seconds': time.monotonic() - start,
        'peak_rss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        'memory_limit_mib': 512,
        'runtime_verified': False,
        'whole_computer_verified': False,
    }
    OUTPUT.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print(json.dumps({key: result[key] for key in ('status', 'route_emitted_operations', 'route_bounds', 'route_occupied_chunks', 'seconds', 'peak_rss_kib')}))


if __name__ == '__main__':
    main()
