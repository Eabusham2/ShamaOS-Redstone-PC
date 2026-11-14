from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def test_locked_machine_capacities_and_display():
    config = tomllib.loads((ROOT / "configs/default.toml").read_text(encoding="utf-8"))

    memory = config["memory"]
    display = config["display"]

    assert memory["logical_ram_bytes"] == 96 << 10
    assert memory["cache_bytes"] == 4 << 10
    assert memory["flash_bytes"] == 200 << 10
    assert memory["logical_ram_bytes"] == 24 * 4096
    assert memory["flash_bytes"] == 50 * 4096
    assert memory["vram_bytes"] == 16 << 10

    assert display["width"] == 320
    assert display["height"] == 180
    assert display["bits_per_pixel"] == 1
    assert display["double_buffer"] is True


def test_canonical_document_matches_locked_spec():
    spec = (ROOT / "docs/INITIAL_SPEC_AND_TRANSCRIPTS.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "**96 KiB main RAM**" in spec
    assert "**4 KiB cache / fast scratch**" in spec
    assert "Owner-authorized cache reduction — 2026-11-14" in spec
    assert "**200 KiB flash**" in spec
    assert "Owner-approved capacity amendment — 2026-11-13" in spec
    assert "95%" in spec
    assert "**16 KiB VRAM**" in spec
    assert "- **320×180**." in spec
    assert "must not be reduced or changed without explicit owner approval" in spec
    assert "Implementation difficulty alone is not permission to shrink it." in spec

    assert "| Flash | **200 KiB**" in readme
    assert "| Display | **320×180**" in readme
