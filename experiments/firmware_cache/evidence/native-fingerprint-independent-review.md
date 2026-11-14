# Independent native fingerprint review

The palette-ID optimization preserves the historical canonical digest and block
ownership behavior in the reviewed cases. No production defect was found. The
actual small saved-world fixture ran faster with identical saved ledgers and
decoded states. These timings describe this fixture and environment only; they
do not measure the full cache world, physical cache creation, redstone execution,
or cold-storage performance.

## Source identity and method

- Historical implementation: `184faa9d28d871b77ff4de73c022b620d57536a7`,
  `src/shamaos/worldio.py` SHA-256
  `08e163497c981357b3aa4ce444709f38769001fb512700b0ffd2a8fc4ebda69c`.
- Candidate implementation: `f7ae2e413df787732dbd976b939bb92330f4c155`,
  `src/shamaos/worldio.py` SHA-256
  `389e525bc0a3bb5a47b53d6dae1362177f7d865dbec4f63c9fd4c84bbfaf3bd0`.
- The audit compiles the **actual historical method AST**, obtained with
  `git show`, against an isolated candidate module. It verifies that the rest of
  the entire module AST is unchanged. It swaps only `_Chunk.fingerprint` within
  that isolated module, and rechecks the candidate file hash after completion.
- Environment: Python 3.12.14, nbtlib 2.0.4,
  Linux 6.18.57 x86_64 with glibc 2.41. Timings use `time.perf_counter`.

The change moves repeated `BlockState` equality/hashing from all 4,096 positions
to the used palette IDs, then packs the same canonical integer IDs. Canonical
state sorting, chunk and section coordinates, assigned bitmap bytes, and assigned
bit counts remain unchanged. Duplicate palette aliases resolve to the same
canonical value. A used invalid index still raises. Unused palette entries remain
absent from the digest.

The native decoder independently checks valid palette IDs and drops uniform
unowned air sections. Therefore a synthetic 24-section microbenchmark describes
that synthetic digest workload. The decoded-chunk measurement below uses the
sections actually retained by the reader.

## Deterministic differential verification

Seed `3394117670` (`0xCA4E2026`) produced 256 chunks with signed coordinates,
1–6 sections, dense/sparse/uniform-air content, duplicate/unused palette entries,
and empty/full/sparse ownership bitmaps. There were **1,280 exact comparisons**
of both digest and assigned block count against the historical method:

| Case | Matches |
| --- | ---: |
| Original generated chunk | 256 |
| Reordered palettes with duplicate aliases and unused entries | 256 |
| State mutation | 256 |
| Ownership mutation | 256 |
| Chunk-position mutation | 256 |

Every state, ownership, and position mutation also changed the digest as
expected. Another 27 direct private-method cases matched: 18 invalid indices
raised `IndexError`, six valid Python negative-index cases retained their
historical result, and three wrong index-array lengths raised `struct.error`.
The private negative-index compatibility check does not claim such indices are
valid serialized block palettes; the decoder rejects invalid serialized IDs.

## Actual native writer and saved-world validation

The fixture creates 64 chunks over negative and positive chunk coordinates,
with four protected terrain layers and sparse hardware in four higher sections.
Each chunk also contains explicit owned air. It has **15,936 assigned placements**
and **65,536 exact terrain cells**, or 81,472 observed nonair/owned-air cells.
The writer cache holds eight chunks, so creating the 64-chunk world exercises
chunk flushing, encoding, save/close, and the expected SQLite ledger.

Each method ran three trials, alternating order. Each timed readback uses a
fresh `validate_native_world` reader over the saved region files. OS caches are
not flushed. The input placements are constructed before timing; creation,
insertion, encoding, flushing, save/close, and validation are timed.

| Measured phase | Historical median | Candidate median | Speedup |
| --- | ---: | ---: | ---: |
| Create, write, encode, save and close | 0.8667 s | 0.4320 s | 2.01× |
| Independent saved-world validation | 0.7631 s | 0.3880 s | 1.97× |
| Paired write plus validation | 1.6306 s | 0.8606 s | 1.89× |

The combined median is calculated from each trial's combined time, rather than
adding separate phase medians. These are short local measurements without a
confidence interval or a guarantee for other world layouts or systems.

All six saved-world ledgers matched exactly, including every chunk digest and
block count. The first saved world from each method also underwent an independent
per-cell state and ownership comparison against the input and protected terrain,
without using `_Chunk.fingerprint` for the semantic comparison. Exact identities:

- Canonical ledger SHA-256:
  `7dde1533e24ff7d0d7363c1ad6573e8604b087628a32c44fea68adfbe371e837`.
- Ordered decoded state and ownership SHA-256:
  `c4e204a08aade46ca61ec252b8a2cfb263134cc014762d74214f949958fd85b1`.

The actually decoded chunk retained six sections, 24,576 palette indices, and
249 assigned cells. Its median isolated fingerprint cost was **5.830 ms →
1.176 ms (4.96×)** over three batches of 32 calls, with identical digest and
count. This isolates fingerprint cost and is separate from the full writer and
validator timings above.

Four saved-region corruptions were also applied to a temporary fixture. Both
implementations rejected each with exactly the same error:

| Mutation | Rejection |
| --- | --- |
| Valid repeater property changed | Saved block-state readback mismatch |
| Ownership bit changed | Saved block-state readback mismatch |
| Protected terrain marked as owned | Generated blocks touch protected ground |
| Packed palette ID made invalid | Block-state palette index out of range |

Restoring the original chunk made both validators pass again. The temporary
worlds were removed afterward; the ongoing full physical cache run was untouched.

## Reproduction and evidence

Run from the repository root, with the candidate checked out and the historical
baseline commit available in Git:

```sh
python experiments/firmware_cache/audit_native_fingerprint.py \
  --candidate-source src/shamaos/worldio.py \
  --report build/native-fingerprint-independent-review.json
```

The committed `native-fingerprint-independent-review.json` contains all raw
timing samples, exact hashes, matched-case counts, and rejection messages. Its
`audit_source_sha256` binds the executed audit script. The adjacent `.txt` retains
the original execution output. This review adds independent evidence and does
not change production code.
