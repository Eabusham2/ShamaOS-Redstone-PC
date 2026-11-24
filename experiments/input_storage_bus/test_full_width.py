"""Bounded checks on source-backed physical receiver recordings."""
from copy import deepcopy
from pathlib import Path
from .archive_io import open_recording

from . import full_width as probe
from .replay import receipt, verify, waves


EVIDENCE = Path(__file__).with_name("evidence.tar.xz")


def test_replay_both_saved_worlds():
    positive = verify(EVIDENCE, 41)
    negative = verify(EVIDENCE, 33)
    assert positive == {"phase_rt": 41, "interior_window_rt": [34, 49], "faults": [], "saved_bits": 512}
    assert negative["saved_bits"] == 512 and negative["interior_window_rt"] == [34, 49]
    [fault] = negative["faults"]
    [timing] = fault.values()
    assert list(fault) == ["unstable/incorrect data during aperture"]
    assert timing["count"] == 13
    assert all((row, bit) == (13, 30) for _, row, bit in timing["examples"])


def test_directed_patterns_distinguish_all_columns_in_every_row():
    with open_recording(EVIDENCE) as archive:
        case = receipt(archive, "phase-41/directed-word.json")
    boundaries = [step["expect"] for step in case["steps"] if step.get("expect")]
    assert len(boundaries) == 12
    for row in range(16):
        columns = [[frame[f"bit{row}_{bit}"] for frame in boundaries] for bit in range(32)]
        for high in range(1, 32):
            for low in range(high):
                assert set(zip(columns[low], columns[high])) == {
                    (False, False), (False, True), (True, False), (True, True)
                }


def test_off_row_transient_is_detected_even_if_final_word_recovers():
    with open_recording(EVIDENCE) as archive:
        case, control, _ = waves(archive, "phase-41", "controls")
        _, tr, _ = waves(archive, "phase-41", "columns-30")
    events, _ = probe.vectors(16, probe.SEED)
    first = probe.changes(control["oscillator"], True)[0]
    epochs, _ = probe.plan(first, events, case["inputs"], 16, 41)
    assert events[0][0] == 0
    modified = deepcopy(tr["bit13_30"])
    protected_tick = epochs[0] + 5
    modified[protected_tick] = not modified[protected_tick]
    observed = probe.analyze({**control, **tr, "bit13_30": modified}, first, epochs, events, 16, [30])
    assert observed["faults"]["out-of-aperture Q transition"]["count"] == 2
