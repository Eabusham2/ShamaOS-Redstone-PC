# Smaller applications and a 4 KiB physical cache

The implementation and measured before/after sizes are documented in
[CACHE_REDUCTION.md](../../docs/CACHE_REDUCTION.md). The original 8 KiB program
window remains in RAM; the cache holds the first 4 KiB. All nine complete app
sources and their features remain installed.

## Earlier source-only reduction

Before introducing short instruction encodings, shared app paths already
reduced Editor from 4,696 to 4,544 bytes and Files from 5,320 to 4,636 bytes.
The [source-only measurement](editor-files-source-reduction.json) retains that
baseline revision, exact source hashes and assembled byte counts.

Files shares list redraw setup, the Editor launch path, selected/candidate-name
rendering and text command submission. Its private renderer saves only the
registers its caller needs. The shared submission helper preserves the viewer
offset, frame boundaries and fences before text scratch is reused.

Editor shares the new-document path, writes either automatic extension through
one byte loop, preserves only live row/index registers around its private
renderer, and removes redundant register copies and constant additions. Byte
writes remain necessary because a filename can end at any alignment. Failed
saves retain the previous document mode and type.

That checkpoint passed 72 tests with no failures or skips. Coverage includes
all nine editable-source handoffs, 65,535-byte sources, 16,380-byte assembly
output, source-tail staging, ownership, folders, navigation, diagnostics,
save/rename/delete and last-good-binary retention. Added tests exercise every
filename alignment, the maximum automatic extension, failed saves before
New Text/New Program, and a GPU model that rejects overwriting submitted text
before its fence. Both renderers must finish events with balanced frame depth
and CPU stacks.

The original source measurement and regression remain available:

```sh
PYTHONPATH=src python experiments/firmware_cache/measure_editor_files.py
python -m pytest tests/test_firmware_cache_compaction.py tests/test_firmware_execution.py tests/test_software_resources.py
```

Running the measurement on current sources includes the later short forms;
the retained source-only JSON preserves the earlier isolated result.

## Evidence scopes

The retained results are separate checks. Their counts overlap and must not be
added into a fictitious single full-suite result.

| Evidence | What it establishes |
|---|---|
| `compact-isa-independent-review.json` | Legacy/short instruction semantics, flags, raw targets, mixed calls and stalled CPU execution |
| `combined-app-validation.json` and its Python reproducer | 170 passing app/CPU/in-world-assembler cases; exact host/RTL app binaries and both Miner hashing paths |
| `cache-backing-independent-review.json` | Services copy, flush, cleanup, accounting and the unchanged RAM execution window |
| `software-completion-audit.json` | Every installed source, binary, packed loader record, persistent entry and generated header matches the current image |
| `cache4-capacity-tests.xml` | 109 passing capacity, image, accounting, adapter and physical-layout checks |
| `cache-tail-tests.xml` | Initial 4 KiB-cache boundary execution in both fast and actual-controller SoC backends; this precedes the final shortened app image |
| `system-regression-source.json` | Private compilation inputs and exact final flash image for the subsequent full-SoC regression |
| `system-regression-real.json` and its JUnit/log | Four final-image production-controller SoC flows pass: boot/save/reboot/Editor handoff, typed-source assembly and execution, full 8 KiB cache/RAM-boundary execution and cleanup, and replacement from owned heap staging |
| `recovered-cache-independent-review.json` and its JUnit | A fresh independent review reproduces every app size, all 32 installed records and source identities, followed by 99 focused tests with no skips |
| `platform-cache4-corrected-review.json` and its complete JUnit/log | A fresh Linux/Python 3.12 platform run passes 1,049 cases with no failures; 647 unavailable hardware/world dependencies remain explicit skips |
| `recovered-root-verification.json` | Five final-image RAM/heap/code/VRAM/protected-flash recovery cases pass; 48 distinct recovery/clock guards pass, including the separately executed actual-engine clock case |
| `platform-cache4-tests.coverage.json` | Original broad platform result, every corrective rerun, skip categories and functional source deltas |
| `root-io-independent-review.json` | Independent graph, port, clock and manifest review for the historical two-bank controller routing comparison |
| `root-cache-root-io.xml` | Root's 17 passing actual-engine/structural checks after integrating that planner option |

The normal platform run had **998 passes, eight stale capacity assertions and
635 tool-dependent skips**. Twenty focused cases then passed, covering all eight
original failures. The combined case inventory therefore has 1,006 passing or
corrected platform cases, with the original skips retained. This is not a
single clean full-suite run. The original failed log and JUnit remain unchanged.
The real RTL history-pressure test separately passes after adding enough test
data to retain the old near-full condition; production files are not replaced
or removed. Hardware tests run with the restored HDL/native tools have their own
evidence and are not inferred from platform skips.

## Reproduction

From the repository root, with the required toolchains available:

```sh
python tools/measure_cache_reduction.py --report build/app-cache-reduction.json
shamaos software-usage --report build/software-usage.json
python -m pytest tests/test_compact_instructions.py tests/test_compact_isa_audit.py tests/test_cpu_rtl.py tests/test_hardware_assembler.py
python -m pytest tests/test_soc_integration.py
```

The exact restored baseline for executable size comparison is `184faa9`. The
measurement tool needs that commit in local Git history. The short instruction
forms are explicit; no source-line estimate or automatic branch relaxation is
used. Complete physical-cache execution, whole-machine timing/loading and the
release remain separate acceptance gates.
