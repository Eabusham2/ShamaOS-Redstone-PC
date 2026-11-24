from experiments.raw_counter_timing.reset_receipt.review import review_retained


def test_native_reset_receipt_preserves_each_request_and_rejects_old_zero_margin_layout():
    report = review_retained()
    assert len(report["positive"]["transactions"]) == 70
    assert sum(v["edges"] for v in report["positive"]["registers"].values()) == 4022
    assert report["positive"]["aperture_violations"] == []
    assert report["positive"]["capture_mismatches"] == []
    assert len(report["retained_negative"]["aperture_violations"]) == 9
    assert len(report["retained_negative"]["capture_mismatches"]) == 2
    assert not report["positive"]["complete_machine_runtime_verified"]
