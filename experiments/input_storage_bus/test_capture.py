"""Check interrupted-stage recovery without executing a native process."""
from hashlib import sha256
import json

import pytest

from . import capture, full_width


def test_reject_unreviewed_runner_before_world_write(tmp_path):
    runner = tmp_path / "runner"
    runner.write_bytes(b"unreviewed")
    output = tmp_path / "world-proof"
    with pytest.raises(ValueError, match="digest differs"):
        capture.capture(output, runner, 41, 0, resume=False)
    assert not output.exists()


def test_refuse_edited_stage_stimulus(tmp_path):
    case = {"inputs": {}, "outputs": {}, "initial_ticks": 1, "capture_trace": False, "steps": []}
    case_bytes = json.dumps(case, separators=(",", ":")).encode()
    (tmp_path / "control.json").write_bytes(case_bytes + b" ")
    (tmp_path / "control-result.json").write_bytes(b"{}")
    marker = {"runner_sha256": full_width.PINNED_RUNNER_SHA256, "stream_sha256": "test",
              "scenario_sha256": sha256(case_bytes).hexdigest(), "file_sha256": sha256(b"{}").hexdigest()}
    (tmp_path / "control-verified.json").write_text(json.dumps(marker))
    with pytest.raises(ValueError, match="changed"):
        capture.checked_stage(tmp_path, "control", case, {"stream_sha256": "test"}, tmp_path / "unused")


def test_freeze_detects_source_edited_and_restored(tmp_path):
    source = tmp_path / "source.py"
    source.write_bytes(b"phase = 33")
    before = full_width.frozen({"source": source})
    watch = capture.InputWatch()
    try:
        watch.add([source])
        source.write_bytes(b"phase = 41")
        source.write_bytes(b"phase = 33")
        with pytest.raises(ValueError, match="frozen input changed"):
            watch.verify()
    finally:
        watch.close()
    after = full_width.frozen({"source": source})
    assert before["source"][1] == after["source"][1]
