"""Pack a pair of native runs and recompute their observations from saved raw traces."""
import argparse
import gzip
import json
import re
import tempfile
from hashlib import sha256
from pathlib import Path

from experiments.input_registered_bank import prove
from experiments.input_storage_bus import full_width as cell
from experiments.input_storage_bus.archive_io import open_recording, write_recording
from shamaos.worldio import NativeWorldReader


def pack(path, positive, delayed):
    files = {}
    for prefix, directory in (("positive", positive), ("delayed", delayed)):
        if json.loads((directory / "report.json").read_bytes())["guard_extra_rt"] != (0 if prefix == "positive" else 6):
            raise ValueError("wrong physical row-guard profile")
        sources = json.loads((directory / "sources.json").read_bytes())
        if sources.pop("native-engine") != cell.PINNED_RUNNER_SHA256:
            raise ValueError("unexpected native executable")
        for name, expected in sources.items():
            source = cell.ROOT / name
            if cell.digest(source) != expected:
                raise ValueError(f"source changed after capture: {name}")
            files["source/" + name] = source
        for recorded in directory.rglob("*"):
            if recorded.is_file():
                files[f"{prefix}/{recorded.relative_to(directory).as_posix()}"] = recorded
    return write_recording(path, files)


def check_saved_world(read, name, imported, points):
    """Reopen the archived Anvil regions and inspect the actual physical states."""
    source = imported["source"]
    with tempfile.TemporaryDirectory() as temporary:
        world = Path(temporary)
        level = read(name + "/level.dat")
        if sha256(level).hexdigest() != source["level_dat_sha256"]:
            raise ValueError("recorded native metadata changed")
        (world / "level.dat").write_bytes(level)
        (world / "region").mkdir()
        seen = set()
        for region in source["regions"]:
            filename = region["name"]
            if not re.fullmatch(r"r\.-?\d+\.-?\d+\.mca", filename) or filename in seen:
                raise ValueError("unexpected native region filename")
            seen.add(filename)
            data = read(name + "/region/" + filename)
            if len(data) != region["bytes"] or sha256(data).hexdigest() != region["sha256"]:
                raise ValueError("recorded native region differs from its engine import")
            (world / "region" / filename).write_bytes(data)
        reader = NativeWorldReader(world)
        for point, expected in sorted(points.items(), key=lambda item: (item[0].x//16, item[0].z//16)):
            actual = reader.get_block(point)
            if isinstance(expected, bool):
                properties = dict(actual.properties)
                if (actual.name != "minecraft:repeater" or properties.get("locked") != "true"
                        or properties.get("powered") != str(expected).lower()):
                    raise ValueError(f"archived native storage differs at {point}")
            elif actual != expected:
                raise ValueError(f"archived source placement differs at {point}")


def review(path):
    result = {}
    with open_recording(path) as archive:
        for prefix in ("positive", "delayed"):
            def read(name):
                return archive.read(prefix + "/" + name)

            def document(name):
                return json.loads(read(name))

            sources = document("sources.json")
            if sources.pop("native-engine") != cell.PINNED_RUNNER_SHA256:
                raise ValueError("recording specifies another engine")
            for name, expected in sources.items():
                if sha256(archive.read("source/" + name)).hexdigest() != expected or cell.digest(cell.ROOT/name) != expected:
                    raise ValueError(f"recording source mismatch at {name}")
            profile = 0 if prefix == "positive" else 6
            blocks, switches, original_pins = prove.build(guard_extra=profile)
            switches = {k: cell.coord(p) for k, p in switches.items()}
            pins = {k: cell.coord(p) for k, p in original_pins.items()}
            if prefix == "positive" and (document("probes.json") != pins or document("actuators.json") != switches):
                raise ValueError("recorded physical interface differs from the rebuilt bank")
            imported = document("import.json")
            if sha256(read("world.jsonl")).hexdigest() != imported["stream_sha256"]:
                raise ValueError("original exported world changed")

            check_saved_world(read, "world", imported, {block.pos: block.block for block in blocks})
            if imported["nonair"] != 1024*imported["chunks"] + len(blocks):
                raise ValueError("saved initial terrain and component blocks exceed their inventories")

            def case(name, steps, *, trace=True, imported_world=imported):
                marker = document(name + "-verified.json")
                scenario = document(name + ".json")
                raw = read(name + "-result.json.gz" if trace else name + "-result.json")
                if sha256(raw).hexdigest() != marker["file_sha256"]:
                    raise ValueError(f"recorded output changed at {name}")
                raw = gzip.decompress(raw) if trace else raw
                runtime = json.loads(raw)
                scenario_hash = sha256(read(name+".json")).hexdigest()
                count = sum(len(step.get("expect",{})) for step in steps)
                outputs = scenario["outputs"]
                if (scenario["inputs"] != {} or scenario["steps"] != steps or scenario["initial_ticks"] != 300
                        or outputs != {signal:pins[signal] for signal in outputs}
                        or marker["scenario_sha256"] != scenario_hash or runtime["scenario_sha256"] != scenario_hash
                        or runtime["imported_stream_sha256"] != marker["stream_sha256"]
                        or marker["stream_sha256"] != imported_world["stream_sha256"]
                        or marker["runner_sha256"] != cell.PINNED_RUNNER_SHA256
                        or sha256(raw).hexdigest() != marker["native_result_sha256"]
                        or runtime["source"] != imported_world["source"] or runtime["engine_revision"] != cell.ENGINE_REVISION
                        or runtime["engine_registry_patch_sha256"] != cell.TERRAIN_PATCH
                        or runtime["assertions"] != count or runtime["status"] != ("passed" if count else "executed-trace-only")
                        or runtime["ticks"] != 300 + sum(step["ticks"] for step in steps)
                        or runtime["complete_machine_runtime_verified"] is not False or scenario["capture_trace"] is not trace
                        or (trace and set(runtime["traces"]) != set(outputs))):
                    raise ValueError(f"recorded case differs from the actuator schedule at {name}")
                return runtime

            origin = case("origin", [{"use_blocks":[switches["enable"]],"ticks":650}])
            first = cell.changes(origin["traces"]["oscillator"], True)[0]
            events, final = prove.events_and_words()
            epochs, steps = prove.scenario_steps(first, switches, events)
            report = document("report.json")
            if prefix == "delayed":
                wave = case("delayed-strobes", steps)["traces"]
                for bit in (0,31):
                    prove.check_launch(wave, first, epochs, events, bit)
                negative = cell.analyze(wave, first, epochs, events, 16, (0,31))
                if (not negative["faults"].get("unstable/incorrect data during aperture")
                        or negative != report["measurement"] or report["receiver_passed"] is not False):
                    raise ValueError("missing measured delayed-guard failure")
                result[prefix] = {k:v["count"] for k,v in negative["faults"].items()}
                continue

            core = case("controls", steps)["traces"]
            strobes = cell.audit_strobes(core, first, epochs, events, 16)
            launches = {}
            analyzed = []
            for b in range(0,32,2):
                wave = case(f"lanes-{b:02}", steps)["traces"]
                if any(core[n]!=wave[n] for n in wave.keys() & core.keys()):
                    raise ValueError("clock/control wave differs across native reimports")
                for bit in (b,b+1):
                    launches[str(bit)] = prove.check_launch(wave, first, epochs, events, bit)
                measurement = cell.analyze(core | wave, first, epochs, events, 16, (b,b+1))
                if measurement["faults"] or json.loads(json.dumps(measurement)) != document(f"capture-{b:02}.json"):
                    raise ValueError(f"incorrect or altered storage timing at lanes {b} and {b+1}")
                analyzed.append(measurement)
            minima = {k:min(m["minimum_rt"][k] for m in analyzed) for k in ("setup","hold","aperture")}
            counts = {k:sum(m["measured"][k] for m in analyzed) for k in analyzed[0]["measured"]}
            expected = {f"bit{r}_{b}":bool(w>>b&1) for r,w in enumerate(final) for b in range(32)}
            cold = case("cold", steps+[{"ticks":1,"expect":expected}], trace=False)
            snapshot_sha = sha256(read("cold-snapshot.jsonl")).hexdigest()
            fresh = document("fresh/import.json")
            restored = document("restored/shamaos/quiescent-snapshot.json")
            check_saved_world(read, "restored", fresh, {original_pins[n]: value for n, value in expected.items()})
            case("fresh/readback", [{"ticks":700,"expect":expected}], trace=False, imported_world=fresh)
            if (cold["pending_ticks_after_scenario"] or cold["snapshot"]["sha256"] != snapshot_sha
                    or restored["snapshot_sha256"] != snapshot_sha or restored["pending_scheduled_ticks"] != 0
                    or restored["parent_imported_stream_sha256"] != imported["stream_sha256"]
                    or restored["parent_scenario_sha256"] != cold["scenario_sha256"]
                    or sha256(read("fresh/world.jsonl")).hexdigest() != fresh["stream_sha256"]
                    or (minima, counts, launches, strobes) != (report["minimum_rt"],report["measured"],
                                                               report["launches"], report["row_control_strobe_edges_checked"])
                    or report["receiver_passed"] is not True or report["complete_machine_runtime_verified"] is not False
                    or report["restored_locked_bits"] != 512 or report["fresh_process_assertions"] != 512):
                raise ValueError("recorded snapshot or complete bank summary changed")
            result[prefix] = {"launches": launches, "minimum_rt": minima, "measured": counts, "saved_bits":len(expected)}
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--pack", nargs=2, metavar=("POSITIVE", "DELAYED"), type=Path)
    args = parser.parse_args()
    if args.pack:
        print("files and unique content hashes", pack(args.archive, *args.pack))
    print(json.dumps(review(args.archive), indent=2))
