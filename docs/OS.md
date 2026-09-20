# ShamaOS

## Design goal

ShamaOS is a real small operating environment stored in flash and loaded into RAM. It exists to make the redstone PC useful without regenerating the world every time a new program or text document is created.

## Boot

Power button flow:

1. assert reset.
2. initialize CPU architectural state.
3. initialize cache bookkeeping.
4. initialize GPU and clear/push display.
5. probe flash superblock.
6. recover/validate filesystem metadata.
7. copy kernel/shell image from flash into RAM.
8. initialize allocator and system-call table.
9. load font/app directory metadata.
10. launch Desktop.

A boot error is displayed through a minimal boot-text path if possible.

## Desktop

Desktop shows application icons/list plus live status:

- RAM used / total.
- cache used / total.
- flash used / total.
- power/run state.

Required launchers:

- Bitcoin Miner
- Editor
- File Explorer
- System Monitor

Baseline utility launchers:

- Terminal
- Calculator
- Paint
- Settings

## Universal controller actions

Controller layout includes D-pad plus:

- Confirm/Open
- Back/Cancel
- Home
- Exit
- Editor
- File Explorer

Rules:

- **Home:** return to Desktop, closing the foreground app using normal lifecycle rules.
- **Exit:** close foreground app.
- **Editor:** close foreground app then launch Editor.
- **File Explorer:** close foreground app then launch File Explorer.
- **Back:** app-specific back/cancel.
- **Confirm:** activate selected control.

The keyboard is used for text and assembly entry.

## Application lifecycle

### Launch

1. resolve executable metadata in flash.
2. allocate RAM for text/data/stack/heap.
3. load executable/code pages.
4. create app handle table.
5. initialize window/UI state.
6. transfer control to app entry point.

### Exit

1. request graceful close.
2. flush explicit app file writes.
3. close handles.
4. release app GPU objects/temporary buffers.
5. invalidate/free app cache accounting.
6. clear/free app RAM.
7. return to shell.

The kernel, filesystem state and shared GUI buffers remain resident.

## Editor

Final app name: **Editor**.

Home/actions:

- New Text File
- New Program
- Open
- Save
- Save As
- Rename
- Delete
- Run/Assemble (program source)
- Exit

### New Text File

Creates/edit a UTF-8/limited-ASCII text file depending final character hardware. The filesystem stores bytes; the UI may restrict unsupported glyph input.

### New Program

Creates Shama Assembly source.

The Editor can:

- assemble source.
- show line/error number.
- save source.
- save executable image/compiled output.
- run successful program.
- reopen existing source.

Deleting from Editor requires a confirmation dialog.

## File Explorer

Shows:

- name.
- type.
- size.
- optional modified generation/counter.

Actions for text:

- Open/Read
- Edit
- Rename
- Delete

Actions for program/source:

- Run
- Edit
- Rename
- Delete

Selecting Edit closes File Explorer and launches Editor with that file.

Delete always asks for confirmation.

## Bitcoin Miner

Controls:

- Start
- Stop
- Reset run/generation
- Previous saved run
- Next saved run
- Exit

Live fields:

- status.
- generation/run number.
- nonce.
- attempts.
- current or most recent hash.
- target.
- VALID/invalid state.
- CPU/SHA state.
- hashes/unit-time counter as measured by machine timer.
- RAM/cache/flash usage summary.

On Stop:

- current nonce/run state may be saved.
- CPU mining loop stops cleanly.
- GUI remains responsive.

On valid result:

- result record is persisted to flash.
- screen highlights success.
- history can be browsed.

## System Monitor

Displays actual accounting:

- RAM total/used/free.
- per-process RAM where space permits.
- cache occupancy/dirty/valid lines.
- flash total/used/free.
- GPU queue occupancy.
- CPU/SHA busy state.
- timer/performance counters.

## Terminal

Provides a compact command interface for:

- `ls`
- `cat`
- `rm`
- `mv`
- `run`
- `edit`
- `mem`
- `flash`
- `gpu`
- `miner`

Terminal commands call the same syscalls as GUI apps.

## Syscalls

System calls use `SYS id` with argument registers. ABI baseline:

- `r1-r6`: arguments.
- `r1`: return value.
- `r2`: optional secondary return/error detail.
- negative/signed error convention is implementation-defined; exact numeric codes live in source.

Required families:

### Process/UI
- EXIT
- YIELD
- GET_EVENT
- GET_KEY
- GET_CONTROLLER
- DRAW_TEXT
- DRAW_RECT
- GPU_SUBMIT
- MESSAGE_BOX

### Memory
- ALLOC
- FREE
- RAM_USAGE
- CACHE_USAGE
- CACHE_FLUSH_APP

### Files
- FILE_CREATE
- FILE_OPEN
- FILE_CLOSE
- FILE_READ
- FILE_WRITE
- FILE_TRUNCATE
- FILE_RENAME
- FILE_DELETE
- FILE_STAT
- FILE_LIST
- FLASH_USAGE

### Time/debug
- GET_TIME
- GET_COUNTER
- DEBUG_PRINT

### Miner
- MINER_LOG_RESULT
- MINER_LOAD_HISTORY
- MINER_SAVE_STATE

## Destructive confirmations

The OS shell owns confirmation dialogs so apps do not implement inconsistent deletion UX.

A delete request is not committed until the user selects Confirm/Delete on a distinct dialog. Back/Cancel aborts with no filesystem mutation.

## Resource indicators

The desktop and System Monitor read live kernel counters, not fake percentages.

- RAM used = allocated pages/blocks + resident kernel/shared buffers.
- cache used = valid cache lines/entries.
- flash used = allocated filesystem blocks + metadata.
