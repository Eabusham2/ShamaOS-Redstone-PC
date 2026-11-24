# Physical reset-receipt review

This retained experiment exercises a 19-cell reset-receipt bridge. It is a
component candidate, not acceptance of the joined counter reset path or the
complete computer. Its four external inputs are raw clock, local reset,
returned valid and returned ticket. The counter's actual return routes are
outside this world.

The independent reviewer reconstructs every input from the scenario and grades
all 70 relative raw-clock phases directly from the actual native per-tick
outputs. Each request changes its physical ticket once, retains reset during a
stale response, and releases only after a matching response. All 4,022 observed
register edges capture D at the template's seven-tick Q delay. Minimum setup
and hold are 21/19 ticks for issuance, 3,312/48 for the ticket, and 59/11 for
release, against the required 8/2.

The preceding 18-cell world is preserved as a negative. Its final transaction
values appear correct, but nine physical register apertures fail. At two of
those events, Q also differs from the D value at the nominal clock edge. The
review must reproduce those failures rather than accepting final values alone.

The digest-bound archive retains both saved Anvil worlds, decoded streams,
mapped circuits, construction sources, scenarios and complete raw traces.
No Python callback supplies a returned result during native execution: the
four input pins are the only driven coordinates. Returned valid/ticket are
external stimuli in this component experiment.

```sh
python experiments/raw_counter_timing/reset_receipt/review.py
```

To repeat both native runs with the exact pinned terrain engine:

```sh
python experiments/raw_counter_timing/reset_receipt/review.py \
  --runner "$SHAMA_MCHPRS_TERRAIN_RUNNER" --output reset-receipt-review.json
```

The connected return, rapidly renewed resets, STEP request ownership and full
machine behavior require their own evidence. This study does not enable a
release validator or replace production hardware.
