# Row tiling feasibility, 2026-11-13

All cases emit the same 1,024 × 32 physical storage bits (4 KiB). The tiled candidates include actual binary address, data, enable and isolated selected read-return wiring. The 10-bit row comparisons retain the global logical address. Every support is at Y −60 or higher above grass at −61. No production source was changed.

| Layout | Block dimensions X × Z | Occupied chunks | Unique blocks | Perimeter change | Chunk change |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 × 1024 rows | 305 × 6,173 | 7,715 | 3,151,814 | +0.00% | +0.00% |
| 2 × 512 rows | 655 × 3,695 | 9,154 | 3,295,840 | -32.85% | +18.65% |
| 4 × 256 rows | 1,311 × 2,159 | 10,680 | 3,460,328 | -46.43% | +38.43% |
| 8 × 128 rows | 2,623 × 1,391 | 13,732 | 3,789,304 | -38.04% | +77.99% |

The current eight-tile wiring emits 500,224 extra data/return placements plus 126,872 address/enable placements. Repeated local headers account for the remaining difference. Splitting the row decoder into seven local bits and a separate three-bit tile qualifier could remove some gates, but lowering decoder height does not remove the front/rear chunk footprint of the 32 independent data buses.

Every profile has zero conflicting block states at shared coordinates. That is only a geometry check. Adjacent active-wire isolation, native execution, read/write retention and settled timing have not been proved for these candidates. There is no runtime speed claim.

The four-tile case reduces the bounding perimeter most, by 46.43%, but increases occupied chunks by 38.43%. The two-tile case still adds 18.65% occupied chunks. This does not meet the requested chunk reduction, so the existing tested geometry remains the default. A future experiment must first reduce the cross-tile fanout/return header footprint.

Reproduce from the repository root using `PYTHONPATH=src:tests python experiments/memory_row_tiles/measure.py --tiles 4 --output row-tiles-profile.json`. The experiment source hash is recorded in each profile; preparation uses about 135 MiB peak and 12–14 seconds per case. Large complete-native-bank fixtures were not rerun for this rejected geometry.
