from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def test_locked_machine_capacities_and_display():
    config = tomllib.loads((ROOT / "configs/default.toml").read_text(encoding="utf-8"))

    memory = config["memory"]
    display = config["display"]

    assert memory["logical_ram_bytes"] == 1 << 20
    assert memory["cache_bytes"] == 16 << 10
    assert memory["flash_bytes"] == 4 << 20
    assert memory["flash_bytes"] == 4 * memory["logical_ram_bytes"]
    assert memory["vram_bytes"] == 32 << 10

    assert display["width"] == 320
    assert display["height"] == 180
    assert display["bits_per_pixel"] == 1
    assert display["double_buffer"] is True


def test_canonical_document_matches_locked_spec():
    spec = (ROOT / "docs/INITIAL_SPEC_AND_TRANSCRIPTS.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "**1 MiB main RAM**" in spec
    assert "**16 KiB cache / fast scratch**" in spec
    assert "**4 MiB flash**" in spec
    assert "exactly 4×" in spec
    assert "- **320×180**." in spec
    assert "must not be reduced or changed without explicit owner approval" in spec
    assert "Implementation difficulty alone is not permission to shrink it." in spec

    assert "| Flash | **4 MiB**" in readme
    assert "| Display | **320×180**" in readme
