from __future__ import annotations

from contextlib import AbstractContextManager
from array import array
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass, field
from functools import lru_cache
import gzip
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import struct
import time
from typing import Iterable, Iterator
import zlib

import nbtlib

from .model import BlockState, Placement, Vec3


def to_amulet_block(state: BlockState):
    """Convert a project BlockState to an exact Amulet Java block state.

    Amulet requires NBT tag values for block properties. Never silently drop
    repeater/comparator/lamp properties: orientation, lock state and power
    state are functional parts of the computer.
    """
    try:
        from amulet.api.block import Block  # type: ignore
        from amulet_nbt import StringTag  # type: ignore
    except Exception as exc:
        raise WorldWriteError(
            "world generation requires the optional 'world' dependencies; "
            "install with: pip install -e '.[world]'"
        ) from exc

    if ":" not in state.name:
        namespace, base_name = "minecraft", state.name
    else:
        namespace, base_name = state.name.split(":", 1)

    properties = {
        key: StringTag(value)
        for key, value in state.properties
    }
    return Block(namespace, base_name, properties)


class WorldWriteError(RuntimeError):
    pass


JAVA_DATA_VERSION = 3700
SECTOR_BYTES = 4096
MAX_CHUNK_NBT_BYTES = 16 << 20
DISK_RESERVE_BYTES = 64 << 20
AIR = BlockState.of("minecraft:air")

# This is the context-free palette used by ShamaOS, not a permissive substitute
# for Minecraft's complete block registry. Rejecting unknown names/properties
# prevents the game silently replacing an invalid state with air/defaults.
_BOOL = {"false", "true"}
_HORIZONTAL = {"north", "east", "south", "west"}
_STATE_PROPERTIES: dict[str, dict[str, tuple[str, set[str]]]] = {
    name: {} for name in (
        "air", "stone", "smooth_stone", "black_concrete", "polished_deepslate",
        "smooth_quartz", "redstone_block", "bedrock", "barrier", "dirt",
    )
}
_STATE_PROPERTIES.update({
    "grass_block": {"snowy": ("false", _BOOL)},
    "target": {"power": ("0", {str(i) for i in range(16)})},
    "redstone_wire": {
        **{direction: ("none", {"none", "side", "up"}) for direction in _HORIZONTAL},
        "power": ("0", {str(i) for i in range(16)}),
    },
    "repeater": {
        "facing": ("north", _HORIZONTAL), "delay": ("1", {"1", "2", "3", "4"}),
        "powered": ("false", _BOOL), "locked": ("false", _BOOL),
    },
    "comparator": {
        "facing": ("north", _HORIZONTAL), "mode": ("compare", {"compare", "subtract"}),
        "powered": ("false", _BOOL),
    },
    "redstone_torch": {"lit": ("true", _BOOL)},
    "redstone_wall_torch": {"facing": ("north", _HORIZONTAL), "lit": ("true", _BOOL)},
    "redstone_lamp": {"lit": ("false", _BOOL)},
    **{name: {
        "face": ("wall", {"floor", "wall", "ceiling"}),
        "facing": ("north", _HORIZONTAL), "powered": ("false", _BOOL),
    } for name in ("lever", "stone_button")},
})


def canonical_block_state(state: BlockState) -> BlockState:
    """Validate the native hardware palette and make all properties explicit."""
    name = state.name if ":" in state.name else "minecraft:" + state.name
    if not name.startswith("minecraft:") or name[10:] not in _STATE_PROPERTIES:
        raise WorldWriteError(f"unsupported native Java 1.20.4 block: {name}")
    schema = _STATE_PROPERTIES[name[10:]]
    values = dict(state.properties)
    if len(values) != len(state.properties):
        raise WorldWriteError(f"duplicate block property in {state}")
    for key, value in values.items():
        if key not in schema or value not in schema[key][1]:
            raise WorldWriteError(f"invalid Java 1.20.4 state {name}[{key}={value}]")
    return BlockState.of(name, **{key: values.get(key, default) for key, (default, _) in schema.items()})


def validate_position(pos: Vec3) -> None:
    if any(type(value) is not int for value in (pos.x, pos.y, pos.z)):
        raise WorldWriteError(f"block coordinates must be integers: {pos}")
    if not -64 <= pos.y <= 319:
        raise WorldWriteError(f"placement outside Java 1.20.4 build height at {pos}")
    if not -29_999_984 <= pos.x <= 29_999_983 or not -29_999_984 <= pos.z <= 29_999_983:
        raise WorldWriteError(f"placement outside safe Java world border at {pos}")


def _flat_surface_from_level(level: nbtlib.File, *, require_grass: bool) -> int | None:
    """Find the grass block's Y from this world's actual flat-layer settings."""
    try:
        data = level["Data"]
        if int(data["DataVersion"]) != JAVA_DATA_VERSION:
            raise WorldWriteError("surface detection requires a Java 1.20.4 world")
        dimension = data["WorldGenSettings"]["dimensions"]["minecraft:overworld"]
        generator = dimension["generator"]
        if str(dimension["type"]) != "minecraft:overworld" or str(generator["type"]) != "minecraft:flat":
            raise WorldWriteError("the full build requires a grass superflat overworld")
        layers = generator["settings"]["layers"]
        heights = [int(layer["height"]) for layer in layers]
        if not heights or any(height <= 0 for height in heights) or sum(heights) > 384:
            raise WorldWriteError("invalid superflat terrain layers")
        if str(layers[-1]["block"]) != "minecraft:grass_block":
            if not require_grass and all(str(layer["block"]) == "minecraft:air" for layer in layers):
                return None
            raise WorldWriteError("superflat terrain must have grass as its top layer")
        return -64 + sum(heights) - 1
    except WorldWriteError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise WorldWriteError(f"cannot determine superflat grass surface: {exc}") from exc


def superflat_surface_y(world_path: str | Path) -> int | None:
    """Return an existing world's grass datum, or None for a new destination."""
    path = Path(world_path) / "level.dat"
    if not path.exists():
        return None
    try:
        return _flat_surface_from_level(nbtlib.load(path), require_grass=True)
    except WorldWriteError:
        raise
    except Exception as exc:
        raise WorldWriteError(f"cannot read superflat world settings: {exc}") from exc


def resolve_world_surface(config: dict, world_path: str | Path | None) -> dict:
    """Keep the layout above actual grass, including higher custom flat presets.

    Existing level.dat settings take precedence over the new-world default.
    Preserve the requested component clearance above grass when moving that
    datum. Explicit absolute memory-base overrides are checked by the layout.
    """
    result = deepcopy(config)
    world = result["world"]
    configured = world.get("surface_y", -61)
    if type(configured) is not int or not -63 <= configured <= 318:
        raise WorldWriteError("world.surface_y must be a grass-block Y between -63 and 318")
    actual = None if world_path is None else superflat_surface_y(world_path)
    surface = configured if actual is None else actual
    if actual is not None:
        world["origin_y"] = world.get("origin_y", configured + 5) + actual - configured
    world["surface_y"] = surface
    world["surface_source"] = "config-new-world" if actual is None else "level.dat"
    world["minimum_build_y"] = surface + 1
    return result


def _signed_long(value: int) -> int:
    return value if value < 1 << 63 else value - (1 << 64)


def _pack_indices(indices: Iterable[int], bits: int) -> list[int]:
    """Java 1.16+ padded packing: entries never straddle a 64-bit long."""
    per_long = 64 // bits
    words: list[int] = []
    current = 0
    count = 0
    for value in indices:
        if not 0 <= value < 1 << bits:
            raise WorldWriteError("palette index does not fit bit width")
        current |= value << ((count % per_long) * bits)
        count += 1
        if count % per_long == 0:
            words.append(_signed_long(current))
            current = 0
    if count % per_long:
        words.append(_signed_long(current))
    return words


def _unpack_indices(words: Iterable[int], count: int, bits: int, palette_size: int) -> array:
    data = list(words)
    per_long = 64 // bits
    if len(data) != math.ceil(count / per_long):
        raise WorldWriteError("invalid packed block-state array length")
    mask = (1 << bits) - 1
    result = array("H")
    for signed in data:
        word = int(signed) & ((1 << 64) - 1)
        entries = min(per_long, count - len(result))
        for _ in range(entries):
            value = word & mask
            if value >= palette_size:
                raise WorldWriteError("block-state palette index out of range")
            result.append(value)
            word >>= bits
        if word:
            raise WorldWriteError("nonzero padding in block-state array")
    return result


@dataclass
class _Section:
    palette: list[BlockState] = field(default_factory=lambda: [AIR])
    indices: array = field(default_factory=lambda: array("H", [0]) * 4096)
    assigned: bytearray = field(default_factory=lambda: bytearray(512))
    lookup: dict[BlockState, int] = field(default_factory=lambda: {AIR: 0})

    def put(self, index: int, state: BlockState, pos: Vec3, component: str) -> bool:
        old = self.palette[self.indices[index]]
        occupied = bool(self.assigned[index >> 3] & (1 << (index & 7))) or old != AIR
        if occupied and old != state:
            raise WorldWriteError(
                f"conflicting placements at {pos}: existing {old}, "
                f"incoming {state} from {component}; implicit overwrite is forbidden"
            )
        if occupied:
            return False
        palette_index = self.lookup.get(state)
        if palette_index is None:
            palette_index = len(self.palette)
            self.palette.append(state)
            self.lookup[state] = palette_index
        self.indices[index] = palette_index
        self.assigned[index >> 3] |= 1 << (index & 7)
        return True


@dataclass
class _Chunk:
    x: int
    z: int
    sections: dict[int, _Section] = field(default_factory=dict)
    dirty: bool = False

    def fingerprint(self) -> tuple[str, int]:
        """Canonical semantic digest, independent of palette order/NBT encoding."""
        digest = hashlib.sha256()
        digest.update(struct.pack(">ii", self.x, self.z))
        count = 0
        for sy, section in sorted(self.sections.items()):
            if not any(section.assigned) and all(section.palette[i] == AIR for i in section.indices):
                continue
            used = sorted({section.palette[i] for i in section.indices}, key=lambda state: (state.name, state.properties))
            remap = {state: index for index, state in enumerate(used)}
            digest.update(struct.pack(">b", sy))
            digest.update(json.dumps([(state.name, state.properties) for state in used], separators=(",", ":")).encode())
            digest.update(struct.pack(">4096H", *(remap[section.palette[index]] for index in section.indices)))
            digest.update(section.assigned)
            count += sum(value.bit_count() for value in section.assigned)
        return digest.hexdigest(), count


@lru_cache(maxsize=8)
def _terrain_sections(surface_y: int) -> dict[int, _Section]:
    """Reusable flat ground; ownership bits remain clear because it is terrain."""
    if type(surface_y) is not int or not -63 <= surface_y <= 318:
        raise WorldWriteError("grass surface must leave bedrock below and building space above")
    palette = [AIR, BlockState.of("minecraft:bedrock"), BlockState.of("minecraft:dirt"),
               BlockState.of("minecraft:grass_block", snowy="false")]
    sections = {}
    for sy in range(-4, surface_y // 16 + 1):
        section = _Section(list(palette), lookup={state: index for index, state in enumerate(palette)})
        for y in range(max(-64, sy * 16), min(surface_y, sy * 16 + 15) + 1):
            value = 1 if y == -64 else 3 if y == surface_y else 2
            start = (y & 15) * 256
            section.indices[start:start + 256] = array("H", [value]) * 256
        sections[sy] = section
    return sections


def _new_chunk(cx: int, cz: int, surface_y: int | None) -> _Chunk:
    if surface_y is None:
        return _Chunk(cx, cz)
    sections = {
        sy: _Section(list(section.palette), array("H", section.indices),
                     bytearray(section.assigned), dict(section.lookup))
        for sy, section in _terrain_sections(surface_y).items()
    }
    return _Chunk(cx, cz, sections, dirty=True)


def _chunk_nbt(chunk: _Chunk) -> nbtlib.File:
    sections = []
    entities = []
    for sy in range(-4, 20):
        section = chunk.sections.get(sy)
        if section is None:
            block_states = nbtlib.Compound({"palette": nbtlib.List[nbtlib.Compound]([nbtlib.Compound({"Name": nbtlib.String(AIR.name)})])})
        else:
            used_ids = sorted(set(section.indices))
            compact = {old: new for new, old in enumerate(used_ids)}
            palette = []
            for old in used_ids:
                state = section.palette[old]
                entry = nbtlib.Compound({"Name": nbtlib.String(state.name)})
                if state.properties:
                    entry["Properties"] = nbtlib.Compound({key: nbtlib.String(value) for key, value in state.properties})
                palette.append(entry)
            block_states = nbtlib.Compound({"palette": nbtlib.List[nbtlib.Compound](palette)})
            if len(palette) > 1:
                bits = max(4, (len(palette) - 1).bit_length())
                block_states["data"] = nbtlib.LongArray(_pack_indices((compact[index] for index in section.indices), bits))
            for index, palette_id in enumerate(section.indices):
                state = section.palette[palette_id]
                if state.name == "minecraft:comparator":
                    entities.append(nbtlib.Compound({
                        "id": nbtlib.String("minecraft:comparator"),
                        "x": nbtlib.Int(chunk.x * 16 + (index & 15)),
                        "y": nbtlib.Int(sy * 16 + (index >> 8)),
                        "z": nbtlib.Int(chunk.z * 16 + ((index >> 4) & 15)),
                        "OutputSignal": nbtlib.Int(15 if dict(state.properties)["powered"] == "true" else 0),
                        "keepPacked": nbtlib.Byte(0),
                    }))
        entry = nbtlib.Compound({
            "Y": nbtlib.Byte(sy), "block_states": block_states,
            "biomes": nbtlib.Compound({"palette": nbtlib.List[nbtlib.String]([nbtlib.String("minecraft:plains")])}),
        })
        if section is not None:
            entry["shamaos_written"] = nbtlib.ByteArray([value if value < 128 else value - 256 for value in section.assigned])
        sections.append(entry)
    # Light and height maps are deliberately absent; isLightOn=0 asks the game
    # to calculate them. The generated block states themselves are complete.
    return nbtlib.File({
        "DataVersion": nbtlib.Int(JAVA_DATA_VERSION),
        "xPos": nbtlib.Int(chunk.x), "zPos": nbtlib.Int(chunk.z), "yPos": nbtlib.Int(-4),
        "Status": nbtlib.String("minecraft:full"),
        "LastUpdate": nbtlib.Long(0), "InhabitedTime": nbtlib.Long(0), "isLightOn": nbtlib.Byte(0),
        "sections": nbtlib.List[nbtlib.Compound](sections),
        "block_entities": nbtlib.List[nbtlib.Compound](entities),
        "block_ticks": nbtlib.List[nbtlib.Compound]([]), "fluid_ticks": nbtlib.List[nbtlib.Compound]([]),
        "PostProcessing": nbtlib.List[nbtlib.List]([nbtlib.List[nbtlib.Short]([]) for _ in range(24)]),
        "structures": nbtlib.Compound({"starts": nbtlib.Compound(), "References": nbtlib.Compound()}),
    })


def _decode_chunk(root: nbtlib.File, cx: int, cz: int) -> _Chunk:
    try:
        if any(not isinstance(root.get(key), nbtlib.Int) for key in ("DataVersion", "xPos", "zPos", "yPos")):
            raise WorldWriteError("chunk version/coordinates must be NBT integers")
        if int(root["DataVersion"]) != JAVA_DATA_VERSION:
            raise WorldWriteError("chunk is not Java 1.20.4 DataVersion 3700")
        if (int(root["xPos"]), int(root["zPos"])) != (cx, cz):
            raise WorldWriteError(f"chunk coordinates disagree with region location ({cx}, {cz})")
        if int(root["yPos"]) != -4 or str(root["Status"]) != "minecraft:full":
            raise WorldWriteError("chunk has invalid height or generation status")
        chunk = _Chunk(cx, cz)
        seen = set()
        expected_comparators: dict[tuple[int, int, int], int] = {}
        for encoded in root["sections"]:
            if not isinstance(encoded.get("Y"), nbtlib.Byte):
                raise WorldWriteError("chunk section Y is not an NBT byte")
            sy = int(encoded["Y"])
            if sy not in range(-4, 20) or sy in seen:
                raise WorldWriteError("duplicate/out-of-range chunk section")
            seen.add(sy)
            biomes = encoded["biomes"]
            if list(map(str, biomes["palette"])) != ["minecraft:plains"] or "data" in biomes:
                raise WorldWriteError("native chunk must use the declared plains biome")
            block_states = encoded["block_states"]
            palette = []
            for entry in block_states["palette"]:
                if not isinstance(entry["Name"], nbtlib.String):
                    raise WorldWriteError("block name is not an NBT string")
                props = entry.get("Properties", {})
                if any(not isinstance(value, nbtlib.String) for value in props.values()):
                    raise WorldWriteError("block property is not an NBT string")
                state = BlockState.of(str(entry["Name"]), **{key: str(value) for key, value in props.items()})
                if canonical_block_state(state) != state:
                    raise WorldWriteError(f"incomplete/noncanonical native block state {state}")
                palette.append(state)
            if not 1 <= len(palette) <= 4096 or len(set(palette)) != len(palette):
                raise WorldWriteError("empty/duplicate/oversized block-state palette")
            if palette == [AIR] and "data" not in block_states and "shamaos_written" not in encoded:
                continue
            if len(palette) == 1:
                if "data" in block_states:
                    raise WorldWriteError("singleton block-state palette unexpectedly has data")
                indices = array("H", [0]) * 4096
            else:
                if not isinstance(block_states.get("data"), nbtlib.LongArray):
                    raise WorldWriteError("block-state data is not an NBT long array")
                indices = _unpack_indices(block_states["data"], 4096, max(4, (len(palette) - 1).bit_length()), len(palette))
            if "shamaos_written" in encoded:
                if not isinstance(encoded["shamaos_written"], nbtlib.ByteArray) or len(encoded["shamaos_written"]) != 512:
                    raise WorldWriteError("invalid placement ownership bitmap")
                assigned = bytearray(int(value) & 255 for value in encoded["shamaos_written"])
            else:
                assigned = bytearray(512)
                for index, palette_index in enumerate(indices):
                    if palette[palette_index] != AIR:
                        assigned[index >> 3] |= 1 << (index & 7)
            if any(assigned) or any(palette[index] != AIR for index in indices):
                chunk.sections[sy] = _Section(palette, indices, assigned, {state: index for index, state in enumerate(palette)})
            if any(state.name == "minecraft:comparator" for state in palette):
                for index, palette_index in enumerate(indices):
                    state = palette[palette_index]
                    if state.name == "minecraft:comparator":
                        expected_comparators[(cx * 16 + (index & 15), sy * 16 + (index >> 8), cz * 16 + ((index >> 4) & 15))] = 15 if dict(state.properties)["powered"] == "true" else 0
        if seen != set(range(-4, 20)):
            raise WorldWriteError("native chunk is missing a height/biome section")
        found_entities: set[tuple[int, int, int]] = set()
        for entity in root["block_entities"]:
            coordinate = (int(entity["x"]), int(entity["y"]), int(entity["z"]))
            if str(entity["id"]) != "minecraft:comparator" or coordinate in found_entities or coordinate not in expected_comparators:
                raise WorldWriteError("invalid/duplicate comparator block entity")
            if int(entity["OutputSignal"]) != expected_comparators[coordinate]:
                raise WorldWriteError("comparator block entity disagrees with powered state")
            found_entities.add(coordinate)
        if found_entities != set(expected_comparators):
            raise WorldWriteError("missing comparator block entity")
        return chunk
    except WorldWriteError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise WorldWriteError(f"malformed chunk ({cx}, {cz}): {exc}") from exc


class _Region:
    """Validated Anvil sectors; append then atomically compact on each save."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.header = bytearray(8192)
        if not path.exists():
            return
        size = path.stat().st_size
        if size < 8192 or size % SECTOR_BYTES:
            raise WorldWriteError(f"invalid Anvil region length: {path}")
        with path.open("rb") as stream:
            self.header[:] = stream.read(8192)
        occupied = []
        for index in range(1024):
            location = struct.unpack_from(">I", self.header, index * 4)[0]
            offset, sectors = location >> 8, location & 255
            if not location:
                continue
            if offset < 2 or sectors == 0 or (offset + sectors) * SECTOR_BYTES > size:
                raise WorldWriteError(f"invalid Anvil chunk sector location in {path}, slot {index}")
            occupied.append((offset, offset + sectors, index))
        occupied.sort()
        if any(left[1] > right[0] for left, right in zip(occupied, occupied[1:])):
            raise WorldWriteError(f"overlapping Anvil chunk sectors in {path}")

    def locations(self) -> Iterator[int]:
        for index in range(1024):
            if struct.unpack_from(">I", self.header, index * 4)[0]:
                yield index

    def record(self, index: int) -> bytes | None:
        location = struct.unpack_from(">I", self.header, index * 4)[0]
        if not location:
            return None
        offset, sectors = location >> 8, location & 255
        with self.path.open("rb") as stream:
            stream.seek(offset * SECTOR_BYTES)
            length_data = stream.read(4)
            if len(length_data) != 4:
                raise WorldWriteError("truncated Anvil chunk length")
            length = struct.unpack(">I", length_data)[0]
            if length < 2 or length > sectors * SECTOR_BYTES - 4:
                raise WorldWriteError("invalid Anvil chunk payload length")
            payload = stream.read(length)
            if len(payload) != length:
                raise WorldWriteError("truncated Anvil chunk payload")
        return length_data + payload

    def read(self, index: int) -> nbtlib.File | None:
        record = self.record(index)
        if record is None:
            return None
        compression = record[4]
        if compression & 128:
            raise WorldWriteError("external Anvil chunks are not supported by the native hardware reader")
        try:
            if compression in (1, 2):
                decoder = zlib.decompressobj(16 + zlib.MAX_WBITS if compression == 1 else zlib.MAX_WBITS)
                raw = decoder.decompress(record[5:], MAX_CHUNK_NBT_BYTES + 1)
                if len(raw) > MAX_CHUNK_NBT_BYTES or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
                    raise WorldWriteError("invalid/oversized compressed Anvil chunk")
            elif compression == 3:
                raw = record[5:]
                if len(raw) > MAX_CHUNK_NBT_BYTES:
                    raise WorldWriteError("oversized uncompressed Anvil chunk")
            else:
                raise WorldWriteError(f"unsupported Anvil compression type {compression}")
            stream = io.BytesIO(raw)
            root = nbtlib.File.parse(stream)
            if stream.tell() != len(raw):
                raise WorldWriteError("trailing data after chunk NBT")
            return root
        except (zlib.error, EOFError, ValueError, KeyError, struct.error) as exc:
            raise WorldWriteError(f"corrupt Anvil chunk in {self.path}: {exc}") from exc

    def write(self, index: int, root: nbtlib.File) -> None:
        buffer = io.BytesIO()
        root.write(buffer)
        payload = b"\x02" + zlib.compress(buffer.getvalue())
        record = struct.pack(">I", len(payload)) + payload
        sectors = math.ceil(len(record) / SECTOR_BYTES)
        if sectors > 255:
            raise WorldWriteError("chunk exceeds Anvil's 255-sector internal payload limit")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(self.path.parent).free < len(record) + DISK_RESERVE_BYTES:
            raise WorldWriteError("insufficient disk for another Anvil chunk while preserving the 64 MiB reserve")
        if not self.path.exists():
            self.path.write_bytes(self.header)
        with self.path.open("r+b") as stream:
            stream.seek(0, os.SEEK_END)
            offset = stream.tell() // SECTOR_BYTES
            if offset >= 1 << 24:
                raise WorldWriteError("region exceeds Anvil's sector-offset limit")
            stream.write(record)
            stream.write(bytes(sectors * SECTOR_BYTES - len(record)))
            stream.flush()
            struct.pack_into(">I", self.header, index * 4, (offset << 8) | sectors)
            stream.seek(index * 4)
            stream.write(self.header[index * 4:index * 4 + 4])

    def compact(self) -> None:
        if not self.path.exists():
            return
        if shutil.disk_usage(self.path.parent).free < self.path.stat().st_size + DISK_RESERVE_BYTES:
            raise WorldWriteError("insufficient disk for atomic Anvil region compaction plus the 64 MiB reserve")
        temporary = self.path.with_suffix(".mca.tmp")
        header = bytearray(8192)
        try:
            with temporary.open("wb") as stream:
                stream.write(header)
                offset = 2
                for index in self.locations():
                    record = self.record(index)
                    assert record is not None
                    sectors = math.ceil(len(record) / SECTOR_BYTES)
                    struct.pack_into(">I", header, index * 4, (offset << 8) | sectors)
                    stream.write(record)
                    stream.write(bytes(sectors * SECTOR_BYTES - len(record)))
                    offset += sectors
                stream.seek(0)
                stream.write(header)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            self.header = header
        finally:
            if temporary.exists():
                temporary.unlink()


class NativeWorldReader:
    """Independent disk decoder for native Java 1.20.4 generated worlds."""

    def __init__(self, world_path: str | Path, *, max_cached_chunks: int = 64) -> None:
        self.world_path = Path(world_path)
        self.max_cached_chunks = max(1, max_cached_chunks)
        self._cache: OrderedDict[tuple[int, int], _Chunk] = OrderedDict()
        self._regions: OrderedDict[tuple[int, int], _Region] = OrderedDict()
        try:
            level = nbtlib.load(self.world_path / "level.dat")
            if int(level["Data"]["DataVersion"]) != JAVA_DATA_VERSION or int(level["Data"]["version"]) != 19133:
                raise WorldWriteError("level.dat is not a Java 1.20.4 Anvil world")
            self.surface_y = _flat_surface_from_level(level, require_grass=False)
        except WorldWriteError:
            raise
        except Exception as exc:
            raise WorldWriteError(f"invalid level.dat: {exc}") from exc

    def chunk(self, cx: int, cz: int) -> _Chunk:
        key = (cx, cz)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        rkey = (cx // 32, cz // 32)
        region = self._regions.get(rkey)
        if region is None:
            region = _Region(self.world_path / "region" / f"r.{rkey[0]}.{rkey[1]}.mca")
            self._regions[rkey] = region
            if len(self._regions) > 32:
                self._regions.popitem(last=False)
        else:
            self._regions.move_to_end(rkey)
        root = region.read((cx & 31) + (cz & 31) * 32)
        if root is None:
            chunk = _new_chunk(cx, cz, self.surface_y)
        else:
            chunk = _decode_chunk(root, cx, cz)
        self._cache[key] = chunk
        while len(self._cache) > self.max_cached_chunks:
            self._cache.popitem(last=False)
        return chunk

    def get_block(self, pos: Vec3) -> BlockState:
        validate_position(pos)
        section = self.chunk(pos.x // 16, pos.z // 16).sections.get(pos.y // 16)
        if section is None:
            return AIR
        index = ((pos.y & 15) << 8) | ((pos.z & 15) << 4) | (pos.x & 15)
        return section.palette[section.indices[index]]

    def iter_blocks(self, *, include_assigned_air: bool = False) -> Iterator[Placement]:
        for path in sorted((self.world_path / "region").glob("*.mca")):
            match = re.fullmatch(r"r\.(-?\d+)\.(-?\d+)\.mca", path.name)
            if match is None:
                raise WorldWriteError(f"invalid Anvil region filename: {path.name}")
            rx, rz = map(int, match.groups())
            region = _Region(path)
            for index in region.locations():
                cx, cz = rx * 32 + (index & 31), rz * 32 + (index >> 5)
                root = region.read(index)
                assert root is not None
                chunk = _decode_chunk(root, cx, cz)
                for sy, section in sorted(chunk.sections.items()):
                    for cell, palette_index in enumerate(section.indices):
                        state = section.palette[palette_index]
                        assigned = section.assigned[cell >> 3] & (1 << (cell & 7))
                        if state != AIR or (include_assigned_air and assigned):
                            yield Placement(Vec3(cx * 16 + (cell & 15), sy * 16 + (cell >> 8), cz * 16 + ((cell >> 4) & 15)), state, "world-readback")


def _create_level_dat(path: Path, *, name: str, spawn: Vec3, surface_y: int | None = None) -> None:
    validate_position(spawn)
    if spawn.y < -63:
        raise WorldWriteError("spawn requires room for a supporting block")
    n = nbtlib
    if surface_y is None:
        layers = [n.Compound({"height": n.Int(1), "block": n.String("minecraft:air")})]
    else:
        _terrain_sections(surface_y)  # Validate before creating any world files.
        layers = [n.Compound({"height": n.Int(1), "block": n.String("minecraft:bedrock")})]
        dirt = surface_y + 63
        if dirt:
            layers.append(n.Compound({"height": n.Int(dirt), "block": n.String("minecraft:dirt")}))
        layers.append(n.Compound({"height": n.Int(1), "block": n.String("minecraft:grass_block")}))
    root = n.File({"Data": n.Compound({
        "DataVersion": n.Int(JAVA_DATA_VERSION), "version": n.Int(19133),
        "Version": n.Compound({"Name": n.String("1.20.4"), "Id": n.Int(JAVA_DATA_VERSION), "Snapshot": n.Byte(0), "Series": n.String("main")}),
        "LevelName": n.String(name), "LastPlayed": n.Long(int(time.time() * 1000)),
        "GameType": n.Int(1), "allowCommands": n.Byte(1), "hardcore": n.Byte(0),
        "Difficulty": n.Byte(0), "DifficultyLocked": n.Byte(0), "initialized": n.Byte(1),
        "SpawnX": n.Int(spawn.x), "SpawnY": n.Int(spawn.y), "SpawnZ": n.Int(spawn.z), "SpawnAngle": n.Float(0),
        "Time": n.Long(6000), "DayTime": n.Long(6000), "clearWeatherTime": n.Int(0),
        "rainTime": n.Int(0), "raining": n.Byte(0), "thunderTime": n.Int(0), "thundering": n.Byte(0),
        "GameRules": n.Compound({key: n.String(value) for key, value in {
            "doDaylightCycle": "false", "doWeatherCycle": "false", "doMobSpawning": "false",
            "doFireTick": "false", "keepInventory": "true", "randomTickSpeed": "0", "spawnRadius": "0",
        }.items()}),
        "DataPacks": n.Compound({"Enabled": n.List[n.String]([n.String("vanilla")]), "Disabled": n.List[n.String]([])}),
        "enabled_features": n.List[n.String]([n.String("minecraft:vanilla")]),
        "WorldGenSettings": n.Compound({
            "seed": n.Long(0), "generate_features": n.Byte(0), "bonus_chest": n.Byte(0),
            "dimensions": n.Compound({
                "minecraft:overworld": n.Compound({
                    "type": n.String("minecraft:overworld"),
                    "generator": n.Compound({
                        "type": n.String("minecraft:flat"),
                        "settings": n.Compound({
                            "biome": n.String("minecraft:plains"), "features": n.Byte(0), "lakes": n.Byte(0),
                            "layers": n.List[n.Compound](layers),
                            "structure_overrides": n.List[n.String]([]),
                        }),
                    }),
                }),
                "minecraft:the_nether": n.Compound({
                    "type": n.String("minecraft:the_nether"),
                    "generator": n.Compound({"type": n.String("minecraft:noise"), "settings": n.String("minecraft:nether"), "biome_source": n.Compound({"type": n.String("minecraft:multi_noise"), "preset": n.String("minecraft:nether")})}),
                }),
                "minecraft:the_end": n.Compound({
                    "type": n.String("minecraft:the_end"),
                    "generator": n.Compound({"type": n.String("minecraft:noise"), "settings": n.String("minecraft:end"), "biome_source": n.Compound({"type": n.String("minecraft:the_end")})}),
                }),
            }),
        }),
    })}, gzipped=True)
    temporary = path / "level.dat.tmp"
    root.save(temporary)
    os.replace(temporary, path / "level.dat")


class WorldWriter(AbstractContextManager["WorldWriter"]):
    def set_block(self, placement: Placement) -> None:
        raise NotImplementedError

    def set_blocks(self, placements: Iterable[Placement]) -> None:
        for placement in placements:
            self.set_block(placement)

    def save(self) -> None:
        raise NotImplementedError

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.save()
        return False


def validate_native_world(world_path: str | Path) -> dict[str, object]:
    """Read every saved chunk and compare its blocks with pre-encoding digests.

    The expected ledger is disk-backed so full-capacity generation does not
    retain billions of placements in Python RAM. A fresh reader is essential:
    validating writer caches would merely compare the input with itself.
    """
    path = Path(world_path)
    ledger = path / "shamaos" / "expected.sqlite3"
    if not ledger.is_file():
        raise WorldWriteError("native world has no expected-block validation ledger")
    reader = NativeWorldReader(path, max_cached_chunks=1)
    connection = sqlite3.connect(f"{ledger.resolve().as_uri()}?mode=ro", uri=True)
    try:
        chunks = 0
        blocks = 0
        for cx, cz, expected, expected_count in connection.execute("SELECT x,z,digest,blocks FROM chunks ORDER BY x,z"):
            chunk = reader.chunk(cx, cz)
            if reader.surface_y is not None:
                for sy, section in chunk.sections.items():
                    terrain_rows = max(0, min(16, reader.surface_y - sy * 16 + 1))
                    if any(section.assigned[:terrain_rows * 32]):
                        raise WorldWriteError(f"generated blocks touch protected grass/ground in chunk ({cx}, {cz})")
            digest, count = chunk.fingerprint()
            if bytes.fromhex(digest) != expected or count != expected_count:
                raise WorldWriteError(f"saved block-state readback mismatch in chunk ({cx}, {cz})")
            chunks += 1
            blocks += count
        found_chunks = 0
        regions = 0
        region_bytes = 0
        for region_path in sorted((path / "region").glob("*.mca")):
            match = re.fullmatch(r"r\.(-?\d+)\.(-?\d+)\.mca", region_path.name)
            if match is None:
                raise WorldWriteError(f"invalid Anvil region filename: {region_path.name}")
            rx, rz = map(int, match.groups())
            region = _Region(region_path)
            regions += 1
            region_bytes += region_path.stat().st_size
            for index in region.locations():
                cx, cz = rx * 32 + (index & 31), rz * 32 + (index >> 5)
                if connection.execute("SELECT 1 FROM chunks WHERE x=? AND z=?", (cx, cz)).fetchone() is None:
                    raise WorldWriteError(f"untracked saved chunk ({cx}, {cz})")
                found_chunks += 1
        if found_chunks != chunks:
            raise WorldWriteError("saved chunk inventory differs from expected ledger")
        return {
            "status": "passed", "method": "independent-anvil-block-state-readback",
            "data_version": JAVA_DATA_VERSION, "chunks": chunks,
            "assigned_blocks": blocks, "regions": regions, "region_bytes": region_bytes,
            "grass_surface_y": reader.surface_y,
            "minimum_build_y": None if reader.surface_y is None else reader.surface_y + 1,
            "terrain_preserved": reader.surface_y is not None,
        }
    finally:
        connection.close()


class NativeWorldWriter(WorldWriter):
    """Create a disposable Java 1.20.4 world using only nbtlib and the stdlib.

    Different states at the same coordinate are always errors, including
    conflicts across cache eviction, periodic saves and resumed invocations.
    Identical state overlaps are allowed for shared terminals/supports. Native
    editing is limited to worlds created here; the optional Amulet backend is
    retained for arbitrary existing saves with entities/other dimensions.
    """

    def __init__(
        self,
        world_path: str | Path,
        *,
        name: str = "ShamaOS Redstone PC",
        spawn: Vec3 = Vec3(-16, 64, -16),
        spawn_platform: bool = True,
        max_cached_chunks: int = 128,
        surface_y: int | None = None,
        dimension: str = "minecraft:overworld",
        version: tuple[str, tuple[int, int, int]] = ("java", (1, 20, 4)),
    ) -> None:
        if dimension != "minecraft:overworld" or version != ("java", (1, 20, 4)):
            raise WorldWriteError("native generation supports Java 1.20.4 overworld only")
        validate_position(spawn)
        if spawn.y < -63 or spawn.y > 318:
            raise WorldWriteError("spawn must leave room for floor and player")
        self.world_path = Path(world_path)
        self._closed = False
        self._chunks: OrderedDict[tuple[int, int], _Chunk] = OrderedDict()
        self._regions: OrderedDict[tuple[int, int], _Region] = OrderedDict()
        self._dirty_regions: set[tuple[int, int]] = set()
        self._state_cache: dict[BlockState, BlockState] = {}
        self.max_cached_chunks = max(1, max_cached_chunks)
        if surface_y is not None:
            _terrain_sections(surface_y)
        self.surface_y = surface_y
        marker = self.world_path / "shamaos" / "world.json"
        fresh = not (self.world_path / "level.dat").exists()
        if fresh and surface_y is not None and spawn_platform and spawn.y - 1 <= surface_y:
            raise WorldWriteError("spawn platform must also be above the grass surface")
        if fresh:
            if self.world_path.exists() and any(self.world_path.iterdir()):
                raise WorldWriteError("new native world destination must be empty")
            self.world_path.mkdir(parents=True, exist_ok=True)
        elif not marker.is_file():
            raise WorldWriteError("native writer refuses an existing unowned save; use the Amulet backend explicitly")
        else:
            try:
                metadata = json.loads(marker.read_text(encoding="utf-8"))
                if metadata.get("format") != "shamaos-native-anvil-v1" or metadata.get("data_version") != JAVA_DATA_VERSION:
                    raise WorldWriteError("unsupported native world metadata")
                actual_surface = NativeWorldReader(self.world_path).surface_y
                if surface_y is not None and surface_y != actual_surface:
                    raise WorldWriteError("requested grass surface differs from the existing world's terrain")
                self.surface_y = actual_surface
                if metadata.get("surface_y") != actual_surface:
                    raise WorldWriteError("native world metadata and grass surface disagree")
            except (ValueError, OSError) as exc:
                raise WorldWriteError(f"invalid native world metadata: {exc}") from exc
        self._lock = (self.world_path / "session.lock").open("a+b")
        try:
            # Lock the same region from byte zero on every platform, including
            # an existing session.lock whose append-mode cursor starts at EOF.
            # Minecraft locks this file too; another process must fail before
            # either the session marker or any world data is changed.
            self._lock.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.lockf(self._lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB,
                            0, 0, os.SEEK_SET)
        except ImportError as exc:
            self._lock.close()
            raise WorldWriteError("native world generation needs Windows or POSIX file locking") from exc
        except OSError as exc:
            self._lock.close()
            raise WorldWriteError("world is locked by another writer or Minecraft") from exc
        self._lock.seek(0)
        self._lock.truncate()
        self._lock.write("☃".encode())
        self._lock.flush()
        if not fresh:
            try:
                validate_native_world(self.world_path)
            except Exception:
                self._lock.close()
                raise
        (self.world_path / "shamaos").mkdir(exist_ok=True)
        if fresh:
            _create_level_dat(self.world_path, name=name, spawn=spawn, surface_y=self.surface_y)
            marker.write_text(json.dumps({
                "format": "shamaos-native-anvil-v1", "data_version": JAVA_DATA_VERSION,
                "minecraft_version": "1.20.4", "spawn": vars(spawn),
                "surface_y": self.surface_y,
            }, indent=2) + "\n", encoding="utf-8")
        self._ledger = sqlite3.connect(self.world_path / "shamaos" / "expected.sqlite3")
        self._ledger.execute("CREATE TABLE IF NOT EXISTS chunks (x INTEGER NOT NULL,z INTEGER NOT NULL,digest BLOB NOT NULL,blocks INTEGER NOT NULL,PRIMARY KEY(x,z)) WITHOUT ROWID")
        self._ledger.commit()
        self.written_placements = 0
        self.identical_overlaps = 0
        self._reports_invalidated = False
        if fresh and spawn_platform:
            for dx in range(-4, 5):
                for dz in range(-4, 5):
                    self.set_block(Placement(spawn.offset(dx, -1, dz), BlockState.of("minecraft:stone"), "spawn-platform"))

    def _region(self, rx: int, rz: int) -> _Region:
        key = (rx, rz)
        if key in self._regions:
            self._regions.move_to_end(key)
            return self._regions[key]
        region = _Region(self.world_path / "region" / f"r.{rx}.{rz}.mca")
        self._regions[key] = region
        if len(self._regions) > 32:
            self._regions.popitem(last=False)
        return region

    def _flush_chunk(self, chunk: _Chunk) -> None:
        if not chunk.dirty:
            return
        digest, count = chunk.fingerprint()
        region = self._region(chunk.x // 32, chunk.z // 32)
        region.write((chunk.x & 31) + (chunk.z & 31) * 32, _chunk_nbt(chunk))
        self._dirty_regions.add((chunk.x // 32, chunk.z // 32))
        self._ledger.execute("INSERT OR REPLACE INTO chunks VALUES (?,?,?,?)", (chunk.x, chunk.z, bytes.fromhex(digest), count))
        chunk.dirty = False

    def _chunk(self, cx: int, cz: int) -> _Chunk:
        key = (cx, cz)
        if key in self._chunks:
            self._chunks.move_to_end(key)
            return self._chunks[key]
        root = self._region(cx // 32, cz // 32).read((cx & 31) + (cz & 31) * 32)
        chunk = _new_chunk(cx, cz, self.surface_y) if root is None else _decode_chunk(root, cx, cz)
        self._chunks[key] = chunk
        if len(self._chunks) > self.max_cached_chunks:
            _, evicted = self._chunks.popitem(last=False)
            self._flush_chunk(evicted)
        return chunk

    def set_block(self, placement: Placement) -> None:
        if self._closed:
            raise WorldWriteError("native world writer is closed")
        pos = placement.pos
        validate_position(pos)
        if self.surface_y is not None and pos.y <= self.surface_y:
            raise WorldWriteError(
                f"placement at {pos} would touch protected grass/ground; "
                f"every generated block must be at or above Y={self.surface_y + 1}"
            )
        state = self._state_cache.get(placement.block)
        if state is None:
            state = canonical_block_state(placement.block)
            self._state_cache[placement.block] = state
        chunk = self._chunk(pos.x // 16, pos.z // 16)
        section = chunk.sections.get(pos.y // 16)
        if section is None:
            section = _Section()
            chunk.sections[pos.y // 16] = section
        index = ((pos.y & 15) << 8) | ((pos.z & 15) << 4) | (pos.x & 15)
        if section.put(index, state, pos, placement.component):
            chunk.dirty = True
            if not self._reports_invalidated:
                for name in ("readback.json", "fixture.json", "runtime_case.json"):
                    (self.world_path / "shamaos" / name).unlink(missing_ok=True)
                self._reports_invalidated = True
        else:
            self.identical_overlaps += 1
        self.written_placements += 1

    def save(self) -> None:
        if self._closed:
            return
        for chunk in self._chunks.values():
            self._flush_chunk(chunk)
        self._chunks.clear()
        for rx, rz in sorted(self._dirty_regions):
            self._region(rx, rz).compact()
        self._dirty_regions.clear()
        self._regions.clear()
        self._ledger.commit()

    def validate(self) -> dict[str, object]:
        self.save()
        report = validate_native_world(self.world_path)
        (self.world_path / "shamaos" / "readback.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        self._reports_invalidated = False
        return report

    def close(self) -> None:
        if self._closed:
            return
        self._ledger.close()
        self._lock.close()
        self._chunks.clear()
        self._regions.clear()
        self._closed = True

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self.save()
        finally:
            self.close()
        return False


class AmuletWorldWriter(WorldWriter):
    """Fast Minecraft Java world writer backed by stable Amulet Core 1.9.x.

    The generator is vastly larger than a normal editor operation, so this
    writer:
      * creates missing far-away chunks explicitly;
      * translates each distinct Java block state only once;
      * registers the universal block once in the level palette;
      * writes runtime palette IDs directly into chunk block arrays;
      * periodically saves and unloads chunk caches.

    Functional block properties are never dropped.
    """

    def __init__(
        self,
        world_path: str | Path,
        *,
        dimension: str = "minecraft:overworld",
        version: tuple[str, tuple[int, int, int]] = ("java", (1, 20, 4)),
    ) -> None:
        self.world_path = Path(world_path)
        if not (self.world_path / "level.dat").exists():
            raise WorldWriteError(f"not a Java world folder: {self.world_path}")

        try:
            import amulet  # type: ignore
        except Exception as exc:
            raise WorldWriteError(
                "world generation requires the optional 'world' dependencies; "
                "install with: pip install -e '.[world]'"
            ) from exc

        try:
            self._world = amulet.load_level(str(self.world_path))
        except Exception as exc:
            raise WorldWriteError(f"failed to open world: {exc}") from exc

        self.dimension = dimension
        self.version = version
        self._closed = False
        self._chunk_cache: dict[tuple[int, int], object] = {}
        self._block_id_cache: dict[BlockState, int] = {}

        try:
            self._translator = self._world.translation_manager.get_version(
                version[0],
                version[1],
            )
        except Exception as exc:
            self._world.close()
            raise WorldWriteError(
                f"Amulet does not support requested block version {version}: {exc}"
            ) from exc

    def _block_id(self, state: BlockState) -> int:
        cached = self._block_id_cache.get(state)
        if cached is not None:
            return cached

        java_block = to_amulet_block(state)
        try:
            universal_block, block_entity, extra = (
                self._translator.block.to_universal(java_block)
            )
        except Exception as exc:
            raise WorldWriteError(
                f"failed translating block state {state}: {exc}"
            ) from exc

        if block_entity is not None or extra:
            raise WorldWriteError(
                f"block state {state} requires context/block entity translation; "
                "ShamaOS hardware only supports context-free redstone blocks"
            )

        try:
            block_id = self._world.block_palette.get_add_block(universal_block)
        except Exception as exc:
            raise WorldWriteError(
                f"failed registering block state {state}: {exc}"
            ) from exc

        self._block_id_cache[state] = int(block_id)
        return int(block_id)

    def _chunk(self, cx: int, cz: int):
        key = (cx, cz)
        chunk = self._chunk_cache.get(key)
        if chunk is not None:
            return chunk

        try:
            if not self._world.has_chunk(cx, cz, self.dimension):
                self._world.create_chunk(cx, cz, self.dimension)
            chunk = self._world.get_chunk(cx, cz, self.dimension)
        except Exception as exc:
            raise WorldWriteError(
                f"failed creating/loading chunk ({cx}, {cz}): {exc}"
            ) from exc

        self._chunk_cache[key] = chunk
        return chunk

    def set_block(self, placement: Placement) -> None:
        p = placement.pos

        # Java 1.20.4 overworld build limits.
        if not -64 <= p.y <= 319:
            raise WorldWriteError(
                f"placement outside Java 1.20.4 build height at {p}: "
                f"{placement.block.name}"
            )
        if not -29_999_984 <= p.x <= 29_999_983 or not -29_999_984 <= p.z <= 29_999_983:
            raise WorldWriteError(
                f"placement outside safe Java world border at {p}: "
                f"{placement.block.name}"
            )

        cx = p.x // 16
        cz = p.z // 16
        ox = p.x - cx * 16
        oz = p.z - cz * 16

        chunk = self._chunk(cx, cz)
        block_id = self._block_id(placement.block)

        try:
            chunk.blocks[ox, p.y, oz] = block_id
            chunk.changed = True
        except Exception as exc:
            raise WorldWriteError(
                f"failed writing {placement.block.name} at {p}: {exc}"
            ) from exc

    def save(self) -> None:
        if self._closed:
            return
        try:
            self._world.save()
            # Saved chunks can be unloaded to bound RAM use during enormous
            # streaming builds. The next placement recreates/loads as needed.
            self._world.unload()
            self._chunk_cache.clear()
        except Exception as exc:
            raise WorldWriteError(f"world save failed: {exc}") from exc

    def close(self) -> None:
        if self._closed:
            return
        try:
            self._world.close()
        finally:
            self._closed = True
            self._chunk_cache.clear()

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self.save()
        finally:
            self.close()
        return False
