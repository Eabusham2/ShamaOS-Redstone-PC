"""Run disjoint required CI partitions and reject incomplete aggregate coverage.

Every job collects the same complete suite before selecting its assigned tests.
The aggregate gate requires every selected test's setup, call and teardown to
pass, and proves that the selections cover the exact collected suite once.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import subprocess
import sys

PARTITIONS = ("components", "system-lifecycle", "system-other")
SYSTEM_FILE = "tests/test_soc_integration.py"
EXCLUDED_FILE = "tests/test_world_blockstates.py"
LIFECYCLE_TESTS = frozenset({
    "test_boot_edit_dirty_close_shutdown_reboot_and_explorer_handoff",
    "test_real_memory_controllers_boot_save_reboot_and_explorer_handoff",
    "test_real_memory_controllers_assemble_typed_source_and_run_saved_binary",
    "test_physical_keyboard_source_assembly_runs_saved_binary_and_exits_halted_app",
})


def partition_for(nodeid: str) -> str:
    source, _, test = nodeid.partition("::")
    if source != SYSTEM_FILE:
        return "components"
    return "system-lifecycle" if test.split("[", 1)[0] in LIFECYCLE_TESTS else "system-other"


def _unique_strings(value, label: str) -> set[str]:
    if not isinstance(value, list) or not all(isinstance(x, str) and x for x in value):
        raise ValueError(f"{label} must be a list of node IDs.")
    if len(set(value)) != len(value):
        raise ValueError(f"{label} contains duplicate node IDs.")
    return set(value)


def validate_partition(report: dict, *, source_commit: str) -> set[str]:
    if report.get("format") != "shamaos-required-regressions-v1":
        raise ValueError("Unknown required-regression report format.")
    part = report.get("partition")
    if part not in PARTITIONS or report.get("source_commit") != source_commit:
        raise ValueError("Missing partition or different source commit.")
    if report.get("excluded_file") != EXCLUDED_FILE:
        raise ValueError("The separately required world backend is the only excluded file.")
    universe = _unique_strings(report.get("collected"), "Collected suite")
    selected = _unique_strings(report.get("selected"), "Selected partition")
    if not universe or not selected:
        raise ValueError("Required suite and partition must be nonempty.")
    if any(n.split("::", 1)[0] == EXCLUDED_FILE for n in universe):
        raise ValueError("The collected suite includes the separate world-backend job.")
    expected = {node for node in universe if partition_for(node) == part}
    if selected != expected:
        raise ValueError("The partition omitted tests or selected another partition's tests.")
    if type(report.get("exit_code")) is not int or report["exit_code"] != 0:
        raise ValueError("Required pytest execution did not finish successfully.")
    if report.get("collection_errors") != []:
        raise ValueError("Required collection did not finish successfully.")
    outcomes = report.get("outcomes")
    if not isinstance(outcomes, dict) or set(outcomes) != selected:
        raise ValueError("Missing or unexpected executed test outcomes.")
    expected_phases = Counter({("setup", "passed"): 1, ("call", "passed"): 1, ("teardown", "passed"): 1})
    for node, phases in outcomes.items():
        if not isinstance(phases, list) or any(
            not isinstance(row, dict) or set(row) != {"when", "outcome"} for row in phases
        ):
            raise ValueError(f"Malformed outcomes for {node}.")
        actual = Counter((row["when"], row["outcome"]) for row in phases)
        if actual != expected_phases:
            raise ValueError(f"Test did not pass each phase exactly once: {node}.")
    return universe


def validate_aggregate(reports: list[dict], *, source_commit: str) -> dict:
    if re.fullmatch(r"[0-9a-f]{40}", source_commit) is None:
        raise ValueError("A full exact-source commit is required.")
    if len(reports) != len(PARTITIONS) or Counter(r.get("partition") for r in reports) != Counter(PARTITIONS):
        raise ValueError("Exactly one report from each required partition is required.")
    universes = [validate_partition(r, source_commit=source_commit) for r in reports]
    if any(u != universes[0] for u in universes[1:]):
        raise ValueError("Required jobs collected different test suites.")
    covered = Counter(node for r in reports for node in r["selected"])
    if set(covered) != universes[0] or any(count != 1 for count in covered.values()):
        raise ValueError("Required partitions must cover the complete suite exactly once.")
    return {
        "format": "shamaos-required-regression-aggregate-v1",
        "source_commit": source_commit,
        "status": "passed",
        "tests": len(covered),
        "partitions": {r["partition"]: len(r["selected"]) for r in reports},
        "separate_required_world_backend": EXCLUDED_FILE,
        "complete_machine_runtime_verified": False,
    }



def junit_identity(nodeid: str) -> tuple[str, str]:
    prefix, bracket, parameters = nodeid.partition("[")
    parts = prefix.split("::")
    parts[0] = parts[0].replace("/", ".").removesuffix(".py")
    parts[-1] += bracket + parameters
    return ".".join(parts[:-1]), parts[-1]


def merge_junit(reports: list[dict], reports_dir: Path, output: Path) -> None:
    from copy import deepcopy
    from xml.etree import ElementTree as ET

    combined = ET.Element("testsuites")
    for report in reports:
        part = report["partition"]
        root = ET.parse(reports_dir / part / "results.xml").getroot()
        suites = list(root) if root.tag == "testsuites" else [root]
        if not suites or any(s.tag != "testsuite" for s in suites):
            raise ValueError(f"Missing JUnit suites for {part}.")
        expected = Counter(junit_identity(node) for node in report["selected"])
        actual = Counter()
        for suite in suites:
            cases = list(suite.findall("testcase"))
            if (suite.get("tests") != str(len(cases))
                    or any(suite.get(key) != "0" for key in ("errors", "failures", "skipped"))):
                raise ValueError(f"Incomplete or nonpassing JUnit suite for {part}.")
            for case in cases:
                if any(case.find(key) is not None for key in ("error", "failure", "skipped")):
                    raise ValueError(f"Nonpassing JUnit case in {part}.")
                actual[(case.get("classname"), case.get("name"))] += 1
            copied = deepcopy(suite)
            copied.set("name", f"{part}:{suite.get('name', 'pytest')}")
            combined.append(copied)
        if actual != expected:
            raise ValueError(f"JUnit cases do not match the executed partition: {part}.")
    output.parent.mkdir(parents=True, exist_ok=True)
    pending = output.with_suffix(output.suffix + ".tmp")
    ET.ElementTree(combined).write(pending, encoding="utf-8", xml_declaration=True)
    pending.replace(output)


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + ".tmp")
    pending.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    pending.replace(path)


class RequiredPartition:
    def __init__(self, partition: str, source_commit: str, output: Path):
        self.output = output
        self.report = {
            "format": "shamaos-required-regressions-v1",
            "source_commit": source_commit,
            "partition": partition,
            "excluded_file": EXCLUDED_FILE,
            "collected": [],
            "selected": [],
            "outcomes": {},
            "collection_errors": [],
            "exit_code": None,
        }

    def pytest_collection_modifyitems(self, session, config, items):
        part = self.report["partition"]
        self.report["collected"] = [item.nodeid for item in items]
        selected = [item for item in items if partition_for(item.nodeid) == part]
        deselected = [item for item in items if partition_for(item.nodeid) != part]
        self.report["selected"] = [item.nodeid for item in selected]
        config.hook.pytest_deselected(items=deselected)
        items[:] = selected
        _write(self.output / "report.json", self.report)

    def pytest_collectreport(self, report):
        if report.failed or report.skipped:
            self.report["collection_errors"].append({
                "nodeid": report.nodeid, "outcome": report.outcome,
            })

    def pytest_runtest_logreport(self, report):
        self.report["outcomes"].setdefault(report.nodeid, []).append({
            "when": report.when, "outcome": report.outcome,
        })
        if report.when == "teardown":
            finished = sum(
                any(p["when"] == "teardown" for p in phases)
                for phases in self.report["outcomes"].values()
            )
            _write(self.output / "progress.json", {
                "partition": self.report["partition"],
                "source_commit": self.report["source_commit"],
                "last_completed": report.nodeid,
                "completed": finished,
                "selected": len(self.report["selected"]),
            })


def run_partition(partition: str, output: Path) -> int:
    import pytest

    root = Path(__file__).resolve().parents[1]
    if Path.cwd().resolve() != root:
        raise ValueError("Run required regressions from the repository root.")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    if subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], text=True):
        raise ValueError("Required CI evidence needs a clean tracked source checkout.")
    output.mkdir(parents=True, exist_ok=False)
    plugin = RequiredPartition(partition, commit, output)
    code = pytest.main([
        "-ra", f"--ignore={EXCLUDED_FILE}", "--durations=25",
        f"--junitxml={output / 'results.xml'}",
    ], plugins=[plugin])
    plugin.report["exit_code"] = int(code)
    _write(output / "report.json", plugin.report)
    if code:
        return int(code)
    validate_partition(plugin.report, source_commit=commit)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--partition", choices=PARTITIONS, required=True)
    run.add_argument("--output", type=Path, required=True)
    check = sub.add_parser("verify")
    check.add_argument("--reports", type=Path, required=True)
    check.add_argument("--source-commit", required=True)
    check.add_argument("--output", type=Path, required=True)
    check.add_argument("--junit-output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "run":
            return run_partition(args.partition, args.output)
        reports = [
            json.loads((args.reports / part / "report.json").read_text(encoding="utf-8"))
            for part in PARTITIONS
        ]
        aggregate = validate_aggregate(reports, source_commit=args.source_commit)
        merge_junit(reports, args.reports, args.junit_output)
        _write(args.output, aggregate)
        print(json.dumps(aggregate, sort_keys=True))
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Required regression gate failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
