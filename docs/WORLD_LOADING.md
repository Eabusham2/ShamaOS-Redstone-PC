# Exact saved-world chunk loading

The generator now derives a loading plan from the complete saved world's
construction ledger and its Anvil location tables. The two inventories must
agree. It partitions the actual occupied set into disjoint rectangles of at
most 256 chunks, independently reconstructs the set, and rejects omissions,
overlap, invalid coordinates or extra gap chunks.

The Java 1.20.4 limit applies to each `forceload` command rectangle. It is not
a 256-entry limit on the dimension's total forced set. The pinned official
bytecode review is retained in
[the loading audit](../experiments/world_loading_audit/README.md).
The data pack uses format 26, matching Mojang's
[1.20.3 technical changes](https://www.minecraft.net/en-us/article/minecraft-java-edition-1-20-3)
and the subsequent 1.20.4 patch.

## Generated files and commands

After a complete generation passes independent block readback, the world gets:

- `shamaos/loading-plan.json`: exact rectangle inventory, coordinate digest,
  command count and the load/stop/unload commands.
- `shamaos/LOADING.txt`: instructions for that particular saved world.
- A dedicated data pack whose namespace contains the chunk-inventory digest.

For an already generated native world, run:

```sh
python -m shamaos.cli world-loading --world build/bounded-world
```

This command first repeats the independent block readback. It then writes the
plan and data pack without running Minecraft or changing any circuit block.
An unchanged bundle can be regenerated; a conflicting existing pack is not
overwritten.

Close the saved world before generating the files: the saved forced-ticket
set is read under its session lock. Then open the world and run the exact
`/function ...:load` command in `LOADING.txt`.
The pack has no automatic load or tick tags. Each batch contains at
most 128 `forceload` commands, and the next batch is scheduled one game tick
later. The stop function cancels the scheduled batches of both operations;
it leaves already installed tickets in place. The unload function removes
only the plan's machine chunks that were not already forced when the plan was
created. The original forced set remains intact. Regeneration retains this
initial baseline. Java has no per-pack ownership of forced tickets, so do not
use unload after independently forcing overlapping chunks for another purpose.
Starting either load or unload first cancels
the other operation's queued work.

These functions only request chunk tickets. They never execute the CPU,
assembler, OS, filesystem, GPU or mining work.

## Installation into a different save

The installer continues to copy and verify the original save before modifying
the copy. Once all translated machine chunks pass readback, it generates a new
plan from the template ledger plus the actual chunk offset. The generated
functions contain the new coordinates; they do not reuse the template's old
commands or include unrelated chunks from the user's save.

The new plan and instructions live in `shamaos-install/`. `START-HERE.txt`
includes the installation's actual load command before its other setup steps.
Existing source-world files and its previously saved chunks remain preserved.

## Verification and remaining runtime work

The focused regression checks rectangular and sparse sets, negative and distant
coordinates, holes, randomized input order, per-command limits, exact command
decoding, scheduled cancellation, changed-pack preservation, saved inventory
omissions, installation offsets, pre-existing forced tickets, symbolic-link
outputs, conflicting user notes, and interrupted pack construction. The loading,
packaging, world-generation and release-backend cohort is run with these cases.

The report explicitly retains `actual_server_loading_verified: false` and
`complete_machine_runtime_verified: false`. Exact requested coverage does not
measure the server's active chunks, neighboring ticket dependencies, RAM,
throughput, persistence after a server restart or client rendering. Those
remain actual server/client acceptance checks, separate from generation and
the native redstone test harness.
