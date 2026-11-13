"""Reserve real branch length before placing a complete clocked hierarchy.

The existing matcher still chooses and verifies every repeater position.  This
experiment only moves complete routing track bands north before their literal
geometry and parent packing are checked.  It neither changes a graph endpoint
nor accepts an unbalanced clock.
"""
from __future__ import annotations

from contextlib import contextmanager

from shamaos.hardware import partition_placement


def validate_padding(blocks: int) -> None:
    if type(blocks) is not int or blocks < 0 or blocks > 512 or blocks % 4:
        raise ValueError("clock track padding must be a multiple of four blocks from 0 through 512")


@contextmanager
def reserve_clock_tracks(clock_bits, blocks: int):
    """Extend clock-bearing leaves and every sibling of a clock-bearing domain.

Sibling bands move together to preserve their separation.  Native cells,
ports, escape prefixes, net identities and track assignments are untouched.
The caller must still perform complete literal/collision and clock audits.
"""
    validate_padding(blocks)
    clock_bits = frozenset(clock_bits)
    if any(type(bit) is not int for bit in clock_bits):
        raise ValueError("clock identities must be exact integer signal bits")
    if not blocks:
        yield
        return
    originals = {name: getattr(partition_placement, name)
                 for name in ("compact_tile", "plan_bounded_domains")}

    def has_clock(tile):
        return any(net.bit in clock_bits for net in tile.physical.nets)

    def extend(tile):
        if tile.overrides or tile.clock_delays or tile._balanced_positions:
            raise ValueError("clock space must be reserved before repeater matching")
        tile.track_base_z -= blocks
        return tile

    def compact(*args, **kwargs):
        tile = originals["compact_tile"](*args, **kwargs)
        return extend(tile) if has_clock(tile) else tile

    def domains(*args, **kwargs):
        tiles = originals["plan_bounded_domains"](*args, **kwargs)
        if any(has_clock(tile) for tile in tiles):
            for tile in tiles:
                extend(tile)
        return tiles

    try:
        partition_placement.compact_tile = compact
        partition_placement.plan_bounded_domains = domains
        yield
    finally:
        for name, original in originals.items():
            setattr(partition_placement, name, original)
