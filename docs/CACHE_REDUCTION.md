# Application and physical-cache reduction — November 13, 2026

Dates in this document use America/Chicago. Retained evidence filenames use
the corresponding November 14 UTC date.

The renewed owner request is to shrink the applications' cache use and make
physical cache construction significantly smaller. The implemented capacity is
**4 KiB in one full bank**, down from 8 KiB in two banks. The original **8 KiB
program execution window remains** in its existing RAM backing. No source,
editing limit, hash field, history record or software-SHA implementation is
removed to achieve the cache reduction.

## Actual executable sizes

These are assembled byte counts against the restored `184faa9` checkpoint,
not estimates from source-line counts. Every app retains a complete editable
source file, separate binary and packed loader record.

| App | Restored executable | Current executable | Reduction | Cached bytes | Direct-RAM tail |
|---|---:|---:|---:|---:|---:|
| Desktop | 232 | 184 | 20.69% | 184 | 0 |
| Editor | 4,696 | 3,476 | 25.98% | 3,476 | 0 |
| File Explorer | 5,320 | 3,584 | 32.63% | 3,584 | 0 |
| Bitcoin Miner | 6,904 | 5,004 | 27.52% | 4,096 | 908 |
| System Monitor | 52 | 40 | 23.08% | 40 | 0 |
| Terminal | 1,920 | 1,488 | 22.50% | 1,488 | 0 |
| Calculator | 1,824 | 1,448 | 20.61% | 1,448 | 0 |
| Paint | 868 | 696 | 19.82% | 696 | 0 |
| Settings | 772 | 608 | 21.24% | 608 | 0 |

Together the nine executables fall from **22,588 to 16,528 bytes**, a **26.83%**
reduction. Editor/Files share private launch, redraw and formatting paths. Miner
shares snapshot/save tails, repeated digest/configuration rows and hexadecimal
formatting, and copies aligned workspaces by words. Explicit one-word branch,
call and signed-immediate forms then save an additional word per eligible
instruction. The old instruction encodings remain valid. The host assembler,
in-world assembler, CPU and reference interpreter all implement the same
[short-form contract](ISA.md#one-word-forms).

[Exact app measurements and source hashes](evidence/app-cache-reduction-2026-11-14.json)
and the [full resource ledger](evidence/software-usage-cache4-2026-11-14.json)
retain the input/output identities. Reassemble the checked-in sources and run
`shamaos software-usage --report build/software-usage.json` to reproduce current
occupancy. The earlier source-only checkpoints are retained separately; their
larger byte counts precede the short forms.

`python tools/measure_cache_reduction.py --report build/app-cache-reduction.json`
reproduces all before/after executable measurements from the immutable baseline
and the current complete sources.

## How 4 KiB of cache retains 8 KiB programs

The previous machine already allocated RAM at `0x0000..0x1fff` for the complete
executable backing. It also held a second copy of those bytes in physical cache.
The revised loader writes every executable word to that same RAM window and
copies only bytes `0x0000..0x0fff` into the smaller cache. CPU accesses below
`0x1000` use cache; the rest use RAM through the existing SoC address selection.
There is no new RAM allocation or second application workspace.

Miner is **5,004 bytes**, so it is not a 4 KiB executable. Its **908-byte tail**
runs from existing RAM. Native mining, event handling and UI code occupy the
first **3,164 bytes**; the independent software-SHA library crosses the boundary.
The software path remains usable. Its RAM accesses can have different physical
latency from cache accesses, so no whole-machine speedup is claimed.

The services count `min(executable_length, 4096)` as cache use and the full
executable as RAM backing. Flush copies only the cached prefix back to RAM and
preserves direct tail writes. App replacement clears all 8 KiB of old RAM code
and all 4 KiB of cache, while preserving the resident kernel and Editor context.
Staging remains live until the full RAM copy and cache prefix finish. Programs
above the original 8 KiB execution limit still produce visible code-capacity
recovery; the 65,535-byte editing and 16,380-byte assembly/save limits remain.

## Savings outside the executable cache

The installed image uses **146,688 bytes / 143.25 KiB / 71.625% of 200 KiB
flash**, down from 158.5 KiB / 79.25%. It still contains all 32 entries and the
full 32 KiB Miner history. There are **56.75 KiB free**, with **46.75 KiB** of
allocation headroom before the 95% write guard. Editor's maximum assembly
working set falls to **85,988 bytes / 87.47% of RAM**; Miner uses **6,392 bytes /
6.50%** including its complete software-SHA workspace and RAM code backing.

Removing one physical cache bank eliminates **32,768 storage cells**. Regular
storage/local-circuit emissions fall from **202,522,304 to 200,066,290 operations**.
Because these banks are vertically stacked, the regular-component horizontal
union remains **23,328 chunks**. It excludes synthesized logic and external
wires. The [current preflight](evidence/cache4-preflight-2026-11-14.json) and
[regular geometry](evidence/regular-geometry-cache4-2026-11-14.json) preserve that
scope; halving cache bytes is not a claim of halving the complete world.

## Smaller controller construction

The fresh one-bank controller has been mapped from the current configuration,
adapter RTL and generated header. Its complete original-I/O facade passes graph
conservation, physical placement guards, register-clock matching and an
independent exact block-state union.

| Controller and exterior signal delivery | Earlier 8 KiB mapping/layout | Current 4 KiB mapping/layout |
|---|---:|---:|
| Unique emitted blocks | 27,156,924 | 15,645,176 |
| Occupied X/Z chunks | 136,764 | 67,206 |
| Original port bits | 183 | 182 |
| Register clock pins | 115 | 115 |

That is **42.39% fewer blocks and 50.86% fewer occupied chunks** for this
controller-and-facade scope. The physical memory bank, decoder and external
memory routes are additional. The fresh mapping contains all 1,178 native cells;
it is not a claim that halving stored bytes halves the controller's cell count.
The separate same-graph comparison isolates the routing change: keeping original
port leaves near the root reduced the historical controller's occupied chunks
by 48.76% while preserving its exact 1,144-cell graph.

The [current source bundle and receipt](../experiments/memory_controller_runtime/current_four_kib/README.md)
retain every elaboration input, raw/normalized mapping, complete plan, accepted
manifest, exact union and rejected-address RTL checks. The new transaction
oracle exercises 601 frames and 49 complete storage checkpoints, comparing
1,605,632 stored bits through the real controller RTL. This oracle still models
storage; saved redstone execution remains a separate gate.

The [bounded production backend](BOUNDED_BACKEND.md) now selects this layout for
the complete one-bank cache. It records the chosen budgets and real clock-space
reservations in its manifest and retains every collision and clock-matching
rejection. Other partitions keep their existing policy, and the overall default
backend remains `flat`.

The complete cache geometry now also includes its physical storage, decoder and
all 77 external routes. Two independent collectors agree on every block state
and the same final digest:

| Complete cache fixture | Earlier 8 KiB | Current 4 KiB | Reduction |
|---|---:|---:|---:|
| Unique blocks | 37,102,012 | 22,805,546 | 38.53% |
| Occupied X/Z chunks | 192,026 | 118,781 | 38.14% |

The [complete-cache union receipt](../experiments/memory_controller_runtime/current_four_kib/evidence/connected-union-checkpoint.json)
binds both collectors and all 55 frozen input files. It preserves every current
controller cell, 182 original terminals, 115 register clocks, 32,768 stored bits
and 32 physical read-return nets. These are cache-fixture counts; they are not
the complete computer's footprint and cannot be added to the overlapping
regular-component subtotal. Native writing/readback and actual electrical
transactions remain separate checks.

## Faster native creation and readback

The native writer now resolves each used palette entry once when calculating
the canonical saved-chunk fingerprint. It then remaps integer palette IDs,
preserving the existing digest bytes, ownership bits and terrain checks.
Independent comparison against the actual earlier implementation passes
1,280 randomized cases and 27 malformed/boundary cases. Six saved-world ledgers
match exactly; altered block properties, ownership, protected terrain and invalid
palette indices are still rejected.

On a separate 64-chunk fixture with 15,936 circuit placements and 65,536 terrain
cells, median write/save time falls from 0.867 to 0.432 seconds, and independent
readback from 0.763 to 0.388 seconds. Paired write/readback improves 1.89 times.
These are small local fixture measurements, with operating-system caches not
flushed; they do not establish the complete cache's generation time. The
[independent review](../experiments/firmware_cache/evidence/native-fingerprint-independent-review.md)
retains all trials, exact sources and correctness checks. The frozen large-world
recovery continues with its original source bytes; subsequent runtime preparation
can separately bind the reviewed faster reader/writer.

## Verification and remaining physical proof

- The new instruction encodings pass 193 focused assembler/CPU cases, followed
  by an independent raw-encoding/flag/target review and retained negative tests.
- The combined current firmware batch passes **170 cases with no skips**,
  including all nine exact host-versus-in-world assembler comparisons, full
  editing/assembly limits, all app behavior, full Miner rendering and both SHA
  paths. Every app reaches idle with balanced call/data stacks.
- Original-layout Miner SHA executes **14,567 instruction fetches above byte
  4,095** in each tested zero/three-wait-state CPU run, matches the complete
  native and `hashlib` digest, preserves its public registers and raises no CPU
  fault. This isolated CPU bench uses a unified bus; it does not substitute for
  the following SoC address-routing test.
- The complete-SoC boundary test loads an 8 KiB executable, fetches a legacy
  two-word instruction across the cache/RAM boundary, calls into RAM and returns
  to cache, writes both regions, flushes, and clears the full app on Home. It
  passes both the fast backend and the production memory-controller backend.
  The test also checks real counters and the last byte of the original program
  window. The physical storage cells remain modeled in this RTL test.

The [combined app evidence](../experiments/firmware_cache/evidence/combined-app-validation.json),
[ISA review](../experiments/firmware_cache/evidence/compact-isa-independent-review.json)
and [backing-store review](../experiments/firmware_cache/evidence/cache-backing-independent-review.json)
record exact source hashes and limits. The one-bank adapter also has real RTL
boundary/reset/byte-merge tests, including rejected uninstalled addresses.

The [completion audit](../experiments/firmware_cache/evidence/software-completion-audit.json)
rebuilds and checks every installed source, executable and packed loader record,
the generated RTL header, all 32 entries and retained persistent data. The
[platform evidence inventory](../experiments/firmware_cache/README.md) distinguishes
the original broad run from its corrected capacity assertions and separately
executed hardware cases; tool-dependent skips are not counted as verification.

The final shortened image also passes all four
[production-controller SoC regressions](../experiments/firmware_cache/evidence/system-regression-real.json)
with no skips, including boot/save/reboot, typed-source assembly/execution, the
full 8 KiB executable boundary and replacement from owned heap staging. A fresh
[independent recovery review](../experiments/firmware_cache/evidence/recovered-cache-independent-review.json)
reproduces every app measurement and installed record and passes 99 focused
cases without skips. These results preserve the distinction between RTL storage
models and saved Minecraft blocks.

The subsequent clean platform run passes **1,049 tests with no failures**;
its **647 dependency skips** are recorded separately for the dedicated HDL,
native-engine and Amulet jobs. Root also verifies all five final-image resource
fault paths in the SoC and the corrected clock-spacing/recovery guards. See
[the platform review](../experiments/firmware_cache/evidence/platform-cache4-corrected-review.json)
and [root verification](../experiments/firmware_cache/evidence/recovered-root-verification.json)
for the exact source scopes; these results are not a single combined full-suite
or physical-cache runtime pass.

The connected physical cache still needs its own saved-world electrical and
persistence proof. Earlier two-bank mapping/world evidence is retained as an
explicit historical fixture with its original source hashes. The complete
accepted one-bank geometry is being written and reopened for that proof.
Complete ShamaOS world routing, timing, loading and end-to-end release acceptance
remain unfinished.
