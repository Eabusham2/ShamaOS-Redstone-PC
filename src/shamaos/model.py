from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True, order=True)
class Vec3:
    x: int
    y: int
    z: int

    def offset(self, dx: int = 0, dy: int = 0, dz: int = 0) -> "Vec3":
        return Vec3(self.x + dx, self.y + dy, self.z + dz)


@dataclass(frozen=True)
class Box:
    min: Vec3
    max: Vec3

    def __post_init__(self) -> None:
        if self.min.x > self.max.x or self.min.y > self.max.y or self.min.z > self.max.z:
            raise ValueError("invalid box")

    @property
    def size(self) -> Vec3:
        return Vec3(
            self.max.x - self.min.x + 1,
            self.max.y - self.min.y + 1,
            self.max.z - self.min.z + 1,
        )

    def intersects(self, other: "Box") -> bool:
        return not (
            self.max.x < other.min.x
            or self.min.x > other.max.x
            or self.max.y < other.min.y
            or self.min.y > other.max.y
            or self.max.z < other.min.z
            or self.min.z > other.max.z
        )


@dataclass(frozen=True)
class BlockState:
    name: str
    properties: tuple[tuple[str, str], ...] = ()

    @classmethod
    def of(cls, name: str, **properties: str) -> "BlockState":
        return cls(name, tuple(sorted((k, str(v)) for k, v in properties.items())))


@dataclass(frozen=True)
class Placement:
    pos: Vec3
    block: BlockState
    component: str


@dataclass
class ComponentPlan:
    name: str
    box: Box
    kind: str
    ports: dict[str, tuple[Vec3, int]] = field(default_factory=dict)
    metadata: dict[str, object] = field(default_factory=dict)
    placements: list[Placement] = field(default_factory=list)


@dataclass
class BuildPlan:
    components: list[ComponentPlan] = field(default_factory=list)

    def add(self, component: ComponentPlan) -> None:
        for existing in self.components:
            if component.box.intersects(existing.box):
                raise ValueError(
                    f"component overlap: {component.name} intersects {existing.name}"
                )
        self.components.append(component)

    def placements(self) -> list[Placement]:
        all_blocks: list[Placement] = []
        seen: dict[Vec3, Placement] = {}
        for component in self.components:
            for p in component.placements:
                if p.pos in seen:
                    prev = seen[p.pos]
                    raise ValueError(
                        f"block collision at {p.pos}: {prev.component} vs {p.component}"
                    )
                seen[p.pos] = p
                all_blocks.append(p)
        return sorted(all_blocks, key=lambda p: (p.pos.x, p.pos.y, p.pos.z))

    def manifest(self) -> dict[str, object]:
        return {
            "components": [
                {
                    "name": c.name,
                    "kind": c.kind,
                    "box": {
                        "min": vars(c.box.min),
                        "max": vars(c.box.max),
                        "size": vars(c.box.size),
                    },
                    "ports": {
                        n: {"position": vars(pos), "width": width}
                        for n, (pos, width) in c.ports.items()
                    },
                    "metadata": c.metadata,
                    "placement_count": len(c.placements),
                }
                for c in self.components
            ],
            "placement_count": sum(len(c.placements) for c in self.components),
        }
