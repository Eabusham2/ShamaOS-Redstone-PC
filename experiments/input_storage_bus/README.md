# Native 16×32 input storage bank

This experiment drives the production oscillator, all 16 clocked row selects, their physical comparators and the complete 512-bit passive bank. Bit 0 and bit 31 retain their clocked launch registers and exterior routes. For bits 1–30, the native test driver changes the conductive block beside each bank data terminal at a specified tick after the oscillator rising edge. That boundary leaves those 30 launch-register routes, input classification, CDC, and queue readout outside this measurement.

The original +33 RT run completed 91 epochs and observed 8,000 selected writes and 38,592 inhibited cell observations. All 6,144 directed physical assertions passed. The quiescent snapshot reopened with the expected 16 mixed 32-bit words. On 13 writes to row 13, column 30, the next physical D transition arrived only one tick after the local lock closed. A clean final word therefore coexisted with a failed hold requirement. The estimates derived from the local D and lock traces overlap at +34 through +49 RT for a common phase on the 30 interior terminals. These bounds require stable data one tick before the aperture opens and a next transition at least two ticks after it closes. The endpoint-only comparison already in `experiments/input_receiver_timing/evidence/receiver-comparison.json` measured a minimum hold of two ticks. Its four-row control counted 200 wrong stored samples after the write strobe was delayed by six ticks.

The recovery manifest records the original source and image digests, the incomplete +41 recording and its exact interruption point. Both recovered historical producers match SHA-256 values recorded before the original workspace disconnected. The original code and historical recovery manifest are preserved in `recovery/source-recovery-v1.tgz`.

## Fresh source-bound recordings

Both configurations use one geometry with 60,239 placed blocks, 528 comparators and 293 saved chunks. The complete region hashes to `298d3ef8e781d6271e773318c3ea3fa76d48f4ec5bd9fcaa19bc7286626942ed`, identical to the region from the original run. The native executable hashes to `c8032089622501cb57f9edbb0ea7e3be24e3a23fd0b2b80653b0ed4f07045610`.

At +41 RT, all 8,000 writes and all 38,592 inhibited observations matched expectations, including the exact count and timing of 2,515 rising and 2,249 falling storage transitions. Every monitored local lock opened for 35 ticks per selected write. The minimum observed D setup at closure was 44 ticks at row 0, bit 1. The minimum hold was two ticks at row 13, bit 31. The two registered endpoints retain their physically measured clock routes. The 30 independent interior stimuli share the same +41 tick reference for this run.

The fresh +33 replay again observed 13 one-tick hold violations, all at row 13, bit 30. It passed every directed and final storage assertion, including the reopened words. Its report hashes to `784040501192256a5a81032390cb3b9c78285e00a424cbaa9cd977e31d6ecfa2`, and its independent quiescent snapshot hashes to `6525ca5c3c01aad0e87f60f27c907691acca00c3899577b366aff148bb646175`.

The +41 quiescent native snapshot hashes to `d940a0f98bb488ad0ac85190ca77ded9604e311675fa768136c1c86d849211b5`. A second reconstruction reproduced the complete restored Anvil region byte-for-byte and all 16 mixed 32-bit words. The report hashes to `499a714527b522137d4669cfac73c2cfbc700901b61c53eac8c77a9330fca32c`.

## Capture and replay

Run in a Linux source checkout with the pinned terrain-enabled MCHPRS executable and the project's Python dependencies installed. The recorder validates the executable digest before it creates an image. It uses inotify and file identity checks during recording. The commands below show the +41 profile in independently recoverable slices.

```sh
python experiments/input_storage_bus/capture.py /tmp/storage-41 --runner "$SHAMA_MCHPRS_TERRAIN_RUNNER" --phase 41 --through-column 7
python experiments/input_storage_bus/capture.py /tmp/storage-41 --runner "$SHAMA_MCHPRS_TERRAIN_RUNNER" --phase 41 --through-column 15 --resume
python experiments/input_storage_bus/capture.py /tmp/storage-41 --runner "$SHAMA_MCHPRS_TERRAIN_RUNNER" --phase 41 --through-column 23 --resume
python experiments/input_storage_bus/capture.py /tmp/storage-41 --runner "$SHAMA_MCHPRS_TERRAIN_RUNNER" --phase 41 --resume
```

Use a separate output directory and `--phase 33` to record the negative profile. Exit code 1 with a complete report is the expected +33 timing verdict. The recorder writes raw physical traces, each submitted scenario, source digests, original and restored Anvil files, the quiescent native snapshot and decoded words. Every restart regenerates exact probes and stimuli, checks the original image and source identity, and revalidates all completed native results. An interrupted shard can be repeated in the same directory. The selected two-column trace boundaries limit the runner's serialization memory. The final snapshot uses a separate native invocation with trace capture disabled.

```sh
python experiments/input_storage_bus/pack.py experiments/input_storage_bus/evidence.tar.xz /tmp/storage-33 /tmp/storage-41
python experiments/input_storage_bus/replay.py experiments/input_storage_bus/evidence.tar.xz
python -m pytest -q experiments/input_storage_bus/test_capture.py experiments/input_storage_bus/test_full_width.py
```

The tar.xz archive stores identical contents once under their SHA-256 digest. Its manifest maps each original filename to the corresponding bytes. Both original Anvil regions and the complete regenerated snapshots are included.

The replay reconstructs physical pins and generated stimuli, recalculates D, lock and Q behavior from native recordings and reads all 512 saved repeaters from the restored Anvil region. Its directed patterns cover all four binary combinations for each of the 496 column pairs in every row. A trace-mutation test adds a one-tick Q pulse to an inhibited row to check sensitivity to a fault that disappears by the end of the epoch.
