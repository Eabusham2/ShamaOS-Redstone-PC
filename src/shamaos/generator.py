from __future__ import annotations

from dataclasses import dataclass, field, replace
from collections import Counter
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import tomllib
from typing import Callable, Iterable, Iterator

from .hardware.controls import iter_controls
from .hardware.display import LampPanelSpec, iter_lamp_panel
from .hardware.memory import MemoryBankSpec, MemoryFabricSpec, bank_ports, iter_memory_bank
from .hardware.memory_backbone import StackedMemoryFabricSpec, iter_stacked_memory_tile
from .hardware.memory_streaming import iter_memory_bank_ordered
from .layout import (
    MachineGeometry,
    StackedBankPacking,
    default_origins,
    memory_placement_options,
    memory_specs,
    plan_machine,
)
from .model import BlockState, BuildPlan, Placement, Vec3
from .machine_spec import RAM_BYTES, FLASH_BYTES, CACHE_BYTES, VRAM_BYTES
from .os_image import build_default_os_image
from .worldio import NativeWorldReader, NativeWorldWriter, WorldWriteError, resolve_world_surface


REQUIRED_CAPACITY = {
    "logical_ram_bytes": RAM_BYTES, "physical_ram_bytes": RAM_BYTES,
    "cache_bytes": CACHE_BYTES, "physical_cache_bytes": CACHE_BYTES,
    "flash_bytes": FLASH_BYTES, "physical_flash_bytes": FLASH_BYTES,
    "vram_bytes": VRAM_BYTES,
}


def source_provenance() -> dict[str, object]:
    """Record exact generation sources, including an uncommitted work tree."""
    root = Path(__file__).resolve().parents[2]
    files = ["src/shamaos/generator.py", "src/shamaos/worldio.py", "src/shamaos/layout.py",
             "src/shamaos/hardware/logic.py", "src/shamaos/hardware/memory.py",
             "src/shamaos/hardware/memory_backbone.py", "src/shamaos/hardware/memory_streaming.py",
             "src/shamaos/hardware/external_router.py", "src/shamaos/system_build.py",
             "src/shamaos/memory_fixture.py", "src/shamaos/physical_resources.py",
             "src/shamaos/resource_accounting.py", "src/shamaos/machine_spec.py", "configs/default.toml"]
    result: dict[str, object] = {
        "files_sha256": {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in files if (root / name).is_file()},
    }
    try:
        result["base_revision"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, stderr=subprocess.DEVNULL, text=True).strip()
        result["working_tree_dirty"] = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=root, stderr=subprocess.DEVNULL, text=True))
    except (OSError, subprocess.SubprocessError):
        result["base_revision"] = None
    return result


def validate_manifest_capacity(data: dict) -> dict[str, object]:
    """Check declared capacity against the literal contract, never a rescaled config.

    This verifies manifest claims and arithmetic. It is not evidence that a
    complete machine was generated or that its redstone works at runtime.
    """
    errors = []
    for key, expected in REQUIRED_CAPACITY.items():
        if type(data.get(key)) is not int or data[key] != expected:
            errors.append(f"{key}: expected {expected}, got {data.get(key)!r}")
    display = data.get("display", {})
    for key, expected in (("width", 320), ("height", 180), ("bits_per_pixel", 1)):
        if display.get(key) != expected:
            errors.append(f"display.{key}: expected {expected}, got {display.get(key)!r}")
    banks = data.get("memory_banks", {})
    bank_bytes = banks.get("bytes_per_bank")
    for name, key in (("cache", "cache_bytes"), ("ram", "logical_ram_bytes"), ("flash", "flash_bytes"), ("vram", "vram_bytes")):
        count = banks.get(name)
        if type(count) is not int or type(bank_bytes) is not int or count <= 0 or bank_bytes <= 0 or count * bank_bytes != REQUIRED_CAPACITY[key]:
            errors.append(f"memory_banks.{name}: count × bytes_per_bank must equal {REQUIRED_CAPACITY[key]}")
    return {
        "status": "passed" if not errors else "failed", "scope": "declared-literal-capacity-contract",
        "errors": errors, "complete_world_runtime_verified": False,
    }


def _capacity_claims(config: dict) -> dict[str, object]:
    g = geometry_from_config(config)
    flags = config.get("generator", {})
    return {
        "logical_ram_bytes": g.ram_bytes,
        "physical_ram_bytes": g.ram_bytes if flags.get("physical_ram", True) else 0,
        "cache_bytes": g.cache_bytes,
        "physical_cache_bytes": g.cache_bytes if flags.get("physical_cache", True) else 0,
        "flash_bytes": g.flash_bytes,
        "physical_flash_bytes": g.flash_bytes if flags.get("physical_flash", True) else 0,
        "vram_bytes": g.vram_bytes,
        "display": {"width": g.display_width, "height": g.display_height, "bits_per_pixel": config["display"]["bits_per_pixel"]},
        "memory_banks": {"bytes_per_bank": g.bank_words * g.bank_word_bits // 8, **{
            name: size // (g.bank_words * g.bank_word_bits // 8)
            for name, size in (("cache", g.cache_bytes), ("ram", g.ram_bytes), ("flash", g.flash_bytes), ("vram", g.vram_bytes))
        }},
    }


@lru_cache(maxsize=8)
def memory_bank_profile(spec: MemoryBankSpec = MemoryBankSpec()) -> dict[str, object]:
    """Measure one real bank emitter without allocating the complete machine."""
    chunks: set[tuple[int, int]] = set()
    blocks: Counter[str] = Counter()
    minimum = [10**9] * 3
    maximum = [-10**9] * 3
    for placement in iter_memory_bank(Vec3(0, 64, 0), spec=spec):
        chunks.add((placement.pos.x // 16, placement.pos.z // 16))
        blocks[placement.block.name] += 1
        for index, value in enumerate((placement.pos.x, placement.pos.y - 64, placement.pos.z)):
            minimum[index] = min(minimum[index], value)
            maximum[index] = max(maximum[index], value)
    return {
        "method": "counted-real-memory-bank-emitter", "words": spec.words, "word_bits": spec.word_bits,
        "bytes": spec.bytes, "placements": sum(blocks.values()), "chunks_at_aligned_origin": len(chunks),
        "storage_repeaters": spec.words * spec.word_bits,
        "other_cell_and_wiring_blocks": sum(blocks.values()) - spec.words * spec.word_bits,
        "block_types": dict(blocks), "bounds_relative_to_origin": {"min": minimum, "max": maximum},
    }


@lru_cache(maxsize=64)
def _bank_chunk_footprint(spec: MemoryBankSpec, x_mod: int, z_mod: int) -> frozenset[tuple[int, int]]:
    """Exact horizontal occupancy of the regular bank geometry, in chunks."""
    if spec.bit_pitch < 5 or spec.word_pitch < 6:
        raise WorldWriteError("native resource estimate requires nonoverlapping memory-cell pitches")
    chunks: set[tuple[int, int]] = set()
    z_first = (z_mod - 2) // 16
    z_last = (z_mod + spec.words * spec.word_pitch + 4) // 16
    ports = bank_ports(Vec3(x_mod, 64, z_mod), spec)
    for cx in {point.x // 16 for point in (*ports.data_in, *ports.data_out)}:
        chunks.update((cx, cz) for cz in range(z_first, z_last + 1))
    x_first = min(point.x for point in ports.write_select) // 16
    x_last = max(point.x for point in ports.data_out) // 16
    for word in range(spec.words):
        z0 = (z_mod + word * spec.word_pitch + 1) // 16
        z1 = (z_mod + word * spec.word_pitch + 5) // 16
        chunks.update((cx, cz) for cx in range(x_first, x_last + 1) for cz in range(z0, z1 + 1))
    return frozenset(chunks)


@lru_cache(maxsize=16)
def _storage_layout_cost(g: MachineGeometry) -> dict[str, object]:
    origins = default_origins(g)
    cache, ram, flash = memory_specs(g)
    from .layout import vram_spec
    fabrics = (("cache", origins.cache, cache), ("ram", origins.ram, ram),
               ("flash", origins.flash, flash), ("vram", origins.vram, vram_spec(g)))
    regions: set[tuple[int, int]] = set()
    total_chunks = 0
    details = {}
    for name, origin, fabric in fabrics:
        chunks = 0
        for group in range(fabric.group_count):
            base = fabric.group_origin(origin, group)
            footprint = _bank_chunk_footprint(fabric.bank, base.x & 15, base.z & 15)
            chunks += len(footprint)
            regions.update(((base.x // 16 + cx) // 32, (base.z // 16 + cz) // 32) for cx, cz in footprint)
        box = fabric.bounds(origin)
        details[name] = {
            "bytes": fabric.total_bytes, "banks": fabric.bank_count, "layers": fabric.layers,
            "tiles": fabric.group_count, "banks_in_final_tile": fabric.tile_spec(fabric.group_count - 1).banks,
            "chunks": chunks, "origin": vars(origin),
            "bounds_with_local_controls": {"min": vars(box.min), "max": vars(box.max)},
        }
        total_chunks += chunks
    # Separate tile/control envelopes have gaps greater than one chunk; only
    # vertically stacked banks share a chunk. Count each horizontal tile once.
    return {
        "storage_fabrics": details, "storage_chunks": total_chunks, "storage_regions": len(regions),
        "minimum_storage_region_bytes": total_chunks * 4096 + len(regions) * 8192,
        "storage_banks": sum(fabric.bank_count for _, _, fabric in fabrics),
        "storage_bits": sum(fabric.total_bytes for _, _, fabric in fabrics) * 8,
    }


def generation_preflight(config: dict, *, world_path: str | Path | None = None, benchmark_path: str | Path | None = None) -> dict[str, object]:
    """Report literal capacity, sampled storage cost and unresolved full-world costs."""
    config = resolve_world_surface(config, world_path)
    g = geometry_from_config(config)
    origins = default_origins(g)
    cache, ram, flash = memory_specs(g)
    from .layout import vram_spec
    fabrics = (("cache", origins.cache, cache), ("ram", origins.ram, ram), ("flash", origins.flash, flash), ("vram", origins.vram, vram_spec(g)))
    profile = memory_bank_profile(ram.bank)
    layout_cost = _storage_layout_cost(g)
    from .physical_resources import regular_fabric_resources
    regular = regular_fabric_resources(g)
    total_chunks = int(layout_cost["storage_chunks"])
    total_banks = int(layout_cost["storage_banks"])
    minimum_region_bytes = int(layout_cost["minimum_storage_region_bytes"])
    disk_path = Path(world_path or ".").resolve()
    while not disk_path.exists():
        disk_path = disk_path.parent
    free = shutil.disk_usage(disk_path).free
    contract = validate_manifest_capacity(_capacity_claims(config))
    for flag in ("physical_display", "physical_input", "synthesize_logic"):
        if not config.get("generator", {}).get(flag, True):
            contract["errors"].append(f"generator.{flag} must remain enabled for a full machine")
            contract["status"] = "failed"
    result: dict[str, object] = {
        "kind": "full-capacity-generation-preflight", "capacity_contract": contract,
        "source": source_provenance(), "sample_bank": profile, **layout_cost,
        "regular_fabrics": regular,
        "storage_banks": total_banks, "storage_placements": int(profile["placements"]) * total_banks,
        "minimum_storage_region_bytes": minimum_region_bytes, "available_disk_bytes": free,
        "storage_minimum_fits_available_disk": minimum_region_bytes < free,
        "estimate_excludes": ["synthesized logic", "interconnect", "validation ledger", "temporary compaction space"],
        "regular_fabric_minimum_fits_available_disk": int(regular["minimum_regular_fabric_region_bytes"]) < free,
        "minimum_region_bytes_note": "Anvil sector/header lower bound only; dense stacked chunks may require several sectors. Physical block count is unchanged by sharing X/Z chunks.",
        "production_packing": {"requested_layers": g.memory_layers if g.memory_layers is not None else "auto",
                               "maximum_safe_layers": g.maximum_memory_layers, "actual_max_layers": g.resolved_memory_layers,
                               "layer_pitch": g.memory_layer_pitch, "surface_y": g.surface_y,
                               "minimum_build_y": g.minimum_build_y, "storage_base_y": g.storage_origin_y,
                               "control_ceiling_y": g.memory_control_ceiling,
                               "scope": "complete literal banks with above-ground local decode and shared isolated read return"},
        "resident_chunk_memory_scenarios": [
            {"assumed_bytes_per_chunk": assumption, "total_bytes": total_chunks * assumption}
            for assumption in (16 << 10, 64 << 10, 256 << 10)
        ],
        "resident_memory_note": "Illustrative assumptions, not measured Java heap requirements. Loading all storage would additionally require logic/routes and engine overhead.",
        "full_world_loading_strategy": "unverified: no proven complete loaded/ticking-chunk plan or routed clock-skew bound",
        "complete_generation_feasibility": "not-established-until-logic-routes-and-loading-are-validated",
        "complete_world_runtime_verified": False,
    }
    density_scenarios = []
    for layers in sorted({1, *(value for value in (8, 16, 24, g.resolved_memory_layers, g.maximum_memory_layers) if value <= g.maximum_memory_layers)}):
        pitch = g.memory_layer_pitch
        alternate = _storage_layout_cost(replace(g, memory_layers=layers, memory_layer_pitch=pitch))
        density_scenarios.append({
            "layers": layers, "layer_pitch": pitch, "physical_storage_bytes": sum(f.total_bytes for _, _, f in fabrics),
            "physical_storage_placements": int(profile["placements"]) * total_banks,
            **alternate,
            "production_enabled": layers == g.resolved_memory_layers and pitch == g.memory_layer_pitch,
            "scope": "storage chunks only; bounds include local control and shared return geometry; global routes are additional",
            "validation": "representative local decode, shared return and first/middle/last bank read/write/retention proven through 32 layers; complete-machine timing remains unverified",
        })
    result["stacking_scenarios"] = density_scenarios
    if benchmark_path is not None:
        benchmark = json.loads(Path(benchmark_path).read_text(encoding="utf-8"))
        if "z-slabs" in benchmark:
            benchmark = benchmark["z-slabs"]
        expected_hashes = result["source"]["files_sha256"]
        geometry_matches = benchmark.get("source_sha256", {}).get("src/shamaos/hardware/memory.py") == expected_hashes.get("src/shamaos/hardware/memory.py") and benchmark.get("physical_placements", benchmark.get("placements")) == profile["placements"]
        writer_matches = benchmark.get("source_sha256", {}).get("src/shamaos/worldio.py") == expected_hashes.get("src/shamaos/worldio.py")
        result["sample_benchmark"] = {
            "source_matches_current": geometry_matches and writer_matches,
            "geometry_matches_current": geometry_matches, "writer_matches_current": writer_matches,
            "measured": benchmark,
            "estimated_storage_seconds": float(benchmark["total_seconds"]) * total_banks if geometry_matches else None,
            "estimate_note": "Flat per-bank linear extrapolation using the measured writer revision; stacked chunk compression, interleaving, other hardware and system-wide I/O contention are not measured here. Check writer_matches_current before treating throughput as current.",
        }
    return result


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
    world_validation: dict[str, object] = field(default_factory=dict)


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
        **memory_placement_options(config),
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
    if isinstance(fabric, StackedMemoryFabricSpec):
        return fabric.bank_origin(fabric_origin, bank_index)
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
    if isinstance(fabric, StackedMemoryFabricSpec):
        for group in range(fabric.group_count):
            tile = fabric.tile_spec(group)
            tile_origin = fabric.group_origin(origin, group)
            first_bank = group * fabric.layers
            start = first_bank * fabric.bank.bytes
            tile_data = None if initial is None else initial[start:start + tile.bytes]

            def factory(tile=tile, tile_origin=tile_origin, tile_data=tile_data, group=group):
                yield from iter_stacked_memory_tile(tile_origin, tile=tile, initial=tile_data,
                                                    component=f"{name}-tile-{group:03d}", include_backbone=False)

            yield PhysicalSection(f"{name}-tile-{group:03d}", factory, {
                "kind": "stacked-memory-tile", "tile_index": group,
                "first_bank_index": first_bank, "banks": tile.banks,
                "bytes": tile.bytes, "layer_pitch": tile.layer_pitch,
                "origin": vars(tile_origin), "preloaded": tile_data is not None,
                "emission_order": "ascending-z-slabs-interleaved-layers",
            })
        return
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
            yield from iter_memory_bank_ordered(
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
            raise ValueError(f"boot firmware exceeds {g.cache_bytes // 1024} KiB cache/scratch capacity")
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

    # Dedicated GPU VRAM is always a real physical memory fabric.
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
        "memory_packing": {
            "requested_layers": g.memory_layers if g.memory_layers is not None else "auto",
            "maximum_safe_layers": g.maximum_memory_layers, "layer_pitch": g.memory_layer_pitch,
            "surface_y": g.surface_y, "minimum_build_y": g.minimum_build_y,
            "storage_base_y": g.storage_origin_y, "control_ceiling_y": g.memory_control_ceiling,
            "bank_address_order": "group,layer=divmod(bank_index,layers)",
            "actual_layers": {"cache": cache_spec.layers, "ram": ram_spec.layers,
                              "flash": flash_spec.layers, "vram": vram_fabric.layers},
            "tiles": {"cache": cache_spec.group_count, "ram": ram_spec.group_count,
                      "flash": flash_spec.group_count, "vram": vram_fabric.group_count},
        },
        "display": {
            "width": g.display_width,
            "height": g.display_height,
            "bits_per_pixel": config["display"]["bits_per_pixel"],
            "pixel_pitch_x": g.pixel_pitch_x,
            "pixel_pitch_y": g.pixel_pitch_y,
        },
        "physical_generation_status": "planned-physical-topology",
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
    result["capacity_contract"] = validate_manifest_capacity(result)
    result["source"] = source_provenance()
    return result


def write_manifest(path: str | Path, data: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_placement_stages(
    world_path: str | Path,
    stages: Iterable[tuple[str, Iterable[Placement]]],
    *,
    save_every: int = 250_000,
    validate_after_write: bool = True,
    spawn: Vec3 = Vec3(-16, 64, -16),
    name: str = "ShamaOS Redstone PC",
    surface_y: int | None = None,
) -> tuple[int, dict[str, int], dict[str, object]]:
    """Stream placements with collision checks and optional exact disk readback.

    A failed stage leaves a partial, explicitly unverified world. It never
    silently overwrites another component or produces a success manifest.
    """
    count = 0
    counts: dict[str, int] = {}
    with NativeWorldWriter(world_path, name=name, spawn=spawn, surface_y=surface_y) as writer:
        for stage_name, placements in stages:
            if stage_name in counts:
                raise WorldWriteError(f"duplicate generation stage name: {stage_name}")
            stage_count = 0
            for placement in placements:
                writer.set_block(placement)
                count += 1
                stage_count += 1
                if count % max(1, save_every) == 0:
                    writer.save()
            counts[stage_name] = stage_count
            writer.save()
        verification = writer.validate() if validate_after_write else {
            "status": "not-run", "reason": "generator.validate_after_write is false",
        }
    return count, counts, verification


def create_runtime_fixture_world(world_path: str | Path) -> dict[str, object]:
    """Create a small playable block fixture, not the complete ShamaOS machine.

    Two physical logic circuits have actual lever inputs and visible redstone
    outputs. The simulator case is reconstructed from saved Anvil blocks so an
    optional MCHPRS run exercises the exported states, not generator inputs.
    """
    from .hardware.logic import TEMPLATES

    blocks: list[Placement] = []
    levers: list[Vec3] = []
    outputs: dict[str, list[int]] = {}
    for cell, origin in (("BUF", Vec3(32, 64, 32)), ("NOT", Vec3(32, 64, 64))):
        template = TEMPLATES[cell]
        blocks.extend(template.emitter(origin, f"fixture-{cell.lower()}"))
        pin = template.pins["A"]
        lever = origin.offset(pin.x, pin.y, pin.z + 1)
        levers.append(lever)
        blocks.extend([
            Placement(lever.offset(0, -1, 0), BlockState.of("minecraft:stone"), "fixture-controls"),
            Placement(lever, BlockState.of("minecraft:lever", face="floor", facing="north", powered="false"), "fixture-controls"),
        ])
        pin = template.pins["Y"]
        outputs[cell] = [origin.x + pin.x, origin.y + pin.y, origin.z + pin.z]
    count, stages, verification = write_placement_stages(
        world_path, [("runtime-logic-fixture", blocks)],
        name="ShamaOS Redstone Runtime Fixture", spawn=Vec3(24, 64, 48), save_every=17,
    )
    reader = NativeWorldReader(world_path)
    positions = sorted({placement.pos for placement in blocks}, key=lambda p: (p.x, p.y, p.z))
    case = {
        "blocks": [{
            "pos": [pos.x, pos.y, pos.z], "name": reader.get_block(pos).name,
            "properties": dict(reader.get_block(pos).properties),
        } for pos in positions],
        "outputs": outputs, "initial_ticks": 32,
        "steps": [
            {"ticks": 16, "expect": {"BUF": False, "NOT": True}},
            {"use_blocks": [[p.x, p.y, p.z] for p in levers], "ticks": 24, "expect": {"BUF": True, "NOT": False}},
            {"use_blocks": [[p.x, p.y, p.z] for p in levers], "ticks": 24, "expect": {"BUF": False, "NOT": True}},
        ],
    }
    report = {
        "kind": "small-runtime-fixture", "complete_shamaos_machine": False,
        "written_placements": count, "stage_counts": stages, "world_validation": verification,
        "runtime_validation": {"status": "not-run"},
        "controls": [[p.x, p.y, p.z] for p in levers], "outputs": outputs,
        "source": source_provenance(),
    }
    write_manifest(Path(world_path) / "shamaos" / "runtime_case.json", case)
    write_manifest(Path(world_path) / "shamaos" / "fixture.json", report)
    return report


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
    from .resource_accounting import prepared_system_resource_preflight

    config = resolve_world_surface(config, world_path)
    save_every = max(
        1,
        int(config.get("generator", {}).get("save_every_blocks", 250_000)),
    )
    build_dir = Path(
        config.get("generator", {}).get("build_dir", "build/physical")
    )
    if config["world"]["minecraft_version"] != "1.20.4" or config["world"]["data_version"] != 3700 or config["world"]["dimension"] != "minecraft:overworld":
        raise WorldWriteError("native generation requires Java 1.20.4 / DataVersion 3700 / overworld")
    preflight = generation_preflight(config, world_path=world_path)
    write_manifest(build_dir / "generation-preflight.json", preflight)
    if preflight["capacity_contract"]["status"] != "passed":
        raise WorldWriteError("full generation violates the literal capacity contract: " + "; ".join(preflight["capacity_contract"]["errors"]))
    if not preflight["storage_minimum_fits_available_disk"]:
        raise WorldWriteError("insufficient disk even for the storage-only Anvil lower bound; see generation-preflight.json")
    prepared = prepare_system(config, build_dir=build_dir)
    mapped_preflight = prepared_system_resource_preflight(prepared, preflight)
    write_manifest(build_dir / "mapped-resource-preflight.json", mapped_preflight)
    if not mapped_preflight["disk_lower_bound_fits"]:
        raise WorldWriteError(
            "insufficient disk for the mapped logic/routing Anvil lower bound "
            f"({mapped_preflight['minimum_whole_machine_region_bytes']:,} bytes minimum, "
            f"{mapped_preflight['available_disk_bytes']:,} available); "
            "no world blocks were emitted; see mapped-resource-preflight.json"
        )

    def stages() -> Iterator[tuple[str, Iterable[Placement]]]:
        for section in physical_sections(config):
            yield section.name, section.factory()
        yield "system-infrastructure", iter_system_infrastructure(config, prepared)
        yield "synthesized-soc", iter_logic(prepared)
        # Only identical terminal/support overlaps are allowed. A router that
        # crosses or replaces another component must fail before publication.
        yield "system-interconnect", iter_interconnect(prepared)

    count, stage_counts, verification = write_placement_stages(
        world_path, stages(), save_every=save_every,
        validate_after_write=config.get("generator", {}).get("validate_after_write", True),
        spawn=Vec3(config["world"]["origin_x"] - 16, config["world"]["surface_y"] + 2, config["world"]["origin_z"] - 16),
        surface_y=config["world"]["surface_y"],
    )

    metadata = dict(prepared.metadata)
    metadata["stage_counts"] = dict(stage_counts)
    metadata["written_blocks"] = count
    metadata["world_validation"] = verification
    metadata["runtime_validation"] = {"status": "not-run"}
    metadata["generation_preflight"] = preflight
    metadata["mapped_resource_preflight"] = mapped_preflight

    return GenerationResult(
        written_blocks=count,
        system_metadata=metadata,
        stage_counts=stage_counts,
        world_validation=verification,
    )
