from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
import math
import struct


MAGIC = b"SHAMAFS1"
VERSION = 1
DEFAULT_FLASH_BYTES = 4 << 20
DEFAULT_BLOCK_SIZE = 256
MAX_FILES = 64
ENTRY_SIZE = 64
SUPER_FMT = "<8sIIIIIIII"
ENTRY_FMT = "<BBHIIIIB43s"


class FileType(IntEnum):
    DATA = 0
    TXT = 1
    ASM = 2
    BIN = 3
    SYS = 4
    LOG = 5
    CFG = 6


@dataclass
class FileEntry:
    name: str
    file_type: FileType
    data: bytes
    flags: int = 0
    start_block: int = 0
    block_count: int = 0
    generation: int = 0


class ShamaFS:
    def __init__(self, flash_bytes: int = DEFAULT_FLASH_BYTES, block_size: int = DEFAULT_BLOCK_SIZE):
        if flash_bytes <= 0 or block_size <= 0 or flash_bytes % block_size:
            raise ValueError("flash size must be a positive multiple of block size")
        self.flash_bytes = flash_bytes
        self.block_size = block_size
        self.block_count = flash_bytes // block_size
        self.bitmap_start = 1
        self.bitmap_blocks = math.ceil(math.ceil(self.block_count / 8) / block_size)
        self.table_start = self.bitmap_start + self.bitmap_blocks
        self.table_blocks = math.ceil(MAX_FILES * ENTRY_SIZE / block_size)
        self.data_start = self.table_start + self.table_blocks
        if self.data_start >= self.block_count:
            raise ValueError("flash too small for metadata")
        self.generation = 1
        self.clean = True
        self._files: dict[str, FileEntry] = {}
        self._used = [False] * self.block_count
        for i in range(self.data_start):
            self._used[i] = True

    @staticmethod
    def _normalize_name(name: str) -> str:
        name = name.strip()
        encoded = name.encode("utf-8")
        if not name or len(encoded) > 43:
            raise ValueError("filename must be 1..43 UTF-8 bytes")
        if "\x00" in name:
            raise ValueError("filename contains NUL")
        return name

    def list_files(self) -> list[FileEntry]:
        return sorted(self._files.values(), key=lambda e: e.name.lower())

    def exists(self, name: str) -> bool:
        return name in self._files

    def read_file(self, name: str) -> bytes:
        try:
            return self._files[name].data
        except KeyError as exc:
            raise FileNotFoundError(name) from exc

    def stat(self, name: str) -> FileEntry:
        try:
            e = self._files[name]
        except KeyError as exc:
            raise FileNotFoundError(name) from exc
        return FileEntry(**vars(e))

    def _find_extent(self, blocks: int) -> int:
        if blocks == 0:
            return 0
        run = 0
        start = self.data_start
        for i in range(self.data_start, self.block_count):
            if not self._used[i]:
                if run == 0:
                    start = i
                run += 1
                if run >= blocks:
                    return start
            else:
                run = 0
        raise OSError("flash full / no contiguous extent large enough")

    def _mark(self, start: int, count: int, used: bool) -> None:
        if count == 0:
            return
        if start < self.data_start or start + count > self.block_count:
            raise ValueError("invalid data extent")
        for i in range(start, start + count):
            self._used[i] = used

    def create_file(self, name: str, data: bytes = b"", file_type: FileType = FileType.DATA) -> None:
        name = self._normalize_name(name)
        if name in self._files:
            raise FileExistsError(name)
        if len(self._files) >= MAX_FILES:
            raise OSError("file table full")
        blocks = math.ceil(len(data) / self.block_size) if data else 0
        start = self._find_extent(blocks)
        self._mark(start, blocks, True)
        self.generation += 1
        self._files[name] = FileEntry(
            name=name,
            file_type=FileType(file_type),
            data=bytes(data),
            start_block=start,
            block_count=blocks,
            generation=self.generation,
        )

    def write_file(self, name: str, data: bytes, *, file_type: FileType | None = None) -> None:
        if name not in self._files:
            self.create_file(name, data, file_type or FileType.DATA)
            return

        old = self._files[name]
        new_blocks = math.ceil(len(data) / self.block_size) if data else 0

        # Allocate new extent before freeing old, mirroring a safer save sequence.
        start = self._find_extent(new_blocks)
        self._mark(start, new_blocks, True)

        self.generation += 1
        replacement = FileEntry(
            name=name,
            file_type=file_type or old.file_type,
            data=bytes(data),
            flags=old.flags,
            start_block=start,
            block_count=new_blocks,
            generation=self.generation,
        )
        self._files[name] = replacement
        self._mark(old.start_block, old.block_count, False)

    def rename(self, old: str, new: str) -> None:
        new = self._normalize_name(new)
        if new in self._files:
            raise FileExistsError(new)
        try:
            entry = self._files.pop(old)
        except KeyError as exc:
            raise FileNotFoundError(old) from exc
        self.generation += 1
        entry.name = new
        entry.generation = self.generation
        self._files[new] = entry

    def delete(self, name: str) -> None:
        try:
            entry = self._files.pop(name)
        except KeyError as exc:
            raise FileNotFoundError(name) from exc
        self._mark(entry.start_block, entry.block_count, False)
        self.generation += 1

    @property
    def used_bytes(self) -> int:
        return sum(1 for b in self._used if b) * self.block_size

    @property
    def free_bytes(self) -> int:
        return self.flash_bytes - self.used_bytes

    def serialize(self) -> bytes:
        image = bytearray(self.flash_bytes)
        clean = 1 if self.clean else 0
        superblock = struct.pack(
            SUPER_FMT,
            MAGIC,
            VERSION,
            self.block_size,
            self.block_count,
            self.bitmap_start,
            self.table_start,
            self.data_start,
            self.generation,
            clean,
        )
        image[: len(superblock)] = superblock

        bitmap_bytes = bytearray(self.bitmap_blocks * self.block_size)
        for i, used in enumerate(self._used):
            if used:
                bitmap_bytes[i // 8] |= 1 << (i % 8)
        boff = self.bitmap_start * self.block_size
        image[boff : boff + len(bitmap_bytes)] = bitmap_bytes

        entries = self.list_files()
        for idx, entry in enumerate(entries):
            name_b = entry.name.encode("utf-8")
            packed = struct.pack(
                ENTRY_FMT,
                1,
                int(entry.file_type),
                entry.flags & 0xFFFF,
                len(entry.data),
                entry.start_block,
                entry.block_count,
                entry.generation,
                len(name_b),
                name_b.ljust(43, b"\x00"),
            )
            off = self.table_start * self.block_size + idx * ENTRY_SIZE
            image[off : off + ENTRY_SIZE] = packed

            if entry.block_count:
                data_off = entry.start_block * self.block_size
                image[data_off : data_off + len(entry.data)] = entry.data

        return bytes(image)

    @classmethod
    def deserialize(cls, image: bytes) -> "ShamaFS":
        if len(image) < struct.calcsize(SUPER_FMT):
            raise ValueError("image too small")
        (
            magic,
            version,
            block_size,
            block_count,
            bitmap_start,
            table_start,
            data_start,
            generation,
            clean,
        ) = struct.unpack_from(SUPER_FMT, image, 0)
        if magic != MAGIC or version != VERSION:
            raise ValueError("unsupported ShamaFS image")
        if len(image) != block_size * block_count:
            raise ValueError("flash image length mismatch")

        fs = cls(len(image), block_size)
        if (fs.bitmap_start, fs.table_start, fs.data_start) != (
            bitmap_start,
            table_start,
            data_start,
        ):
            raise ValueError("metadata geometry mismatch")
        fs.generation = generation
        fs.clean = bool(clean)
        fs._files.clear()
        fs._used = [False] * fs.block_count

        bitmap_len = fs.bitmap_blocks * fs.block_size
        bitmap = image[
            fs.bitmap_start * fs.block_size :
            fs.bitmap_start * fs.block_size + bitmap_len
        ]
        for i in range(fs.block_count):
            fs._used[i] = bool(bitmap[i // 8] & (1 << (i % 8)))

        table_off = fs.table_start * fs.block_size
        for idx in range(MAX_FILES):
            off = table_off + idx * ENTRY_SIZE
            fields = struct.unpack_from(ENTRY_FMT, image, off)
            in_use = fields[0]
            if not in_use:
                continue
            _, ftype, flags, size, start, count, fgen, name_len, name_raw = fields
            name = name_raw[:name_len].decode("utf-8")
            data_off = start * fs.block_size
            data = bytes(image[data_off : data_off + size]) if count else b""
            fs._files[name] = FileEntry(
                name=name,
                file_type=FileType(ftype),
                data=data,
                flags=flags,
                start_block=start,
                block_count=count,
                generation=fgen,
            )

        return fs
