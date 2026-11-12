# ShamaOS

## Boot

ShamaOS is stored in the 4 MiB physical flash image. An immutable gate-based boot
ROM remains available even after executable fast memory has been erased. Boot
mounts ShamaFS, copies the kernel into resident RAM, clears foreground RAM/fast
memory, loads Desktop, and starts its program at fast-memory PC 0.

Kernel RAM base is `0x4000`; foreground app RAM base is `0x8000`.

## App lifecycle

Only one foreground app is active. A normal replacement or shutdown:

- waits for a coherent Editor/miner state snapshot;
- saves dirty named Editor content or miner state and waits for storage completion;
- closes the app's remaining file handles;
- releases and clears its heap allocations and fixed workspaces;
- clears app RAM and executable fast memory;
- loads exact executable bytes from flash;
- updates actual RAM/fast-memory usage counters;
- resets CPU app state;
- starts the next app at PC 0, or completes shutdown before clock gating.

Hardware-universal Home/Exit/Editor/Files works for a halted or nonpolling app.
The same save-before-cleanup path applies. A failed save leaves the app, buffer,
and ownership alive; `SYS_LIFECYCLE_STATUS` reports the failure. It does not
silently convert a failed save into forced teardown. Power-off overrides pause
long enough to complete this path. Reset is immediate recovery, distinct from
orderly power-off.

Explorer-to-Editor handoff explicitly transfers the file handle and preserves
the name, complete source buffer, and context. Other replacements clear those
workspaces after successful persistence. A source sequence counter is odd while
an edit is in progress and even when the buffer/length/dirty flag agree, so an
asynchronous close cannot save half an insertion.

## Desktop

D-pad selects; A opens. Launchers: Editor, File Explorer, Bitcoin Miner, System
Monitor, Terminal, Calculator, Paint and Settings. Views identify the selected
action and display used/total RAM, fast memory and flash in explicitly labelled
hexadecimal bytes. Status-only refresh preserves the foreground canvas.

## Editor

Editor offers New Text, New Program and Open. New Text/Program asks for a name;
Enter creates the file exclusively. Names without a final three-character
extension receive `.TXT`/`.ASM`. Full canonical paths must fit 43 bytes.
Duplicate names and invalid parents report errors without replacing another file.

The source workspace is 64 KiB at `0xC0000`, with **65,535 editable bytes** and a
reserved guard byte. All shipped app sources fit and are editable in full.
Oversized files are refused explicitly; saving never replaces a complete file
with a truncated display prefix. Binaries must fit **16,380 bytes**, the executable
slot payload after its length word; assembly staging is at `0xE0000`.

Keyboard input inserts at the visible cursor. Left/Right moves by byte;
Up/Down moves between logical lines while preserving the column where possible.
Backspace removes the preceding byte, Delete removes the current byte, Enter
inserts a newline, and Tab inserts a tab. Long lines are shown in 48-character
segments. The viewport displays up to eight lines.

| Control/workflow | Result |
|---|---|
| A while editing | Save the current file |
| B while editing | Open the action menu; Up/Down selects, A activates |
| Save As | Create a separate file, copy the content, then make it active |
| Rename | Change the active file's name without changing its content |
| Delete | Show filename with Cancel initially selected; choose Confirm before A |
| Assemble | Save source, assemble, and save a separate BIN only on success |
| Assemble and Run | Complete the same save/assembly path, then launch the staged program |
| New Text / New Program | Save the previous dirty file before starting another |
| Exit or universal controls | Save dirty named content before replacement |

Assembly diagnostics show an explicit decimal `LINE` and hexadecimal `CODE`,
plus a readable error category. Errors preserve the previous valid executable.
An existing three-character extension is replaced with `.BIN`; otherwise `.BIN`
is appended. A BIN-name/type collision, including an ASM file itself named
`something.BIN`, is refused without overwriting source or unrelated files.
Returning from a diagnostic goes back to the operation that produced it.

## File Explorer

Explorer navigates real directories. Its selected entry shows the complete path,
type and exact size in labelled hexadecimal bytes. Up/Down selects another
immediate child; nested descendants remain inside their parent directory.

| Control | Behavior |
|---|---|
| A on a folder | Enter it |
| B / Backspace in the list | Parent folder; B at root returns to Desktop |
| A on TXT | Open the eight-line text viewer; Up/Down scrolls |
| A on ASM | Transfer the full source to Editor |
| Left on TXT/ASM, including the viewer | Transfer to Editor |
| A on BIN | Run it if it fits the executable slot |
| Right in the list | Open Cancel-first delete confirmation |
| Keyboard R | Rename within the current folder |
| Keyboard N | Create a folder within the current folder |

Empty folders are labelled. New/renamed paths are relative to the visible
directory. Deleting or renaming a nonempty directory is refused to avoid
orphaning children; moving an entire directory tree is not implemented.
The text viewer respects newlines and wraps long lines. Text/source files over
65,535 bytes are refused with a size error rather than silently truncated.

## Bitcoin Miner

The miner has independently implemented native and software double-SHA-256,
editable 80-byte headers/full targets, measured comparisons, stop/resume,
nonce-exhaustion handling, complete live hashes and persistent 128-byte records.
See [MINER.md](MINER.md) for controls, exact units, the 256-slot history and saved
state ABI.

## System Monitor

Displays live RAM/cache/flash usage, CPU cycle/retired counters, CPU halt/run, SHA busy/idle and GPU busy/idle.

## Utility apps

Terminal accepts complete, case-insensitive commands without arguments:
`LS`, `EDIT`, `MEM`, `FLASH`, `GPU`, `MINER`, `PAINT`, `CALC`, `RUN`, `CAT`, `RM`,
`MV`, and `CLEAR`. File operations open Explorer's selected-file workflow;
`MEM`/`FLASH`/`GPU` open Monitor. Unknown commands display an error. This is a
small app-launching terminal, not a general command-language parser.

Calculator performs integer decimal arithmetic. Paint uses D-pad movement,
A to draw, B to erase, and keyboard C to clear. Its cursor is a non-destructive
GPU overlay. Movement preserves canvas pixels; canvas edits use front-to-back
copy, drawing, and presentation. The canvas is temporary app state.

Settings changes cursor visibility and cross/point style, offers Save and
Monitor, and previews the choice. Changes write a 16-byte `settings.cfg` record
before the updated view returns. The record contains magic `0x53434647`, flags
(enable bit 0, point bit 1), version 1 and a reserved word. Paint restores this
record on launch. A failed save is visible and can be retried; it is not reported
as durable success. Settings persistence is tested by serializing flash,
reopening it, and launching both Settings and Paint.

## Syscalls

Implemented families include app/process/event/time, ALLOC/FREE/usage, full ShamaFS file operations, GPU submit/text/rect/message/fence, GUI views, miner persistence, hardware assembly and staged user-binary execution.
