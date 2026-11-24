# Level-request startup and saved restart evidence

This is an independently measured **four-word physical fixture** containing
the mapped power/reset circuit and the cache write path. The 177 mapped cells,
all four stored words, generated clock paths and native Anvil files are retained.
The raw clock and divider are fixture boundaries. The default full-computer
shell/router does not yet implement this measured local layout.

## Measured correction

The original edge-history request can lose a short Power-on lead. The recovered
level-request source is original Git commit
`34a3ea6e40127905fe2564aba10839746c96ea47`, tree
`10b2a72eb3ad6e27ced35fd8ab66a374a7e7e221`, with parent
`370c24df4db7de1d9d34dec6272f40bb90e49a34`. The original object survived the
workspace interruption and is preserved in `original-source.bundle`.

Level-request RTL alone still produced a one-tick setup failure. Three existing
repeaters on the private clock-hold branch add seven redstone ticks of delay;
all 109,719 input records were compared and only those three delay properties
changed. Ten fresh native profiles then passed, including Power leads of 12,
55, 95 and 157 ticks, controlled writes, out-of-range retention and the complete
power cycle. The normal cycle's four shell registers measured setup/hold minima
of 8/27, 13/2, 39/12 and 38/32 ticks; the six receiver registers met 8/2.
This private route adjustment must not be applied by guessing synthetic cell
names in another mapped graph.

## Shutdown, save and fresh-process restart

`SAVED_RESTART.md` describes both recorded continuations. The first leaves the
saved acknowledgment and core gate high. The second adds a genuine lever and
its support before the original shutdown, retains that lever in the native
snapshot, and uses it three times after reopening. It passes two short-lead
restarts, all 128 saved bits, 112 complete reset-qualified clock highs and all
ten local register timing checks. The original invalid drive declaration is
retained as a failed preflight; it was not accepted as a runtime result.

## Reproduce the retained measurements

The archives contain original source/scenario bytes, raw native traces, Anvil
regions, quiescent snapshots, independent readback and negative results. The
extractors rebuild and verify every original file's size and SHA-256. Use a
temporary directory with room for the expanded recordings; the base extractor
requires its temporary files and destination on the same filesystem.

```sh
mkdir -p /tmp/shama-startup-review
TMPDIR=/tmp/shama-startup-review python restore_measurements.py \
  measured-startup.tar.xz /tmp/shama-startup-review/base
TMPDIR=/tmp/shama-startup-review python restore_continuations.py \
  saved-restart.tar.xz /tmp/shama-startup-review/base \
  /tmp/shama-startup-review/continuations
python verify_continuations.py /tmp/shama-startup-review/continuations \
  /tmp/shama-startup-review/base
```

The base reconstructs 124 exact files, including the parent's independent
timing review; the supplement reconstructs 68 files using those verified
references. The measured engine is pinned MCHPRS
`f9e10d2572116bf5dcb9a36a305f28e40641a61a`; the terrain runner SHA-256 is
`c8032089622501cb57f9edbb0ea7e3be24e3a23fd0b2b80653b0ed4f07045610`.

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `measured-startup.tar.xz` | 1,665,488 | `5c21172628338991f90a09d115a1ab38dce7b72e63593a2e88e94682ed0a8175` |
| `saved-restart.tar.xz` | 52,812 | `9affabaf251ea1ee06620cfea1a4eedb7918c70e6f621b74b4d8f7f96f55063b` |
| `original-source.bundle` | 1,009 | `1af5b63ba68a8f70bae2485005f30bef8e5be4021a38e3ce36e3b666d614e39c` |

## Remaining production work

The full shell still routes raw-clock feedback through distant tracks. Its
divider/counter timing and the shared input clock require their own measured
closure. The implementation must place the startup logic as an identifiable
local component, preserve the receipt and hold protocol, and validate it
through the production generator. Full-cache execution and full-machine
software boot remain separate gates. No complete-world validator is enabled
by this fixture.
