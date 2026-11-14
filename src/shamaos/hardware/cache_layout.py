"""Measured one-bank cache layout policy for the bounded production backend.

Only the cache partition selects this policy. It retains every mapped entity,
uses real additional clock-routing space and still requires the ordinary
physical conservation, collision and clock-matching checks. Complete native
cache execution and whole-system acceptance remain independent gates.
"""
from __future__ import annotations

from contextlib import contextmanager
from threading import RLock

from . import partition_placement
from .bounded_facade import place_with_egress
from .partition_planner import audit_plan, build_plan


_RESERVATION_LOCK = RLock()


def cache_policy(graph):
    if graph.source["top"] != "shama_cache_adapter":
        return None
    pins = {(entity["port_name"], entity["port_index"]): entity["direction"]
            for entity in graph.entities if entity["kind"] == "port"}
    banks = [key for key in pins if key[0] == "bank_select"]
    if len(banks) != 1:
        return None
    widths = {"clk": ("input", 1), "rst": ("input", 1), "req_valid": ("input", 1),
              "req_we": ("input", 1), "req_addr": ("input", 32), "req_wdata": ("input", 32),
              "req_wstrb": ("input", 4), "selected_word": ("input", 32),
              "req_ready": ("output", 1), "req_rdata": ("output", 32), "bank_select": ("output", 1),
              "row_address": ("output", 10), "read_enable": ("output", 1),
              "write_commit": ("output", 1), "write_data": ("output", 32)}
    expected = {(name, bit): direction for name, (direction, width) in widths.items() for bit in range(width)}
    if pins != expected:
        raise ValueError("compact cache policy requires the complete one-bank controller interface")
    return {"name": "one-bank-cache-root-io-v1", "ports_at_root": True,
            "native_cells_per_leaf": 64, "boundary_terminals_per_leaf": 128,
            "routing_terminals_per_domain": 256, "children_per_cluster": 4,
            "clock_padding_blocks": 128, "clock_domain_padding_blocks": {6: 160},
            "native_runtime_verified": False}


@contextmanager
def reserve_clock_space(clock_bits, baseline_blocks, domain_padding):
    """Move complete clock-bearing track groups before their geometry is checked.

    The serial bounded builder owns this scoped reservation. Other algorithms
    keep their original callbacks after success or failure. Every targeted
    domain must be encountered and must carry a real requested clock.
    """
    if (type(baseline_blocks) is not int or not 0 <= baseline_blocks <= 512 or baseline_blocks % 4
            or not isinstance(domain_padding, dict)):
        raise ValueError("clock reservation requires bounded exact multiples of four blocks")
    requested = dict(domain_padding)
    for identity, blocks in requested.items():
        if (type(identity) is not int or identity < 0 or type(blocks) is not int
                or not baseline_blocks <= blocks <= 4096 or blocks % 4):
            raise ValueError("targeted clock reservation requires exact bounded domain identities and padding")
    values = tuple(clock_bits)
    if any(type(bit) is not int for bit in values):
        raise ValueError("clock identities must be exact integer signal bits")
    clocks = frozenset(values)
    with _RESERVATION_LOCK:
        original = {name: getattr(partition_placement, name) for name in ("compact_tile", "plan_bounded_domains")}
        seen, applications = set(), {}

        def has_clock(tile):
            return any(net.bit in clocks for net in tile.physical.nets)

        def extend(tile, blocks):
            if tile.overrides or tile.clock_delays or tile._balanced_positions:
                raise ValueError("clock space must be reserved before repeater matching")
            tile.track_base_z -= blocks
            return tile

        def compact(*args, **kwargs):
            tile = original["compact_tile"](*args, **kwargs)
            return extend(tile, baseline_blocks) if baseline_blocks and has_clock(tile) else tile

        def domains(*args, **kwargs):
            tiles = original["plan_bounded_domains"](*args, **kwargs)
            selected = {}
            for tile in tiles:
                for identity, blocks in requested.items():
                    if tile.physical.top == f"domain-{identity}":
                        if not has_clock(tile):
                            raise ValueError(f"targeted domain {identity} contains no requested clock")
                        selected[identity] = blocks
            if any(has_clock(tile) for tile in tiles):
                blocks = max((baseline_blocks, *selected.values()))
                if blocks:
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
            if seen != set(requested):
                raise ValueError(f"complete placement did not encounter targeted clock domains: {sorted(set(requested) - seen)}")
        finally:
            for name, callback in original.items():
                setattr(partition_placement, name, callback)


def place_compact_cache(graph, *, surface_y=-61, maximum_y=285):
    policy = cache_policy(graph)
    if policy is None:
        raise ValueError("the measured compact-cache policy applies only to the complete one-bank cache adapter")
    plan = build_plan(graph, cell_budget=policy["native_cells_per_leaf"],
        boundary_budget=policy["boundary_terminals_per_leaf"], domain_budget=policy["routing_terminals_per_domain"],
        arity=policy["children_per_cluster"], ports_at_root=True)
    audit_plan(graph, plan)
    clocks = {record["bit"] for record in plan["signals"] if any(sink["pin"] == "C" for sink in record["sinks"])}
    domains = {record["id"]: record for record in plan["routing_domains"]}
    for identity in policy["clock_domain_padding_blocks"]:
        if identity not in domains or not any(segment["net"] in clocks for segment in domains[identity]["segments"]):
            raise ValueError("compact-cache clock reservation is not a real clock-bearing domain of this complete graph")
    with reserve_clock_space(clocks, policy["clock_padding_blocks"], policy["clock_domain_padding_blocks"]) as applications:
        placed = place_with_egress(graph, plan, surface_y=surface_y, maximum_y=maximum_y, balance_clocks=False)
    partition_placement.balance_partition_clocks(plan, placed)
    placed.packing["cache_layout_policy"] = dict(policy, clock_reservation_groups=list(applications.values()))
    return plan, placed
