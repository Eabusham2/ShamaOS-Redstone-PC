"""Check a retained assembler mapping's effective source without resynthesis."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact_directory", type=Path)
    parser.add_argument("--artifact-commit", default="4e933a5")
    parser.add_argument("--current-ref", default="HEAD")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    git = lambda *values: subprocess.check_output(["git", *values], cwd=root)
    report = {"artifact_commit": git("rev-parse", args.artifact_commit).decode().strip(),
              "current_commit": git("rev-parse", args.current_ref).decode().strip(), "snapshots": {}}
    outputs = []
    with tempfile.TemporaryDirectory(prefix="asm-provenance-") as temporary:
        for label, revision in (("artifact-source", args.artifact_commit), ("current-source", args.current_ref)):
            folder = Path(temporary) / label
            folder.mkdir()
            metadata = {}
            for name in ("shama_asm_accel.sv", "shama_boot_params.svh"):
                data = git("show", f"{revision}:rtl/{name}")
                (folder / name).write_bytes(data)
                metadata[name + "_sha256"] = hashlib.sha256(data).hexdigest()
            rtl = (folder / "shama_asm_accel.sv").read_text()
            header = (folder / "shama_boot_params.svh").read_text()
            macros = sorted(set(re.findall(r"`(SHAMA_\w+)", rtl)))
            metadata["referenced_boot_macros"] = macros
            metadata["effective_macros"] = {
                name: re.search(r"^`define\s+" + name + r"\s+([^\n]+)", header, re.M).group(1)
                for name in macros
            }
            output = folder / "preprocessed.sv"
            subprocess.run(["iverilog", "-E", "-g2012", "-I", str(folder), "-o", str(output),
                            str(folder / "shama_asm_accel.sv")], cwd=folder, check=True, capture_output=True)
            contents = output.read_bytes()
            outputs.append(contents)
            metadata["preprocessed_sha256"] = hashlib.sha256(contents).hexdigest()
            report["snapshots"][label] = metadata
    mapped = args.artifact_directory / "partition-u_asm-shama_asm_accel.json"
    report.update(preprocessed_byte_identical=outputs[0] == outputs[1], preprocessed_bytes=len(outputs[0]),
                  mapped_json_sha256=digest(mapped), mapped_json_bytes=mapped.stat().st_size,
                  scope="effective assembler source comparison; no other-partition or physical routing claim")
    archive = args.artifact_directory / "artifact.zip"
    if archive.exists():
        report["artifact_zip_sha256"] = digest(archive)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    if not report["preprocessed_byte_identical"]:
        raise SystemExit("assembler preprocessing differs; do not label the retained mapping current-effective-RTL")


if __name__ == "__main__":
    main()
