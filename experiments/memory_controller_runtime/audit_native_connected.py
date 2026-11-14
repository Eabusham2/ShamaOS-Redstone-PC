"""Independently check complete 4 KiB cache runtime evidence as it arrives.

No native process is started and no acceptance flag is inferred from a label.
File identities, exact expanded assertions, pulse timing and fresh-process
snapshot bindings are recomputed. Geometry and native behavior remain distinct.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from experiments.memory_controller_runtime import scenarios
from shamaos.mchprs_snapshot import ENGINE_REVISION, TERRAIN_PATCH
from shamaos.model import Vec3
from shamaos.worldio import NativeWorldReader

CAPACITY = 4096
STORED_NAMES = {f"storage:0:{row}:{bit}" for row in range(1024) for bit in range(32)}
RUNTIME_SOURCES = {
    "experiments/memory_controller_runtime/run_connected.py",
    "experiments/memory_controller_runtime/scenarios.py",
    "experiments/memory_controller_runtime/probe_controller.py",
    "experiments/memory_controller_runtime/save_connected.py",
    "src/shamaos/mchprs_import.py", "src/shamaos/mchprs_snapshot.py",
    "src/shamaos/worldio.py", "src/shamaos/release_io.py", "src/shamaos/model.py",
}


def require(value, message):
    if not value:
        raise ValueError(message)


def unique_object(pairs):
    result = {}
    for name, value in pairs:
        require(name not in result, f"evidence repeats JSON field {name}")
        result[name] = value
    return result


def read_json(path):
    with Path(path).open() as stream:
        return json.load(stream, object_pairs_hook=unique_object)


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def check_file(record):
    path = Path(record["path"])
    require(path.is_file() and sha(path) == record["sha256"], f"bound evidence changed: {path}")
    if "bytes" in record:
        require(path.stat().st_size == record["bytes"], f"bound evidence size changed: {path}")
    return path


def file_record(path):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": sha(path), "bytes": path.stat().st_size}


def check_process(case):
    elapsed, limit = case.get("wall_seconds"), case.get("wall_limit_seconds")
    require(case.get("exit_code") == 0 and case.get("explicit_wall_timeout_reached") is False
            and type(elapsed) in (int, float) and math.isfinite(elapsed)
            and type(limit) is int and 0 <= elapsed < limit,
            "native evidence did not complete successfully inside its deadline")


def check_imported_source(imported, world, inventory):
    """Bind the export's complete source identity without decoding it again."""
    source = imported["source"]
    regions = [{"name": name.removeprefix("region/"), **record}
               for name, record in sorted(inventory.items()) if name.startswith("region/") and name.endswith(".mca")]
    require(source["native_world"] == str(world.resolve()) and source["data_version"] == 3700
            and source["saved_native_pending_ticks"] == 0
            and source["level_dat_sha256"] == inventory["level.dat"]["sha256"]
            and source["regions"] == regions, "native stream source differs from the actual complete saved world")


def snapshot_endpoints(path):
    """Inspect completion as well as the header of an already checksum-bound file."""
    with path.open("rb") as stream:
        header = json.loads(stream.readline(), object_pairs_hook=unique_object)
        stream.seek(max(0, path.stat().st_size - 65536))
        end = stream.read()
    require(end.endswith(b"\n"), "native snapshot is truncated")
    footer = json.loads(end.splitlines()[-1], object_pairs_hook=unique_object)
    return header, footer


def pulse_intervals(values):
    result, start = [], None
    for index, value in enumerate(values):
        require(type(value) is bool, "native pulse sample is not an exact boolean")
        if value and start is None:
            start = index
        elif not value and start is not None:
            result.append((start, index - start))
            start = None
    require(start is None, "native clock trace ends inside a pulse")
    return result


def check_clock_traces(result, names, scenario):
    """Derive actual requested rises independently of the production analyzer."""
    elapsed, level, rises, falls = 0, False, [], []
    for step in scenario["steps"]:
        next_level = step.get("drive", {}).get("clk", level)
        if next_level and not level:
            rises.append(elapsed)
        if level and not next_level:
            falls.append(elapsed)
        level = next_level
        elapsed += step["ticks"]
    require(len(rises) == len(falls) == 3, "clock scenario lost a requested pulse")
    widths = [fall - rise for rise, fall in zip(rises, falls)]
    traces = result["traces"]
    require(set(traces) == set(names), "clock trace inventory differs from every original register")
    require(all(len(trace) == elapsed for trace in traces.values()), "clock trace lost native tick samples")
    runs = {name: pulse_intervals(trace) for name, trace in traces.items()}
    reference = runs[next(iter(runs))]
    identical = all(value == reference for value in runs.values())
    complete = len(reference) == 3 and [width for _, width in reference] == widths
    # The runner samples after each world.tick(), so trace index zero is
    # tick one, not the instant immediately before the first driven step.
    latencies = [start + 1 - requested for (start, _), requested in zip(reference, rises)] if complete else []
    preserved = complete and min(latencies) >= 1 and len(set(latencies)) == 1
    return {"register_clocks": len(names), "requested_rise_ticks": rises,
        "observed_pulses": reference, "observed_latencies_ticks": latencies,
        "trace_sample_zero_is_tick": 1, "clock_tick_origin": "after scenario initial_ticks, before the first driven step",
        "all_registers_identical": identical, "each_requested_pulse_delivered_at_one_fixed_latency": preserved,
        "passed": identical and preserved, "captured_ticks_per_clock": elapsed}


def check_scenario_document(document, frames, inputs, outputs, half_period, initial_ticks):
    """Check every expanded expectation, not only aggregate assertion counts."""
    require(set(document) == {"inputs", "outputs", "initial_ticks", "capture_trace", "steps"}
            and document["inputs"] == inputs and document["outputs"] == outputs
            and document["initial_ticks"] == initial_ticks and document["capture_trace"] is False,
            "transaction document changed native maps, tracing or initial timing")
    require(len(document["steps"]) == len(frames), "transaction scenario lost or added a frame")
    assertions = boundaries = elapsed = 0
    responses = []
    for index, (step, frame) in enumerate(zip(document["steps"], frames)):
        expect = dict(frame.expect or {})
        if frame.stored_words is not None:
            require(len(frame.stored_words) == 1024, "independent frame has incomplete one-bank storage")
            expect.update({f"storage:0:{row}:{bit}": bool(word & (1 << bit))
                           for row, word in enumerate(frame.stored_words) for bit in range(32)})
            require({name for name in step["expect"] if name.startswith("storage:")} == STORED_NAMES,
                    f"frame {index} lost a real stored-bit assertion")
            boundaries += 1
        expected = {"ticks": frame.half_periods * half_period, "expect": expect}
        if frame.drive:
            expected["drive"] = frame.drive
        require(step == expected, f"native frame {index} differs from the complete bound request oracle")
        require(all(type(value) is bool for value in step["expect"].values()), "assertion is not boolean")
        require(set(step.get("drive", {})) <= inputs.keys(), "native frame drives an internal/memory return signal")
        assertions += len(step["expect"])
        elapsed += step["ticks"]
        if frame.label.endswith(":response:rise"):
            response = sum(int(step["expect"][f"port:req_rdata:{bit}"]) << bit for bit in range(32))
            responses.append({"label": frame.label, "sample_tick": initial_ticks + elapsed,
                              "expected_response_word_proven_by_native_assertions": response})
    return {"frames": len(frames), "assertions": assertions, "ticks": initial_ticks + elapsed,
        "storage_boundaries": boundaries, "all_bit_assertions": boundaries * 32768,
        "response_samples": responses,
        "response_timing_scope": "Response values and ready/control levels are checked at exact frame endpoints; per-tick response latency or glitches are not observed when traces are disabled"}


def check_runtime_result(result, imported, scenario_sha, *, steps, ticks, assertions, output_names, clock_names=None):
    require(result.get("status") == "passed" and result["imported_stream_sha256"] == imported["stream_sha256"]
            and result["scenario_sha256"] == scenario_sha and result["source"] == imported["source"],
            "native result does not bind its actual world and complete scenario")
    require((result["loaded_chunks"], result["decoded_nonair_blocks"], result["initialized_nonair_blocks"],
             result["comparator_entities"]) == (imported["chunks"], imported["nonair"], imported["nonair"], imported["comparators"]),
            "native result omitted chunks, initialized blocks or comparators")
    require((result["steps"], result["ticks"], result["assertions"]) == (steps, ticks, assertions),
            "native result omitted steps, ticks or assertions")
    require(result["engine_revision"] == ENGINE_REVISION and result["engine_registry_patch_sha256"] == TERRAIN_PATCH
            and result["complete_machine_runtime_verified"] is False, "native engine/scope provenance differs")
    capture = result["trace_capture"]
    require(capture["declared_outputs"] == len(output_names), "trace report silently narrowed declared observations")
    if clock_names is None:
        require(capture["mode"] == "disabled" and capture["traced_outputs"] == capture["samples_per_output"] == 0
                and set(result["traces"]) == set(output_names) and all(value == [] for value in result["traces"].values()),
                "full transaction assertions changed disabled trace scope")
    else:
        require(capture["mode"] == "selected" and capture["traced_outputs"] == len(clock_names)
                and set(result["traces"]) == set(clock_names), "clock trace selection lost original registers")


def bound_physical_inputs(build):
    """Independently close the stopped-writer's original preparation bindings."""
    from experiments.memory_controller_runtime import current_fixture, interconnect
    from experiments.partition_placement.port_egress_manifest import read_egress_manifest
    from shamaos.hardware.logic import template_for
    from shamaos.hardware.memory_backbone import fabric_tile_ports

    path = build / "connected-build-report.json"
    report = read_json(path)
    require(report.get("status") == "complete-connected-native-cache-generated-readback-verified"
            and report.get("independent_union_agreement") is True, "complete saved cache readback is not accepted")
    prepared_path = check_file(report["current_preparation"])
    directory = prepared_path.parent
    require(sha(directory / "plan.json") == report["plan_sha256"], "accepted preparation plan changed")
    prepared, graph, _, fabric, origin = current_fixture.load_prepared(directory)
    require((prepared["capacity_bytes"], fabric.total_bytes, fabric.bank_count) == (4096, 4096, 1),
            "native audit requires the exact complete one-bank capacity")
    require(sha(directory / "initial.bin") == report["physical_memory"]["initial_sha256"], "original physical initial bytes changed")
    identity = lambda value: {key: item for key, item in value.items() if key != "path"}
    require(identity(graph.source) == identity(report["mapped_source"]), "original mapped graph changed")
    bundle = check_file(report["source_input_bundle"])
    with zipfile.ZipFile(bundle) as archive:
        for name in ("prepared.json", "plan.json", "mapped.json", "mapped-raw.json", "initial.bin"):
            require(archive.read("inputs/current/" + name) == (directory / name).read_bytes(), f"original bound {name} changed")
        for name in current_fixture.INPUTS:
            require(archive.read("inputs/current/source/" + name) == (directory / "source" / name).read_bytes(),
                    f"original elaboration input changed: {name}")
    physical_root = directory.parent.parent
    manifest = Path(report["facade_manifest"]["path"])
    if not manifest.is_absolute():
        manifest = physical_root / manifest
    plan = read_json(directory / "plan.json")
    placed = read_egress_manifest(manifest, report["facade_manifest"]["sha256"], graph, plan, source_root=physical_root)
    connected = interconnect.connect_cache(graph, placed, fabric, origin, capacity_bytes=4096)
    memory = report["physical_memory"]
    require(asdict(connected.bank_origin) == memory["bank_origin"]
            and asdict(fabric.bounds(connected.bank_origin)) == memory["bounds"]
            and (memory["capacity_bytes"], memory["banks"], memory["rows_per_bank"], memory["word_bits"], memory["stored_bits"])
            == (4096, 1, 1024, 32, 32768), "accepted physical bank capacity or bounds changed")
    nets = (json.dumps([asdict(net) for net in connected.nets], indent=2, sort_keys=True) + "\n").encode()
    require(hashlib.sha256(nets).hexdigest() == report["external_nets_sha256"] and len(connected.nets) == 77
            and sum(net.wired_or for net in connected.nets) == 32, "complete physical data/return nets changed")
    inputs_path, outputs_path = build / "native-inputs.json", build / "native-outputs.json"
    require(sha(inputs_path) == report["native_input_map_sha256"] and sha(outputs_path) == report["native_output_map_sha256"],
            "native saved-world interface maps changed")
    inputs, outputs = read_json(inputs_path), read_json(outputs_path)
    encode = lambda point: [point.x, point.y, point.z]
    expected_inputs = {f"host:{name}:{bit}": encode(connected.controller_ports[name, bit])
                       for name, width in scenarios.HOST_WIDTHS.items() for bit in range(width)}
    expected_outputs = {}
    for entity in graph.entities:
        if entity["kind"] == "port":
            name = f"port:{entity['port_name']}:{entity['port_index']}"
            expected_outputs[name] = encode(placed.exposed_ports[entity["id"]])
            expected_outputs["original-" + name] = encode(placed.ports[entity["id"]])
    for tile in placed.all_modules().values():
        for name, cell_origin in tile.physical.cell_origins.items():
            template = template_for(tile.physical.cell_types[name])
            if template.cell_type == "DFF":
                for pin, offset in template.pins.items():
                    expected_outputs[f"register:{name}:{pin}"] = encode(cell_origin.offset(offset.x, offset.y, offset.z))
    expected_outputs.update({f"contact:{name}": encode(value["position"]) for name, value in placed.shared_contacts.items()})
    base, bank = fabric.bank_origin(connected.bank_origin, 0), fabric.bank_spec(0)
    for row in range(1024):
        for bit in range(32):
            expected_outputs[f"storage:0:{row}:{bit}"] = encode(base.offset(bit * bank.bit_pitch + 2, bank.cell_y, 3 + row * bank.word_pitch))
    ports = fabric_tile_ports(connected.bank_origin, fabric)[0]
    for name in ("bank_select", "row_address", "write_data", "selected_word"):
        expected_outputs.update({f"fabric:{name}:{bit}": encode(point) for bit, point in enumerate(getattr(ports, name))})
    expected_outputs.update({"fabric:read_enable:0": encode(ports.read_enable), "fabric:write_commit:0": encode(ports.write_enable)})
    require(inputs == expected_inputs and outputs == expected_outputs, "saved maps do not match all physical graph/bank terminals")
    require(len({tuple(outputs[name]) for name in STORED_NAMES}) == 32768, "stored-bit observations alias")
    clocks = {name for name in outputs if name.startswith("register:") and name.endswith(":C")}
    require(len(clocks) == 115 and len({tuple(outputs[name]) for name in clocks}) == 115, "original register clock inventory changed")
    reader = NativeWorldReader(build / "world", max_cached_chunks=4)
    offsets = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))
    for point in inputs.values():
        terminal = Vec3(*point)
        pad = terminal.offset(dz=1)
        require(reader.get_block(terminal).name == "minecraft:redstone_wire" and reader.get_block(pad).name == "minecraft:air",
                "saved external host terminal/pad is incorrect")
        require(all(reader.get_block(pad.offset(*offset)).name == "minecraft:air"
                    for offset in offsets if pad.offset(*offset) != terminal), "external host pad touches another saved block")
    if "recovery" in report:
        require(sha(build / "recovery-origin-report.json") == report["recovery"]["original_report_sha256"], "recovery origin report changed")
    return report, inputs, outputs, clocks, {"original_preparation_inputs_and_plan_bound": True,
        "physical_map_coordinates_recomputed": True, "host_drive_pads": len(inputs), "original_ports": len(placed.ports),
        "register_clocks": len(clocks), "stored_bits": 32768, "all_external_nets": len(connected.nets), "physical_return_nets": 32}


def verify_clocks(clock_path, prepared, imported, inputs, outputs, clocks):
    report = read_json(clock_path)
    require(report.get("status") == "complete-connected-cache-exterior-clocks-passed" and set(report["cases"]) == {"safe", "too-fast"},
            "both completed native clock cases are required")
    check_file(report["runner"])
    require(report["world_stream"] == prepared["stream"], "clock evidence uses another world stream")
    result = {}
    for name, case in report["cases"].items():
        check_process(case)
        document = read_json(check_file(case["scenario"]))
        runtime = read_json(check_file(case["stdout"]))
        expected_outputs = {key: outputs[key] for key in clocks} | {"req_ready": outputs["port:req_ready:0"]}
        period, settle = case["half_period_ticks"], case["settle_ticks"]
        require(type(period) is int and period >= 1 and type(settle) is int and settle >= 1
                and (period == 1 if name == "too-fast" else period > 1), "clock probe changed safe/negative periods")
        steps = [{"drive": {"clk": False, "rst": True}, "ticks": settle}]
        for _ in range(3):
            steps.extend(({"drive": {"clk": True}, "ticks": period}, {"drive": {"clk": False}, "ticks": period}))
        steps.append({"ticks": settle, "expect": dict.fromkeys(clocks, False) | {"req_ready": False}})
        expected_document = {"inputs": {key: inputs[f"host:{key}:0"] for key in ("clk", "rst")},
            "outputs": expected_outputs, "initial_ticks": settle, "steps": steps,
            "capture_trace": True, "trace_outputs": sorted(clocks)}
        require(document == expected_document, "native clock scenario changes requested pulses, exact reset or original locations")
        captured = sum(step["ticks"] for step in document["steps"])
        check_runtime_result(runtime, imported, case["scenario"]["sha256"], steps=len(document["steps"]),
            ticks=document["initial_ticks"] + captured, assertions=116, output_names=expected_outputs, clock_names=clocks)
        require(runtime["trace_capture"]["samples_per_output"] == captured, "native clock trace count differs")
        checked = check_clock_traces(runtime, clocks, document)
        require(checked["passed"] is (name == "safe"), "safe/too-fast native clock evidence has the wrong outcome")
        result[name] = checked
    return result


def bound_transaction_frames():
    """Retain the exact current schedule and every independently checked word."""
    cold = list(scenarios.functional_frames(capacity_bytes=4096))
    final = next(frame.stored_words for frame in reversed(cold) if frame.stored_words is not None)
    reopened = list(scenarios.functional_frames(final, resume=True, capacity_bytes=4096))
    frames = cold + reopened
    # Compare all full-state expectations with the immutable 601-frame RTL
    # oracle, independently of native scenario expansion or summary counters.
    evidence = ROOT / "experiments/memory_controller_runtime/current_four_kib/evidence"
    receipt = read_json(evidence / "receipt.json")
    bundle = ROOT / receipt["bundle"]["path"]
    require(sha(bundle) == receipt["bundle"]["sha256"], "retained RTL oracle archive changed")
    with zipfile.ZipFile(bundle) as archive:
        expected_bytes = archive.read("inputs/current/rtl-review/expected.hex")
        require(hashlib.sha256(expected_bytes).hexdigest() == receipt["inventory"]["inputs/current/rtl-review/expected.hex"],
                "retained RTL full-storage oracle changed")
        require(archive.read("experiments/memory_controller_runtime/scenarios.py") == Path(scenarios.__file__).read_bytes(),
                "native request oracle source differs from independently RTL-checked schedule")
    actual_words = [word for frame in frames if frame.stored_words is not None for word in frame.stored_words]
    require(actual_words == [int(line, 16) for line in expected_bytes.splitlines()], "native schedule changed the retained 49 complete storage states")
    require(len(frames) == 601 and len(actual_words) == 49 * 1024, "retained current oracle lost full frames or boundaries")
    return cold, reopened, final


def verify_transactions(path, imported, inputs, outputs, prepared):
    from shamaos import release_io

    report = read_json(path)
    require(report.get("smoke_only") is False and report.get("status") == "complete-connected-cache-native-transactions-and-reopen-passed",
            "full transactions and fresh-process reopen are required; a smoke run is incomplete")
    require(set(report["cases"]) == {"cold", "reopen"}, "native evidence lost cold/reopen cases")
    check_file(report["runner"])
    cold, reopened, final = bound_transaction_frames()
    reopened_import = read_json(check_file(report["reopened_import"]))
    require(check_file(report["reopened_stream"]).stat().st_size == report["reopened_stream"]["bytes"]
            and reopened_import["stream_sha256"] == report["reopened_stream"]["sha256"], "reopened stream identity differs")
    world = path.parent / "reopened-world"
    check_imported_source(reopened_import, world, release_io.file_inventory(world))
    require(reopened_import["sections"] == reopened_import["chunks"] * 24, "reopened import lost native height sections")
    cases = {}
    for name, sequence, source in (("cold", cold, imported), ("reopen", reopened, reopened_import)):
        case = report["cases"][name]
        check_process(case)
        document = read_json(check_file(case["scenario"]))
        checked = check_scenario_document(document, sequence, inputs, outputs, report["half_period_ticks"], report["initial_ticks"])
        runtime = read_json(check_file(case["stdout"]))
        check_runtime_result(runtime, source, case["scenario"]["sha256"], steps=checked["frames"], ticks=checked["ticks"],
                             assertions=checked["assertions"], output_names=outputs)
        snapshot = check_file(case["snapshot_file"])
        header, footer = snapshot_endpoints(snapshot)
        require(runtime["pending_ticks_after_scenario"] == 0 and runtime["snapshot"]["status"] == "quiescent-snapshot-written"
                and runtime["snapshot"]["sha256"] == case["snapshot_file"]["sha256"]
                and runtime["snapshot"]["bytes"] == snapshot.stat().st_size
                and header["pending_scheduled_ticks"] == 0 and header["imported_stream_sha256"] == source["stream_sha256"]
                and header["scenario_sha256"] == case["scenario"]["sha256"]
                and header["engine_revision"] == ENGINE_REVISION and header["engine_registry_patch_sha256"] == TERRAIN_PATCH
                and header["kind"] == "snapshot_header" and header["schema"] == "shama-mchprs-quiescent-snapshot-v1"
                and header["source"] == source["source"] and header["executed_redstone_ticks"] == checked["ticks"]
                and header["complete_machine_runtime_verified"] is False
                and footer == {"kind": "snapshot_complete", "pending_scheduled_ticks": 0,
                               **{key: runtime["snapshot"][key] for key in ("chunks", "sections", "nonair", "comparators")}}
                and footer["chunks"] == runtime["chunks_after_drives"] and footer["sections"] == footer["chunks"] * 24,
                "snapshot is not the actual quiescent transaction result")
        cases[name] = checked
    require(tuple(sum(case[key] for case in cases.values()) for key in ("frames", "storage_boundaries", "all_bit_assertions"))
            == (601, 49, 1605632), "complete native proof lost frames or stored-bit checkpoints")
    restored = report["native_restore"]
    require(restored["status"] == "quiescent-snapshot-native-readback-passed"
            and restored["snapshot_sha256"] == report["cases"]["cold"]["snapshot_file"]["sha256"]
            and restored["parent_imported_stream_sha256"] == prepared["stream"]["sha256"]
            and restored["parent_scenario_sha256"] == report["cases"]["cold"]["scenario"]["sha256"]
            and restored["original_chunk_inventory_preserved"] is True and restored["pending_scheduled_ticks"] == 0
            and restored["original_source_chunks"] == imported["chunks"]
            and restored["chunks"] == reopened_import["chunks"]
            and restored["sections"] == reopened_import["sections"],
            "native reopen is not bound to actual cold-run state")
    require(read_json(world / "shamaos/quiescent-snapshot.json") == restored, "actual reopened save has different restore evidence")
    reader = NativeWorldReader(world, max_cached_chunks=128)
    for row, word in enumerate(final):
        for bit in range(32):
            state = reader.get_block(Vec3(*outputs[f"storage:0:{row}:{bit}"]))
            require(state.name == "minecraft:repeater" and dict(state.properties).get("locked") == "true"
                    and dict(state.properties).get("powered") == str(bool(word & (1 << bit))).lower(),
                    f"reopened native stored state changed at row {row}, bit {bit}")
    return {"cases": cases, "frames": 601, "full_storage_checkpoints": 49,
            "all_bit_assertions": 1605632, "reopened_saved_bits_independently_checked": 32768}


def main(build, runtime, output, *, clocks=None, transactions=None):
    from shamaos import release_io

    require(not output.exists(), "independent audit output already exists")
    build_report, inputs, outputs, names, physical = bound_physical_inputs(build)
    prepared_path = runtime / "prepared.json"
    prepared = read_json(prepared_path)
    require(prepared.get("status") == "complete-connected-cache-native-stream-prepared", "native import preparation is incomplete")
    require(check_file(prepared["build_report"]) == build / "connected-build-report.json", "runtime points to another physical build")
    for name in ("inputs", "outputs", "import_report", "stream"):
        check_file(prepared[name])
    helper_path = check_file(prepared["preparation_source"])
    helper_root = helper_path.parents[2]
    require(helper_path == helper_root / "experiments/memory_controller_runtime/run_connected.py"
            and set(prepared["runtime_helper_sources"]) == RUNTIME_SOURCES
            and prepared["runtime_helper_sources"] == {name: sha(helper_root / name) for name in RUNTIME_SOURCES},
            "runtime/oracle/import/snapshot helper sources changed after preparation")
    require(read_json(prepared["inputs"]["path"]) == inputs and read_json(prepared["outputs"]["path"]) == outputs,
            "runtime copied maps differ from physically verified maps")
    imported = read_json(prepared["import_report"]["path"])
    world = build / "world"
    inventory = release_io.file_inventory(world)
    require(release_io.inventory_digest(inventory) == prepared["world_inventory_sha256"] == build_report["world_inventory_sha256"]
            and prepared["native_world"] == str(world.resolve())
            and prepared["source_input_bundle"] == build_report["source_input_bundle"], "prepared native world or source bundle changed")
    check_imported_source(imported, world, inventory)
    require(imported["stream_sha256"] == prepared["stream"]["sha256"]
            and imported["chunks"] == build_report["native_readback"]["chunks"]
            and imported["sections"] == imported["chunks"] * 24, "native stream omitted accepted chunks or height sections")
    result = {"schema": "independent-complete-cache-native-audit-v1", "status": "native-inputs-verified-runtime-pending",
        "auditor_sha256": sha(__file__), "build_report_sha256": sha(build / "connected-build-report.json"),
        "prepared_sha256": sha(prepared_path), "physical_input_review": physical,
        "build_report": file_record(build / "connected-build-report.json"), "prepared": file_record(prepared_path),
        "native_clock_verified": False, "connected_cache_runtime_verified": False, "native_persistence_verified": False,
        "complete_machine_runtime_verified": False}
    if clocks:
        clock_report = read_json(clocks)
        require(check_file(clock_report["prepared"]) == prepared_path
                and clock_report["helper_source"] == prepared["preparation_source"]
                and clock_report["runtime_helper_sources"] == prepared["runtime_helper_sources"], "clock gate uses another prepared/helper identity")
        result["clocks"] = verify_clocks(clocks, prepared, imported, inputs, outputs, names)
        result["clock_report"] = file_record(clocks)
        result.update(status="complete-cache-clocks-independently-verified-transactions-pending", native_clock_verified=True)
    if transactions:
        require(clocks is not None, "independent complete transaction proof requires native clock evidence")
        transaction_report = read_json(transactions)
        require(check_file(transaction_report["clock_gate"]) == clocks.resolve()
                and check_file(transaction_report["prepared"]) == prepared_path
                and transaction_report["runner"] == clock_report["runner"]
                and transaction_report["helper_source"] == prepared["preparation_source"]
                and transaction_report["half_period_ticks"] == clock_report["cases"]["safe"]["half_period_ticks"]
                and transaction_report["runtime_helper_sources"] == prepared["runtime_helper_sources"],
                "transactions differ from the verified native clock/preparation/runner identity")
        result["transactions"] = verify_transactions(transactions, imported, inputs, outputs, prepared)
        result["transaction_report"] = file_record(transactions)
        result.update(status="complete-cache-native-transactions-and-reopen-independently-verified",
                      connected_cache_runtime_verified=True, native_persistence_verified=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({key: value for key, value in result.items() if key not in ("clocks", "transactions")}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--clocks", type=Path)
    parser.add_argument("--transactions", type=Path)
    args = parser.parse_args()
    main(args.build.resolve(), args.runtime.resolve(), args.output, clocks=args.clocks, transactions=args.transactions)
