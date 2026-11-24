"""Package the complete native inputs, outputs and source with lossless deduplication."""
from argparse import ArgumentParser
import json
from pathlib import Path

if __package__:
    from . import full_width as probe
    from .archive_io import write_recording
else:
    import full_width as probe
    from archive_io import write_recording


def archive_runs(destination, phases):
    files = {}
    for phase, directory in phases.items():
        prefix = f"phase-{phase}"
        report = json.loads((directory / "report.json").read_bytes())
        if report["bus_launch_after_oscillator_rise_rt"] != phase:
            raise ValueError("Native report has the wrong bus phase")
        source = json.loads((directory / "source-sha256.json").read_bytes())
        for name in source.keys() - {"native-executable"}:
            path = probe.ROOT / name
            if source[name] != probe.digest(path):
                raise ValueError(f"Source no longer matches run at {name}")
            files["source/" + name] = path
        for path in sorted(directory.rglob("*")):
            if path.is_file() and path.suffix in (".dat", ".mca", ".json", ".jsonl", ".gz", ".log"):
                files[prefix + "/" + path.relative_to(directory).as_posix()] = path
    return write_recording(destination, files)


if __name__ == "__main__":
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("phase33", type=Path)
    parser.add_argument("phase41", type=Path)
    args = parser.parse_args()
    print("logical files and unique blobs", archive_runs(args.destination, {33: args.phase33, 41: args.phase41}))
