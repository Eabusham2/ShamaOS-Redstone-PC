from __future__ import annotations

from dataclasses import dataclass

from .model import Box, BuildPlan, ComponentPlan, Vec3


@dataclass(frozen=True)
class MachineGeometry:
    origin: Vec3
    ram_bytes: int
    cache_bytes: int
    flash_bytes: int
    display_width: int
    display_height: int


def _box(x: int, y: int, z: int, sx: int, sy: int, sz: int) -> Box:
    return Box(Vec3(x, y, z), Vec3(x + sx - 1, y + sy - 1, z + sz - 1))


def plan_machine(g: MachineGeometry) -> BuildPlan:
    """Create a non-overlapping coarse machine floorplan.

    Detailed redstone component generators refine these boxes; this function is
    intentionally about stable districts/routing reservations.
    """
    ox, oy, oz = g.origin.x, g.origin.y, g.origin.z
    plan = BuildPlan()

    plan.add(ComponentPlan(
        "cpu",
        _box(ox, oy, oz, 256, 96, 256),
        "cpu",
        metadata={"word_bits": 32, "native_sha": True},
    ))
    plan.add(ComponentPlan(
        "cache",
        _box(ox + 320, oy, oz, 192, 80, 192),
        "cache",
        metadata={"logical_bytes": g.cache_bytes},
    ))
    plan.add(ComponentPlan(
        "ram",
        _box(ox + 576, oy, oz, 512, 96, 384),
        "ram",
        metadata={"logical_bytes": g.ram_bytes},
    ))
    plan.add(ComponentPlan(
        "flash",
        _box(ox + 576, oy, oz + 448, 512, 96, 320),
        "flash",
        metadata={"bytes": g.flash_bytes},
    ))
    plan.add(ComponentPlan(
        "gpu",
        _box(ox, oy, oz + 352, 320, 96, 256),
        "gpu",
        metadata={"command_processor": True},
    ))
    plan.add(ComponentPlan(
        "input",
        _box(ox + 352, oy, oz + 352, 160, 64, 160),
        "input",
        metadata={"keyboard": True, "controller": True},
    ))

    # Display is vertical: x = width, y = height, shallow z-depth for tile logic.
    plan.add(ComponentPlan(
        "display",
        _box(ox, oy + 128, oz + 704, g.display_width, g.display_height, 48),
        "display",
        metadata={
            "width": g.display_width,
            "height": g.display_height,
            "bits_per_pixel": 1,
        },
    ))

    return plan
