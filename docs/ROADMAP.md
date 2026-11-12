# Remaining release work

The detailed requirements are [REQUIREMENTS.md](REQUIREMENTS.md). Features
promised during planning are required before a complete release, including full
source editing, software/native SHA comparison, dirty display updates, persistent
settings, clean lifecycle and diagnostics. They are not deferred as optional
post-release enhancements.

Current priorities are:

1. Preserve the behavioral CPU/GPU/OS/filesystem/assembler regressions and finish
   integrated first-boot/power-cycle/error cases against exact shipped firmware.
2. Complete physical cell and connected-route validation, including dense
   sequential nets, endpoint escape, fanout and clock timing.
3. Validate compact stacked storage and shared local backbones, retaining every
   advertised byte; reduce chunk/generation overhead with measured emission work.
4. Run all full-size synthesis partitions and reconnection for the exact candidate.
5. Generate and verify the complete world with a practical loaded/ticking-chunk
   configuration; run the full in-world acceptance sequence.
6. Publish the world, source revision, checksums, exact CI/runtime reports and
   acceleration/loading instructions together.

The current compact cell is an implemented density improvement, not the end of
layout optimization. Capacities/resolution are unchanged. See
[IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md) for the distinction between
completed behavioral work, local physical fixtures and open full-world gates.
