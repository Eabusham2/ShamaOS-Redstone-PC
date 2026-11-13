"""Measure exact native loading of one complete production-size 4 KiB bank."""
from __future__ import annotations

import sys

if not __debug__:
    raise SystemExit("native-bank benchmark refuses Python -O: verification assertions must remain enabled")

import hashlib
import json
from pathlib import Path
import os
import subprocess
import time

from shamaos.hardware.memory import MemoryBankSpec, bank_ports
from shamaos.hardware.memory_streaming import iter_memory_bank_ordered
from shamaos.mchprs_import import export_world
from shamaos.model import Vec3
from shamaos.worldio import NativeWorldWriter


def file_sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def generator_fingerprint() -> dict[str, str]:
    """Bind a cached native bank to the exact generator/importer sources used."""
    repository = Path(__file__).resolve().parents[1]
    sources = {"tools/benchmark_mchprs_native_bank.py": file_sha256(Path(__file__))}
    for module_name in (
        "shamaos.hardware.memory", "shamaos.hardware.memory_streaming",
        "shamaos.model", "shamaos.worldio", "shamaos.mchprs_import",
    ):
        relative = "src/" + module_name.replace(".", "/") + ".py"
        actual = Path(sys.modules[module_name].__file__).resolve()
        if actual != (repository / relative).resolve():
            raise ValueError(f"benchmark source mismatch for {module_name}; run with PYTHONPATH=src:tests")
        sources[relative] = file_sha256(actual)
    return sources


def world_inventory(world: Path) -> dict[str, dict[str, str | int]]:
    """Hash every native input file; exporter independently rechecks during read."""
    level = world / "level.dat"
    regions = sorted(world.rglob("*.mca"))
    if not level.is_file() or not regions:
        raise ValueError("cached native world is missing level.dat or region files")
    return {path.relative_to(world).as_posix(): {"bytes": path.stat().st_size, "sha256": file_sha256(path)}
            for path in (level, *regions)}


def validate_prepared_cache(prepared: dict, world: Path, initial_path: Path,
                            initial_sha256: str, sources: dict[str, str]) -> None:
    """Reject stale geometry rather than reusing its previous validation label."""
    required = {"cache_version": 1, "physical_bytes": 4096, "stored_bits": 32768,
                "physical_placements": 1_991_552, "initial_image_sha256": initial_sha256,
                "generator_source_files_sha256": sources}
    for key, expected in required.items():
        if prepared.get(key) != expected:
            raise ValueError(f"stale prepared cache: {key} changed or is missing; use a fresh output directory")
    if not initial_path.is_file() or file_sha256(initial_path) != initial_sha256:
        raise ValueError("stale prepared cache: initial image changed or is missing")
    if prepared.get("native_validation", {}).get("status") != "passed":
        raise ValueError("stale prepared cache: native validation did not pass")
    if prepared.get("native_world_files") != world_inventory(world):
        raise ValueError("stale prepared cache: native world file inventory or content changed")


def validate_exported_source(prepared: dict, manifest: dict) -> None:
    """Also bind bytes actually exported after cache validation to the saved bank."""
    expected = prepared["native_world_files"]
    source = manifest["source"]
    regions = {"region/" + row["name"]: {"bytes": row["bytes"], "sha256": row["sha256"]}
               for row in source["regions"]}
    if (source["level_dat_sha256"] != expected["level.dat"]["sha256"]
            or regions != {name: info for name, info in expected.items() if name != "level.dat"}):
        raise ValueError("exported native source changed after bank validation")


def normalize_max_rss_kib(value: int, platform: str) -> int:
    """wait4/getrusage use bytes on Darwin and KiB on Linux."""
    if platform == "darwin":
        return (value + 1023) // 1024
    if platform.startswith("linux"):
        return value
    raise ValueError(f"unsupported ru_maxrss platform: {platform}")


def require_measurement_platform() -> None:
    if not (sys.platform.startswith("linux") or sys.platform == "darwin") or not hasattr(os, "wait4"):
        raise ValueError("this complete-process memory benchmark requires Linux or macOS and POSIX wait4")


def main(DIRECTORY: Path, runner: Path):
    require_measurement_platform()
    import resource

    DIRECTORY = DIRECTORY.resolve()
    runner = runner.resolve()
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    world = DIRECTORY / "world"
    origin = Vec3(256, -56, 128)
    bank = MemoryBankSpec(words=1024, word_bits=32)
    pins = bank_ports(origin, bank)
    values = [((word + 1) * 0x9E3779B9 ^ 0xA53C96F0) & 0xFFFFFFFF for word in range(bank.words)]
    initial = b"".join(value.to_bytes(4, "little") for value in values)
    initial_path = DIRECTORY / "initial-4k.bin"
    initial_sha256 = hashlib.sha256(initial).hexdigest()
    sources = generator_fingerprint()
    prepared_path = DIRECTORY / "prepared.json"
    if prepared_path.exists():
        prepared = json.loads(prepared_path.read_text())
        validate_prepared_cache(prepared, world, initial_path, initial_sha256, sources)
    else:
        if world.exists():
            raise ValueError("output contains an unverified native world; use a fresh output directory")
        prepared = None
        initial_path.write_bytes(initial)
    coord = lambda p: [p.x, p.y, p.z]
    driver = lambda p: coord(p.offset(-1, 0, -1))
    addresses = (0, 512, 1023)
    inputs = {f"D{bit}": driver(point) for bit, point in enumerate(pins.data_in)}
    inputs |= {f"W{row}": driver(pins.write_select[row]) for row in addresses}
    inputs |= {f"R{row}": driver(pins.read_select[row]) for row in addresses}
    outputs = {f"Q{bit}": coord(point) for bit, point in enumerate(pins.data_out)}
    outputs |= {f"S{row}:{bit}": coord(origin.offset(bit * bank.bit_pitch + 2, bank.cell_y, 3 + row * bank.word_pitch))
                for row in range(1024) for bit in range(32)}

    def stored():
        return {f"S{row}:{bit}": bool(value >> bit & 1) for row, value in enumerate(values) for bit in range(32)}

    def selected(value):
        return {f"Q{bit}": bool(value >> bit & 1) for bit in range(32)}

    phase = 4096
    steps = [{"ticks": phase, "expect": stored() | selected(0)}]
    for row, value in zip(addresses, (0, 0xFFFFFFFF, 0x6A95F00F)):
        steps += [{"drive": {f"R{row}": True}, "ticks": phase, "expect": stored() | selected(values[row])},
                  {"drive": {f"R{row}": False}, "ticks": phase, "expect": stored() | selected(0)},
                  {"drive": {f"D{bit}": bool(value >> bit & 1) for bit in range(32)}, "ticks": phase, "expect": stored()}]
        values[row] = value
        steps += [{"drive": {f"W{row}": True}, "ticks": phase, "expect": stored()},
                  {"drive": {f"W{row}": False, f"R{row}": True}, "ticks": phase, "expect": stored() | selected(value)},
                  {"drive": {f"R{row}": False}, "ticks": phase, "expect": stored() | selected(0)}]
    scenario = dict(inputs=inputs, outputs=outputs, steps=steps, initial_ticks=phase, capture_trace=False)
    scenario_path = DIRECTORY / "scenario.json"
    scenario_path.write_text(json.dumps(scenario, separators=(",", ":")))
    del scenario, steps, inputs, outputs
    started = time.monotonic()
    if prepared is None:
        count = 0
        with NativeWorldWriter(world, surface_y=-61, spawn=Vec3(224, -59, 96),
                               spawn_platform=False, max_cached_chunks=64) as writer:
            for placement in iter_memory_bank_ordered(origin, spec=bank, initial=initial):
                writer.set_block(placement)
                count += 1
                if count % 200_000 == 0:
                    print(json.dumps({"phase": "native-save", "placements": count,
                                      "seconds": time.monotonic() - started}), flush=True)
            assert count == 1_991_552
            validation = writer.validate()
            assert validation["status"] == "passed"
        prepared = {"cache_version": 1, "physical_bytes": bank.bytes, "stored_bits": 32768,
                    "physical_placements": count, "initial_image_sha256": initial_sha256,
                    "generator_source_files_sha256": sources, "native_world_files": world_inventory(world),
                    "native_save_and_validate_seconds": time.monotonic() - started,
                    "native_validation": validation, "complete_machine_runtime_verified": False}
        prepared_path.write_text(json.dumps(prepared, indent=2) + "\n")
    print(json.dumps({"phase": "native-save-complete", "seconds": prepared["native_save_and_validate_seconds"]}), flush=True)
    manifests = {}
    runtimes = {}
    for name, compact in (("legacy", False), ("compact", True)):
        stream = DIRECTORY / f"world-{name}.jsonl"
        export_started = time.monotonic()
        manifest = export_world(world, stream, compact_sections=compact)
        validate_exported_source(prepared, manifest)
        manifest["export_seconds"] = time.monotonic() - export_started
        (DIRECTORY / f"{name}-import-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        print(json.dumps({"phase": f"{name}-stream-complete", "seconds": manifest["export_seconds"],
                          **{key: manifest[key] for key in ("chunks", "sections", "nonair", "comparators", "stream_bytes")}}), flush=True)
        run_started = time.monotonic()
        output = DIRECTORY / f"{name}-runtime-result.json"
        with output.open("w") as stdout, (DIRECTORY / f"{name}-runtime.stderr.txt").open("w") as stderr:
            process = subprocess.Popen([str(runner), str(stream), str(scenario_path), "10000"], stdout=stdout, stderr=stderr)
            _, status, usage = os.wait4(process.pid, 0)
            process.returncode = os.waitstatus_to_exitcode(status)
        measurement = {"method": "external parent os.wait4 kernel rusage after complete child exit",
                       "elapsed_seconds": time.monotonic() - run_started,
                       "max_rss_kib": normalize_max_rss_kib(usage.ru_maxrss, sys.platform),
                       "platform": sys.platform,
                       "ru_maxrss_native_units": "bytes" if sys.platform == "darwin" else "KiB",
                       "ru_maxrss_native_value": usage.ru_maxrss,
                       "user_seconds": usage.ru_utime, "system_seconds": usage.ru_stime,
                       "exit_status": process.returncode}
        if process.returncode:
            raise RuntimeError(f"{name} runtime failed: inspect {DIRECTORY / f'{name}-runtime.stderr.txt'}")
        actual = json.loads(output.read_text())
        assert actual["status"] == "passed"
        assert actual["decoded_nonair_blocks"] == manifest["nonair"]
        assert actual["loaded_chunks"] == manifest["chunks"]
        assert actual["imported_stream_sha256"] == manifest["stream_sha256"]
        actual.pop("traces", None)
        manifests[name] = manifest
        runtimes[name] = {"runtime": actual, "external_process": measurement}
        print(json.dumps({"phase": f"{name}-runtime-passed", **measurement}), flush=True)
    # Independent comparison of every section's actual states. The overwhelmingly
    # common single-entry legacy palette has zero indices, so check those exact
    # packed words directly; mixed palettes are decoded at every cell.
    sections = uniform_sections = comparator_records = 0
    with (DIRECTORY / "world-legacy.jsonl").open() as legacy, (DIRECTORY / "world-compact.jsonl").open() as compact:
        for old_line, new_line in zip(legacy, compact, strict=True):
            old, new = json.loads(old_line), json.loads(new_line)
            if old["kind"] == "header":
                assert old["source"] == new["source"] and old["surface_y"] == new["surface_y"]
            elif new["kind"] == "uniform_section":
                assert old["kind"] == "section"
                assert (old["chunk"], old["y"], old["nonair"]) == (new["chunk"], new["y"], new["nonair"])
                if len(old["palette"]) == 1:
                    assert old["palette"][0] == new["state"] and all(value == 0 for value in old["data"])
                else:
                    per_long = 64 // old["bits"]
                    for index in range(4096):
                        palette_id = (old["data"][index // per_long] >> ((index % per_long) * old["bits"])) & ((1 << old["bits"]) - 1)
                        assert old["palette"][palette_id] == new["state"]
                sections += 1
                uniform_sections += 1
            else:
                assert old == new
                sections += new["kind"] == "section"
                comparator_records += new["kind"] == "comparator"
    comparable = ("assertions", "loaded_chunks", "chunks_after_drives", "comparator_entities", "decoded_nonair_blocks",
                  "initialized_nonair_blocks", "steps", "ticks", "scheduled_ticks_total", "peak_pending_ticks")
    for field in comparable:
        assert runtimes["legacy"]["runtime"][field] == runtimes["compact"]["runtime"][field], field
    report = {"fixture": "complete-native-4k-bank", "capacity_bytes": bank.bytes,
              "stored_bits_checked_after_each_step": 32768, "addresses_read_written": list(addresses),
              "settling_redstone_ticks_per_phase": phase, "runner_sha256": file_sha256(runner),
              "generator_source_files_sha256": sources,
              "native_save_and_validate_seconds": prepared["native_save_and_validate_seconds"],
              "initial_image_sha256": prepared["initial_image_sha256"],
              "native_validation": prepared["native_validation"],
              "streams": manifests, "runs": runtimes,
              "exact_state_comparison": {"status": "passed", "sections": sections,
                                         "uniform_sections": uniform_sections, "comparators": comparator_records},
              "python_peak_rss_kib": normalize_max_rss_kib(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss, sys.platform),
              "measurement_platform": sys.platform,
              "complete_machine_runtime_verified": False}
    (DIRECTORY / "benchmark-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"status": "passed", "report": str(DIRECTORY / "benchmark-report.json"),
                      "complete_machine_runtime_verified": False}), flush=True)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="untracked working directory for the native world, streams and reports")
    parser.add_argument("--runner", type=Path, required=True, help="terrain-enabled sparse MCHPRS executable")
    args = parser.parse_args()
    try:
        require_measurement_platform()
    except ValueError as error:
        parser.error(str(error))
    main(args.output, args.runner)
