# Reproduce complete physical cache validation

The **Validate complete physical cache world** Actions workflow runs the current
4 KiB cache through production placement, native Minecraft world writing,
independent readback, actual redstone execution and save/reopen. It is an
engineering check for one memory subsystem. It cannot certify the whole PC or
publish a finished-world release.

## Run it

Open the workflow in GitHub Actions, select the intended revision and use
**Run workflow**. Leave the last stage at `audit` for the complete check. The
default clock half-period is 12,000 redstone ticks; the test verifies the actual
saved circuit's clock pulses before using that rate for transactions.

Before expensive placement, the supplied native runner must load a small saved
grass world and correctly toggle its physical lever and lamp. The check rejects
an unpatched runner or an executable that returns success without running the
circuit. Preparation also compares all fresh RTL storage words against the
separately retained transaction oracle.

Stages run in this order:

| Stage | Required evidence |
|---|---|
| `prepare` | Fresh mapping and the real controller RTL's 601-frame reference sequence |
| `geometry` | Production layout, every original connection, physical clock balancing and retained placement manifest |
| `world` | Two independent block-union checks, native world write and independent readback |
| `runtime` | Every saved chunk/section exported for the actual redstone runner, with exact physical input/output bindings |
| `clocks` | Every register's delivered pulses, including their spacing, and a deliberately excessive clock rate that must fail |
| `transactions` | Full reads/writes and 49 full-storage checkpoints, followed by saved-state restoration and a fresh process |
| `audit` | Independent verification of the original artifacts, all 1,605,632 stored-bit assertions and all 32,768 reopened bits |

Choosing an earlier stage deliberately leaves full validation pending. A zero
exit status from a partial run is **not** a complete cache result. The final
report must say `complete-connected-cache-validation-passed`, and its independent
audit must say `complete-cache-native-transactions-and-reopen-independently-verified`.
Neither report may mark the complete machine verified.

## Run locally

Use a Linux checkout with Python 3.12, Yosys, Icarus Verilog and the pinned native
terrain runner. The test-tool export workflow can provide the binaries; their
source identity and hashes are retained. Install the package with its `dev`
dependencies, then run:

```sh
python tools/validate_connected_cache.py \
  --output build/cache-validation \
  --runner /absolute/path/to/shama-sparse-world-check-terrain
```

The output directory must be new. The command copies the tracked source into
that directory and detects changes to source files, selected tools or retained
stage reports. A changed-and-restored input also invalidates the run. Keep the
checkout unchanged until the run finishes.

This is a large physical test. For runs that include a world, the hosted
workflow checks for at least 12 GiB of free disk before starting, bounds each
writer/engine process to 4 GiB, and
gives execution a five-hour deadline so evidence can be uploaded before the job
ends. These are resource limits, not a promised completion time or proof that
the complete PC is playable at that size. A limit failure remains a failed or
incomplete experiment; it never becomes a pass.

## Keep and inspect the evidence

Two artifacts are retained for 14 days. `cache-diagnostics-*` contains concise
progress and stage reports. `cache-physical-evidence-*` retains the source
snapshot, mapped circuit, complete placement, native save, imported world,
clock traces, transaction snapshots, reopen results and independent audit
produced before the run ended. Download valuable artifacts before they expire.

Diagnostics alone are not an independently reproducible electrical proof.
Retain the full physical artifact as well. No failed or partial file is
silently reused by the orchestration command. Existing strict experiment
helpers can inspect a retained stage, but they must continue to validate its
source, world and runner bindings. These bindings include absolute source,
world and runner paths. To re-audit a downloaded artifact, recreate its recorded
paths with the retained checkout and runner. Editing a report to substitute a
different path invalidates that evidence. The physical ZIP's paths are relative
to the recorded checkout's `build/` directory; extract it there. Artifact ZIPs
do not preserve executable permissions, so use `chmod +x` on the downloaded
`runtime-target/release/shama-sparse-world-check-terrain` binary before running
it. Verify its retained SHA-256 first.

Whole-machine acceptance still separately requires all production partitions,
their interconnections and timing, a workable loaded/ticking setup, readable
client output, boot, every promised app and persistent lifecycle behavior.
