# Shama Flash Filesystem

ShamaFS stores files and directories in the machine's persistent flash. The
runtime controller is `rtl/shama_fs_accel.sv`; `src/shamaos/flashfs.py` builds and
reads the same image format. Runtime file operations execute in hardware. The
host model also provides convenient inspection methods; its Python object
handles are not the hardware's numeric handle ABI.

## Default image geometry

The current hardware accepts this version-1 geometry:

| Region | Blocks | Byte offset | Size |
|---|---:|---:|---:|
| Superblock and transaction journal | 0 | `0x000000` | 256 bytes |
| Allocation bitmap | 1 | `0x000100` | 256 bytes (250 meaningful) |
| File table | 2–17 | `0x000200` | 4,096 bytes |
| File data | 18–1,999 | `0x001200` | 507,392 bytes |

Flash is **500 KiB**, divided into 2,000 blocks of 256 bytes. The table contains
64 fixed-size entries, including directories. Metadata reserves 4,608 bytes;
installed files consume additional data blocks. Used space includes reserved
metadata and allocated file blocks, so it is not the sum of logical file sizes.
There is no second superblock or redundant metadata copy in this format.

The 2,000-bit bitmap needs 63 32-bit controller words. Only the low 16 bits of
its final word represent real blocks; the remaining bits are outside the
physical flash and must never be allocated or counted as free capacity.

The host constructor can create other geometries for tests. That does not make
them bootable on the default controller: mount validates the fixed hardware
geometry before accepting an image.

## Superblock

All multibyte integers are unsigned and little-endian. The first 40 bytes use
Python layout `<8sIIIIIIII`:

| Offset | Bytes | Field |
|---:|---:|---|
| 0 | 8 | Magic `SHAMAFS1` |
| 8 | 4 | Format version: 1 |
| 12 | 4 | Block size: 256 |
| 16 | 4 | Block count: 2,000 |
| 20 | 4 | Bitmap start block: 1 |
| 24 | 4 | Table start block: 2 |
| 28 | 4 | Data start block: 18 |
| 32 | 4 | Image generation |
| 36 | 4 | Clean flag |

Generation and clean are emitted by the host image serializer. Hardware
recovery uses the transaction journal and validates every live entry; it does
not depend on an automatically maintained clean-shutdown flag or a checksum of
the whole superblock. The tail of this block holds the journal described below.

## File entries and types

Each 64-byte entry uses `<BBHIIIIB43s`:

| Offset | Bytes | Field |
|---:|---:|---|
| 0 | 1 | In-use flag |
| 1 | 1 | Type |
| 2 | 2 | Flags |
| 4 | 4 | Logical size in bytes |
| 8 | 4 | First data block |
| 12 | 4 | Allocated block count |
| 16 | 4 | Entry generation |
| 20 | 1 | Name length in bytes |
| 21 | 43 | Canonical relative path bytes |

| Value | Type | Use |
|---:|---|---|
| 0 | DATA | Generic bytes |
| 1 | TXT | Text document |
| 2 | ASM | Assembly source |
| 3 | BIN | Executable image |
| 4 | SYS | Boot/OS data |
| 5 | LOG | Persistent log |
| 6 | CFG | Settings or saved state |
| 7 | DIR | Directory |

A file occupies one contiguous extent. Its block count is `ceil(size/256)`.
Empty files and directories have size, first block and block count all zero.
A directory uses a table entry but no data block. The hardware increments the
entry generation on committed content/metadata replacement; it is not a wall
clock timestamp.

## Paths and directories

Names are full paths relative to a virtual root, for example
`documents/notes.txt`. The complete encoded path must be **1–43 bytes**. Callers
supply a NUL terminator in a buffer of at least 44 bytes. The API accepts one
leading slash and removes it; stored names never begin with `/`.
The longest such input needs 45 bytes including the optional slash and NUL;
every byte read remains checked against RAM bounds. LIST emits the canonical
name without that slash and therefore needs only 44 bytes.

Paths are case-sensitive. Parent directories must already exist as DIR entries.
Empty components, `.` and `..` components, control bytes below 32, and backslash
are invalid. Spaces are ordinary name characters. The physical keyboard/font
supports printable ASCII; use ASCII paths when editing through the current GUI.

Explorer lists the immediate children of its current directory and keeps nested
children within their parent. Its new-folder and rename prompts accept a name
relative to that current directory. An empty directory can be renamed/deleted.
Renaming or deleting a nonempty directory returns the busy error; moving a
whole directory tree is not implemented.

## Runtime handles and operations

The [syscall ABI](SYSCALLS.md) specifies IDs, registers, errors and metadata
packing. Important operational rules are:

- CREATE is exclusive and returns an opened handle. It does not replace an
  existing name.
- OPEN returns `table_index + 1`. Each entry has an ownership bit for each app
  ID, so the caller must own that handle before accessing it. Repeated OPEN by
  one owner is not reference-counted.
- LIST accepts a table index, returns metadata, and copies the name plus NUL.
  It does **not** open the file. Unused slots can appear between live entries.
- READ takes `(handle, destination, length)`, always starts at byte zero, and
  returns the smaller of requested length and file size. There is no implicit
  cursor; r4 is ignored.
- WRITE takes `(handle, source, length)` and replaces the entire content.
  TRUNCATE retains a prefix or zero-fills an expansion.
- RENAME changes the path after checking collisions and the new parent.
  DELETE removes the entry after the UI has obtained confirmation.
- CLOSE removes the current owner's access. FILE_TRANSFER explicitly moves
  ownership for Explorer-to-Editor handoff. Normal app cleanup closes its
  remaining handles only after persistence succeeds.
- FS_SYNC is a fence for the serialized storage controller. Earlier mutations
  have completed before it returns; it does not maintain a separate write-back
  cache.

Ordinary API calls enforce ownership and RAM bounds. This is resource management
within a single trusted machine, not memory isolation against arbitrary CPU
stores. An invalid or unopened handle fails explicitly. CPU flash stores are
also blocked by the global 95% protection signal; ordinary data writes should
use the filesystem API so allocation and journaling stay consistent.

## 95% write protection

The configured threshold is **1,900 of 2,000 blocks**, or **486,400 bytes
(475 KiB / 95%)**, including metadata. Before reserving a copy-on-write extent,
the controller checks current allocation plus the complete temporary extent.
A reservation above the limit returns **`E_PROTECTED = -8`** before any bitmap,
file data or journal write. A transaction admitted exactly at the limit can
finish; subsequent write/create/rename requests are rejected. Existing files
remain readable, and the OS presents a visible storage warning.

Explicit DELETE and TRUNCATE-to-zero remain available to reclaim space. Mount
recovery can replay the metadata journal and rebuild allocation even when an
imported image is already above the threshold. Reclaiming enough space releases
protection. A zero-length WRITE is still an ordinary write request; callers
must explicitly choose the reclamation operation when protection is active.

The usage count can include a temporary new extent during a permitted save.
The OS reports protection after the serialized operation completes or when a
request is refused, avoiding a transient-reservation warning for a save that
successfully returns below the threshold.

## Transaction and recovery protocol

Mutation uses copy-on-write data plus a single-entry redo journal. The journal
occupies otherwise unused bytes in block 0:

| Offset | Bytes | Meaning |
|---:|---:|---|
| 64 | 4 | Commit marker `0x314E524A` (`JRN1`), or zero |
| 68 | 4 | Destination table index |
| 72 | 64 | Complete replacement entry |
| 136 | 4 | Checksum |

The checksum is `0x53464A31 XOR table_index XOR each_of_the_16_entry_words`.
It protects the journal payload; it is not a checksum of file data.

For a content replacement, the controller:

1. Reserves a new contiguous extent while retaining the old extent.
2. Writes the complete new data, including required zero-fill for TRUNCATE.
3. Writes journal index, replacement entry and checksum.
4. Writes the commit marker.
5. Copies the replacement entry into the file table.
6. Clears the commit marker.
7. Reclaims the old extent and returns success.

Create, rename and delete use the same entry commit mechanism; delete journals
an unused replacement entry. The controller finishes one operation before
accepting the next.

On mount it validates geometry, checks/replays any committed journal, then
reconstructs allocation from all live entries. It rejects invalid extents,
overlapping allocations, invalid paths, duplicates and missing parents. A
partial uncommitted payload does not replace the old entry; a valid committed
payload can replay a partially copied table entry. Bitmap reconstruction reclaims
unreferenced extents left by an interruption.

Tests interrupt transactions at acknowledged memory-word boundaries and verify
that recovery yields the complete old or complete new content. These tests do
not claim protection from arbitrary physical corruption of a word, nor from a
bad data bit that the format does not checksum. An invalid image is rejected;
boot halts with the error in the debug registers instead of auto-formatting
persistent storage.

Copy-on-write temporarily needs room for both versions. A save can therefore
fail even when the final file would fit after deleting its old copy. Contiguous
allocation can also fail despite enough total free bytes. Such failures retain
the last valid file and return an error; the GUI retains unsaved source.

## Source, executables and persistent app data

ASM and BIN are separate files. Editor saves source before assembly and replaces
a BIN only after successful assembly. Diagnostic failures and destination-type
collisions preserve the previous executable. Editor can hold 65,535 content
bytes, enough for every shipped app source; it explicitly refuses larger files.
Assembly output and saved binaries can contain up to 16,380 bytes. The executable
cache is **8,192 bytes**; larger binaries are rejected for execution with a
capacity-recovery notice before the copy can wrap or overwrite another region.

TXT contents are plain bytes. Settings uses a versioned 16-byte `settings.cfg`
record, restored by Settings and Paint. The canvas itself is temporary. Miner
uses the installed `miner-history.log` and `miner-state.cfg` files; their exact
history/state layouts and generations are documented in [MINER.md](MINER.md).

The supplied image contains real `documents`, `programs`, `projects`, `logs`, and
`config` directory entries. Boot, shipped app source/binary files and the two
miner persistence files retain their established root names for ABI
compatibility; directory existence does not imply that every installed file has
been relocated into a directory.

## Behavioral evidence

`rtl/tb_fs_accel.sv` exercises the actual controller, ownership, ranges,
directories, full-storage rejection, transactional recovery and metadata
validation. `tests/test_flashfs.py` checks host image roundtrips and malformed
images. Firmware execution tests cover complete source editing and file handoff.
The integrated SoC test runs real CPU/services/filesystem/kernel/GPU/input logic
through edit, automatic save, shutdown, cache erasure, reboot and reopening.
Its RAM/flash/VRAM devices use bounded wait-state test models; physical memory
cells and generated-world execution have separate validation gates.
