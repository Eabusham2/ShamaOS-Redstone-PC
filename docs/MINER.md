# Bitcoin Miner: behavior, measurements, and persistence

The shipped app computes `SHA256(SHA256(header80))` on the actual CPU. It has two
independent paths: the native `DSHA256` execution unit and assembly routines that
use ordinary loads, stores, integer arithmetic, rotates/shifts and Boolean
operations. The software path does not call a native SHA opcode or a host hash
function. Python `hashlib` is an independent test oracle, not a Minecraft runtime
service.

The current executable is **6,904 bytes / 84.2773% of the 8 KiB executable
cache**, down from 8,508 bytes before compaction (18.85% smaller). The latest
pass removes 404 bytes of redundant record-pointer reloads, dead drawing saves
and address-only immediate instructions. Relative addressing and tail calls
preserve the native and independent software SHA paths. Clearing a 53-character
display line uses thirteen word stores and one byte store instead of 53 byte
stores; bytes beyond that line are unchanged.

The complete workspace now uses **1,376 contiguous bytes**, down from 5,376.
All simultaneously live header, digest, target, snapshot, history, edit and
software-SHA regions remain distinct. Together with the 12-byte kernel and
executable backing, Miner uses **8,292 bytes / 8.4351% of 96 KiB RAM** and leaves
80 KiB of allocatable heap. Neighbor-guard tests exercise native/software hashing,
configuration and full digest comparison without writes outside this workspace.

## Controls

| Control | Action |
|---|---|
| Controller A | Start/resume the current generation. After finding a result, resumes at the next nonce. |
| Controller B | Stop and persist state and this generation's record. |
| Up | Save the preceding generation, increment generation, and reset nonce/attempt/elapsed counters. |
| Down | Return to the live view. |
| Left / Right | Browse the 256 persistent history slots. An unused slot displays EMPTY. |
| Keyboard M | Toggle native/software hashing while paused. |
| Keyboard C | Hash the same current header through both paths, compare all 32 output bytes, and record both measured costs. |
| Keyboard D | Cycle target presets with 0, 4, 8, …, 28 leading zero bits. |
| Keyboard T | Edit the full 256-bit target as exactly 64 hexadecimal digits. |
| Keyboard H | Edit all 80 header bytes as exactly 160 hexadecimal digits. |
| Enter / Backspace / Escape | Commit an exactly complete hex edit / move back one digit / cancel without changing the live value. |
| Home / Exit / power off | Request the common app lifecycle, which snapshots/saves before clearing the foreground workspace. |

Commands accept either letter case. Invalid hex characters and incomplete Enter
are ignored without partially replacing the current header/target. Editing uses
a staging buffer; cancellation leaves the live bytes unchanged. Reopening a
saved run always stops hashing until Start is pressed. A saved exhausted run
remains exhausted.

The initial header is 80 zero bytes and the initial target is `00` followed by
31 `ff` bytes. These are educational inputs, not a supplied live mining job.
There is no network, pool, wallet, or block-submission integration.

## Hash and nonce representation

The header's nonce is the little-endian 32-bit field at byte offset 76. Hash and
target fields are displayed as their complete byte sequences. Target comparison
uses those 32 bytes as an unsigned **big-endian** integer, matching `HASHCMP`.
The commonly displayed reversed Bitcoin hash string is a different convention;
reverse/convert it explicitly when preparing inputs.

Every attempted header increments the 64-bit attempt count, including a
successful attempt. The displayed nonce identifies the last attempted header.
The saved header may already contain the next nonce after an unsuccessful
attempt. At `0xffffffff`, the miner stops with exhausted status; it does not wrap
to zero, repeat the terminal nonce on repeated Start, or lose the final attempt.
Start after a successful terminal nonce also enters exhausted state. A new
generation or an explicit configuration change is required to continue.

## Display and measurement units

Numeric fields are hexadecimal. Each full hash/target occupies two lines.

| Label | Meaning |
|---|---|
| G / N / A | Generation / last attempted nonce / 64-bit attempts |
| H / T | Full 32-byte hash / full 32-byte target |
| S | Status: 0 paused, 1 running, 2 found, 3 nonce space exhausted |
| M | 0 native; 1 software |
| D | Number of leading zero bits in the target |
| V | Whether the displayed hash is at or below the displayed target |
| N / S on performance line | Last native / software CPU-cycle sample |
| R | Measured microhashes per raw-clock period, computed from the latest attempt; `ffffffff` means no elapsed sample is available. |
| C | Comparison: 0 not performed, 1 identical complete digests, 2 mismatch |
| E | Last save error; zero after a successful save |
| X | Resume marker: 0 ordinary, 1 advance after a found nonce, 2 exhausted |
| T on final line | 64-bit elapsed raw-clock periods for the selected record |

The elapsed counters cover hashing, not time spent navigating the UI. CPU cycles
and raw-clock periods are not wall-clock seconds. Increasing `/tick rate`, using
MCHPRS, changing the clock divider, or pausing changes the relationship to real
time. A measured real-time hash rate would additionally require a calibrated
runtime timebase; the app does not invent one.

The comparison command runs both paths on the same header, reads the CPU counter
around each, compares all eight digest words, and persists the result. Test
coverage also runs the shipped software routine on CPU RTL against the native
unit and `hashlib`. Reference-model instruction counts validate firmware control
flow; they are not used as a claim about native hardware cycle performance.

## Workspaces and record ABI

| RAM address | Use |
|---|---|
| `0x4000..0x404F` | 80-byte live header |
| `0x4050..0x406F` | 32-byte live digest |
| `0x4070..0x408F` | 32-byte target |
| `0x4090..0x40AF` | 32-byte software comparison digest |
| `0x40B0..0x41AF` | 256-byte state snapshot; first 128 bytes are a history record |
| `0x41B0..0x422F` | Loaded 128-byte history record |
| `0x4230..0x427F` | 80-byte edit staging buffer |
| `0x4280..0x42BF` | Software-SHA working state |
| `0x42C0..0x433F` | Software-SHA padded message |
| `0x4340..0x443F` | Software-SHA schedule |
| `0x4440..0x453F` | Software-SHA constants |
| `0x4540..0x455F` | Software-SHA temporary digest |

These locations overlay the Editor/Files source buffer because only one
foreground app runs at a time. Lifecycle cleanup clears the outgoing workspace
before the next app starts. The lifecycle owns and clears the complete miner
workspace. A sequence word at
state offset 252 is odd during a multiword update and even after a coherent
snapshot. Asynchronous close/shutdown waits for an even snapshot before saving.

### 128-byte history record, version 1

All scalar integers use little-endian encoding. Hash/target arrays retain their
SHA byte order.

| Offset | Bytes | Field |
|---:|---:|---|
| 0 | 4 | Magic `0x31524d53` (SMR1 bytes) |
| 4 | 4 | Record version 1 |
| 8 | 4 | Generation |
| 12 | 4 | Last attempted nonce |
| 16 | 8 | Attempt count |
| 24 | 4 | Status |
| 28 | 4 | Hash mode |
| 32 | 8 | Accumulated CPU cycles spent hashing |
| 40 | 4 | Target leading-zero-bit count |
| 44 | 4 | Reserved scratch; readers must ignore |
| 48 | 32 | Full hash |
| 80 | 32 | Full target |
| 112 | 4 | Last native-cycle measurement |
| 116 | 4 | Last software-cycle measurement |
| 120 | 8 | Accumulated raw-clock periods spent hashing |

`miner-history.log` is exactly **32,768 bytes = 256 × 128-byte records**. Generation
`g` writes slot `g & 255`, so a later generation eventually replaces the record
from 256 generations earlier. Stop/found/new-generation updates the current
slot, rather than consuming a new slot for every attempted nonce. A history
entry must have valid magic/version before the app treats it as a result.

### 256-byte saved state

The first 128 bytes are the history record. Offsets 128..207 contain the complete
80-byte header. Remaining words are edit/view state, snapshot bookkeeping,
comparison status and the resume/error markers. Offset 236 is comparison status,
240 is last save error, 244 is resume marker, and 252 is the sequence word.
The live header and target are the authoritative inputs restored on reopening;
the app does not resume a partially typed edit.

`SYS_MINER_SAVE_STATE`/`LOAD_STATE` use this 256-byte contract. History operations
use 128-byte records; no digest or target is truncated to fit a smaller record.
ShamaFS performs copy-on-write data replacement and journaled metadata commit.
See [FILESYSTEM.md](FILESYSTEM.md) for recovery and full-disk behavior.

## Software SHA scope and validation

The exported `.soft_sha256` routine accepts 0..119 bytes, covering one or two
padded blocks. `.soft_dsha256` applies it to 80 bytes and then to the 32-byte
first digest. The app supplies only these bounded lengths; this is not a general
unbounded streaming hash API.

Tests cover padding lengths 0, 1, 32, 55, 56, 64, 80 and 119; native/software
complete-digest equality; editable header/target; unsuccessful/successful attempt
accounting; stop/resume; persisted reload; generation rollover into history
slots; cancellation; and maximum-nonce exhaustion. Physical cell/routed-net
validation and full generated-world boot are separate gates. Passing these
firmware/RTL tests does not by itself establish full-world execution.
