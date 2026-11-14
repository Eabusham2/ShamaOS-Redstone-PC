"""Compare canonical chunk digest cost with the immutable saved-world baseline."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]
from shamaos import worldio
from test_native_fingerprint_compatibility import sample_chunk

BASELINE = "184faa9d28d871b77ff4de73c022b620d57536a7"


def baseline_method():
    raw = subprocess.check_output(["git", "show", f"{BASELINE}:src/shamaos/worldio.py"], cwd=ROOT)
    tree = ast.parse(raw)
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "_Chunk")
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "fingerprint")
    namespace = vars(worldio).copy()
    exec(compile(ast.Module(body=[method], type_ignores=[]), "immutable-worldio-baseline", "exec"), namespace)
    return namespace["fingerprint"], hashlib.sha256(raw).hexdigest()


def benchmark(repetitions=40):
    baseline, source_hash = baseline_method()
    result = {"scope": "Synthetic canonical-fingerprint microbenchmark with all 24 height sections retained; the native reader omits unowned uniform-air sections, so these ratios do not establish real save/readback or whole-world speed",
              "baseline_commit": BASELINE, "baseline_source_sha256": source_hash,
              "current_source_sha256": hashlib.sha256((ROOT / "src/shamaos/worldio.py").read_bytes()).hexdigest(),
              "repetitions_per_trial": repetitions, "trials": 5, "cases": {}}
    for kind in ("air", "owned_air", "terrain", "sparse", "dense", "permuted"):
        chunk = sample_chunk(kind)
        expected = baseline(chunk)
        assert chunk.fingerprint() == expected
        samples = {"baseline": [], "current": []}
        for trial in range(5):
            order = ("baseline", "current") if trial % 2 == 0 else ("current", "baseline")
            for name in order:
                method = baseline if name == "baseline" else worldio._Chunk.fingerprint
                start = time.perf_counter()
                for _ in range(repetitions):
                    assert method(chunk) == expected
                samples[name].append(time.perf_counter() - start)
        old, new = (statistics.median(samples[name]) for name in ("baseline", "current"))
        result["cases"][kind] = {"fingerprint": expected, "samples_seconds": samples,
                                "median_before_seconds": old, "median_after_seconds": new,
                                "speedup": old / new, "all_digests_identical": True}
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=40)
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error("repetitions must be positive")
    result = benchmark(args.repetitions)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({name: round(case["speedup"], 3) for name, case in result["cases"].items()}))
