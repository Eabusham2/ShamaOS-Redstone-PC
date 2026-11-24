"""Grade the complete mapped STEP cut and the joined finite negative from actual traces."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile

ARCHIVE = Path(__file__).parent / "evidence" / "step-predecessor-negatives.tar.xz"
ARCHIVE_SHA = "8fc7fb47a2fda0576cf535de2b06b39ac22425f92dc223ecd6db24fc832aa50b"
RUNNER_SHA = "c8032089622501cb57f9edbb0ea7e3be24e3a23fd0b2b80653b0ed4f07045610"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def transitions(values):
    return [(t, values[t]) for t in range(1, len(values)) if values[t] != values[t - 1]]


def review(archive=ARCHIVE, runner=None):
    if digest(Path(archive).read_bytes()) != ARCHIVE_SHA:
        raise ValueError("STEP recording digest mismatch")
    with tarfile.open(archive, "r:xz") as tar:
        names = tar.getnames()
        assert len(names) == len(set(names)) and all(m.isfile() for m in tar)
        contents = {m.name: tar.extractfile(m).read() for m in tar}
    hashes = json.loads(contents.pop("inventory.json"))["members_sha256"]
    assert hashes == {name: digest(data) for name, data in contents.items()}
    if runner:
        runner = Path(runner).resolve()
        assert digest(runner.read_bytes()) == RUNNER_SHA
    shell = json.loads(contents["original/shell.json"])["modules"]["shama_soc"]
    cut = json.loads(contents["original/original.json"])["modules"]["shama_step_control"]
    assert len(cut["cells"]) == 43 and all(shell["cells"][name] == cell for name, cell in cut["cells"].items())
    assert sum(len(p["bits"]) for p in cut["ports"].values() if p["direction"] == "input") == 23

    def traces(group, profile):
        scenario_bytes = contents[f"{group}/{profile}.json"]
        world_bytes = contents[f"{group}/world.jsonl"]
        case = json.loads(scenario_bytes)
        name = f"{group}/{profile}-result.json"
        result = json.loads(contents[name] if name in contents else gzip.decompress(contents[name + ".gz"]))
        ticks = case["initial_ticks"] + sum(s["ticks"] for s in case["steps"])
        assert result["ticks"] == ticks and result["pending_ticks_after_scenario"] == 0
        assert result["status"] == "executed-trace-only" and result["complete_machine_runtime_verified"] is False
        assert result["scenario_sha256"] == digest(scenario_bytes) and result["imported_stream_sha256"] == digest(world_bytes)
        assert set(result["traces"]) == set(case["outputs"]) and all(len(v) == ticks for v in result["traces"].values())
        if runner:
            with tempfile.TemporaryDirectory() as temp:
                dest = Path(temp)
                (dest/"world.jsonl").write_bytes(world_bytes)
                (dest/"case.json").write_bytes(scenario_bytes)
                fresh = json.loads(subprocess.check_output([str(runner), str(dest/"world.jsonl"), str(dest/"case.json"), "2500"]))
                for key in ("scenario_sha256", "imported_stream_sha256", "engine_revision", "engine_registry_patch_sha256",
                            "status", "ticks", "pending_ticks_after_scenario", "traces"):
                    assert fresh[key] == result[key], (profile, key)
        return case, result["traces"]

    original_case, w = traces("original", "set-retain")
    retained = transitions(w["step_pending_Q"])
    assert transitions(w["step_button"]) == [(7747, True), (7866, False)]
    false_periods = [(a, b) for (a, high), (b, low) in zip(retained, retained[1:])
                     if high and not low and 7747 < a < b < 15200]
    assert len(false_periods) == 23 and all(b - a == 70 for a, b in false_periods)
    for name in ("one-press", "stop-before-predecessor"):
        case, w = traces("joined", name)
        assert transitions(w["step_pending_Q"]) == [(14439, True), (22935, False)]
        after_reset = [(t, state) for t, state in transitions(w["startup_core_clk"]) if t > 4000]
        word = time = 0
        words = []
        for step in case["steps"]:
            for pin, value in step["drive"].items():
                if pin.startswith("word"):
                    mask = 1 << int(pin[4:])
                    word = word | mask if value else word & ~mask
            words.append((time, word))
            time += step["ticks"]
        assert all(state is False for state in w["startup_rst"][4000:])
        if name == "one-press":
            assert after_reset == [(23744, True), (25844, False)]
            assert next(t for t, value in words if value == 0x1FFFF) == 23216
        else:
            assert max(value for _, value in words) == 0x1FFF9 and words[-1][1] == 0x1FFF9
            assert not any(w["word17"]) and not after_reset
            assert not w["grant"][-1] and not w["step_pending_Q"][-1]
    return {"original_cells": len(cut["cells"]), "premature_pending_pulses": len(false_periods),
            "finite_terminal_count": "0x1fff9", "normal_core_edges": 0, "premature_clear_tick": 22935,
            "full_world_verified": False, "fresh_native_cases": 3 if runner else 0}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runner", type=Path)
    print(json.dumps(review(runner=parser.parse_args().runner), indent=2))
