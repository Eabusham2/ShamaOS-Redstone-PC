# Next complete controller and memory runtime target

The **8 KiB cache adapter** is the selected next integration target. Its
complete current mapping is smaller than the current VRAM adapter, and its
two full 4 KiB banks are the smallest installed memory fabric. This is a
scoping result; no connected cache-controller runtime or placement is claimed.

| Complete partition | Installed storage | Native cells | DFFs | Cell-template blocks | Scalar input/output pins | Nets with sinks |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `shama_cache_adapter` | 8 KiB, 2 banks | 1,144 | 115 | 50,844 | 104 / 79 | 1,248 |
| `shama_vram_adapter` | 16 KiB, 4 banks | 1,152 | 116 | 51,132 | 104 / 81 | 1,256 |

The cell block counts exclude routing, port terminals, storage, decoders and
returns. They do not provide a connected-world chunk count. RAM/flash mapped
counts were not recovered for this scoping comparison; their capacities remain
96 KiB and 200 KiB. No capacity, pin or state is removed to fit this target.

## Provenance and complete interface

These are complete `c3315eb35b3d52c1828e0d5c0c93dc0139ebd6e9` CI artifacts,
created by Yosys 0.33 (`2584903a060`) through the production ABC flow in
`synth/redstone.ys`. The cache artifact ID is `14007345466`. The captured
mapped/ZIP hashes, source hashes, all port widths, native cell inventories and
clock sink counts are in `evidence/cache-vram-scope.json`. The memory-adapter
RTL, generated header, mapping script and production lowering source were
independently checked byte-for-byte against that commit. Parsing both small
complete mappings through the production graph loader found no unresolved
constants or ambiguous drivers/pins.

The cache's 183 scalar pins include the original request address/data/strobes,
read/write request, clock/reset, ready/read response, both bank selects, all ten
row-address bits, read enable, write commit, 32 write-data outputs and all 32
selected-word inputs. Its single original clock drives all 115 mapped DFF clock
pins. The seven controller states perform latched direct addressing, read
settling, byte merging, commit, post-commit holding and response. No rotating
row seek is part of this wrapper.

## Required connected proof

The next fixture must keep every mapped controller cell and pin, two complete
1,024-row × 32-bit banks, the real ten-bit binary decoder, local bank/write
gates, and the upward read-return wiring. Use the production cache component's
layer spacing and decoder offset from `memory_specs`/`default_origins`; the
other fabrics remain separate proof targets. Every support begins above the
superflat grass. The controller outputs and selected-word inputs must be wired
to the native bank circuit in one loaded, ticking world. Drive only the original
external request/reset/clock pins; a sampled host-memory response would not
establish this integration.

Acceptance should cover both banks' first/last rows; opposing bit patterns;
aligned byte/halfword lane merges on each side of the 4 KiB boundary;
held-valid consecutive requests; rejection of reads and writes addressed at
`0x2000` or higher; reset suppression of writes without erasing stored bits;
and a quiescent native
snapshot followed by fresh-process continuation. Check all 65,536 stored bits
at transaction boundaries. Clock/data timing must include the complete decoder
and return path and all 115 clock destinations, with a deliberately too-fast
negative case where a timing violation is observable.

The interface has four byte-lane strobes and no transfer-size field. The master
supplies aligned data/strobes; the adapter selects the word using address bits
above bit 1. An operation spanning `0x0fff` to `0x1000` requires two bus
requests. An adapter test must not invent automatic cross-word splitting or
size-based rejection that this interface does not express.

The existing full 4 KiB bank proof uses physical row-select inputs directly.
It proves complete bank storage, writes and native loading, but does not
substitute for the binary-decoder or mapped-controller integration above.

## Runtime preparation still needed

Controller placement, every inter-domain contact, the external memory routes,
and the resulting loaded-world memory/timing bounds remain unmeasured. Sparse
native execution supports the complete signed-coordinate world and actual
redstone updates, but it currently has a global trace flag. A selective trace
output set would let this proof retain all storage assertions while recording
controller/clock/contact traces without storing 65,536 memory-bit samples on
every tick. Such a change needs its own equivalence and validation checks.
