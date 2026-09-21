from shamaos.model import BlockState, Placement, Vec3
from shamaos.worldio import AmuletWorldWriter


class FakeBlocks:
    def __init__(self):
        self.data = {}

    def __setitem__(self, key, value):
        self.data[key] = value


class FakeChunk:
    def __init__(self):
        self.blocks = FakeBlocks()
        self.changed = False


class FakeWorld:
    def __init__(self):
        self.chunks = {}
        self.created = []
        self.saved = 0
        self.unloaded = 0

    def has_chunk(self, cx, cz, dimension):
        return (cx, cz, dimension) in self.chunks

    def create_chunk(self, cx, cz, dimension):
        self.created.append((cx, cz, dimension))
        self.chunks[(cx, cz, dimension)] = FakeChunk()

    def get_chunk(self, cx, cz, dimension):
        return self.chunks[(cx, cz, dimension)]

    def save(self):
        self.saved += 1

    def unload(self):
        self.unloaded += 1

    def close(self):
        pass


def fake_writer():
    writer = object.__new__(AmuletWorldWriter)
    writer.world_path = None
    writer.dimension = "minecraft:overworld"
    writer.version = ("java", (1, 20, 4))
    writer._closed = False
    writer._world = FakeWorld()
    writer._chunk_cache = {}
    writer._block_id_cache = {}
    writer._block_id = lambda _state: 7
    return writer


def test_far_away_missing_chunk_is_created_and_written():
    writer = fake_writer()
    pos = Vec3(500_123, 64, -400_017)
    writer.set_block(
        Placement(pos, BlockState.of("minecraft:redstone_wire"), "test")
    )

    cx, cz = pos.x // 16, pos.z // 16
    assert (cx, cz, "minecraft:overworld") in writer._world.created
    chunk = writer._world.chunks[(cx, cz, "minecraft:overworld")]

    assert chunk.changed
    assert chunk.blocks.data[(pos.x - cx * 16, 64, pos.z - cz * 16)] == 7


def test_save_unloads_chunk_cache_for_streaming_scale():
    writer = fake_writer()
    writer.set_block(
        Placement(Vec3(-500_001, -41, 600_001), BlockState.of("minecraft:stone"), "test")
    )
    assert writer._chunk_cache

    writer.save()
    assert writer._world.saved == 1
    assert writer._world.unloaded == 1
    assert writer._chunk_cache == {}


def test_writer_rejects_invalid_java_build_height():
    writer = fake_writer()
    try:
        writer.set_block(
            Placement(Vec3(0, 320, 0), BlockState.of("minecraft:stone"), "bad")
        )
    except Exception as exc:
        assert "build height" in str(exc)
    else:
        raise AssertionError("writer accepted y=320")
