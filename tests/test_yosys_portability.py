import re
import subprocess
from pathlib import Path

import pytest

import shamaos.synthesis as synthesis


@pytest.mark.parametrize("help_text,expected", [("    -fast\n      fast mapping\n", True), ("    -g type\n", False)])
def test_mapping_uses_installed_abc_capabilities(tmp_path, monkeypatch, help_text, expected):
    synthesis._abc_supports_fast.cache_clear()
    monkeypatch.setattr(synthesis.shutil, "which", lambda _: "/fake/yosys")
    scripts = []

    def run(command, **kwargs):
        if "help abc" in command:
            return subprocess.CompletedProcess(command, 0, help_text)
        script = Path(command[-1]).read_text()
        scripts.append(script)
        path = re.search(r'^write_json "([^"]+)"', script, re.MULTILINE).group(1)
        Path(path).write_text('{"modules": {}}')
        return subprocess.CompletedProcess(command, 0, "")

    monkeypatch.setattr(synthesis.subprocess, "run", run)
    synthesis.synthesize_json([tmp_path / "input.sv"], top="tiny", output_json=tmp_path / "out.json")
    abc_line = next(line for line in scripts[0].splitlines() if line.startswith("abc "))
    assert ("-fast" in abc_line) is expected
    assert "-g NAND,NOR" in abc_line
    synthesis._abc_supports_fast.cache_clear()


def test_legacy_fast_script_is_normalized_for_old_yosys():
    assert synthesis._portable_mapping("abc -fast -g NAND,NOR\n", fast=False) == "abc -g NAND,NOR\n"


def test_failed_capability_probe_uses_portable_command(monkeypatch):
    synthesis._abc_supports_fast.cache_clear()
    monkeypatch.setattr(synthesis.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 1, "unknown option -Q"))
    assert synthesis._abc_supports_fast("old-yosys") is False
    synthesis._abc_supports_fast.cache_clear()
