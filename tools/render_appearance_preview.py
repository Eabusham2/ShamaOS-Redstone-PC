"""Render the real console operating layer and the component color legend.

Run with PYTHONPATH=src python tools/render_appearance_preview.py. This is a
diagram of emitted blocks, not a screenshot or a complete-machine render.
"""
from __future__ import annotations

from html import escape
from pathlib import Path

from shamaos.hardware.appearance import PALETTE, AppearancePlan
from shamaos.hardware.controls import KEYBOARD_OFFSET, control_actuators, iter_controls, keyboard_buttons
from shamaos.model import Vec3


RGB = {
    "white": "#cfd4d5", "orange": "#e46500", "magenta": "#a930a0", "light_blue": "#2398ca",
    "yellow": "#efb91d", "lime": "#60ac19", "pink": "#d6658f", "gray": "#373a3e",
    "light_gray": "#7d7d73", "cyan": "#177d8d", "purple": "#67249f", "blue": "#2c2e8f",
    "brown": "#613b24", "green": "#45611e", "red": "#93211f", "black": "#111217",
}
KEYS = (
    tuple("ABCDEFGH"), tuple("IJKLMNOP"), tuple("QRSTUVWX"), tuple("YZ012345"),
    ("6", "7", "8", "9", "Space", "Enter", "Back", "Tab"), tuple(".,-_/+=:"),
    tuple("[]()!?*;"), ("Up", "Down", "Left", "Right", "Esc", "Del", "Space", "Shift"),
)


def render() -> str:
    origin = Vec3(96, 32, 5000)
    kb = origin.offset(KEYBOARD_OFFSET.x, KEYBOARD_OFFSET.y, KEYBOARD_OFFSET.z)
    plan = AppearancePlan(console_origin=origin)
    blocks = {p.pos: p.block for p in plan.placements(iter_controls(origin))}
    svg = ['<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="820" viewBox="0 0 1280 820">',
           '<rect width="1280" height="820" rx="20" fill="#121822"/>',
           '<style>text{font-family:Arial,sans-serif;fill:#e6edf3}.muted{fill:#a4b5c4}.key{font-size:8px;fill:#10151d;font-weight:bold}</style>']

    def text(x, y, value, size=16, css="", **attrs):
        extra = " ".join(f'{k.replace("_", "-")}="{v}"' for k, v in attrs.items())
        svg.append(f'<text x="{x}" y="{y}" font-size="{size}" class="{css}" {extra}>{escape(value)}</text>')

    def rect(x, y, width, height, color, **attrs):
        extra = " ".join(f'{k.replace("_", "-")}="{v}"' for k, v in attrs.items())
        svg.append(f'<rect x="{x}" y="{y}" width="{width}" height="{height}" fill="{color}" {extra}/>')

    text(40, 52, "ShamaOS component colors", 30, font_weight="bold")
    text(40, 82, "Existing support blocks gain color. Geometry, wiring and operating area stay the same.", 17, "muted")
    rect(30, 110, 615, 625, "#1c2531", rx=14)
    rect(665, 110, 585, 625, "#1c2531", rx=14)
    text(50, 145, "Console operating layer", 22, font_weight="bold")
    text(50, 170, "Actual emitted positions; overhead circuits omitted", 14, "muted")

    tile = 23
    px, pz = 90, 205
    for x in range(17):
        for z in range(-2, 21):
            pos = kb.offset(x, 0, z)
            state = blocks.get(pos)
            color = RGB.get(state.name.removeprefix("minecraft:").removesuffix("_concrete"), "#111822") if state else "#111822"
            rect(px + x * tile, pz + z * tile, tile - 1, tile - 1, color)
    for row, buttons in enumerate(keyboard_buttons(origin)):
        for col, button in enumerate(buttons):
            x, z = button.x - kb.x, button.z - kb.z
            state = blocks[button.offset(dy=-1)]
            color = RGB[state.name.removeprefix("minecraft:").removesuffix("_concrete")]
            rect(px + x * tile, pz + z * tile, tile - 1, tile - 1, color)
            rect(px + x * tile + 5, pz + z * tile + 6, tile - 11, tile - 13, "#939791", rx=2)
            text(px + x * tile + 13, pz + z * tile + 17, KEYS[row][col], 8, "key", text_anchor="middle")
    for index, (name, button) in enumerate(control_actuators(origin).items()):
        backing = button.offset(dz=-1) if index < 8 else button.offset(dx=1)
        state = blocks[backing]
        color = RGB[state.name.removeprefix("minecraft:").removesuffix("_concrete")]
        x, z = backing.x - kb.x, backing.z - kb.z
        rect(px + x * tile, pz + z * tile, tile - 1, tile - 1, color)
        if index < 8:
            text(px + x * tile + 13, pz + z * tile + 17, name, 8, "key", text_anchor="middle")
        else:
            text(px + (x + 1) * tile + 5, pz + z * tile + 17, name, 10)
    text(px+7*tile, pz+20*tile+17, "VIEW", 10, "key")
    text(50, 713, "64 keys + 15 perimeter controls in 16 × 16; walk south to the viewing lip.", 13, "muted")
    text(690, 145, "Component body / alignment accent", 22, font_weight="bold")
    for index, (name, (body, accent)) in enumerate(PALETTE.items()):
        x = 690 + (index // 8) * 278
        y = 185 + (index % 8) * 62
        rect(x, y, 58, 34, RGB[body], rx=4)
        rect(x, y, 5, 34, RGB[accent])
        rect(x, y, 58, 4, RGB[accent])
        text(x + 72, y + 15, name.upper(), 12, font_weight="bold")
        text(x + 72, y + 32, body.replace("_", " ") + " / " + accent.replace("_", " "), 11, "muted")
    text(40, 770, "Preview from generator coordinates • approximate block colors • no decorative walls, extra signs or filled air", 15, "muted")
    text(40, 796, "This operating-layer diagram is not a rendered full computer or evidence of full-machine runtime.", 13, "muted")
    svg.append("</svg>\n")
    return "\n".join(svg)


if __name__ == "__main__":
    destination = Path(__file__).resolve().parents[1] / "docs" / "images" / "component-colors.svg"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(render(), encoding="utf-8")
    print(destination)
