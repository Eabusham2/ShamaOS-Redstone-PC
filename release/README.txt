SHAMAOS - START HERE

Use Minecraft Java 1.20.4. This computer needs the tested settings listed at
the end of this file. Keep a copy of your save before trying other runtimes.

WHAT EACH DOWNLOAD IS
  ShamaOS-world.zip: a grass superflat save with the whole computer in place.
  ShamaOS-installer.zip: adds it to a NEW COPY of your own superflat save.
  ShamaOS.litematic: the computer for Litematica; use an empty area above grass.
  ShamaOS-source-and-tools.zip: source, assembly programs, documentation and tools.
  ShamaOS-validation.zip: the full-world test results and setup used for this build.
  full-soc-* / shamaos-tested-fixture-*.zip: the original CI evidence. Fixture
    worlds inside these are small tests, not the complete computer.
  release-manifest.json / SHA256SUMS.txt: version, setup and download checks.
  README.txt: this guide.

If a large download ends in .001, .002, etc., download ALL its parts into the
same folder. The installer joins and checks the world parts automatically.
For a split schematic or source archive, join the parts with 7-Zip first.

EASIEST SETUP: READY-MADE WORLD
1. Unzip ShamaOS-world.zip. Put the ShamaOS folder in your Minecraft saves folder.
2. Open it in Java 1.20.4 and follow THIS RELEASE'S TESTED SETTINGS below.
   For a split world, run the installer with --extract-world; it makes the same
   ShamaOS folder. On Windows: Start-Windows.cmd --extract-world

ADD IT TO YOUR OWN WORLD
1. Download the world, installer and release-manifest.json into one folder.
2. Unzip the installer. Install Python 3.11 or newer from python.org if needed.
3. Close Minecraft. Run Start-Windows.cmd. On Mac/Linux open Terminal in the
   installer folder and run: sh Start-Mac-Linux.command
4. Pick your Java 1.20.4 grass superflat save folder (the one with level.dat).
   It creates a new save ending in " - ShamaOS". Your original is unchanged.
5. Open the NEW save. Read its START-HERE.txt for the console location and setup.
   The installer leaves grass/bedrock intact and avoids all existing chunks.
   A different ground height may not fit; use the ready-made save in that case.

USING THE COMPUTER
At the console, use Power, the on-screen menus and the physical keyboard.
Controller arrows move selection; A/Enter confirms, B/Escape cancels. Home,
Editor and Files are shortcuts. In Editor, type text, save it, then assemble
and run assembly programs. Files opens saved documents. Let saves finish before
closing Minecraft. At 95% flash use, writes stop and a warning appears: use
Files to delete unwanted files. If saving fails, Keep App preserves your work;
choose Discard and Leave only when you want to leave without that save.

SPEED AND VIEW DISTANCE
Use the tested simulation and render distances below. Simulation distance keeps
nearby circuits active; render distance controls what you see. Distant views
alone do not keep the whole computer running. Follow the release's loading steps.

More tick speed is recommended. With commands enabled, try /tick rate 40,
then /tick query. Increase gradually if your computer keeps up. /tick rate 20
returns to normal. Changing randomTickSpeed will not speed up this redstone.

MCHPRS is a separate fast redstone server, not a client mod. Follow its setup
guide at github.com/MCHPR/MCHPRS. On a tested import, /rtps 100 increases its
speed, /rtps 10 restores normal, and /rp c starts Redpiler. Use only the exact
runtime/import/settings certified below; a Litematica file is not automatically
an MCHPRS-compatible import. Avoid experimental optimization flags.

Distant Horizons helps you SEE farther. Install its Java 1.20.4 build and matching
Fabric/Forge loader from modrinth.com/mod/distanthorizons, then adjust its distance
in Video Settings. It does not speed the logic or keep distant circuits active.

Horizon Launcher is an experimental alternative: playhorizon.dev/docs/faq.
It targets a newer Minecraft version and does not run ordinary Java mod jars.
Only try a copied world; it is supported for this release only if listed below.
More help: docs/RUNNING_AND_ACCELERATION.md in the source download.
