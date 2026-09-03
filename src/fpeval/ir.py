"""Canonical floor-plan IR.

Design decisions (see docs):
  * All geometry in **integer millimetres**. Keeps CP-SAT domains integral and
    eliminates float drift at wall joins.
  * Walls are **centrelines + thickness** (a planar straight-line graph).
  * Openings are **parametric on a host wall**: (wall_id, position 0..1, width).
  * Rooms are **derived faces** of the wall graph, not independent polygons.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Literal, Optional
import math

Kind = Literal["door", "window", "front_door"]


@dataclass(frozen=True)
class P:
    x: int
    y: int
    def as_tuple(self) -> tuple[int, int]: return (self.x, self.y)


@dataclass
class Wall:
    id: str
    start: P
    end: P
    thickness: int          # mm
    height: int = 3000      # mm, storey default

    @property
    def length(self) -> float:
        return math.hypot(self.end.x - self.start.x, self.end.y - self.start.y)


@dataclass
class Opening:
    id: str
    kind: Kind
    wall_id: str
    position: float         # 0..1 along wall centreline
    width: int              # mm
    sill: int = 0           # mm above FFL
    head: int = 2100        # mm above FFL


@dataclass
class Room:
    id: str
    name: str
    category: str
    wall_ids: list[str]
    polygon: list[P]        # mm, closed implicitly
    area: int               # mm^2

    @property
    def area_m2(self) -> float: return self.area / 1_000_000.0


@dataclass
class Site:
    plot_polygon: list[P] = field(default_factory=list)
    north_deg: float = 0.0          # bearing of +Y axis, degrees clockwise from north
    setbacks_mm: dict[str, int] = field(default_factory=dict)


@dataclass
class Plan:
    id: str
    walls: list[Wall] = field(default_factory=list)
    openings: list[Opening] = field(default_factory=list)
    rooms: list[Room] = field(default_factory=list)
    site: Site = field(default_factory=Site)
    storey_height: int = 3000
    provenance: dict = field(default_factory=dict)

    def wall(self, wid: str) -> Optional[Wall]:
        return next((w for w in self.walls if w.id == wid), None)

    def to_dict(self) -> dict: return asdict(self)
