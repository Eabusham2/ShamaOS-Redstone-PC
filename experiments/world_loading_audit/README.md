# Loading the connected cache in an actual server

**The all-loaded native runner is a circuit test harness, not a playable
Minecraft server.** Vanilla Java 1.20.4 has a supported mechanism to request
enough active chunks: repeated `/forceload` commands. We have not measured
whether a practical vanilla host can run this fixture at an acceptable rate.
The pinned stock MCHPRS server cannot run the connected fixture through settings
alone: its isolated plot world and player-dependent ticking must first change.

This audit is static and read-only. It does not start a server, accept an EULA,
change the generated world, or certify a client. The accompanying
[calculation record](evidence/connected-cache-loading.json) binds the geometry,
source files and official Java artifacts. The calculation script is
[calculate_requirements.py](calculate_requirements.py).

## Exact fixture and scope

The connected fixture contains the complete 1,144-cell cache controller, its
real exterior wires, two physical 4 KiB banks and all 78 controller/bank routes.
Its two independent literal unions agree on these measurements:

| Measurement | Result |
|---|---:|
| Unique circuit blocks | 37,102,012 |
| Occupied X/Z chunks | 192,026 |
| Occupied region files | 283 |
| Inclusive block bounds | `(0,-60,-4744)` through `(9456,318,7456)` |
| Inclusive block extent | 9,457 × 379 × 12,201 |
| Enclosing chunk rectangle, including gaps | 592 × 764 = 452,288 |
| Section records at 24 sections per occupied chunk | 4,608,624 |
| Anvil file allocation lower bound | 788,856,832 bytes = 752.3125 MiB |

The last number includes one 4 KiB sector per occupied chunk and two header
sectors per region. It is a file-format lower bound, not the finished world size
or a RAM estimate. The complete computer has additional unplaced/unconnected
logic; these figures must not be presented as its final footprint.

The connected native writer was still running when this audit was recorded.
Its save/readback, clock and transaction results belong to the separately bound
runtime reports. This audit does not promote those pending stages to passed.

## Vanilla Java 1.20.4: supported loading, unmeasured performance

The official `ForceLoadCommand.changeForceLoad` checks the area of **one
inclusive command rectangle** against 256. It then visits each requested chunk
and calls `ServerLevel.setChunkForced`. The latter inserts/removes a coordinate
in the dimension's saved forced-chunk set and updates its ticket. Neither method
limits that set to 256 total entries. Therefore **256 is not a global forced
chunk limit** in this version. A succession of legal commands is a supported
way to request a much larger forced set. This is established from the official
1.20.4 server bytecode and Mojang's matching method names, not a third-party
summary. Artifact identities and inspected members are in
[official-java-1.20.4-source-review.json](evidence/official-java-1.20.4-source-review.json).

Forced tickets enter both ordinary and ticking trackers at entity-ticking level
31; block ticking is allowed through level 32. Requesting every occupied circuit
chunk therefore addresses the player-distance loading problem. The ticket
system also loads surrounding dependencies, so 192,026 is not an upper bound on
the actual server's resident chunks. The exact runtime inventory must be measured.

For scale, 751 commands is only an arithmetic lower bound at 256 occupied chunks
per command. Sparse geometry generally needs more rectangles. Covering the
entire enclosing rectangle with 16×16-chunk batches would require 1,776 commands
and request all 452,288 chunks, including the gaps. That is not our recommended
loading plan. A future installer should derive bounded rectangles or single
chunk requests from the exact occupied-chunk inventory, verify every requested
coordinate, and retain the installed set for restart checks.

Raising `simulation-distance` to hundreds is not an alternative. The official
player ticket calculation is `max(0, 31 - simulationDistance)`; it saturates
instead of creating a radius large enough to cover this fixture. Even the best
integer chunk center needs a Chebyshev radius of at least 382 to include its
extreme occupied chunks. View distance, simulation distance and forced tickets
are separate controls.

**Actionable vanilla path:** finish the native save and runtime gates; create an
exact occupied-chunk loading manifest; import the unchanged world into Java
1.20.4 on a measured host; install its forced tickets in bounded batches; verify
loaded/ticking status, remote signal propagation and ticket persistence through
restart. Record JVM heap, peak RSS, GC pauses, tick cost, startup time and disk
usage. No measured vanilla RAM requirement or achieved rate exists yet, so this
audit does not invent a hardware recommendation. The roughly 8 GiB development
machine and the native runner's 3 GiB cap are not evidence of vanilla capacity.

The `/tick` command changes the target rate or performs a bounded sprint; it
does not load circuitry or guarantee that the host achieves its target. The
official [1.20.3 release notes](https://www.minecraft.net/en-us/article/minecraft-java-edition-1-20-3)
describe that command, which is present in the audited 1.20.4 artifact. A
16,384-redstone-tick half-period, if eventually accepted for the connected cache,
would mean 65,536 game ticks per full clock cycle: about 54.6 minutes at the
normal 20 game ticks/s. Native execution speed and a requested `/tick rate` are
not measurements of actual server throughput.

## Stock MCHPRS: concrete code and resource blockers

All statements here are pinned to upstream commit
`f9e10d2572116bf5dcb9a36a305f28e40641a61a`. Its primary sources are
[plot world and loop](https://github.com/MCHPR/MCHPRS/blob/f9e10d2572116bf5dcb9a36a305f28e40641a61a/crates/core/src/plot/mod.rs),
[plot preparation](https://github.com/MCHPR/MCHPRS/blob/f9e10d2572116bf5dcb9a36a305f28e40641a61a/crates/core/src/plot/data.rs),
[configuration](https://github.com/MCHPR/MCHPRS/blob/f9e10d2572116bf5dcb9a36a305f28e40641a61a/crates/core/src/config.rs),
[chunk storage](https://github.com/MCHPR/MCHPRS/blob/f9e10d2572116bf5dcb9a36a305f28e40641a61a/crates/world/src/storage.rs),
and [plot save format](https://github.com/MCHPR/MCHPRS/blob/f9e10d2572116bf5dcb9a36a305f28e40641a61a/crates/save_data/src/plot_data.rs).

| Source behavior | Consequence for this geometry |
|---|---|
| `PLOT_SCALE=5`; 32×32 chunks, 512×512 blocks per plot | The 9,457×12,201 fixture crosses many independent worlds. |
| `PlotWorld` reads outside its plot as air and rejects outside writes | Adjacent plot threads do not form a connected electrical world. |
| The ordinary update loop ticks only while `players` is nonempty | `always_running` prevents unloading but does not make an empty plot tick. |
| `view_distance` affects client chunk delivery | Changing it does not enlarge or connect simulation plots. |
| Plot storage is an eager `Vec<Chunk>` and 24 eager sections per chunk | Enlarging a plot allocates its empty spaces too. |
| The loader reads the custom `PlotData` format | An Anvil world is not directly the stock server's plot save. |

A source-resized single plot is a possible development approach, not a tested
configuration. The smallest power-of-two square fitting the translated fixture
is 1,024×1,024 chunks (16,384×16,384 blocks), requiring `PLOT_SCALE=10`. The
fixture must also be rebased into that one plot: its current negative Z range
crosses a plot boundary. Native Y must be translated consistently by +64 into
the engine's 0…383 range. All save, index, networking and compiler assumptions
must then be checked for the new size; merely changing the constant is not an
accepted server port.

The current representation allocates a minimum 4-bit index for each of 4,096
positions per section: 2,048 bytes, even for uniform air. At 24 sections this is
48 KiB per chunk. Arrays for only the 192,026 occupied chunks would consume
**8.7903 GiB**; arrays for the required square plot consume **48 GiB** before
palettes, structs, block entities, changed-block buffers, queues, network state,
compiler state or transient copies. This is a representation-specific lower
bound, not a sufficient host specification and not a lower bound for vanilla
or our lazy runner.

The other concrete development path is to connect a sparse global world backend
to MCHPRS's actual server: Anvil import, globally consistent cross-chunk access
and tick ordering, an explicit player-independent active-world policy, client
chunk/section updates, and save/reopen handling. Our native runner supplies
tested pieces of sparse storage and engine execution; it has no player session,
server command loop or live client network. Those pieces still have to be
integrated and tested. Redpiler compilation would need its own complete-world
equivalence and persistence gates. Acceleration does not repair plot boundaries.

## Separate client blocker in the current screen arrangement

The default viewing lip places the eye at `(967.5,-53.38,64.5)`, in chunk
`(60,4)`. All 57,600 lamps occupy 61 chunks, from `(30,32)` through `(90,32)`.
Applying the **official Java 1.20.4 rounded chunk-tracking predicate**, rather
than a square-distance estimate, gives:

| Radius | Lamps in tracking region | Lamps in inner view region |
|---:|---:|---:|
| 32 | 37,440 | 35,640 |
| 38 | 54,720 | 52,920 |
| 39 | 57,600 | 56,520 |
| 40 | 57,600 | 57,600 |

The official server clamps its view distance to 2…32. The normal client option
has a maximum of 32 on its larger-memory 64-bit path, or 16 otherwise. Writing
`view-distance=40` alone does not change the server clamp. Thus the current
viewing position cannot receive the full face from the stock vanilla server,
even when every screen circuit chunk is forced and all geometric sightlines
are clear. This is a static delivery calculation, not an in-client screenshot.

The next isolated geometry task is to move the walkable viewing position closer
and/or re-center it while retaining all controls, pixel count, safe sightlines
and a usable FOV. An alternative is a separately verified server/client pair
whose actual chunk delivery and full-detail rendering exceed 32. Distant
geometry rendering alone does not establish live lamp updates.

## Evidence required before calling it playable

1. Complete the connected fixture's saved readback, actual exterior-clock gate,
   transactions, full storage assertions and quiescent save/reopen proof.
2. On the chosen real server, retain the exact version and world hashes, complete
   active-chunk inventory, loading mechanism, distant signal traces and restart
   results. A nearby player or unchanged native-runner result is insufficient.
3. In the actual client, observe every live screen region from the walkable
   operating view, including edge updates, control reach, FOV, text legibility
   and sustained frame/update rate. Save the client/server settings and capture.
4. Repeat the resource and loading inventory for the eventual complete computer.
   Then prove boot, all apps, in-world assembly, shutdown/reopen and resource
   faults from that same saved world. Cache-only evidence remains cache-only.

## Reproducing this static audit

Use the repository revision and file hashes in the JSON record. Obtain the four
official artifacts from the URLs stored there, plus the official
[version metadata](https://piston-meta.mojang.com/v1/packages/bd984a42a9b403c620eae6cd77a1095850ca4479/1.20.4.json).
The calculation validates their published SHA-1 and byte lengths. Official
binary artifacts and disassemblies stay in `build/`; they are not redistributed
with this report. Map method names using Mojang's matching mapping files and
inspect the listed classes with the JDK's `javap -p -c -constants`. The server
class JAR is the bundler's
`META-INF/versions/1.20.4/server-1.20.4.jar` entry.

```sh
python experiments/world_loading_audit/calculate_requirements.py \
  --connected-report /path/to/connected-build-report.json \
  --mchprs-source /path/to/pinned-mchprs-checkout \
  --official-artifacts build/loading-audit/official-java-1.20.4 \
  --output build/loading-audit/recalculated.json
```

No result from this command is a Minecraft launch or an end-to-end acceptance.
