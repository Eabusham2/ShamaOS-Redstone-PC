from __future__ import annotations

from dataclasses import dataclass
import math

from .hardware.display import LampPanelSpec
from .hardware.memory import MemoryBankSpec, MemoryFabricSpec
from .model import Box, BuildPlan, ComponentPlan, Vec3


@dataclass(frozen=True)
class MachineGeometry:
    origin: Vec3
    ram_bytes: int
    cache_bytes: int
    flash_bytes: int
    display_width: int
    display_height: int
    bank_words: int = 1024
    bank_word_bits: int = 32
    banks_per_row: int = 16
    pixel_pitch_x: int = 4
    pixel_pitch_y: int = 2


@dataclass(frozen=True)
class PhysicalOrigins:
    cpu: Vec3
    gpu: Vec3
    control: Vec3
    cache: Vec3
    ram: Vec3
    flash: Vec3
    input: Vec3
    display: Vec3


def default_origins(g: MachineGeometry) -> PhysicalOrigins:
    ox, oy, oz = g.origin.x, g.origin.y, g.origin.z
    return PhysicalOrigins(
        cpu=Vec3(ox, oy, oz - 600_000),
        gpu=Vec3(ox, oy, oz - 500_000),
        control=Vec3(ox, oy, oz - 400_000),
        cache=Vec3(ox + 10_000, oy, oz),
        ram=Vec3(ox + 12_000, oy, oz),
        flash=Vec3(ox + 12_000, oy, oz + 110_000),
        input=Vec3(ox + 900, oy, oz - 2_000),
        display=Vec3(ox, -41, oz - 2_000),
    )


def _box_from_size(origin: Vec3, sx: int, sy: int, sz: int) -> Box:
    return Box(
        origin,
        Vec3(origin.x + sx - 1, origin.y + sy - 1, origin.z + sz - 1),
    )


def _fabric_dimensions(fabric: MemoryFabricSpec) -> tuple[int, int, int]:
    count = fabric.bank_count
    columns = min(fabric.banks_per_row, count)
    rows = math.ceil(count / fabric.banks_per_row)
    width = columns * fabric.bank.width + max(0, columns - 1) * fabric.bank_gap_x
    depth = rows * fabric.bank.depth + max(0, rows - 1) * fabric.bank_gap_z
    return width, fabric.bank.height, depth


def memory_specs(g: MachineGeometry) -> tuple[MemoryFabricSpec, MemoryFabricSpec, MemoryFabricSpec]:
    bank = MemoryBankSpec(words=g.bank_words, word_bits=g.bank_word_bits)
    cache = MemoryFabricSpec(
        total_bytes=g.cache_bytes,
        bank=bank,
        banks_per_row=min(g.banks_per_row, max(1, g.cache_bytes // bank.bytes)),
    )
    ram = MemoryFabricSpec(
        total_bytes=g.ram_bytes,
        bank=bank,
        banks_per_row=g.banks_per_row,
    )
    flash = MemoryFabricSpec(
        total_bytes=g.flash_bytes,
        bank=bank,
        banks_per_row=g.banks_per_row,
    )
    return cache, ram, flash


def plan_machine(g: MachineGeometry) -> BuildPlan:
    """Coarse floorplan using the real physical memory/display dimensions."""
    origins = default_origins(g)
    cache_spec, ram_spec, flash_spec = memory_specs(g)
    cache_size = _fabric_dimensions(cache_spec)
    ram_size = _fabric_dimensions(ram_spec)
    flash_size = _fabric_dimensions(flash_spec)

    panel = LampPanelSpec(
        width=g.display_width,
        height=g.display_height,
        pixel_pitch_x=g.pixel_pitch_x,
        pixel_pitch_y=g.pixel_pitch_y,
    )

    plan = BuildPlan()

    # Logic zones intentionally have generous routing reservations. Exact
    # synthesized bounds are recorded in the generated logic manifests.
    plan.add(ComponentPlan(
        "cpu",
        _box_from_size(origins.cpu, 2_000_000, 16, 50_000),
        "synthesized-logic",
        metadata={"word_bits": 32, "native_sha": True},
    ))
    plan.add(ComponentPlan(
        "gpu",
        _box_from_size(origins.gpu, 2_000_000, 16, 50_000),
        "synthesized-logic",
        metadata={"command_processor": True},
    ))
    plan.add(ComponentPlan(
        "control",
        _box_from_size(origins.control, 1_000_000, 16, 50_000),
        "synthesized-logic",
        metadata={"input": True, "memory_controller": True, "display_bridge": True},
    ))

    plan.add(ComponentPlan(
        "cache",
        _box_from_size(origins.cache, *cache_size),
        "physical-memory",
        metadata={"bytes": g.cache_bytes, "banks": cache_spec.bank_count},
    ))
    plan.add(ComponentPlan(
        "ram",
        _box_from_size(origins.ram, *ram_size),
        "physical-memory",
        metadata={"bytes": g.ram_bytes, "banks": ram_spec.bank_count},
    ))
    plan.add(ComponentPlan(
        "flash",
        _box_from_size(origins.flash, *flash_size),
        "physical-memory",
        metadata={"bytes": g.flash_bytes, "banks": flash_spec.bank_count, "persistent": True},
    ))

    plan.add(ComponentPlan(
        "input",
        _box_from_size(origins.input, 64, 8, 64),
        "physical-input",
        metadata={"keyboard": "8x8", "controller": True, "power": True},
    ))

    plan.add(ComponentPlan(
        "display",
        _box_from_size(
            origins.display,
            panel.physical_width,
            panel.physical_height,
            panel.depth,
        ),
        "physical-display",
        metadata={
            "width": panel.width,
            "height": panel.height,
            "bits_per_pixel": 1,
            "pixel_pitch_x": panel.pixel_pitch_x,
            "pixel_pitch_y": panel.pixel_pitch_y,
        },
    ))

    return plan
