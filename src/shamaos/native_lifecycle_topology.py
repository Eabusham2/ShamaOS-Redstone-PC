"""Check a saved production circuit against its independently re-emitted source placements."""
from __future__ import annotations

from itertools import chain
import json
from pathlib import Path
import re

from .generator import (build_plan, generation_preflight, geometry_from_config, manifest, physical_sections,
                        source_provenance, validate_manifest_capacity)
from .hardware.appearance import appearance_for_system
from .literal_union import literal_union
from .model import BlockState, Placement, Vec3
from .system_build import finalize_prepared_system, iter_interconnect, iter_logic, iter_system_infrastructure, prepare_system
from .worldio import AIR, NativeWorldReader, WorldWriteError, _Region, _decode_chunk, validate_native_world


def check_manifest(generated, config, commit):
    capacity = validate_manifest_capacity(generated)
    if capacity["errors"]:
        raise WorldWriteError("Incorrect lifecycle capacity: " + ", ".join(capacity["errors"]))
    source = source_provenance()
    if (source.get("working_tree_dirty") is not False or source.get("base_revision") != commit
            or generated.get("source") != source):
        raise WorldWriteError("Lifecycle source inventory must match this clean checkout and candidate revision")
    if generated.get("physical_generation_status") != "generated-topology-readback-verified":
        raise WorldWriteError("Lifecycle execution requires an actual generated production world")
    expected = manifest(config, build_plan(config))
    for key, value in expected.items():
        if (key != "physical_generation_status"
                and json.dumps(generated.get(key), sort_keys=True) != json.dumps(value, sort_keys=True)):
            raise WorldWriteError(f"Generated manifest differs from the source configuration at {key}")
    sections = {s["name"] for s in expected["physical_sections"]}
    stages = generated.get("stage_counts", {})
    required = sections | {"system-infrastructure", "synthesized-soc", "system-interconnect"}
    if (set(stages) != required or any(type(v) is not int or v <= 0 for v in stages.values())
            or sum(stages.values()) != generated.get("written_placements")
            or not {"display", "input"} <= sections
            or not all(any(n.startswith(f + "-tile-") for n in sections) for f in ("cache", "ram", "flash", "vram"))):
        raise WorldWriteError("Lifecycle requires every production fabric and generation stage")


def check_placements(world, stages, readback, temporary):
    """Compare literal states and require an exact distinct-coordinate count, including overlaps."""
    reader = NativeWorldReader(world)
    counts = {}
    def verify():
        for name, placements in stages:
            if name in counts:
                raise WorldWriteError(f"Duplicate physical stage {name}")
            counts[name] = 0
            for placement in placements:
                pos = placement.pos
                section = reader.chunk(pos.x//16, pos.z//16).sections.get(pos.y//16)
                cell = ((pos.y & 15) << 8) | ((pos.z & 15) << 4) | (pos.x & 15)
                if (section is None or not section.assigned[cell >> 3] & (1 << (cell & 7))
                        or section.palette[section.indices[cell]] != placement.block):
                    raise WorldWriteError(f"Missing or different {name} placement at {pos}")
                counts[name] += 1
                yield placement
    union = literal_union(verify(), temporary_parent=temporary)
    if (union["literal_unique_blocks"] != readback["assigned_blocks"] or
            union["occupied_chunks"] != readback["chunks"]):
        raise WorldWriteError("Native assignments or chunks differ from the complete generated circuit")
    # A handwritten ownership bitmap can omit an unexpected native circuit.
    # Count all actual above-ground states, including explicitly assigned air.
    # Check the unowned flat terrain too, including missing blocks in a chunk.
    terrain, above = audit_native_contents(world, reader.surface_y)
    if above != union["literal_unique_blocks"] or terrain != readback["chunks"] * (reader.surface_y+65) * 256:
        raise WorldWriteError("Native terrain or unowned above-ground blocks differ from production")
    return {"counts": counts, "literal_union": union, "terrain_blocks": terrain}


def audit_native_contents(world, surface_y):
    """Validate actual terrain, unowned states and original comparator analog outputs."""
    terrain = above = 0
    grass = BlockState.of("minecraft:grass_block", snowy="false")
    dirt, bedrock = BlockState.of("minecraft:dirt"), BlockState.of("minecraft:bedrock")
    for path in sorted((world / "region").glob("*.mca")):
        match = re.fullmatch(r"r\.(-?\d+)\.(-?\d+)\.mca", path.name)
        if match is None:
            raise WorldWriteError(f"Unexpected native region {path}")
        rx, rz = map(int, match.groups())
        region = _Region(path)
        for index in region.locations():
            root = region.read(index)
            chunk = _decode_chunk(root, rx*32 + index%32, rz*32 + index//32)
            # The generated comparator writes only 0 or 15. A restored world
            # may carry a genuine intermediate strength and follows a separate path.
            # _decode_chunk also requires an exact one-to-one entity set and
            # (OutputSignal > 0) == powered. Together these are the exact
            # initial writer rule: 15 when powered, otherwise zero.
            if any(int(e["OutputSignal"]) not in (0, 15) for e in root["block_entities"]):
                raise WorldWriteError("An original comparator has a non-generated analog output")
            for sy, section in chunk.sections.items():
                boundary = max(0, min(16, surface_y - sy*16 + 1)) * 256
                for row in range(0, boundary, 256):
                    y = sy*16 + row//256
                    ground = grass if y == surface_y else bedrock if y == -64 else dirt
                    ids = section.indices[row:row+256]
                    if any(section.palette[i] != ground for i in set(ids)):
                        raise WorldWriteError(f"Unexpected saved terrain in chunk {(chunk.x, chunk.z)} at Y={y}")
                    terrain += len(ids)
                for cell in range(boundary, len(section.indices)):
                    state = section.palette[section.indices[cell]]
                    if state != AIR or section.assigned[cell >> 3] & (1 << (cell & 7)):
                        above += 1
    return terrain, above


def check_topology(world: Path, config, generated, output: Path):
    readback = validate_native_world(world)
    if (readback != generated.get("world_validation") or readback["grass_surface_y"] != config["world"]["surface_y"]
            or readback["terrain_preserved"] is not True):
        raise WorldWriteError("Fresh Anvil readback differs from the generated production candidate")
    # Even a detached console with a forged passing ledger cannot start a large synthesis.
    minimum_bits = 8 * sum(generated[k] for k in ("cache_bytes", "logical_ram_bytes", "flash_bytes", "vram_bytes"))
    if readback["assigned_blocks"] < minimum_bits + 320*180:
        raise WorldWriteError("Saved world cannot contain the physical storage and screen")
    storage = generation_preflight(config, world_path=output)
    prepared = prepare_system(config, build_dir=output)
    prepared, resources = finalize_prepared_system(prepared, storage, temporary_parent=output)
    if not resources["disk_lower_bound_fits"]:
        raise WorldWriteError("Insufficient disk for the complete independently prepared system")
    appearance = appearance_for_system(geometry_from_config(config), partitions=prepared.logic.partitions)
    sections = ((s.name, s.factory()) for s in physical_sections(config))
    source_stages = chain(sections, (("system-infrastructure", iter_system_infrastructure(config, prepared)),
                                      ("synthesized-soc", iter_logic(prepared)),
                                      ("system-interconnect", iter_interconnect(prepared))))
    spawn = Vec3(config["world"]["origin_x"]-16, config["world"]["surface_y"]+2, config["world"]["origin_z"]-16)
    platform = (Placement(spawn.offset(dx, -1, dz), BlockState.of("minecraft:stone"), "spawn-platform")
                for dx in range(-4, 5) for dz in range(-4, 5))
    stages = chain([("spawn-platform", platform)],
                   ((name, map(appearance.apply, placements)) for name, placements in source_stages))
    report = check_placements(world, stages, readback, output)
    if report["counts"] != (generated["stage_counts"] | {"spawn-platform": 81}):
        raise WorldWriteError("Re-emitted production stage totals differ from the manifest")
    return report | {"anvil": readback}
