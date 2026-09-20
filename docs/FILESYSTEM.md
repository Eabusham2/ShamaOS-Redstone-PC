# Shama Flash Filesystem

## Purpose

A redstone filesystem must be simple, deterministic and easy to recover. ShamaFS is a compact block-based filesystem designed for flash-like persistent redstone storage.

## Default geometry

- flash size: 1 MiB.
- block size: 256 bytes.
- total blocks: 4096.
- superblock: block 0.
- redundant metadata/superblock copy: block 1.
- allocation bitmap: following reserved block(s).
- file table: fixed reserved region.
- data blocks: remaining blocks.

Geometry is configurable and encoded in the superblock.

## Superblock fields

- magic: `SHAMAFS1`.
- filesystem version.
- block size.
- block count.
- file-entry count.
- bitmap start/count.
- file-table start/count.
- data start.
- clean-shutdown flag.
- generation counter.
- metadata checksum.

## File entry

Baseline fixed-size entry contains:

- in-use flag.
- file type.
- flags.
- name length.
- name bytes.
- size in bytes.
- first block or extent index.
- block count.
- generation/modified counter.
- optional checksum.

Initial implementation may use contiguous extents for simple hardware. Fragmented/extents can be added without changing user-facing syscalls if the on-disk version changes.

## File types

- `TXT` — text document.
- `ASM` — assembly source.
- `BIN` — executable/program image.
- `SYS` — OS/boot image.
- `LOG` — miner/system log.
- `CFG` — settings/config.
- generic data type.

## Required operations

- create.
- open.
- close.
- read.
- write.
- truncate.
- rename.
- delete.
- stat.
- list.
- free/used accounting.

## Save behavior

Editor saves should avoid destroying the last valid file if power is lost mid-save.

Preferred sequence:

1. allocate new data blocks.
2. write new data.
3. verify/checksum.
4. write/update file entry with incremented generation.
5. release old blocks.
6. flush metadata copy.
7. mark filesystem clean.

A smaller first hardware implementation may use a simpler two-phase metadata flag while preserving the same safety principle.

## Delete

Deletion:

1. UI confirmation occurs before syscall.
2. file entry is marked pending/delete transaction.
3. data blocks are released.
4. file entry is cleared.
5. allocation metadata is persisted.

## Rename

Rename changes metadata only if name length fits the entry. If names are stored externally in a later version, the version field distinguishes formats.

## Program files

Editor keeps source and executable separately:

```text
hello.asm
hello.bin
```

A source save does not silently overwrite the executable unless assembly succeeds.

A Run action:

1. assemble source.
2. if errors, show line/error and keep old executable.
3. if successful, write/update `.bin`.
4. launch new binary.

## Text files

`.txt` files are plain byte sequences. UI character repertoire is constrained by available font/input, but the on-flash format is not tied to lamp glyph geometry.

## Miner history

Recommended path/name convention if directories are not implemented:

- `miner-history.log`
- `miner-state.cfg`

If directories are implemented later:

- `/miner/history.log`
- `/miner/state.cfg`

Each result record includes:

- run/generation.
- nonce.
- hash.
- target.
- attempt count.
- machine timer/counter.
- status.

## Reference implementation

`src/shamaos/flashfs.py` is the executable format/reference model. The OS and redstone storage controller must match it.
