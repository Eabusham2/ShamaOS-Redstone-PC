import json
from pathlib import Path

import pytest

from experiments.input_registered_bank.review_sources import approved_sources, compare_active, review_frozen
from experiments.input_storage_bus.archive_io import open_recording


RECORDING = Path(__file__).with_name("evidence.tar.xz")


def test_unrecognized_archived_code_is_rejected_before_execution():
    with open_recording(RECORDING) as archive:
        class Altered:
            def read(self, name):
                if name.endswith("/sources.json"):
                    sources = json.loads(archive.read(name))
                    sources["experiments/input_registered_bank/fixture.py"] = "0"*64
                    return json.dumps(sources).encode()
                raise AssertionError("code should not be read before approval")
        with pytest.raises(ValueError, match="unrecognized"):
            approved_sources(Altered())


def test_archived_producer_replays_with_separate_current_geometry_receipt():
    actual = review_frozen(RECORDING)
    assert actual["positive"]["minimum_rt"] == {"setup":39,"hold":2,"aperture":35}
    assert actual["delayed"]["unstable/incorrect data during aperture"] == 96
    comparison = compare_active(RECORDING)
    assert all(p["identical_physical_positions_and_states"] == 75255
               for p in comparison["profiles"].values())
    assert comparison["fresh_native_run_performed"] is False
