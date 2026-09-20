# Validation Plan

ShamaOS is too large for “looks right” validation.

## Test layers

### Unit

- instruction encode/decode.
- assembler parsing.
- pseudo-instruction expansion.
- SHA primitives.
- SHA-256 vectors.
- double-SHA-256 vectors.
- target comparison.
- framebuffer pixel packing.
- GPU geometry.
- flash filesystem operations.

### Reference integration

- CPU executes small programs.
- calls/returns.
- stack limits.
- load/store boundaries.
- syscalls.
- GPU command queue.
- app launch/exit memory accounting.
- filesystem save/open/rename/delete.
- miner start/stop/history.

### Physical-plan validation

- component boxes do not overlap.
- named ports match widths.
- bus routes preserve bit numbering.
- tile mapping covers every pixel exactly once.
- memory mapping covers intended ranges with no accidental aliases.
- block orientations are legal.
- SHA constants/round wiring generated from canonical tables.

### World validation

After writing:

- every touched region header parses.
- every chunk NBT parses.
- section palettes/indices are valid.
- block-state names are valid for target version.
- generated chunks can be read back.
- manifest block hash/count matches readback.
- `level.dat` remains readable.

## SHA known-answer tests

At minimum:

- SHA-256 of empty byte string.
- SHA-256 of `abc`.
- multi-block vector.
- double-SHA-256 of deterministic test input.
- Bitcoin-style 80-byte header test fixture.

Reference Python uses `hashlib` only as an oracle in tests. The redstone generator/runtime does not call Python for mining.

## Filesystem tests

- format/mount.
- create/write/read.
- append/overwrite/truncate.
- rename.
- delete.
- free-space recovery.
- duplicate names.
- full flash.
- max filename.
- metadata generation increments.
- corrupted metadata rejection/recovery behavior.

## OS tests

- boot image contains required apps.
- Desktop resource indicators equal allocator/filesystem counters.
- Editor New Text File.
- Editor New Program.
- save/rename/delete confirmation.
- File Explorer opens text.
- File Explorer Edit launches Editor and closes Explorer.
- universal Exit frees app allocation.
- universal Editor/Files close foreground app before switch.
- miner Start/Stop state machine.

## Acceptance gates

A generated world release is not called complete until:

1. unit/reference tests green.
2. generation plan validates.
3. world writes successfully.
4. written chunks read back.
5. smoke test in target Minecraft version succeeds.
6. representative hardware component behavior is verified in-game/accelerated simulator.
