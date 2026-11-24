"""Run the retained physical input-bank checks in the required CI collection."""
from pathlib import Path
import subprocess
import sys

import nbtlib
import numpy
import pytest

from experiments.input_registered_bank.review_independent import review
from experiments.input_registered_bank.test_review_sources import (
    test_archived_producer_replays_with_separate_current_geometry_receipt,
    test_unrecognized_archived_code_is_rejected_before_execution,
)


def test_registered_bank_independent_native_recording_review():
    path = Path(__file__).resolve().parents[1] / "experiments/input_registered_bank/evidence.tar.xz"
    result = review(path)
    assert result["complete_machine_runtime_verified"] is False


def test_frozen_recording_replays_when_posix_resource_module_is_unavailable(monkeypatch):
    from experiments.input_registered_bank import review_sources

    # Exercise the actual isolated frozen-source process under the Windows
    # dependency condition, while keeping the producer/archive untouched.
    prefix = """
import importlib.abc, sys
class ResourceUnavailable(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'resource':
            raise ModuleNotFoundError('resource is unavailable', name=fullname)
sys.meta_path.insert(0, ResourceUnavailable())
import importlib.util
original_find_spec = importlib.util.find_spec
importlib.util.find_spec = lambda name, *args, **kwargs: None if name == 'resource' else original_find_spec(name, *args, **kwargs)
"""
    monkeypatch.setattr(review_sources, "_OFFLINE_PROCESS_SETUP", prefix + review_sources._OFFLINE_PROCESS_SETUP)
    archive = Path(__file__).resolve().parents[1] / "experiments/input_registered_bank/evidence.tar.xz"
    actual = review_sources.review_frozen(archive)
    assert actual["positive"]["minimum_rt"] == {"setup": 39, "hold": 2, "aperture": 35}
    assert actual["delayed"]["unstable/incorrect data during aperture"] == 96


def test_current_geometry_import_does_not_require_or_bypass_native_limits():
    root = Path(__file__).resolve().parents[1]
    script = """
import importlib.abc, sys
sys.path[:0] = [sys.argv[1] + '/src', *sys.argv[1:]]
sys.modules.pop('resource', None)
class MissingResource(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'resource':
            raise ModuleNotFoundError('resource is unavailable', name=fullname)
sys.meta_path.insert(0, MissingResource())
from experiments.input_storage_bus.full_width import limit_native_memory
try:
    limit_native_memory()
except ModuleNotFoundError as error:
    assert error.name == 'resource'
else:
    raise AssertionError('native memory limit must remain mandatory')
"""
    dependencies = sorted({str(Path(module.__file__).resolve().parents[1])
                           for module in (nbtlib, numpy)})
    result = subprocess.run([sys.executable, "-I", "-c", script, str(root), *dependencies],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("operation", [
    "import subprocess; subprocess.run([sys.executable, '-c', 'raise AssertionError(42)'])",
    "import os; os.system('echo native-execution-must-not-run')",
])
def test_frozen_offline_scope_rejects_native_processes(operation):
    from experiments.input_registered_bank.review_sources import _OFFLINE_PROCESS_SETUP

    result = subprocess.run([sys.executable, "-I", "-c", _OFFLINE_PROCESS_SETUP + operation,
                             ".", "unused-recording"], capture_output=True, text=True)
    assert result.returncode != 0
    assert "captured waveform replay cannot launch a native process" in result.stderr
    assert "native-execution-must-not-run" not in result.stdout
