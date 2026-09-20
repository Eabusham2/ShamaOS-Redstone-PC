from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Literal

from .assembler import AssemblyError, assemble
from .flashfs import FileEntry, FileType, ShamaFS
from .sha256 import MiningState


APP_DEFAULT_RAM = {
    "desktop": 24 * 1024,
    "editor": 64 * 1024,
    "files": 32 * 1024,
    "miner": 48 * 1024,
    "monitor": 24 * 1024,
    "terminal": 32 * 1024,
    "calculator": 16 * 1024,
    "paint": 64 * 1024,
    "settings": 16 * 1024,
}


@dataclass(frozen=True)
class ResourceSnapshot:
    ram_used: int
    ram_total: int
    cache_used: int
    cache_total: int
    flash_used: int
    flash_total: int

    @property
    def ram_free(self) -> int:
        return self.ram_total - self.ram_used

    @property
    def cache_free(self) -> int:
        return self.cache_total - self.cache_used

    @property
    def flash_free(self) -> int:
        return self.flash_total - self.flash_used


@dataclass
class Allocation:
    owner: str
    start: int
    size: int


class MemoryManager:
    """First-fit allocation model used as the OS/reference contract."""

    def __init__(self, total: int = 1 << 20, reserved: int = 96 << 10) -> None:
        if not 0 <= reserved < total:
            raise ValueError("invalid RAM reservation")
        self.total = total
        self.reserved = reserved
        self.allocations: list[Allocation] = [
            Allocation("kernel", 0, reserved)
        ]

    def allocate(self, owner: str, size: int, *, align: int = 256) -> Allocation:
        if size <= 0:
            raise ValueError("allocation size must be positive")
        spans = sorted(self.allocations, key=lambda a: a.start)
        cursor = self.reserved
        for alloc in spans:
            if alloc.start < self.reserved:
                continue
            cursor = (cursor + align - 1) // align * align
            if cursor + size <= alloc.start:
                break
            cursor = max(cursor, alloc.start + alloc.size)
        cursor = (cursor + align - 1) // align * align
        if cursor + size > self.total:
            raise MemoryError("ShamaOS RAM exhausted")
        result = Allocation(owner, cursor, size)
        self.allocations.append(result)
        return result

    def free_owner(self, owner: str) -> int:
        removed = sum(a.size for a in self.allocations if a.owner == owner)
        self.allocations = [a for a in self.allocations if a.owner != owner]
        return removed

    @property
    def used(self) -> int:
        return sum(a.size for a in self.allocations)


class CacheManager:
    def __init__(self, total: int = 16 << 10, line_size: int = 32) -> None:
        if total <= 0 or line_size <= 0 or total % line_size:
            raise ValueError("invalid cache geometry")
        self.total = total
        self.line_size = line_size
        self._lines: dict[tuple[str, int], bool] = {}

    @property
    def capacity_lines(self) -> int:
        return self.total // self.line_size

    def touch(self, owner: str, byte_count: int, *, dirty: bool = False) -> None:
        lines = min(self.capacity_lines, (max(0, byte_count) + self.line_size - 1) // self.line_size)
        # Reference replacement policy is deterministic owner/index FIFO-ish.
        for idx in range(lines):
            if len(self._lines) >= self.capacity_lines and (owner, idx) not in self._lines:
                oldest = next(iter(self._lines))
                self._lines.pop(oldest)
            self._lines[(owner, idx)] = self._lines.get((owner, idx), False) or dirty

    def invalidate_owner(self, owner: str) -> int:
        before = len(self._lines)
        self._lines = {k: v for k, v in self._lines.items() if k[0] != owner}
        return (before - len(self._lines)) * self.line_size

    @property
    def used(self) -> int:
        return len(self._lines) * self.line_size


@dataclass
class AppProcess:
    name: str
    pid: int
    allocation: Allocation
    executable: str
    open_files: set[str] = field(default_factory=set)


@dataclass
class EditorState:
    filename: str | None = None
    buffer: str = ""
    kind: Literal["txt", "program"] = "txt"
    dirty: bool = False


@dataclass
class MinerRuntime:
    state: MiningState | None = None
    running: bool = False
    found: bool = False
    saved_runs: list[tuple[int, int, str, int]] = field(default_factory=list)


class ShamaOSRuntime:
    """Executable reference contract for the in-world ShamaOS behavior.

    This is a test oracle and OS-behavior model. The generated Minecraft
    machine must reproduce these state transitions; Python is not the runtime
    used by the final redstone world.
    """

    def __init__(
        self,
        fs: ShamaFS,
        *,
        ram_bytes: int = 1 << 20,
        cache_bytes: int = 16 << 10,
    ) -> None:
        self.fs = fs
        self.ram = MemoryManager(ram_bytes)
        self.cache = CacheManager(cache_bytes)
        self.processes: dict[int, AppProcess] = {}
        self.foreground_pid: int | None = None
        self.next_pid = 1
        self.editor = EditorState()
        self.miner = MinerRuntime()
        self.booted = False
        self.powered = False
        self.last_confirmation: tuple[str, str] | None = None

    @classmethod
    def from_flash_image(cls, image: bytes, **kwargs) -> "ShamaOSRuntime":
        return cls(ShamaFS.deserialize(image), **kwargs)

    @property
    def foreground(self) -> AppProcess | None:
        return self.processes.get(self.foreground_pid) if self.foreground_pid else None

    def resources(self) -> ResourceSnapshot:
        return ResourceSnapshot(
            ram_used=self.ram.used,
            ram_total=self.ram.total,
            cache_used=self.cache.used,
            cache_total=self.cache.total,
            flash_used=self.fs.used_bytes,
            flash_total=self.fs.flash_bytes,
        )

    def power_on(self) -> None:
        if self.powered:
            return
        # Mount happened when the ShamaFS image was deserialized/constructed.
        required = {"kernel.sys", "desktop.bin", "editor.bin", "files.bin", "miner.bin"}
        missing = sorted(required - {e.name for e in self.fs.list_files()})
        if missing:
            raise RuntimeError(f"boot files missing: {', '.join(missing)}")
        self.powered = True
        self.booted = True
        self.launch("desktop")

    def power_off(self) -> None:
        if not self.powered:
            return
        for pid in list(self.processes):
            self._close_pid(pid)
        self.fs.clean = True
        self.powered = False
        self.booted = False

    def _app_executable(self, name: str) -> str:
        mapping = {
            "desktop": "desktop.bin",
            "editor": "editor.bin",
            "files": "files.bin",
            "miner": "miner.bin",
            "monitor": "monitor.bin",
            "terminal": "terminal.bin",
            "calculator": "calculator.bin",
            "paint": "paint.bin",
            "settings": "settings.bin",
        }
        try:
            return mapping[name]
        except KeyError as exc:
            raise ValueError(f"unknown ShamaOS app {name!r}") from exc

    def launch(self, name: str, *, close_foreground: bool = False) -> AppProcess:
        if not self.powered and name != "desktop":
            raise RuntimeError("ShamaOS is powered off")
        if close_foreground and self.foreground_pid:
            self.exit_foreground()
        executable = self._app_executable(name)
        if not self.fs.exists(executable):
            raise FileNotFoundError(executable)
        size = APP_DEFAULT_RAM.get(name, 24 << 10)
        alloc = self.ram.allocate(f"pid:{self.next_pid}:{name}", size)
        proc = AppProcess(name, self.next_pid, alloc, executable)
        self.processes[proc.pid] = proc
        self.foreground_pid = proc.pid
        self.cache.touch(f"pid:{proc.pid}:{name}", min(size, len(self.fs.read_file(executable)) + 4096))
        self.next_pid += 1
        return proc

    def _close_pid(self, pid: int) -> None:
        proc = self.processes.pop(pid, None)
        if not proc:
            return
        owner = proc.allocation.owner
        self.ram.free_owner(owner)
        self.cache.invalidate_owner(owner)
        if self.foreground_pid == pid:
            self.foreground_pid = None

    def exit_foreground(self, *, return_desktop: bool = True) -> None:
        proc = self.foreground
        if not proc:
            return
        name = proc.name
        self._close_pid(proc.pid)
        if return_desktop and self.powered and name != "desktop":
            # Re-use an existing desktop if one exists.
            desktop = next((p for p in self.processes.values() if p.name == "desktop"), None)
            if desktop:
                self.foreground_pid = desktop.pid
            else:
                self.launch("desktop")

    def universal_action(self, action: str) -> None:
        action = action.upper()
        if action in {"EXIT", "HOME"}:
            self.exit_foreground(return_desktop=True)
        elif action == "EDITOR":
            self.launch("editor", close_foreground=True)
        elif action in {"FILES", "FILE_EXPLORER"}:
            self.launch("files", close_foreground=True)
        else:
            raise ValueError(f"unknown universal action {action}")

    # ---------- File Explorer ----------
    def file_list(self) -> list[FileEntry]:
        return self.fs.list_files()

    def file_read_text(self, name: str) -> str:
        return self.fs.read_file(name).decode("utf-8")

    def explorer_open(self, name: str, *, edit: bool = False) -> bytes | str:
        entry = self.fs.stat(name)
        if edit and entry.file_type in {FileType.TXT, FileType.ASM}:
            self.launch("editor", close_foreground=True)
            self.editor_open(name)
            return self.editor.buffer
        if entry.file_type == FileType.TXT:
            return self.file_read_text(name)
        return self.fs.read_file(name)

    def rename_file(self, old: str, new: str) -> None:
        self.fs.rename(old, new)
        if self.editor.filename == old:
            self.editor.filename = new

    def request_delete(self, name: str) -> tuple[str, str]:
        if not self.fs.exists(name):
            raise FileNotFoundError(name)
        self.last_confirmation = ("delete", name)
        return self.last_confirmation

    def confirm_delete(self, *, confirm: bool) -> bool:
        pending = self.last_confirmation
        self.last_confirmation = None
        if not pending or pending[0] != "delete":
            return False
        if not confirm:
            return False
        name = pending[1]
        self.fs.delete(name)
        if self.editor.filename == name:
            self.editor = EditorState()
        return True

    # ---------- Editor ----------
    def editor_new_text(self, name: str) -> None:
        if not name.lower().endswith(".txt"):
            name += ".txt"
        self.editor = EditorState(name, "", "txt", True)

    def editor_new_program(self, name: str) -> None:
        if not name.lower().endswith(".asm"):
            name += ".asm"
        self.editor = EditorState(name, "", "program", True)

    def editor_open(self, name: str) -> None:
        entry = self.fs.stat(name)
        if entry.file_type not in {FileType.TXT, FileType.ASM}:
            raise ValueError("Editor opens TXT or ASM source files")
        self.editor = EditorState(
            filename=name,
            buffer=entry.data.decode("utf-8"),
            kind="program" if entry.file_type == FileType.ASM else "txt",
            dirty=False,
        )

    def editor_set_buffer(self, text: str) -> None:
        if self.editor.filename is None:
            raise RuntimeError("no Editor document")
        self.editor.buffer = text
        self.editor.dirty = True

    def editor_save(self) -> None:
        if self.editor.filename is None:
            raise RuntimeError("no Editor document")
        ftype = FileType.ASM if self.editor.kind == "program" else FileType.TXT
        data = self.editor.buffer.encode("utf-8")
        if self.fs.exists(self.editor.filename):
            self.fs.write_file(self.editor.filename, data, file_type=ftype)
        else:
            self.fs.create_file(self.editor.filename, data, ftype)
        self.editor.dirty = False

    def editor_save_as(self, name: str) -> None:
        suffix = ".asm" if self.editor.kind == "program" else ".txt"
        if not name.lower().endswith(suffix):
            name += suffix
        self.editor.filename = name
        self.editor_save()

    def editor_rename(self, new_name: str) -> None:
        if self.editor.filename is None:
            raise RuntimeError("no Editor document")
        old = self.editor.filename
        suffix = ".asm" if self.editor.kind == "program" else ".txt"
        if not new_name.lower().endswith(suffix):
            new_name += suffix
        if self.fs.exists(old):
            self.fs.rename(old, new_name)
        self.editor.filename = new_name

    def editor_assemble(self) -> str:
        if self.editor.kind != "program" or not self.editor.filename:
            raise RuntimeError("current Editor document is not a program")
        result = assemble(self.editor.buffer)
        out_name = str(PurePosixPath(self.editor.filename).with_suffix(".bin"))
        data = result.to_bytes()
        if self.fs.exists(out_name):
            self.fs.write_file(out_name, data, file_type=FileType.BIN)
        else:
            self.fs.create_file(out_name, data, FileType.BIN)
        return out_name

    # ---------- Miner ----------
    def miner_configure(self, header: bytes, target: bytes, *, generation: int = 0) -> None:
        if len(header) != 80 or len(target) != 32:
            raise ValueError("miner needs 80-byte header and 32-byte target")
        self.miner.state = MiningState(bytearray(header), bytes(target), generation=generation)
        self.miner.running = False
        self.miner.found = False

    def miner_start(self) -> None:
        if self.miner.state is None:
            raise RuntimeError("miner is not configured")
        self.miner.running = True

    def miner_stop(self) -> None:
        self.miner.running = False
        self._save_miner_state()

    def miner_step(self, hashes: int = 1) -> bool:
        if not self.miner.running or self.miner.state is None:
            return False
        for _ in range(max(0, hashes)):
            if self.miner.state.step():
                self.miner.running = False
                self.miner.found = True
                self._log_miner_result()
                return True
        return False

    def _save_miner_state(self) -> None:
        state = self.miner.state
        if state is None:
            return
        text = (
            f"generation={state.generation}\n"
            f"nonce={state.nonce()}\n"
            f"attempts={state.attempts}\n"
            f"running={int(self.miner.running)}\n"
            f"last_hash={state.last_hash.hex()}\n"
        ).encode()
        self.fs.write_file("miner-state.cfg", text, file_type=FileType.CFG)

    def _log_miner_result(self) -> None:
        state = self.miner.state
        if state is None:
            return
        record = (state.generation, state.nonce(), state.last_hash.hex(), state.attempts)
        self.miner.saved_runs.append(record)
        line = (
            f"gen={record[0]} nonce={record[1]} hash={record[2]} attempts={record[3]}\n"
        ).encode()
        old = self.fs.read_file("miner-history.log") if self.fs.exists("miner-history.log") else b""
        self.fs.write_file("miner-history.log", old + line, file_type=FileType.LOG)
        self._save_miner_state()

    def flash_image(self) -> bytes:
        return self.fs.serialize()
