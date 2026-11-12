"""Electrical regression tests for generated block-level logic cells.

This is a deliberately bounded signal/timing model, NOT Minecraft or MCHPRS.
It models flat dust strength/adjacency, directional repeaters, repeater locks,
strong/weak solid-block power, wall torches and redstone-tick delays. It does
not model chunks, update-order quirks, torch burnout, pistons or comparators.
Passing these tests is necessary evidence for these cell templates, not an
end-to-end in-game certification. Each fixture is built from actual placements.
"""
from collections import deque
from itertools import product

import pytest

from shamaos.hardware.logic import TEMPLATES
from shamaos.model import Vec3


DIRECTIONS = {
    "east": (1, 0, 0), "west": (-1, 0, 0),
    "north": (0, 0, -1), "south": (0, 0, 1),
}
NEIGHBORS = (*DIRECTIONS.values(), (0, 1, 0), (0, -1, 0))


def offset(p, delta, sign=1):
    return tuple(a + sign*b for a, b in zip(p, delta))


class BoundedRedstone:
    def __init__(self, placements):
        self.blocks = {}
        for placement in placements:
            p = (placement.pos.x, placement.pos.y, placement.pos.z)
            if p in self.blocks:
                assert self.blocks[p] == placement.block, f"conflicting blocks at {p}"
            self.blocks[p] = placement.block
        self.properties = {p: dict(b.properties) for p, b in self.blocks.items()}
        self.wires = {p for p, b in self.blocks.items() if b.name.endswith(":redstone_wire")}
        self.repeaters = {p for p, b in self.blocks.items() if b.name.endswith(":repeater")}
        self.torches = {p for p, b in self.blocks.items() if b.name.endswith(":redstone_wall_torch")}
        self.solids = {p for p, b in self.blocks.items() if b.name in {"minecraft:stone", "minecraft:smooth_stone", "minecraft:redstone_block"}}
        unsupported = {b.name for b in self.blocks.values()} - {
            "minecraft:redstone_wire", "minecraft:repeater", "minecraft:redstone_wall_torch",
            "minecraft:stone", "minecraft:smooth_stone", "minecraft:redstone_block",
        }
        assert not unsupported, unsupported
        self.on = {p: self.properties[p].get("powered", "false") == "true" for p in self.repeaters}
        self.on.update({p: self.properties[p].get("lit", "true") == "true" for p in self.torches})
        self.forced = {}
        self.power = {p: 0 for p in self.wires}
        self.strong = set()
        self.weak = set()
        self.pending = {}
        self.tick = 0
        self.electrical()

    def front(self, p):
        # Java diode facing points to its input; a wall torch faces outward.
        sign = -1 if p in self.repeaters else 1
        return offset(p, DIRECTIONS[self.properties[p]["facing"]], sign)

    def behind(self, p):
        sign = 1 if p in self.repeaters else -1
        return offset(p, DIRECTIONS[self.properties[p]["facing"]], sign)

    def drive(self, **inputs):
        for p, value in inputs.items():
            self.forced[p] = bool(value)

    def set_pin(self, p, value):
        key = (p.x, p.y, p.z) if isinstance(p, Vec3) else p
        assert key in self.wires
        self.forced[key] = bool(value)
        self.electrical()

    def level(self, p):
        key = (p.x, p.y, p.z) if isinstance(p, Vec3) else p
        return self.power.get(key, 0)

    def electrical(self):
        self.strong = {p for p, b in self.blocks.items() if b.name.endswith(":redstone_block")}
        for p in self.repeaters:
            if self.on[p] and self.front(p) in self.solids:
                self.strong.add(self.front(p))
        for p in self.torches:
            above = offset(p, (0, 1, 0))
            if self.on[p] and above in self.solids:
                self.strong.add(above)

        self.power = {p: 0 for p in self.wires}
        queue = deque()

        def seed(p, level=15):
            if p in self.wires and level > self.power[p]:
                self.power[p] = level
                queue.append(p)

        for p, value in self.forced.items():
            if value:
                seed(p)
        for p in self.repeaters:
            if self.on[p]:
                seed(self.front(p))
        for p in self.torches:
            if self.on[p]:
                for delta in NEIGHBORS:
                    target = offset(p, delta)
                    if target != self.behind(p):
                        seed(target)
        for p in self.strong:
            for delta in NEIGHBORS:
                seed(offset(p, delta))
        while queue:
            p = queue.popleft()
            for direction, delta in DIRECTIONS.items():
                target = offset(p, delta)
                if self.properties[p].get(direction, "none") != "none":
                    seed(target, self.power[p] - 1)

        self.weak = set(self.strong)
        for p, level in self.power.items():
            if not level:
                continue
            below = offset(p, (0, -1, 0))
            if below in self.solids:
                self.weak.add(below)
            for direction, delta in DIRECTIONS.items():
                target = offset(p, delta)
                if target in self.solids and self.properties[p].get(direction, "none") != "none":
                    self.weak.add(target)

    def source_on(self, source, receiver):
        if source in self.wires:
            return self.power[source] > 0
        if source in self.solids:
            return source in self.weak
        if source in self.repeaters:
            return self.on[source] and self.front(source) == receiver
        if source in self.torches:
            return self.on[source] and self.behind(source) != receiver
        return False

    def locked(self, p):
        facing = self.properties[p]["facing"]
        lateral = ((1, 0, 0), (-1, 0, 0)) if facing in {"north", "south"} else ((0, 0, 1), (0, 0, -1))
        return any(
            (side := offset(p, delta)) in self.repeaters
            and self.on[side] and self.front(side) == p
            for delta in lateral
        )

    def advance(self, ticks):
        trace = []
        for _ in range(ticks):
            self.electrical()
            for p in self.on:
                if p in self.repeaters and self.locked(p):
                    self.pending.pop(p, None)
                    continue
                desired = (
                    self.behind(p) not in self.weak if p in self.torches
                    else self.source_on(self.behind(p), p)
                )
                if desired == self.on[p]:
                    self.pending.pop(p, None)
                elif p not in self.pending or self.pending[p][1] != desired:
                    delay = 1 if p in self.torches else int(self.properties[p]["delay"])
                    self.pending[p] = (self.tick + delay, desired)
            self.tick += 1
            updates = {p: value for p, (due, value) in self.pending.items() if due <= self.tick}
            for p, value in updates.items():
                self.pending.pop(p, None)
                if p not in self.repeaters or not self.locked(p):
                    self.on[p] = value
            self.electrical()
            trace.append(dict(self.power))
        return trace


@pytest.mark.parametrize("name", ["BUF", "NOT", "AND", "NAND", "NOR"])
def test_generated_combinational_cell_truth_tables_without_input_backfeed(name):
    template = TEMPLATES[name]
    model = BoundedRedstone(template.emitter(Vec3(0, 0, 0), name))
    inputs = [pin for pin in template.pins if pin != "Y"]
    # All transitions in both directions catch stale powered/torch state too.
    states = list(product((0, 1), repeat=len(inputs)))
    for values in states + list(reversed(states)):
        for pin, value in zip(inputs, values):
            model.set_pin(template.pins[pin], value)
        model.advance(20)
        a = values[0]
        b = values[-1]
        expected = {"BUF": a, "NOT": 1-a, "AND": a & b, "NAND": 1-(a & b), "NOR": 1-(a | b)}[name]
        assert bool(model.level(template.pins["Y"])) == bool(expected), (name, values)
        for pin, value in zip(inputs, values):
            assert bool(model.level(template.pins[pin])) == bool(value), (name, "backfeed", pin, values)


def test_dff_captures_only_rising_edges_and_holds_during_falling_edge_data_changes():
    template = TEMPLATES["DFF"]
    model = BoundedRedstone(template.emitter(Vec3(0, 0, 0), "DFF"))
    d, c, q = (template.pins[p] for p in ("D", "C", "Q"))
    model.set_pin(c, 0)
    model.set_pin(d, 0)
    model.advance(24)
    expected = 0
    for value in (1, 0, 1, 1, 0, 0, 1):
        model.set_pin(d, value)
        model.advance(template.minimum_clock_half_period)
        assert bool(model.level(q)) == bool(expected), "transparent during clock-low"
        model.set_pin(c, 1)
        model.advance(template.minimum_clock_half_period)
        expected = value
        assert bool(model.level(q)) == bool(expected), "rising edge failed to capture"
        model.set_pin(d, 1-value)
        model.advance(template.minimum_clock_half_period)
        assert bool(model.level(q)) == bool(expected), "transparent during clock-high"
        # A data transition at the falling edge must not leak through the slave.
        model.set_pin(c, 0)
        model.set_pin(d, value)
        trace = model.advance(template.minimum_clock_half_period)
        key = (q.x, q.y, q.z)
        assert all(bool(step[key]) == bool(expected) for step in trace), "falling-edge race/glitch"


def test_cell_footprints_cover_emitted_blocks_and_have_no_conflicting_placements():
    for name, template in TEMPLATES.items():
        placements = list(template.emitter(Vec3(0, 0, 0), name))
        BoundedRedstone(placements)  # Also rejects conflicting block overwrites.
        assert all(0 <= p.pos.x < template.width for p in placements), name
        assert all(-template.depth < p.pos.z <= 0 for p in placements), name
