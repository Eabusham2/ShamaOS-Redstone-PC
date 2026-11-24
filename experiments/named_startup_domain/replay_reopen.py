"""Recheck retained cold/reopen raw samples and every native register aperture."""
from bisect import bisect_left, bisect_right
import argparse
import hashlib
import json
import subprocess
from pathlib import Path
import tarfile

from experiments.named_startup_domain import measure, review_reopen


def sha(data):
    return hashlib.sha256(data).hexdigest()


def grade_registers(wave):
    result = {}
    for role in measure.ROLES:
        clock, data, q = (wave[f"{role}_{pin}"] for pin in "CDQ")
        edges = [i for i in range(1, len(clock)) if clock[i] and not clock[i-1]]
        toggles = [i for i in range(1, len(data)) if data[i] != data[i-1]]
        margins = []
        for t in edges:
            before, after = bisect_right(toggles, t), bisect_left(toggles, t)
            setup = t - (toggles[before-1] if before else 0)
            hold = (toggles[after] if after < len(toggles) else len(data)) - t
            if t+7 >= len(q) or setup < 8 or hold < 2 or q[t+7] != data[t]:
                raise ValueError(f"native register {role} missed its bound/capture on rising edge {t}")
            margins.append((setup, hold))
        if not margins:
            raise ValueError(f"receiver {role} never clocked")
        result[role] = {"edges":len(edges),"setup_min_rt":min(s for s,_ in margins),
                        "hold_min_rt":min(h for _,h in margins),"sampled_q_delay_rt":7}
    return result


def review(path):
    with tarfile.open(path) as archive:
        def read(name):
            return archive.extractfile(name).read()
        receipt = json.loads(read("review.json"))
        sources = read("review-source-hashes.json")
        if (sha(sources) != receipt["review_source_inventory_sha256"]
                or sha(Path(review_reopen.__file__).read_bytes()) != receipt["source_sha256"]
                or json.loads(sources)["native-runner"] != review_reopen.PINNED
                or sha(review_reopen.ARCHIVE.read_bytes()) != receipt["original_evidence_archive_sha256"]):
            raise ValueError("reopen recording uses different sources or native engine")
        results = {}
        for name in ("cold","reopened"):
            case_data, raw = read(name+".json"), read(name+"-result.json")
            scenario, result = json.loads(case_data), json.loads(raw)
            if (sha(case_data) != receipt["recordings"][name+".json"]
                    or sha(raw) != receipt["recordings"][name+"-result.json"]
                    or result["scenario_sha256"] != sha(case_data)
                    or result["imported_stream_sha256"] != receipt["original_stream_sha256" if name=="cold" else "reopened_stream_sha256"]
                    or result["assertions"] != sum(len(step.get("expect",{})) for step in scenario["steps"])
                    or result["ticks"] != sum(step["ticks"] for step in scenario["steps"])
                    or result["pending_ticks_after_scenario"] or result["status"] != "passed"):
                raise ValueError(f"changed native {name} scenario, stream, timing or assertions")
            measured = review_reopen.verify_waves(result,restart=(name=="reopened"))
            if measured != receipt[name]:
                raise ValueError(f"native {name} receipt differs from the actual transitions")
            results[name] = {"facade": measured, "registers":grade_registers(result["traces"])}
        restore = json.loads(read("restored/shamaos/quiescent-snapshot.json"))
        reimport = json.loads(read("restored-import.json"))
        if (receipt["snapshot_sha256"] != sha(read("off-snapshot.jsonl"))
                or restore["snapshot_sha256"] != receipt["snapshot_sha256"]
                or restore["parent_imported_stream_sha256"] != receipt["original_stream_sha256"]
                or restore["parent_scenario_sha256"] != receipt["recordings"]["cold.json"]
                or restore["pending_scheduled_ticks"] or reimport["source"]["saved_native_pending_ticks"]
                or reimport["stream_sha256"] != sha(read("restored.jsonl"))
                or receipt["reopened_stream_sha256"] != reimport["stream_sha256"]
                or reimport["source"]["level_dat_sha256"] != sha(read("restored/level.dat"))):
            raise ValueError("quiescent saved-world parent, restored metadata or complete reimport changed")
        for region in reimport["source"]["regions"]:
            data = read("restored/region/" + region["name"])
            if len(data) != region["bytes"] or sha(data) != region["sha256"]:
                raise ValueError("restored region differs from actual fresh-process source")
        # Same actual world, no initial 2,048-RT warmup. Only the probe
        # inventory changes from the retained 157-RT directional scenario.
        first_case = json.loads(read("load/early.json"))
        record = json.loads(read("load/early-result.json"))
        with tarfile.open(review_reopen.ARCHIVE) as original:
            expected = json.load(original.extractfile("positive/lead-157.json"))
        expected["steps"] = expected["steps"][1:]
        expected["outputs"] = json.loads(read("cold.json"))["outputs"]
        if (first_case != expected or record["scenario_sha256"] != sha(read("load/early.json"))
                or record["imported_stream_sha256"] != receipt["original_stream_sha256"]
                or record["engine_revision"] != review_reopen.ENGINE_REVISION
                or record["engine_registry_patch_sha256"] != review_reopen.TERRAIN_PATCH
                or record["status"] != "passed" or record["pending_ticks_after_scenario"] != 0
                or record["ticks"] != sum(s["ticks"] for s in expected["steps"])):
            raise ValueError("immediate-load case differs from the same saved 157-RT source trajectory")
        results["immediate_load"] = {"facade":review_reopen.verify_waves(record,restart=True),
                                     "registers":grade_registers(record["traces"])}
        return results


def capture_immediate(directory, runner):
    """Repeat the immediate-power control after producing the two-process proof."""
    if review_reopen.digest(runner) != review_reopen.PINNED:
        raise ValueError("wrong terrain engine")
    out = directory / "load"
    out.mkdir()
    with tarfile.open(review_reopen.ARCHIVE) as saved:
        scenario = json.load(saved.extractfile("positive/lead-157.json"))
    scenario["steps"] = scenario["steps"][1:]
    scenario["outputs"] = json.loads((directory/"cold.json").read_bytes())["outputs"]
    review_reopen.write(out/"early.json", scenario)
    with (out/"early-result.json").open("xb") as result:
        subprocess.run([str(runner),str(directory/"original/world.jsonl"),str(out/"early.json"),"1000"],
                       stdout=result,check=True,timeout=90)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recording", type=Path)
    parser.add_argument("--capture-immediate", metavar="TERRAIN_RUNNER", type=Path)
    args = parser.parse_args()
    if args.capture_immediate:
        capture_immediate(args.recording.resolve(), args.capture_immediate.resolve())
    else:
        print(json.dumps(review(args.recording), indent=2))
