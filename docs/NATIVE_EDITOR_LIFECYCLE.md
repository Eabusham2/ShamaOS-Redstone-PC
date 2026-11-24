# Native Editor lifecycle

Run this exercise against a closed, fully generated production candidate from the exact clean checkout that built it. It uses actual keyboard and controller blocks, monitor lamps, architectural debug signals and storage repeaters. The complete-machine run is pending. The retained native evidence covers a bounded lever fixture and rejection of an undersized candidate.

The first session boots to Desktop, creates `X.TXT`, types `ABC`, and returns Home to save the dirty document. It then creates `Z.ASM`, types a program, assembles and runs it, observes a halted CPU with register 1 equal to 42, and returns Home. On shutdown it samples the physical acknowledgment lamp, waits for the clock to stop, and requires a zero-pending-tick snapshot. It restores that snapshot to a distinct Anvil world, reads the three files from all 200 KiB of physical flash, and checks that all 4 KiB of cache were erased.

A fresh engine process boots the restored world. Physical Files, X and Left inputs must reopen `X.TXT` in Editor. Register and RAM probes check the file type, editing mode, length and `ABC` bytes. Home, shutdown, a second restore and another flash readback finish the exercise.

## Run

The candidate directory must contain `world/` and `generated-manifest.json`. Use Linux or macOS for atomic snapshot publication. The output must be a new directory outside the candidate. Allow space for recomputed placement unions, two full native import streams, snapshots and restored saves. The runner checks every regenerated placement, the exact count of distinct coordinates, native terrain and initial comparator levels before exercising the console.

Supply measured phase budgets in redstone ticks and a wall-time limit per native session. Both key and action budgets must exceed the console's 119-tick stretcher. The acknowledgment budget is the sample offset after POWER goes off and must lie strictly inside the total shutdown budget. The full world's routing and execution budgets have yet to be measured.

```sh
python tools/run_native_editor_lifecycle.py "$CANDIDATE" "$FRESH_OUTPUT" \
  --config configs/default.toml --runner "$SHAMA_MCHPRS_TERRAIN_RUNNER" \
  --timeout-seconds "$SESSION_SECONDS" \
  --initial-ticks "$INITIAL_RT" --boot-ticks "$BOOT_RT" \
  --editor-ticks "$EDITOR_RT" --home-ticks "$HOME_RT" \
  --key-ticks "$KEY_RT" --action-ticks "$ACTION_RT" --run-ticks "$RUN_RT" \
  --shutdown-ticks "$SHUTDOWN_RT" --shutdown-ack-ticks "$ACK_RT"
```

The binary option accepts SHA-256 `c8032089622501cb57f9edbb0ea7e3be24e3a23fd0b2b80653b0ed4f07045610`. To compile for a different host, replace that option with `--engine-source "$MCHPRS_CHECKOUT"`. It requires Cargo, cached dependencies and the clean pinned engine at `f9e10d2572116bf5dcb9a36a305f28e40641a61a`. The optional repeatable `--require-flow` accepts the flow names in `--help`, rejects unknown requirements and always executes the whole Editor exercise.

Each phase retains the import, physical scenario, native report, snapshot, reconstructed save and flash readback. The final `editor-lifecycle.json` binds these measurements to the source, config, manifest and original world inventory. A failed execution retains available artifacts and diagnostics. Release acceptance, global clock timing, client simulation distance, mining, capacity-fault recovery and relocated installation require separate evidence.

## Reproduce the bounded checks

At source commit `e4a13690ea6887895b578651a93e9a3a1f30c224`, one native process switched a saved lever on and a fresh process reopened the saved on-state and switched it off. Each process ran 40 ticks, made two physical assertions, and produced a quiescent one-chunk snapshot. The same test rejected a wrong initial lever state and a genuine snapshot substituted from the other process. Separately, the public CLI rejected a one-block Anvil world carrying full capacity and stage labels plus a real passing readback. The topology check stopped before synthesis. The evidence archive includes the original inputs and outputs, both saved-state transitions, the rejection, and per-file SHA-256 bindings. [Bounded fixture evidence](evidence/native-editor-lifecycle-fixtures.zip)

```sh
python -m pytest -q tests/test_native_editor_lifecycle.py
```

Set `SHAMA_MCHPRS_TERRAIN_RUNNER` to execute the native cases. Tests also cover byte-identical edit-and-restore races, stale provenance, incorrect file contents and missing physical topology. [Lifecycle tests](../tests/test_native_editor_lifecycle.py)
