from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .generator import geometry_from_config
from .hardware.clock import clock_ports, iter_clock
from .hardware.controls import control_ports
from .hardware.display import LampPanelSpec, panel_ports
from .hardware.external_router import ExternalNet, iter_external_router
from .hardware.logic import REDSTONE_BLOCK
from .hardware.memory_backbone import backbone_ports, iter_memory_backbone
from .layout import default_origins, memory_specs
from .model import BlockState, Placement, Vec3
from .synthesis import PhysicalNetlist, build_physical_netlist, synthesize_json


@dataclass(frozen=True)
class PreparedSystem:
    logic: PhysicalNetlist
    nets: tuple[ExternalNet, ...]
    clock_origin: Vec3
    constant_high: Vec3
    metadata: dict[str, object]


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


def prepare_system(
    config: dict,
    *,
    build_dir: str | Path = "build/physical",
    yosys: str = "yosys",
) -> PreparedSystem:
    root = _repo_root()
    g = geometry_from_config(config)
    origins = default_origins(g)
    cache_spec, ram_spec, flash_spec = memory_specs(g)

    build = Path(build_dir)
    build.mkdir(parents=True, exist_ok=True)
    netlist_path = build / "shama_soc-mapped.json"

    synthesize_json(
        [root / p for p in SOC_RTL_FILES],
        top="shama_soc",
        output_json=netlist_path,
        yosys=yosys,
        repo_root=root,
    )
    logic = build_physical_netlist(
        netlist_path,
        top="shama_soc",
        origin=origins.cpu,
    )

    inputs = logic.input_ports
    outputs = logic.output_ports

    nets: list[ExternalNet] = []

    # ---------- Physical controls / clock ----------
    controls = control_ports(origins.input)
    clock_origin = origins.input.offset(96, 0, 0)
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

    controller = _require_port(inputs, "controller", 10)
    nets.extend(
        _vector_nets(
            "controller",
            controls.controller,
            controller,
        )
    )

    kb_rows = _require_port(inputs, "kb_rows", 8)
    kb_cols = _require_port(inputs, "kb_cols", 8)
    nets.extend(_vector_nets("kb_rows", controls.keyboard_rows, kb_rows))
    nets.extend(_vector_nets("kb_cols", controls.keyboard_cols, kb_cols))

    # ---------- Physical cache/RAM/flash ----------
    for name, origin, fabric in (
        ("cache", origins.cache, cache_spec),
        ("ram", origins.ram, ram_spec),
        ("flash", origins.flash, flash_spec),
    ):
        physical = backbone_ports(origin, fabric)

        nets.extend(
            _vector_nets(
                f"{name}_bank_select",
                _require_port(outputs, f"{name}_bank_select", len(physical.bank_select)),
                physical.bank_select,
            )
        )
        nets.extend(
            _vector_nets(
                f"{name}_row_select",
                _require_port(outputs, f"{name}_row_select", len(physical.row_select)),
                physical.row_select,
            )
        )
        nets.append(
            ExternalNet(
                f"{name}_read_enable",
                _require_port(outputs, f"{name}_read_enable", 1)[0],
                (physical.read_enable,),
            )
        )
        nets.append(
            ExternalNet(
                f"{name}_write_commit",
                _require_port(outputs, f"{name}_write_commit", 1)[0],
                (physical.write_enable,),
            )
        )
        nets.extend(
            _vector_nets(
                f"{name}_write_data",
                _require_port(outputs, f"{name}_write_data", 32),
                physical.write_data,
            )
        )
        nets.extend(
            _vector_nets(
                f"{name}_selected_word",
                physical.selected_word,
                _require_port(inputs, f"{name}_selected_word", 32),
            )
        )

    # ---------- 320x180 physical lamp display ----------
    panel = LampPanelSpec(
        width=g.display_width,
        height=g.display_height,
        pixel_pitch_x=g.pixel_pitch_x,
        pixel_pitch_y=g.pixel_pitch_y,
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

    # The physical panel is a latch matrix and accepts a row whenever the SoC
    # asserts a one-hot row pulse. It does not need back-pressure, so tie the
    # bridge ready input high with a real redstone-block source.
    constant_high = origins.input.offset(160, 1, 0)
    display_ready = _require_port(inputs, "display_row_ready", 1)[0]
    nets.append(ExternalNet("display_ready", constant_high, (display_ready,)))

    metadata = {
        "logic": logic.manifest(),
        "external_net_count": len(nets),
        "clock_origin": vars(clock_origin),
        "constant_high": vars(constant_high),
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
    )


def iter_system_infrastructure(
    config: dict,
    prepared: PreparedSystem,
) -> Iterator[Placement]:
    g = geometry_from_config(config)
    origins = default_origins(g)
    cache_spec, ram_spec, flash_spec = memory_specs(g)

    # Power-gated comparator clock.
    yield from iter_clock(prepared.clock_origin)

    # A physical always-high source for ready/constant inputs.
    yield Placement(
        prepared.constant_high.offset(dy=-1),
        REDSTONE_BLOCK,
        "system-constant-high",
    )
    yield Placement(
        prepared.constant_high,
        BlockState.of("minecraft:redstone_wire"),
        "system-constant-high",
    )

    # Full physical selector/data backbones for all storage fabrics.
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


def iter_logic(prepared: PreparedSystem) -> Iterator[Placement]:
    yield from prepared.logic.iter_placements(component="shama-soc")


def iter_interconnect(prepared: PreparedSystem) -> Iterator[Placement]:
    yield from iter_external_router(
        prepared.nets,
        component="system-interconnect",
    )
