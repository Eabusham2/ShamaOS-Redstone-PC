# Isolated upright display recovery

These are experimental files recovered after the execution workspace became
unavailable on 2026-11-12. They are not production replacements and are not a
verified complete computer. Use a matching ShamaOS source checkout as PYTHONPATH.

## Recovery identity and evidence

prototype.py is the exact 5,336-byte 4 X by 2 Y source recovered from the
recorded file read and applied patch. Its SHA-256 is
1c2c9e73bd49931ac2d3786b13f98bc4cd6b09062502408ef292f4f2de742c4b,
identical to the source hash recorded by the original passing full-panel run.
prototype3.py is its exact recorded three-X-pitch transformation; its recovered
SHA-256 is d725aa024478f382828ab079db843bba7adccc22c2a4c7a2b696ce8af026bfa0.
The probe scripts reproduce the recorded fixtures; no new tests were run during
recovery. SHA256.json gives UTF-8 hashes for every Python file.

The complete 4x2 panel passed the pinned actual MCHPRS engine:
- 320x180; 57,600 lamps and independently locked storage repeaters.
- 2,600,523 unique blocks; 185 horizontal chunks.
- Real bounds X330..1968, Y-60..304, Z1050..1067.
- 1,024 initial redstone ticks, then 73 steps of 1,024 ticks.
- All 57,600 output lamps checked after every step; 75,776 RT total.
- Source coordinate translation for the nonnegative legacy fixture is
  (-298,+64,-1018), applied equally to all blocks and endpoints.
- A saved/reopened 8x180 4x2 panel also passed 73 steps / 75,776 RT.
  The Anvil reader confirmed grass at Y-61 and supports from Y-60.

The 3x2 panel passed 4x4, 5x5, 8x180 and 320x4 actual-engine fixtures. Its
complete generated geometry is 2,371,162 unique blocks / 108 horizontal chunks,
with real bounds X489..1808, Y-60..304, Z512..529. Two dense full-panel attempts
were killed with exit 137 before a circuit assertion. Its COMPLETE full-panel
runtime is still unverified. Use the sparse actual-engine runner for that gate.
full3_probe.py writes the frozen case layout with translation (-457,+64,-480).

The legacy runner interprets each input coordinate as a terminal and places a
redstone block one block SOUTH of it for true, removing that driver for false.
Do not offset those input coordinates a second time.

## Console correction candidate -- unvalidated

controls_compact_view.py is the prepared correction candidate, not a recovered
passing circuit. It copies the production 4e933a5 controls, moves the eight
column pulse circuits 96 X blocks east and adds two south floor rows. All 79
actuator positions remain unchanged. Leads use five south repeaters, then an
eastward wire at tower top.z+6. Pulse origins are top+(96,-1,9); their outputs
are keyboard origin+(2*i-3+96,11+4*i,26). This detours a measured collision with
the Shift return at (979,-17,65) in the first unsuccessful prototype. The fix
was not executed before the environment outage.

control_view_probe.py checks literal collisions, all 57,600 lamp-center rays,
and every physical console actuator. It requires the unchanged pulse widths
and at least the actual raw-clock plus DFF setup/hold overlap (currently 80 RT);
the old exact 101-RT overlap must not be assumed after adding the leads.

sightlines.py conservatively treats every occupied cell as an opaque cube in
one result and checks full solid blocks separately. It now derives exit bounds
from the actual supplied geometry. These are geometric rays, not Minecraft
screenshots or proof of client render distance. NumPy is required for that
script. The proposed viewing lip is at absolute Z60..61, Y-56; a candidate
standing eye is (967.5,-53.38,61.5), with 1.62 assumed eye height.

Before production integration: prove the complete 3x2 runtime, collision-free
console, all 79 inputs, saved/reopened world and intact terrain, connected
two-block walking clearance to the viewing lip, full lamp visibility and a
practical rendered view. Update all control size/bounds/port metadata together.
The prototype's inherited CONTROL_SIZE is not yet production integration data.

## Integration constraints

Logical row odd/even polarity must be compensated before choosing a row.
For height 180, odd logical rows need inversion; one serial pixel-bit XOR before
row buffering can do it. Neither display-bridge RTL nor production interfaces
have been changed.

A 3x2 face is 958x359 blocks, physically about 2.67:1 while the logical image is
16:9. Keep the aspect and camera/view-distance tradeoff explicit. The full
above-grass geometry needs surface_y + 365 <= 319 with this cell. It fits the
classic grass-at-Y-61 world; high-grass worlds need a separately safe fallback.

All scripts write under /tmp/shama-vertical-display. Create that directory and
place these files there, then run from a repository checkout with
PYTHONPATH=src:tests:/tmp/shama-vertical-display and SHAMA_MCHPRS_RUNNER set to
the tested harness. Do not run another dense full case while memory is scarce.
