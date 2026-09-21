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
    """Fast Minecraft Java world writer backed by stable Amulet Core 1.9.x.

    The generator is vastly larger than a normal editor operation, so this
    writer:
      * creates missing far-away chunks explicitly;
      * translates each distinct Java block state only once;
      * registers the universal block once in the level palette;
      * writes runtime palette IDs directly into chunk block arrays;
      * periodically saves and unloads chunk caches.

    Functional block properties are never dropped.
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

        try:
            self._world = amulet.load_level(str(self.world_path))
        except Exception as exc:
            raise WorldWriteError(f"failed to open world: {exc}") from exc

        self.dimension = dimension
        self.version = version
        self._closed = False
        self._chunk_cache: dict[tuple[int, int], object] = {}
        self._block_id_cache: dict[BlockState, int] = {}

        try:
            self._translator = self._world.translation_manager.get_version(
                version[0],
                version[1],
            )
        except Exception as exc:
            self._world.close()
            raise WorldWriteError(
                f"Amulet does not support requested block version {version}: {exc}"
            ) from exc

    def _block_id(self, state: BlockState) -> int:
        cached = self._block_id_cache.get(state)
        if cached is not None:
            return cached

        java_block = to_amulet_block(state)
        try:
            universal_block, block_entity, extra = (
                self._translator.block.to_universal(java_block)
            )
        except Exception as exc:
            raise WorldWriteError(
                f"failed translating block state {state}: {exc}"
            ) from exc

        if block_entity is not None or extra:
            raise WorldWriteError(
                f"block state {state} requires context/block entity translation; "
                "ShamaOS hardware only supports context-free redstone blocks"
            )

        try:
            block_id = self._world.block_palette.get_add_block(universal_block)
        except Exception as exc:
            raise WorldWriteError(
                f"failed registering block state {state}: {exc}"
            ) from exc

        self._block_id_cache[state] = int(block_id)
        return int(block_id)

    def _chunk(self, cx: int, cz: int):
        key = (cx, cz)
        chunk = self._chunk_cache.get(key)
        if chunk is not None:
            return chunk

        try:
            if not self._world.has_chunk(cx, cz, self.dimension):
                self._world.create_chunk(cx, cz, self.dimension)
            chunk = self._world.get_chunk(cx, cz, self.dimension)
        except Exception as exc:
            raise WorldWriteError(
                f"failed creating/loading chunk ({cx}, {cz}): {exc}"
            ) from exc

        self._chunk_cache[key] = chunk
        return chunk

    def set_block(self, placement: Placement) -> None:
        p = placement.pos

        # Java 1.20.4 overworld build limits.
        if not -64 <= p.y <= 319:
            raise WorldWriteError(
                f"placement outside Java 1.20.4 build height at {p}: "
                f"{placement.block.name}"
            )
        if not -29_999_984 <= p.x <= 29_999_983 or not -29_999_984 <= p.z <= 29_999_983:
            raise WorldWriteError(
                f"placement outside safe Java world border at {p}: "
                f"{placement.block.name}"
            )

        cx = p.x // 16
        cz = p.z // 16
        ox = p.x - cx * 16
        oz = p.z - cz * 16

        chunk = self._chunk(cx, cz)
        block_id = self._block_id(placement.block)

        try:
            chunk.blocks[ox, p.y, oz] = block_id
            chunk.changed = True
        except Exception as exc:
            raise WorldWriteError(
                f"failed writing {placement.block.name} at {p}: {exc}"
            ) from exc

    def save(self) -> None:
        if self._closed:
            return
        try:
            self._world.save()
            # Saved chunks can be unloaded to bound RAM use during enormous
            # streaming builds. The next placement recreates/loads as needed.
            self._world.unload()
            self._chunk_cache.clear()
        except Exception as exc:
            raise WorldWriteError(f"world save failed: {exc}") from exc

    def close(self) -> None:
        if self._closed:
            return
        try:
            self._world.close()
        finally:
            self._closed = True
            self._chunk_cache.clear()

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self.save()
        finally:
            self.close()
        return False
