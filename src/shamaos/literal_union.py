"""Exact literal union with bounded memory; separate from routing isolation.

Every emitted coordinate and complete BlockState is retained in sorted records.
Identical duplicates coalesce; unequal states at the same coordinate fail. The
record key groups horizontal chunks, so counting chunks requires no large set.
This does not replace routing adjacency, rising-wire headroom, the caller's
above-grass constraint or the native writer's narrower world-border checks.
Sort buffers and open files are bounded; the exact state palette and run-path
inventory scale with the distinct state and initial run counts, respectively.
"""
from __future__ import annotations

from contextlib import ExitStack
import hashlib
import heapq
from pathlib import Path
import struct
import tempfile

from shamaos.model import Vec3

RECORD = struct.Struct(">QI")
CHUNK_BIAS = 1 << 21


def coordinate_key(point: Vec3) -> int:
    if not all(type(value) is int for value in (point.x, point.y, point.z)):
        raise ValueError("literal coordinates must be integers")
    if not -(1 << 25) <= point.x < (1 << 25) or not -(1 << 25) <= point.z < (1 << 25):
        raise ValueError("literal coordinate exceeds the exact signed26-bit X/Z envelope")
    if not -64 <= point.y <= 319:
        raise ValueError("literal coordinate leaves the declared Minecraft build height")
    return (((point.x // 16 + CHUNK_BIAS) << 39)
            | ((point.z // 16 + CHUNK_BIAS) << 17)
            | ((point.x & 15) << 13) | ((point.z & 15) << 9) | (point.y + 64))


def key_coordinate(key: int) -> Vec3:
    return Vec3(((key >> 39) - CHUNK_BIAS) * 16 + ((key >> 13) & 15),
                (key & 511) - 64,
                (((key >> 17) & ((1 << 22) - 1)) - CHUNK_BIAS) * 16 + ((key >> 9) & 15))


def _records(file):
    while True:
        record = file.read(RECORD.size)
        if not record:
            return
        if len(record) != RECORD.size:
            raise ValueError("truncated literal-union sort record")
        yield record


def _merged(paths):
    with ExitStack() as opened:
        streams = [_records(opened.enter_context(path.open("rb"))) for path in paths]
        yield from heapq.merge(*streams)


def literal_union(placements, *, run_records=65536, merge_fan_in=64, temporary_parent=None):
    if type(run_records) is not int or run_records < 1:
        raise ValueError("run_records must be a positive integer")
    if type(merge_fan_in) is not int or merge_fan_in < 2:
        raise ValueError("merge_fan_in must be an integer of at least two")
    palette, states, runs, batch = {}, [], [], []
    emitted = 0
    minimum = maximum = None
    with tempfile.TemporaryDirectory(prefix="shama-literal-union-", dir=temporary_parent) as temporary:
        directory = Path(temporary)

        def flush():
            if not batch:
                return
            batch.sort()
            path = directory / f"run-{len(runs):08d}.bin"
            with path.open("wb") as output:
                output.writelines(batch)
            runs.append(path)
            batch.clear()

        for placement in placements:
            point, state = placement.pos, placement.block
            key = coordinate_key(point)
            index = palette.get(state)
            if index is None:
                index = len(states)
                if index >= (1 << 32):
                    raise ValueError("literal-state palette exceeds the exact record representation")
                palette[state] = index
                states.append(state)
            batch.append(RECORD.pack(key, index))
            emitted += 1
            coordinates = (point.x, point.y, point.z)
            if minimum is None:
                minimum = maximum = coordinates
            else:
                minimum = tuple(min(a, b) for a, b in zip(minimum, coordinates))
                maximum = tuple(max(a, b) for a, b in zip(maximum, coordinates))
            if len(batch) >= run_records:
                flush()
        flush()
        initial_runs = len(runs)
        passes = 0
        while len(runs) > merge_fan_in:
            following = []
            for offset in range(0, len(runs), merge_fan_in):
                group = runs[offset:offset + merge_fan_in]
                path = directory / f"merge-{passes:03d}-{len(following):08d}.bin"
                with path.open("wb") as output:
                    output.writelines(_merged(group))
                for consumed in group:
                    consumed.unlink()
                following.append(path)
            runs = following
            passes += 1
        unique, chunks, merged_records = 0, 0, 0
        previous_key = previous_index = previous_chunk = None
        digest = hashlib.sha256()
        for record in _merged(runs):
            merged_records += 1
            key, index = RECORD.unpack(record)
            if key == previous_key:
                if index != previous_index:
                    raise ValueError(f"incompatible blocks at {key_coordinate(key)}: "
                                     f"{states[previous_index]} / {states[index]}")
                continue
            unique += 1
            chunk = key >> 17
            if chunk != previous_chunk:
                chunks += 1
                previous_chunk = chunk
            digest.update(record)
            previous_key, previous_index = key, index
        if merged_records != emitted:
            raise ValueError("literal-union merge did not conserve every emitted record")
    return {
        "scope": "Exact emitted-block union; routing adjacency/headroom are independent checks",
        "emitted_blocks": emitted, "merged_records": merged_records,
        "literal_unique_blocks": unique,
        "identical_duplicate_blocks": emitted - unique, "occupied_chunks": chunks,
        "bounds": None if minimum is None else {"min": dict(zip(("x", "y", "z"), minimum)),
                                                "max": dict(zip(("x", "y", "z"), maximum))},
        "palette": [{"name": state.name, "properties": state.properties} for state in states],
        "sorted_unique_record_sha256": digest.hexdigest(),
        "record_encoding": "big-endian u64 chunk-X/chunk-Z/local-X/local-Z/Y key, u32 palette index",
        "record_bytes": RECORD.size, "run_records": run_records,
        "merge_fan_in": merge_fan_in, "initial_sort_runs": initial_runs,
        "intermediate_merge_passes": passes,
        "memory_scope": "Bounded run buffers/open files; exact palette and run-path inventory retained",
    }
