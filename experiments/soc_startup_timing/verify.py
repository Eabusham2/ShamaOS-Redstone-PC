"""Verify retained MCHPRS inputs and recompute the measured DFF timing margins."""
from __future__ import annotations

import argparse
from bisect import bisect_left, bisect_right
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
from zipfile import ZipFile


REGISTERS = ("clock_hold", "power_previous", "divided_clock_active",
             "reset_pulse_request", "reset_pulse_received")
WORDS = (0xA55AC33C, 0x12345678, 0x11223344, 0xABCDEF01)
COMMIT = "370c24df4db7de1d9d34dec6272f40bb90e49a34"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def measurements(wave, first_raw, prefix):
    """Use local pin transitions, on the tick indexed from 1 in native reports."""
    values = {}
    for register in REGISTERS:
        clock = wave[prefix + register + "_C"]
        data = wave[prefix + register + "_D"]
        require(len(clock) == len(data), register + " pin length mismatch")
        transitions = [i for i in range(1, len(data)) if data[i - 1] != data[i]]
        samples = []
        for i in range(first_raw, len(clock) - 2):
            if clock[i] and not clock[i - 1]:
                left, right = bisect_right(transitions, i), bisect_left(transitions, i)
                previous = transitions[left - 1] if left else 0
                following = transitions[right] if right < len(transitions) else len(data)
                samples.append((i + 1, i - previous, following - i))
        require(bool(samples), register + " has no observable clock edges")
        values[register] = {
            "rising_edges": len(samples),
            "minimum_setup": min(setup for tick, setup, hold in samples),
            "minimum_hold": min(hold for tick, setup, hold in samples),
            "below_contract": [{"tick": tick, "setup": setup, "hold": hold}
                               for tick, setup, hold in samples if setup < 8 or hold < 2],
        }
    return values


def cold_words(wave, erased):
    for row, word in enumerate(WORDS):
        for bit in range(32):
            values = wave[f"stored_{row}_{bit}"]
            preloaded = bool(word & (1 << bit))
            require(values[0] == preloaded, "incorrect preloaded storage")
            if erased and row == 0:
                require(not values[-1], "Boolean control did not erase row zero")
            else:
                require(all(value == preloaded for value in values), "stored value changed")
    require(any(wave["row_write"]) == erased, "cold row-write did not match the storage transition")


def verify(bundle, source=None):
    def read(path):
        return json.loads(bundle.read(path))

    manifest = read("manifest.json")
    entries = bundle.namelist()
    require(len(entries) == len(set(entries)), "duplicate ZIP member")
    require(set(entries) == {"manifest.json", *manifest["files"]}, "archive inventory mismatch")
    require(manifest["source_commit"] == COMMIT, "wrong candidate revision")
    for flag in ("whole_pc_boot_validated", "whole_system_timing_validated", "nominal_timing_validated"):
        require(manifest[flag] is False, f"invalid scope flag {flag}")
    for name, spec in manifest["files"].items():
        member = PurePosixPath(name)
        require(not member.is_absolute() and ".." not in member.parts and "\\" not in name, "unsafe member")
        with bundle.open(name) as f:
            result = hashlib.file_digest(f, "sha256").hexdigest()
        require(bundle.getinfo(name).file_size == spec["bytes"] and result == spec["sha256"], name)
    def file_hash(name):
        return manifest["files"][name]["sha256"]

    if source:
        inventory = read("final/35/source-hashes.json")
        require(inventory == read("final/140/source-hashes.json"), "source inventory changed")
        for name, expected in inventory.items():
            with (source / name).open("rb") as f:
                actual = hashlib.file_digest(f, "sha256").hexdigest()
            require(actual == expected, f"source mismatch: {name}")
        print(f"Verified {len(inventory)} source files against the native run inventory.")

    for item in manifest["replays"]:
        output = read(item["result"])
        require(output["imported_stream_sha256"] == file_hash(item["stream"]), item["result"] + " stream")
        require(output["scenario_sha256"] == file_hash(item["scenario"]), item["result"] + " scenario")
        require(output["complete_machine_runtime_verified"] is False, item["result"] + " scope")

    for half, expected in ((35, (1, 1, 4)), (70, (40, 1, 1)),
                           (140, (36, 7, 0)), (280, (130, 7, 0))):
        stem = f"timing/{half}/"
        wave = read(stem + "power-result.json")["traces"]
        per_register = measurements(wave, 4096, "")
        original = read(stem + "margins.json")["DFF_margins"]
        for register, reg in per_register.items():
            recorded = original[register]
            require((reg["minimum_setup"], reg["minimum_hold"], reg["rising_edges"]) ==
                    (recorded["min_setup"], recorded["min_hold"], recorded["edges"]), stem + register)
            require([[v["tick"], v["setup"], v["hold"]] for v in reg["below_contract"]] ==
                    recorded["violations"], stem + register + " violations")
        regs = per_register.values()
        observed = (min(r["minimum_setup"] for r in regs), min(r["minimum_hold"] for r in regs),
                    sum(len(r["below_contract"]) for r in regs))
        require(observed == expected, stem + " timing classification")
        print(f"{half:3} RT half-period   minimum setup {observed[0]:3} RT   "
              f"minimum hold {observed[1]:2} RT   violations {observed[2]}")

    for half in (35, 140):
        prefix = f"final/{half}/"
        obs = read(prefix + "observations.json")
        cold_words(read(prefix + "cold-result.json")["traces"], erased=False)
        require(obs["source_inventory_sha256"] == file_hash(prefix + "source-hashes.json") and
                obs["world_stream_sha256"] == file_hash(prefix + "world.jsonl") and
                obs["runner_sha256"] == manifest["runner_sha256"], prefix + " observation provenance")
        for map_name, sha in obs["mapped_sha256"].items():
            map_path = prefix + map_name if map_name == "joined.json" else "final/35/" + map_name
            require(sha == file_hash(map_path), map_name + " graph mismatch")
        wave = read(prefix + "power-cycle-result.json")["traces"]
        require(measurements(wave, 4096, "timing_") == obs["power_control_timing"]["registers"],
                prefix + " executable timing results")
        require(obs["power_control_timing"]["met_local_setup_hold"] is (half == 140),
                prefix + " timing contract")
        require(obs["whole_pc_boot_validated"] is False, prefix + " scope")
        if half == 35:
            require(obs["whole_system_timing_validated"] is False, prefix + " scope")
            require(set(obs["receiver_profiles"]) == {"idle", "write", "out-of-range"},
                    prefix + " incomplete receiver suite")

    source = read("final/35/cold-result.json")["source"]
    require(source["level_dat_sha256"] == file_hash("final/35/world/level.dat"), "level.dat mismatch")
    for region in source["regions"]:
        require(region["sha256"] == file_hash("final/35/world/region/" + region["name"]),
                region["name"] + " saved Anvil hash")

    cold_words(read("witness/boolean/cold-result.json")["traces"], erased=True)
    old = read("witness/pre-return/lifecycle-result.json")["traces"]
    first = old["core_clk_Y"].index(True)
    require(not old["reset_Y"][first], "missing pre-return reset counterexample")
    for variant in ("positive1", "mask2"):
        pre = f"witness/{variant}/"
        require(read(pre + "source-hashes.json")["rtl/shama_soc.sv"] == file_hash(pre + "shama_soc.sv"),
                pre + " intermediate RTL does not match its original source inventory")
    for stem, expected in (("12-None", False), ("55-None", False), ("95-12", True),
                           ("157-None", True), ("157-12", True), ("157-31", True)):
        require(read(f"power-lead/{stem}-report.json")["passed"] is expected, stem + " power lead")
    print(f"Verified {len(entries)-1} payloads and {len(manifest['replays'])} native trace bindings.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=Path(__file__).with_name("evidence.zip"))
    parser.add_argument("--source", type=Path, help="check the restored candidate checkout against both source inventories")
    parser.add_argument("--extract", type=Path, help="create this new directory after verification")
    args = parser.parse_args()
    if args.extract and args.extract.exists():
        parser.error("the extraction directory must be new")
    with ZipFile(args.archive) as bundle:
        verify(bundle, args.source)
        if args.extract:
            args.extract.mkdir(parents=True, exist_ok=False)
            for name in bundle.namelist():
                target = args.extract / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(name) as source, target.open("xb") as destination:
                    shutil.copyfileobj(source, destination)


if __name__ == "__main__":
    main()
