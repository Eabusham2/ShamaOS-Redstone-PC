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
runtime before treating it as a tested target. ShamaOS currently checks physical
circuit fixtures against a pinned MCHPRS engine; this does not establish that
the complete generated computer imports, stays active and boots on an MCHPRS
server. Keep Redpiler options and the tested engine revision in the run report.

The pinned stock server uses separate **512×512-block plots**. A plot reads
outside its own boundary as air, and its normal update loop stops ticking when
no player is inside it; `always_running` prevents unloading but does not change
that tick condition. Those limits must be resolved for this machine before
stock MCHPRS becomes a complete-world recommendation. The experimental
[native-world runtime](NATIVE_RUNTIME.md) uses one connected, all-loaded world
and has saved-fixture evidence, including actual terrain and console actions.
Whole-machine boot remains an acceptance gate.

## Viewing the build: Distant Horizons

[Distant Horizons](https://modrinth.com/mod/distanthorizons) is recommended as an
optional way to see the overall build from farther away. It renders distant
geometry with reduced detail, including player-created structures. Use a
version matching Minecraft Java 1.20.4 and its supported mod loader.

Treat distant views as an overview: they do not verify live redstone simulation,
chunk loading, or pixel-accurate updates on the OS lamp screen. The operating
console and screen still need their own usable, tested viewing arrangement.

## Experimental runtime: Horizon Launcher

[Horizon](https://github.com/horizon-rust/horizon), installed through Horizon
Launcher, is another performance candidate to evaluate. Its current project
targets Minecraft Java 26.3 behavior in a Rust runtime. ShamaOS's generated world
targets Java 1.20.4, so Horizon needs its own world-import, redstone-timing and
full-OS acceptance run before it becomes a supported recommendation.

The [Horizon FAQ](https://playhorizon.dev/docs/faq) explains that ordinary
Fabric, Forge and NeoForge Java mods are incompatible with this runtime. Do not
assume a Java mod configuration, including Distant Horizons, transfers to it.

## Loading and timing

Acceleration does not load unloaded circuitry. All required parts of the machine
must remain loaded and ticking, and all long routes must retain sufficient
settling time. The generated release's manifest and validation report must state
its tested runtime, loading arrangement, clock settings and achieved rate.

The [pinned loading audit](../experiments/world_loading_audit/README.md) checks
the current connected-cache geometry against official Java 1.20.4 artifacts
and the pinned MCHPRS sources. Vanilla's 256-chunk `/forceload` restriction
applies to each command rectangle; successive commands can request the complete
occupied set. The actual resident chunk count, memory use and achieved tick rate
still need measurement on the chosen server. Stock MCHPRS's plot boundaries and
idle-plot ticking require implementation changes for this geometry.

The audit also identifies a current viewing limitation: from the existing
viewing lip, the whole lamp face needs a tracking radius of 39, while the stock
Java 1.20.4 server caps view distance at 32. A closer viewing position is under
review. Raising a configuration value to 40 alone does not bypass that cap, and
the complete live screen has not yet been observed in a client.

## Verification before calling a world ready

Boot ShamaOS, enter and save text, assemble and run a program, switch/exit apps,
restart and reopen the saved files, and test both miner execution modes. Verify
hashes and memory/storage counters against the release's recorded test cases.

Sources:

- [Mojang Java 1.20.3 release notes: tick command](https://feedback.minecraft.net/hc/en-us/articles/21968446892173-Minecraft-Java-Edition-1-20-3)
- [MCHPRS README and command documentation](https://github.com/MCHPR/MCHPRS)
- [MCHPRS Redpiler documentation](https://github.com/MCHPR/MCHPRS/blob/master/docs/Redpiler.md)
- [Distant Horizons project and compatibility list](https://modrinth.com/mod/distanthorizons)
- [Horizon source and supported game version](https://github.com/horizon-rust/horizon)
- [Horizon compatibility FAQ](https://playhorizon.dev/docs/faq)
