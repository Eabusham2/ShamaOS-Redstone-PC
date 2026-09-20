from __future__ import annotations

from dataclasses import dataclass
import heapq
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Iterator, Sequence

from .hardware.logic import REDSTONE_BLOCK, SUPPORT, DUST, template_for
from .hardware.memory import repeater
from .model import Placement, Vec3


class SynthesisError(RuntimeError):
    pass


@dataclass(frozen=True)
class Endpoint:
    name: str
    pin: str
    pos: Vec3
    is_source: bool


@dataclass(frozen=True)
class RoutedNet:
    bit: int
    source: Endpoint
    sinks: tuple[Endpoint, ...]
    track: int

    @property
    def min_x(self) -> int:
        return min([self.source.pos.x, *(s.pos.x for s in self.sinks)])

    @property
    def max_x(self) -> int:
        return max([self.source.pos.x, *(s.pos.x for s in self.sinks)])


@dataclass
class PhysicalNetlist:
    top: str
    origin: Vec3
    cell_origins: dict[str, Vec3]
    cell_types: dict[str, str]
    input_ports: dict[str, tuple[Vec3, ...]]
    output_ports: dict[str, tuple[Vec3, ...]]
    nets: tuple[RoutedNet, ...]
    constant_high: tuple[Vec3, ...]
    cell_stride: int = 24
    route_z0: int = 32
    track_pitch: int = 16
    trunk_height: int = 7

    @property
    def track_count(self) -> int:
        return 0 if not self.nets else 1 + max(n.track for n in self.nets)

    def manifest(self) -> dict[str, object]:
        return {
            "top": self.top,
            "cell_count": len(self.cell_origins),
            "net_count": len(self.nets),
            "routing_tracks": self.track_count,
            "origin": vars(self.origin),
            "input_ports": {
                name: [vars(p) for p in pins] for name, pins in self.input_ports.items()
            },
            "output_ports": {
                name: [vars(p) for p in pins] for name, pins in self.output_ports.items()
            },
            "max_x": max(
                [self.origin.x, *(p.x for pins in self.output_ports.values() for p in pins),
                 *(o.x + template_for(self.cell_types[n]).width for n, o in self.cell_origins.items())]
            ),
            "max_route_z": self.origin.z + self.route_z0 + max(0, self.track_count - 1) * self.track_pitch,
        }

    def iter_placements(self, *, component: str | None = None) -> Iterator[Placement]:
        comp = component or f"logic-{self.top}"

        # Top-level terminals.
        for pins in self.input_ports.values():
            for p in pins:
                yield Placement(p.offset(dy=-1), SUPPORT, comp)
                yield Placement(p, DUST, comp)
        for pins in self.output_ports.values():
            for p in pins:
                yield Placement(p.offset(dy=-1), SUPPORT, comp)
                yield Placement(p, DUST, comp)

        # Physical logic cells.
        for name in sorted(self.cell_origins):
            origin = self.cell_origins[name]
            template = template_for(self.cell_types[name])
            yield from template.emitter(origin, f"{comp}:{name}")

        # Constant-one sinks get a local redstone block. Constant zero is just
        # an unpowered input terminal.
        for p in self.constant_high:
            yield Placement(p.offset(dz=1), REDSTONE_BLOCK, comp)

        # Routed nets.
        for net in self.nets:
            lane_z = self.origin.z + self.route_z0 + net.track * self.track_pitch
            yield from _route_net(
                net,
                lane_z=lane_z,
                trunk_y=self.origin.y + self.trunk_height,
                component=comp,
            )


def _quote_yosys(path: str | Path) -> str:
    return '"' + str(Path(path).resolve()).replace('\\', '\\\\').replace('"', '\\"') + '"'


def synthesize_json(
    rtl_files: Sequence[str | Path],
    *,
    top: str,
    output_json: str | Path,
    yosys: str = "yosys",
    repo_root: str | Path | None = None,
) -> Path:
    if not rtl_files:
        raise ValueError("at least one RTL file is required")
    if shutil.which(yosys) is None:
        raise SynthesisError(
            "Yosys is required for RTL-to-redstone synthesis. Install yosys "
            "and make sure it is in PATH."
        )

    root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[2]
    mapping = (root / "synth" / "redstone.ys").read_text(encoding="utf-8")
    output = Path(output_json).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    script = "\n".join(
        [
            "read_verilog -sv " + " ".join(_quote_yosys(p) for p in rtl_files),
            f"hierarchy -check -top {top}",
            mapping,
            f"write_json {_quote_yosys(output)}",
            "",
        ]
    )

    with tempfile.NamedTemporaryFile("w", suffix=".ys", delete=False, encoding="utf-8") as tmp:
        tmp.write(script)
        script_path = Path(tmp.name)

    try:
        proc = subprocess.run(
            [yosys, "-q", "-s", str(script_path)],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    finally:
        script_path.unlink(missing_ok=True)

    if proc.returncode != 0:
        raise SynthesisError(f"Yosys synthesis failed:\n{proc.stdout}")
    if not output.exists():
        raise SynthesisError("Yosys completed without producing the JSON netlist")
    return output


def _cell_pin_position(base: Vec3, cell_type: str, pin: str) -> Vec3:
    template = template_for(cell_type)
    try:
        local = template.pins[pin]
    except KeyError as exc:
        raise SynthesisError(
            f"physical template {cell_type} has no pin {pin!r}"
        ) from exc
    return base.offset(local.x, local.y, local.z)


def _assign_tracks(intervals: list[tuple[int, int, int]]) -> dict[int, int]:
    """Optimal-ish interval coloring with deterministic lowest-free track reuse."""
    intervals.sort(key=lambda item: (item[0], item[1], item[2]))
    active: list[tuple[int, int]] = []  # (end, track)
    free: list[int] = []
    next_track = 0
    assigned: dict[int, int] = {}

    for start, end, bit in intervals:
        while active and active[0][0] < start - 2:
            _, track = heapq.heappop(active)
            heapq.heappush(free, track)
        if free:
            track = heapq.heappop(free)
        else:
            track = next_track
            next_track += 1
        assigned[bit] = track
        heapq.heappush(active, (end, track))
    return assigned


def build_physical_netlist(
    netlist_json: str | Path,
    *,
    top: str,
    origin: Vec3 = Vec3(0, 64, 0),
    cell_stride: int = 24,
) -> PhysicalNetlist:
    raw = json.loads(Path(netlist_json).read_text(encoding="utf-8"))
    try:
        module = raw["modules"][top]
    except KeyError as exc:
        raise SynthesisError(f"top module {top!r} not found in Yosys JSON") from exc

    cell_origins: dict[str, Vec3] = {}
    cell_types: dict[str, str] = {}
    endpoints: dict[int, list[Endpoint]] = {}
    const_high: list[Vec3] = []

    def add_endpoint(bit: object, endpoint: Endpoint) -> None:
        if isinstance(bit, int):
            endpoints.setdefault(bit, []).append(endpoint)
        elif str(bit) == "1":
            if not endpoint.is_source:
                const_high.append(endpoint.pos)
        elif str(bit) in {"0", "x", "z"}:
            pass
        else:
            raise SynthesisError(f"unsupported Yosys bit token {bit!r}")

    # Top input terminals first.
    input_ports: dict[str, tuple[Vec3, ...]] = {}
    output_ports: dict[str, tuple[Vec3, ...]] = {}
    x_cursor = origin.x

    for port_name in sorted(module.get("ports", {})):
        port = module["ports"][port_name]
        if port["direction"] != "input":
            continue
        pins: list[Vec3] = []
        for index, bit in enumerate(port["bits"]):
            pos = Vec3(x_cursor, origin.y + 1, origin.z)
            x_cursor += 4
            pins.append(pos)
            add_endpoint(bit, Endpoint(f"port:{port_name}[{index}]", port_name, pos, True))
        input_ports[port_name] = tuple(pins)

    x_cursor += 16

    # Logic cells. The technology-mapped library is scalar, so each physical
    # cell has one-bit pins.
    for cell_name in sorted(module.get("cells", {})):
        cell = module["cells"][cell_name]
        cell_type = str(cell["type"]).lstrip("\").upper()
        template = template_for(cell_type)  # validates support
        base = Vec3(x_cursor, origin.y, origin.z)
        cell_origins[cell_name] = base
        cell_types[cell_name] = cell_type
        x_cursor += max(cell_stride, template.width + 6)

        directions = cell.get("port_directions", {})
        for pin_name, bits in cell.get("connections", {}).items():
            direction = directions.get(pin_name)
            if direction not in {"input", "output"}:
                raise SynthesisError(
                    f"cell {cell_name} pin {pin_name} lacks a supported direction"
                )
            if len(bits) != 1:
                raise SynthesisError(
                    f"mapped cell {cell_name}:{pin_name} is {len(bits)} bits; "
                    "redstone technology cells must be scalar"
                )
            pos = _cell_pin_position(base, cell_type, pin_name)
            add_endpoint(
                bits[0],
                Endpoint(cell_name, pin_name, pos, direction == "output"),
            )

    x_cursor += 16

    # Top output terminals after the cells.
    for port_name in sorted(module.get("ports", {})):
        port = module["ports"][port_name]
        if port["direction"] != "output":
            continue
        pins = []
        for index, bit in enumerate(port["bits"]):
            pos = Vec3(x_cursor, origin.y + 1, origin.z)
            x_cursor += 4
            pins.append(pos)
            add_endpoint(bit, Endpoint(f"port:{port_name}[{index}]", port_name, pos, False))
        output_ports[port_name] = tuple(pins)

    pending: list[tuple[int, Endpoint, tuple[Endpoint, ...]]] = []
    intervals: list[tuple[int, int, int]] = []

    for bit, eps in sorted(endpoints.items()):
        sources = [e for e in eps if e.is_source]
        sinks = [e for e in eps if not e.is_source]
        if not sinks:
            continue
        if len(sources) != 1:
            raise SynthesisError(
                f"net bit {bit} has {len(sources)} drivers; exactly one is required"
            )
        source = sources[0]
        sink_tuple = tuple(sorted(sinks, key=lambda e: (e.pos.x, e.name, e.pin)))
        lo = min([source.pos.x, *(s.pos.x for s in sink_tuple)])
        hi = max([source.pos.x, *(s.pos.x for s in sink_tuple)])
        pending.append((bit, source, sink_tuple))
        intervals.append((lo, hi, bit))

    tracks = _assign_tracks(intervals)
    routed = tuple(
        RoutedNet(bit, source, sinks, tracks[bit])
        for bit, source, sinks in pending
    )

    return PhysicalNetlist(
        top=top,
        origin=origin,
        cell_origins=cell_origins,
        cell_types=cell_types,
        input_ports=input_ports,
        output_ports=output_ports,
        nets=routed,
        constant_high=tuple(const_high),
        cell_stride=cell_stride,
    )


def _wire_line_z(
    x: int,
    y: int,
    z0: int,
    z1: int,
    *,
    direction: str,
    component: str,
    source_side: bool,
) -> Iterator[Placement]:
    if z1 < z0:
        z0, z1 = z1, z0
    for z in range(z0, z1 + 1):
        yield Placement(Vec3(x, y - 1, z), SUPPORT, component)
        if source_side:
            use_repeater = (z - z0) > 0 and (z - z0) % 10 == 0
        else:
            use_repeater = (z1 - z) > 0 and (z1 - z) % 10 == 0
        if use_repeater:
            yield Placement(Vec3(x, y, z), repeater(direction), component)
        else:
            yield Placement(Vec3(x, y, z), DUST, component)


def _endpoint_branch(
    endpoint: Endpoint,
    *,
    lane_z: int,
    trunk_y: int,
    source: bool,
    component: str,
) -> Iterator[Placement]:
    pin = endpoint.pos
    rise = trunk_y - pin.y
    if rise < 1:
        raise SynthesisError("routing trunk must be above cell pins")

    stair_start = lane_z - rise - 1
    flat_start = pin.z + 1
    flat_end = max(flat_start, stair_start)

    if flat_end >= flat_start:
        yield from _wire_line_z(
            pin.x,
            pin.y,
            flat_start,
            flat_end,
            direction="south" if source else "north",
            component=component,
            source_side=source,
        )

    # Bidirectional dust staircase to the elevated routing trunk.
    z = flat_end
    for level in range(1, rise + 1):
        z += 1
        y = pin.y + level
        yield Placement(Vec3(pin.x, y - 1, z), SUPPORT, component)
        yield Placement(Vec3(pin.x, y, z), DUST, component)

    while z < lane_z:
        z += 1
        yield Placement(Vec3(pin.x, trunk_y - 1, z), SUPPORT, component)
        yield Placement(Vec3(pin.x, trunk_y, z), DUST, component)


def _trunk(
    net: RoutedNet,
    *,
    lane_z: int,
    trunk_y: int,
    component: str,
) -> Iterator[Placement]:
    source_x = net.source.pos.x
    endpoint_x = {source_x, *(s.pos.x for s in net.sinks)}
    lo, hi = net.min_x, net.max_x

    # Repeater locations are chosen independently left/right of the source and
    # never occupy endpoint T-junctions.
    repeaters: dict[int, str] = {}

    pos = source_x + 10
    while pos < hi:
        while pos in endpoint_x and pos < hi:
            pos += 1
        if pos < hi:
            repeaters[pos] = "east"
            pos += 10

    pos = source_x - 10
    while pos > lo:
        while pos in endpoint_x and pos > lo:
            pos -= 1
        if pos > lo:
            repeaters[pos] = "west"
            pos -= 10

    for x in range(lo, hi + 1):
        yield Placement(Vec3(x, trunk_y - 1, lane_z), SUPPORT, component)
        if x in repeaters:
            yield Placement(Vec3(x, trunk_y, lane_z), repeater(repeaters[x]), component)
        else:
            yield Placement(Vec3(x, trunk_y, lane_z), DUST, component)


def _route_net(
    net: RoutedNet,
    *,
    lane_z: int,
    trunk_y: int,
    component: str,
) -> Iterator[Placement]:
    yield from _endpoint_branch(
        net.source,
        lane_z=lane_z,
        trunk_y=trunk_y,
        source=True,
        component=component,
    )
    for sink in net.sinks:
        yield from _endpoint_branch(
            sink,
            lane_z=lane_z,
            trunk_y=trunk_y,
            source=False,
            component=component,
        )
    yield from _trunk(net, lane_z=lane_z, trunk_y=trunk_y, component=component)
