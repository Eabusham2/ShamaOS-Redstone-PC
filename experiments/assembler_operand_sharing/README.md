# Shared assembler immediate lookup

This isolated candidate selects the immediate token once and shares its numeric,
definition, label, and syscall lookup across instruction formats. The instruction
formats, exact operand-count checks, diagnostics, DMA interface, and FSM states
are preserved. It adds no cycle to assembly.

## Recorded equivalence

The portable differential miter passed **232 runs: 116 sources in each of two
DMA modes**. It compares both implementations on every clock, including FSM
state, completion, active DMA address/control/strobes/write data, diagnostics,
and the output words. The valid cases must also match the host assembler, and
the destination guard word must remain untouched.

The cases include all nine shipped apps; all seven immediate formats with
numeric, definition, label, and syscall operands; same-line labels; missing,
extra, unknown and out-of-range operands; invalid registers/conditions; and
non-immediate instructions after a line containing an immediate. The second
DMA mode inserts deterministic backpressure at the seventh/eleventh-cycle
boundaries. Across both modes, the models matched **761,468 clocks, 316,068
read transactions, and 11,776 write transactions**. This is differential
testing over the recorded corpus, not a proof over every possible input.

| Shipped app | Binary bytes | Cycles with ready DMA | Cycles with stalled DMA |
| --- | ---: | ---: | ---: |
| Editor | 4,696 | 67,555 | 74,932 |
| Files | 5,320 | 82,267 | 91,248 |
| Miner | 6,904 | 102,541 | 113,347 |
| Paint | 868 | 12,024 | 13,253 |
| Settings | 772 | 11,114 | 12,293 |
| Terminal | 1,920 | 28,385 | 31,329 |
| Calculator | 1,824 | 28,573 | 31,910 |
| Desktop | 232 | 4,353 | 4,883 |
| Monitor | 52 | 966 | 1,106 |

Each entry is identical for baseline and candidate. The testbench is
`tb_asm_differential.sv`; `evidence/equivalence-report.json` retains every source
hash, output hash, result, and measured cycle/transaction count. Compiled models
and raw per-case logs belong in a disposable build directory.

## Size measurement boundary

The measurement uses **Yosys 0.33**, the production
`synth/redstone_aig.ys` flow, and the production `SOC_RTL_FILES` order. The
reference is the complete retained `u_asm` mapping from CI run `43333367519`,
artifact `13999023957`, whose effective preprocessed assembler matches
`c3315eb35b3d52c1828e0d5c0c93dc0139ebd6e9`. Its mapped JSON is 283,098,586 bytes
with SHA-256
`78a6ab3abcbbe4bde94acaf6bbfdb8388dbeebf301757f34c49172f97b1664ee`.

The original and current baseline's only referenced boot macro is
`SHAMA_RAM_BYTES = 32'h00018000` (96 KiB). Their preprocessed source hash is
`8f8491e4bf6099d68abbf38642b814cbda9d807dfdf35dcf085743e9d55c2702`.
The candidate retains that effective macro. Source hashes are:

| Source | SHA-256 |
| --- | --- |
| Baseline assembler | `34d04460a043e31b45d412b93762b7ebfd413c718679a73d1fad8f0f0481fc65` |
| Shared-lookup candidate | `7f2ec4266e960dfa9083bff08aabc49cdea578b5bde8ecf5e281338d6f925770` |

`measure_size.py` freezes the complete input set before synthesis, binds it to
hashes and tool version, and rechecks those bindings before accepting a result.
It rejects another checkout's imported lowering/template implementation. It
counts raw mapped cells and then calls the **same sequential lowering function
used by production** before counting supported native cells. Each template's
actual emitter supplies its placement count, unique block count, and a hash of
the literal native states. The complete reference must reproduce **559,453
lowered native cells and 23,025,358 literal cell blocks** before a comparison
can pass.

Intercell wiring, top-level terminals, constant source blocks, placement spacing,
other partitions, and memory/screen/input fabrics are excluded. The experiment
cannot establish a whole-computer chunk count. Optional unit-gate depth and
fanout statistics ignore physical wire length, repeaters, setup/hold time, and
clock skew; they cannot establish a redstone clock-speed improvement.

## Measured isolated result

The complete same-flow comparison passed. Sharing saves **77,508 native cells
(13.85%) and 2,767,590 literal cell-template blocks (12.02%)**. Every one of the
17,486 mapped register bits is retained. The reductions are 39,769 AND cells and
37,739 NOT cells; the sequential-control NAND inventory is unchanged.

| Measurement | Baseline | Shared resolver | Change |
| --- | ---: | ---: | ---: |
| Lowered native cells | 559,453 | 481,945 | −77,508 (−13.85%) |
| Literal cell-template blocks | 23,025,358 | 20,257,768 | −2,767,590 (−12.02%) |
| Positive-edge DFFs | 17,486 | 17,486 | Unchanged |
| Maximum unit native-gate depth | 1,208 | 1,256 | +48 (+3.97%) |
| Maximum fanout excluding DFF clock pins | 672 | 491 | −181 (−26.93%) |
| Maximum fanout including clocks | 17,486 | 17,486 | Unchanged |

The depth increase is a tradeoff: fewer cells and lower maximum data fanout do
not establish a faster physical clock. The RTL miter preserves clock counts,
while the routed redstone implementation still needs its complete timing proof.
No chunk reduction or physical clock-speed improvement is inferred here.

The candidate mapped JSON is 241,805,889 bytes with SHA-256
`96665222c9fce4928fd38418bbe43aa9788655741b5fc150a79141d1d742023a`.
The compact `evidence/size-comparison.json` binds it to the source/preprocessed
hashes, exact tool version, mapping script, production lowering and actual
template emitters. The accepted baseline reproduces its retained resource
counts exactly. These figures describe the **resolver-only source hash above**;
the separate later assembler declaration/alias correction was not part of this
measurement. Combined-source physical counts require another synthesis.

| Complete measured process | Elapsed time | Peak RSS (KiB) |
| --- | ---: | ---: |
| Candidate synthesis | 417.914 s | 1,522,428 |
| Baseline native counting | 11.119 s | 1,258,724 |
| Candidate native counting | 9.669 s | 1,085,992 |

The three phases ran sequentially. All 15 small experiment guards passed, and
the portable 232-run differential corpus passed after its paths were made
independent of this workspace.

## Reproduce

The recorded isolated size result uses `frozen_shared_resolver.sv`, whose bytes
match the candidate hash above. The production assembler now also contains the
separate declaration correction. Running the commands below on the current
checkout checks that combined implementation and records its own source hash.
An additional root run of the combined source passes **177 focused tests** and
all **232 differential runs**; see `evidence/integrated-root-review.json` and
`evidence/integrated-equivalence-report.json`. The corrected declaration failures
have separate tests because their old baseline behavior is intentionally fixed.

To reproduce the isolated measurement exactly, create a disposable checkout of
`c3315eb`, copy this experiment directory into it, and replace that checkout's
`rtl/shama_asm_accel.sv` with `frozen_shared_resolver.sv`. Run the commands from
that checkout with its own `PYTHONPATH=src:tests`. The baseline commit and frozen
candidate are both retained; no unpublished local commit is required.

From the repository root, use its own Python sources and the installed Icarus
tools (`iverilog` and `vvp`):

```sh
PYTHONPATH=src:tests python experiments/assembler_operand_sharing/check_equivalence.py \
  --output build/assembler-sharing-equivalence

PYTHONPATH=src:tests python -m pytest tests/test_assembler_operand_sharing_experiment.py
```

The output directory must be new. To reproduce size, recover the complete
baseline mapped JSON and its `effective-rtl-provenance.json`, then prepare a
new disposable output directory:

```sh
PYTHONPATH=src:tests python experiments/assembler_operand_sharing/measure_size.py \
  --prepare-only \
  --baseline-netlist /path/to/partition-u_asm-shama_asm_accel.json \
  --baseline-provenance /path/to/effective-rtl-provenance.json \
  --yosys /path/to/yosys-0.33 \
  --output build/assembler-sharing-size
```

Preparation only reads small source/provenance files and performs preprocessing.
Reserve an exclusive large-process slot before resuming:

```sh
PYTHONPATH=src:tests python experiments/assembler_operand_sharing/measure_size.py \
  --run-prepared --output build/assembler-sharing-size
```

The script runs synthesis and the two JSON counts sequentially in separate
processes. Its external parent records complete-process elapsed time and peak
RSS using `os.wait4` on Linux/macOS, normalizing macOS bytes to KiB. A failed
phase retains diagnostic logs and cannot produce a passed comparison. A fresh
directory is required for a new run; existing mapped/measurement outputs are
not silently reused. Python with disabled assertions is rejected.

Preserve only the compact scripts, miter, reports, and hashes in Git. Large
mapped JSON, frozen source copies, compiler outputs, and raw case logs are
reproducible build artifacts.
