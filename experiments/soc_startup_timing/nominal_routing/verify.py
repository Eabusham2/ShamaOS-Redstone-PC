"""Verify every retained native input and recalculate local setup and hold."""
import argparse
from bisect import bisect_left, bisect_right
from hashlib import sha256
import json
from pathlib import Path
from zipfile import ZipFile


def sample_margins(clock, data):
    changes = [i for i in range(1, len(data)) if data[i] != data[i - 1]]
    samples = []
    for i in range(4096, len(clock) - 2):
        if clock[i] and not clock[i - 1]:
            previous = bisect_right(changes, i)
            following = bisect_left(changes, i)
            before = changes[previous - 1] if previous else 0
            after = changes[following] if following < len(changes) else len(clock)
            samples.append((i + 1, i - before, after - i))
    return (min(s for _, s, _ in samples), min(h for _, _, h in samples),
            [[t, s, h] for t, s, h in samples if s < 8 or h < 2])


def main():
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extract", type=Path)
    args = parser.parse_args()
    receipt = json.loads((here / "receipt.json").read_bytes())
    path = here / "evidence.zip"
    assert sha256(path.read_bytes()).hexdigest() == receipt["zip_sha256"]
    if args.extract:
        args.extract.mkdir(parents=True, exist_ok=False)
    with ZipFile(path) as archive:
        assert set(archive.namelist()) == set(receipt["members_sha256"])
        for name, digest in receipt["members_sha256"].items():
            contents = archive.read(name)
            assert sha256(contents).hexdigest() == digest
            if args.extract:
                dest = args.extract / name
                assert dest.resolve().is_relative_to(args.extract.resolve())
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(contents)
        for variant in ("baseline", "mode", "power", "both"):
            result = json.loads(archive.read(f"{variant}/power-cycle-result.json"))
            source = receipt["variants"][variant]
            assert result["imported_stream_sha256"] == source["stream_sha256"]
            assert result["scenario_sha256"] == sha256(archive.read("scenarios/power-cycle.json")).hexdigest()
            actual = {}
            for prefix in ("timing_", "receiver_"):
                for signal in source["registers"][prefix]:
                    probe = prefix + signal
                    waves = result["traces"]
                    setup, hold, violations = sample_margins(waves[probe + "_C"], waves[probe + "_D"])
                    expected = source["registers"][prefix][signal]
                    assert [setup, hold, violations] == expected, (variant, signal)
                    actual[signal] = (setup, hold, len(violations))
            print(variant, json.dumps(actual))
        for case in receipt["combined_profiles"]:
            run = json.loads(archive.read(f"both/{case}-result.json"))
            assert run["imported_stream_sha256"] == receipt["variants"]["both"]["stream_sha256"]
            assert run["scenario_sha256"] == sha256(archive.read(f"scenarios/{case}.json")).hexdigest()


if __name__ == "__main__":
    main()
