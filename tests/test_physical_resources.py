"""Check symbolic regular-fabric counts against the literal emitters."""
import pytest

from shamaos.hardware.display import LampPanelSpec, iter_lamp_panel
from shamaos.hardware.memory import MemoryBankSpec
from shamaos.hardware.memory_backbone import StackedMemoryTileSpec, iter_stacked_memory_tile
from shamaos.model import Vec3
from shamaos.physical_resources import _control_placement_count, binary_row_resources, panel_resources, tile_control_chunks


@pytest.mark.parametrize("width,height,px,pz", [(3, 2, 5, 5), (8, 17, 5, 4), (7, 9, 7, 5), (17, 3, 17, 19)])
def test_panel_count_and_chunk_formula_matches_actual_blocks(width, height, px, pz):
    origin = Vec3(7, 64, 9)
    spec = LampPanelSpec(width, height, px, pz)
    count, chunks = panel_resources(origin, spec)
    blocks = list(iter_lamp_panel(origin, spec=spec))
    assert count == len(blocks)
    assert chunks == {(block.pos.x // 16, block.pos.z // 16) for block in blocks}


@pytest.mark.parametrize("width,height,x,z", [(1, 1, 0, 0), (8, 4, 7, 9), (7, 9, -17, -17),
                                            (8, 180, 489, 512), (320, 4, 489, 512)])
def test_upright_panel_resources_match_unique_emission(width, height, x, z):
    origin = Vec3(x, -54, z)
    spec = LampPanelSpec(width, height, 3, 2, orientation="upright")
    blocks = list(iter_lamp_panel(origin, spec=spec))
    assert len({p.pos for p in blocks}) == len(blocks)
    count, chunks = panel_resources(origin, spec)
    assert count == len(blocks)
    assert chunks == {(p.pos.x // 16, p.pos.z // 16) for p in blocks}


@pytest.mark.parametrize("words,x,z", [(1, 0, 0), (2, 7, 9), (8, 15, 15), (17, 4, 8), (32, 0, 0)])
@pytest.mark.parametrize("decoder_offset", [-170, -154, -138])
def test_binary_row_profile_matches_literal_decoder(words, x, z, decoder_offset):
    from shamaos.hardware.binary_row_decoder import iter_binary_row_decoder
    origin = Vec3(x, 8, z)
    tile = StackedMemoryTileSpec(MemoryBankSpec(words=words, word_bits=32), 1, 12)
    count, chunks = binary_row_resources(origin, tile, decoder_x=x + decoder_offset)
    blocks = list(iter_binary_row_decoder(origin, tile, decoder_x=x + decoder_offset))
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


@pytest.mark.parametrize("words,tail,layers", [(8, 4, 3), (8, 5, 3), (7, 2, 1), (7, 3, 2), (2, 1, 1)])
def test_partial_bank_control_counts_chunks_and_bounds_match_literal_geometry(words, tail, layers):
    from shamaos.hardware.memory_backbone import stacked_tile_bounds
    origin = Vec3(7, -56, 9)
    tile = StackedMemoryTileSpec(MemoryBankSpec(words=words, word_bits=32), layers, 12, tail)
    blocks = list(iter_stacked_memory_tile(origin, tile=tile, include_storage=False))
    assert _control_placement_count(tile) == len(blocks)
    assert tile_control_chunks(origin, tile) == {(block.pos.x // 16, block.pos.z // 16) for block in blocks}
    bounds = stacked_tile_bounds(origin, tile)
    assert all(bounds.min.x <= block.pos.x <= bounds.max.x and bounds.min.y <= block.pos.y <= bounds.max.y
               and bounds.min.z <= block.pos.z <= bounds.max.z for block in blocks)
