"""Keep the full retained physical receiver proof executable on all platforms."""
from experiments.input_receiver_review.review import HERE, check, load_archive, world_check


def test_complete_default_height_receiver_recordings_and_saved_world(tmp_path):
    files = load_archive(HERE / "evidence/default-height-input-receiver.tar.gz")
    reviewed = check(files)
    saved = world_check(files, tmp_path / "world")
    assert reviewed["qualified_register_apertures"] == 1_001_128
    assert reviewed["edge_word_apertures"] == 428_970
    assert reviewed["retained_skew_negative"].startswith("clock skew:")
    assert saved["fresh_anvil_export_body_matches_native_input"] is True
