"""Streaming, deterministic release archives and strict download verification."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
import zipfile

from .worldio import WorldWriteError


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def file_inventory(root: Path, *, exclude: tuple[str, ...] = ()) -> dict:
    result = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise WorldWriteError(f"Linked files are not supported: {relative}")
        if path.is_file() and relative not in exclude:
            result[relative] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
        elif not path.is_dir() and not path.is_file():
            raise WorldWriteError(f"Unsupported file: {relative}")
    return result


def inventory_digest(inventory: dict) -> str:
    return hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def write_zip(output: Path, files: dict[str, Path]) -> None:
    """ZIP64 supports a large world; never hold an archive/file in memory."""
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6,
                         allowZip64=True) as archive:
        for name, source in sorted(files.items()):
            safe_relative(name)
            if source.is_symlink() or not source.is_file():
                raise WorldWriteError(f"Cannot archive non-regular file: {source}")
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            with source.open("rb") as incoming, archive.open(info, "w", force_zip64=True) as outgoing:
                shutil.copyfileobj(incoming, outgoing, 1 << 20)
    with zipfile.ZipFile(output) as archive:
        bad = archive.testzip()
        if bad:
            raise WorldWriteError(f"Archive readback failed: {bad}")


def safe_relative(name: str) -> PurePosixPath:
    relative = PurePosixPath(name)
    if (not name or "\\" in name or relative.is_absolute() or ".." in relative.parts
            or any(":" in part for part in relative.parts) or str(relative) != name
            or any(part.endswith((".", " ")) for part in relative.parts)):
        raise WorldWriteError(f"Unsafe archive path: {name!r}")
    return relative


def extract_zip(archive_path: Path, destination: Path, *, inventory: dict | None = None) -> None:
    """Reject traversal, duplicate paths, links, unexpected files and truncation."""
    destination.mkdir(parents=True, exist_ok=False)
    seen = set()
    try:
        with zipfile.ZipFile(archive_path) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                # ZipInfo normalizes backslashes on Windows and truncates at
                # NUL. Validate the stored name before either transformation.
                relative = safe_relative(info.orig_filename)
                if info.filename != info.orig_filename:
                    raise WorldWriteError("Unsafe archive path was normalized by the ZIP reader")
                # Case-fold to avoid collisions on Windows/macOS as well.
                normalized = str(relative).casefold()
                if normalized in seen or stat.S_ISLNK(info.external_attr >> 16):
                    raise WorldWriteError("Duplicate or linked file in download")
                seen.add(normalized)
                expected = None if inventory is None else inventory.get(str(relative))
                if inventory is not None and (expected is None or expected["bytes"] != info.file_size):
                    raise WorldWriteError(f"Unexpected file or file size in download: {relative}")
                path = destination.joinpath(*relative.parts)
                path.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as incoming, path.open("xb") as outgoing:
                    shutil.copyfileobj(incoming, outgoing, 1 << 20)
                if expected is not None and sha256(path) != expected["sha256"]:
                    raise WorldWriteError(f"Download check failed: {relative}")
        if inventory is not None and seen != {name.casefold() for name in inventory}:
            raise WorldWriteError("Download is missing files")
    except Exception:
        shutil.rmtree(destination)
        raise


def split_asset(path: Path, *, part_bytes: int = 1 << 30) -> dict:
    """Keep every uploaded file below GitHub's 2 GiB per-asset limit."""
    if part_bytes <= 0 or part_bytes >= 2 << 30:
        raise ValueError("release part size must be between 1 byte and 2 GiB")
    descriptor = {"name": path.name, "bytes": path.stat().st_size, "sha256": sha256(path)}
    parts = []
    if descriptor["bytes"] <= part_bytes:
        parts.append(dict(descriptor))
    else:
        with path.open("rb") as incoming:
            index = 1
            while incoming.tell() < descriptor["bytes"]:
                part = path.with_name(f"{path.name}.{index:03d}")
                remaining = part_bytes
                with part.open("xb") as outgoing:
                    while remaining:
                        data = incoming.read(min(1 << 20, remaining))
                        if not data:
                            break
                        outgoing.write(data)
                        remaining -= len(data)
                parts.append({"name": part.name, "bytes": part.stat().st_size, "sha256": sha256(part)})
                index += 1
        path.unlink()
    descriptor["parts"] = parts
    return descriptor


def materialize_asset(folder: Path, descriptor: dict, destination: Path) -> Path:
    """Verify each download, then join split files and verify the whole result."""
    parts = descriptor.get("parts", [])
    if not parts or sum(part["bytes"] for part in parts) != descriptor["bytes"]:
        raise WorldWriteError("Invalid release file list")
    for part in parts:
        if len(safe_relative(part["name"]).parts) != 1:
            raise WorldWriteError("Release downloads must be in one folder")
        path = folder / part["name"]
        if path.is_symlink() or not path.is_file() or path.stat().st_size != part["bytes"] or sha256(path) != part["sha256"]:
            raise WorldWriteError(f"Download {part['name']} again; it is missing or incomplete.")
    if len(parts) == 1 and parts[0]["sha256"] == descriptor["sha256"]:
        return folder / parts[0]["name"]
    try:
        with destination.open("xb") as outgoing:
            for part in parts:
                with (folder / part["name"]).open("rb") as incoming:
                    shutil.copyfileobj(incoming, outgoing, 1 << 20)
        if sha256(destination) != descriptor["sha256"]:
            raise WorldWriteError("The joined download failed its check")
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return destination
