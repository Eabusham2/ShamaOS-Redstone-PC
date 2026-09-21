# ShamaOS

## Boot

ShamaOS is stored in the 4 MiB physical flash image. Boot establishes reset, mounts ShamaFS, copies the kernel into resident RAM, clears foreground RAM/cache, loads Desktop into RAM/cache and starts the app at cache PC 0.

Kernel RAM base is `0x4000`; foreground app RAM base is `0x8000`.

## App lifecycle

Only one foreground app is active. App replacement:

- releases foreground heap allocations;
- clears app RAM;
- clears executable cache;
- loads exact executable bytes from flash;
- updates real RAM/cache counters;
- resets CPU app state;
- starts the next app at cache PC 0.

Hardware-universal Home/Exit/Editor/File Explorer can replace a halted or non-polling foreground app.

## Desktop

D-pad selects; A opens. Launchers: Editor, File Explorer, Bitcoin Miner, System Monitor, Terminal, Calculator, Paint and Settings.

## Editor

Editor supports New Text, New Program, Open, keyboard editing, Save, Save As, Rename, confirmed Delete, Assemble, save compiled `.BIN` and Run. Editable source is currently bounded to the 4 KiB-class Editor buffer; compiled binaries must fit the 16 KiB executable cache.

## File Explorer

File Explorer lists ShamaFS entries and can read/scroll text, hand TXT/ASM files to Editor, run binaries, rename and confirmed-delete files.

## Bitcoin Miner

Controls:

- A Start
- B Stop/save
- Up new generation
- Down live view
- Left/Right browse saved runs

The miner executes CPU `DSHA256 -> HASHCMP -> INCNONCE` and displays nonce, attempts, generation, current 256-bit hash and target. Persistent storage includes a 256-byte state record and 16 KiB / 256-entry history ring.

## System Monitor

Displays live RAM/cache/flash usage, CPU cycle/retired counters, CPU halt/run, SHA busy/idle and GPU busy/idle.

## Utility apps

Terminal accepts keyboard commands; Calculator performs integer decimal arithmetic; Paint uses the GPU/VRAM framebuffer; Settings provides safe navigation/maintenance actions.

## Syscalls

Implemented families include app/process/event/time, ALLOC/FREE/usage, full ShamaFS file operations, GPU submit/text/rect/message/fence, GUI views, miner persistence, hardware assembly and staged user-binary execution.
