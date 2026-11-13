from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .hardware.clock import clock_ports, iter_clock, system_clock_origin
from .hardware.controls import control_ports
from .hardware.debug import debug_ports,iter_debug_panel
from .hardware.display import LampPanelSpec, panel_ports
from .hardware.external_router import ExternalNet, iter_external_router
from .hardware.interfaces import RoutingInterfaces, system_routing_interfaces
from .hardware.logic import REDSTONE_BLOCK
from .hardware.memory import MemoryFabricSpec
from .hardware.memory import DUST
from .hardware.memory_backbone import fabric_tile_ports, iter_memory_backbone
from .layout import MachineGeometry, default_origins, memory_placement_options, memory_specs, vram_spec
from .model import BlockState, Placement, Vec3
from .synthesis import (
    MacroInstance,
    PhysicalNetlist,
    build_physical_netlist,
    synthesize_json,
)


SOC_RTL_FILES = (
    "rtl/sha256_compress.sv",
    "rtl/sha256_fixed64.sv",
    "rtl/bitcoin_dsha256.sv",
    "rtl/shama_cpu.sv",
    "rtl/shama_font5x7.sv",
    "rtl/shama_gpu.sv",
    "rtl/shama_display_bridge.sv",
    "rtl/shama_input.sv",
    "rtl/shama_onehot_ring.sv",
    "rtl/shama_ring_memctl.sv",
    "rtl/shama_memory_adapter.sv",
    "rtl/shama_services.sv",
    "rtl/shama_kernel_accel.sv",
    "rtl/shama_fs_accel.sv",
    "rtl/shama_asm_accel.sv",
    "rtl/shama_soc.sv",
)


# Each direct child of shama_soc is synthesized independently. The top shell is
# then synthesized with these modules black-boxed and physically connected to
# the real partition port terminals. This is exactly the same logic hierarchy
# as the integrated RTL; it simply avoids one gigantic flatten/ABC problem.
PARTITION_SPECS: tuple[tuple[str, str], ...] = (
    ("u_cpu", "shama_cpu"),
    ("u_input", "shama_input"),
    ("u_gpu", "shama_gpu"),
    ("u_display_bridge", "shama_display_bridge"),
    ("u_vram", "shama_vram_adapter"),
    ("u_services", "shama_services"),
    ("u_kernel", "shama_kernel_accel"),
    ("u_fs", "shama_fs_accel"),
    ("u_asm", "shama_asm_accel"),
    ("u_cache", "shama_cache_adapter"),
    ("u_ram", "shama_ram_adapter"),
    ("u_flash", "shama_flash_adapter"),
) 

# The two memory-heavy accelerators intentionally bypass ABC. Their inferred
# workspace/metadata memories are already behavior-validated in RTL; direct
# AIG mapping preserves the exact Boolean/sequential network while avoiding
# expensive liberty optimization of enormous mux cones.
AIG_PARTITION_MODULES = frozenset({
    "shama_fs_accel",
    "shama_asm_accel",
})


def partition_mapping_mode(module_type: str) -> str:
    return "aig" if module_type in AIG_PARTITION_MODULES else "abc"


@dataclass(frozen=True)
class PartitionedLogic:
    shell: PhysicalNetlist
    partitions: tuple[tuple[str, PhysicalNetlist], ...]

    @property
    def input_ports(self) -> dict[str, tuple[Vec3, ...]]:
        return self.shell.input_ports

    @property
    def output_ports(self) -> dict[str, tuple[Vec3, ...]]:
        return self.shell.output_ports

    def manifest(self) -> dict[str, object]:
        part_manifests = {
            name: physical.manifest()
            for name, physical in self.partitions
        }
        shell_manifest = self.shell.manifest()
        return {
            "top": "shama_soc",
            "strategy": "partitioned-blackbox-shell",
            "cell_count": (
                shell_manifest["cell_count"]
                + sum(int(m["cell_count"]) for m in part_manifests.values())
            ),
            "net_count": (
                shell_manifest["net_count"]
                + sum(int(m["net_count"]) for m in part_manifests.values())
            ),
            "routing_tracks": (
                shell_manifest["routing_tracks"]
                + sum(int(m["routing_tracks"]) for m in part_manifests.values())
            ),
            "shell": shell_manifest,
            "partitions": part_manifests,
            "input_ports": shell_manifest["input_ports"],
            "output_ports": shell_manifest["output_ports"],
        }

    def iter_placements(self, *, component: str = "shama-soc") -> Iterator[Placement]:
        for instance_name, physical in self.partitions:
            yield from physical.iter_placements(
                component=f"{component}:partition:{instance_name}"
            )
        yield from self.shell.iter_placements(component=f"{component}:shell")


@dataclass(frozen=True)
class PreparedSystem:
    logic: PartitionedLogic
    nets: tuple[ExternalNet, ...]
    clock_origin: Vec3
    constant_high: Vec3
    metadata: dict[str, object]
    routing: RoutingInterfaces


def _geometry_from_config(config: dict) -> MachineGeometry:
    w = config["world"]
    m = config["memory"]
    d = config["display"]
    return MachineGeometry(
        origin=Vec3(w["origin_x"], w["origin_y"], w["origin_z"]),
        ram_bytes=m["logical_ram_bytes"],
        cache_bytes=m["cache_bytes"],
        flash_bytes=m["flash_bytes"],
        display_width=d["width"],
        display_height=d["height"],
        vram_bytes=m["vram_bytes"],
        bank_words=m.get("bank_words", 1024),
        bank_word_bits=m.get("bank_word_bits", 32),
        banks_per_row=m.get("banks_per_row", 16),
        **memory_placement_options(config),
        pixel_pitch_x=d.get("pixel_pitch_x", 5),
        pixel_pitch_y=d.get("pixel_pitch_y", 4),
        display_orientation=d.get("orientation", "horizontal"),
    )


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _require_port(
    ports: dict[str, tuple[Vec3, ...]],
    name: str,
    width: int,
) -> tuple[Vec3, ...]:
    try:
        result = ports[name]
    except KeyError as exc:
        raise RuntimeError(f"synthesized shama_soc is missing port {name!r}") from exc
    if len(result) != width:
        raise RuntimeError(
            f"synthesized shama_soc port {name!r} is {len(result)} bits; "
            f"expected {width}"
        )
    return result


def _vector_nets(
    prefix: str,
    sources: tuple[Vec3, ...],
    sinks: tuple[Vec3, ...],
) -> Iterator[ExternalNet]:
    if len(sources) != len(sinks):
        raise ValueError(
            f"{prefix}: source/sink width mismatch {len(sources)} != {len(sinks)}"
        )
    for bit, (source, sink) in enumerate(zip(sources, sinks)):
        yield ExternalNet(f"{prefix}[{bit}]", source, (sink,))


def memory_fabric_nets(
    name: str,
    origin: Vec3,
    fabric: MemoryFabricSpec,
    *,
    inputs: dict[str, tuple[Vec3, ...]],
    outputs: dict[str, tuple[Vec3, ...]],
) -> Iterator[ExternalNet]:
    """Join the unchanged controller ABI to every actual physical tile.

    Bank selectors remain one-to-one in increasing logical bank order. Rows,
    write data and global enables broadcast to all tiles. Isolated return
    trunks OR each tile's selected word into the controller read-data input.
    """
    tiles = fabric_tile_ports(origin, fabric)
    bank_pins = tuple(pin for tile in tiles for pin in tile.bank_select)
    if len(bank_pins) != fabric.bank_count:
        raise ValueError("physical tile selectors do not match literal bank capacity")
    yield from _vector_nets(f"{name}_bank_select", _require_port(outputs, f"{name}_bank_select", fabric.bank_count), bank_pins)
    row_field = ("row_address", max(1, (fabric.bank.words - 1).bit_length())) if getattr(fabric, "binary_rows", False) else ("row_select", fabric.bank.words)
    for field, width in (row_field, ("write_data", fabric.bank.word_bits)):
        for bit, source in enumerate(_require_port(outputs, f"{name}_{field}", width)):
            sinks = tuple(getattr(tile, field)[bit] for tile in tiles if bit < len(getattr(tile, field)))
            if sinks:
                yield ExternalNet(f"{name}_{field}[{bit}]", source, sinks)
    for port_name, tile_field in (("read_enable", "read_enable"), ("write_commit", "write_enable")):
        yield ExternalNet(f"{name}_{port_name}", _require_port(outputs, f"{name}_{port_name}", 1)[0],
                          tuple(getattr(tile, tile_field) for tile in tiles))
    for bit, central_sink in enumerate(_require_port(inputs, f"{name}_selected_word", fabric.bank.word_bits)):
        leaves = tuple(tile.selected_word[bit] for tile in tiles)
        yield ExternalNet(f"{name}_selected_word[{bit}]", central_sink, leaves, wired_or=True)


def _partition_origin(base: Vec3, index: int) -> Vec3:
    # Four-column physical grid. Each partition owns a generous independent
    # redstone-routing region, while remaining far inside the Java world border.
    col = index % 4
    row = index // 4
    return Vec3(
        base.x - 900_000 + col * 260_000,
        base.y,
        base.z - 900_000 - row * 260_000,
    )


def _prepare_partitioned_logic(
    *,
    root: Path,
    build: Path,
    base_origin: Vec3,
    yosys: str,
    minimum_build_y: int = -64,
) -> PartitionedLogic:
    rtl_files = [root / p for p in SOC_RTL_FILES]

    partition_list: list[tuple[str, PhysicalNetlist]] = []
    macro_bindings: dict[str, MacroInstance] = {}

    for index, (instance_name, module_type) in enumerate(PARTITION_SPECS):
        print(f"[partition-synth] START {instance_name}: {module_type}", flush=True)
        output = build / f"partition-{instance_name}-{module_type}.json"
        synthesize_json(
            rtl_files,
            top=module_type,
            output_json=output,
            yosys=yosys,
            repo_root=root,
            mapping_mode=partition_mapping_mode(module_type),
        )
        physical = build_physical_netlist(
            output,
            top=module_type,
            origin=_partition_origin(base_origin, index),
            minimum_build_y=minimum_build_y,
        )
        pm = physical.manifest()
        print(
            f"[partition-synth] DONE  {instance_name}: "
            f"{pm['cell_count']} cells, {pm['net_count']} nets",
            flush=True,
        )
        partition_list.append((instance_name, physical))
        macro_bindings[instance_name] = MacroInstance(
            instance_name=instance_name,
            module_type=module_type,
            physical=physical,
        )

    print("[partition-synth] START shell: shama_soc", flush=True)
    shell_output = build / "shama_soc-shell-mapped.json"
    synthesize_json(
        rtl_files,
        top="shama_soc",
        output_json=shell_output,
        yosys=yosys,
        repo_root=root,
        blackbox_modules=tuple(module for _instance, module in PARTITION_SPECS),
    )

    shell = build_physical_netlist(
        shell_output,
        top="shama_soc",
        origin=Vec3(
            base_origin.x + 250_000,
            base_origin.y,
            base_origin.z - 1_750_000,
        ),
        macro_instances=macro_bindings,
        routing_branch_y=300,
        routing_trunk_y=304,
        minimum_build_y=minimum_build_y,
    )
    sm = shell.manifest()
    print(
        f"[partition-synth] DONE  shell: "
        f"{sm['cell_count']} cells, {sm['net_count']} nets",
        flush=True,
    )

    return PartitionedLogic(
        shell=shell,
        partitions=tuple(partition_list),
    )


def prepare_system(
    config: dict,
    *,
    build_dir: str | Path = "build/physical",
    yosys: str = "yosys",
) -> PreparedSystem:
    root = _repo_root()
    g = _geometry_from_config(config)
    if g.display_orientation != "upright":
        raise ValueError("the complete computer requires the upright display bridge; horizontal panels are standalone fixtures")
    origins = default_origins(g)
    cache_spec, ram_spec, flash_spec = memory_specs(g)
    vram_fabric = vram_spec(g)

    build = Path(build_dir)
    build.mkdir(parents=True, exist_ok=True)

    logic = _prepare_partitioned_logic(
        root=root,
        build=build,
        base_origin=origins.control,
        yosys=yosys,
        minimum_build_y=g.minimum_build_y,
    )

    inputs = logic.input_ports
    outputs = logic.output_ports

    nets: list[ExternalNet] = []

    # ---------- Physical controls / clock ----------
    controls = control_ports(origins.input)
    clock_origin = system_clock_origin(origins.input)
    clock = clock_ports(clock_origin)

    power_switch = _require_port(inputs, "power_switch", 1)[0]
    reset_button = _require_port(inputs, "reset_button", 1)[0]
    clk = _require_port(inputs, "clk", 1)[0]

    nets.append(
        ExternalNet(
            "power",
            controls.power,
            (power_switch, clock.enable),
        )
    )
    nets.append(ExternalNet("reset", controls.reset, (reset_button,)))
    nets.append(ExternalNet("clock", clock.clock, (clk,)))
    nets.append(ExternalNet("clock_hold", _require_port(outputs,"clock_hold",1)[0],(clock.hold,)))
    nets.append(ExternalNet("pause",controls.pause,(_require_port(inputs,"pause_switch",1)[0],)))
    nets.append(ExternalNet("manual_step",controls.step,(_require_port(inputs,"step_button",1)[0],)))
    diagnostics=debug_ports(origins.input.offset(0,0,-224))
    for name,pins in diagnostics.items():
        nets.extend(_vector_nets(name,_require_port(outputs,name,len(pins)),pins))

    nets.extend(
        _vector_nets(
            "controller",
            controls.controller,
            _require_port(inputs, "controller", 10),
        )
    )
    nets.extend(
        _vector_nets(
            "kb_rows",
            controls.keyboard_rows,
            _require_port(inputs, "kb_rows", 8),
        )
    )
    nets.extend(
        _vector_nets(
            "kb_cols",
            controls.keyboard_cols,
            _require_port(inputs, "kb_cols", 8),
        )
    )

    # ---------- Physical cache/RAM/flash/VRAM ----------
    for name, origin, fabric in (
        ("cache", origins.cache, cache_spec),
        ("ram", origins.ram, ram_spec),
        ("flash", origins.flash, flash_spec),
        ("vram", origins.vram, vram_fabric),
    ):
        nets.extend(memory_fabric_nets(name, origin, fabric, inputs=inputs, outputs=outputs))

    # ---------- 320x180 physical lamp display ----------
    panel = LampPanelSpec(
        width=g.display_width,
        height=g.display_height,
        pixel_pitch_x=g.pixel_pitch_x,
        pixel_pitch_y=g.pixel_pitch_y,
        orientation=g.display_orientation,
    )
    display = panel_ports(origins.display, panel)

    nets.extend(
        _vector_nets(
            "display_row_data",
            _require_port(outputs, "display_row_data", g.display_width),
            display.data_in,
        )
    )
    nets.extend(
        _vector_nets(
            "display_row_select",
            _require_port(outputs, "display_row_select", g.display_height),
            display.row_select,
        )
    )

    constant_high = origins.input.offset(300, 1, 0)
    nets.append(
        ExternalNet(
            "display_ready",
            constant_high,
            (_require_port(inputs, "display_row_ready", 1)[0],),
        )
    )

    metadata = {
        "logic": logic.manifest(),
        "external_net_count": len(nets),
        "clock_origin": vars(clock_origin),
        "constant_high": vars(constant_high),
        "debug_panel": {name:[vars(pin) for pin in pins] for name,pins in diagnostics.items()},
        "verification_scope": "partition synthesis and port connectivity; full route timing and Minecraft runtime remain separate gates",
        "memory_backbones": {
            "cache": {
                "banks": cache_spec.bank_count,
                "rows": cache_spec.bank.words,
            },
            "ram": {
                "banks": ram_spec.bank_count,
                "rows": ram_spec.bank.words,
            },
            "flash": {
                "banks": flash_spec.bank_count,
                "rows": flash_spec.bank.words,
            },
            "vram": {
                "banks": vram_fabric.bank_count,
                "rows": vram_fabric.bank.words,
            },
        },
        "display": {
            "width": panel.width,
            "height": panel.height,
        },
    }

    return PreparedSystem(
        logic=logic,
        nets=tuple(nets),
        clock_origin=clock_origin,
        constant_high=constant_high,
        metadata=metadata,
        routing=system_routing_interfaces(g,inputs,outputs,clock_origin=clock_origin,constant_high=constant_high),
    )


def iter_system_infrastructure(
    config: dict,
    prepared: PreparedSystem,
) -> Iterator[Placement]:
    g = _geometry_from_config(config)
    origins = default_origins(g)
    cache_spec, ram_spec, flash_spec = memory_specs(g)
    vram_fabric = vram_spec(g)

    yield from iter_clock(prepared.clock_origin)
    yield from iter_debug_panel(origins.input.offset(0,0,-224))

    yield Placement(
        prepared.constant_high.offset(dy=-1),
        REDSTONE_BLOCK,
        "system-constant-high",
    )
    yield Placement(
        prepared.constant_high,
        DUST,
        "system-constant-high",
    )

    yield from iter_memory_backbone(
        origins.cache,
        fabric=cache_spec,
        component="cache-backbone",
    )
    yield from iter_memory_backbone(
        origins.ram,
        fabric=ram_spec,
        component="ram-backbone",
    )
    yield from iter_memory_backbone(
        origins.flash,
        fabric=flash_spec,
        component="flash-backbone",
    )
    yield from iter_memory_backbone(
        origins.vram,
        fabric=vram_fabric,
        component="vram-backbone",
    )


def iter_logic(prepared: PreparedSystem) -> Iterator[Placement]:
    yield from prepared.logic.iter_placements(component="shama-soc")


def iter_interconnect(prepared: PreparedSystem) -> Iterator[Placement]:
    yield from iter_external_router(
        prepared.nets,
        component="system-interconnect",
        access=prepared.routing.access,
        obstacles=prepared.routing.obstacles,
        endpoint_bounds=prepared.routing.endpoint_bounds,
        obstacle_bounds=prepared.routing.obstacle_bounds,
        minimum_y=prepared.routing.minimum_y,
    )
