"""Measure every retained clock path without accepting or mutating a match."""
from __future__ import annotations

from dataclasses import asdict

from shamaos.hardware.external_router import _trunk
from shamaos.hardware.logic import template_for
from shamaos.hardware.memory import DUST, SUPPORT, repeater
from shamaos.hardware.partition_placement import PlacementError, _literal_states


def _delay(blocks):
    return sum(int(dict(block.block.properties)["delay"])
               for block in blocks if block.block.name == "minecraft:repeater")


def _path_flat_capacity(blocks, start, finish):
    """Upper bound only: require an unbranched directed geometric path.

    No position here is approved for replacement. Actual use must additionally
    reject shared positions, other-net neighbors and module contacts, then
    prove the resulting clock pulses in the native engine.
    """
    states = {point: state for point, state in _literal_states(iter(blocks)).items() if state != SUPPORT}
    if set(state.name for state in states.values()) - {"minecraft:redstone_wire", "minecraft:repeater"}:
        raise PlacementError("clock endpoint path contains unsupported active blocks")
    adjacency = {}
    for point in states:
        neighbors = [point.offset(dx, dy, dz)
                     for dx, dz in ((-1, 0), (1, 0), (0, -1), (0, 1)) for dy in (-1, 0, 1)
                     if point.offset(dx, dy, dz) in states]
        if len(neighbors) != (1 if point in (start, finish) else 2):
            return {"status": "not-a-simple-geometric-path", "point": asdict(point), "neighbors": len(neighbors)}
        adjacency[point] = neighbors
    if start not in states or finish not in states:
        return {"status": "missing-path-endpoint"}
    order, previous, point = [], None, start
    while True:
        order.append(point)
        if point == finish:
            break
        next_points = [value for value in adjacency[point] if value != previous]
        if len(next_points) != 1 or len(order) > len(states):
            return {"status": "geometric-path-cycle-or-branch"}
        previous, point = point, next_points[0]
    if len(order) != len(states):
        return {"status": "disconnected-geometric-path"}
    directions = {(1, 0): "east", (-1, 0): "west", (0, 1): "south", (0, -1): "north"}
    available, dust, existing = 0, 0, 0
    for index in range(2, len(order) - 2):
        before, point, after = order[index - 1:index + 2]
        if before.y != point.y or after.y != point.y:
            continue
        if (point.x - before.x, point.z - before.z) != (after.x - point.x, after.z - point.z):
            continue
        state = states[point]
        facing = directions[after.x - point.x, after.z - point.z]
        if state.name == "minecraft:repeater":
            properties = dict(state.properties)
            if properties["facing"] != dict(repeater(facing).properties)["facing"]:
                return {"status": "existing-repeater-opposes-directed-path", "point": asdict(point)}
            available += 4 - int(properties["delay"])
            existing += 1
        elif state == DUST:
            available += 4
            dust += 1
    return {"status": "geometric-upper-bound-only", "straight_dust_positions": dust,
            "straight_existing_repeaters": existing, "available_extra_delay_upper_bound": available,
            "path_active_positions": len(order)}


def diagnose_clock_capacity(plan, placed):
    modules = placed.all_modules()
    entities = {record["id"]: record for record in plan["entities"]}
    reports, rows = {}, []
    for signal in plan["signals"]:
        clock_sinks = [sink for sink in signal["sinks"] if sink["pin"] == "C"
            and entities[sink["entity"]]["kind"] == "cell"
            and template_for(entities[sink["entity"]]["cell_type"]).cell_type == "DFF"]
        if not clock_sinks:
            continue
        bit = signal["bit"]
        links = {link["source"]["terminal_id"]: link for link in signal["boundary_links"]}
        endpoint_key = lambda end: ("leaf", end["cluster"]) if end["domain"] is None else ("domain", end["domain"])
        completed, pending = {}, set()

        def tail(key):
            if key in completed:
                return completed[key]
            if key in pending:
                raise PlacementError("clock boundary routes form a cycle")
            pending.add(key)
            tile = modules[key]
            net = next(net for net in tile.physical.nets if net.bit == bit)
            tails = {}
            for sink in net.sinks:
                if sink.pin in links:
                    delay = tail(endpoint_key(links[sink.pin]["sink"]))
                    if delay is not None:
                        tails[sink.pos] = delay
                elif sink.name in tile.physical.cell_types and sink.pin == "C" and template_for(tile.physical.cell_types[sink.name]).cell_type == "DFF":
                    tails[sink.pos] = 0
            if not tails:
                completed[key] = None
                pending.remove(key)
                return None
            source_trunk, source_blocks = tile.endpoint_route(net, net.source)
            leaves = {sink: tile.endpoint_route(net, sink) for sink in net.sinks}
            trunk_blocks = list(_trunk(source_trunk, tuple(trunk for trunk, _ in leaves.values()), component="clock-capacity"))
            arrivals = {}
            for sink in net.sinks:
                if sink.pos in tails:
                    trunk, blocks = leaves[sink]
                    lo, hi = sorted((source_trunk.x, trunk.x))
                    arrivals[sink] = _delay(source_blocks) + _delay(blocks) + _delay(
                        block for block in trunk_blocks if lo < block.pos.x < hi) + tails[sink.pos]
            target = max(arrivals.values())
            record = {"module": list(key), "net": bit, "target_if_all_descendants_can_be_balanced": target,
                      "track_base_z": tile.track_base_z, "sinks": []}
            for sink, arrival in arrivals.items():
                trunk, blocks = leaves[sink]
                escape = tile.escapes[sink.pos]
                track_z = tile.track_base_z + tile.tracks[bit] * 4
                lo, hi = sorted((track_z + 4, escape.escape_z - 6))
                candidates = [block for block in blocks if block.pos.y == tile.branch_y and lo < block.pos.z < hi
                              and (block.block == DUST or block.block.name == "minecraft:repeater")]
                capacity = sum(4 - (int(dict(block.block.properties)["delay"])
                    if block.block.name == "minecraft:repeater" else 0) for block in candidates)
                needed = target - arrival
                record["sinks"].append({"sink": sink.name, "pin": sink.pin, "position": asdict(sink.pos),
                    "arrival": arrival, "tail": tails[sink.pos], "needed_extra_delay": needed,
                    "available_private_branch_extra_delay": capacity, "private_branch_positions": len(candidates),
                    "shortfall": max(0, needed - capacity),
                    "entire_endpoint_straight_path": _path_flat_capacity(blocks, trunk, sink.pos)})
            rows.append(record)
            completed[key] = target
            pending.remove(key)
            return target

        source_leaf = plan["entity_leaf"][signal["source"]["entity"]]
        target = tail(("leaf", source_leaf))
        facade_prefix = 0
        source_entity = signal["source"]["entity"]
        for tile in getattr(placed, "all_facade_modules", lambda: {})().values():
            for net in tile.physical.nets:
                if net.bit != source_entity:
                    continue
                if len(net.sinks) != 1:
                    raise PlacementError("original clock facade is not a single directed delivery path")
                source_trunk, source_blocks = tile.endpoint_route(net, net.source)
                sink_trunk, sink_blocks = tile.endpoint_route(net, net.sinks[0])
                lo, hi = sorted((source_trunk.x, sink_trunk.x))
                facade_prefix += _delay(source_blocks) + _delay(sink_blocks) + _delay(
                    block for block in _trunk(source_trunk, (sink_trunk,), component="clock-capacity") if lo < block.pos.x < hi)
        reports[str(bit)] = {"clock_leaves": len(clock_sinks), "modules": len(completed),
            "ideal_original_pin_to_register_delay": target, "facade_common_prefix_repeater_delay": facade_prefix,
            "ideal_exterior_to_register_delay": target + facade_prefix}
    shortages = [{"module": record["module"], "net": record["net"], **sink}
                 for record in rows for sink in record["sinks"] if sink["shortfall"]]
    return {"status": "diagnostic-only-no-clock-matching-or-runtime-acceptance", "clocks": reports,
            "modules": rows, "shortages": shortages, "maximum_shortfall": max((row["shortfall"] for row in shortages), default=0),
            "scope": "All current private-branch capacities and hypothetical balanced tails; full-path figures are upper bounds pending ownership/isolation checks",
            "runtime_verified": False}
