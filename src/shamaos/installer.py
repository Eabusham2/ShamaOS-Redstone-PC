"""Add the released machine to a verified copy of a closed superflat save.

The original is only opened for reading. Existing chunks (including their
entities, block entities and other mods' tags) are never decoded or rewritten.
Only previously absent overworld chunks receive the checked machine template.
"""
from __future__ import annotations

import argparse
from collections import OrderedDict
from contextlib import contextmanager, closing
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile

import nbtlib as n

from .model import Vec3
from .release_io import extract_zip, file_inventory, materialize_asset
from .worldio import (
    JAVA_DATA_VERSION, NativeWorldReader, WorldWriteError, _Region, _decode_chunk,
    _flat_surface_from_level, validate_native_world, validate_position,
)


def _windows_shared_lock(stream):
    """Use Win32 shared locking; CRT LK_NBRLCK also blocks other read handles.

    Inventory hashing and copytree open independent read handles. A real shared
    LockFileEx lock permits those reads while still excluding Minecraft's
    exclusive session lock. Keep the OVERLAPPED object alive until unlock.
    """
    import ctypes
    from ctypes import wintypes
    import msvcrt

    class Overlapped(ctypes.Structure):
        _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t),
                    ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD), ("hEvent", wintypes.HANDLE)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                 wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(Overlapped)]
    kernel.LockFileEx.restype = wintypes.BOOL
    kernel.UnlockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                   wintypes.DWORD, ctypes.POINTER(Overlapped)]
    kernel.UnlockFileEx.restype = wintypes.BOOL
    handle = wintypes.HANDLE(msvcrt.get_osfhandle(stream.fileno()))
    overlapped = Overlapped()
    # LOCKFILE_FAIL_IMMEDIATELY; deliberately omit LOCKFILE_EXCLUSIVE_LOCK.
    if not kernel.LockFileEx(handle, 1, 0, 1, 0, ctypes.byref(overlapped)):
        raise ctypes.WinError(ctypes.get_last_error())

    def unlock():
        if not kernel.UnlockFileEx(handle, 0, 1, 0, ctypes.byref(overlapped)):
            raise ctypes.WinError(ctypes.get_last_error())
    return unlock


@contextmanager
def closed_world_lock(world: Path):
    """Hold a read-only lock against Minecraft's exclusive session lock.

    Yield its owning handle: POSIX record locks are released if another handle
    to the same inode is closed, so installer inventory/copy must use this handle
    for session.lock rather than opening the file again.
    """
    path = world / "session.lock"
    if path.is_symlink() or not path.is_file():
        raise WorldWriteError("Open this world once in Minecraft Java 1.20.4, then close Minecraft and try again.")
    stream = path.open("rb")
    unlock = None
    try:
        try:
            if os.name == "nt":
                unlock = _windows_shared_lock(stream)
            else:
                import fcntl
                fcntl.lockf(stream.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB, 0, 0, os.SEEK_SET)
        except OSError as exc:
            raise WorldWriteError("Close Minecraft and any world editor before installing.") from exc
        yield stream
    finally:
        try:
            if unlock is not None:
                unlock()
        finally:
            stream.close()


def _locked_world_inventory(world: Path, lock_stream) -> dict:
    inventory = file_inventory(world, exclude=("session.lock",))
    lock_stream.seek(0)
    inventory["session.lock"] = {"bytes": os.fstat(lock_stream.fileno()).st_size,
                                 "sha256": hashlib.file_digest(lock_stream, "sha256").hexdigest()}
    lock_stream.seek(0)
    return inventory


def _copy_locked_world(source: Path, staging: Path, lock_stream) -> None:
    shutil.copytree(source, staging, dirs_exist_ok=True,
        ignore=lambda folder, _names: {"session.lock"} if Path(folder) == source else set())
    lock_stream.seek(0)
    with (staging / "session.lock").open("xb") as output:
        shutil.copyfileobj(lock_stream, output, 1 << 20)
    lock_stream.seek(0)
    shutil.copystat(source / "session.lock", staging / "session.lock")


def _region_inventory(world: Path):
    """Only inspect location tables; preserve arbitrary existing chunk payloads."""
    for path in sorted((world / "region").glob("*.mca")):
        match = re.fullmatch(r"r\.(-?\d+)\.(-?\d+)\.mca", path.name)
        if match is None:
            raise WorldWriteError(f"Unrecognized region file: {path.name}")
        rx, rz = map(int, match.groups())
        region = _Region(path)
        for index in region.locations():
            yield rx * 32 + (index & 31), rz * 32 + (index >> 5)


def _source_settings(world: Path):
    try:
        level = n.load(world / "level.dat")
        data = level["Data"]
        if int(data["DataVersion"]) != JAVA_DATA_VERSION or int(data["version"]) != 19133:
            raise WorldWriteError("Please choose a Minecraft Java 1.20.4 save.")
        surface = _flat_surface_from_level(level, require_grass=True)
        if surface is None:
            raise WorldWriteError("Please choose a grass superflat world.")
        if "shamaos-install" in {p.name for p in world.iterdir()}:
            raise WorldWriteError("This copy already contains a ShamaOS installation. Choose the original save.")
        return level, surface
    except WorldWriteError:
        raise
    except Exception as exc:
        raise WorldWriteError(f"Cannot read this Minecraft save: {exc}") from exc


def _check_template_sidecars(template: Path) -> None:
    # Runtime-specific chunk loaders, entities, POIs and extra dimensions may
    # carry absolute coordinates outside region chunk NBT. Never silently drop
    # them while claiming a relocated machine is equivalent.
    for name in ("entities", "poi", "data", "DIM-1", "DIM1", "dimensions"):
        folder = template / name
        if folder.exists() and any(path.is_file() for path in folder.rglob("*")):
            raise WorldWriteError(f"This machine template has {name} data that the installer cannot safely relocate. Use its ready-made save.")


def _relocate_chunk(root, x: int, z: int, dx: int, dz: int) -> None:
    allowed = {"DataVersion", "xPos", "zPos", "yPos", "Status", "LastUpdate", "InhabitedTime", "isLightOn",
               "sections", "block_entities", "block_ticks", "fluid_ticks", "PostProcessing", "structures", "Heightmaps"}
    if set(root) - allowed or any(root.get("structures", {}).get(name) for name in ("starts", "References")):
        raise WorldWriteError("This template has unsupported chunk/structure data; use its ready-made save.")
    root["xPos"], root["zPos"] = n.Int(x), n.Int(z)
    for entity in root.get("block_entities", []):
        entity["x"] = n.Int(int(entity["x"]) + dx * 16)
        entity["z"] = n.Int(int(entity["z"]) + dz * 16)
    for field in ("block_ticks", "fluid_ticks"):
        for tick in root.get(field, []):
            if set(tick) - {"i", "x", "y", "z", "t", "p"} or not {"x", "y", "z"} <= set(tick):
                raise WorldWriteError("This template has unsupported scheduled tick data.")
            tick["x"] = n.Int(int(tick["x"]) + dx * 16)
            tick["z"] = n.Int(int(tick["z"]) + dz * 16)


def install_world(template: Path, source: Path, output: Path, *, console: dict,
                  source_commit: str, setup_steps: list[str], offset_chunks: tuple[int, int] | None = None) -> dict:
    """Low-level copy installer; the entry point checks the release certificate.

    Tests call this with explicitly small native fixtures to verify file safety.
    Such fixtures can never pass the public release candidate gate.
    """
    source, template = source.resolve(), template.resolve()
    output = output.parent.resolve() / output.name
    if output.exists() or output.is_symlink():
        raise WorldWriteError("The new save folder already exists. Choose a different name.")
    if source == template or source in output.parents or template in output.parents:
        raise WorldWriteError("Choose a new save folder outside both original folders.")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = None
    with closed_world_lock(source) as source_lock, closed_world_lock(template):
        level, surface = _source_settings(source)
        report = validate_native_world(template)
        _check_template_sidecars(template)
        if report["grass_surface_y"] != surface:
            raise WorldWriteError(
                "This world uses a different ground height. Use a default grass superflat world "
                "or the ready-made ShamaOS save; moving this tall machine vertically could exceed build height.")
        original = _locked_world_inventory(source, source_lock)
        template_bytes = sum(p.stat().st_size for p in (template / "region").glob("*.mca"))
        required = sum(item["bytes"] for item in original.values()) + template_bytes * 2 + (128 << 20)
        if shutil.disk_usage(output.parent).free < required:
            raise WorldWriteError("There is not enough free space to copy your save and add the computer.")
        ledger = template / "shamaos" / "expected.sqlite3"
        with closing(sqlite3.connect(f"{ledger.resolve().as_uri()}?mode=ro", uri=True)) as db:
            extents = db.execute("SELECT MIN(x),MAX(x),MIN(z),MAX(z),COUNT(*) FROM chunks").fetchone()
            if not extents[-1]:
                raise WorldWriteError("The machine download contains no chunks.")
            if offset_chunks is None:
                max_existing_x = max((cx for cx, _ in _region_inventory(source)), default=0)
                offset_chunks = (max_existing_x + 33 - extents[0], 0)
            dx, dz = (int(v) for v in offset_chunks)
            for x in (extents[0], extents[1]):
                for z in (extents[2], extents[3]):
                    validate_position(Vec3((x + dx) * 16, surface + 1, (z + dz) * 16))
                    validate_position(Vec3((x + dx) * 16 + 15, 319, (z + dz) * 16 + 15))
            # Check collisions before copying or adding any machine files.
            target_regions = OrderedDict()
            for cx, cz in db.execute("SELECT x,z FROM chunks ORDER BY x,z"):
                x, z = cx + dx, cz + dz
                key = (x // 32, z // 32)
                if key not in target_regions:
                    target_regions[key] = _Region(source / "region" / f"r.{key[0]}.{key[1]}.mca")
                    if len(target_regions) > 8:
                        target_regions.popitem(last=False)
                if target_regions[key].record((x & 31) + (z & 31) * 32) is not None:
                    raise WorldWriteError("The requested spot already has saved chunks. Choose an unused area.")

            staging = Path(tempfile.mkdtemp(prefix=f".{output.name}-install-", dir=output.parent))
            try:
                _copy_locked_world(source, staging, source_lock)
                if file_inventory(staging) != original or _locked_world_inventory(source, source_lock) != original:
                    raise WorldWriteError("The original save changed while copying. Close Minecraft and try again.")
                # The complete copy is verified before its first mutation. The
                # unchanged original remains the recoverable pre-install save.
                target_regions.clear()
                reader = NativeWorldReader(template, max_cached_chunks=1)
                for cx, cz in db.execute("SELECT x,z FROM chunks ORDER BY x,z"):
                    source_region = _Region(template / "region" / f"r.{cx // 32}.{cz // 32}.mca")
                    root = source_region.read((cx & 31) + (cz & 31) * 32)
                    if root is None:
                        raise WorldWriteError("A machine chunk is missing.")
                    x, z = cx + dx, cz + dz
                    _relocate_chunk(root, x, z, dx, dz)
                    key = (x // 32, z // 32)
                    if key not in target_regions:
                        target_regions[key] = _Region(staging / "region" / f"r.{key[0]}.{key[1]}.mca")
                        if len(target_regions) > 8:
                            target_regions.popitem(last=False)
                    region = target_regions[key]
                    index = (x & 31) + (z & 31) * 32
                    region.write(index, root)
                    # Compare the relocated serialized block data independently.
                    saved_root = region.read(index)
                    saved = _decode_chunk(saved_root, x, z)
                    expected = reader.chunk(cx, cz)
                    saved.x, saved.z = cx, cz
                    if saved.fingerprint() != expected.fingerprint():
                        raise WorldWriteError("The installed machine failed its block check.")
                    for field in ("block_ticks", "fluid_ticks", "block_entities"):
                        if saved_root.get(field) != root.get(field):
                            raise WorldWriteError("The installed machine failed its scheduled-tick/entity check.")
                position = {"x": int(console["x"]) + dx * 16, "y": int(console["y"]),
                            "z": int(console["z"]) + dz * 16}
                data = level["Data"]
                data["LevelName"] = n.String(str(data["LevelName"]) + " - ShamaOS")
                # Preserve the player's progress and all other saved data. The
                # first launch directions provide the console teleport instead
                # of silently rewriting player inventories/locations.
                for axis in "XYZ":
                    data[f"Spawn{axis}"] = n.Int(position[axis.lower()])
                level.save(staging / "level.dat")
                info = staging / "shamaos-install"
                info.mkdir()
                details = {"format": "shamaos-install-v1", "source_commit": source_commit,
                    "original_save": str(source), "copied_files_verified": len(original),
                    "installed_chunks": extents[-1], "offset_blocks": [dx * 16, 0, dz * 16],
                    "console": position, "original_unchanged": True,
                    "setup_steps": [s.format(offset_x=dx * 16, offset_z=dz * 16, **position) for s in setup_steps]}
                (info / "installation.json").write_text(json.dumps(details, indent=2) + "\n", encoding="utf-8")
                (staging / "START-HERE.txt").write_text(
                    "Open this new save in Minecraft Java 1.20.4. Your original save is unchanged.\n"
                    f"To reach the console: /tp @s {position['x']} {position['y']} {position['z']}\n"
                    + "\n".join(details["setup_steps"]) + "\n", encoding="utf-8")
                if _locked_world_inventory(source, source_lock) != original:
                    raise WorldWriteError("The original save changed during installation; the new copy was not published.")
                # Refuse a destination created by another process since preflight.
                if output.exists():
                    raise WorldWriteError("The new save name was taken during installation.")
                staging.rename(output)
                staging = None
                return details
            finally:
                if staging is not None:
                    shutil.rmtree(staging)


def choose_world() -> Path:
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        try:
            selected = filedialog.askdirectory(title="Choose your closed Java 1.20.4 grass superflat save")
        finally:
            root.destroy()
        if not selected:
            raise WorldWriteError("No world selected; nothing changed.")
        return Path(selected)
    except ImportError:
        return Path(input("Paste the path of your closed grass superflat save folder: ").strip().strip('"'))
    except Exception as exc:
        if exc.__class__.__name__ == "TclError":
            return Path(input("Paste the path of your closed grass superflat save folder: ").strip().strip('"'))
        raise


def extract_ready_world(template: Path, output: Path, expected: dict) -> None:
    """Publish a verified ready-world copy atomically, even across filesystems."""
    output = output.parent.resolve() / output.name
    if output.exists() or output.is_symlink():
        raise WorldWriteError("The ShamaOS folder already exists; choose an empty destination.")
    output.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(output.parent).free < sum(item["bytes"] for item in expected.values()) + (64 << 20):
        raise WorldWriteError("There is not enough free space to unpack the ready-made world.")
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}-unpack-", dir=output.parent))
    try:
        shutil.copytree(template, staging, dirs_exist_ok=True)
        if file_inventory(staging) != expected:
            raise WorldWriteError("The unpacked world failed its file check.")
        validate_native_world(staging)
        if output.exists():
            raise WorldWriteError("The destination name was taken while unpacking.")
        staging.rename(output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Create a new copy of your superflat world with ShamaOS added.")
    parser.add_argument("--downloads", type=Path, default=Path.cwd())
    parser.add_argument("--world", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--extract-world", action="store_true", help="unpack the ready-made world instead")
    args = parser.parse_args(argv)
    try:
        metadata = json.loads((args.downloads / "release-manifest.json").read_text(encoding="utf-8"))
        if metadata.get("format") != "shamaos-release-v1" or metadata.get("scope") != "complete-machine" or metadata.get("status") != "passed":
            raise WorldWriteError("This download is not a verified complete ShamaOS release.")
        descriptor = metadata["assets"]["world"]
        with tempfile.TemporaryDirectory(prefix="shamaos-download-") as temporary:
            temp = Path(temporary)
            archive = materialize_asset(args.downloads, descriptor, temp / "world.zip")
            extract_zip(archive, temp / "unpacked", inventory=metadata["world_files"])
            template = temp / "unpacked" / "ShamaOS"
            if args.extract_world:
                output = args.output or (args.downloads / "ShamaOS")
                expected = {name.removeprefix("ShamaOS/"): value for name, value in metadata["world_files"].items()}
                extract_ready_world(template, output, expected)
                print(f"Ready-made world: {output}. Move this folder into Minecraft's saves folder.")
                return 0
            source = args.world or choose_world()
            output = args.output or source.with_name(source.name + " - ShamaOS")
            print("Copying your save and adding ShamaOS. The original save stays unchanged.")
            result = install_world(template, source, output, console=metadata["console"],
                source_commit=metadata["source_commit"], setup_steps=metadata["installer_setup_steps"])
            print(f"Done. Open the new save: {output}\nConsole: {result['console']}\nRead START-HERE.txt in the new save.")
        return 0
    except (WorldWriteError, OSError, ValueError, KeyError) as exc:
        print(f"Could not finish: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
