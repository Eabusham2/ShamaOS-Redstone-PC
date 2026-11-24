"""Record bounded native bank evidence in restartable two-column stages."""
from __future__ import annotations

import argparse
import ctypes
import os
import gzip
from hashlib import sha256
import json
from pathlib import Path
import shutil

if __package__:
    from . import full_width as cell
else:
    import full_width as cell


class InputWatch:
    """Use Linux file notifications to catch even same-timestamp edit/restore during a stage."""

    def __init__(self):
        self.libc = ctypes.CDLL(None, use_errno=True)
        self.fd = self.libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
        if self.fd < 0:
            raise OSError(ctypes.get_errno(), "inotify_init1")

    def add(self, paths):
        # IN_MODIFY, IN_ATTRIB, IN_CLOSE_WRITE, IN_DELETE_SELF and IN_MOVE_SELF.
        for path in paths:
            if self.libc.inotify_add_watch(self.fd, os.fsencode(path), 0xC0E) < 0:
                raise OSError(ctypes.get_errno(), f"inotify_add_watch: {path}")

    def verify(self):
        try:
            changed = os.read(self.fd, 65536)
        except BlockingIOError:
            return
        if changed:
            raise ValueError("A frozen input changed during this native stage")

    def close(self):
        os.close(self.fd)


def retain(path, data):
    if path.exists():
        if json.loads(path.read_bytes()) != json.loads(json.dumps(data)):
            raise ValueError(f"Previously recorded inputs or measurements changed at {path}")
    else:
        temporary = path.with_suffix(".partial")
        cell.write(temporary, data)
        temporary.replace(path)


def checked_stage(directory, name, scenario, imported, runner, *, snapshot=False):
    raw = directory / f"{name}-result.json"
    packed = raw.with_suffix(".json.gz")
    case = directory / f"{name}.json"
    marker = directory / f"{name}-verified.json"
    encoded = json.dumps(scenario, separators=(",", ":")).encode()
    case_hash = sha256(encoded).hexdigest()
    trace = scenario["capture_trace"]
    if marker.exists():
        record = json.loads(marker.read_bytes())
        saved = packed if trace else raw
        if (record["runner_sha256"] != cell.PINNED_RUNNER_SHA256
                or record["stream_sha256"] != imported["stream_sha256"]
                or record["scenario_sha256"] != case_hash or case.read_bytes() != encoded
                or cell.digest(saved) != record["file_sha256"]):
            raise ValueError(f"Source, stimulus, runner or output changed for {name}")
        with (gzip.open(saved, "rb") if trace else saved.open("rb")) as src:
            blob = src.read()
        if sha256(blob).hexdigest() != record["native_result_sha256"]:
            raise ValueError(f"Native result digest differs for {name}")
        actual = json.loads(blob)
    else:
        # An interruption may leave complete-looking files before the stage was sealed.
        leftovers = [path for path in (case, raw, packed, directory / f"{name}-stderr.log",
                                       directory / f"{name}-snapshot.jsonl",
                                       directory / f"{name}-snapshot.jsonl.partial") if path.exists()]
        if leftovers:
            audit = directory / "interrupted" / f"{name}-{leftovers[0].stat().st_mtime_ns}"
            audit.mkdir(parents=True)
            for path in leftovers:
                path.rename(audit / path.name)
        actual = cell.run_native(runner, directory, name, scenario, imported, snapshot=snapshot)
        native_digest = cell.digest(raw)
        if trace:
            with raw.open("rb") as src, gzip.open(packed, "wb", compresslevel=5) as dest:
                shutil.copyfileobj(src, dest)
            raw.unlink()
        record = {"scenario_sha256": case_hash, "stream_sha256": imported["stream_sha256"],
                  "runner_sha256": cell.PINNED_RUNNER_SHA256, "native_result_sha256": native_digest,
                  "file_sha256": cell.digest(packed if trace else raw)}
        retain(marker, record)
    assertions = sum(len(step.get("expect", {})) for step in scenario["steps"])
    expected = {"kind": "streamed-native-anvil-sparse-mchprs-runtime",
                "engine_revision": cell.ENGINE_REVISION, "engine_registry_patch_sha256": cell.TERRAIN_PATCH,
                "source": imported["source"], "loaded_chunks": imported["chunks"],
                "decoded_nonair_blocks": imported["nonair"], "comparator_entities": imported["comparators"],
                "imported_stream_sha256": imported["stream_sha256"], "scenario_sha256": case_hash,
                "status": "passed" if assertions else "executed-trace-only", "assertions": assertions,
                "ticks": scenario["initial_ticks"] + sum(step["ticks"] for step in scenario["steps"]),
                "complete_machine_runtime_verified": False}
    if any(actual.get(key) != val for key, val in expected.items()):
        raise ValueError(f"Native result disagrees with submitted world or scenario at {name}")
    if trace:
        if (set(actual["traces"]) != set(scenario["outputs"])
                or any(len(v) != expected["ticks"] - scenario["initial_ticks"] for v in actual["traces"].values())):
            raise ValueError(f"Missing declared trace data at {name}")
    if snapshot and (actual["pending_ticks_after_scenario"] != 0 or
                     actual["snapshot"]["sha256"] != cell.digest(directory / f"{name}-snapshot.jsonl")):
        raise ValueError("Corrupt or nonquiescent saved state")
    return actual, record["native_result_sha256"]


def capture(directory, runner, phase, through, *, resume):
    watch = InputWatch()
    try:
        return _capture(directory, runner, phase, through, resume=resume, watch=watch)
    finally:
        watch.close()


def _capture(directory, runner, phase, through, *, resume, watch):
    if cell.digest(runner) != cell.PINNED_RUNNER_SHA256:
        raise ValueError("Native engine digest differs from the pinned terrain runner")
    if resume and not (directory / "image-state.json").is_file():
        raise ValueError("A resumed stage requires the initial frozen Anvil image")
    directory.mkdir(parents=True, exist_ok=resume)
    own = Path(__file__).resolve()
    sources = {**cell.source_paths(), own.relative_to(cell.ROOT).as_posix(): own, "native-executable": runner}
    watch.add(sources.values())
    source_state = cell.frozen(sources)
    retain(directory / "source-state.json", source_state)
    retain(directory / "source-sha256.json", {n: value[1] for n, value in source_state.items()})
    retain(directory / "run-descriptor.json", {"phase": phase, "seed": cell.SEED, "rows": 16, "columns": 32})
    blocks, inputs, pins = cell.fixture(16, 0)
    world = directory / "world"
    if not resume:
        with cell.NativeWorldWriter(world, surface_y=-61, spawn_platform=False) as writer:
            writer.set_blocks(blocks)
        del writer
        imported = cell.export_world(world, directory / "world.jsonl")
        retain(directory / "import.json", imported)
        retain(directory / "readback.json", cell.validate_native_world(world))
    else:
        imported = json.loads((directory / "import.json").read_bytes())
    del blocks
    files = [directory / "world.jsonl", world / "level.dat", *sorted((world / "region").glob("*.mca"))]
    images = {p.relative_to(directory).as_posix(): p for p in files}
    watch.add(images.values())
    image_state = cell.frozen(images)
    retain(directory / "image-state.json", image_state)
    if image_state["world.jsonl"][1] != imported["stream_sha256"]:
        raise ValueError("Decoded Anvil stream digest differs from the imported image")

    def finish_stage():
        watch.verify()
        if cell.frozen(sources) != source_state or cell.frozen(images) != image_state:
            raise ValueError("Source, executable, Anvil region or stream changed during capture")

    def execute(name, names, steps, *, trace=True, snapshot=False):
        return checked_stage(directory, name, {"inputs": inputs, "outputs": {n: pins[n] for n in names},
            "initial_ticks": 300, "capture_trace": trace, "steps": steps}, imported, runner, snapshot=snapshot)

    origin, _ = execute("origin", ["oscillator"], [{"drive": {"enable": True}, "ticks": 600}])
    first = cell.changes(origin["traces"]["oscillator"], True)[0]
    events, final = cell.vectors(16, cell.SEED)
    epochs, steps = cell.plan(first, events, inputs, 16, phase)
    core = ["oscillator", "root", "near_C", "far_C", *(f"phase{r}" for r in range(16))]
    controls = [*core, *(f"sel{r}_{p}" for r in range(16) for p in ("C", "D", "Q")),
                *(f"write{r}" for r in range(16)), *(f"guard{r}_{p}" for r in range(16) for p in ("A", "Y"))]
    receipt, _ = execute("controls", controls, steps)
    control = receipt["traces"]
    strobes = cell.audit_strobes(control, first, epochs, events, 16)
    shared = sha256(b"".join(bytes(control[n]) for n in core)).hexdigest()
    reports = []
    for b in range(0, 32, 2):
        if b > through:
            finish_stage()
            return None
        bits = [b, b + 1]
        names = core + [f"terminal{i}" for i in bits] + [
            f"{key}{row}_{i}" for row in range(16) for i in bits for key in ("bit", "lock", "data")]
        name = f"columns-{b:02d}"
        actual, native_hash = execute(name, names, steps)
        signals = actual["traces"]
        if any(signals[n] != control[n] for n in core):
            raise ValueError("Separate native runs disagree about the same clock and row phases")
        result = cell.analyze({**control, **signals}, first, epochs, events, 16, bits)
        result.update(columns=bits, scenario_sha256=actual["scenario_sha256"], result_sha256=native_hash)
        retain(directory / f"{name}-measurements.json", result)
        reports.append(result)
        del actual, signals
    directed, _ = checked_stage(directory, "directed-word", cell.directed_word_case(first, inputs, pins, 16, phase),
                                imported, runner)
    expected = {f"bit{r}_{b}": bool(word >> b & 1) for r, word in enumerate(final) for b in range(32)}
    actual, _ = execute("quiescent", expected, [*steps[:-1], steps[-1] | {"expect": expected}],
                        trace=False, snapshot=True)
    restored = directory / "restored-world"
    restore_receipt = directory / "restored-storage.json"
    if not restore_receipt.exists():
        if restored.exists():
            restored.rename(directory / f"interrupted-restore-{restored.stat().st_mtime_ns}")
        restoration = cell.restore_snapshot(directory / "quiescent-snapshot.jsonl", world, restored,
                                             expected_sha256=actual["snapshot"]["sha256"])
    else:
        restoration = json.loads(restore_receipt.read_bytes())["restore"]
    reader = cell.NativeWorldReader(restored)
    saved = []
    for r in range(16):
        word = 0
        for b in range(32):
            state = reader.get_block(cell.Vec3(*pins[f"bit{r}_{b}"]))
            if state == cell.repeater("east", locked=True, powered=True):
                word |= 1 << b
            elif state != cell.repeater("east", locked=True):
                raise ValueError(f"Saved cell {r}/{b} is not locked native storage")
        saved.append(word)
    retain(restore_receipt, {"actual_words": saved, "expected_words": final, "restore": restoration})
    if restoration["snapshot_sha256"] != actual["snapshot"]["sha256"] or saved != final:
        raise ValueError("Restored Anvil words or snapshot lineage differ")
    windows = [bounds for report in reports for bounds in report["interior_bus_phase_adjustment_window_rt"].values()]
    common = [phase + max(x[0] for x in windows), phase + min(x[1] for x in windows)]
    passed = not any(report["faults"] for report in reports) and common[0] <= phase <= common[1]
    finish_stage()
    report = {"rows": 16, "word_bits": 32, "storage_pins": 512, "epochs": len(events), "seed": cell.SEED,
              "bus_launch_after_oscillator_rise_rt": phase, "real_launch_register_columns": [0, 31],
              "runner_sha256": cell.PINNED_RUNNER_SHA256, "stream_sha256": imported["stream_sha256"],
              "shared_trace_sha256": shared, "observed_guard_and_terminal_strobes": strobes,
              "directed_column_pair_assertions": directed["assertions"], "measurements": reports,
              "interior_phase_intersection_from_measured_transitions_rt": common,
              "saved_matches_all_rows": True, "receiver_passed": passed, "classifier_fanout_verified": False,
              "cdc_verified": False, "complete_machine_runtime_verified": False}
    retain(directory / "report.json", report)
    return passed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--runner", type=Path, required=True)
    parser.add_argument("--phase", type=int, choices=(33, 41), required=True)
    parser.add_argument("--through-column", type=int, choices=range(32), default=31, metavar="0..31")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    verdict = capture(args.output.resolve(), args.runner.resolve(), args.phase, args.through_column, resume=args.resume)
    raise SystemExit(1 if verdict is False else 0)
