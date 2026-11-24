"""Visible title observations for the production upright redstone lamp panel."""
from .font5x7 import glyph_rows
from .hardware.display import LampPanelSpec, panel_pixel
from .model import Vec3
from .worldio import NativeWorldReader, WorldWriteError, validate_position


# shama_kernel_accel emits a 32-character DRAW_TEXT at (4, 4). shama_gpu
# advances six columns for each 5x7 glyph. Keep padding and a one-pixel
# border so a suffix, stale character, smear or shifted frame is observable.
TITLE_X, TITLE_Y = 4, 4
TITLE_COLUMNS, GLYPH_PITCH = 32, 6
CROP_X = range(TITLE_X - 1, TITLE_X + TITLE_COLUMNS * GLYPH_PITCH + 1)
CROP_Y = range(TITLE_Y - 1, TITLE_Y + 7 + 1)
DESKTOP_TITLE = "DESKTOP > EDITOR"  # Desktop selection r8=1.
EDITOR_TITLE = "EDITOR > NEW TEXT"  # Fresh Editor, mode=0, selection=0.
_PANEL = LampPanelSpec(pixel_pitch_x=3, pixel_pitch_y=2, orientation="upright")


def title_pixels(title: str) -> dict[str, bool]:
    """Expected visible lit and dark lamps, in top-to-bottom scan order."""
    if len(title) > TITLE_COLUMNS or any(not 32 <= ord(c) < 127 for c in title):
        raise ValueError("The kernel title must fit its 32 printable ASCII characters.")
    expected = {}
    for y in CROP_Y:
        for x in CROP_X:
            glyph, column = divmod(x - TITLE_X, GLYPH_PITCH)
            row = y - TITLE_Y
            on = (0 <= glyph < len(title) and column < 5 and 0 <= row < 7
                  and bool(glyph_rows(ord(title[glyph]))[row] & (1 << (4 - column))))
            expected[f"title[{x},{y}]"] = on
    return expected


def _saved_title_lamps(world, manifest):
    sections = [section for section in manifest["physical_sections"] if section["name"] == "display"]
    geometry = {"kind": "latched-lamp-panel", "orientation": _PANEL.orientation, "width": _PANEL.width,
                "height": _PANEL.height, "physical_width": _PANEL.physical_width,
                "physical_height": _PANEL.physical_height, "physical_depth": _PANEL.depth}
    if len(sections) != 1 or any(sections[0].get(key) != value for key, value in geometry.items()):
        raise WorldWriteError("Title observations require one production upright 320x180 display section.")
    origin = Vec3(**sections[0]["origin"])
    validate_position(origin)
    reader = NativeWorldReader(world)
    outputs = {}
    for y in CROP_Y:
        for x in CROP_X:
            point = panel_pixel(origin, _PANEL, row=y, col=x)
            lamp = reader.get_block(point)
            if lamp.name != "minecraft:redstone_lamp" or dict(lamp.properties).get("lit") != "false":
                raise WorldWriteError(f"Expected an initially dark saved monitor lamp at pixel {(x, y)}, {point}.")
            outputs[f"title[{x},{y}]"] = [point.x, point.y, point.z]
    return outputs


def add_boot_title_observations(world, manifest, scenario, *, home_step):
    """Extend the initial, boot, fresh Editor and Home samples in place."""
    outputs = _saved_title_lamps(world, manifest)
    if set(map(tuple, outputs.values())).intersection(map(tuple, scenario["outputs"].values())):
        raise WorldWriteError("The visible monitor crop overlaps another declared output.")
    scenario["outputs"].update(outputs)
    for index, title in ((0, ""), (1, DESKTOP_TITLE), (2, EDITOR_TITLE), (home_step, DESKTOP_TITLE)):
        scenario["steps"][index]["expect"].update(title_pixels(title))
