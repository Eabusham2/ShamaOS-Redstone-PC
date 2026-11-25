"""Exact chunk-loading commands for saved and relocated Java 1.20.4 worlds.

The commands only install/remove chunk tickets. All computation stays in the
physical circuits. An exact command cover is not evidence that a server has
loaded the chunks, can tick them fast enough, or can display the whole screen.
"""
from __future__ import annotations

from bisect import bisect_right
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile

import nbtlib

from .model import Vec3
from .worldio import WorldWriteError, _Region, validate_position


COMMAND_CHUNK_LIMIT = 256
COMMANDS_PER_BATCH = 128


def _checked_chunks(chunks) -> set[tuple[int, int]]:
    result = set()
    for coordinate in chunks:
        if (not isinstance(coordinate, (tuple, list)) or len(coordinate) != 2
                or any(type(value) is not int for value in coordinate)):
            raise WorldWriteError("Chunk coordinates must be pairs of integers.")
        x, z = coordinate
        for corner in (Vec3(x * 16, 0, z * 16), Vec3(x * 16 + 15, 0, z * 16 + 15)):
            validate_position(corner)
        if (x, z) in result:
            raise WorldWriteError("The loading inventory repeats a chunk.")
        result.add((x, z))
    if not result:
        raise WorldWriteError("The loading inventory has no chunks.")
    return result


def saved_world_chunks(world: Path) -> set[tuple[int, int]]:
    """Match the construction ledger against every saved Anvil location table.

This inventory check is deliberately separate from expensive block readback;
the generator/installer performs that before producing its loading bundle.
"""
    world = Path(world)
    ledger = world / "shamaos" / "expected.sqlite3"
    if not ledger.is_file():
        raise WorldWriteError("The world has no generated chunk ledger.")
    with closing(sqlite3.connect(f"{ledger.resolve().as_uri()}?mode=ro", uri=True)) as db:
        expected = _checked_chunks(db.execute("SELECT x,z FROM chunks ORDER BY z,x"))
    saved = set()
    for path in sorted((world / "region").glob("*.mca")):
        match = re.fullmatch(r"r\.(-?\d+)\.(-?\d+)\.mca", path.name)
        if match is None:
            raise WorldWriteError(f"Invalid Anvil region filename: {path.name}")
        rx, rz = map(int, match.groups())
        saved.update((rx * 32 + (index & 31), rz * 32 + (index >> 5))
                     for index in _Region(path).locations())
    if saved != expected:
        raise WorldWriteError("Saved chunks differ from the loading inventory.")
    return expected


def _row_runs(chunks: set[tuple[int, int]]) -> dict[int, list[tuple[int, int]]]:
    rows = {}
    for x, z in sorted(chunks, key=lambda p: (p[1], p[0])):
        runs = rows.setdefault(z, [])
        if runs and runs[-1][1] + 1 == x:
            runs[-1] = (runs[-1][0], x)
        else:
            runs.append((x, x))
    return rows


def loading_plan(chunks) -> dict:
    """Cover only the actual occupied chunks with legal, disjoint rectangles.

Greedily choose the largest rectangle anchored at the first remaining chunk.
Each height is capped by the per-command area limit. No enclosing-gap chunks
are added, and an independent set reconstruction checks the resulting cover.
"""
    expected = _checked_chunks(chunks)
    rows = _row_runs(expected)
    rectangles = []
    for z in sorted(rows):
        while rows[z]:
            x = rows[z][0][0]
            width = COMMAND_CHUNK_LIMIT
            best = (0, 0, 0)
            for height in range(1, COMMAND_CHUNK_LIMIT + 1):
                runs = rows.get(z + height - 1, [])
                index = bisect_right(runs, (x, float("inf"))) - 1
                if index < 0 or runs[index][1] < x:
                    break
                width = min(width, runs[index][1] - x + 1, COMMAND_CHUNK_LIMIT // height)
                candidate = (width * height, width, height)
                if candidate > best:
                    best = candidate
            _, width, height = best
            end_x, end_z = x + width - 1, z + height - 1
            for row in range(z, end_z + 1):
                runs = rows[row]
                index = bisect_right(runs, (x, float("inf"))) - 1
                left, right = runs[index]
                runs[index:index + 1] = ([(left, x - 1)] if left < x else []) + (
                    [(end_x + 1, right)] if end_x < right else [])
            rectangles.append([x, z, end_x, end_z])
    covered = set()
    for x0, z0, x1, z1 in rectangles:
        area = (x1 - x0 + 1) * (z1 - z0 + 1)
        if not 1 <= area <= COMMAND_CHUNK_LIMIT:
            raise WorldWriteError("A loading rectangle exceeds Java's per-command limit.")
        for z in range(z0, z1 + 1):
            for x in range(x0, x1 + 1):
                if (x, z) in covered:
                    raise WorldWriteError("Loading rectangles overlap.")
                covered.add((x, z))
    if covered != expected:
        raise WorldWriteError("Loading rectangles omit chunks or include unoccupied gaps.")
    digest = hashlib.sha256(json.dumps(sorted(expected), separators=(",", ":")).encode()).hexdigest()
    return {"schema": "shamaos-exact-chunk-loading-v1", "minecraft_version": "1.20.4",
            "dimension": "minecraft:overworld", "requested_chunks": len(expected),
            "chunk_inventory_sha256": digest, "rectangles": rectangles,
            "commands": len(rectangles), "maximum_chunks_per_command": COMMAND_CHUNK_LIMIT,
            "cover_verified": True, "extra_requested_chunks": 0,
            "actual_server_loading_verified": False, "complete_machine_runtime_verified": False}


def _command(rectangle, action: str) -> str:
    x0, z0, x1, z1 = rectangle
    return f"execute in minecraft:overworld run forceload {action} {x0 * 16} {z0 * 16} {x1 * 16} {z1 * 16}"


def _unlinked_path(path: Path) -> None:
    for part in (path, *path.parents):
        if part.is_symlink():
            raise WorldWriteError(f"Loading files cannot use a symbolic-link path: {part}")


def _existing_forced_chunks(world: Path) -> set[tuple[int, int]]:
    """Read, but never modify, vanilla's existing overworld forced-chunk set."""
    path = world / "data" / "chunks.dat"
    _unlinked_path(path)
    if not path.exists():
        return set()
    try:
        root = nbtlib.load(path)
        forced = root["data"]["Forced"]
        if not isinstance(forced, nbtlib.LongArray):
            raise ValueError("Forced is not a LongArray")
        result = set()
        for value in forced:
            packed = int(value) & ((1 << 64) - 1)
            x, z = packed & 0xffffffff, packed >> 32
            result.add((x - (1 << 32) if x >= (1 << 31) else x,
                        z - (1 << 32) if z >= (1 << 31) else z))
        return result
    except Exception as exc:
        raise WorldWriteError("Cannot preserve the world's existing forced-chunk tickets.") from exc


def write_loading_bundle(world: Path, chunks, *, report_directory: Path | None = None) -> dict:
    """Create loading files while excluding a running Minecraft save writer."""
    world = Path(world)
    if (world / "session.lock").exists():
        from .installer import closed_world_lock
        with closed_world_lock(world):
            return _write_loading_bundle(world, chunks, report_directory=report_directory)
    return _write_loading_bundle(world, chunks, report_directory=report_directory)


def prepare_world_loading(world: Path) -> dict:
    """Validate blocks and capture existing tickets under one closed-world lock."""
    from .installer import closed_world_lock
    from .worldio import validate_native_world
    world = Path(world)
    with closed_world_lock(world):
        validate_native_world(world)
        return _write_loading_bundle(world, saved_world_chunks(world))


def _write_loading_bundle(world: Path, chunks, *, report_directory: Path | None = None) -> dict:
    """Write explicit load/stop/unload functions without enabling automatic work.

    No load/tick function tags are installed. Commands execute only when the
    player invokes the returned function. Batches yield between game ticks;
    starting either operation cancels any scheduled batches of the other one.
    """
    world = Path(world).absolute()
    if ".." in world.parts:
        raise WorldWriteError("The selected world path cannot contain parent traversal.")
    expected = _checked_chunks(chunks)
    plan = loading_plan(expected)
    report_directory = Path(report_directory).absolute() if report_directory is not None else world / "shamaos"
    if ".." in report_directory.parts or not report_directory.is_relative_to(world):
        raise WorldWriteError("Loading reports must stay inside the selected world.")
    for path in (world / "datapacks", report_directory / "loading-plan.json", report_directory / "LOADING.txt"):
        _unlinked_path(path)
    prior_report = report_directory / "loading-plan.json"
    protected = expected & _existing_forced_chunks(world)
    if prior_report.exists():
        try:
            prior = json.loads(prior_report.read_text(encoding="utf-8"))
            if (prior["schema"] != plan["schema"]
                    or prior["chunk_inventory_sha256"] != plan["chunk_inventory_sha256"]):
                raise ValueError("different loading inventory")
            protected = set(map(tuple, prior["preserved_existing_forced_chunks"]))
            if protected and (_checked_chunks(sorted(protected)) != protected or not protected <= expected):
                raise ValueError("invalid protected chunk set")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise WorldWriteError("Existing loading reports do not match this inventory; they were not replaced.") from exc
    protection_digest = hashlib.sha256(json.dumps(sorted(protected), separators=(",", ":")).encode()).hexdigest()
    namespace = "shamaos_loading_" + hashlib.sha256(
        (plan["chunk_inventory_sha256"] + protection_digest).encode()).hexdigest()[:16]
    relative_pack = Path("datapacks") / namespace
    base = Path("data") / namespace / "functions"
    unload_rectangles = loading_plan(expected - protected)["rectangles"] if expected - protected else []
    batches = {operation: [rectangles[i:i + COMMANDS_PER_BATCH]
                           for i in range(0, len(rectangles), COMMANDS_PER_BATCH)]
               for operation, rectangles in (("load", plan["rectangles"]), ("unload", unload_rectangles))}
    files = {Path("pack.mcmeta"): json.dumps({"pack": {"pack_format": 26,
             "description": "ShamaOS explicit chunk loading (Java 1.20.4)"}}, indent=2) + "\n"}
    cancellations = [f"schedule clear {namespace}:{operation}/{index:06d}"
                     for operation in ("load", "unload") for index in range(len(batches[operation]))]
    # The cleanup itself must remain below the vanilla chain limit. A larger
    # machine needs a different loading mechanism rather than a truncated plan.
    if len(cancellations) + COMMANDS_PER_BATCH + 8 >= 65536:
        raise WorldWriteError("This loading inventory exceeds the bounded function-chain budget.")
    files[base / "stop.mcfunction"] = "\n".join(cancellations) + "\n"
    for operation, action in (("load", "add"), ("unload", "remove")):
        files[base / f"{operation}.mcfunction"] = (
            f"function {namespace}:stop\n" +
            (f"function {namespace}:{operation}/000000\n" if batches[operation] else ""))
        for index, batch in enumerate(batches[operation]):
            lines = [_command(rectangle, action) for rectangle in batch]
            if index + 1 < len(batches[operation]):
                lines.append(f"schedule function {namespace}:{operation}/{index + 1:06d} 1t replace")
            files[base / operation / f"{index:06d}.mcfunction"] = "\n".join(lines) + "\n"
    destination = world / relative_pack
    _unlinked_path(destination)
    if destination.exists():
        if any(p.is_symlink() for p in destination.rglob("*")):
            raise WorldWriteError("The loading data pack contains a symbolic link.")
        existing = {p.relative_to(destination): p for p in destination.rglob("*") if p.is_file()}
        if set(existing) != set(files) or any(
                existing[name].is_symlink() or existing[name].read_text(encoding="utf-8") != text
                for name, text in files.items()):
            raise WorldWriteError("A different data pack already occupies the loading destination.")
    plan.update(data_pack=relative_pack.as_posix(), namespace=namespace,
                batches=len(batches["load"]), commands_per_batch=COMMANDS_PER_BATCH,
                preserved_existing_forced_chunks=sorted(protected),
                unload_rectangles=unload_rectangles, unload_requested_chunks=len(expected - protected),
                load_command=f"/function {namespace}:load", stop_command=f"/function {namespace}:stop",
                unload_command=f"/function {namespace}:unload")
    reports = {"loading-plan.json": json.dumps(plan, indent=2) + "\n", "LOADING.txt": (
        "Java 1.20.4: load the ShamaOS circuit chunks\n\n"
        "Generate these files with the world closed, then open the saved world.\n"
        f"Start loading: {plan['load_command']}\n"
        f"Stop queued loading/unloading batches: {plan['stop_command']}\n"
        f"Remove tickets added by this plan: {plan['unload_command']}\n\n"
        f"The exact plan requests {plan['requested_chunks']:,} chunks in {plan['commands']:,} commands, "
        f"with at most {COMMAND_CHUNK_LIMIT} chunks per command.\n"
        "Commands run in bounded batches on successive game ticks. Forced tickets persist in the save.\n"
        "Stopping a batch sequence leaves already-added tickets in place; use unload to remove them.\n"
        f"The unload plan preserves {len(protected):,} machine chunks that were already forced at creation.\n"
        "Do not use unload if you have independently forced overlapping chunks since creating this plan.\n"
        "The server can load additional neighboring chunks. Measure actual loaded/ticking coverage and\n"
        "resource use before powering the machine. These files do not certify a running computer.\n")}
    for name, text in reports.items():
        path = report_directory / name
        if path.exists() and (not path.is_file() or path.read_text(encoding="utf-8") != text):
            raise WorldWriteError("Existing loading instructions differ; they were not replaced.")
    # Construct the complete pack privately and publish only after all files are
    # written. An interrupted construction leaves no half-written destination.
    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=".shama-loading-", dir=destination.parent))
        try:
            for name, text in files.items():
                path = temporary / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8", newline="\n")
            temporary.rename(destination)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    report_directory.mkdir(parents=True, exist_ok=True)
    for name, text in reports.items():
        path = report_directory / name
        if path.exists():
            continue
        fd, filename = tempfile.mkstemp(prefix=".shama-loading-", dir=report_directory)
        temporary = Path(filename)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(text)
            # Never replace a report or link created concurrently.
            os.link(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return plan
