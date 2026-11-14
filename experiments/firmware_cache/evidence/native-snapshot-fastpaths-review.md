# Exact snapshot restoration fast paths

Functional commit `306d353` removes two redundant scans while retaining the same
validation and serialized native output. It changes only `_section` in
`mchprs_snapshot.py` and `_chunk_nbt` in `worldio.py`, plus portable regression
tests. The baseline is `f7ae2e413df787732dbd976b939bb92330f4c155`, which already
contains the separately reviewed fingerprint optimization.

## Behavior and boundaries

A uniform section still passes its state through the exact canonical-state
validator. Its nonair count is then exactly zero for air or 4,096 for every other
supported state, avoiding 4,096 repeated comparisons. The declared count still
passes the same strict integer/range/equality checks. Packed sections retain
their existing decoder and full count calculation.

Native NBT encoding still resolves every used palette ID while building its
compact palette. It enumerates all block positions for comparator entities only
when a used palette entry is a comparator. The entity loop itself is unchanged.
An invalid used index still fails before this guard; an unused comparator entry
remains irrelevant. Comparator coordinates, powered-state output, ownership
bytes, and section serialization remain identical.

The snapshot stream has no native ownership field. Its existing zero-ownership
restoration behavior is unchanged. Separately, the direct NBT differential and
golden tests include nonzero ownership and explicit owned air, and verify that
those native bytes and decoded ownership remain exact.

## Source identity

| File | Audited candidate SHA-256 |
| --- | --- |
| `src/shamaos/mchprs_snapshot.py` | `6de6d761158a6cdcf2ce8fbc05fac1fea3c317a4ce69d3e9f94184624a2f1805` |
| `src/shamaos/worldio.py` | `f02cb999c0ecde4841d042def5f4b5c2200509cee025977c340eabd03e5d10d8` |
| `tests/test_native_snapshot_fastpaths.py` | `7ba3a5c154456104147c5c43838fd86f673291b97821c5197d7d2e00703c3ba4` |

The audit compiles the actual historical `_section` and `_chunk_nbt` ASTs from
Git. For each module, it verifies that the remainder of the module AST is
identical. It compares the real baseline functions with the candidate, and
checks candidate source hashes again at completion.

With deterministic seed `1590104102`, all comparisons passed:

- 128 uniform sections, including air, normal blocks, repeaters and comparators.
- 128 packed sections with valid exact states and counts.
- 128 random chunks with exact serialized NBT equality and decoded ownership.
- Seven full NBT byte goldens captured from the baseline, including owned air,
  an unused invalid comparator, sparse comparators, and 4,096 uniform comparators.
- Fifteen malformed cases with identical exception types and messages, including
  noncanonical uniform states, wrong or boolean counts, invalid used palette
  indices, and a used comparator missing its powered property.

## Focused tests and actual-engine persistence

**133 tests passed, zero skipped, zero failures/errors, in 27.932 seconds.**
The 27 new portable cases ran alongside the existing native-world, fingerprint,
and MCHPRS snapshot tests. The latter used the already built, pinned terrain
runner, so actual-engine persistence tests executed rather than skipping.

The actual-engine fixture writes and saves 16 bytes of memory. It independently
checks all 128 stored bits in the native file before reopening in a fresh runner
process. The saved analog comparator strength of 12 survives restoration and
reopening. Every quiescent section and comparator remains unchanged after the
fresh process reads the memory; nonempty pending work is rejected.

That fixture restored 38 chunks and 912 sections with 129 comparators, preserving
the original 37 source chunks plus one chunk created during runtime. This is a
focused persistence proof; its report correctly retains
`complete_machine_runtime_verified: false`.

Runner SHA-256:
`74e11d2563d9b70f8d21b1a030124c155cf617c241366d49cbd4da4b3f882f31`.

The test command was:

```sh
SHAMA_MCHPRS_TERRAIN_RUNNER=/workspace/scratch/b1e71409d064/native-runtime/target/release/shama-sparse-world-check-terrain \
PYTHONPATH=src:tests pytest \
  tests/test_native_snapshot_fastpaths.py tests/test_mchprs_snapshot.py \
  tests/test_native_world.py tests/test_native_fingerprint_compatibility.py \
  --junitxml=build/native-snapshot-fastpaths.xml
```

## Representative native restoration timing

The benchmark restores a synthetic snapshot derived from an actual saved native
fixture: **64 chunks, 1,536 sections, 1,152 uniform sections, 81,536 nonair blocks,
and 128 comparators**. It covers the complete restore call, including checksum
and provenance validation, section decoding, native encoding, region writes and
compaction, independent saved-world readback, and atomic destination publication.
Fixture construction and output identity inspection are outside the timed call.

| Complete native restore | Historical method | Candidate |
| --- | ---: | ---: |
| Trial 1 | 6.2222 s | 5.3160 s |
| Trial 2 | 6.5385 s | 4.7131 s |
| Trial 3 | 6.5391 s | 4.9419 s |
| Median | **6.5385 s** | **4.9419 s** |

This is a **1.32× speedup**, or approximately **24.4% less time**, on this fixture.
Trial order alternates. Environment: Python 3.12.14, nbtlib 2.0.4, Linux 6.18.57
x86_64 with glibc 2.41. OS caches were not flushed; these short local measurements
are not a guarantee for other layouts or systems.

All six output NBT inventories and restoration reports match exactly. Each
restored chunk contains the expected comparator strengths 0 and 12. The ordered
full-NBT inventory SHA-256 is
`92e187b511a68b26a24ccf4a30ad9c72c7d8ec9b28d6c106e3ed85c88eb5d2c7`.
The source world and input snapshot remain unchanged.

The benchmark measures restoration of a static, synthetic snapshot. It does not
perform engine execution, full-cache restoration, or a complete-machine warm
runtime proof. The actual-engine regression above supplies the separate focused
execution check. Existing large-world recovery was untouched.

## Evidence and reproduction

The adjacent `native-snapshot-fastpaths-review.json` retains raw measurements,
baseline/candidate hashes, comparison counts, and the executed audit script hash.
`native-snapshot-fastpaths-tests.xml` and `.txt` retain the full focused result;
`native-snapshot-fastpaths-actual-runtime.json` retains the actual-engine report.

With the baseline commit available in Git, run from the repository root:

```sh
python experiments/firmware_cache/audit_native_snapshot_fastpaths.py \
  --report build/native-snapshot-fastpaths-review.json
```
