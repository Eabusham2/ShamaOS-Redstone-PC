# ShamaOS system call ABI

`SYS symbol` uses the IDs in [`isa.py`](../src/shamaos/isa.py). Arguments are
`r1..r6`; results replace `r1` and `r2`. Ordinary returning syscalls preserve
`r3..r15`. No general-purpose register is implicitly a stack pointer: the CPU
has separate architectural data/call stacks. An app replacement resets CPU app
state and resumes the new executable at PC 0.

## Process, input, and memory

| ID | Symbol | Arguments / result |
|---|---|---|
| `001` | SYS_EXIT | Save and close the foreground app, return to Desktop |
| `002` | SYS_YIELD | Cooperative idle boundary; returns 0 |
| `003` | SYS_GET_EVENT | Consume next event; `r1=0` when empty |
| `004` | SYS_GET_TIME | Raw-clock tick count, low/high words in r1/r2 |
| `005` | SYS_GET_COUNTER | Same system tick counter; CPU retired/cycle counters use the ISA counter instruction |
| `006` | SYS_APP_LAUNCH | r1 app ID; r2 bit 0 explicitly requests forced discard, normally 0 |
| `007` | SYS_APP_EXIT_FOREGROUND | Same normal close path as SYS_EXIT |
| `008` | SYS_BOOT_MOUNT | Mount/recover/validate persistent ShamaFS |
| `009` | SYS_BOOT_LOAD_OS | Load resident kernel and Desktop from the executable bundle |
| `00A` | SYS_APP_EVENT | Process an app's packed universal event in r1 |
| `00B` | SYS_LIFECYCLE_STATUS | r1 last close error; r2 low nibble target app, bit 4 closing for shutdown |
| `00C` | SYS_SHUTDOWN | Complete persistence/cleanup before permitting clock gating |
| `010` | SYS_ALLOC | r1 requested bytes; return zeroed allocation base or error |
| `011` | SYS_FREE | r1 exact allocation base; clear and release allocation |
| `012` | SYS_RAM_USAGE | r1 used bytes, r2 total bytes (1,048,576) |
| `013` | SYS_CACHE_USAGE | r1 used executable fast-memory bytes, r2 total (16,384) |
| `014` | SYS_CACHE_FLUSH_APP | Copy active executable fast memory to its RAM backing; retain executable contents; return copied bytes |
| `030` | SYS_GET_KEY | Consume a queued key event and return its byte, otherwise 0 |
| `031` | SYS_GET_CONTROLLER | Current/presented 10-bit controller state |
| `032` | SYS_INPUT_STATUS | r1 bits 0–7 queued count, bit 8 sticky overflow, bit 9 keyboard Shift state |

A packed event has event code in bits 10–17 and key byte in bits 18–25.
Controller bits occupy bits 26–35, so the top four bits are in r2. Events are
`01` key, `10` Up, `11` Down, `12` Left, `13` Right, `14` A, `15` B,
`16` Home, `17` Exit, `18` Editor, and `19` Files. Universal controls are serviced
by hardware even if the foreground app does not poll.

App IDs are Desktop 0, Editor 1, Files 2, Miner 3, Monitor 4, Terminal 5,
Calculator 6, Paint 7, and Settings 8. Staged user code runs as app 15.

The foreground heap is `0x10000..0xAFFFF`: 160 pages of 4 KiB, with up to 16 live
allocation records. Allocation rounds up to pages, initializes them to zero,
and reports reserved pages in usage. Free/exit clears them before reuse.
Close/save errors preserve the live app and appear in `SYS_LIFECYCLE_STATUS`.
A caller must intentionally set the forced-discard flag; normal GUI paths use 0.

## Filesystem

Paths are NUL-terminated strings in RAM; use a buffer of at least 44 bytes for a
43-byte path plus terminator. Handles belong to the foreground app. LIST returns
metadata, **not an opened handle**; OPEN before reading, writing, renaming,
deleting, or transferring a listed file.

| ID | Symbol | Arguments / successful result |
|---|---|---|
| `020` | SYS_FILE_CREATE | r1 path pointer, r2 file type; exclusive create, return opened handle |
| `021` | SYS_FILE_OPEN | r1 path pointer; return handle |
| `022` | SYS_FILE_CLOSE | r1 handle; remove this app's open ownership |
| `023` | SYS_FILE_READ | r1 handle, r2 destination, r3 requested length; read from byte 0, return bytes copied |
| `024` | SYS_FILE_WRITE | r1 handle, r2 source, r3 length; replace complete content transactionally, return bytes written |
| `025` | SYS_FILE_TRUNCATE | r1 handle, r2 new length; retain prefix, zero-fill expansion |
| `026` | SYS_FILE_RENAME | r1 handle, r2 new path pointer |
| `027` | SYS_FILE_DELETE | r1 handle; GUI confirmation must already have occurred |
| `028` | SYS_FILE_STAT | r1 handle; return packed metadata in r1 and size in r2 |
| `029` | SYS_FILE_LIST | r1 table index 0–63, r2 name destination; return metadata/size, copy name plus NUL |
| `02A` | SYS_FLASH_USAGE | r1 allocated bytes, r2 total bytes (4,194,304) |
| `02B` | SYS_FILE_TRANSFER | r1 handle, r2 new owner app ID 0–15; transfer an open handle before app replacement |
| `02C` | SYS_FS_SYNC | Complete prior serialized storage work; return 0 |

READ has **three arguments**. r4 is ignored; there is no implicit seek offset or
handle cursor. READ copies `min(requested length, file size)`. WRITE replaces the
whole file. All byte transfers retain CPU-style byte strobes. Mutation success
is returned only after its journal/metadata work and reclamation complete.

Metadata r1 has type in bits 0–7, name-byte length in 8–15, and starting block in
16–31. r2 is the file size. An unused LIST slot returns zero metadata; scan all
64 slots rather than assuming an unused slot marks the end. Types are DATA 0,
TXT 1, ASM 2, BIN 3, SYS 4, LOG 5, CFG 6, and DIR 7. CREATE with DIR creates an
empty directory. See [the filesystem format](FILESYSTEM.md) for path/transaction
rules and nonempty-directory restrictions.

Filesystem errors return `r2=0xFFFFFFFF` and the signed code in r1:

| r1 | Meaning |
|---|---|
| -1 | Invalid/unopened handle or unavailable operation |
| -2 | Invalid buffer/length/type range |
| -3 | No free table slot or suitable contiguous storage extent |
| -4 | Destination already exists |
| -5 | Filesystem metadata/I/O validation failure |
| -6 | Invalid path or missing/non-directory parent |
| -7 | Busy/nonempty directory |

For STAT/LIST, a nonzero r2 is normally the size, not an error. Check specifically
for `0xFFFFFFFF`. File contents and metadata remain intact on rejected operations.
The handle implementation is not a security boundary against arbitrary CPU
memory writes, but ordinary calls enforce ownership and buffer bounds.

## Graphics and UI

| ID | Symbol | Arguments / behavior |
|---|---|---|
| `040` | SYS_GPU_SUBMIT | r1 opcode, r2..r6 GPU arguments 0..4; enqueue |
| `041` | SYS_DRAW_TEXT | r1 GPU text offset, r2 length, r3 X, r4 Y |
| `042` | SYS_DRAW_RECT | r1 X, r2 Y, r3 width, r4 height, r5 bit 0 filled |
| `043` | SYS_MESSAGE_BOX | Text drawing convenience with the same arguments as DRAW_TEXT; firmware supplies layout/interaction |
| `044` | SYS_GPU_FENCE | Wait for completion of queued GPU work; return fence count |
| `045` | SYS_UI_REDRAW | Desktop view, r1 selected app |
| `046` | SYS_UI_STATUSBAR | Refresh actual usage counters without erasing the foreground canvas |
| `047` | SYS_UI_FILE_LIST | r1 selected table slot, r2 mode, r3 confirmation choice, r4 error |
| `048` | SYS_UI_EDITOR_VIEW | r1 mode, r2 action/confirmation choice, r3 diagnostic |
| `049` | SYS_UI_MINER_VIEW | Miner view; app supplies full data pages |
| `04A` | SYS_UI_MONITOR_VIEW | r1 cycle count, r2 retired instructions |
| `04B` | SYS_UI_TERMINAL_VIEW | r1 unknown-command flag |
| `04C` | SYS_UI_PAINT_VIEW | Paint view |
| `04D` | SYS_UI_SETTINGS_VIEW | r1 selected action, r2 cursor flags, r3 save-error flag |
| `04E` | SYS_UI_CALCULATOR_VIEW | Calculator view |

Text offsets refer to GPU text staging, not arbitrary RAM pointers. Applications
write staging through MMIO before drawing and must respect queued-use lifetime.
Full UI views present and fence before returning. [GPU.md](GPU.md) defines all
commands, presentation/clip/color/cursor semantics, bounds, and queue status.

## Assembly and execution

`SYS_ASSEMBLE` (`060`) takes r1 source RAM pointer, r2 source byte length,
r3 output RAM pointer, and r4 output capacity. Source is at most 65,536 bytes.
Buffers must be valid, nonoverlapping RAM ranges; output is word aligned.
The implementation has 128 label slots, 128 definition slots, and bounded tokens.

On success r1 is emitted byte count and r2 is 0. On failure **r1 is 0** and
r2 is `(source_line << 8) | error_code`. Source lines are one-based. Codes are
1 invalid source/output range, 2 invalid token/number, 3 duplicate label or symbol
capacity, 4 invalid/duplicate definition, 5 unknown instruction, 6 bad operands
or undefined name, and 7 insufficient output space. Failed staging is never a
valid executable; callers must retain their previous BIN until assembly succeeds.

`SYS_RUN_BUFFER` (`061`) takes r1 staging pointer and r2 emitted byte length.
Staging must lie within `0xE0000..0xE3FFF`, be word aligned, and contain a nonzero,
word-multiple payload of at most 16,380 bytes. Normal close/save precedes copying
the staged program into executable fast memory and transferring control.

## Miner persistence

The reserved IDs are `050` SYS_MINER_LOG_RESULT, `051` SYS_MINER_SAVE_STATE,
`052` SYS_MINER_LOAD_HISTORY, and `053` SYS_MINER_LOAD_STATE. They use the same
serialized filesystem controller. Record layouts, arguments, and software/native
mode behavior are documented with the miner implementation.
