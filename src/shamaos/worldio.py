from __future__ import annotations

from contextlib import AbstractContextManager
from pathlib import Path
from typing import Iterable

from .model import BlockState, Placement


def to_amulet_block(state: BlockState):
    """Convert a project BlockState to an exact Amulet Java block state.

    Amulet requires NBT tag values for block properties. Never silently drop
    repeater/comparator/lamp properties: orientation, lock state and power
    state are functional parts of the computer.
    """
    try:
        from amulet.api.block import Block  # type: ignore
        from amulet_nbt import StringTag  # type: ignore
    except Exception as exc:
        raise WorldWriteError(
            "world generation requires the optional 'world' dependencies; "
            "install with: pip install -e '.[world]'"
        ) from exc

    if ":" not in state.name:
        namespace, base_name = "minecraft", state.name
    else:
        namespace, base_name = state.name.split(":", 1)

    properties = {
        key: StringTag(value)
        for key, value in state.properties
    }
    return Block(namespace, base_name, properties)


class WorldWriteError(RuntimeError):
    pass


class WorldWriter(AbstractContextManager["WorldWriter"]):
    def set_block(self, placement: Placement) -> None:
        raise NotImplementedError

    def set_blocks(self, placements: Iterable[Placement]) -> None:
        for placement in placements:
            self.set_block(placement)

    def save(self) -> None:
        raise NotImplementedError

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.save()
        return False


class AmuletWorldWriter(WorldWriter):
    """Minecraft Java world writer using Amulet Core.

    Amulet is deliberately imported lazily so assembler/reference-model users
    do not need the large world-editing dependency.

    The adapter targets the classic Amulet Core API used by Java world editors.
    If an installed Amulet major version changes that API, fail loudly instead
    of silently writing the wrong world.
    """

    def __init__(
        self,
        world_path: str | Path,
        *,
        dimension: str = "minecraft:overworld",
        version: tuple[str, tuple[int, int, int]] = ("java", (1, 20, 4)),
    ) -> None:
        self.world_path = Path(world_path)
        if not (self.world_path / "level.dat").exists():
            raise WorldWriteError(f"not a Java world folder: {self.world_path}")

        try:
            import amulet  # type: ignore
        except Exception as exc:
            raise WorldWriteError(
                "world generation requires the optional 'world' dependencies; "
                "install with: pip install -e '.[world]'"
            ) from exc

        self._amulet = amulet
        try:
            self._world = amulet.load_level(str(self.world_path))
        except Exception as exc:
            raise WorldWriteError(f"failed to open world: {exc}") from exc

        self.dimension = dimension
        self.version = version
        self._closed = False

    def _block(self, state: BlockState):
        return to_amulet_block(state)

    def set_block(self, placement: Placement) -> None:
        p = placement.pos
        try:
            self._world.set_version_block(
                p.x,
                p.y,
                p.z,
                self.dimension,
                self.version,
                self._block(placement.block),
            )
        except Exception as exc:
            raise WorldWriteError(
                f"failed placing {placement.block.name} at {placement.pos}: {exc}"
            ) from exc

    def save(self) -> None:
        if self._closed:
            return
        try:
            self._world.save()
        except Exception as exc:
            raise WorldWriteError(f"world save failed: {exc}") from exc

    def close(self) -> None:
        if self._closed:
            return
        try:
            self._world.close()
        finally:
            self._closed = True

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self.save()
        finally:
            self.close()
        return False
