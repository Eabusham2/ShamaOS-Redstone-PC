"""Read hash-addressed tar.xz recordings or the original direct ZIP distribution."""
from contextlib import contextmanager
from hashlib import sha256
from io import BytesIO
import json
import re
import tarfile
from zipfile import ZipFile


class ArchiveView:
    def __init__(self, manifest, blobs):
        self.manifest = manifest
        self.blobs = blobs

    def read(self, name):
        if name == "manifest.json":
            return self.blobs["manifest.json"]
        return self.blobs[self.manifest[name]]


def add_bytes(container, name, data):
    member = tarfile.TarInfo(name)
    member.size = len(data)
    # TarInfo defaults to zero timestamps and zero ownership.
    container.addfile(member, BytesIO(data))


def write_recording(destination, files):
    manifest = {}
    unique = {}
    for name, path in sorted(files.items()):
        data = path.read_bytes()
        key = sha256(data).hexdigest()
        manifest[name] = key
        unique.setdefault(key, data)
    with tarfile.open(destination, "x:xz", preset=4) as out:
        add_bytes(out, "manifest.json", json.dumps(manifest, indent=2, sort_keys=True).encode() + b"\n")
        for key in sorted(unique):
            add_bytes(out, f"blobs/{key}", unique[key])
    return len(manifest), len(unique)


@contextmanager
def open_recording(path):
    if str(path).endswith(".zip"):
        with ZipFile(path) as archived:
            yield archived
        return
    blobs = {}
    with tarfile.open(path, "r|xz") as archived:
        for member in archived:
            key = member.name if member.name == "manifest.json" else member.name.removeprefix("blobs/")
            if (not member.isfile() or key in blobs or member.size > 64 * 1024**2
                    or key != "manifest.json" and not re.fullmatch(r"blobs/[0-9a-f]{64}", member.name)):
                raise ValueError("Invalid recording member")
            data = archived.extractfile(member).read()
            if key != "manifest.json" and sha256(data).hexdigest() != key:
                raise ValueError("Invalid recorded blob digest")
            blobs[key] = data
    manifest = json.loads(blobs["manifest.json"])
    if set(manifest.values()) != blobs.keys() - {"manifest.json"}:
        raise ValueError("Missing or extra recorded blobs")
    yield ArchiveView(manifest, blobs)
