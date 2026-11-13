# Physical controls and GUI navigation

## Power, reset, pause and step

| Control | Behavior |
|---|---|
| Power lever on | Start from immutable boot ROM, mount persistent flash, and load Desktop |
| Power lever off | Request orderly save/cleanup; gate the core only after shutdown completion |
| Reset button | Immediately reset logic and restart boot while powered; this is recovery, not orderly save |
| Pause lever | Stop the divided core clock while preserving CPU state |
| Step button while paused | Permit one complete core clock pulse per press |

Shutdown overrides pause so a paused machine can finish a save. If persistence
fails, the machine retains its live app/buffer and does not grant shutdown
completion. Fix the error or save successfully, then retry normal close/power-off.
Reset can interrupt pending work; filesystem recovery applies on the next mount.

The debug panel exposes CPU registers, PC/instruction, flags, CPU/fault/stall
state, SHA state/round and memory usage. An invalid flash header or rejected boot
bundle stops at a deliberate HLT before Desktop: r1 retains the signed error and
r2 is `0xFFFFFFFF`. This does not set a CPU instruction fault. Repair/replace the
image, then reset. The machine does not silently format invalid persistent data.

## Controller and universal actions

| Physical input | Bit | Event code | Ordinary app behavior |
|---|---:|---:|---|
| Up | 0 | `10` | Previous item or move cursor up |
| Down | 1 | `11` | Next item or move cursor down |
| Left | 2 | `12` | Move left or app-specific alternate action |
| Right | 3 | `13` | Move right or app-specific alternate action |
| A | 4 | `14` | Confirm/activate |
| B | 5 | `15` | Back/cancel or app-specific action |
| Home | 6 | `16` | Close foreground app normally and open Desktop |
| Exit | 7 | `17` | Close foreground app normally and open Desktop |
| Editor | 8 | `18` | Close foreground app normally and open Editor |
| Files | 9 | `19` | Close foreground app normally and open Explorer |

Codes in this table are hexadecimal. Universal Home/Exit/Editor/Files is serviced
by hardware, including when the foreground program is halted or never polls
input. Dirty named Editor content and miner state must finish saving before
replacement. A failed save retains the app and shows **Keep App** initially.
Left selects Keep App; Right selects **Discard and Leave**. A/Enter confirms the visible choice,
and B/Escape cancels. Universal controls while this dialog is open also cancel
it. The explicit discard choice completes the originally requested destination
or shutdown; ordinary navigation does not silently discard unsaved work.

## Keyboard matrix

The keyboard is an 8×8, single-key matrix. Press and release a key for each
character. Holding a key does not generate automatic repeat; ambiguous
multi-row/multi-column chords are ignored. Row/column numbers below are the
zero-based hardware coordinates used by tests and wiring.

| Row | Col 0 | Col 1 | Col 2 | Col 3 | Col 4 | Col 5 | Col 6 | Col 7 |
|---:|---|---|---|---|---|---|---|---|
| 0 | A | B | C | D | E | F | G | H |
| 1 | I | J | K | L | M | N | O | P |
| 2 | Q | R | S | T | U | V | W | X |
| 3 | Y | Z | 0 | 1 | 2 | 3 | 4 | 5 |
| 4 | 6 | 7 | 8 | 9 | Space | Enter | Backspace | Tab |
| 5 | `.` | `,` | `-` | `_` | `/` | `+` | `=` | `:` |
| 6 | `[` | `]` | `(` | `)` | `!` | `?` | `*` | `;` |
| 7 | Up | Down | Left | Right | Escape | Delete | Space | Shift |

**Shift is a toggle**, not a held chord. Press row 7/column 7 to switch the
alternate bank on or off. Letters become lowercase; digits and control keys stay
the same. The two punctuation rows become:

| Row | Col 0 | Col 1 | Col 2 | Col 3 | Col 4 | Col 5 | Col 6 | Col 7 |
|---:|---|---|---|---|---|---|---|---|
| 5 | Double quote `"` | Apostrophe `'` | `<` | `>` | Backslash | Vertical bar | `@` | Grave accent |
| 6 | `{` | `}` | `#` | `$` | `%` | `&` | `^` | `~` |

Together the two banks provide all 95 printable ASCII characters. Shift changes
local keyboard state and does not queue a character. `SYS_INPUT_STATUS` bit 9
reports that state. Arrow key bytes are `80`–`83`; Enter is `0D`, Backspace `08`,
Tab `09`, Escape `1B` and Delete `7F`.

## Event buffering and overflow

The default FIFO holds **16 events**. Input is sampled on the raw clock, so
presses can remain queued while the slower core is executing. One acknowledgement
pops one event even when its assertion spans several raw clocks. This prevents a
slow CPU acknowledgement from draining several pending keys.
The acknowledgement carries the consumed event snapshot, so a newly arriving
Home/Exit action cannot be removed in place of an older key already consumed by
the slower services clock.

Ordinary events retain FIFO order. Universal actions select the oldest queued
universal event ahead of ordinary typing so a nonpolling app remains escapable.
Simultaneous fresh controller presses enter in Editor, Files, Exit, Home, Up,
Down, Left, Right, A, B order, followed by a keyboard character.

When full, the queue drops new ordinary events and sets a sticky overflow flag.
A newly pressed universal action can replace the newest queued ordinary event
so saturated typing cannot disable recovery controls. A queue already full of
universal actions cannot hold more than its capacity. Overflow stays set until
reset; `SYS_INPUT_STATUS` returns count in bits 0–7 and overflow in bit 8.
The physical overflow output is available for diagnostics.

## Physical signal timing and debug placement

The emitted oscillator has **35 redstone ticks per high/low phase**, for a
70-tick raw-clock period. Its enable register admits complete pulses when power
changes; the SoC's `clock_hold` keeps it running during orderly shutdown. Pausing
and manual stepping operate on complete divided-core pulses. The oscillator's
local timing does not establish timing closure for the complete routed machine.

A real stone-button press lasts 10 redstone ticks. Physical delay lines stretch
controller/reset/step signals to **94 ticks** and keyboard row/column signals to
**119 ticks**. Wait for the stretched release before another key. The longer
keyboard pulse allows the two independently routed matrix lines to overlap at
the sampling clock. All 64 native keys and the dedicated Shift button have at
least **101 ticks** of simultaneous row/column high. The all-actuator routed
console test measures a minimum **81 ticks** against the required 80 ticks:
one raw period plus the DFF's eight-tick
setup and two-tick hold budget. Full-system routes must preserve that margin too. Tick-rate
acceleration advances these same circuits; it does not bypass the input FIFO.

All **79 actuators fit within a 16×16 walking area**: 64 matrix keys, ten
controller buttons, Reset, Step, Power, Pause, and a dedicated Shift button.
The dedicated Shift duplicates matrix key R7/C7; it does not remove any key.
A connected deck and four ordinary steps provide access from the grass, with
two blocks of player headroom. The physical pulse circuits are stacked behind
and above the controls. Their exact bounds relative to the input origin are
`(2,-4,0)` through `(206,39,134)` (**205×44×135**); the lowest support is one block
above the selected grass surface. This backend emits **13,254 placements** at
**13,006 unique block positions** and occupies **47 X/Z chunks** at the production alignment, separately from the small
player activation area. Exports use explicit open faces and climb outside the
backend envelope; input-tower and pulse-output clearance is tested.

Eight column pulse circuits sit 96 blocks farther east, and four side-control
pulse circuits sit 80 blocks farther east. This clears their native bodies and
their export leads from the screen view. Existing repeaters add 14 ticks of
common row delay to balance the longer column path; neither pulse widths nor
the 79 activation positions change. The clock sits 48 blocks north of its old
position, outside the console envelope, with unchanged occupied chunk count.

The measured native column rise times are 26–40 redstone ticks. The earlier
32-tick keyboard snapshot therefore sampled columns four through seven too
early. Its regression now samples after one actual 70-tick raw-clock period,
and also checks every tick for correct 119-tick pulses and no unrelated output.
For all 64 keys, every one of the 70 possible clock phases must contain a capture
edge with the required setup and hold interval. These 4,480 phase checks and the
unchanged 101-tick minimum native overlap are retained in the
[keyboard timing report](evidence/keyboard-clock-phase-review-2026-11-13.json).
This corrects the test's sampling assumption; it does not change circuit timing.

The measured native and exported console waveforms also drive the actual
`shama_input` RTL in a separate integration test. Both keyboard banks and all ten
controller buttons decode correctly at every raw-clock phase, including the
dedicated Shift, held acknowledgements, queue order, urgent actions and overflow.
The same unmodified waveforms fail capture with a deliberately slower 140-tick
raw period. The [integration report](evidence/console-input-integration-2026-11-13.json)
retains these four passing positive/negative regressions. Propagation inside the
mapped input-controller circuitry still requires its own physical timing proof.

## Screen viewing position

The production screen is an upright **320×180** array with **57,600 lamps and
57,600 independent pixel latches**. Its visible face is **958×359 blocks**,
using a three-block horizontal and two-block vertical pixel pitch. Its complete
native circuit occupies **108 horizontal chunks**; external routes are counted
separately. Logical row 0 is the top row. The physical 3×2 pitch makes the image
about 50% wider than square logical pixels; resolution remains 320×180.

For the default origin, walk south from the keyboard onto the connected viewing
lip. Stand at **X967.5, feet Y−55, Z64.5**, looking toward **+Z**. A standing eye
height of 1.62 gives eye Y−53.38. The 16×16 activation area stays unchanged; the
viewing extension is a short walk beyond its south edge and has two-block
standing clearance. All **57,600 lamp-center rays** are clear from this exact
position, including the native console, debug/clock cells and all 30 planned
console export paths. Other key positions are not guaranteed to show every
pixel unobstructed.

The farthest lamp center is about **656 horizontal blocks** from the viewing
position; the farthest native panel circuit is about **961 horizontal blocks**
away. These are geometric distances, not claims about a particular client's
render or simulation-distance setting. Render distance must actually include
the whole face, and the operating screen requires all of its circuit chunks
loaded. A distant-geometry visual mod alone is not proof that those redstone
chunks are ticking. Full-world loading and in-client rendering remain required
checks even when the camera projection and sightlines pass.

The deterministic camera check uses **yaw 0°, pitch −21.788°, 70° vertical
field of view and a 16:9 viewport**. At 1920×1080 the entire face projects inside
X71.44..1850.41 and Y305.67..849.51; a lamp is approximately 1.41..1.86 pixels
wide. This establishes the geometry of a complete view. It does not claim an
observed Minecraft client FOV-slider mapping or in-client text legibility.
Generate the labeled perspective preview with
`python tools/render_operator_view.py` (Pillow required).

The default native panel bounds are **(489,−60,512)..(1808,304,529)**, above
intact classic-superflat grass at Y−61. A full upright panel cannot fit above
grass at Y63; generation rejects it before placement instead of clipping pixels
or digging into terrain. Complete-machine routing, client rendering and
saved-world OS execution remain separate release gates.

The debug lamps form a vertical north-facing wall, with four-block column
and row pitch and input terminals facing south. Its relative bounds are
`(0,0,-3)` through `(124,153,0)`; at the classic-flat origin Y=-56, its top is
Y=97. Each lamp is isolated by a repeater and a strong driver block. The complete
wall exposes **993 signals**. Its dedicated escape schedule keeps the initial
southward leads within 192 blocks, then sends the wires to separate columns to
the east before they rise. The full-wall geometry is checked against the
nearby control assembly and clock; the real-engine routed-lamp test covers a
representative subset. The long outer routing columns remain an optimization
target, so the wall's native bounds do not describe its complete wire footprint.

These local and routed properties are exercised by
`tests/test_mchprs_runtime.py` in the actual MCHPRS engine, including cold-clock
startup, every clock start/stop phase, all 79 console actuators, and independently
driven debug lamps. `tests/test_compact_controls.py` checks the connected deck,
player clearance, exact bounds and grass protection.
[`test_upright_display.py`](../tests/test_upright_display.py) checks full-frame
projection, every sightline, the 500 native panel escapes, actual GPU/bridge
rows, full-resolution lamp state, and reopened panel polarity/ground. Its
native-clock fixtures also retain writes with 70-tick data setup and a
70-tick commit pulse across the full height and full width; complete-machine
route skew remains a separate release requirement.

## Desktop and Editor

Desktop uses Up/Down to select a visibly marked app and A to open it. Editor's
initial menu offers New Text, New Program and Open. New files prompt for a name;
Enter confirms, Backspace edits, and B cancels. Full paths must fit 43 bytes.

While editing, keyboard characters insert at the visible cursor. D-pad and
keyboard arrows navigate; Up/Down follows logical lines. Enter inserts a
newline, Tab inserts a tab, Backspace removes the previous byte, and Delete
removes the current byte. Long lines wrap into 48-character display segments.

A saves. B opens the visibly selected action menu; Up/Down chooses and A runs
Save, Save As, Rename, Delete, Assemble, Assemble and Run, New Text, New Program,
or Exit. Assembly errors show the line, code and category while preserving the
last valid executable. Universal controls follow the same save-before-exit path.
See [OS.md](OS.md) for capacities and file behavior.

## File Explorer

| Control in the file list | Action |
|---|---|
| Up / Down | Select another immediate child of the current folder |
| A on DIR | Enter the directory |
| A on TXT | Open text viewer |
| A on ASM | Open complete source in Editor |
| A on BIN | Run a program that fits the executable slot |
| Left on TXT / ASM | Transfer file ownership and complete content to Editor |
| Right | Open delete confirmation |
| B / keyboard Backspace | Parent folder; at root, return to Desktop |
| Keyboard N, either case | Create a directory |
| Keyboard R, either case | Rename the selected entry |
| Other letters, either case | Select the first matching filename initial in the current directory |

The filename search compares basenames and preserves selection when no match
exists; it does not search through descendants or cycle among multiple matches.
N and R remain reserved for their commands. In the TXT viewer, Up/Down scrolls,
Left opens Editor, and B returns to the list. Newline boundaries and blank lines
are retained.

## Delete confirmation

Editor and Explorer show the selected filename and begin with **Cancel**
selected. Left/Up selects Cancel; Right/Down selects Confirm. A activates the
selected choice; B cancels. Opening the dialog and pressing A immediately does
not delete anything. The filesystem syscall itself assumes the caller already
obtained this confirmation.

## Utility apps and Miner

Paint moves its non-destructive cursor with the D-pad. A draws a pixel, B erases,
and keyboard C clears the canvas. Use a universal button to leave Paint.
Settings uses Up/Down and A to change cursor visibility or cross/point style,
save, open Monitor or exit. Its saved cursor settings are restored by Paint.

Terminal accepts a complete command with Enter, Backspace edits, and B exits.
Supported commands and their destination workflows are listed in [OS.md](OS.md).
Calculator uses signed decimal input, unary `+`/`-`, and `+ - * / %`; Enter or
`=` evaluates and B clears. Backspace dismisses a visible error first, then
removes digits. Arithmetic is checked within signed 32-bit range; overflow and
zero divisors show errors. Operations chain left to right. A new digit after a
result starts another calculation. `%` is in the alternate punctuation bank.
See [OS.md](OS.md) for signed division/remainder examples. The Bitcoin
Miner's start/stop, software/native mode, header/target editing, history pages,
generation/reset and exit controls are specified in [MINER.md](MINER.md).

## Verification scope

The input RTL tests cover printable characters, Shift, simultaneous controls,
priority, overflow and a held acknowledgement across multiple raw clocks. CPU
and services tests cover universal exit, failed saves and resource cleanup.
The integrated SoC test exercises physical row/column and controller signals,
pause/manual step, editing, automatic save, shutdown while paused, reboot and
Explorer handoff. It uses test memory devices; physical signal propagation and
end-to-end generated-world execution are separate acceptance gates.
