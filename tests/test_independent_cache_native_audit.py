"""Independent native evidence checks reject omitted bits and distorted timing."""
from copy import deepcopy
import json

import pytest

from experiments.memory_controller_runtime import audit_native_connected as audit, scenarios


def clock_fixture():
    scenario = {"steps": [{"drive": {"clk": False, "rst": True}, "ticks": 2},
        *[step for _ in range(3) for step in ({"drive": {"clk": True}, "ticks": 2},
                                             {"drive": {"clk": False}, "ticks": 2})],
        {"ticks": 2}]}
    trace = [False] * 16
    for start in (3, 7, 11):
        trace[start:start + 2] = [True, True]
    return scenario, {"traces": {"a": list(trace), "b": list(trace)}}


def test_independent_clock_audit_derives_same_latency_from_actual_requested_rises():
    scenario, result = clock_fixture()
    checked = audit.check_clock_traces(result, {"a", "b"}, scenario)
    assert checked["passed"]
    assert checked["requested_rise_ticks"] == [2, 6, 10]
    assert checked["observed_latencies_ticks"] == [2, 2, 2]
    assert checked["trace_sample_zero_is_tick"] == 1


@pytest.mark.parametrize("fault", ["spacing", "skew", "early", "width", "missing", "truncated", "type", "tail-high"])
def test_independent_clock_audit_rejects_incomplete_or_distorted_observations(fault):
    scenario, result = clock_fixture()
    if fault == "spacing":
        for trace in result["traces"].values():
            trace[7:10] = [False, True, True]
    elif fault == "skew":
        result["traces"]["b"] = [False] + result["traces"]["b"][:-1]
    elif fault == "early":
        result["traces"] = {name: trace[3:] + [False] * 3 for name, trace in result["traces"].items()}
    elif fault == "width":
        for trace in result["traces"].values():
            trace[4] = False
    elif fault == "missing":
        del result["traces"]["b"]
    elif fault == "truncated":
        result["traces"]["b"].pop()
    elif fault == "type":
        result["traces"]["b"][0] = 0
    else:
        result["traces"]["b"][-1] = True
    if fault in ("missing", "truncated", "type", "tail-high"):
        with pytest.raises(ValueError):
            audit.check_clock_traces(result, {"a", "b"}, scenario)
    else:
        assert not audit.check_clock_traces(result, {"a", "b"}, scenario)["passed"]


@pytest.fixture(scope="module")
def full_boundary_document(tmp_path_factory):
    directory = tmp_path_factory.mktemp("full-cache-boundary")
    inputs = {scenarios.host(name, bit): [index * 4, 0, 0]
              for index, (name, bit) in enumerate((name, bit) for name, width in scenarios.HOST_WIDTHS.items() for bit in range(width))}
    words = scenarios.initial_words(capacity_bytes=4096)
    outputs = {name: [index * 4, 1, 0] for index, name in enumerate(audit.STORED_NAMES)}
    outputs["port:req_ready:0"] = [0, 4, 0]
    frames = [scenarios.Frame("complete-storage", 0, expect={"port:req_ready:0": False},
                              stored_words=words, capacity_bytes=4096)]
    path = directory / "scenario.json"
    scenarios.write_scenario(path, frames, inputs, outputs, half_period=8, initial_ticks=16, trace_outputs=None)
    return json.loads(path.read_text()), frames, inputs, outputs


def test_independent_scenario_audit_checks_all_32768_bits_with_traces_disabled(full_boundary_document):
    document, frames, inputs, outputs = full_boundary_document
    checked = audit.check_scenario_document(document, frames, inputs, outputs, 8, 16)
    assert checked["all_bit_assertions"] == 32768
    assert checked["assertions"] == 32769
    assert checked["storage_boundaries"] == 1


@pytest.mark.parametrize("fault", ["lost-bit", "changed-bit", "no-step", "tick", "trace-scope", "internal-drive", "wrong-map", "integer-bool"])
def test_independent_scenario_audit_rejects_omitted_or_substituted_native_expectations(full_boundary_document, fault):
    original, frames, inputs, outputs = full_boundary_document
    document = deepcopy(original)
    step = document["steps"][0]
    if fault == "lost-bit":
        del step["expect"]["storage:0:1023:31"]
    elif fault == "changed-bit":
        step["expect"]["storage:0:1023:31"] = not step["expect"]["storage:0:1023:31"]
    elif fault == "no-step":
        document["steps"].clear()
    elif fault == "tick":
        step["ticks"] = 1
    elif fault == "trace-scope":
        document.update(capture_trace=True, trace_outputs=["port:req_ready:0"])
    elif fault == "internal-drive":
        step["drive"] = {"port:selected_word:0": True}
    elif fault == "wrong-map":
        document["outputs"]["storage:0:1023:31"] = document["outputs"]["storage:0:0:0"]
    else:
        step["expect"]["storage:0:1023:31"] = int(step["expect"]["storage:0:1023:31"])
    with pytest.raises(ValueError):
        audit.check_scenario_document(document, frames, inputs, outputs, 8, 16)


def test_independent_json_reader_rejects_duplicate_stored_bit_fields(tmp_path):
    path = tmp_path / "duplicate.json"
    path.write_text('{"storage:0:0:0":true,"storage:0:0:0":false}')
    with pytest.raises(ValueError, match="repeats"):
        audit.read_json(path)


def test_independent_complete_schedule_matches_immutable_rtl_all_bit_oracle():
    cold, reopened, final = audit.bound_transaction_frames()
    assert (len(cold), len(reopened), len(final)) == (547, 54, 1024)
    assert [sum(frame.stored_words is not None for frame in frames) for frames in (cold, reopened)] == [43, 6]


def runtime_result():
    imported = {"stream_sha256": "a" * 64, "source": {"native_world": "/complete/world"},
                "chunks": 2, "nonair": 123, "comparators": 1}
    native = {"status": "passed", "imported_stream_sha256": imported["stream_sha256"],
        "scenario_sha256": "b" * 64, "source": imported["source"], "loaded_chunks": 2,
        "decoded_nonair_blocks": 123, "initialized_nonair_blocks": 123, "comparator_entities": 1,
        "steps": 4, "ticks": 8, "assertions": 9, "engine_revision": audit.ENGINE_REVISION,
        "engine_registry_patch_sha256": audit.TERRAIN_PATCH, "complete_machine_runtime_verified": False,
        "trace_capture": {"declared_outputs": 2, "mode": "disabled", "traced_outputs": 0, "samples_per_output": 0},
        "traces": {"storage:0:0:0": [], "port:req_ready:0": []}}
    return imported, native


@pytest.mark.parametrize("fault", [None, "world", "scenario", "source", "chunks", "nonair", "initialization",
                                   "comparators", "steps", "ticks", "assertions", "engine", "terrain", "scope", "traces"])
def test_independent_result_audit_binds_complete_native_inventory(fault):
    imported, native = runtime_result()
    changed = {"world": ("imported_stream_sha256", "c" * 64), "scenario": ("scenario_sha256", "c" * 64),
        "source": ("source", {}), "chunks": ("loaded_chunks", 1), "nonair": ("decoded_nonair_blocks", 1),
        "initialization": ("initialized_nonair_blocks", 1), "comparators": ("comparator_entities", 0),
        "steps": ("steps", 3), "ticks": ("ticks", 7), "assertions": ("assertions", 8),
        "engine": ("engine_revision", "other"), "terrain": ("engine_registry_patch_sha256", "other"),
        "scope": ("complete_machine_runtime_verified", True),
        "traces": ("traces", {"unrelated": [], "port:req_ready:0": []})}
    if fault is not None:
        field, value = changed[fault]
        native[field] = value

    def check():
        audit.check_runtime_result(native, imported, "b" * 64, steps=4, ticks=8, assertions=9,
                                   output_names={"storage:0:0:0", "port:req_ready:0"})

    if fault is None:
        check()
    else:
        with pytest.raises(ValueError):
            check()


def test_snapshot_endpoint_reader_requires_actual_completion_boundary(tmp_path):
    path = tmp_path / "snapshot.jsonl"
    header = {"kind": "snapshot_header"}
    footer = {"kind": "snapshot_complete", "chunks": 2}
    path.write_bytes(json.dumps(header).encode() + b"\n" + b"x" * 65536 + b"\n" + json.dumps(footer).encode() + b"\n")
    assert audit.snapshot_endpoints(path) == (header, footer)
    path.write_bytes(path.read_bytes()[:-1])
    with pytest.raises(ValueError, match="truncated"):
        audit.snapshot_endpoints(path)


@pytest.mark.parametrize("fault", [None, "region", "level", "world", "pending"])
def test_imported_source_binding_rejects_saved_world_substitution(tmp_path, fault):
    inventory = {"level.dat": {"bytes": 16, "sha256": "a" * 64},
                 "region/r.0.0.mca": {"bytes": 8192, "sha256": "b" * 64}}
    source = {"native_world": str(tmp_path.resolve()), "data_version": 3700,
              "saved_native_pending_ticks": 0, "level_dat_sha256": "a" * 64,
              "regions": [{"name": "r.0.0.mca", "bytes": 8192, "sha256": "b" * 64}]}
    if fault == "region":
        source["regions"][0]["sha256"] = "c" * 64
    elif fault == "level":
        source["level_dat_sha256"] = "c" * 64
    elif fault == "world":
        source["native_world"] = "/another/world"
    elif fault == "pending":
        source["saved_native_pending_ticks"] = 1
    if fault is None:
        audit.check_imported_source({"source": source}, tmp_path, inventory)
    else:
        with pytest.raises(ValueError):
            audit.check_imported_source({"source": source}, tmp_path, inventory)
