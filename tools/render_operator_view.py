"""Project real upright lamp/console geometry from the standing viewing lip.

Requires Pillow only to write the preview. Circuit generation and validation
use the project dependencies. This is not a Minecraft client screenshot.
"""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from shamaos.font5x7 import glyph_rows
from shamaos.generator import geometry_from_config, load_config
from shamaos.hardware.display import LampPanelSpec, panel_pixel
from shamaos.hardware.viewing import operator_camera, operator_obstacles, screen_projection
from shamaos.layout import default_origins


def render(destination: Path) -> dict[str, object]:
    geometry = geometry_from_config(load_config("configs/default.toml"))
    origin = default_origins(geometry).display
    camera = operator_camera(geometry)
    spec = LampPanelSpec(320, 180, 3, 2, "upright")
    image = Image.new("RGB", (camera.width, camera.height), "#a0bacb")
    draw = ImageDraw.Draw(image)
    lit = set()

    def text(x, y, value, scale=1):
        for index, character in enumerate(value):
            for dy, bits in enumerate(glyph_rows(ord(character))):
                for dx in range(5):
                    if bits >> (4-dx) & 1:
                        for sy in range(scale):
                            for sx in range(scale):
                                lit.add((y+dy*scale+sy, x+index*6*scale+dx*scale+sx))

    # A font/layout test pattern. It is explicitly separate from an OS boot
    # claim, and uses the same resident 5x7 glyph source as the GPU.
    for col in range(320):
        lit.add((0, col)); lit.add((179, col))
    for row in range(180):
        lit.add((row, 0)); lit.add((row, 319))
    text(12, 12, "ShamaOS", 2)
    text(12, 38, "320 x 180 independent lamp pixels")
    text(12, 57, "EDITOR   FILES   CALCULATOR   MINER")
    text(12, 76, "ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    text(12, 91, "abcdefghijklmnopqrstuvwxyz")
    text(12, 106, "0123456789 +-*/= :;.,!?[]()")
    text(12, 131, "Font and perspective test pattern")
    text(12, 148, "Actual OS boot is not shown here")
    text(12, 164, "Top row 0                   Row 179")

    def polygon(points, fill, outline=None):
        projected = [camera.project(p) for p in points]
        if any(p is None for p in projected):
            return
        xy = [(p[0], p[1]) for p in projected]
        if max(x for x, _ in xy) < 0 or min(x for x, _ in xy) > camera.width:
            return
        if max(y for _, y in xy) < 0 or min(y for _, y in xy) > camera.height:
            return
        draw.polygon(xy, fill=fill, outline=outline)

    polygon([(origin.x,origin.y,origin.z),(origin.x+958,origin.y,origin.z),
             (origin.x+958,origin.y+359,origin.z),(origin.x,origin.y+359,origin.z)], "#0d1117")
    for row in range(180):
        for col in range(320):
            p = panel_pixel(origin, spec, row, col)
            polygon([(p.x,p.y,p.z),(p.x+1,p.y,p.z),(p.x+1,p.y+1,p.z),(p.x,p.y+1,p.z)],
                    "#ffe5a0" if (row,col) in lit else "#392e25")

    obstacles = {p.pos: p.block.name for p in operator_obstacles(geometry)}
    # Painter ordering for real nearby circuits; each small component is
    # conservatively shown as a cube, matching the visibility check.
    points = []
    for p, name in obstacles.items():
        center = camera.project((p.x+.5,p.y+.5,p.z+.5))
        if center and p.z < origin.z:
            points.append((center[2], p, name))
    for _, p, name in sorted(points, key=lambda item:item[0], reverse=True):
        color = "#a6a9a9" if name.endswith("stone") or name.endswith("target") else "#664c40"
        polygon([(p.x,p.y,p.z),(p.x+1,p.y,p.z),(p.x+1,p.y+1,p.z),(p.x,p.y+1,p.z)],color)
        polygon([(p.x,p.y+1,p.z),(p.x+1,p.y+1,p.z),(p.x+1,p.y+1,p.z+1),(p.x,p.y+1,p.z+1)],"#b8bcbb")

    draw.rectangle((0,0,camera.width,66), fill="#10151d")
    font = ImageFont.truetype("DejaVuSans.ttf", 23)
    draw.text((24,12), "Standing operator view · real block coordinates · 70° vertical FOV · 1920×1080", font=font, fill="#f1f5f9")
    draw.rectangle((0,camera.height-76,camera.width,camera.height), fill="#10151d")
    font = ImageFont.truetype("DejaVuSans.ttf", 19)
    draw.text((24,camera.height-68), "Geometry projection with resident 5×7 font test pattern. This is not an in-client screenshot or a booted-computer result.",font=font,fill="#dbe5ed")
    draw.text((24,camera.height-38), "3×2 physical pitch widens the logical image. Console export paths are included; complete-machine routes still require validation.",font=font,fill="#a9bdcd")
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination)
    report = screen_projection(geometry)
    destination.with_suffix(".json").write_text(json.dumps(report,indent=2)+"\n")
    return report


if __name__ == "__main__":
    path = Path("build/upright-display/operator-view.png")
    print(json.dumps({"image": str(path), "projection": render(path)},indent=2))
