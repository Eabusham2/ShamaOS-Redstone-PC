# Running ShamaOS and increasing simulation speed

**An increased game tick rate or a compatible redstone acceleration mod/server is
recommended.** A large physical redstone computer can take a long time at normal
Minecraft speed. Acceleration changes elapsed real time; the computer must still
perform its calculations through the generated redstone.

## Minecraft Java 1.20.4

Java 1.20.4 includes the `/tick` command introduced in 1.20.3. With commands/admin
permission enabled, start conservatively and inspect the actual achieved rate:

```mcfunction
/tick query
/tick rate 40
```

If the host keeps up, increase gradually, for example `/tick rate 100`. Restore
normal speed with `/tick rate 20`. `/tick sprint 200t` runs a bounded burst as fast
as the host can simulate it. For debugging, use `/tick freeze`, `/tick step 1t`
and `/tick unfreeze`.

Use game tick rate for this purpose. `randomTickSpeed` controls random block
updates and does not accelerate scheduled repeater/redstone logic.

## Acceleration mods and MCHPRS

A compatible mod that changes the simulation tick rate can provide the same kind
of acceleration. [MCHPRS](https://github.com/MCHPR/MCHPRS) is a specialized
redstone server with a redstone compiler, rather than a normal client mod. Its
`/rtps` and Redpiler commands are described in its own README. Use a supported
version/import path and validate the machine's block and timing behavior on that
runtime before treating it as a tested target.

Acceleration does not load unloaded circuitry. All required parts of the machine
must remain loaded and ticking, and all long routes must retain sufficient
settling time. The generated release's manifest and validation report must state
its tested runtime, loading arrangement, clock settings and achieved rate.

## Verification before calling a world ready

Boot ShamaOS, enter and save text, assemble and run a program, switch/exit apps,
restart and reopen the saved files, and test both miner execution modes. Verify
hashes and memory/storage counters against the release's recorded test cases.

Sources:

- [Mojang Java 1.20.3 release notes: tick command](https://feedback.minecraft.net/hc/en-us/articles/21968446892173-Minecraft-Java-Edition-1-20-3)
- [MCHPRS README and command documentation](https://github.com/MCHPR/MCHPRS)
