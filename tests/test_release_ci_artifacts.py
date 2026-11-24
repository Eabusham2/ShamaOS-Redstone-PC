"""Preserve each CI rerun artifact as a distinct original ZIP in the release."""
from hashlib import sha256
import importlib.util
from pathlib import Path
import subprocess

import pytest

from shamaos.worldio import WorldWriteError


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("release_ci_artifacts_tested", ROOT / "tools/release_ci.py")
ci = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ci)
COMMIT = "a" * 40


def contents(artifact_id):
    return f"original ZIP bytes for artifact {artifact_id}".encode()


def entry(artifact_id, name):
    return {"id": artifact_id, "name": name, "digest": "sha256:" + sha256(contents(artifact_id)).hexdigest()}


def workflow(monkeypatch, artifacts):
    names = ["world-backend", "rtl", "behavioral-and-redstone", "full-physical-synthesis",
             "behavioral-partitions (components)", "behavioral-partitions (system)"]
    names += [f"test ({i})" for i in range(4)] + [f"full-physical-partitions ({i})" for i in range(12)]
    jobs = [{"name": name, "conclusion": "success"} for name in names]
    run = {"id": 99, "head_sha": COMMIT, "conclusion": "success", "status": "completed",
           "path": ".github/workflows/ci.yml", "event": "push", "head_repository": {"full_name": "a/b"}}

    def pages(path, key):
        if path.startswith("repos/a/b/actions/workflows/ci.yml/runs?"):
            return [run]
        if path == "repos/a/b/actions/runs/99/jobs":
            return jobs
        if path == "repos/a/b/actions/runs/99/artifacts":
            return artifacts
        if path.startswith("repos/a/b/actions/runs?"):
            return []
        raise AssertionError(path)

    downloads = []
    def download(command, *, stdout, check):
        assert check and command[:2] == ["gh", "api"]
        artifact_id = int(command[2].split("/")[-2])
        assert command[2] == f"repos/a/b/actions/artifacts/{artifact_id}/zip"
        downloads.append(artifact_id)
        stdout.write(contents(artifact_id))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(ci, "paginated", pages)
    monkeypatch.setattr(ci.subprocess, "run", download)
    return downloads


def required_entries():
    modules = "u_cpu u_input u_gpu u_display_bridge u_vram u_services u_kernel u_fs u_asm u_cache u_ram u_flash"
    names = [f"full-soc-{name}" for name in modules.split()]
    names.extend([f"full-soc-report-{COMMIT}", f"shamaos-tested-fixture-{COMMIT}"])
    return [entry(i, n) for i, n in enumerate(names, 1)]


def test_acquire_retains_every_attempt_and_noncolliding_original_name(tmp_path, monkeypatch):
    artifacts = required_entries()
    # Future genuine logical names may resemble earlier generated filenames.
    artifacts += [entry(21, "full-soc-u_cpu"), entry(22, "full-soc-u_cpu--artifact-1"),
                  entry(23, "full-soc-u_cpu-1--artifact-1")]
    expected = ci.retained_artifact_names(artifacts)
    assert expected == ci.retained_artifact_names(list(reversed(artifacts)))
    assert expected[1] == "full-soc-u_cpu--artifact-1.zip"
    assert expected[1] == ci.retained_artifact_names(artifacts[:-2])[1]
    assert expected[21] == "full-soc-u_cpu--artifact-21.zip"
    assert expected[22] == "full-soc-u_cpu--artifact-1--artifact-22.zip"
    assert expected[2] == "full-soc-u_input.zip"
    downloads = workflow(monkeypatch, artifacts)
    output = tmp_path / "release-input"
    ci.acquire("a/b", COMMIT, output)
    assert set(downloads) == set(expected) and len(downloads) == len(expected)
    folder = output / "ci-artifacts"
    assert {p.name: p.read_bytes() for p in folder.iterdir()} == {n: contents(i) for i, n in expected.items()}
    with pytest.raises(FileExistsError):
        ci.acquire("a/b", COMMIT, output)
    assert len(downloads) == len(expected)


@pytest.mark.parametrize("bad", [
    {"id": 1, "name": "same-id-different-name"}, {"id": True, "name": "wrong"}, {"id": 0, "name": "zero"},
    {"id": "99", "name": "noninteger"}, {"id": 99, "name": "../escape"}, {"id": 99, "name": r"dir\name"},
    {"id": 99, "name": "NUL.hardware"}, {"id": 99, "name": ".."}, {"id": 99, "name": ""}, {"id": 99, "name": ".hidden"},
])
def test_acquire_rejects_malformed_inventory_before_downloading(tmp_path, monkeypatch, bad):
    downloads = workflow(monkeypatch, [*required_entries(), bad])
    with pytest.raises(WorldWriteError, match="duplicate GitHub artifact ID|unsafe file name"):
        ci.acquire("a/b", COMMIT, tmp_path / "evidence")
    assert not downloads


def test_generated_filename_cannot_fulfil_a_required_logical_name(tmp_path, monkeypatch):
    artifacts = required_entries()[1:]
    artifacts.extend([entry(90, "full-soc-u_cpu--artifact-1"), entry(91, "full-soc-u_input")])
    downloads = workflow(monkeypatch, artifacts)
    with pytest.raises(WorldWriteError, match="missing original validation artifacts"):
        ci.acquire("a/b", COMMIT, tmp_path / "evidence")
    assert not downloads


def test_file_names_portable_for_case_collisions_and_long_names():
    long = "a" * 250
    files = ci.retained_artifact_names([entry(5, long), entry(6, long.upper()), entry(7, long + "bc")])
    assert len({n.casefold() for n in files.values()}) == 3
    for i, name in files.items():
        assert name.endswith(f"--artifact-{i}.zip") and len((name + ".001").encode()) <= 255
