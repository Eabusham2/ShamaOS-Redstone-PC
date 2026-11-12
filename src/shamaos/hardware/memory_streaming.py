"""Cache-local ordering of the exact physical memory-bank placement stream.

Cell construction stays in memory.py. Its row phase is reused directly, while
the same bus generators advance together in small Z slabs. No storage cells,
initial-state bits, support blocks or repeated placements are omitted.
"""
from dataclasses import replace
from functools import lru_cache
from itertools import chain, islice
from typing import Iterator

from ..model import Placement, Vec3
from .memory import DUST, SUPPORT, MemoryBankSpec, _bus_z, bank_ports, iter_memory_bank


def _trunk_placements(spec: MemoryBankSpec) -> int:
    return 4 * spec.word_bits * (spec.words * spec.word_pitch + 7)


@lru_cache(maxsize=16)
def _one_row_placements(spec: MemoryBankSpec) -> int:
    single = replace(spec, words=1)
    count = sum(1 for _ in iter_memory_bank(Vec3(0, 64, 0), spec=single))
    return count - _trunk_placements(single) - 4


def iter_memory_bank_ordered(
    origin: Vec3,
    *,
    spec: MemoryBankSpec = MemoryBankSpec(),
    initial: bytes | None = None,
    component: str = "memory-bank",
) -> Iterator[Placement]:
    """Emit the unchanged bank by ascending word/Z slab instead of long columns.

    The reference column phase is consumed once to reach the authoritative
    row-cell generator. Its allocation cost is included in benchmarks; it
    does not add physical placements. This keeps one source of cell logic.
    """
    reference = iter_memory_bank(origin, spec=spec, initial=initial, component=component)
    first = next(reference)  # Run the authoritative geometry/image validation.
    rows = islice(chain((first,), reference), _trunk_placements(spec), None)
    row_count = _one_row_placements(spec)
    ports = bank_ports(origin, spec)
    columns = []
    for data, read in zip(ports.data_in, ports.data_out):
        for point in (data, read):
            columns.append(_bus_z(point.x, point.y, origin.z - 2, origin.z + spec.words * spec.word_pitch + 4,
                                  facing="south", component=component))
    next_z = origin.z - 2
    for word in range(spec.words):
        last_z = origin.z + word * spec.word_pitch + 5
        while next_z <= last_z:
            for column in columns:
                yield next(column)  # Support and its conductor at this Z.
                yield next(column)
            next_z += 1
        for _ in range(row_count):
            yield next(rows)
        for point in (ports.write_select[word], ports.read_select[word]):
            yield Placement(point.offset(dy=-1), SUPPORT, component)
            yield Placement(point, DUST, component)
    while next_z <= origin.z + spec.words * spec.word_pitch + 4:
        for column in columns:
            yield next(column)
            yield next(column)
        next_z += 1
    # The only reference placements left are the markers moved beside their
    # rows above. Fail if a future emitter adds an unaccounted trailing phase.
    if sum(1 for _ in rows) != 4 * spec.words:
        raise ValueError("memory emitter phases changed; ordered stream needs an equality-verified update")
