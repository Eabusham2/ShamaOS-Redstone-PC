"""Reserve measured extra clock space for complete sibling routing groups.

This changes real track coordinates before any parent's literal validation and
packing. It preserves all native cells, route endpoints, tracks and sibling
separation. Actual clock matching and saved-world pulse tests remain required.
"""
from __future__ import annotations

from contextlib import contextmanager

from experiments.memory_controller_runtime.clock_reserve import validate_padding
from shamaos.hardware import partition_placement


def validate_domain_padding(baseline_blocks, domain_padding):
    validate_padding(baseline_blocks)
    if not isinstance(domain_padding, dict):
        raise ValueError("targeted clock reservations must identify exact routing domains")
    for domain, blocks in domain_padding.items():
        if type(domain) is not int or domain < 0:
            raise ValueError("clock reservation domains must be nonnegative integers")
        if type(blocks) is not int or blocks < baseline_blocks or blocks > 4096 or blocks % 4:
            raise ValueError("targeted absolute padding must be a multiple of four, at least the baseline, and at most 4096 blocks")


def parse_domain_padding(values, baseline_blocks):
    result = {}
    for value in values:
        parts = value.split("=")
        if len(parts) != 2:
            raise ValueError("targeted clock padding must be DOMAIN=ABSOLUTE_BLOCKS")
        try:
            domain, blocks = map(int, parts)
        except ValueError as error:
            raise ValueError("targeted clock padding must contain exact integers") from error
        if [str(domain), str(blocks)] != parts or domain in result:
            raise ValueError("targeted clock padding must use unique canonical domain identities")
        result[domain] = blocks
    validate_domain_padding(baseline_blocks, result)
    return result


@contextmanager
def reserve_targeted_clock_tracks(clock_bits, baseline_blocks, domain_padding):
    """Apply the largest requested reservation to every tile in that sibling group.

    A named target must be a real clock-bearing routing domain encountered by
    the completed builder. Empty overrides reproduce the original reservation.
    All changes are made before matcher overrides or parent geometry exist.
    """
    validate_domain_padding(baseline_blocks, domain_padding)
    domain_padding = dict(domain_padding)
    clock_values = tuple(clock_bits)
    if any(type(bit) is not int for bit in clock_values):
        raise ValueError("clock identities must be exact integer signal bits")
    clock_bits = frozenset(clock_values)
    originals = {name: getattr(partition_placement, name) for name in ("compact_tile", "plan_bounded_domains")}
    seen, applications = set(), {}

    def has_clock(tile):
        return any(net.bit in clock_bits for net in tile.physical.nets)

    def extend(tile, blocks):
        if tile.overrides or tile.clock_delays or tile._balanced_positions:
            raise ValueError("clock space must be reserved before repeater matching")
        tile.track_base_z -= blocks
        return tile

    def compact(*args, **kwargs):
        tile = originals["compact_tile"](*args, **kwargs)
        return extend(tile, baseline_blocks) if baseline_blocks and has_clock(tile) else tile

    def domains(*args, **kwargs):
        tiles = originals["plan_bounded_domains"](*args, **kwargs)
        selected = {}
        for tile in tiles:
            for identity, blocks in domain_padding.items():
                if tile.physical.top == f"domain-{identity}":
                    if not has_clock(tile):
                        raise ValueError(f"targeted domain {identity} contains no requested clock")
                    selected[identity] = blocks
        if any(has_clock(tile) for tile in tiles):
            blocks = max((baseline_blocks, *selected.values()))
            if blocks:
                if any(tile.overrides or tile.clock_delays or tile._balanced_positions for tile in tiles):
                    raise ValueError("clock space must be reserved before repeater matching")
                for tile in tiles:
                    extend(tile, blocks)
            names = tuple(tile.physical.top for tile in tiles)
            applications[names] = {"sibling_modules": list(names), "absolute_padding_blocks": blocks,
                                   "targeted_domains": sorted(selected)}
        seen.update(selected)
        return tiles

    try:
        partition_placement.compact_tile = compact
        partition_placement.plan_bounded_domains = domains
        yield applications
        if set(domain_padding) != seen:
            raise ValueError(f"complete placement did not encounter targeted clock domains: {sorted(set(domain_padding) - seen)}")
    finally:
        for name, original in originals.items():
            setattr(partition_placement, name, original)
