from __future__ import annotations

from dataclasses import dataclass
import math

from .hardware.display import LampPanelSpec, panel_bounds
from .hardware.clock import CLOCK_BOUNDS
from .hardware.debug import DEBUG_BOUNDS
from .hardware.controls import CONTROL_SIZE
from .hardware.memory import MemoryBankSpec, MemoryFabricSpec, bank_ports
from .hardware.memory_backbone import StackedMemoryFabricSpec
from .machine_spec import VRAM_BYTES
from .model import Box, BuildPlan, ComponentPlan, Vec3


@dataclass(frozen=True)
class MachineGeometry:
    origin: Vec3
    ram_bytes: int
    cache_bytes: int
    flash_bytes: int
    display_width: int
    display_height: int
    vram_bytes: int = VRAM_BYTES
    bank_words: int = 1024
    bank_word_bits: int = 32
    banks_per_row: int = 16
    memory_layers: int | None = None
    memory_layer_pitch: int = 12
    memory_base_y: int | None = None
    surface_y: int = -61
    memory_control_ceiling: int = 307
    pixel_pitch_x: int = 5
    pixel_pitch_y: int = 5

    def __post_init__(self) -> None:
        if not -64 <= self.surface_y <= 314:
            raise ValueError("surface_y leaves no valid above-ground Java build area")
        if self.origin.y - 3 < self.minimum_build_y:
            raise ValueError("component origin would place display supports below the grass surface")
        if self.origin.y + DEBUG_BOUNDS[1][1] > 319:
            raise ValueError("component origin would place the debug wall above Java build height")
        if self.memory_layer_pitch < 12 or self.memory_layer_pitch % 4:
            raise ValueError("memory layer pitch must be at least 12 and divisible by 4")
        if self.storage_origin_y - 4 < self.minimum_build_y:
            raise ValueError("storage base would place supports below the grass surface")
        if self.memory_control_ceiling > 307:
            raise ValueError("memory controls must leave the global routing canopy above Y307")
        if self.maximum_memory_layers < 1:
            raise ValueError("grass surface leaves no memory layer below the routing canopy")
        if self.memory_layers is not None and not 1 <= self.memory_layers <= self.maximum_memory_layers:
            raise ValueError(f"requested memory layers exceed the above-ground routing limit of {self.maximum_memory_layers}")

    @property
    def minimum_build_y(self) -> int:
        return self.surface_y + 1

    @property
    def storage_origin_y(self) -> int:
        return self.surface_y + 5 if self.memory_base_y is None else self.memory_base_y

    @property
    def maximum_memory_layers(self) -> int:
        return min(32, (self.memory_control_ceiling - self.storage_origin_y - 7) // self.memory_layer_pitch + 1)

    @property
    def resolved_memory_layers(self) -> int:
        return self.maximum_memory_layers if self.memory_layers is None else self.memory_layers


def memory_placement_options(config: dict) -> dict[str, int | None]:
    """Use the detected/declared grass surface as the placement authority."""
    memory = config["memory"]

    def optional_int(key: str) -> int | None:
        value = memory.get(key, "auto")
        if value == "auto" or value is None:
            return None
        if type(value) is not int:
            raise ValueError(f"memory.{key} must be an integer or 'auto'")
        return value

    return {"surface_y": config["world"].get("surface_y", -61),
            "memory_layers": optional_int("stack_layers"),
            "memory_layer_pitch": memory.get("stack_layer_pitch", 12),
            "memory_base_y": optional_int("storage_base_y"),
            "memory_control_ceiling": memory.get("control_ceiling_y", 307)}

@dataclass(frozen=True)
class PhysicalOrigins:
    cpu: Vec3
    gpu: Vec3
    control: Vec3
    cache: Vec3
    ram: Vec3
    flash: Vec3
    vram: Vec3
    input: Vec3
    display: Vec3


@dataclass(frozen=True)
class StackedBankPacking:
    """Compare layer counts while preserving every complete physical bank."""

    fabric: MemoryFabricSpec
    layers: int = 16
    layer_pitch: int = 16

    def __post_init__(self) -> None:
        _ = self.stacked

    @property
    def stacked(self) -> StackedMemoryFabricSpec:
        return StackedMemoryFabricSpec(self.fabric.total_bytes, self.fabric.bank,
                                      self.fabric.banks_per_row, self.fabric.bank_gap_x,
                                      self.fabric.bank_gap_z, self.layers, self.layer_pitch)

    @property
    def groups(self) -> int:
        return self.stacked.group_count

    def bank_origin(self, origin: Vec3, bank_index: int) -> Vec3:
        return self.stacked.bank_origin(origin, bank_index)

    def bounds(self, origin: Vec3, *, local_controls: bool = False) -> Box:
        return self.stacked.bounds(origin, local_controls=local_controls)


def default_origins(g: MachineGeometry) -> PhysicalOrigins:
    ox, oy, oz = g.origin.x, g.origin.y, g.origin.z
    cache, ram, flash = memory_specs(g)
    memory_origins: dict[str, Vec3] = {}
    # Adjacent fabrics share a compact horizontal district. Keep a real 512
    # block routing corridor between exact local-control/return envelopes.
    left = ox + 2_000
    for name, fabric in (("cache", cache), ("vram", vram_spec(g)), ("ram", ram), ("flash", flash)):
        base = Vec3(left + 125, g.storage_origin_y, oz)
        box = fabric.bounds(base)
        memory_origins[name] = base
        left = box.max.x + 1 + 512
    return PhysicalOrigins(
        cpu=Vec3(ox, oy, oz - 600_000),
        gpu=Vec3(ox, oy, oz - 500_000),
        control=Vec3(ox, oy, oz - 400_000),
        cache=memory_origins["cache"],
        ram=memory_origins["ram"],
        flash=memory_origins["flash"],
        vram=memory_origins["vram"],
        input=Vec3(ox + 900, oy, oz - 3_000),
        display=Vec3(ox, oy, oz - 2_000),
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


def _stacked_fabric(g: MachineGeometry, total_bytes: int) -> StackedMemoryFabricSpec:
    bank = MemoryBankSpec(words=g.bank_words, word_bits=g.bank_word_bits)
    banks = total_bytes // bank.bytes
    layers = min(g.resolved_memory_layers, banks)
    return StackedMemoryFabricSpec(total_bytes=total_bytes, bank=bank,
                                  banks_per_row=min(g.banks_per_row, max(1, math.ceil(banks / max(1, layers)))),
                                  layers=layers, layer_pitch=g.memory_layer_pitch)


def memory_specs(g: MachineGeometry) -> tuple[StackedMemoryFabricSpec, StackedMemoryFabricSpec, StackedMemoryFabricSpec]:
    return tuple(_stacked_fabric(g, size) for size in (g.cache_bytes, g.ram_bytes, g.flash_bytes))


def vram_spec(g: MachineGeometry) -> StackedMemoryFabricSpec:
    return _stacked_fabric(g, g.vram_bytes)


def plan_machine(g: MachineGeometry, *, origins: PhysicalOrigins | None = None) -> BuildPlan:
    """Coarse floorplan using the real physical memory/display dimensions."""
    origins = default_origins(g) if origins is None else origins
    cache_spec, ram_spec, flash_spec = memory_specs(g)
    vram_fabric = vram_spec(g)

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
        cache_spec.bounds(origins.cache),
        "physical-memory",
        metadata={"bytes": g.cache_bytes, "banks": cache_spec.bank_count, "layers": cache_spec.layers, "tiles": cache_spec.group_count},
    ))
    plan.add(ComponentPlan(
        "ram",
        ram_spec.bounds(origins.ram),
        "physical-memory",
        metadata={"bytes": g.ram_bytes, "banks": ram_spec.bank_count, "layers": ram_spec.layers, "tiles": ram_spec.group_count},
    ))
    plan.add(ComponentPlan(
        "flash",
        flash_spec.bounds(origins.flash),
        "physical-memory",
        metadata={"bytes": g.flash_bytes, "banks": flash_spec.bank_count, "layers": flash_spec.layers, "tiles": flash_spec.group_count, "persistent": True},
    ))
    plan.add(ComponentPlan(
        "vram",
        vram_fabric.bounds(origins.vram),
        "physical-memory",
        metadata={"bytes": g.vram_bytes, "banks": vram_fabric.bank_count, "layers": vram_fabric.layers, "tiles": vram_fabric.group_count, "gpu": True},
    ))

    plan.add(ComponentPlan(
        "input",
        _box_from_size(origins.input, *CONTROL_SIZE),
        "physical-input",
        metadata={"keyboard": "8x8 with Shift", "controller": True, "power": True,
                  "pause": True, "manual_step": True},
    ))

    plan.add(ComponentPlan(
        "display",
        panel_bounds(origins.display, panel),
        "physical-display",
        metadata={
            "width": panel.width,
            "height": panel.height,
            "bits_per_pixel": 1,
            "pixel_pitch_x": panel.pixel_pitch_x,
            "pixel_pitch_y": panel.pixel_pitch_y,
            "orientation": "horizontal-xz-viewed-from-above",
        },
    ))

    for name, origin, bounds in (
        ("debug-panel", origins.input.offset(0, 0, -224), DEBUG_BOUNDS),
        ("clock", origins.input.offset(180, 0, 0), CLOCK_BOUNDS),
    ):
        plan.add(ComponentPlan(name, Box(origin.offset(*bounds[0]), origin.offset(*bounds[1])),
                               "physical-diagnostics" if name == "debug-panel" else "physical-clock"))

    return plan
