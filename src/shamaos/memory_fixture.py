"""A saved, independently reopened physical stacked-memory runtime fixture."""
from pathlib import Path
import json
import subprocess

from .generator import source_provenance, write_manifest, write_placement_stages
from .hardware.memory import MemoryBankSpec
from .hardware.memory_backbone import StackedMemoryTileSpec, iter_stacked_memory_tile, stacked_tile_ports
from .model import Vec3
from .worldio import NativeWorldReader, WorldWriteError


def create_stacked_runtime_fixture_world(
    world_path: str | Path,
    *,
    surface_y: int = -61,
    runtime_runner: str | Path | None = None,
) -> dict[str, object]:
    """Save three complete representative banks, reopen them, then exercise them.

    This fixture has two 32-bit words per bank. It tests the production cell,
    local selection and shared return topology, not the whole machine's
    capacity or timing. MCHPRS drives only external input terminals; every
    simulated circuit block is reconstructed from the saved region files.
    """
    origin = Vec3(256, surface_y + 5, 128)
    bank = MemoryBankSpec(words=2, word_bits=32)
    tile = StackedMemoryTileSpec(bank=bank, banks=3, layer_pitch=12)
    pins = stacked_tile_ports(origin, tile)
    values = [[0x12345678, 0xA55A0FF0], [0x9ABCDEF0, 0x01234567], [0xDEADBEEF, 0x80000001]]
    initial = b"".join(value.to_bytes(4, "little") for words in values for value in words)
    blocks = list(iter_stacked_memory_tile(origin, tile=tile, initial=initial, component="saved-stacked-fixture"))
    if min(block.pos.y for block in blocks) <= surface_y:
        raise WorldWriteError("stacked fixture would overwrite its grass surface")
    count, stages, verification = write_placement_stages(
        world_path, [("stacked-memory-fixture", blocks)], surface_y=surface_y,
        name="ShamaOS Above-Ground Stacked Memory Fixture", spawn=Vec3(120, surface_y + 2, 96),
    )
    reader = NativeWorldReader(world_path)
    # The engine's small TestWorld uses nonnegative array coordinates. A
    # uniform translation preserves the exact saved geometry and states.
    translation_y = max(0, -min(block.pos.y for block in blocks))

    def coords(point: Vec3) -> list[int]:
        return [point.x, point.y + translation_y, point.z]

    inputs = {f"D{bit}": coords(point.offset(-1, 0, -1)) for bit, point in enumerate(pins.write_data)}
    inputs |= {f"ROW{row}": coords(point.offset(-1, 0, -1)) for row, point in enumerate(pins.row_select)}
    inputs |= {f"BANK{index}": coords(point.offset(-1, 0, -1)) for index, point in enumerate(pins.bank_select)}
    inputs |= {"READ": coords(pins.read_enable.offset(-1, 0, -1)), "WRITE": coords(pins.write_enable.offset(-1, 0, -1))}
    outputs = {f"OUT{bit}": coords(point) for bit, point in enumerate(pins.selected_word)}
    outputs |= {f"S{index}:{row}:{bit}": coords(origin.offset(bit * bank.bit_pitch + 2, bank.cell_y + index * tile.layer_pitch, 3 + row * bank.word_pitch))
                for index in range(3) for row in range(2) for bit in range(32)}

    def stored() -> dict[str, bool]:
        return {f"S{index}:{row}:{bit}": bool(value >> bit & 1)
                for index, words in enumerate(values) for row, value in enumerate(words) for bit in range(32)}

    def selected(value: int) -> dict[str, bool]:
        return {f"OUT{bit}": bool(value >> bit & 1) for bit in range(32)}

    steps = [{"ticks": 256, "expect": stored() | selected(0)}]
    for index in range(3):
        for row in range(2):
            steps.extend([
                {"drive": {f"BANK{index}": True, f"ROW{row}": True, "READ": True}, "ticks": 256,
                 "expect": stored() | selected(values[index][row])},
                {"drive": {f"BANK{index}": False, f"ROW{row}": False, "READ": False}, "ticks": 256,
                 "expect": stored() | selected(0)},
            ])
        value = (0xFFFFFFFF, 0, 0xA53C96F0)[index]
        steps.append({"drive": {f"D{bit}": bool(value >> bit & 1) for bit in range(32)},
                      "ticks": 256, "expect": stored(), "continuous": True})
        values[index][1] = value
        steps.extend([
            {"drive": {f"BANK{index}": True, "ROW1": True, "WRITE": True}, "ticks": 256, "expect": stored()},
            {"drive": {"WRITE": False, "READ": True}, "ticks": 256, "expect": stored() | selected(value)},
            {"drive": {f"BANK{index}": False, "ROW1": False, "READ": False}, "ticks": 256,
             "expect": stored() | selected(0)},
        ])
    saved_blocks = []
    for position in sorted({placement.pos for placement in blocks}):
        state = reader.get_block(position)
        saved_blocks.append({"pos": coords(position), "name": state.name, "properties": dict(state.properties)})
    case = {"blocks": saved_blocks, "inputs": inputs, "outputs": outputs, "steps": steps, "initial_ticks": 256}
    case_path = Path(world_path) / "shamaos" / "runtime_case.json"
    write_manifest(case_path, case)
    runtime = {"status": "not-run"}
    if runtime_runner is not None:
        result = subprocess.run([str(runtime_runner)], input=json.dumps(case), capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise WorldWriteError("saved stacked-memory runtime failed: " + result.stdout + result.stderr)
        runtime = json.loads(result.stdout)
        if runtime.get("status") != "passed":
            raise WorldWriteError("saved stacked-memory runtime did not report success")
        if not any(runtime.get("traces", {}).values()):
            runtime.pop("traces", None)
    report = {
        "kind": "saved-stacked-memory-fixture", "complete_shamaos_machine": False,
        "banks": tile.banks, "words_per_bank": bank.words, "word_bits": bank.word_bits,
        "physical_storage_bits": tile.bytes * 8, "surface_y": surface_y,
        "lowest_circuit_y": min(block.pos.y for block in blocks),
        "highest_circuit_y": max(block.pos.y for block in blocks),
        "runtime_coordinate_translation_y": translation_y,
        "written_placements": count, "unique_circuit_blocks": len(saved_blocks), "stage_counts": stages,
        "world_validation": verification, "runtime_validation": runtime,
        "runtime_source": "independently reopened Anvil block states; external terminal test drives",
        "source": source_provenance(),
    }
    write_manifest(Path(world_path) / "shamaos" / "fixture.json", report)
    return report
