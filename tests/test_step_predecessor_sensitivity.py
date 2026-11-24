from experiments.step_control_timing.review_predecessor import review


def test_actual_step_request_clears_without_an_admission_at_finite_cutoff():
    result = review()
    assert result["normal_core_edges"] == 0
    assert result["finite_terminal_count"] == "0x1fff9"
    assert result["premature_pending_pulses"] == 23
