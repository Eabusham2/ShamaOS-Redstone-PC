# Shama GPU

The GPU is an independent, queued raster engine with **16 KiB of physical VRAM**
and a **320×180 monochrome display**. Drawing modifies the back buffer;
presentation controls what reaches the lamp display. The default command queue
holds **256 commands**. A full queue stalls submission until room is available;
an acknowledged command is not dropped during normal operation. A resource
capacity fault discards the remaining app queue at a safe command boundary so
the OS can recover.

## Command interface

The MMIO base is `0x02000000`. Register accesses are 32-bit words. Write the
arguments, write the opcode, then write `1` to submit. Arguments are captured at
submission, so later register writes do not change a queued command.

| Offset | Register |
|---|---|
| `0x000` | Status: bit 0 engine active; bits 4–12 queued count; bit 16 front-buffer selector; bit 17 sticky command error; bit 18 resource-capacity fault |
| `0x004` | Command opcode |
| `0x008`–`0x024` | Arguments 0–7 |
| `0x028` | Submit; writes wait when the queue is full |
| `0x02C` | Last pixel-read or fence result |
| `0x030` | Completed fence counter |
| `0x034`, `0x038` | Cursor X and Y |
| `0x03C` | Rows emitted by the most recent presentation |
| `0x040` | Cursor flags: enable bit 0, single-pixel style bit 1 |
| `0x044`, `0x048`, `0x04C`, `0x050` | Current clip X0, Y0, X1, Y1; read only |
| `0x054`, `0x058` | Current font ID and monochrome drawing color; read only |
| `0x05C` | Current system notice code, or zero when hidden; read only |
| `0x400`–`0x4FF` | Direct access to 256-byte text staging RAM |
| `0x800`–`0xBFF` | Direct access to 1024-byte sprite staging RAM |

Busy means status bit 0 **or a nonzero queue count**. Unsupported opcodes/font
IDs, invalid resource spans, and malformed raster arguments set the sticky error
bit, which clears on reset. Text/sprite source-capacity violations also assert
the sticky `capacity_fault` output before any invalid DMA; the OS acknowledges
it with `clear_capacity_fault`. Queued work from the failing app is discarded,
but the front buffer remains intact and recovery commands remain usable.
Malformed coordinates alone do not trigger memory recovery. Unused register
addresses read as zero. Text/sprite accesses wait until queued drawing has
finished before accessing shared VRAM. Byte write strobes are preserved.

## Commands and arguments

Coordinates are signed 32-bit values. Raster shape coordinates are accepted in
`[-4096,4096]`; rectangle dimensions are `0..4096`; circle radius is `0..512`.
These explicit limits bound malformed requests. Valid off-screen portions are
clipped. Invalid resource spans are rejected before any drawing.

| Opcode | Operation | Arguments, starting at 0 |
|---|---|---|
| `00` | NOP | None |
| `01` | CLEAR | Fill value low bit; clears the entire back buffer |
| `02` | SET_PIXEL | X, Y; writes 1 |
| `03` | CLEAR_PIXEL | X, Y; writes 0 |
| `04` | INVERT_PIXEL | X, Y |
| `05` | READ_PIXEL | X, Y; returns back-buffer bit or 0 outside the screen |
| `06` | LINE | X0, Y0, X1, Y1; inclusive Bresenham line |
| `07` | RECT | X, Y, width, height; outline |
| `08` | FILL_RECT | X, Y, width, height |
| `09` | BLIT | Sprite offset, X, Y, width, height |
| `0A` | SPRITE | Same as BLIT; zero source bits are transparent |
| `0B` | DRAW_CHAR | Character byte, X, Y |
| `0C` | DRAW_TEXT | Text offset, byte length, X, Y |
| `0D` | SCROLL | Signed X shift, signed Y shift; exposed pixels become zero |
| `0E` | SET_CLIP | X0, Y0, X1, Y1, inclusive |
| `0F` | RESET_CLIP | Restore whole-screen clip |
| `10` | SET_FONT | Font ID; only resident font 0 is installed |
| `11` | SET_COLOR | Low bit becomes the monochrome drawing color |
| `12` | SET_CURSOR | X, Y, flags |
| `13` | COPY_BUFFER | Copy current front to back |
| `14` | SWAP_BUFFER | Exchange front/back and present changed rows |
| `15` | PUSH_DIRTY | Present pending rows of current front, including cursor changes |
| `16` | FENCE | Increment completed counter after earlier queued work |
| `17` | CIRCLE | Center X, center Y, radius; ring approximation |
| `18` | NOTICE | Low eight bits select a system warning; zero dismisses it and restores the front image |

Pixel writes, shapes, text, and sprites obey the clip. READ_PIXEL ignores the
clip. CLEAR, SCROLL, and COPY_BUFFER operate on the whole buffer. Empty
rectangles, zero-length text, and zero-area blits are valid no-ops. Reversed clip
bounds permit no writes. Circle pixels satisfy
`(r-1)² <= dx²+dy² <= r²`; radius 0 draws its center. Shapes and text use the
current color; SET_PIXEL/CLEAR_PIXEL explicitly write 1/0.

BLIT/SPRITE use **one byte per source pixel**, selecting its low bit. Source bit
1 draws the current color. BLIT source bit 0 draws the inverse color; SPRITE
source bit 0 leaves the destination unchanged. The complete source rectangle
must fit the 1024-byte sprite area.

Text uses a resident 5×7 font, six-pixel character advance, and transparent
background. All 95 printable ASCII bytes are visible, including distinct upper
and lowercase and assembly punctuation. Other bytes are blank glyphs; firmware
handles newlines and wrapping. Canonical glyphs in
[`font5x7.py`](../src/shamaos/font5x7.py) generate
[`shama_font5x7.sv`](../rtl/shama_font5x7.sv).

## Buffers, dirty rows, and cursor

Dirty tracking is by **row**. A row records a possible front/back difference
since the last copy. Drawing marks affected rows; clear/scroll marks all rows.
COPY_BUFFER resets the difference set. SWAP_BUFFER presents the union of pending
cursor rows and buffer-difference rows. The difference set survives a swap,
because swapping back must restore the previous image.

PUSH_DIRTY never swaps and does not expose unpresented back-buffer edits. With
no pending rows it emits no pixels. Tracking is conservative: writing an
unchanged value can still mark its row.

The cursor is an **inverting scanout overlay**; it never changes VRAM. Flag bit 0
enables it. With bit 1 clear it is a cross five pixels wide and high (nine pixels
total before clipping); bit 1 selects one pixel. Moving/hiding it marks old/new
rows, restoring the underlying image on presentation. It ignores the drawing
clip but is clipped to the screen.

For incremental drawing, use COPY_BUFFER, draw, then SWAP_BUFFER. For cursor-only
movement use SET_CURSOR then PUSH_DIRTY. Wait for completion before reusing
staging memory referenced by queued text or sprite commands.

### System warning overlay

NOTICE presents the current front buffer with a fixed system warning over it.
It uses the resident font during scanout and performs **no VRAM writes**. Showing
or dismissing it preserves both framebuffers, unfinished back-buffer edits, text
and sprite staging, the front selector, clip, font, color, cursor and the set of
front/back differences. It reserves no additional VRAM. Each NOTICE command
refreshes the warning's 72 rows plus any already-pending cursor rows; a following
FENCE waits for that presentation to finish. With no pending rows elsewhere,
this takes 720 VRAM word reads and 23,040 pixel updates, 60% fewer than a full
native-screen presentation. These are transfer counts, not measured wall time.

| Low seven bits of code | Warning |
|---|---|
| `1` | Flash writes paused at 95%; delete files to restore writing |
| `2` | RAM limit; OS restarted |
| `3` | Executable-cache limit; OS restarted |
| `4` | VRAM limit; OS restarted |
| `6` | Save failed; Keep App selected |
| `7` | Save failed; Discard and Leave selected |
| Other nonzero code | App fault; OS restarted |

For recovery warnings, bit 7 reports that unsaved edits could not be saved;
otherwise the message confirms preservation of previously saved files. The
services control modal input and dismiss ordinary warnings with NOTICE 0 after
A/B or Enter/Escape. Codes 6 and 7 instead present a failed-close decision:
Left/Right chooses, A/Enter confirms, and B/Escape cancels. The initial selection
is always Keep App. The kernel fences each changed selection before accepting
confirmation, so the committed choice is the one already visible on the screen.
The GPU renders the notice; it does not perform recovery or consume input.

Before submitting a show, selection change or dismissal, the kernel drains
older GPU work. Otherwise a queued invalid app command could discard the
NOTICE before it rendered while a later fence still appeared to succeed.
After draining, the kernel rechecks the desired notice, submits it and waits
for its presentation fence. A changed or cancelled request cannot make an
undisplayed choice ready for confirmation.

On the native screen the warning occupies X=24..295, Y=48..119. It overrides the
cursor inside that rectangle while preserving the cursor state. Dismissal
restores the exact front image and cursor without exposing the unfinished back
buffer. Notices use white text on black independently of an app's drawing color
or clip, so a restrictive custom clip cannot hide a capacity warning.

## Physical VRAM layout

| Byte offset | Use |
|---|---|
| `0x0000` | Framebuffer 0 |
| `0x1C20` | Framebuffer 1 |
| `0x3840` | Text staging, 256 bytes |
| `0x3940` | Sprite staging, 1024 bytes |
| `0x3D40..0x3FFF` | 704 bytes unreserved |

Pixels are packed linearly at bit `Y×320+X`, least-significant bit first. Each
visible plane uses 7,200 bytes. The packed reservation is **15,680 bytes / 95.7031%**
of four physical 4 KiB banks. MMIO resource window offsets are unchanged; these
are physical VRAM offsets. The display
bridge uses the actual emitted pixel index, so skipped clean rows do not shift
later updates to the wrong row. Physical placement/loading/runtime checks are
covered separately by the hardware and validation documents.

Scrolling uses no snapshot buffer. It visits destination words in the direction
that preserves unread source data, reads up to two adjacent source words,
shifts/masks them and writes one destination word. Exposed columns/rows become
zero; the front buffer and resource staging remain untouched. Native 320-pixel
rows need at most **5,400 VRAM transactions** for a nontrivial full-screen
scroll (three per 32-pixel word). A zero shift needs none; a completely offscreen
shift uses the ordinary clear path. Odd-width test configurations use the same
overlap-safe order with a bounded pixel fallback.

## Behavioral evidence

[`test_gpu_execution.py`](../tests/test_gpu_execution.py) compares real RTL to
[`GPUReference`](../src/shamaos/gpu.py) with stalled VRAM/display interfaces. It
checks every front/back/display pixel, readback, fences, errors, queue behavior,
and dirty rows. Cases include every command and printable glyph, randomized
signed-coordinate lines, transparency, scrolling, edge cursors, malformed
requests, queue depths 1/4/256, and native 320×180 presentation of the final pixel.
Notice tests additionally compare every VRAM word and both visible images across
show/dismiss, restrictive clips, black drawing color, active cursors and pending
back-buffer edits. Kernel/GPU integration verifies the modal input and retained
application state through the actual warning path.
RTL tests alone do not establish the generated world's redstone timing.
