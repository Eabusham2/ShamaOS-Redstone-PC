# Exact lazy native section storage

This directory retains the reviewed candidate source. Its exact storage
implementation has also been promoted into `tests/mchprs_sparse_runtime`; the
frozen baseline binary and original failure evidence remain separate. The
pinned MCHPRS redstone implementation and audited pending-tick queue are
unchanged. Only the host representation of imported world sections changes.

Every imported section is retained and counted. A uniform section stores its
exact state ID; a mixed section stores the original native `ChunkSection`.
The first differing write expands a uniform section into a native section.
Restoring all changed coordinates to the original uniform value, or clearing
every non-air block, can compact it again without dropping a block. The complete
native section inventory, terrain, palette validation, comparator entities and
scheduled updates remain mandatory.

`World::get_chunk` and `get_chunk_mut` still return real upstream `Chunk`
references. On demand, the complete chunk materializes and immediately becomes
authoritative for every later block, entity and snapshot access. The inactive
compact input copy is discarded on the first mutable access. Normal redstone
execution uses the World's block/entity interface and does not request a full
chunk, so initialization and snapshots use exact section views without expanding
all empty sections.

The accepted cache-controller stream contains 895,464 uniform air sections and
169,416 mixed sections. The original uniform-section arrays alone occupy
1,833,910,272 bytes; their single-entry palettes add 3,581,856 bytes. Eliminating
that allocation is a host memory improvement, not a reduction in saved Minecraft
blocks or a change to redstone timing.

## Validation

- Twelve Rust tests cover uniform expansion/restoration, every section coordinate,
  native chunk mutation authority, wide palettes, scheduling, and byte-identical
  snapshots before and after materialization.
- The differential helper runs all seven existing saved fixtures in both legacy
  v1 and uniform v2 form. It verifies every converted single-state palette index
  and preserves all stream records. The frozen original and candidate runners
  match 9,426,080 trace samples and 22,504 assertions, including saved comparator
  state and intentionally too-fast pipeline transfers.
- All 115 existing native loader, selective-trace and quiescent-persistence tests
  pass with the corresponding lazy basic/terrain binaries, without skips.

The evidence is in `../evidence/lazy-native-storage-equivalence.json` and
`../evidence/lazy-native-regression.json`. The changed allocation count is checked
separately: exactly 2,052 bytes are avoided per v2 uniform section. Every other
semantic report field and requested snapshot must match.

The manifest pins the same upstream revision. A normal `cargo test --locked
--release --manifest-path experiments/memory_controller_runtime/lazy_native_runner/Cargo.toml`
builds the unmodified registry variant. The terrain variant uses only the
repository's existing audited native terrain registry patch; the generated local
manifest points its binary source at this directory and patches
`mchprs_blocks` to that prepared registry. No `mchprs_redstone` or `mchprs_world`
source changes are required. The differential evidence binds the exact binary,
sources, scenario and world stream hashes.

`../resume_controller_clock.py` can reuse the accepted complete-controller world
stream and its original safe scenario. It keeps all 115 physical register-clock
observations, requested pulse lengths and the too-fast negative case. Runtime
retry results are separate evidence: passing these storage regressions does not
establish the complete controller's clock gate or connected two-bank operation.
