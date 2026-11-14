"""Generated source identity must change with the physical cache policy."""
import hashlib

from shamaos import generator


def test_cache_policy_changes_are_bound_even_without_a_git_revision(tmp_path, monkeypatch):
    source = tmp_path / "src/shamaos/generator.py"
    source.parent.mkdir(parents=True)
    source.write_text("# Standalone generator source fixture\n")
    policy = source.parent / "hardware/cache_layout.py"
    policy.parent.mkdir()
    policy.write_bytes(b"CLOCK_PADDING = 128\n")
    monkeypatch.setattr(generator, "__file__", str(source))

    def unavailable_git(*args, **kwargs):
        raise OSError("this source tree has no Git executable")

    monkeypatch.setattr(generator.subprocess, "check_output", unavailable_git)
    before = generator.source_provenance()
    key = "src/shamaos/hardware/cache_layout.py"
    assert before["base_revision"] is None
    assert before["files_sha256"][key] == hashlib.sha256(policy.read_bytes()).hexdigest()

    policy.write_bytes(b"CLOCK_PADDING = 160\n")
    after = generator.source_provenance()
    assert after["base_revision"] is None
    assert after["files_sha256"][key] == hashlib.sha256(policy.read_bytes()).hexdigest()
    assert before["files_sha256"][key] != after["files_sha256"][key]
    assert {path for path in before["files_sha256"] if before["files_sha256"][path]
            != after["files_sha256"][path]} == {key}
