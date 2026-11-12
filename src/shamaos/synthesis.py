from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import heapq
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Iterator, Sequence

from .hardware.external_router import ExternalNet, iter_external_router
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
    track_pitch: int = 4
    routing_branch_y: int = 286
    routing_trunk_y: int = 290
    minimum_build_y: int = -64
    north_constant_high: tuple[Vec3, ...] = ()

    @property
    def track_count(self) -> int:
        # The physical router currently gives each net its own Z track. The
        # interval-coloring estimate in RoutedNet.track is not emitted reuse.
        return len(self.nets)

    @property
    def trunk_track_bounds(self) -> tuple[int, int] | None:
        if not self.nets:
            return None
        minimum_endpoint_z = min(
            endpoint.pos.z
            for net in self.nets
            for endpoint in (net.source, *net.sinks)
        )
        first = minimum_endpoint_z - (self.track_count + 1) * self.track_pitch - 4096
        return first, first + (self.track_count - 1) * self.track_pitch

    def manifest(self) -> dict[str, object]:
        return {
            "top": self.top,
            "cell_count": len(self.cell_origins),
            "net_count": len(self.nets),
            "routing_tracks": self.track_count,
            "routing_planes": {
                "branch_y": self.routing_branch_y,
                "trunk_y": self.routing_trunk_y,
                "minimum_build_y": self.minimum_build_y,
            },
            "trunk_track_bounds": self.trunk_track_bounds,
            "routing_bounds_scope": "trunk Z tracks only; cell and endpoint escape bounds are separate",
            "cell_timing": {
                "minimum_clock_half_period_redstone_ticks": max(
                    (template_for(kind).minimum_clock_half_period for kind in self.cell_types.values()), default=0
                ),
                "setup_redstone_ticks": max(
                    (template_for(kind).setup_ticks for kind in self.cell_types.values()), default=0
                ),
                "hold_redstone_ticks": max(
                    (template_for(kind).hold_ticks for kind in self.cell_types.values()), default=0
                ),
                "scope": "local cell pins; external route delays and clock skew require additional validation",
            },
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
            "min_route_z": None if self.trunk_track_bounds is None else self.trunk_track_bounds[0],
            "max_route_z": None if self.trunk_track_bounds is None else self.trunk_track_bounds[1],
        }

    def _iter_local_placements(self, comp: str) -> Iterator[Placement]:
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
        # an unpowered input terminal. A top port's south side is reserved for
        # the enclosing route, so its local tie must use the north side.
        north_constants = set(self.north_constant_high)
        for p in self.constant_high:
            yield Placement(p.offset(dz=-1 if p in north_constants else 1), REDSTONE_BLOCK, comp)

    def iter_placements(self, *, component: str | None = None) -> Iterator[Placement]:
        comp = component or f"logic-{self.top}"
        yield from self._iter_local_placements(comp)

        # Routed nets use the same crossing-safe two-plane router as the
        # system interconnect. Partitions use Y=286/290, the shell Y=300/304,
        # and global wiring Y=314/318. At shared macro terminals the internal
        # route owns the north face and the enclosing route owns the south.
        # The latter climbs beyond the starts of lower-tier northbound Z
        # branches, instead of passing through those wires on its way up.
        physical_nets = tuple(
            ExternalNet(
                f"logic-bit-{net.bit}",
                net.source.pos,
                tuple(sink.pos for sink in net.sinks),
            )
            for net in self.nets
        )
        access = {
            endpoint.pos: ("south",) if endpoint.name.startswith("macro:") else ("north",)
            for net in self.nets
            for endpoint in (net.source, *net.sinks)
            if endpoint.name.startswith(("port:", "macro:"))
        }
        yield from iter_external_router(
            physical_nets,
            component=f"{comp}:internal",
            branch_y=self.routing_branch_y,
            trunk_y=self.routing_trunk_y,
            track_spacing=self.track_pitch,
            obstacles=self._iter_local_placements(comp),
            access=access,
            minimum_y=self.minimum_build_y,
        )



@dataclass(frozen=True)
class MacroInstance:
    """Physical implementation bound to one black-box cell in a shell netlist."""

    instance_name: str
    module_type: str
    physical: PhysicalNetlist



def _quote_yosys(path: str | Path) -> str:
    return '"' + str(Path(path).resolve()).replace('\\', '\\\\').replace('"', '\\"') + '"'


@lru_cache(maxsize=16)
def _abc_supports_fast(yosys: str) -> bool:
    """Probe the installed command, rather than assuming a Yosys version.

    Some packaged Yosys builds do not implement ``abc -fast``. The checked-in
    mapping script uses the portable command; this optimization is added only
    when the *installed executable's* help advertises it. A failed probe leaves
    the portable mapping in place and does not hide a later synthesis error.
    """
    try:
        result = subprocess.run(
            [yosys, "-Q", "-T", "-p", "help abc"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and bool(
        re.search(r"^\s*-fast(?:\s|$)", result.stdout or "", re.MULTILINE)
    )


def _portable_mapping(mapping: str, *, fast: bool) -> str:
    """Normalize legacy scripts too; never pass unsupported ``-fast`` blindly."""
    lines = []
    for line in mapping.splitlines():
        if re.match(r"^\s*abc(?:\s|$)", line):
            line = re.sub(r"\s+-fast(?=\s|$)", "", line)
            if fast:
                line = re.sub(r"^(\s*abc)(?=\s|$)", r"\1 -fast", line)
        lines.append(line)
    return "\n".join(lines) + "\n"


def synthesize_json(
    rtl_files: Sequence[str | Path],
    *,
    top: str,
    output_json: str | Path,
    yosys: str = "yosys",
    repo_root: str | Path | None = None,
    blackbox_modules: Sequence[str] = (),
    mapping_mode: str = "abc",
) -> Path:
    if not rtl_files:
        raise ValueError("at least one RTL file is required")
    if shutil.which(yosys) is None:
        raise SynthesisError(
            "Yosys is required for RTL-to-redstone synthesis. Install yosys "
            "and make sure it is in PATH."
        )

    root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[2]
    mapping_files = {
        "abc": root / "synth" / "redstone.ys",
        "aig": root / "synth" / "redstone_aig.ys",
    }
    try:
        mapping_path = mapping_files[mapping_mode]
    except KeyError as exc:
        raise ValueError(
            f"unknown synthesis mapping mode {mapping_mode!r}; "
            f"expected one of {sorted(mapping_files)}"
        ) from exc
    mapping = _portable_mapping(
        mapping_path.read_text(encoding="utf-8"),
        fast=mapping_mode == "abc" and _abc_supports_fast(str(shutil.which(yosys))),
    )
    output = Path(output_json).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    commands = [
        "read_verilog -sv -I " + _quote_yosys(root / "rtl") + " " + " ".join(_quote_yosys(p) for p in rtl_files),
        f"hierarchy -check -top {top}",
    ]
    if blackbox_modules:
        commands.append("blackbox " + " ".join(blackbox_modules))
    commands.extend(
        [
            mapping,
            f"write_json {_quote_yosys(output)}",
            "",
        ]
    )
    script = "\n".join(commands)

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

    output.with_suffix(".yosys.log").write_text(proc.stdout or "", encoding="utf-8")
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


def _expand_sequential_cells(module: dict[str, object]) -> None:
    """Lower all supported scalar Yosys edge-register variants exactly.

    Supported families are DFF, DFFE, SDFF, SDFFE and SDFFCE, for both clock
    edges, either control polarity and reset-to-zero/reset-to-one. In Yosys,
    SDFFE gives synchronous reset priority over enable; SDFFCE gives enable
    priority. Those are different functions and deliberately use different mux
    nesting here. A negative edge is an inverted clock, never a positive-edge
    alias. No control is discarded and no enable is turned into a gated clock.

    Asynchronous reset/set and level-sensitive cells are rejected explicitly.
    Converting them to synchronous resets would change their architecture.
    See Yosys ``techlibs/common/simcells.v`` for the source cell semantics.
    """
    cells = module.get("cells", {})
    if not isinstance(cells, dict):
        return

    max_bit = 1
    ports = module.get("ports", {})
    if isinstance(ports, dict):
        for port in ports.values():
            if isinstance(port, dict):
                for bit in port.get("bits", []):
                    if isinstance(bit, int):
                        max_bit = max(max_bit, bit)

    for cell in cells.values():
        if not isinstance(cell, dict):
            continue
        for bits in cell.get("connections", {}).values():
            for bit in bits:
                if isinstance(bit, int):
                    max_bit = max(max_bit, bit)

    next_bit = max_bit + 1

    def alloc_bit() -> int:
        nonlocal next_bit
        bit = next_bit
        next_bit += 1
        return bit

    expanded: dict[str, object] = {}
    reserved_names = set(cells)
    inverses: dict[object, object] = {}

    def add_cell(name: str, cell_type: str, connections: dict[str, object]) -> None:
        candidate = name
        index = 1
        while candidate in reserved_names or candidate in expanded:
            candidate = f"{name}${index}"
            index += 1
        reserved_names.add(candidate)
        expanded[candidate] = {
            "type": cell_type,
            "port_directions": {
                pin: "output" if pin in {"Y", "Q"} else "input"
                for pin in connections
            },
            "connections": {pin: [bit] for pin, bit in connections.items()},
        }

    def invert(name: str, bit: object) -> object:
        if bit in {"0", "1"}:
            return "1" if bit == "0" else "0"
        if bit in inverses:
            return inverses[bit]
        result = alloc_bit()
        add_cell(name, "$_NOT_", {"A": bit, "Y": result})
        inverses[bit] = result
        inverses[result] = bit
        return result

    def mux(name: str, select: object, when_zero: object, when_one: object) -> object:
        # Y = S ? B : A, expressed only in validated NOT/NAND cells.
        if when_zero == when_one:
            return when_zero
        inverse = invert(f"{name}$not", select)
        n_zero, n_one, result = alloc_bit(), alloc_bit(), alloc_bit()
        add_cell(f"{name}$zero", "$_NAND_", {"A": when_zero, "B": inverse, "Y": n_zero})
        add_cell(f"{name}$one", "$_NAND_", {"A": when_one, "B": select, "Y": n_one})
        add_cell(f"{name}$join", "$_NAND_", {"A": n_zero, "B": n_one, "Y": result})
        return result

    for cell_name, cell_obj in cells.items():
        if not isinstance(cell_obj, dict):
            expanded[cell_name] = cell_obj
            continue

        raw_type = str(cell_obj.get("type", "")).lstrip("\\").upper()
        patterns = (
            ("DFF", r"\$_DFF_([PN])_"),
            ("DFFE", r"\$_DFFE_([PN])([PN])_"),
            ("SDFF", r"\$_SDFF_([PN])([PN])([01])_"),
            ("SDFFE", r"\$_SDFFE_([PN])([PN])([01])([PN])_"),
            ("SDFFCE", r"\$_SDFFCE_([PN])([PN])([01])([PN])_"),
        )
        match = None
        family = ""
        for kind, pattern in patterns:
            match = re.fullmatch(pattern, raw_type)
            if match:
                family = kind
                break
        if match is None:
            if re.match(r"\$_(?:[AS]?DFF|DFFS|DFFE|SDFF|DLATCH|SR)", raw_type):
                raise SynthesisError(
                    f"unsupported sequential cell {cell_name!r}: {raw_type}. "
                    "Asynchronous set/reset and level-sensitive storage cannot "
                    "be silently replaced by synchronous positive-edge DFFs."
                )
            expanded[cell_name] = cell_obj
            continue

        conns = cell_obj.get("connections", {})

        def scalar(pin: str) -> object:
            bits = conns.get(pin)
            if not isinstance(bits, list) or len(bits) != 1:
                raise SynthesisError(f"malformed {raw_type} cell {cell_name!r}: {pin} must be scalar")
            return bits[0]

        c_bit, d_bit, q_bit = scalar("C"), scalar("D"), scalar("Q")
        suffix = match.groups()
        c_active = c_bit if suffix[0] == "P" else invert(f"{cell_name}$clock_not", c_bit)

        reset_active = None
        reset_value = None
        if family.startswith("SDFF"):
            r_bit = scalar("R")
            reset_active = r_bit if suffix[1] == "P" else invert(f"{cell_name}$reset_not", r_bit)
            reset_value = suffix[2]

        enable_active = None
        if family in {"DFFE", "SDFFE", "SDFFCE"}:
            e_bit = scalar("E")
            enable_polarity = suffix[1] if family == "DFFE" else suffix[3]
            enable_active = e_bit if enable_polarity == "P" else invert(f"{cell_name}$enable_not", e_bit)

        d_next = d_bit
        if family == "SDFFCE":
            # Enable priority: disabled means hold, even while reset is active.
            d_next = mux(f"{cell_name}$reset_mux", reset_active, d_bit, reset_value)
            d_next = mux(f"{cell_name}$enable_mux", enable_active, q_bit, d_next)
        else:
            if enable_active is not None:
                d_next = mux(f"{cell_name}$enable_mux", enable_active, q_bit, d_next)
            if reset_active is not None:
                # SDFFE reset priority: reset overrides a disabled enable.
                d_next = mux(f"{cell_name}$reset_mux", reset_active, d_next, reset_value)

        # Preserve a plain positive-edge DFF instead of gratuitously renaming it.
        if family == "DFF" and suffix[0] == "P":
            expanded[cell_name] = cell_obj
        else:
            add_cell(f"{cell_name}$dff", "$_DFF_P_", {"C": c_active, "D": d_next, "Q": q_bit})

    module["cells"] = expanded


def build_physical_netlist(
    netlist_json: str | Path,
    *,
    top: str,
    origin: Vec3 = Vec3(0, 64, 0),
    cell_stride: int = 24,
    macro_instances: dict[str, MacroInstance] | None = None,
    routing_branch_y: int = 286,
    routing_trunk_y: int = 290,
    minimum_build_y: int = -64,
) -> PhysicalNetlist:
    raw = json.loads(Path(netlist_json).read_text(encoding="utf-8"))
    try:
        module = raw["modules"][top]
    except KeyError as exc:
        raise SynthesisError(f"top module {top!r} not found in Yosys JSON") from exc

    _expand_sequential_cells(module)

    cell_origins: dict[str, Vec3] = {}
    cell_types: dict[str, str] = {}
    macro_instances = macro_instances or {}
    endpoints: dict[int, list[Endpoint]] = {}
    const_high: list[Vec3] = []
    north_const_high: list[Vec3] = []

    def add_endpoint(bit: object, endpoint: Endpoint) -> None:
        if isinstance(bit, int):
            endpoints.setdefault(bit, []).append(endpoint)
        elif str(bit) == "1":
            if not endpoint.is_source:
                const_high.append(endpoint.pos)
                if endpoint.name.startswith("port:"):
                    north_const_high.append(endpoint.pos)
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

    # Gate cells are placed on a 2-D grid instead of a single multi-million
    # block line. Rows extend toward negative Z; routing tracks are above the
    # cell grid, with endpoint escapes checked against the actual cell blocks.
    primitive_types = [
        str(cell["type"])
        for name, cell in module.get("cells", {}).items()
        if name not in macro_instances and name.lstrip("\\") not in macro_instances
    ]
    templates = [template_for(kind) for kind in primitive_types]
    # Use one common stride derived from the widest template. The old code
    # computed a per-cell `stride` but ignored it, overlapping wider cells.
    grid_stride = max(cell_stride, *(t.width + 8 for t in templates), 24)
    cell_row_pitch = max([32, *(t.depth + 16 for t in templates)])
    cells_per_row = min(1024, max(1, math.isqrt(len(templates) - 1) + 1)) if templates else 1
    cell_grid_x0 = x_cursor + 64

    primitive_index = 0
    for _cell_index, cell_name in enumerate(sorted(module.get("cells", {}))):
        cell = module["cells"][cell_name]
        raw_cell_type = str(cell["type"]).lstrip("\\")
        macro_binding = (
            macro_instances.get(cell_name)
            or macro_instances.get(cell_name.lstrip("\\"))
        )

        if macro_binding is not None:
            if raw_cell_type != macro_binding.module_type:
                raise SynthesisError(
                    f"shell macro {cell_name!r} has type {raw_cell_type!r}; "
                    f"expected {macro_binding.module_type!r}"
                )
            directions = cell.get("port_directions", {})
            for pin_name, bits in cell.get("connections", {}).items():
                direction = directions.get(pin_name)
                if direction not in {"input", "output"}:
                    if pin_name in macro_binding.physical.output_ports:
                        direction = "output"
                    elif pin_name in macro_binding.physical.input_ports:
                        direction = "input"
                    else:
                        raise SynthesisError(
                            f"macro {cell_name}:{pin_name} has no known direction"
                        )

                physical_ports = (
                    macro_binding.physical.output_ports
                    if direction == "output"
                    else macro_binding.physical.input_ports
                )
                try:
                    positions = physical_ports[pin_name]
                except KeyError as exc:
                    raise SynthesisError(
                        f"macro {cell_name} physical partition has no "
                        f"{direction} port {pin_name!r}"
                    ) from exc
                if len(bits) != len(positions):
                    raise SynthesisError(
                        f"macro {cell_name}:{pin_name} width {len(bits)} "
                        f"does not match physical partition width {len(positions)}"
                    )
                for bit_index, (bit, pos) in enumerate(zip(bits, positions)):
                    add_endpoint(
                        bit,
                        Endpoint(
                            f"macro:{cell_name}[{bit_index}]",
                            pin_name,
                            pos,
                            direction == "output",
                        ),
                    )
            continue

        cell_type = raw_cell_type.upper()
        template = template_for(cell_type)  # validates support

        cell_index = primitive_index
        primitive_index += 1
        col = cell_index % cells_per_row
        row = cell_index // cells_per_row
        base = Vec3(
            cell_grid_x0 + col * grid_stride,
            origin.y,
            origin.z - row * cell_row_pitch,
        )
        cell_origins[cell_name] = base
        cell_types[cell_name] = cell_type

        directions = cell.get("port_directions", {})
        for pin_name, bits in cell.get("connections", {}).items():
            direction = directions.get(pin_name)
            if direction not in {"input", "output"}:
                direction = "output" if pin_name in {"Y", "Q"} else "input"
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

    # Top output terminals have their own fixed strip; they no longer move
    # farther away as cell count grows.
    x_cursor = cell_grid_x0 + cells_per_row * grid_stride + 64
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
            aliases = [
                f"{name}[{index}]"
                for name, net in module.get("netnames", {}).items()
                for index, value in enumerate(net.get("bits", ()))
                if value == bit
            ]
            details = ", ".join(aliases[:6]) or "unnamed"
            consumers = ", ".join(f"{e.name}:{e.pin}" for e in sinks[:6])
            raise SynthesisError(
                f"net bit {bit} ({details}) has {len(sources)} drivers; "
                f"exactly one is required; sinks: {consumers}"
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
        cell_stride=grid_stride,
        routing_branch_y=routing_branch_y,
        routing_trunk_y=routing_trunk_y,
        minimum_build_y=minimum_build_y,
        north_constant_high=tuple(north_const_high),
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
