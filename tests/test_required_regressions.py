from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("required_regressions_tested", ROOT / "tools/required_regressions.py")
ci = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ci)
COMMIT = "a" * 40


def reports():
    nodes = ["tests/test_one.py::test_component", "tests/test_soc_integration.py::test_complete_soc",
             "tests/test_soc_integration.py::test_real_memory_controllers_boot_save_reboot_and_explorer_handoff"]
    return [{
        "format": "shamaos-required-regressions-v1",
        "source_commit": COMMIT, "partition": part,
        "excluded_file": ci.EXCLUDED_FILE,
        "collected": nodes[:], "selected": [n for n in nodes if ci.partition_for(n) == part],
        "collection_errors": [], "exit_code": 0,
        "outcomes": {
            n: [{"when": phase, "outcome": "passed"} for phase in ("setup", "call", "teardown")]
            for n in nodes if ci.partition_for(n) == part
        },
    } for part in ci.PARTITIONS]


def test_full_collection_is_covered_once_and_remains_fixture_scope():
    actual = ci.validate_aggregate(reports(), source_commit=COMMIT)
    assert actual["tests"] == 3
    assert actual["partitions"] == {"components": 1, "system-lifecycle": 1, "system-other": 1}
    assert actual["complete_machine_runtime_verified"] is False


@pytest.mark.parametrize("corruption", [
    "missing_job", "duplicate_job", "wrong_commit", "missing_collection", "different_collection",
    "duplicate_collected", "missing_selected", "overlap", "missing_outcome", "unexpected_outcome",
    "skip", "fail", "teardown_error", "missing_teardown", "duplicate_call",
    "collection_skip", "exit_failure", "boolean_exit", "different_exclusion",
])
def test_aggregate_rejects_incomplete_coverage_or_nonpassing_phase(corruption):
    data = reports()
    one = data[0]
    node = one["selected"][0]
    if corruption == "missing_job": data.pop()
    elif corruption == "duplicate_job": data[1] = deepcopy(one)
    elif corruption == "wrong_commit": one["source_commit"] = "b" * 40
    elif corruption == "missing_collection": one["collected"] = []
    elif corruption == "different_collection": data[1]["collected"].append("tests/test_extra.py::test_missing")
    elif corruption == "duplicate_collected": one["collected"].append(node)
    elif corruption == "missing_selected": one["selected"] = []
    elif corruption == "overlap": one["selected"].append(data[1]["selected"][0])
    elif corruption == "missing_outcome": one["outcomes"].pop(node)
    elif corruption == "unexpected_outcome": one["outcomes"]["extra"] = []
    elif corruption == "skip": one["outcomes"][node][1]["outcome"] = "skipped"
    elif corruption == "fail": one["outcomes"][node][1]["outcome"] = "failed"
    elif corruption == "teardown_error": one["outcomes"][node][2]["outcome"] = "failed"
    elif corruption == "missing_teardown": one["outcomes"][node].pop()
    elif corruption == "duplicate_call": one["outcomes"][node].append(one["outcomes"][node][1].copy())
    elif corruption == "collection_skip": one["collection_errors"] = [{"nodeid": "x", "outcome": "skipped"}]
    elif corruption == "exit_failure": one["exit_code"] = 2
    elif corruption == "boolean_exit": one["exit_code"] = False
    else: one["excluded_file"] = "tests/test_other.py"
    with pytest.raises(ValueError):
        ci.validate_aggregate(data, source_commit=COMMIT)


def fixture_repo(tmp_path, *, skip=False):
    (tmp_path / "tools").mkdir()
    helper = tmp_path / "tools/required_regressions.py"
    helper.write_bytes((ROOT / "tools/required_regressions.py").read_bytes())
    tests = tmp_path / "tests"
    tests.mkdir()
    (tmp_path / ".gitignore").write_text("build/\n__pycache__/\n.pytest_cache/\n")
    (tmp_path / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n")
    (tests / "test_a.py").write_text(
        "import pytest\ndef test_a():\n    " + ("pytest.skip('must fail required gate')" if skip else "assert True") + "\n"
    )
    (tests / "test_new.py").write_text("def test_new_file_is_included():\n    assert True\n")
    (tests / "test_soc_integration.py").write_text(
        "def test_soc():\n    assert True\n"
        "def test_real_memory_controllers_boot_save_reboot_and_explorer_handoff():\n    assert True\n")
    (tests / "test_world_blockstates.py").write_text("raise AssertionError('belongs to separate world backend')\n")
    for args in (
        ["init"], ["add", "."],
        ["-c", "user.name=CI Test", "-c", "user.email=ci@example.invalid", "commit", "-m", "fixture"],
    ):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True).strip()
    return helper, head


def test_real_pytest_partitions_collect_identical_universe_without_losing_new_files(tmp_path):
    helper, head = fixture_repo(tmp_path)
    data = []
    for part in ci.PARTITIONS:
        result = subprocess.run([
            sys.executable, str(helper), "run", "--partition", part,
            "--output", f"build/{part}",
        ], cwd=tmp_path, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        data.append(json.loads((tmp_path / f"build/{part}/report.json").read_text()))
        assert (tmp_path / f"build/{part}/results.xml").is_file()
    assert all(d["collected"] == data[0]["collected"] for d in data)
    assert ci.validate_aggregate(data, source_commit=head)["tests"] == 4
    assert len(data[0]["selected"]) == 2
    assert len(data[1]["selected"]) == 1
    assert len(data[2]["selected"]) == 1
    result = subprocess.run([
        sys.executable, str(helper), "verify", "--reports", "build",
        "--source-commit", head, "--output", "build/aggregate.json",
        "--junit-output", "build/behavioral-results.xml",
    ], cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_real_pytest_skip_does_not_turn_into_green_required_gate(tmp_path):
    helper, _ = fixture_repo(tmp_path, skip=True)
    result = subprocess.run([
        sys.executable, str(helper), "run", "--partition", "components",
        "--output", "build/components",
    ], cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode != 0
    assert "did not pass each phase" in result.stderr


def test_real_pytest_refuses_dirty_tracked_source(tmp_path):
    helper, _ = fixture_repo(tmp_path)
    (tmp_path / "tests/test_a.py").write_text("def test_a():\n    assert False\n")
    result = subprocess.run([
        sys.executable, str(helper), "run", "--partition", "components",
        "--output", "build/components",
    ], cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode != 0
    assert "clean tracked source" in result.stderr
    assert not (tmp_path / "build/components").exists()


def test_junit_mapping_preserves_parameters_containing_scope_separators():
    assert ci.junit_identity("tests/test_x.py::TestThing::test_case[left::right]") == (
        "tests.test_x.TestThing", "test_case[left::right]"
    )


@pytest.mark.parametrize("damage", ["none", "wrong_case", "missing", "duplicate", "skipped", "failed", "bad_count"])
def test_junit_merge_checks_real_case_identity_and_retains_legacy_root(tmp_path, damage):
    from xml.etree import ElementTree as ET
    data = reports()
    for report in data:
        folder = tmp_path / report["partition"]
        folder.mkdir()
        root = ET.Element("testsuites")
        suite = ET.SubElement(root, "testsuite", tests="1", errors="0", failures="0", skipped="0", name="pytest")
        classname, name = ci.junit_identity(report["selected"][0])
        case = ET.SubElement(suite, "testcase", classname=classname, name=name, time="0.1")
        if report["partition"] == "system-other":
            if damage == "wrong_case": case.set("name", "test_not_the_selected_case")
            elif damage == "missing": suite.remove(case); suite.set("tests", "0")
            elif damage == "duplicate": suite.append(deepcopy(case)); suite.set("tests", "2")
            elif damage == "skipped": ET.SubElement(case, "skipped")
            elif damage == "failed": ET.SubElement(case, "failure")
            elif damage == "bad_count": suite.set("tests", "3")
        ET.ElementTree(root).write(folder / "results.xml")
    output = tmp_path / "behavioral-results.xml"
    if damage == "none":
        ci.merge_junit(data, tmp_path, output)
        assert len(ET.parse(output).findall(".//testcase")) == 3
        assert len(ET.parse(output).findall("./testsuite")) == 3
    else:
        with pytest.raises(ValueError):
            ci.merge_junit(data, tmp_path, output)
        assert not output.exists()
