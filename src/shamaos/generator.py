from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import tomllib
from typing import Callable, Iterator

from .hardware.controls import iter_controls
from .hardware.display import LampPanelSpec, iter_lamp_panel
from .hardware.memory import MemoryBankSpec, MemoryFabricSpec, iter_memory_bank
from .layout import (
    MachineGeometry,
    default_origins,
    memory_specs,
    plan_machine,
)
from .model import BuildPlan, Placement, Vec3
from .os_image import build_default_os_image
from .worldio import AmuletWorldWriter


@dataclass(frozen=True)
class PhysicalSection:
    name: str
    factory: Callable[[], Iterator[Placement]]
    metadata: dict[str, object]


@dataclass(frozen=True)
class GenerationResult:
    written_blocks: int
    system_metadata: dict[str, object]
    stage_counts: dict[str, int]


def load_config(path: str | Path) -> dict:
    return tomllib.loads(Path(path).read_text(encoding="utf-8"))


def geometry_from_config(config: dict) -> MachineGeometry:
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
        pixel_pitch_x=d.get("pixel_pitch_x", 4),
        pixel_pitch_y=d.get("pixel_pitch_y", 2),
    )


def build_plan(config: dict) -> BuildPlan:
    return plan_machine(geometry_from_config(config))


def _bank_origin(
    fabric_origin: Vec3,
    fabric: MemoryFabricSpec,
    bank_index: int,
) -> Vec3:
    grid_x = bank_index % fabric.banks_per_row
    grid_z = bank_index // fabric.banks_per_row
    return Vec3(
        fabric_origin.x + grid_x * (fabric.bank.width + fabric.bank_gap_x),
        fabric_origin.y,
        fabric_origin.z + grid_z * (fabric.bank.depth + fabric.bank_gap_z),
    )


def _memory_sections(
    *,
    name: str,
    origin: Vec3,
    fabric: MemoryFabricSpec,
    initial: bytes | None,
) -> Iterator[PhysicalSection]:
    for bank_index in range(fabric.bank_count):
        bank_origin = _bank_origin(origin, fabric, bank_index)
        start = bank_index * fabric.bank.bytes
        bank_data = (
            None
            if initial is None
            else initial[start : start + fabric.bank.bytes]
        )

        def factory(
            bank_origin: Vec3 = bank_origin,
            bank_data: bytes | None = bank_data,
            bank_index: int = bank_index,
        ) -> Iterator[Placement]:
            yield from iter_memory_bank(
                bank_origin,
                spec=fabric.bank,
                initial=bank_data,
                component=f"{name}-bank-{bank_index:03d}",
            )

        yield PhysicalSection(
            f"{name}-bank-{bank_index:03d}",
            factory,
            {
                "kind": "memory-bank",
                "bank_index": bank_index,
                "bytes": fabric.bank.bytes,
                "origin": vars(bank_origin),
                "preloaded": bank_data is not None,
            },
        )


def physical_sections(config: dict) -> Iterator[PhysicalSection]:
    """Yield all custom physical fabrics in deterministic write order.

    Synthesized CPU/GPU/control logic is added by the synthesis integration
    layer; these sections are the huge regular structures that are much more
    compact and reliable when generated directly rather than exploded through
    gate-level synthesis.
    """
    g = geometry_from_config(config)
    origins = default_origins(g)
    cache_spec, ram_spec, flash_spec = memory_specs(g)
    from .layout import vram_spec
    vram_fabric = vram_spec(g)
    flags = config.get("generator", {})
    os_image = (
        build_default_os_image(g.flash_bytes)
        if flags.get("physical_cache", True) or flags.get("physical_flash", True)
        else None
    )

    if flags.get("physical_display", True):
        panel = LampPanelSpec(
            width=g.display_width,
            height=g.display_height,
            pixel_pitch_x=g.pixel_pitch_x,
            pixel_pitch_y=g.pixel_pitch_y,
        )

        def display_factory() -> Iterator[Placement]:
            yield from iter_lamp_panel(origins.display, spec=panel)

        yield PhysicalSection(
            "display",
            display_factory,
            {
                "kind": "latched-lamp-panel",
                "origin": vars(origins.display),
                "width": panel.width,
                "height": panel.height,
                "physical_width": panel.physical_width,
                "physical_height": panel.physical_height,
            },
        )

    if flags.get("physical_input", True):
        def input_factory() -> Iterator[Placement]:
            yield from iter_controls(origins.input)

        yield PhysicalSection(
            "input",
            input_factory,
            {
                "kind": "keyboard-controller",
                "origin": vars(origins.input),
                "keyboard": "8x8",
                "controller_buttons": 10,
            },
        )

    if flags.get("physical_cache", True):
        assert os_image is not None
        cache_initial = os_image.boot_binary.ljust(g.cache_bytes, b"\x00")
        if len(cache_initial) > g.cache_bytes:
            raise ValueError("boot firmware exceeds 16 KiB cache/scratch capacity")
        yield from _memory_sections(
            name="cache",
            origin=origins.cache,
            fabric=cache_spec,
            initial=cache_initial,
        )

    if flags.get("physical_ram", True):
        yield from _memory_sections(
            name="ram",
            origin=origins.ram,
            fabric=ram_spec,
            initial=None,
        )

    # Dedicated 32 KiB GPU VRAM is always a real physical memory fabric.
    yield from _memory_sections(
        name="vram",
        origin=origins.vram,
        fabric=vram_fabric,
        initial=None,
    )

    if flags.get("physical_flash", True):
        assert os_image is not None
        yield from _memory_sections(
            name="flash",
            origin=origins.flash,
            fabric=flash_spec,
            initial=os_image.image,
        )


def manifest(config: dict, plan: BuildPlan) -> dict:
    normalized = json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
    g = geometry_from_config(config)
    cache_spec, ram_spec, flash_spec = memory_specs(g)
    from .layout import vram_spec
    vram_fabric = vram_spec(g)
    result = plan.manifest()
    result.update({
        "config_sha256": hashlib.sha256(normalized).hexdigest(),
        "minecraft_version": config["world"]["minecraft_version"],
        "data_version": config["world"]["data_version"],
        "logical_ram_bytes": g.ram_bytes,
        "physical_ram_bytes": g.ram_bytes
            if config["generator"].get("physical_ram", True) else 0,
        "cache_bytes": g.cache_bytes,
        "physical_cache_bytes": g.cache_bytes
            if config["generator"].get("physical_cache", True) else 0,
        "flash_bytes": g.flash_bytes,
        "physical_flash_bytes": g.flash_bytes
            if config["generator"].get("physical_flash", True) else 0,
        "vram_bytes": config["memory"]["vram_bytes"],
        "memory_banks": {
            "cache": cache_spec.bank_count,
            "ram": ram_spec.bank_count,
            "flash": flash_spec.bank_count,
            "vram": vram_fabric.bank_count,
            "bytes_per_bank": ram_spec.bank.bytes,
        },
        "display": {
            "width": g.display_width,
            "height": g.display_height,
            "bits_per_pixel": config["display"]["bits_per_pixel"],
            "pixel_pitch_x": g.pixel_pitch_x,
            "pixel_pitch_y": g.pixel_pitch_y,
        },
        "physical_generation_status": "fabrics+rtl-synthesis",
        "boot": {
            "cache_boot_bytes": len(build_default_os_image(g.flash_bytes).boot_binary),
            "bundle_flash_offset": build_default_os_image(g.flash_bytes).bundle_flash_offset,
            "bundle_bytes": build_default_os_image(g.flash_bytes).bundle_bytes,
            "app_pc_words": build_default_os_image(g.flash_bytes).app_pc_words,
        },
        "physical_sections": [
            section.metadata | {"name": section.name}
            for section in physical_sections(config)
        ],
    })
    return result


def write_manifest(path: str | Path, data: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_available_placements(
    world_path: str | Path,
    config: dict,
    plan: BuildPlan,
) -> GenerationResult:
    """Generate and write the complete physical ShamaOS topology.

    Large regular storage/display/input fabrics are emitted directly. The
    stateful CPU/SHA/GPU/kernel/filesystem/assembler/control logic is synthesized
    from RTL into real redstone cells. A crossing-safe router then joins the
    named SoC ports to every physical fabric.
    """
    from .system_build import (
        iter_interconnect,
        iter_logic,
        iter_system_infrastructure,
        prepare_system,
    )

    save_every = max(
        1,
        int(config.get("generator", {}).get("save_every_blocks", 250_000)),
    )
    build_dir = Path(
        config.get("generator", {}).get("build_dir", "build/physical")
    )
    prepared = prepare_system(config, build_dir=build_dir)

    count = 0
    stage_counts: dict[str, int] = {}

    def emit(
        writer: AmuletWorldWriter,
        name: str,
        placements: Iterator[Placement],
    ) -> None:
        nonlocal count
        stage = 0
        for placement in placements:
            writer.set_block(placement)
            count += 1
            stage += 1
            if count % save_every == 0:
                writer.save()
        stage_counts[name] = stage
        writer.save()

    with AmuletWorldWriter(
        world_path,
        dimension=config["world"]["dimension"],
        version=(
            "java",
            tuple(
                int(x)
                for x in config["world"]["minecraft_version"].split(".")
            ),
        ),
    ) as writer:
        # Huge regular physical fabrics.
        for section in physical_sections(config):
            emit(writer, section.name, section.factory())

        # Clock, constants and cache/RAM/4 MiB flash backbones.
        emit(
            writer,
            "system-infrastructure",
            iter_system_infrastructure(config, prepared),
        )

        # CPU + native SHA + GPU + kernel + ShamaFS + in-world assembler.
        emit(writer, "synthesized-soc", iter_logic(prepared))

        # Named long-distance port wiring is last so it lands on every existing
        # terminal and can safely overlay terminal dust/support blocks.
        emit(writer, "system-interconnect", iter_interconnect(prepared))

    metadata = dict(prepared.metadata)
    metadata["stage_counts"] = dict(stage_counts)
    metadata["written_blocks"] = count

    return GenerationResult(
        written_blocks=count,
        system_metadata=metadata,
        stage_counts=stage_counts,
    )
