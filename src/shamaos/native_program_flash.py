"""Read ShamaFS program files from the physical storage repeaters of closed saves."""
from functools import lru_cache
import hashlib
from pathlib import Path
import struct

from .flashfs import FileType, JOURNAL_OFFSET, ShamaFS
from .hardware.memory import MemoryBankSpec, repeater
from .hardware.memory_backbone import StackedMemoryTileSpec, stacked_bank_origin
from .machine_spec import BANK_BYTES, FLASH_BYTES
from .model import BlockState, Vec3
from .worldio import JAVA_DATA_VERSION, NativeWorldReader, WorldWriteError, _Region, _unpack_indices, validate_position


PROGRAM_SOURCE = b"LDI R1 42\nHLT\n"
# Independent instruction words from rtl/tb_soc.sv:create_assemble_run.
PROGRAM_BINARY = struct.pack("<3I", 0x03100000, 42, 0x01000000)
_FILES = {"Z.ASM": (FileType.ASM, PROGRAM_SOURCE), "Z.BIN": (FileType.BIN, PROGRAM_BINARY)}


class _FlashReader:
    """Read only the addressed section's unmodified Java 1.20.4 block states.

    Java may retain light-only sections below or above build height and may
    save pending oscillator ticks. Neither affects querying a saved repeater.
    """
    def __init__(self, world):
        self.world = Path(world)
        level = self.world / "level.dat"
        self.stamps = {level: self._stamp(level)}
        NativeWorldReader(self.world)  # Validate level.dat version and overworld.
        self.region = lru_cache(maxsize=16)(self._region)
        self.section = lru_cache(maxsize=64)(self._section)

    @staticmethod
    def _stamp(path):
        s = path.stat()
        return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns

    def _region(self, rx, rz):
        path = self.world / "region" / f"r.{rx}.{rz}.mca"
        self.stamps.setdefault(path, self._stamp(path))
        return _Region(path)

    def _section(self, cx, cz, sy):
        root = self.region(cx // 32, cz // 32).read((cx & 31) + (cz & 31) * 32)
        if (root is None or int(root["DataVersion"]) != JAVA_DATA_VERSION or str(root["Status"]) != "minecraft:full"
                or (int(root["xPos"]), int(root["zPos"])) != (cx, cz)):
            raise WorldWriteError(f"Missing or incorrect Java flash chunk at {(cx, cz)}.")
        sections = [s for s in root["sections"] if int(s["Y"]) == sy]
        if len(sections) != 1 or "block_states" not in sections[0]:
            raise WorldWriteError(f"Missing or ambiguous saved flash section at {(cx, sy, cz)}.")
        states = sections[0]["block_states"]
        palette = [BlockState.of(str(p["Name"]), **{k: str(v) for k, v in p.get("Properties", {}).items()})
                   for p in states["palette"]]
        if not 1 <= len(palette) <= 4096:
            raise WorldWriteError("Invalid block-state palette size in a flash section.")
        if len(palette) == 1 and "data" not in states:
            return palette, None
        indices = _unpack_indices(states["data"], 4096, max(4, (len(palette) - 1).bit_length()), len(palette))
        return palette, indices

    def get_block(self, point):
        validate_position(point)
        palette, indices = self.section(point.x // 16, point.z // 16, point.y // 16)
        index = ((point.y & 15) * 16 + (point.z & 15)) * 16 + (point.x & 15)
        return palette[0 if indices is None else indices[index]]

    def check_files(self):
        if any(self._stamp(path) != before for path, before in self.stamps.items()):
            raise WorldWriteError("The world changed during physical flash readback.")


def _flash_banks(manifest):
    bank = MemoryBankSpec()
    banks = {}
    for section in manifest["physical_sections"]:
        if not section["name"].startswith("flash-tile-"):
            continue
        count, first = section["banks"], section["first_bank_index"]
        if (section["kind"] != "stacked-memory-tile" or type(first) is not int or first < 0
                or type(count) is not int or not 1 <= count <= 32
                or section["bank_words"] != [bank.words] * count or section["bytes"] != count * bank.bytes):
            raise WorldWriteError("Invalid production flash tile geometry.")
        tile = StackedMemoryTileSpec(bank=bank, banks=count, layer_pitch=section["layer_pitch"])
        origin = Vec3(**section["origin"])
        validate_position(origin)
        for layer in range(count):
            index = first + layer
            if index in banks:
                raise WorldWriteError("Overlapping flash bank indices.")
            banks[index] = stacked_bank_origin(origin, tile, layer)
    count = FLASH_BYTES // BANK_BYTES
    if set(banks) != set(range(count)) or len(set(banks.values())) != count:
        raise WorldWriteError("The program check needs every distinct bank of the 200 KiB physical flash.")
    ordered = [banks[index] for index in range(count)]
    for i, left in enumerate(ordered):
        for right in ordered[i + 1:]:
            dx, dz = abs(left.x - right.x), abs(left.z - right.z)
            if (left.y == right.y and dx % bank.bit_pitch == 0 and dz % bank.word_pitch == 0
                    and dx <= (bank.word_bits - 1) * bank.bit_pitch and dz <= (bank.words - 1) * bank.word_pitch):
                raise WorldWriteError("Flash bank mappings alias saved storage repeaters.")
    return ordered


def _read_flash_bank(reader, origin, bank):
    """Sample the actual locked east-output storage repeaters in address order."""
    low, high = repeater("east", locked=True), repeater("east", locked=True, powered=True)
    contents = bytearray()
    for row in range(bank.words):
        start = origin.offset(2, bank.cell_y, 3 + row * bank.word_pitch)
        word = 0
        for bit in range(bank.word_bits):
            at = start.offset(dx=bit * bank.bit_pitch)
            state = reader.get_block(at)
            if state == high:
                word |= 1 << bit
            elif state != low:
                raise WorldWriteError(f"Expected a locked flash storage repeater at {at}.")
        contents.extend(word.to_bytes(bank.word_bits // 8, "little"))
    return contents


def _program_flash(world, manifest, power):
    reader = _FlashReader(world)
    switch = reader.get_block(power)
    if switch.name != "minecraft:lever" or dict(switch.properties).get("powered") != "false":
        raise WorldWriteError("Read the program files from an unpowered saved machine.")
    bank = MemoryBankSpec()
    image = b"".join(_read_flash_bank(reader, origin, bank) for origin in _flash_banks(manifest))
    if int.from_bytes(image[JOURNAL_OFFSET:JOURNAL_OFFSET + 4], "little"):
        raise WorldWriteError("Saved flash still contains a pending transaction.")
    reader.check_files()
    fs = ShamaFS.deserialize(image)
    entries = {entry.name: entry for entry in fs.list_files() if entry.name in _FILES}
    bitmap = fs.bitmap_start * fs.block_size
    for entry in entries.values():
        for block in range(entry.start_block, entry.start_block + entry.block_count):
            if not image[bitmap + block // 8] & (1 << (block % 8)):
                raise WorldWriteError(f"Saved flash bitmap does not allocate the {entry.name} extent.")
    return hashlib.sha256(image).hexdigest(), entries


def require_new_program_files(world, manifest, power):
    fingerprint, existing = _program_flash(world, manifest, power)
    if existing:
        raise WorldWriteError(f"Program creation requires absent targets: {', '.join(sorted(existing))} already exists.")
    return fingerprint


def inspect_saved_program(original: Path, saved: Path, manifest: dict, power: Vec3) -> dict:
    """Read the two flash images independently, without changing either save."""
    if original.resolve() == saved.resolve():
        raise WorldWriteError("Keep the original world and inspect a separate powered-off save.")
    before = require_new_program_files(original, manifest, power)
    after, entries = _program_flash(saved, manifest, power)
    checks = {}
    for name, (kind, expected) in _FILES.items():
        entry = entries.get(name)
        if entry is None or entry.file_type != kind or entry.data != expected:
            raise WorldWriteError(f"The saved {name} file is missing or differs in type, length or content.")
        checks[name] = {"type": kind.name, "bytes": len(entry.data), "hex": entry.data.hex(),
                        "start_block": entry.start_block, "block_count": entry.block_count}
    return {"scope": "Powered-off physical flash file comparison", "status": "expected-program-files-observed",
            "original_world": str(original.resolve()), "saved_world": str(saved.resolve()),
            "original_flash_sha256": before, "saved_flash_sha256": after, "files": checks,
            "bytes_read_per_world": FLASH_BYTES, "complete_machine_runtime_verified": False}
