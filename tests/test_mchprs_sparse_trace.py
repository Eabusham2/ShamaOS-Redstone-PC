"""Select retained tick traces without weakening native output assertions."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import re
import subprocess

import pytest

from shamaos.hardware.memory import MemoryBankSpec, bank_ports, iter_memory_bank
from shamaos.mchprs_import import digest, export_world
from shamaos.model import Vec3
from shamaos.worldio import NativeWorldWriter
from test_mchprs_sparse_runtime import _run_sparse, _runner, saved_logic


EXECUTION_FIELDS = (
    "status", "assertions", "engine_revision", "engine_registry",
    "engine_registry_patch_sha256", "loaded_chunks", "uniform_sections",
    "chunks_after_drives", "decoded_nonair_blocks", "initialized_nonair_blocks",
    "paletted_storage_bytes_before_updates", "comparator_entities", "steps", "ticks",
    "scheduled_ticks_total", "peak_pending_ticks", "pending_ticks_after_scenario",
    "imported_stream_sha256", "complete_machine_runtime_verified",
)


def same_execution(full, selected):
    assert {key: full[key] for key in EXECUTION_FIELDS} == \
           {key: selected[key] for key in EXECUTION_FIELDS}


def run_snapshot(runner, stream, scenario, directory, name):
    scenario_path = directory / f"{name}-scenario.json"
    scenario_path.write_text(json.dumps(scenario, separators=(",", ":")))
    destination = directory / f"{name}-snapshot.jsonl"
    result = subprocess.run([runner, str(stream), str(scenario_path), "1000",
                             "--snapshot", str(destination)],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
    report = json.loads(result.stdout)
    assert report["status"] == "passed"
    assert report["scenario_sha256"] == digest(scenario_path)
    assert report["imported_stream_sha256"] == digest(stream)
    assert report["snapshot"]["sha256"] == digest(destination)
    assert report["pending_ticks_after_scenario"] == 0
    return report, [json.loads(line) for line in destination.read_text().splitlines()]


@pytest.mark.parametrize("names", (["BUF"], ["NOT"], ["NOT", "BUF"]))
def test_selected_logic_traces_equal_filtering_full_actual_engine_trace(saved_logic, tmp_path, names):
    _, stream, scenario, _ = saved_logic
    runner = _runner("SHAMA_MCHPRS_SPARSE_RUNNER")
    full = _run_sparse(runner, stream, scenario, tmp_path)
    selected = _run_sparse(runner, stream, scenario | {"trace_outputs": names}, tmp_path)
    assert selected["traces"] == {name: full["traces"][name] for name in names}
    same_execution(full, selected)
    assert full["trace_capture"] == {
        "mode": "all", "declared_outputs": 2, "traced_outputs": 2, "samples_per_output": 64}
    assert selected["trace_capture"] == {
        "mode": "selected", "declared_outputs": 2,
        "traced_outputs": len(names), "samples_per_output": 64}


@pytest.mark.parametrize("explicit_flag", (False, True))
def test_disabled_trace_keeps_legacy_empty_arrays_and_full_assertions(saved_logic, tmp_path, explicit_flag):
    _, stream, original, _ = saved_logic
    scenario = copy.deepcopy(original)
    scenario.pop("capture_trace")
    if explicit_flag:
        scenario["capture_trace"] = False
    result = _run_sparse(_runner("SHAMA_MCHPRS_SPARSE_RUNNER"), stream, scenario, tmp_path)
    assert result["traces"] == {name: [] for name in scenario["outputs"]}
    assert result["assertions"] == sum(len(step.get("expect", {})) for step in scenario["steps"])
    assert result["trace_capture"] == {
        "mode": "disabled", "declared_outputs": 2, "traced_outputs": 0, "samples_per_output": 0}


@pytest.mark.parametrize("selection,flag,error", (
    (["missing"], True, "unknown trace output name missing"),
    (["BUF", "BUF"], True, "duplicate trace output name BUF"),
    ([], True, "trace_outputs must select at least one declared output"),
    (["BUF"], False, "trace_outputs requires capture_trace=true"),
    (["BUF"], None, "trace_outputs requires capture_trace=true"),
    (None, True, "invalid type: null, expected a sequence"),
    ("BUF", True, "scenario JSON"),
    ([1], True, "scenario JSON"),
))
def test_invalid_or_contradictory_trace_selection_is_rejected(
    saved_logic, tmp_path, selection, flag, error
):
    _, stream, original, _ = saved_logic
    scenario = copy.deepcopy(original)
    scenario["trace_outputs"] = selection
    if flag is None:
        scenario.pop("capture_trace")
    else:
        scenario["capture_trace"] = flag
    rejected = _run_sparse(_runner("SHAMA_MCHPRS_SPARSE_RUNNER"), stream, scenario,
                           tmp_path, succeeds=False)
    assert error in rejected


def test_selected_trace_only_execution_still_does_not_claim_asserted_success(saved_logic, tmp_path):
    _, stream, original, _ = saved_logic
    scenario = copy.deepcopy(original)
    for step in scenario["steps"]:
        step.pop("expect", None)
    scenario["trace_outputs"] = ["NOT"]
    result = _run_sparse(_runner("SHAMA_MCHPRS_SPARSE_RUNNER"), stream, scenario,
                         tmp_path, status="executed-trace-only")
    assert result["assertions"] == 0
    assert set(result["traces"]) == {"NOT"}
    assert len(result["traces"]["NOT"]) == 64


@pytest.fixture(scope="module")
def saved_trace_memory(tmp_path_factory):
    directory = tmp_path_factory.mktemp("selective-trace-native-memory")
    origin = Vec3(32, 64, 32)
    bank = MemoryBankSpec(words=4, word_bits=8)
    pins = bank_ports(origin, bank)
    coord = lambda p: [p.x, p.y, p.z]
    driver = lambda p: coord(p.offset(-1, 0, -1))
    inputs = {f"D{bit}": driver(pin) for bit, pin in enumerate(pins.data_in)}
    inputs |= {f"W{row}": driver(pin) for row, pin in enumerate(pins.write_select)}
    inputs |= {f"R{row}": driver(pin) for row, pin in enumerate(pins.read_select)}
    outputs = {f"Q{bit}": coord(pin) for bit, pin in enumerate(pins.data_out)}
    outputs |= {f"S{row}:{bit}": coord(origin.offset(bit * bank.bit_pitch + 2,
                                                   bank.cell_y, 3 + row * bank.word_pitch))
                for row in range(bank.words) for bit in range(bank.word_bits)}
    initial = [0x12, 0xA5, 0, 0xFF]
    world = directory / "world"
    with NativeWorldWriter(world, surface_y=None, spawn=Vec3(16, 64, 16),
                           spawn_platform=False) as writer:
        for placement in iter_memory_bank(origin, spec=bank, initial=bytes(initial)):
            writer.set_block(placement)
        assert writer.validate()["status"] == "passed"
    stream = directory / "world.jsonl"
    manifest = export_world(world, stream)

    def expected(words, selected=0):
        return ({f"S{row}:{bit}": bool(word >> bit & 1)
                 for row, word in enumerate(words) for bit in range(bank.word_bits)}
                | {f"Q{bit}": bool(selected >> bit & 1) for bit in range(bank.word_bits)})

    modified = list(initial)
    modified[1] = 0x5A
    steps = [
        {"ticks": 256, "expect": expected(initial), "continuous": True},
        {"drive": {f"D{bit}": bool(0x5A >> bit & 1) for bit in range(8)},
         "ticks": 256, "expect": expected(initial)},
        {"drive": {"W1": True}, "ticks": 256, "expect": expected(modified)},
        {"drive": {"W1": False}, "ticks": 256, "expect": expected(modified)},
        {"drive": {"R1": True}, "ticks": 256, "expect": expected(modified, 0x5A)},
        {"drive": {"R1": False}, "ticks": 256, "expect": expected(modified)},
        {"drive": {f"D{bit}": False for bit in range(8)},
         "ticks": 256, "expect": expected(modified), "continuous": True},
        {"ticks": 0, "expect": expected(modified)},
    ]
    assert all(set(step["expect"]) == set(outputs) for step in steps)
    scenario = dict(inputs=inputs, outputs=outputs, initial_ticks=256,
                    steps=steps, capture_trace=True)
    return stream, scenario, manifest


def test_selective_native_memory_trace_preserves_all_assertions_and_exact_snapshot(
    saved_trace_memory, tmp_path
):
    stream, scenario, manifest = saved_trace_memory
    runner = _runner("SHAMA_MCHPRS_SPARSE_RUNNER")
    names = ["Q1", "Q7"]
    full, full_snapshot = run_snapshot(runner, stream, scenario, tmp_path, "full")
    selected, selected_snapshot = run_snapshot(
        runner, stream, scenario | {"trace_outputs": names}, tmp_path, "selected")
    assert selected["traces"] == {name: full["traces"][name] for name in names}
    same_execution(full, selected)
    assert full["assertions"] == selected["assertions"] == 8 * 40
    assert full["ticks"] == selected["ticks"] == 2048
    assert all(len(values) == 1792 for values in selected["traces"].values())
    assert set(selected["traces"]["Q1"]) == {False, True}
    assert selected["traces"]["Q1"][5 * 256 - 1] is True
    assert selected["traces"]["Q1"][-1] is False
    assert selected["trace_capture"] == {
        "mode": "selected", "declared_outputs": 40,
        "traced_outputs": 2, "samples_per_output": 1792}
    # Trace settings change only the scenario's provenance hash. Every native
    # section, exact block property and comparator value is byte-equivalent as
    # a parsed record, and every tick/scheduler field in the header agrees.
    assert full_snapshot[1:] == selected_snapshot[1:]
    assert {k: v for k, v in full_snapshot[0].items() if k != "scenario_sha256"} == \
           {k: v for k, v in selected_snapshot[0].items() if k != "scenario_sha256"}
    report = {
        "kind": "actual-native-selective-trace-equivalence", "status": "passed",
        "storage_bits": 32, "declared_outputs": 40, "selected_outputs": names,
        "asserted_outputs_at_each_boundary": 40, "transaction_boundaries": 8,
        "full_boundary_assertions": selected["assertions"],
        "full_trace_samples": sum(map(len, full["traces"].values())),
        "selected_trace_samples": sum(map(len, selected["traces"].values())),
        "all_selected_samples_equal_full_trace": True,
        "tick_scheduler_and_initialization_metrics_unchanged": True,
        "every_snapshot_state_and_comparator_unchanged": True,
        "source_import": manifest,
        "runtime": {key: selected[key] for key in EXECUTION_FIELDS},
        "runner_sha256": digest(Path(runner)),
        "source_sha256": {
            name: digest(Path(__file__).resolve().parents[1] / name)
            for name in ("tests/test_mchprs_sparse_trace.py", "tests/mchprs_sparse_runtime/src/main.rs",
                         "tests/mchprs_sparse_runtime/src/snapshot.rs")},
        "scope": "Four native bytes plus saved two-output logic tests; no complete-cache or whole-machine execution claim.",
    }
    output = Path(os.environ.get("SHAMA_VALIDATION_REPORT_DIR", tmp_path))
    output.mkdir(parents=True, exist_ok=True)
    (output / "native-selective-trace-equivalence.json").write_text(json.dumps(report, indent=2) + "\n")


@pytest.mark.parametrize("mode,expected_tick", (("boundary", 256), ("continuous", 0), ("zero-tick", 0)))
def test_wrong_untraced_storage_bit_still_fails_at_same_tick(
    saved_trace_memory, tmp_path, mode, expected_tick
):
    stream, original, _ = saved_trace_memory
    scenario = copy.deepcopy(original)
    scenario["steps"] = [scenario["steps"][0]]
    step = scenario["steps"][0]
    step["expect"]["S0:0"] = not step["expect"]["S0:0"]
    step["continuous"] = mode == "continuous"
    if mode == "zero-tick":
        step["ticks"] = 0
    runner = _runner("SHAMA_MCHPRS_SPARSE_RUNNER")
    full = _run_sparse(runner, stream, scenario, tmp_path, succeeds=False)
    selected = _run_sparse(runner, stream, scenario | {"trace_outputs": ["Q0"]},
                           tmp_path, succeeds=False)
    witness = rf"step=0 tick={expected_tick} output=S0:0 block=Repeater"
    assert re.search(witness, full) and re.search(witness, selected)
    assert "left: false" in full and "right: true" in full
    assert "left: false" in selected and "right: true" in selected
