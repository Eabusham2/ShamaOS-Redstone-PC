"""Reconstruct a measured native world stream from the original archive and its literal line changes."""
import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile


def reconstruct(original, replacements, destination):
    by_line = {entry["line"]: entry for entry in replacements["changed_lines"]}
    if len(by_line) != len(replacements["changed_lines"]):
        raise ValueError("duplicate replacement line")
    original_digest = hashlib.sha256()
    new_digest = hashlib.sha256()
    seen = set()
    for number, line in enumerate(original):
        original_digest.update(line)
        if number in by_line:
            change = by_line[number]
            if hashlib.sha256(line).hexdigest() != change["old_sha256"]:
                raise ValueError(f"original line {number} differs")
            line = change["new_line"].encode()
            seen.add(number)
        new_digest.update(line)
        destination.write(line)
    if (seen != set(by_line) or original_digest.hexdigest() != replacements["original_sha256"]
            or new_digest.hexdigest() != replacements["result_sha256"]):
        raise ValueError("original or reconstructed native stream differs")


def main():
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("variant", choices=("mode", "power", "both"))
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    with ZipFile(here.parent / "evidence.zip") as original, ZipFile(here / "evidence.zip") as changed:
        spec = json.loads(changed.read(f"{args.variant}/stream-changes.json"))
        with original.open("final/35/world.jsonl") as source, args.destination.open("xb") as target:
            reconstruct(source, spec, target)


if __name__ == "__main__":
    main()
