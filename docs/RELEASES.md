# Release downloads and their gates

The `Release downloads` workflow automatically prepares the world installer,
Litematica schematic, ready-made superflat save, source/tools, short `README.txt`,
checksums and validation downloads when a GitHub release is published. It can
also be rerun from Actions for an existing release tag. Existing assets are
preserved; a rerun skips an identical file and refuses to replace a differing one.

**Complete-world publication is currently closed.** The current global router
fails its mapped-resource preflight. Complete-world timing, loading, usable
screen/console operation and full OS execution have not been accepted. There is
no complete-world acceptance validator in this repository yet. The release code
cannot turn a fixture, a green synthesis job, or a caller's JSON `passed` label
into a verified playable computer.

## What happens automatically

1. Check out the release's exact tag commit and find its successful repository
   CI run. All four OS/Python jobs, twelve physical partitions, behavioral and
   actual-redstone tests, world backend, RTL and full synthesis must be green.
2. Download **all** original CI artifacts, retaining their names. Expired or
   missing required artifacts block release instead of silently disappearing.
3. If a successful same-repository, same-commit run provides
   `shamaos-full-world-candidate-<40-character-commit>`, download it. Pull-request
   artifacts and artifacts from another source commit are rejected. Otherwise
   run `tools/build_release_candidate.py`, which invokes the actual full-capacity
   generator, including storage and mapped-logic resource preflight.
4. Independently read the saved Anvil chunks, validate capacities and above-grass
   placement, verify the source/world/evidence hashes, and run the supported
   complete-world acceptance validator. **No such validator is registered yet,
   so this gate blocks every complete-world release today.** There is no CLI,
   environment or configuration override. `release_acceptance.py` requires a
   repository implementation, not a runner name supplied by an artifact.
5. Once those real gates are implemented and pass, export the actual accepted
   saved blocks to a multi-region Litematica v6 schematic. Reopen its compressed
   NBT and decode every packed palette entry. Create and read-check ZIP downloads,
   generate the guide with the exact tested settings, and publish the complete
   set only after packaging succeeds.

This workflow consumes an independently accepted candidate when available; it
does not yet supply the missing whole-computer runtime harness. The fallback
build currently stops at the concrete resource or runtime gate and uploads its
diagnostics. It never falls back to `fixture-world`.

## Downloads

| Download | Contents |
|---|---|
| `ShamaOS-world.zip` | Java 1.20.4 grass superflat world with the accepted complete machine |
| `ShamaOS.litematic` | Exact owned machine states; tightly bounded chunk-section regions avoid expanding vast empty gaps |
| `ShamaOS-installer.zip` | Folder picker, Windows/Mac/Linux launch scripts, copy installer and download metadata |
| `ShamaOS-source-and-tools.zip` | Exact tracked source revision, firmware, RTL, scripts, examples and docs |
| `ShamaOS-validation.zip` | Complete-world acceptance evidence, generation manifest, exact CI metadata and schematic readback |
| Original `full-soc-*` and `shamaos-tested-fixture-*` ZIPs | Every original CI artifact, with fixture worlds clearly identified as tests |
| `README.txt` | Short nontechnical setup/use/performance guide plus the release's measured settings |
| `release-manifest.json`, `SHA256SUMS.txt` | File checksums, capacity/version/source metadata and tested runtime setup |

GitHub limits each release asset to less than 2 GiB. Large ZIPs or schematics are
split into 1 GiB `.001`, `.002`, ... downloads, each independently hashed, with a
hash for the joined file. The installer joins world parts automatically. The
whole machine is retained; splitting downloads does not reduce capacity.
Litematica's metadata uses signed 32-bit counts; a world exceeding its count
limit blocks that export explicitly instead of writing overflowed metadata.

## Existing-world installer

The installer asks for a closed Java 1.20.4 grass superflat save and creates a
new sibling save ending in ` - ShamaOS`. It holds a read-only lock on the original,
checks its file inventory, verifies a complete copy before adding anything, and
leaves the original unchanged. It picks chunk-aligned unused ground beyond all
existing overworld chunks, checks world-border and ground-height constraints,
and appends only absent chunks. It does not decode/rewrite existing chunk
payloads, player inventory files or unrelated dimensions. Installed block states,
comparator entities and scheduled block/fluid ticks are checked again after
horizontal translation. Template entity, POI, extra-dimension and world-data
sidecars, or unsupported structure/chunk tags, are rejected rather than silently
dropped; use the ready-made save for such a template. The
finished copy is renamed into place only after verification; interrupted or
failed copies are removed.

The current package layout requires the same grass height as the accepted
template. A taller superflat preset gets a clear error and the option to use the
ready-made save. The program does not push the machine into bedrock or past the
height limit. A future alternative layout must itself pass complete acceptance.

The accepted candidate must include tested translated loading/startup directions.
They may use `{x}`, `{y}`, `{z}`, `{offset_x}` and `{offset_z}` placeholders. The
new save's `START-HERE.txt` gives its actual console teleport and loading setup.
Moving block states alone does not establish that distant circuits stay active;
the separate relocation/loading gate is mandatory.

## Candidate evidence contract

A candidate directory contains `world/`, `generated-manifest.json`,
`acceptance.json` and `evidence/`. Its acceptance record uses
`format: shamaos-full-world-acceptance-v1`, `scope: complete-machine`, the clean
40-character `source_commit`, the installed `observed_capacity_bytes`, and
`world_inventory_sha256` calculated by `release_io.inventory_digest` over the
entire saved-world file inventory. Each gate references nonempty hashed evidence
under `evidence/`.

Required gates are literal physical capacity, global routing, timing, full-world
runtime, controls/screen, app lifecycle, persistence, both mining paths, capacity
faults, loaded chunks, and relocated installation. The record also includes the
usable `console` coordinates; runtime name/version, clock, loading setup,
simulation/render distances and setup steps; and `installer_setup_steps`.

Each gate needs a structured `shamaos-full-world-trace-v1` report bound to the
same source commit, world inventory, complete-machine scope and runner. These
fields are integrity checks only. A future registered validator must replay or
independently assess the actual trace observations for every required case;
merely adding a name or trusting `status: passed` is insufficient. No supported
validator exists at this checkpoint, and tests ensure even invented,
correctly-shaped pass reports cannot pass publication.

## Current tests

The release tests use deliberately small, explicitly named fixtures to check
schematic palette packing, comparator metadata, exact states, split/join and
archive readback, invalid paths/downloads, copied-world preservation, locked
saves, existing-chunk rejection, ground-height rejection and release-gate
failures. These are packaging/installer tests and do not claim a full-world boot.

Sources for the user guide and formats:

- [Mojang tick command documentation](https://www.minecraft.net/en-us/article/minecraft-java-edition-1-20-3)
- [MCHPRS commands and Redpiler](https://github.com/MCHPR/MCHPRS)
- [Distant Horizons versions and setup](https://modrinth.com/mod/distanthorizons)
- [Horizon runtime and mod compatibility](https://playhorizon.dev/docs/faq)
- [Litematica schematic reader/writer](https://github.com/maruohon/litematica/blob/pre-rewrite/fabric/1.20.x/src/main/java/fi/dy/masa/litematica/schematic/LitematicaSchematic.java)
- [Litematica metadata fields](https://github.com/maruohon/litematica/blob/pre-rewrite/fabric/1.20.x/src/main/java/fi/dy/masa/litematica/schematic/SchematicMetadata.java)
- [GitHub release asset size/count limits](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases)
