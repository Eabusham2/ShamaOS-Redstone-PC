"""Bounded-memory Litematica v6 export of the native world's owned blocks.

Format reference: maruohon/litematica, pre-rewrite/fabric/1.20.x,
LitematicaSchematic.java and LitematicaBitArray.java. Each occupied chunk section
is a separate, tightly bounded region. Empty space between distant circuits is
not expanded into a gigantic dense array. This module does not certify runtime.
"""
from __future__ import annotations

from contextlib import closing
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import struct

import nbtlib as n

from .worldio import AIR, NativeWorldReader, WorldWriteError, _Region, _signed_long


def owned_sections(world: Path):
    """Use the independent saved-world decoder; never generator input caches."""
    ledger = world / "shamaos" / "expected.sqlite3"
    reader = NativeWorldReader(world, max_cached_chunks=1)
    with closing(sqlite3.connect(f"{ledger.resolve().as_uri()}?mode=ro", uri=True)) as db:
        for cx, cz in db.execute("SELECT x,z FROM chunks ORDER BY x,z"):
            chunk = reader.chunk(cx, cz)
            root = _Region(world / "region" / f"r.{cx // 32}.{cz // 32}.mca").read((cx & 31) + (cz & 31) * 32)
            if root is None:
                raise WorldWriteError("schematic source is missing an owned chunk")
            ticks = {field: root.get(field, []) for field in ("block_ticks", "fluid_ticks")}
            for entries in ticks.values():
                seen = set()
                for entry in entries:
                    x, y, z = (int(entry[axis]) for axis in "xyz")
                    if (x // 16, z // 16) != (cx, cz) or (x, y, z) in seen:
                        raise WorldWriteError("schematic cannot preserve duplicate or out-of-chunk pending ticks")
                    seen.add((x, y, z))
                    section = chunk.sections.get(y // 16)
                    index = ((y & 15) << 8) | ((z & 15) << 4) | (x & 15)
                    if section is None or not section.assigned[index >> 3] & (1 << (index & 7)):
                        raise WorldWriteError("schematic pending tick lies outside owned machine blocks")
            for sy, section in sorted(chunk.sections.items()):
                cells = [i for i in range(4096) if section.assigned[i >> 3] & (1 << (i & 7))]
                if cells:
                    yield cx, sy, cz, section, cells, ticks


def machine_bounds(world: Path) -> dict:
    minimum, maximum = [30_000_000] * 3, [-30_000_000] * 3
    assigned = nonair = regions = 0
    for cx, sy, cz, section, cells, _ticks in owned_sections(world):
        regions += 1
        assigned += len(cells)
        for i in cells:
            position = (cx * 16 + (i & 15), sy * 16 + (i >> 8), cz * 16 + ((i >> 4) & 15))
            for axis, value in enumerate(position):
                minimum[axis] = min(minimum[axis], value)
                maximum[axis] = max(maximum[axis], value)
            nonair += section.palette[section.indices[i]] != AIR
    if not assigned:
        raise WorldWriteError("cannot export an empty machine")
    return {"min": minimum, "max": maximum, "assigned_blocks": assigned,
            "non_air_blocks": nonair, "regions": regions}


def _vector(values):
    return n.Compound({axis: n.Int(value) for axis, value in zip("xyz", values)})


def _tag(stream, name, value):
    encoded = name.encode("utf-8")
    stream.write(bytes([value.tag_id]) + struct.pack(">H", len(encoded)) + encoded)
    value.write(stream)


def _packed(values, bits):
    """Litematica's continuous bit stream (NOT Anvil's padded long packing)."""
    words = [0] * ((len(values) * bits + 63) // 64)
    for i, value in enumerate(values):
        word, shift = divmod(i * bits, 64)
        words[word] |= (value << shift) & ((1 << 64) - 1)
        if shift + bits > 64:
            words[word + 1] |= value >> (64 - shift)
    return [_signed_long(value) for value in words]


def _region(cx, sy, cz, section, cells, origin, ticks):
    lo = [min((i & 15, i >> 8, (i >> 4) & 15)[a] for i in cells) for a in range(3)]
    hi = [max((i & 15, i >> 8, (i >> 4) & 15)[a] for i in cells) for a in range(3)]
    size = [hi[a] - lo[a] + 1 for a in range(3)]
    pos = [cx * 16 + lo[0], sy * 16 + lo[1], cz * 16 + lo[2]]
    palette, lookup, values, entities = [AIR], {AIR: 0}, [], []
    for y in range(lo[1], hi[1] + 1):
        for z in range(lo[2], hi[2] + 1):
            for x in range(lo[0], hi[0] + 1):
                index = y * 256 + z * 16 + x
                owned = section.assigned[index >> 3] & (1 << (index & 7))
                state = section.palette[section.indices[index]] if owned else AIR
                if state not in lookup:
                    lookup[state] = len(palette)
                    palette.append(state)
                values.append(lookup[state])
                if state.name == "minecraft:comparator":
                    entities.append(n.Compound({"id": n.String(state.name),
                        "x": n.Int(x - lo[0]), "y": n.Int(y - lo[1]), "z": n.Int(z - lo[2]),
                        "OutputSignal": n.Int(15 if dict(state.properties)["powered"] == "true" else 0)}))
    encoded_palette = []
    for state in palette:
        tag = n.Compound({"Name": n.String(state.name)})
        if state.properties:
            tag["Properties"] = n.Compound({key: n.String(value) for key, value in state.properties})
        encoded_palette.append(tag)
    pending = {}
    for field, name, target in (("block_ticks", "PendingBlockTicks", "Block"),
                                ("fluid_ticks", "PendingFluidTicks", "Fluid")):
        entries = []
        for order, tick in enumerate(ticks[field]):
            if int(tick["y"]) // 16 != sy:
                continue
            entries.append(n.Compound({target: n.String(tick["i"]), "Priority": n.Int(tick["p"]),
                "Time": n.Int(tick["t"]), "SubTick": n.Long(order),
                **{axis: n.Int(int(tick[axis]) - pos[index]) for index, axis in enumerate("xyz")}}))
        pending[name] = n.List[n.Compound](entries)
    result = n.Compound({
        "Position": _vector([pos[a] - origin[a] for a in range(3)]), "Size": _vector(size),
        "BlockStatePalette": n.List[n.Compound](encoded_palette),
        "BlockStates": n.LongArray(_packed(values, max(2, (len(palette) - 1).bit_length()))),
        "TileEntities": n.List[n.Compound](entities), "Entities": n.List[n.Compound]([]),
        **pending,
    })
    return result


def _semantic_region(digest, name, region):
    """Decode the exported bit stream independently before hashing its meaning."""
    palette = [(str(v["Name"]), sorted((str(k), str(x)) for k, x in v.get("Properties", {}).items()))
               for v in region["BlockStatePalette"]]
    size = [int(region["Size"][a]) for a in "xyz"]
    count = size[0] * size[1] * size[2]
    bits = max(2, (len(palette) - 1).bit_length())
    words = [int(v) & ((1 << 64) - 1) for v in region["BlockStates"]]
    if len(words) != (count * bits + 63) // 64:
        raise WorldWriteError("invalid Litematica packed length")
    digest.update(name.encode() + b"\0")
    digest.update(json.dumps([int(region["Position"][a]) for a in "xyz"]).encode())
    digest.update(json.dumps(size).encode())
    for i in range(count):
        bit = i * bits
        value = words[bit // 64] >> (bit % 64)
        if bit % 64 + bits > 64:
            value |= words[bit // 64 + 1] << (64 - bit % 64)
        index = value & ((1 << bits) - 1)
        if index >= len(palette):
            raise WorldWriteError("invalid Litematica palette index")
        digest.update(json.dumps(palette[index], separators=(",", ":")).encode() + b"\n")
    digest.update(region["TileEntities"].snbt().encode())
    digest.update(region["PendingBlockTicks"].snbt().encode())
    digest.update(region["PendingFluidTicks"].snbt().encode())


def _read_named(stream):
    tag = stream.read(1)
    if len(tag) != 1:
        raise WorldWriteError("truncated schematic")
    if tag == b"\0":
        return 0, ""
    raw = stream.read(2)
    if len(raw) != 2:
        raise WorldWriteError("truncated schematic name")
    length = struct.unpack(">H", raw)[0]
    name = stream.read(length)
    if len(name) != length:
        raise WorldWriteError("truncated schematic name")
    return tag[0], name.decode("utf-8")


def verify_litematic(path: Path, expected_digest: str, expected_regions: int):
    """Reopen gzip/NBT and compare all decoded regions, with bounded memory."""
    digest, count = hashlib.sha256(), 0
    with gzip.open(path, "rb") as stream:
        if _read_named(stream) != (10, ""):
            raise WorldWriteError("invalid schematic root")
        while True:
            tag, name = _read_named(stream)
            if not tag:
                break
            if name == "Regions":
                if tag != 10:
                    raise WorldWriteError("invalid schematic regions")
                while True:
                    kind, region_name = _read_named(stream)
                    if not kind:
                        break
                    if kind != 10:
                        raise WorldWriteError("invalid schematic region")
                    _semantic_region(digest, region_name, n.Compound.parse(stream))
                    count += 1
            else:
                value = n.tag.Base.get_tag(tag).parse(stream)
                if name == "Version" and int(value) != 6:
                    raise WorldWriteError("unexpected schematic version")
        if stream.read(1):
            raise WorldWriteError("trailing schematic bytes")
    if count != expected_regions or digest.hexdigest() != expected_digest:
        raise WorldWriteError("schematic readback differs from the saved machine")


def export_litematic(world: Path, output: Path, *, name: str, bounds: dict | None = None) -> dict:
    """Export only owned machine blocks, excluding the protected superflat soil."""
    bounds = machine_bounds(world) if bounds is None else bounds
    digest = hashlib.sha256()
    volume = 0
    with output.open("xb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as stream:
        stream.write(b"\x0a\x00\x00")
        _tag(stream, "Version", n.Int(6))
        _tag(stream, "SubVersion", n.Int(1))
        _tag(stream, "MinecraftDataVersion", n.Int(3700))
        stream.write(b"\x0a\x00\x07Regions")
        for cx, sy, cz, section, cells, ticks in owned_sections(world):
            region = _region(cx, sy, cz, section, cells, bounds["min"], ticks)
            region_name = f"c{cx}_{sy}_{cz}"
            _semantic_region(digest, region_name, region)
            _tag(stream, region_name, region)
            volume += int(region["Size"]["x"]) * int(region["Size"]["y"]) * int(region["Size"]["z"])
        stream.write(b"\0")
        if bounds["non_air_blocks"] > 2_147_483_647 or volume > 2_147_483_647:
            raise WorldWriteError("the complete schematic exceeds Litematica's signed 32-bit metadata counts")
        _tag(stream, "Metadata", n.Compound({
            "Name": n.String(name), "Author": n.String("ShamaOS"),
            "Description": n.String("Exact owned blocks from the independently read-back ShamaOS world."),
            "RegionCount": n.Int(bounds["regions"]), "TotalBlocks": n.Int(bounds["non_air_blocks"]),
            "TotalVolume": n.Int(volume), "TimeCreated": n.Long(0), "TimeModified": n.Long(0),
            "EnclosingSize": _vector([bounds["max"][a] - bounds["min"][a] + 1 for a in range(3)]),
        }))
        stream.write(b"\0")
    verify_litematic(output, digest.hexdigest(), bounds["regions"])
    return {"format": "litematica-v6", "data_version": 3700, "status": "passed",
            "method": "independent-gzip-nbt-bitstream-readback", "semantic_sha256": digest.hexdigest(),
            "origin": bounds["min"], **bounds}
