"""Check symbolic regular-fabric counts against the literal emitters."""
import pytest

from shamaos.hardware.display import LampPanelSpec, iter_lamp_panel
from shamaos.hardware.memory import MemoryBankSpec
from shamaos.hardware.memory_backbone import StackedMemoryTileSpec, iter_stacked_memory_tile
from shamaos.model import Vec3
from shamaos.physical_resources import _control_placement_count, panel_resources, tile_control_chunks


@pytest.mark.parametrize("width,height,px,pz", [(3, 2, 5, 5), (7, 9, 7, 5), (17, 3, 17, 19)])
def test_panel_count_and_chunk_formula_matches_actual_blocks(width, height, px, pz):
    origin = Vec3(7, 64, 9)
    spec = LampPanelSpec(width, height, px, pz)
    count, chunks = panel_resources(origin, spec)
    blocks = list(iter_lamp_panel(origin, spec=spec))
    assert count == len(blocks)
    assert chunks == {(block.pos.x // 16, block.pos.z // 16) for block in blocks}


@pytest.mark.parametrize("words,bits,layers,pitch", [(1, 8, 1, 12), (7, 8, 4, 12), (8, 8, 3, 16), (3, 32, 30, 12)])
def test_stacked_decoder_and_return_counts_match_actual_geometry(words, bits, layers, pitch):
    origin = Vec3(7, -60, 9)
    tile = StackedMemoryTileSpec(MemoryBankSpec(words=words, word_bits=bits), layers, pitch)
    count = _control_placement_count(tile)
    chunks = tile_control_chunks(origin, tile)
    blocks = list(iter_stacked_memory_tile(origin, tile=tile, include_storage=False))
    assert count == len(blocks)
    assert chunks == {(block.pos.x // 16, block.pos.z // 16) for block in blocks}
