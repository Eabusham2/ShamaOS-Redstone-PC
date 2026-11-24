# Guarded cache clock reservation

The complete cache workflow on `99ee940` stopped during geometry in
[run 45340109477](https://github.com/Eabusham2/ShamaOS-Redstone-PC/actions/runs/45340109477).
The real matcher rejected the clock branch from cluster 27 to cluster 25:
it needed 36 more redstone ticks than its private route could provide.
No world or electrical pass was produced by that run.

The retained retry uses the exact 1,147-cell CI graph, with all 182 original
pins and 116 register clocks. It extends clock-routing domain 6 and its sibling
from 160 to 176 blocks of physical reservation. Every other reservation stays
at 160. Normal collision, contact conservation and repeater matching remain
enabled. The complete controller placement passes and contains 17,597,778
unique blocks; all register paths have 3,163 redstone ticks of matched repeater
delay. The exterior facade adds a common input route and is not included in
that timing figure.

`evidence/receipt.json` binds the original failure, exact mapped graph, complete
plan, compressed geometry, execution script, tracked-source inventory and
modified source. The source is the named Git commit plus the retained patch;
the replay script's original absolute paths are recorded verbatim. Source and
artifact digests must be checked before any replay.

For a fresh production reproduction, use `tools/validate_connected_cache.py`
with `--stop-after geometry` as documented in
[CACHE_VALIDATION.md](../../../docs/CACHE_VALIDATION.md). Running through `audit`
is still required for a complete cache result. This receipt proves controller
geometry and structural clock matching. It does not prove the storage-bank
union, native save/readback, electrical pulse timing, transactions, persistence
or complete-machine operation.
