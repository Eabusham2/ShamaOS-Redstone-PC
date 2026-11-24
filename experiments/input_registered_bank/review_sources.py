"""Review the recording with its exact frozen producer and compare an active layout."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import nbtlib
import numpy

from experiments.input_registered_bank import prove
from experiments.input_registered_bank.recording import check_saved_world
from experiments.input_storage_bus import full_width as cell
from experiments.input_storage_bus.archive_io import open_recording


# Only these reviewed source manifests may supply executable producer code.
APPROVED = {
    "positive": "4fa05039bcadd15c01285190624bb62521cb4b4420759354bfc1249fd65cd10e",
    "delayed": "03bd1ffb0dbda443c6ca2ac6b540e7760e30c7214978ba71babf55e5a7ed2bef",
}


_OFFLINE_PROCESS_SETUP = """
import importlib.util, json, sys, types
root, path, *dependencies = sys.argv[1:]
sys.path[:0] = [root + '/src', root, *dependencies]

def offline_only(event, arguments):
    if event in {'subprocess.Popen', 'os.system', 'os.posix_spawn', 'os.exec', 'os.fork'}:
        raise RuntimeError('captured waveform replay cannot launch a native process')
sys.addaudithook(offline_only)

# Preserve every archived producer byte. Its old eager import is unused by
# build/trace/Anvil review. The unavailable module exposes no POSIX methods:
# any attempt to use the old memory limiter still fails, rather than running
# an unbounded engine. Actual native subprocesses are forbidden above.
if importlib.util.find_spec('resource') is None:
    sys.modules['resource'] = types.ModuleType('resource')
"""


def json_digest(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def approved_sources(archive):
    combined = {}
    for profile, expected in APPROVED.items():
        sources = json.loads(archive.read(profile + "/sources.json"))
        if json_digest(sources) != expected or sources.pop("native-engine") != cell.PINNED_RUNNER_SHA256:
            raise ValueError(f"unrecognized frozen producer inventory for {profile}")
        for name, digest in sources.items():
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts or combined.get(name, digest) != digest:
                raise ValueError("inconsistent frozen producer path or hash")
            combined[name] = digest
    return combined


def review_frozen(path):
    """Run the waveform reviewer with only approved archived producer modules."""
    with open_recording(path) as archive, tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        for name, expected in approved_sources(archive).items():
            data = archive.read("source/" + name)
            if hashlib.sha256(data).hexdigest() != expected:
                raise ValueError(f"modified frozen producer file: {name}")
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        # The reviewer and hash-addressed archive decoder run from the current
        # checkout. Its executable circuit and simulator sources are frozen.
        reviewers = ("experiments/input_registered_bank/recording.py",
                     "experiments/input_storage_bus/archive_io.py")
        for name in reviewers:
            shutil.copyfile(cell.ROOT / name, root / name)
        for name in ("experiments", "experiments/input_registered_bank",
                     "experiments/input_storage_bus", "experiments/input_receiver_timing"):
            (root / name / "__init__.py").touch(exist_ok=False)
        script = _OFFLINE_PROCESS_SETUP + (
            "from experiments.input_registered_bank.recording import review\n"
            "print(json.dumps(review(path)))\n"
        )
        dependencies = sorted({str(Path(module.__file__).resolve().parents[1])
                               for module in (nbtlib, numpy)})
        process = subprocess.run([sys.executable, "-I", "-c", script, str(root), str(path.resolve()),
                                  *dependencies], cwd=root, check=True, capture_output=True, text=True)
        return json.loads(process.stdout)


def compare_active(path):
    """Compare every new emitted position/state and pin against native Anvil."""
    with open_recording(path) as archive:
        original = approved_sources(archive)
        paths = {name:cell.ROOT/name for name in original}
        # Include newly imported production helpers even if the original source
        # did not yet contain those files.
        paths.update({p.relative_to(cell.ROOT).as_posix():p for p in (cell.ROOT/"src/shamaos").rglob("*.py")})
        paths.update({p.relative_to(cell.ROOT).as_posix():p for p in (
            Path(__file__).resolve(), Path(prove.__file__).resolve(),
            cell.ROOT/"experiments/input_registered_bank/recording.py",
            cell.ROOT/"experiments/input_storage_bus/archive_io.py")})
        fixed = cell.frozen(paths)
        profiles = {}
        for name, extra in (("positive",0),("delayed",6)):
            blocks, levers, pins = prove.build(guard_extra=extra)
            states = {p.pos:p.block for p in blocks}
            if len(states) != len(blocks) or len(states) != 75255:
                raise ValueError("active source changed the complete circuit inventory")
            def read(filename):
                return archive.read(name + "/" + filename)
            imported = json.loads(read("import.json"))
            if imported["chunks"] != 367 or imported["nonair"] != 1024*imported["chunks"] + len(states):
                raise ValueError("captured initial world differs from the full terrain and circuit count")
            check_saved_world(read, "world", imported, states)
            coords = {n:cell.coord(p) for n,p in pins.items()}
            controls = {n:cell.coord(p) for n,p in levers.items()}
            # The delayed circuit changes only strobe repeater settings. The
            # original recorder retained the full pin/actuator maps for positive.
            if (coords != json.loads(archive.read("positive/probes.json"))
                    or controls != json.loads(archive.read("positive/actuators.json"))):
                raise ValueError("active pins or saved actuator coordinates changed")
            ordered = [[p.x,p.y,p.z,state.name,state.properties]
                       for p,state in sorted(states.items(),key=lambda item:(item[0].x,item[0].y,item[0].z))]
            profiles[name] = {"identical_physical_positions_and_states":len(states),
                              "native_chunks":imported["chunks"],"logical_probe_names":len(pins),
                              "saved_actuators":len(levers),"sorted_block_states_sha256":json_digest(ordered),
                              "probe_map_sha256":json_digest(coords),"actuators_sha256":json_digest(controls)}
        if cell.frozen(paths) != fixed:
            raise ValueError("active source changed during physical comparison")
    active = {name:item[1] for name,item in sorted(fixed.items())}
    changed = {name:{"captured":old,"active":active[name]} for name,old in sorted(original.items())
               if old!=active[name]}
    revision = subprocess.check_output(["git","-C",str(cell.ROOT),"rev-parse","HEAD"],text=True).strip()
    return {"scope":"active source reconstruction compared with both captured initial Anvil worlds",
            "active_checkout_head":revision,"active_source_hashes":active,"changed_from_captured":changed,
            "physical_archive_sha256":cell.digest(path),"approved_capture_inventories":APPROVED,
            "profiles":profiles,"fresh_native_run_performed":False,"complete_machine_runtime_verified":False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recording", type=Path)
    parser.add_argument("--compare-current", type=Path, metavar="NEW_RECEIPT")
    args = parser.parse_args()
    if args.compare_current:
        receipt = compare_active(args.recording)
        with args.compare_current.open("x") as out:
            json.dump(receipt,out,indent=2,sort_keys=True)
            out.write("\n")
        print(json.dumps(receipt["profiles"],indent=2))
    else:
        print(json.dumps(review_frozen(args.recording),indent=2))
