"""Actual engine write -> quiescent snapshot -> native save -> fresh process."""
from __future__ import annotations

from itertools import chain
import json
from pathlib import Path
import subprocess

from shamaos.hardware.memory import DUST, SUPPORT, MemoryBankSpec, bank_ports, comparator, iter_memory_bank
from shamaos.mchprs_import import digest, export_world
from shamaos.mchprs_snapshot import restore_snapshot
from shamaos.model import BlockState, Placement, Vec3
from shamaos.worldio import NativeWorldReader, NativeWorldWriter


def execute(runner, stream, scenario, directory, name, *, snapshot=True, succeeds=True):
    scenario_path = directory / f'{name}-scenario.json'
    scenario_path.write_text(json.dumps(scenario, separators=(',', ':')))
    destination = directory / f'{name}-snapshot.jsonl'
    command = [str(runner), str(stream), str(scenario_path), '1000']
    if snapshot:
        command += ['--snapshot', str(destination)]
    actual = subprocess.run(command, capture_output=True, text=True, timeout=60)
    (directory / f'{name}-stderr.txt').write_text(actual.stderr)
    if not succeeds:
        assert actual.returncode != 0
        assert '"status":"passed"' not in actual.stdout
        assert not destination.exists()
        return actual.stderr
    assert actual.returncode == 0, actual.stderr[-8000:]
    result = json.loads(actual.stdout)
    assert result['status'] == 'passed'
    assert result['pending_ticks_after_scenario'] == 0
    if snapshot:
        assert result['snapshot']['sha256'] == digest(destination)
        assert result['snapshot']['status'] == 'quiescent-snapshot-written'
    (directory / f'{name}-runtime-report.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def prepare_roundtrip(directory: Path, runner: Path) -> dict:
    directory.mkdir(parents=True, exist_ok=False)
    origin = Vec3(256, -56, 128)
    bank = MemoryBankSpec(words=4, word_bits=32)
    pins = bank_ports(origin, bank)
    coord = lambda p: [p.x, p.y, p.z]
    driver = lambda p: coord(p.offset(-1, 0, -1))
    inputs = {f'D{i}': driver(p) for i, p in enumerate(pins.data_in)}
    inputs |= {f'W{i}': driver(p) for i, p in enumerate(pins.write_select)}
    inputs |= {f'R{i}': driver(p) for i, p in enumerate(pins.read_select)}
    outputs = {f'Q{i}': coord(p) for i, p in enumerate(pins.data_out)}
    outputs |= {f'S{row}:{bit}': coord(origin.offset(bit * 5 + 2, 3, 3 + row * 6))
                for row in range(4) for bit in range(32)}
    analog = origin.offset(dx=220)
    outputs['ANALOG'] = coord(analog.offset(dx=6))
    values = [0x1234ABCD, 0x55AA55AA, 0, 0xFFFFFFFF]
    initial_values = list(values)
    initial = b''.join(word.to_bytes(4, 'little') for word in values)

    def analog_cells():
        for x in range(7):
            p = analog.offset(dx=x)
            yield Placement(p.offset(dy=-1), SUPPORT, 'analog-support')
            state = BlockState.of('minecraft:redstone_block') if x == 0 else comparator('east') if x == 5 else DUST
            yield Placement(p, state, 'analog-retention')

    source_world = directory / 'source-world'
    with NativeWorldWriter(source_world, surface_y=-61, spawn=Vec3(224,-59,96), spawn_platform=False) as writer:
        for placement in chain(iter_memory_bank(origin, spec=bank, initial=initial), analog_cells()):
            writer.set_block(placement)
        source_validation = writer.validate()
        assert source_validation['status'] == 'passed'
    source_stream = directory / 'source.jsonl'
    source_manifest = export_world(source_world, source_stream)

    def stored(words):
        return {f'S{row}:{bit}': bool(word >> bit & 1) for row, word in enumerate(words) for bit in range(32)}

    def read(word):
        return {f'Q{bit}': bool(word >> bit & 1) for bit in range(32)} | {'ANALOG': True}

    phase = 256
    steps = [{'ticks': phase, 'expect': stored(values) | read(0)}]
    for row, value in ((1, 0xA596F00F), (3, 0)):
        steps += [{'drive': {f'D{bit}': bool(value >> bit & 1) for bit in range(32)},
                   'ticks': phase, 'expect': stored(values) | read(0)}]
        values[row] = value
        steps += [{'drive': {f'W{row}': True}, 'ticks': phase, 'expect': stored(values) | read(0)},
                  {'drive': {f'W{row}': False}, 'ticks': phase, 'expect': stored(values) | read(0)},
                  {'drive': {f'R{row}': True}, 'ticks': phase, 'expect': stored(values) | read(value)},
                  {'drive': {f'R{row}': False}, 'ticks': phase, 'expect': stored(values) | read(0)}]
    steps.append({'drive': {f'D{bit}': False for bit in range(32)}, 'ticks': phase, 'expect': stored(values) | read(0)})
    write_case = dict(inputs=inputs, outputs=outputs, initial_ticks=phase, steps=steps, capture_trace=False)
    first = execute(runner, source_stream, write_case, directory, 'write')
    first_snapshot = directory / 'write-snapshot.jsonl'
    records = [json.loads(line) for line in first_snapshot.read_text().splitlines()]
    analog_record = next(r for r in records if r['kind'] == 'comparator' and r['pos'] == coord(analog.offset(dx=5)))
    assert analog_record['output_strength'] == 12, analog_record
    restored_world = directory / 'restored-world'
    restore = restore_snapshot(first_snapshot, source_world, restored_world, expected_sha256=first['snapshot']['sha256'])
    assert restore['status'] == 'quiescent-snapshot-native-readback-passed'
    # Independently inspect the real native repeater state before any second
    # process can initialize or repair it from surrounding circuitry.
    reader = NativeWorldReader(restored_world)
    for row, word in enumerate(values):
        for bit in range(32):
            state = reader.get_block(origin.offset(bit * 5 + 2, 3, 3 + row * 6))
            assert state.name == 'minecraft:repeater'
            assert dict(state.properties)['powered'] == str(bool(word >> bit & 1)).lower()
            assert dict(state.properties)['locked'] == 'true'
    restored_stream = directory / 'restored.jsonl'
    restored_manifest = export_world(restored_world, restored_stream)
    steps = [{'ticks': phase, 'expect': stored(values) | read(0)}]
    for row, word in enumerate(values):
        steps += [{'drive': {f'R{row}': True}, 'ticks': phase, 'expect': stored(values) | read(word)},
                  {'drive': {f'R{row}': False}, 'ticks': phase, 'expect': stored(values) | read(0)}]
    second_case = dict(inputs=inputs, outputs=outputs, initial_ticks=phase, steps=steps, capture_trace=False)
    second = execute(runner, restored_stream, second_case, directory, 'reopen')
    after = [json.loads(line) for line in (directory / 'reopen-snapshot.jsonl').read_text().splitlines()]
    body = lambda rows: [r for r in rows if r['kind'] not in ('snapshot_header', 'snapshot_complete')]
    assert body(records) == body(after), 'quiescent state changed after fresh import/read/clear'

    pending_case = dict(inputs=inputs, outputs=outputs, initial_ticks=phase, capture_trace=False,
        steps=[{'drive': {'W0': True}, 'ticks': 0, 'expect': stored(initial_values) | read(0)}])
    rejected = execute(runner, source_stream, pending_case, directory, 'pending', succeeds=False)
    assert 'zero pending scheduled ticks' in rejected
    report = {'kind': 'actual-engine-quiescent-native-save-reopen', 'status': 'passed',
              'stored_bytes': 16, 'storage_bits': 128, 'words': 4, 'word_bits': 32,
              'initial_words': initial_values, 'saved_words': values,
              'analog_comparator_strength_saved_and_reloaded': analog_record['output_strength'],
              'native_bits_independently_checked_before_fresh_process': 128,
              'first_runtime': first, 'second_runtime': second, 'restore': restore,
              'source_import': source_manifest, 'restored_import': restored_manifest,
              'every_quiescent_section_and_comparator_unchanged_after_reopen_reads': True,
              'nonempty_pending_tick_snapshot_rejected': True,
              'complete_machine_runtime_verified': False}
    (directory / 'roundtrip-report.json').write_text(json.dumps(report, indent=2) + '\n')
    return report
