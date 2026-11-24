"""Run the retained physical input-bank checks in the required CI collection."""
from pathlib import Path

from experiments.input_registered_bank.review_independent import review
from experiments.input_registered_bank.test_review_sources import (
    test_archived_producer_replays_with_separate_current_geometry_receipt,
    test_unrecognized_archived_code_is_rejected_before_execution,
)


def test_registered_bank_independent_native_recording_review():
    path = Path(__file__).resolve().parents[1] / "experiments/input_registered_bank/evidence.tar.xz"
    result = review(path)
    assert result["complete_machine_runtime_verified"] is False
