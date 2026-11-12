from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
import math
import struct

from .machine_spec import FLASH_BYTES, FLASH_WRITE_LIMIT_PERCENT


MAGIC = b"SHAMAFS1"
VERSION = 1
DEFAULT_FLASH_BYTES = FLASH_BYTES
DEFAULT_BLOCK_SIZE = 256
MAX_FILES = 64
ENTRY_SIZE = 64
SUPER_FMT = "<8sIIIIIIII"
ENTRY_FMT = "<BBHIIIIB43s"
# Single-entry redo journal, stored in the unused tail of superblock 0. Data
# extents are copy-on-write. The commit marker is written only after payload and
# checksum; recovery replays it then reconstructs allocation from file entries.
JOURNAL_OFFSET = 64
JOURNAL_MAGIC = 0x314E524A  # little-endian JRN1
JOURNAL_ENTRY_OFFSET = 72
JOURNAL_CHECKSUM_OFFSET = 136
JOURNAL_CHECKSUM_SEED = 0x53464A31


class FileType(IntEnum):
    DATA = 0
    TXT = 1
    ASM = 2
    BIN = 3
    SYS = 4
    LOG = 5
    CFG = 6
    DIR = 7


class FlashWriteProtectedError(OSError):
    """The owner's flash reserve would be consumed by a mutation."""


class BootBundleReadOnlyError(PermissionError):
    """The private loader bundle must retain its generated location and bytes."""


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
        self._handles: dict[int, tuple[int, str]] = {}
        self._next_handle = 1
        self._used = [False] * self.block_count
        for i in range(self.data_start):
            self._used[i] = True

    @staticmethod
    def _normalize_name(name: str) -> str:
        # Names on flash are canonical paths relative to the virtual root.
        # Accept a single leading slash for callers, never traversal aliases.
        # Spaces are real path bytes in the hardware ABI. Do not strip them,
        # or discard leading/trailing controls before validation: doing so would
        # rename valid in-world files and accept paths the controller rejects.
        if name.startswith("/"):
            name = name[1:]
        encoded = name.encode("utf-8")
        if not name or len(encoded) > 43:
            raise ValueError("path must be 1..43 UTF-8 bytes")
        if any(ord(c) < 32 for c in name) or "\\" in name:
            raise ValueError("path contains a control character or backslash")
        if any(part in ("", ".", "..") for part in name.split("/")):
            raise ValueError("path contains an empty or traversal component")
        return name

    def _check_parent(self, name: str) -> None:
        parent, sep, _ = name.rpartition("/")
        if sep:
            entry = self._files.get(parent)
            if entry is None:
                raise FileNotFoundError(parent)
            if entry.file_type != FileType.DIR:
                raise NotADirectoryError(parent)

    def list_files(self, directory: str | None = None) -> list[FileEntry]:
        entries = self._files.values()
        if directory is not None:
            prefix = "" if directory in ("", "/") else self._normalize_name(directory)
            if prefix:
                if self.stat(prefix).file_type != FileType.DIR:
                    raise NotADirectoryError(prefix)
                prefix += "/"
            entries = (e for e in entries if e.name.startswith(prefix)
                       and "/" not in e.name[len(prefix):])
        return [FileEntry(**vars(e)) for e in sorted(entries, key=lambda e: e.name.lower())]

    def exists(self, name: str) -> bool:
        return self._normalize_name(name) in self._files

    def read_file(self, name: str) -> bytes:
        entry = self.stat(name)
        if entry.file_type == FileType.DIR:
            raise IsADirectoryError(name)
        return entry.data

    def stat(self, name: str) -> FileEntry:
        name = self._normalize_name(name)
        try:
            e = self._files[name]
        except KeyError as exc:
            raise FileNotFoundError(name) from exc
        return FileEntry(**vars(e))

    def mkdir(self, path: str) -> None:
        self.create_file(path, file_type=FileType.DIR)

    def open_file(self, name: str, *, owner: int = 0) -> int:
        name = self._normalize_name(name)
        self.stat(name)
        handle = self._next_handle
        self._next_handle += 1
        self._handles[handle] = (owner, name)
        return handle

    def _handle_name(self, handle: int, owner: int) -> str:
        try:
            actual_owner, name = self._handles[handle]
        except KeyError as exc:
            raise OSError("invalid or closed file handle") from exc
        if actual_owner != owner:
            raise PermissionError("file handle belongs to another app")
        return name

    def close_file(self, handle: int, *, owner: int = 0) -> None:
        self._handle_name(handle, owner)
        del self._handles[handle]

    def close_owner(self, owner: int) -> int:
        handles = [h for h, (o, _) in self._handles.items() if o == owner]
        for handle in handles:
            del self._handles[handle]
        return len(handles)

    def transfer_handle(self, handle: int, new_owner: int, *, owner: int = 0) -> None:
        self._handles[handle] = (new_owner, self._handle_name(handle, owner))

    def read_handle(self, handle: int, count: int, *, offset: int = 0, owner: int = 0) -> bytes:
        if count < 0 or offset < 0:
            raise ValueError("negative file range")
        return self.read_file(self._handle_name(handle, owner))[offset:offset + count]

    def write_handle(self, handle: int, data: bytes, *, owner: int = 0) -> None:
        self.write_file(self._handle_name(handle, owner), data)

    def truncate(self, name: str, size: int) -> None:
        if size < 0 or size > self.flash_bytes:
            raise ValueError("invalid truncate size")
        old = self.read_file(name)
        self._check_mutable(self._normalize_name(name))
        if size == 0:
            # Explicit reclamation remains possible in protected mode. Like
            # DELETE, this needs only the metadata transaction, not new data.
            name = self._normalize_name(name)
            entry = self._files[name]
            self._mark(entry.start_block, entry.block_count, False)
            self.generation += 1
            self._files[name] = FileEntry(name, entry.file_type, b"", entry.flags,
                                          0, 0, self.generation)
            return
        self.write_file(name, old[:size].ljust(size, b"\x00"))

    @property
    def write_limit_bytes(self) -> int:
        return (self.block_count * FLASH_WRITE_LIMIT_PERCENT // 100) * self.block_size

    @property
    def write_protected(self) -> bool:
        return self.used_bytes >= self.write_limit_bytes

    def _check_write_reservation(self, blocks: int = 0) -> None:
        # Count the complete temporary COW extent while the old file remains
        # authoritative. Reject before touching allocation, metadata or data.
        if self.write_protected or self.used_bytes + blocks * self.block_size > self.write_limit_bytes:
            raise FlashWriteProtectedError(
                "flash 95% write protection: delete files or truncate to zero to free space; "
                "copy-on-write saves also need temporary space below the limit"
            )

    @staticmethod
    def _check_mutable(name: str) -> None:
        if name == "boot.bundle":
            raise BootBundleReadOnlyError("boot.bundle is private loader data and cannot be changed")

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
        self._check_parent(name)
        file_type = FileType(file_type)
        if file_type == FileType.DIR and data:
            raise IsADirectoryError(name)
        if name in self._files:
            raise FileExistsError(name)
        if len(self._files) >= MAX_FILES:
            raise OSError("file table full")
        blocks = math.ceil(len(data) / self.block_size) if data else 0
        self._check_write_reservation(blocks)
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
        name = self._normalize_name(name)
        if name not in self._files:
            self.create_file(name, data, FileType.DATA if file_type is None else file_type)
            return

        old = self._files[name]
        self._check_mutable(name)
        if old.file_type == FileType.DIR or file_type == FileType.DIR:
            raise IsADirectoryError(name)
        new_blocks = math.ceil(len(data) / self.block_size) if data else 0
        self._check_write_reservation(new_blocks)

        # Allocate new extent before freeing old, mirroring a safer save sequence.
        start = self._find_extent(new_blocks)
        self._mark(start, new_blocks, True)

        self.generation += 1
        replacement = FileEntry(
            name=name,
            file_type=old.file_type if file_type is None else FileType(file_type),
            data=bytes(data),
            flags=old.flags,
            start_block=start,
            block_count=new_blocks,
            generation=self.generation,
        )
        self._files[name] = replacement
        self._mark(old.start_block, old.block_count, False)

    def rename(self, old: str, new: str) -> None:
        old, new = self._normalize_name(old), self._normalize_name(new)
        self._check_mutable(old)
        self._check_parent(new)
        if old == new:
            self.stat(old)
            return
        if new in self._files:
            raise FileExistsError(new)
        entry = self.stat(old)
        if entry.file_type == FileType.DIR and any(n.startswith(old + "/") for n in self._files):
            raise OSError("cannot rename a nonempty directory")
        self._check_write_reservation()
        self.generation += 1
        del self._files[old]
        entry.name, entry.generation = new, self.generation
        self._files[new] = entry
        for handle, (owner, name) in tuple(self._handles.items()):
            if name == old:
                self._handles[handle] = (owner, new)

    def delete(self, name: str) -> None:
        name = self._normalize_name(name)
        entry = self.stat(name)
        self._check_mutable(name)
        if entry.file_type == FileType.DIR and any(n.startswith(name + "/") for n in self._files):
            raise OSError("directory is not empty")
        del self._files[name]
        self._mark(entry.start_block, entry.block_count, False)
        self.generation += 1
        for handle, (_, path) in tuple(self._handles.items()):
            if path == name:
                del self._handles[handle]

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

    @staticmethod
    def journal_checksum(index: int, entry: bytes) -> int:
        if len(entry) != ENTRY_SIZE:
            raise ValueError("invalid journal entry length")
        checksum = JOURNAL_CHECKSUM_SEED ^ index
        for word in struct.unpack("<16I", entry):
            checksum ^= word
        return checksum

    @classmethod
    def recover_image(cls, image: bytes) -> bytes:
        """Replay a committed metadata entry and rebuild the allocation bitmap.

        Each acknowledged DMA word is the transaction interruption boundary.
        The journal checksum detects incomplete/torn payloads; a malformed
        committed journal fails explicitly instead of inventing file metadata.
        """
        fs = cls.deserialize(image)
        return fs.serialize()

    @classmethod
    def deserialize(cls, image: bytes) -> "ShamaFS":
        if len(image) < 256:
            raise ValueError("image too small")
        (magic, version, block_size, block_count, bitmap_start, table_start,
         data_start, generation, clean) = struct.unpack_from(SUPER_FMT, image, 0)
        if magic != MAGIC or version != VERSION:
            raise ValueError("unsupported ShamaFS image")
        if block_size < 256 or len(image) != block_size * block_count:
            raise ValueError("flash image length mismatch")
        fs = cls(len(image), block_size)
        if (fs.bitmap_start, fs.table_start, fs.data_start) != (bitmap_start, table_start, data_start):
            raise ValueError("metadata geometry mismatch")
        fs.generation, fs.clean = generation, bool(clean)
        working = bytearray(image)
        marker, journal_index = struct.unpack_from("<II", image, JOURNAL_OFFSET)
        if marker:
            if marker != JOURNAL_MAGIC or journal_index >= MAX_FILES:
                raise ValueError("invalid committed journal header")
            payload = bytes(image[JOURNAL_ENTRY_OFFSET:JOURNAL_ENTRY_OFFSET + ENTRY_SIZE])
            checksum, = struct.unpack_from("<I", image, JOURNAL_CHECKSUM_OFFSET)
            if checksum != cls.journal_checksum(journal_index, payload):
                raise ValueError("committed journal checksum mismatch")
            off = table_start * block_size + journal_index * ENTRY_SIZE
            working[off:off + ENTRY_SIZE] = payload
            fs.clean = True

        # The bitmap is reconstructible metadata. Never trust leaked allocations
        # or a half-updated bitmap left by an interrupted copy-on-write operation.
        fs._used = [i < fs.data_start for i in range(fs.block_count)]
        table_off = fs.table_start * fs.block_size
        for idx in range(MAX_FILES):
            fields = struct.unpack_from(ENTRY_FMT, working, table_off + idx * ENTRY_SIZE)
            in_use = fields[0]
            if not in_use:
                continue
            if in_use != 1:
                raise ValueError("invalid file-table used flag")
            _, ftype, flags, size, start, count, fgen, name_len, name_raw = fields
            if not 1 <= name_len <= 43:
                raise ValueError("invalid stored path length")
            try:
                name = name_raw[:name_len].decode("utf-8")
                if cls._normalize_name(name) != name:
                    raise ValueError("noncanonical stored path")
                file_type = FileType(ftype)
            except (UnicodeError, ValueError) as exc:
                raise ValueError("invalid file metadata") from exc
            if name in fs._files:
                raise ValueError("duplicate stored path")
            if count != math.ceil(size / block_size):
                raise ValueError("size/extent count mismatch")
            if count == 0 and start != 0:
                raise ValueError("empty file has data extent")
            if count:
                if start < fs.data_start or start + count > fs.block_count:
                    raise ValueError("file extent outside data region")
                if any(fs._used[start:start + count]):
                    raise ValueError("overlapping file extents")
                fs._mark(start, count, True)
            if file_type == FileType.DIR and size:
                raise ValueError("directory contains data")
            data_off = start * fs.block_size
            data = bytes(working[data_off:data_off + size]) if count else b""
            fs._files[name] = FileEntry(name, file_type, data, flags, start, count, fgen)
            fs.generation = max(fs.generation, fgen)
        for name in fs._files:
            fs._check_parent(name)
        return fs
