"""Recalculate receiver measurements from archived native traces and Anvil blocks."""
from __future__ import annotations

import argparse
from hashlib import sha256
import gzip
import json
from pathlib import Path
import tempfile

if __package__:
    from . import full_width as probe
    from .archive_io import open_recording
else:
    import full_width as probe
    from archive_io import open_recording


def receipt(archive, name):
    return json.loads(archive.read(name))


def waves(archive, prefix, name):
    case_bytes = archive.read(f"{prefix}/{name}.json")
    case = json.loads(case_bytes)
    body = gzip.decompress(archive.read(f"{prefix}/{name}-result.json.gz"))
    actual = json.loads(body)
    imported = receipt(archive, f"{prefix}/import.json")
    expected = {
        "source": imported["source"],
        "engine_revision": probe.ENGINE_REVISION,
        "engine_registry_patch_sha256": probe.TERRAIN_PATCH,
        "imported_stream_sha256": imported["stream_sha256"],
        "complete_machine_runtime_verified": False,
        "scenario_sha256": sha256(case_bytes).hexdigest(),
        "status": "executed-trace-only",
        "assertions": 0,
        "ticks": case["initial_ticks"] + sum(s["ticks"] for s in case["steps"]),
    }
    if any(actual.get(k) != v for k, v in expected.items()):
        raise ValueError(f"Native trace provenance differs for {prefix}/{name}")
    if set(case["outputs"]) != set(actual["traces"]):
        raise ValueError("Native traces omit physical probes")
    return case, actual["traces"], sha256(body).hexdigest()


def verify(archive_path, phase):
    prefix = f"phase-{phase}"
    with open_recording(archive_path) as archive:
        manifest = receipt(archive, "manifest.json")
        for name, checksum in manifest.items():
            if sha256(archive.read(name)).hexdigest() != checksum:
                raise ValueError(f"Archive member changed: {name}")
        report = receipt(archive, f"{prefix}/report.json")
        source = receipt(archive, f"{prefix}/source-sha256.json")
        if (source["native-executable"] != probe.PINNED_RUNNER_SHA256
                or source["experiments/input_storage_bus/full_width.py"] != probe.digest(Path(probe.__file__))):
            raise ValueError("Reviewed runner or measurement code differs from this recording")
        for name, checksum in source.items():
            if name != "native-executable" and sha256(archive.read(f"source/{name}")).hexdigest() != checksum:
                raise ValueError(f"Frozen source differs for {name}")
        # Reconstruct all actual source, register, qualifier, terminal, D, lock and Q positions.
        all_blocks, inputs, ports = probe.fixture(16, 0)
        del all_blocks
        events, final = probe.vectors(16, probe.SEED)
        controls_case, control, _ = waves(archive, prefix, "controls")
        first = probe.changes(control["oscillator"], True)[0]
        epochs, expected_steps = probe.plan(first, events, inputs, 16, phase)

        def verify_case(case):
            if case["steps"] != expected_steps or case["inputs"] != inputs:
                raise ValueError("Evidence drives differ from generated word and select protocol")
            if any(ports[n] != p for n, p in case["outputs"].items()):
                raise ValueError("Evidence assigns a probe to a different physical pin")

        verify_case(controls_case)
        strobes = probe.audit_strobes(control, first, epochs, events, 16)
        measurements = []
        windows = {}
        for start in range(0, 32, 2):
            name = f"columns-{start:02d}"
            case, signals, signature = waves(archive, prefix, name)
            verify_case(case)
            if any(signals[n] != control[n] for n in signals.keys() & control.keys()):
                raise ValueError("Shards disagree about shared physical clock phases")
            bits = [start, start + 1]
            measurement = probe.analyze({**control, **signals}, first, epochs, events, 16, bits)
            measurement.update(columns=bits, scenario_sha256=sha256(archive.read(f"{prefix}/{name}.json")).hexdigest(),
                               result_sha256=signature)
            if json.loads(json.dumps(measurement)) != report["measurements"][start // 2]:
                raise ValueError(f"Measurement disagrees with recorded native D/lock/Q at {name}")
            for bit, bounds in measurement["interior_bus_phase_adjustment_window_rt"].items():
                windows[bit] = [bound + phase for bound in bounds]
            measurements.append(measurement)
        if set(windows) != set(range(1, 31)):
            raise ValueError("Missing interior launch column")
        window = [max(bounds[0] for bounds in windows.values()), min(bounds[1] for bounds in windows.values())]
        if report["interior_phase_intersection_from_measured_transitions_rt"] != window:
            raise ValueError("Reported common phase window differs from the local physical transitions")
        for name, checks in (("directed-word", 6144), ("quiescent", 512)):
            result = receipt(archive, f"{prefix}/{name}-result.json")
            case = receipt(archive, f"{prefix}/{name}.json")
            if name == "directed-word" and case != probe.directed_word_case(first, inputs, ports, 16, phase):
                raise ValueError("Directed all-pairs stimulus differs")
            if (result["status"] != "passed" or result["assertions"] != checks
                    or result["scenario_sha256"] != sha256(archive.read(f"{prefix}/{name}.json")).hexdigest()
                    or result["source"] != receipt(archive, f"{prefix}/import.json")["source"]
                    or result["imported_stream_sha256"] != report["stream_sha256"]):
                raise ValueError(f"Native result or evidence differs at {name}")
        restored = receipt(archive, f"{prefix}/restored-storage.json")
        snap_hash = sha256(archive.read(f"{prefix}/quiescent-snapshot.jsonl")).hexdigest()
        if (result["snapshot"]["sha256"] != snap_hash or result["pending_ticks_after_scenario"] != 0
                or restored["restore"]["snapshot_sha256"] != snap_hash):
            raise ValueError("Snapshot lineage differs")
        imported = receipt(archive, f"{prefix}/import.json")
        if sha256(archive.read(f"{prefix}/world.jsonl")).hexdigest() != imported["stream_sha256"]:
            raise ValueError("Archived native stream differs from the stream consumed by the runner")
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            original = path / "original"
            original.mkdir()
            signatures = {"level.dat": imported["source"]["level_dat_sha256"]}
            signatures.update({"region/" + r["name"]: r["sha256"] for r in imported["source"]["regions"]})
            for name, signature in signatures.items():
                data = archive.read(f"{prefix}/world/{name}")
                if sha256(data).hexdigest() != signature:
                    raise ValueError(f"Archived source Anvil image differs at {name}")
                target = original / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            snapshot = path / "snapshot.jsonl"
            snapshot.write_bytes(archive.read(f"{prefix}/quiescent-snapshot.jsonl"))
            regenerated = path / "reopened"
            restoration = probe.restore_snapshot(snapshot, original, regenerated, expected_sha256=snap_hash)
            if restoration["parent_scenario_sha256"] != result["scenario_sha256"]:
                raise ValueError("Restored snapshot was produced by a different physical scenario")
            for region in (regenerated / "region").glob("*.mca"):
                if region.read_bytes() != archive.read(f"{prefix}/restored-world/region/{region.name}"):
                    raise ValueError("Archived reopened region differs from a fresh snapshot conversion")
            reader = probe.NativeWorldReader(regenerated)
            for row, word in enumerate(final):
                for bit in range(32):
                    expect = probe.repeater("east", locked=True, powered=bool(word >> bit & 1))
                    if reader.get_block(probe.Vec3(*ports[f"bit{row}_{bit}"])) != expect:
                        raise ValueError(f"Saved Anvil state disagrees at row {row}, column {bit}")
        faults = [m["faults"] for m in measurements if m["faults"]]
        if (report["receiver_passed"] != (not faults) or report["observed_guard_and_terminal_strobes"] != strobes
                or not report["saved_matches_all_rows"] or final != restored["actual_words"]):
            raise ValueError("Stored verdict disagrees with observed native transitions and words")
        return {"phase_rt": phase, "interior_window_rt": window, "faults": faults, "saved_bits": len(final) * 32}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    for phase in (33, 41):
        print(json.dumps(verify(args.archive, phase)))
