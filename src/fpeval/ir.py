"""Canonical floor-plan IR.

Design decisions (see docs):
  * All geometry in **integer millimetres**. Keeps CP-SAT domains integral and
    eliminates float drift at wall joins.
  * Walls are **centrelines + thickness** (a planar straight-line graph).
  * Openings are **parametric on a host wall**: (wall_id, position 0..1, width).
  * Rooms are **derived faces** of the wall graph, but their *identity* is not
    derived -- see `Room` below.

Two axes are kept apart deliberately:

  **Semantic** state is anything the rules engine, the solver, or the agent has
  to reason about: geometry, opening subtypes and swing, columns, room class.
  It lives on the elements themselves.

  **Presentation** state is the drawing-office layer -- guides, dimension
  strings, notes, entourage, a traced background scan. It affects no rule and
  no solve, so it lives in `Presentation` where the analysis code never has to
  step over it. It is still first-class and still round-trips: dropping a
  user's dimension annotations because they were "only presentation" is data
  loss, not simplification.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Literal, Optional
import math

Kind = Literal["door", "window", "front_door"]

# OpenPlan3D's own vocabularies, mirrored so a Project round-trip is lossless.
DoorType = Literal["single", "double", "sliding", "french", "pocket", "bifold",
                   "opening", "garage"]
WindowType = Literal["standard", "fixed", "casement", "sliding", "bay"]
RoomClass = Literal["indoor", "outdoor", "garage", "utility"]


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
    # -- semantic --
    curve_point: Optional[P] = None   # quadratic bezier control; None = straight
    # -- presentation --
    # "#e5e7eb" is the light poche our renderer and the plan gallery expect for
    # generated walls. Walls drawn in the editor carry OpenPlan3D's own
    # "#444444" and are read back verbatim, so this default only applies to
    # walls constructed in Python (solver output, ResPlan conversion).
    color: str = "#e5e7eb"
    texture: str = ""
    interior_color: str = ""          # "" = inherit `color`
    interior_texture: str = ""
    exterior_color: str = ""
    exterior_texture: str = ""

    @property
    def length(self) -> float:
        return math.hypot(self.end.x - self.start.x, self.end.y - self.start.y)

    @property
    def is_curved(self) -> bool:
        return self.curve_point is not None


@dataclass
class Opening:
    id: str
    kind: Kind
    wall_id: str
    position: float         # 0..1 along wall centreline
    width: int              # mm
    sill: int = 0           # mm above FFL
    head: int = 2100        # mm above FFL
    # -- semantic: a sliding door has no swing, a garage door is not an escape
    # route, and NBC clear-width checks depend on which of these it is.
    subtype: str = ""                 # DoorType or WindowType; "" = default
    swing_direction: Literal["left", "right"] = "left"
    flip_side: bool = False


@dataclass
class Room:
    id: str
    name: str
    category: str
    wall_ids: list[str]
    polygon: list[P]        # mm, closed implicitly
    area: int               # mm^2
    # -- semantic --
    # "" = derive from `category` via roomtypes. Storing the derived value
    # instead would stop IR -> Project -> IR being an identity.
    room_class: str = ""              # OpenPlan3D RoomCategory when explicit
    # A point inside the room, recorded when it was first named. Room identity
    # rides on this, not on `wall_ids`: splitting or adding a wall changes the
    # wall set, and a conversation that says "the master bedroom" cannot have
    # the referent evaporate because a wall was split. Mirrors OpenPlan3D's
    # `Room.anchor` (see vendor/openPlan3D/src/lib/utils/roomIdentity.ts).
    anchor: Optional[P] = None
    # -- presentation --
    floor_texture: str = ""
    color: str = ""
    label_offset: Optional[P] = None  # nudge the label off the centroid

    @property
    def area_m2(self) -> float: return self.area / 1_000_000.0


@dataclass
class Column:
    """A structural column. Semantic, not decorative: it eats clear width and
    a rule that measures circulation has to see it."""
    id: str
    position: P                      # centre, mm
    rotation: float = 0.0            # degrees
    shape: Literal["round", "square"] = "round"
    size: int = 300                  # mm, diameter or side
    height: int = 3000               # mm
    color: str = "#6b7280"


@dataclass
class Stair:
    """A staircase placed inside a room, not a room of its own.

    Matches OpenPlan3D's `Stair` type. Kept out of the room tiling because stair
    polygons trace treads and wreck wall extraction (measured: all-rooms-matched
    90.40% -> 71.60% when treated as a room).
    """
    id: str
    position: P                      # centre, mm
    rotation: float = 0.0            # degrees
    width: int = 1000                # mm, across the flight
    depth: int = 3000                # mm, along the flight
    riser_count: int = 14
    direction: Literal["up", "down"] = "up"
    stair_type: Literal["straight", "l-shaped", "u-shaped", "spiral"] = "straight"
    room_id: Optional[str] = None


@dataclass
class Furniture:
    """A placed furniture item.

    `catalog_id` is an OpenPlan3D catalogue id (189 items in
    vendor/openPlan3D/src/lib/utils/furnitureCatalog.ts) so placements render in
    both our SVG and the editor's 2D and 3D views without a translation table.

    Coordinates are an OUTPUT of the placement solver, never authored by an LLM:
    the model emits symbolic anchors and the solver resolves them against the
    actual room polygon, door swings and clearances.
    """
    id: str
    catalog_id: str
    position: P                      # centre, mm
    rotation: float = 0.0            # degrees, 0 = facing +Y
    width: int = 0                   # mm; 0 = use catalogue default
    depth: int = 0
    height: int = 0
    room_id: Optional[str] = None
    locked: bool = False             # set when a user moves it, so re-solve won't stomp it
    # -- presentation --
    color: str = ""
    material: str = ""
    scale_x: float = 1.0
    scale_y: float = 1.0
    scale_z: float = 1.0


# --------------------------------------------------------------------------
# Presentation layer -- round-trips, but no rule reads it
# --------------------------------------------------------------------------

@dataclass
class GuideLine:
    id: str
    orientation: Literal["horizontal", "vertical"]
    position: int                    # mm, world x (vertical) or y (horizontal)


@dataclass
class Measurement:
    """An ad-hoc ruler the user dropped on the sheet."""
    id: str
    start: P
    end: P


@dataclass
class DimAnnotation:
    """A dimension string with a leader offset. `label` empty = auto-measure."""
    id: str
    start: P
    end: P
    offset: int = 400                # mm perpendicular to the run
    label: str = ""


@dataclass
class TextAnnotation:
    id: str
    position: P
    text: str
    font_size: int = 16              # sheet points, not mm
    color: str = "#1e293b"
    rotation: float = 0.0


@dataclass
class ElementGroup:
    id: str
    element_ids: list[str] = field(default_factory=list)


@dataclass
class EntourageItem:
    """A 2D presentation symbol: a car, a tree, a person for scale."""
    id: str
    def_id: str
    position: P                      # centre, mm
    width: int                       # real-world mm
    rotation: float = 0.0
    opacity: float = 1.0
    locked: bool = False


@dataclass
class EntourageDef:
    """A user-uploaded entourage symbol, carried on the design."""
    id: str
    name: str
    data_url: str
    aspect: float                    # height / width


@dataclass
class BackgroundImage:
    """A scan or sketch the user is tracing over."""
    data_url: str
    position: P = field(default_factory=lambda: P(0, 0))
    scale: float = 1.0
    opacity: float = 0.5
    rotation: float = 0.0
    locked: bool = False


@dataclass
class Presentation:
    guides: list[GuideLine] = field(default_factory=list)
    measurements: list[Measurement] = field(default_factory=list)
    dimensions: list[DimAnnotation] = field(default_factory=list)
    texts: list[TextAnnotation] = field(default_factory=list)
    groups: list[ElementGroup] = field(default_factory=list)
    entourage: list[EntourageItem] = field(default_factory=list)
    background: Optional[BackgroundImage] = None

    def is_empty(self) -> bool:
        return not (self.guides or self.measurements or self.dimensions
                    or self.texts or self.groups or self.entourage
                    or self.background)


@dataclass
class Site:
    plot_polygon: list[P] = field(default_factory=list)
    north_deg: float = 0.0          # bearing of +Y axis, degrees clockwise from north
    setbacks_mm: dict[str, int] = field(default_factory=dict)


@dataclass
class Plan:
    """One storey."""
    id: str
    walls: list[Wall] = field(default_factory=list)
    openings: list[Opening] = field(default_factory=list)
    rooms: list[Room] = field(default_factory=list)
    site: Site = field(default_factory=Site)
    stairs: list[Stair] = field(default_factory=list)
    furniture: list[Furniture] = field(default_factory=list)
    storey_height: int = 3000
    provenance: dict = field(default_factory=dict)
    columns: list[Column] = field(default_factory=list)
    presentation: Presentation = field(default_factory=Presentation)
    name: str = ""
    level: int = 0

    def wall(self, wid: str) -> Optional[Wall]:
        return next((w for w in self.walls if w.id == wid), None)

    def room(self, rid: str) -> Optional[Room]:
        return next((r for r in self.rooms if r.id == rid), None)

    def opening(self, oid: str) -> Optional[Opening]:
        return next((o for o in self.openings if o.id == oid), None)

    def to_dict(self) -> dict: return asdict(self)


@dataclass
class Design:
    """A multi-storey document: what a user actually opens and edits.

    `Plan` stays single-storey so every rule, solver, and renderer written
    against it keeps working unchanged. A `Design` is the container above it,
    and it is what the document service persists.
    """
    id: str
    storeys: list[Plan] = field(default_factory=list)
    active_storey_id: str = ""
    name: str = ""
    description: str = ""
    custom_entourage: list[EntourageDef] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    provenance: dict = field(default_factory=dict)

    @property
    def active(self) -> Optional[Plan]:
        if not self.storeys:
            return None
        return next((s for s in self.storeys if s.id == self.active_storey_id),
                    self.storeys[0])

    @property
    def site(self) -> Site:
        """The site is a property of the plot, not of a storey. The ground
        storey holds the authoritative copy."""
        ground = min(self.storeys, key=lambda s: s.level, default=None)
        return ground.site if ground else Site()

    def storey(self, sid: str) -> Optional[Plan]:
        return next((s for s in self.storeys if s.id == sid), None)

    @classmethod
    def single(cls, plan: Plan, **kw) -> "Design":
        """Wrap a one-storey plan. The common case for solver output."""
        return cls(id=plan.id, storeys=[plan], active_storey_id=plan.id, **kw)

    def to_dict(self) -> dict: return asdict(self)
