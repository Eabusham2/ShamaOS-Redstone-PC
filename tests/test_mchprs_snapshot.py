"""Execute and reopen actual saved memory; reject inexact native persistence."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess

import nbtlib
import pytest

from shamaos.mchprs_import import digest
import shamaos.mchprs_snapshot as snapshot_module
from shamaos.mchprs_snapshot import restore_snapshot
from shamaos.worldio import WorldWriteError, _Region, _decode_chunk

from mchprs_snapshot_fixture import prepare_roundtrip


@pytest.fixture(scope="module")
def saved_roundtrip(tmp_path_factory):
    runner = os.environ.get("SHAMA_MCHPRS_TERRAIN_RUNNER")
    if not runner or not Path(runner).is_file():
        pytest.skip("terrain-enabled actual MCHPRS runner unavailable; native persistence is unverified")
    directory = tmp_path_factory.mktemp("native-quiescent-persistence") / "roundtrip"
    report = prepare_roundtrip(directory, Path(runner))
    output = Path(os.environ.get("SHAMA_VALIDATION_REPORT_DIR", directory))
    output.mkdir(parents=True, exist_ok=True)
    (output / "quiescent-native-persistence-runtime.json").write_text(json.dumps(report, indent=2) + "\n")
    return directory, Path(runner), report


def test_actual_memory_write_native_save_and_fresh_process_retention(saved_roundtrip):
    _, _, report = saved_roundtrip
    assert report["status"] == "passed"
    assert report["native_bits_independently_checked_before_fresh_process"] == 128
    assert report["every_quiescent_section_and_comparator_unchanged_after_reopen_reads"]
    assert report["analog_comparator_strength_saved_and_reloaded"] == 12
    assert report["nonempty_pending_tick_snapshot_rejected"]
    assert report["restore"]["original_chunk_inventory_preserved"]
    assert report["restore"]["original_source_chunks"] == report["source_import"]["chunks"]
    assert report["restore"]["additional_runtime_chunks"] == 1
    assert report["complete_machine_runtime_verified"] is False


def records(directory):
    return [json.loads(line) for line in (directory / 'write-snapshot.jsonl').read_text().splitlines()]


def rewrite(tmp_path, rows):
    path = tmp_path / 'case.jsonl'
    path.write_text(''.join(json.dumps(r, separators=(',', ':')) + '\n' for r in rows))
    return path


@pytest.mark.parametrize('mutation', (
    'truncated', 'record-after-end', 'empty', 'duplicate-section', 'unsupported-state',
    'wrong-section-count', 'bool-section-count', 'wrong-nonair', 'bad-palette', 'bad-index',
    'pending-header', 'pending-footer', 'pending-bool', 'wrong-engine', 'wrong-patch',
    'terrain-without-patch', 'bad-parent-hash', 'missing-scenario-hash',
    'missing-comparator', 'duplicate-comparator', 'out-of-range-strength', 'bool-strength',
    'powered-zero-strength', 'orphan-comparator', 'out-of-range-section', 'out-of-range-chunk',
))
def test_malformed_or_nonquiescent_snapshot_is_rejected(tmp_path, mutation, saved_roundtrip):
    directory, _, _ = saved_roundtrip
    rows = records(directory)
    packed = next(r for r in rows if r['kind'] == 'section')
    analog = next(r for r in rows if r['kind'] == 'comparator' and r['output_strength'] == 12)
    if mutation == 'truncated':
        rows.pop()
    elif mutation == 'record-after-end':
        rows.append(rows[1])
    elif mutation == 'empty':
        rows = [rows[0], rows[-1] | dict(chunks=0, sections=0, nonair=0, comparators=0)]
    elif mutation == 'duplicate-section':
        rows.insert(2, rows[1])
    elif mutation == 'unsupported-state':
        r = next(r for r in rows if r['kind'] == 'uniform_section')
        r['state'] = {'name': 'minecraft:command_block', 'properties': {}}
    elif mutation == 'wrong-section-count':
        rows[-1]['sections'] += 1
    elif mutation == 'bool-section-count':
        rows[-1]['sections'] = True
    elif mutation == 'wrong-nonair':
        packed['nonair'] += 1
    elif mutation == 'bad-palette':
        packed['palette'].append(packed['palette'][0])
    elif mutation == 'bad-index':
        packed['data'][0] |= (1 << packed['bits']) - 1
    elif mutation == 'pending-header':
        rows[0]['pending_scheduled_ticks'] = 1
    elif mutation == 'pending-footer':
        rows[-1]['pending_scheduled_ticks'] = 1
    elif mutation == 'pending-bool':
        rows[0]['pending_scheduled_ticks'] = False
    elif mutation == 'wrong-engine':
        rows[0]['engine_revision'] = 'unrecognized'
    elif mutation == 'wrong-patch':
        rows[0]['engine_registry_patch_sha256'] = 'unrecognized'
    elif mutation == 'terrain-without-patch':
        rows[0]['engine_registry_patch_sha256'] = None
    elif mutation == 'bad-parent-hash':
        rows[0]['imported_stream_sha256'] = 'not-a-digest'
    elif mutation == 'missing-scenario-hash':
        del rows[0]['scenario_sha256']
    elif mutation == 'missing-comparator':
        rows.remove(analog)
    elif mutation == 'duplicate-comparator':
        rows.insert(rows.index(analog), copy.deepcopy(analog))
    elif mutation == 'out-of-range-strength':
        analog['output_strength'] = 16
    elif mutation == 'bool-strength':
        analog['output_strength'] = True
    elif mutation == 'powered-zero-strength':
        analog['output_strength'] = 0
    elif mutation == 'orphan-comparator':
        analog['pos'][0] += 1
    elif mutation == 'out-of-range-section':
        rows[1]['y'] = 20
    elif mutation == 'out-of-range-chunk':
        rows[1]['chunk'][0] = 1_874_999
    path = rewrite(tmp_path, rows)
    destination = tmp_path / 'output-world'
    foreign = tmp_path / 'output-world.partial-unrelated'
    foreign.mkdir()
    (foreign / 'user-file').write_text('retain this unrelated directory')
    with pytest.raises(WorldWriteError):
        restore_snapshot(path, directory / 'source-world', destination, expected_sha256=digest(path))
    assert not destination.exists()
    assert set(tmp_path.glob('output-world.partial-*')) == {foreign}
    assert (foreign / 'user-file').read_text() == 'retain this unrelated directory'


def test_runner_hash_binds_the_exact_snapshot(tmp_path, saved_roundtrip):
    directory, _, _ = saved_roundtrip
    snapshot, source = directory / 'write-snapshot.jsonl', directory / 'source-world'
    with pytest.raises(WorldWriteError, match="runner's reported SHA-256"):
        restore_snapshot(snapshot, source, tmp_path / 'output-world', expected_sha256='0' * 64)
    assert not (tmp_path / 'output-world').exists()
    assert not list(tmp_path.glob('output-world.partial-*'))


@pytest.mark.parametrize('populated', (False, True))
def test_destination_created_during_publication_is_never_replaced(tmp_path, saved_roundtrip, monkeypatch, populated):
    directory, _, _ = saved_roundtrip
    destination = tmp_path / 'output-world'
    publisher = snapshot_module._publish_no_replace
    original_identity = []

    def concurrent_destination(source, target):
        assert target == destination
        target.mkdir()  # Arrives after validation, immediately before the atomic operation.
        if populated:
            (target / 'user-file').write_text('retain concurrent user data')
        info = target.stat()
        original_identity.append((info.st_dev, info.st_ino))
        publisher(source, target)

    monkeypatch.setattr(snapshot_module, '_publish_no_replace', concurrent_destination)
    with pytest.raises(WorldWriteError, match='destination appeared during restore'):
        restore_snapshot(directory / 'write-snapshot.jsonl', directory / 'source-world', destination,
                         expected_sha256=digest(directory / 'write-snapshot.jsonl'))
    info = destination.stat()
    assert (info.st_dev, info.st_ino) == original_identity[0]
    if populated:
        assert (destination / 'user-file').read_text() == 'retain concurrent user data'
    else:
        assert list(destination.iterdir()) == []
    assert not list(tmp_path.glob('output-world.partial-*'))


def test_missing_original_chunk_is_rejected_even_when_total_count_still_matches_source(tmp_path, saved_roundtrip):
    directory, _, report = saved_roundtrip
    rows = records(directory)
    source = [json.loads(line) for line in (directory / 'source.jsonl').read_text().splitlines()]
    omitted = next(r['chunk'] for r in source if r['kind'] in ('section', 'uniform_section'))
    retained = [rows[0]]
    for row in rows[1:-1]:
        chunk = row.get('chunk') if row['kind'] != 'comparator' else [row['pos'][0] // 16, row['pos'][2] // 16]
        if chunk != omitted:
            retained.append(row)
    sections = [r for r in retained if r['kind'] in ('section', 'uniform_section')]
    comparators = [r for r in retained if r['kind'] == 'comparator']
    chunk_count = len({tuple(r['chunk']) for r in sections})
    assert chunk_count == report['source_import']['chunks']  # Extra drive-pad chunk hides count-only checks.
    retained.append(rows[-1] | dict(chunks=chunk_count, sections=len(sections),
                                   comparators=len(comparators), nonair=sum(r['nonair'] for r in sections)))
    path = rewrite(tmp_path, retained)
    with pytest.raises(WorldWriteError, match='omits 1 original saved native chunks'):
        restore_snapshot(path, directory / 'source-world', tmp_path / 'output-world', expected_sha256=digest(path))
    assert not (tmp_path / 'output-world').exists()
    assert not list(tmp_path.glob('output-world.partial-*'))


def test_unsupported_publication_platform_does_not_fall_back_to_replacing_rename(tmp_path, monkeypatch):
    source, destination = tmp_path / 'source', tmp_path / 'destination'
    source.mkdir()
    (source / 'native-file').write_text('retain')
    monkeypatch.setattr(snapshot_module.sys, 'platform', 'unsupported-platform')
    with pytest.raises(WorldWriteError, match='requires Linux renameat2'):
        snapshot_module._publish_no_replace(source, destination)
    assert (source / 'native-file').read_text() == 'retain'
    assert not destination.exists()


@pytest.mark.parametrize('destination', ('existing', 'source', 'source-child'))
def test_existing_and_source_worlds_cannot_be_overwritten(tmp_path, destination, saved_roundtrip):
    directory, _, _ = saved_roundtrip
    snapshot, source = directory / 'write-snapshot.jsonl', directory / 'source-world'
    if destination == 'existing':
        target = tmp_path / 'existing'
        target.mkdir()
        (target / 'user-file').write_text('retain')
    else:
        target = source if destination == 'source' else source / 'new-child'
    with pytest.raises(WorldWriteError, match='fresh destination outside the source world'):
        restore_snapshot(snapshot, source, target, expected_sha256=digest(snapshot))
    if destination == 'existing':
        assert (target / 'user-file').read_text() == 'retain'


def test_runner_does_not_replace_an_existing_snapshot(tmp_path, saved_roundtrip):
    directory, runner, _ = saved_roundtrip
    target = tmp_path / 'snapshot.jsonl'
    target.write_bytes(b'user snapshot')
    result = subprocess.run([str(runner), str(directory / 'source.jsonl'), str(directory / 'write-scenario.json'),
                             '1000', '--snapshot', str(target)], capture_output=True, text=True, timeout=60)
    assert result.returncode != 0
    assert 'refusing to overwrite' in result.stderr
    assert target.read_bytes() == b'user snapshot'
    assert '"status":"passed"' not in result.stdout


def test_pending_work_is_rejected_before_any_snapshot_is_created(tmp_path, saved_roundtrip):
    directory, runner, _ = saved_roundtrip
    target = tmp_path / 'snapshot.jsonl'
    result = subprocess.run([str(runner), str(directory / 'source.jsonl'), str(directory / 'pending-scenario.json'),
                             '1000', '--snapshot', str(target)], capture_output=True, text=True, timeout=60)
    assert result.returncode != 0
    assert 'zero pending scheduled ticks' in result.stderr
    assert not target.exists()
    assert not target.with_suffix('.jsonl.partial').exists()


@pytest.fixture
def analog_native(saved_roundtrip):
    directory, _, _ = saved_roundtrip
    pos = next(r['pos'] for r in records(directory) if r['kind'] == 'comparator' and r['output_strength'] == 12)
    cx, cz = pos[0] // 16, pos[2] // 16
    root = _Region(directory / 'restored-world' / 'region' / f'r.{cx // 32}.{cz // 32}.mca').read((cx & 31) + (cz & 31) * 32)
    entity = next(e for e in root['block_entities'] if [int(e[k]) for k in ('x', 'y', 'z')] == pos)
    return root, entity, cx, cz


@pytest.mark.parametrize('strength', range(1, 16))
def test_native_powered_comparator_preserves_every_legal_positive_strength(analog_native, strength):
    root, entity, cx, cz = analog_native
    entity['OutputSignal'] = nbtlib.Int(strength)
    _decode_chunk(root, cx, cz)
    assert int(entity['OutputSignal']) == strength


@pytest.mark.parametrize('value', (nbtlib.Int(-1), nbtlib.Int(16), nbtlib.Int(0), nbtlib.Byte(12), nbtlib.Short(12), nbtlib.String('12')))
def test_native_invalid_or_inconsistent_comparator_strength_is_rejected(analog_native, value):
    root, entity, cx, cz = analog_native
    entity['OutputSignal'] = value
    with pytest.raises(WorldWriteError):
        _decode_chunk(root, cx, cz)
