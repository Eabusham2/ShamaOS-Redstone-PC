# Held completion source

`counter-review-c0e2939.patch.gz` preserves the reviewed shared-counter
development tree without applying it to production. Its receipt names the
exact base and resulting Git trees. An independent temporary Git index applied
the patch and reproduced the complete candidate tree exactly.

The candidate's standalone 64-bit physical counter, source arithmetic and
timestamp ABI checks passed, but joined electrical testing found remaining
faults in reset propagation and STEP request ownership. The complete input
path also remains under integration. These failures prevent promotion of the
candidate; the archive is for recovery and further work.

The three STEP negative recordings and the positive/negative reset-receipt
component study are retained separately in `experiments/step_control_timing`
and `experiments/raw_counter_timing/reset_receipt`. Root repeated all five
native cases and matched their complete observed traces. The reset study drives
returned valid/ticket at its boundary; it does not establish the connected
counter return path.

This checkpoint changes CI scheduling, documentation and retained engineering
evidence. It keeps the current production hardware, capacities, app contents
and closed full-world release gate unchanged.
