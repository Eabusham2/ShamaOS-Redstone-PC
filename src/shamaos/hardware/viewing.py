"""Deterministic geometry checks for the upright screen's operator view.

This is a conservative projection/ray model, not an in-client screenshot.
Dust, repeaters and torches count as opaque unit cubes for the ray test.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

from ..layout import MachineGeometry, default_origins
from ..model import Placement
from .controls import control_bounds, control_interaction_descriptor, control_route_access, iter_controls
from .clock import iter_clock, system_clock_origin
from .debug import iter_debug_panel
from .display import LampPanelSpec, panel_pixel
from .external_router import _endpoint_path, _plan_escapes


def operator_obstacles(geometry: MachineGeometry, *, routed: bool = True) -> list[Placement]:
    """Actual console/debug/clock cells and the thirty console export paths.

    The export test uses the production Y314 branch plane. Full synthesized
    computer routing is a separate validation gate; its planner may allocate
    different lanes when all endpoints are assembled together.
    """
    origins = default_origins(geometry)
    controls = list(iter_controls(origins.input))
    obstacles = controls + list(iter_clock(system_clock_origin(origins.input)))
    obstacles += list(iter_debug_panel(origins.input.offset(dz=-224)))
    if routed:
        access = control_route_access(origins.input)
        bounds = control_bounds(origins.input)
        escapes = _plan_escapes(access, 314, obstacles, access=access,
                    minimum_y=geometry.minimum_build_y, endpoint_bounds=dict.fromkeys(access, bounds),
                    obstacle_bounds=(bounds,))
        for pin, escape in escapes.items():
            obstacles.extend(_endpoint_path(pin, escape.escape_z, escape.branch_x, 314, origins.input.z-2000,
                source=True, component="console-export", escape_x=escape.escape_x,
                wall_lead_z=escape.wall_lead_z, branch_direction=escape.branch_direction))
    return obstacles


def screen_visibility(geometry: MachineGeometry, obstacles: list[Placement]) -> dict[str, object]:
    """Ray trace each actual lamp center from the designated standing eye."""
    origins = default_origins(geometry)
    eye = tuple(control_interaction_descriptor(origins.input)["screen_viewing_eye"])
    panel = LampPanelSpec(geometry.display_width, geometry.display_height, 3, 2, "upright")
    # All screen blocks lie on or behind the visible Z plane. They cannot
    # obstruct another pixel before its ray reaches that plane.
    occupied = {(p.pos.x, p.pos.y, p.pos.z) for p in obstacles if p.pos.z < origins.display.z}
    lower = tuple(min(p[axis] for p in occupied) for axis in range(3))
    upper = tuple(max(p[axis] for p in occupied) + 1 for axis in range(3))
    blocked = 0
    for row in range(panel.height):
        for col in range(panel.width):
            lamp = panel_pixel(origins.display, panel, row, col)
            direction = (lamp.x + .5 - eye[0], lamp.y + .5 - eye[1], lamp.z - eye[2])
            steps = tuple(1 if d > 0 else -1 if d < 0 else 0 for d in direction)
            voxel = [math.floor(value) for value in eye]
            delta = [abs(1/d) if d else math.inf for d in direction]
            next_crossing = [((voxel[i] + (steps[i] > 0) - eye[i]) / direction[i])
                             if direction[i] else math.inf for i in range(3)]
            until = min(1., *(max((lower[i]-eye[i])/direction[i], (upper[i]-eye[i])/direction[i])
                             for i in range(3) if direction[i]))
            while True:
                if tuple(voxel) in occupied:
                    blocked += 1
                    break
                axis = min(range(3), key=next_crossing.__getitem__)
                if next_crossing[axis] >= until:
                    break
                voxel[axis] += steps[axis]
                next_crossing[axis] += delta[axis]
    total = panel.width * panel.height
    return {"eye": list(eye), "standing_eye_height": 1.62,
            "visible_pixels": total-blocked, "blocked_pixels": blocked,
            "method": "conservative-unit-cube-voxel-rays-to-every-actual-lamp-center"}


@dataclass(frozen=True)
class OperatorCamera:
    eye: tuple[float, float, float]
    pitch_up: float
    vertical_fov: float = 70.
    width: int = 1920
    height: int = 1080

    def project(self, point) -> tuple[float, float, float] | None:
        dx, dy, dz = (value-origin for value, origin in zip(point, self.eye))
        pitch = math.radians(self.pitch_up)
        depth = math.sin(pitch)*dy + math.cos(pitch)*dz
        if depth <= .01:
            return None
        up = math.cos(pitch)*dy - math.sin(pitch)*dz
        focal = self.height/(2*math.tan(math.radians(self.vertical_fov/2)))
        return self.width/2 + dx*focal/depth, self.height/2 - up*focal/depth, depth


def operator_camera(geometry: MachineGeometry) -> OperatorCamera:
    origins = default_origins(geometry)
    eye = tuple(control_interaction_descriptor(origins.input)["screen_viewing_eye"])
    center_y = origins.display.y + (geometry.display_height-1) + .5
    return OperatorCamera(eye, math.degrees(math.atan2(center_y-eye[1], origins.display.z-eye[2])))


def screen_projection(geometry: MachineGeometry) -> dict[str, object]:
    origins = default_origins(geometry)
    camera = operator_camera(geometry)
    width, height = 3*(geometry.display_width-1)+1, 2*(geometry.display_height-1)+1
    corners = [camera.project((origins.display.x+x, origins.display.y+y, origins.display.z))
               for x in (0, width) for y in (0, height)]
    assert all(point is not None for point in corners)
    xs, ys = [p[0] for p in corners], [p[1] for p in corners]
    inside = min(xs) >= 0 and max(xs) <= camera.width and min(ys) >= 0 and max(ys) <= camera.height
    lamp_sizes = []
    for row in (0, geometry.display_height-1):
        y = origins.display.y+2*(geometry.display_height-1-row)
        a, b = (camera.project((origins.display.x+x, y, origins.display.z)) for x in (0, 1))
        lamp_sizes.append(abs(b[0]-a[0]))
    return {"eye": list(camera.eye), "yaw_degrees": 0., "pitch_degrees": -camera.pitch_up,
            "vertical_fov_degrees": camera.vertical_fov,
            "horizontal_fov_degrees": math.degrees(2*math.atan(camera.width/camera.height*math.tan(math.radians(camera.vertical_fov/2)))),
            "viewport": [camera.width, camera.height], "aspect_ratio": "16:9",
            "screen_inside_viewport": inside, "screen_bounds_px": [min(xs), min(ys), max(xs), max(ys)],
            "lamp_horizontal_width_px_range": [min(lamp_sizes), max(lamp_sizes)],
            "logical_resolution": [geometry.display_width, geometry.display_height], "physical_pitch": [3, 2],
            "scope": "deterministic pinhole projection; in-client legibility and full-machine routing are unverified"}
