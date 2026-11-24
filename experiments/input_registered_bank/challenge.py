"""Check whether adding six ticks to physical row strobes violates storage timing."""
import argparse
from pathlib import Path

from experiments.input_registered_bank import prove
from experiments.input_storage_bus import full_width as cell
from experiments.input_storage_bus.capture import checked_stage, retain
from shamaos.mchprs_import import digest, export_world
from shamaos.worldio import NativeWorldWriter


def challenge(directory, runner):
    directory.mkdir(parents=True, exist_ok=False)
    runner = runner.resolve()
    if digest(runner) != cell.PINNED_RUNNER_SHA256:
        raise ValueError("pinned terrain runner required")
    sources = cell.source_paths()
    for module in ("input_registered_bank/fixture.py", "input_registered_bank/prove.py",
                   "input_registered_bank/challenge.py", "input_storage_bus/capture.py"):
        path = cell.ROOT / "experiments" / module
        sources[path.relative_to(cell.ROOT).as_posix()] = path
    sources["native-engine"] = runner
    frozen = cell.frozen(sources)
    retain(directory / "sources.json", {name: item[1] for name, item in frozen.items()})
    blocks, controls, pins = prove.build(guard_extra=6)
    with NativeWorldWriter(directory / "world", surface_y=-61, spawn_platform=False) as writer:
        writer.set_blocks(blocks)
        if writer.validate()["status"] != "passed":
            raise ValueError("delayed-strobe world readback failed")
    imported = export_world(directory / "world", directory / "world.jsonl")
    retain(directory / "import.json", imported)
    controls = {k: cell.coord(v) for k, v in controls.items()}

    def run(name, signals, steps):
        case = {"inputs": {}, "outputs": {n: cell.coord(pins[n]) for n in signals},
                "initial_ticks": 300, "capture_trace": True, "steps": steps}
        result, _ = checked_stage(directory, name, case, imported, runner)
        return result["traces"]

    origin = run("origin", ["oscillator"], [{"use_blocks": [controls["enable"]], "ticks": 650}])
    first = cell.changes(origin["oscillator"], True)[0]
    events, _ = prove.events_and_words()
    epochs, steps = prove.scenario_steps(first, controls, events)
    core = ["oscillator", "root", "near_C", "far_C",
            *[f"phase{r}" for r in range(16)],
            *[f"sel{r}_{pin}" for r in range(16) for pin in "CDQ"]]
    bits = (0, 31)
    signals = [*core, *[f"launch{b}_{p}" for b in bits for p in "CDQ"],
               *[f"terminal{b}" for b in bits],
               *[f"{p}{r}_{b}" for r in range(16) for b in bits for p in ("bit", "data", "lock")]]
    wave = run("delayed-strobes", signals, steps)
    for bit in bits:
        prove.check_launch(wave, first, epochs, events, bit)
    reviewed = cell.analyze(wave, first, epochs, events, 16, bits)
    if cell.frozen(sources) != frozen or digest(directory / "world.jsonl") != imported["stream_sha256"]:
        raise ValueError("negative-control inputs changed")
    if not reviewed["faults"].get("unstable/incorrect data during aperture", {}).get("count"):
        raise ValueError("delayed-strobe control failed to expose negative data timing")
    report = {"guard_extra_rt": 6, "observed_columns": bits, "measurement": reviewed,
              "receiver_passed": False, "complete_machine_runtime_verified": False}
    retain(directory / "report.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--runner", type=Path, required=True)
    args = parser.parse_args()
    print(challenge(args.output.resolve(), args.runner)["measurement"]["faults"])
