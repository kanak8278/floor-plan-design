"""The command vocabulary: one algebra for the mouse and for the model.

Every change to a design -- a drag, a rename, an agent's repair, a solver's
output -- is a `Command` appended to one ordered log. There is exactly one
applier (`fpeval.apply`), so "the server thinks X, the client thinks Y" is not
a state the system can reach. The log is also, for free, the thing the chat
pane shows and the thing the model reads as context.

## Two families, and why the split is load-bearing

`symbolic` commands name *intent* and never carry a coordinate: "add a door at
0.6 along wall w7", "move w4 north by 150 mm", "the master bedroom should be
150-190 sqft". These are what an LLM may author, and `llm.py` already refuses
absolute coordinates in their params (`_BANNED_PARAM_KEYS`). DECISIONS.md #6 --
the model emits specs and symbolic patches, solvers emit coordinates -- is
enforced here rather than hoped for.

`direct` commands carry coordinates: "this wall endpoint is now at (4210,
3000)". A mouse produces these constantly and there is nothing wrong with that.
They are simply not authorable by an agent, which is checked in `validate()`
against `source`.

Encoding the distinction as two families rather than as a rule about who calls
what means the check survives refactoring.

## Determinism

A command carries the ids of anything it creates (`wall_id` on `add_wall`, not
a return value). Nothing in a command reads a clock or a random number
generator. Replaying the log from an empty document therefore reproduces the
document exactly, which is what makes event sourcing a real mechanism here
rather than an aspiration: undo, time travel, and audit all fall out of it.

## Summaries

Each command renders its own one-line summary from the before and after
document -- "Middle bedroom 3.6 x 3.9 m", "Dining table added". The summary is
never written at the call site. Sixty call sites writing their own would drift
within a week, and the summary is not decoration: it is what the user reads in
the chat and what the model reads next turn.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Optional, Sequence

from .ir import Design, Plan
from . import roomtypes as _rt

Family = Literal["symbolic", "direct"]
Source = Literal["user", "agent", "solver", "import"]

SYMBOLIC = "symbolic"
DIRECT = "direct"

SOURCES = ("user", "agent", "solver", "import")

# Vocabularies mirrored from OpenPlan3D so a command can be validated without
# importing the editor. `llm.py` has its own copies for the patch schema; these
# are the ones the applier enforces.
DOOR_TYPES = ("single", "double", "sliding", "french", "pocket", "bifold",
              "opening", "garage")
WINDOW_TYPES = ("standard", "fixed", "casement", "sliding", "bay")
ROOM_CLASSES = ("indoor", "outdoor", "garage", "utility")
COLUMN_SHAPES = ("round", "square")
STAIR_TYPES = ("straight", "l-shaped", "u-shaped", "spiral")
ORIENTATIONS = ("horizontal", "vertical")
ENDPOINTS = ("start", "end")
COMPASS = ("north", "north_east", "east", "south_east",
           "south", "south_west", "west", "north_west")
POSITION_WORDS = ("start", "quarter", "centre", "three_quarter", "end")

# A direct command may carry coordinates; a symbolic one may not. These are the
# param names that count as geometry, checked against the family rather than
# against a convention about who calls what.
COORDINATE_KEYS = frozenset({
    "x", "y", "x1", "y1", "x2", "y2", "position", "start", "end", "dx", "dy",
    "point", "points", "polygon", "anchor", "centre", "center", "offset_x",
    "offset_y",
})


# --------------------------------------------------------------------------
# the envelope
# --------------------------------------------------------------------------

@dataclass
class Command:
    """One unit of change. Immutable once logged."""
    op: str
    params: dict = field(default_factory=dict)
    source: Source = "user"
    id: str = ""
    storey_id: str = ""            # "" = the active storey
    # Set by the agent path; carried so the diff card can cite them.
    description: str = ""
    finding_ids: list[str] = field(default_factory=list)
    confidence: float = 1.0
    rationale: str = ""
    # Set by the log, not by the author.
    seq: int = 0

    def __post_init__(self) -> None:
        if not self.id:
            self.id = f"c-{uuid.uuid4().hex[:12]}"

    @property
    def spec(self) -> Optional["CommandSpec"]:
        return TABLE.get(self.op)

    @property
    def family(self) -> str:
        s = self.spec
        return s.family if s else ""

    def to_dict(self) -> dict:
        return {
            "op": self.op, "params": dict(self.params), "source": self.source,
            "id": self.id, "storey_id": self.storey_id, "seq": self.seq,
            "description": self.description,
            "finding_ids": list(self.finding_ids),
            "confidence": self.confidence, "rationale": self.rationale,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Command":
        d = dict(d or {})
        return cls(
            op=str(d.get("op", "")),
            params=dict(d.get("params") or {}),
            source=d.get("source") or "user",
            id=str(d.get("id") or ""),
            storey_id=str(d.get("storey_id") or ""),
            description=str(d.get("description") or ""),
            finding_ids=list(d.get("finding_ids") or []),
            confidence=float(d.get("confidence", 1.0) or 0.0),
            rationale=str(d.get("rationale") or ""),
            seq=int(d.get("seq", 0) or 0),
        )

    def validate(self, design: Optional[Design] = None) -> list[str]:
        """Everything checkable before the applier runs."""
        spec = self.spec
        if spec is None:
            return [f"unknown command {self.op!r}"]
        errs: list[str] = []
        if self.source not in SOURCES:
            errs.append(f"{self.op}: unknown source {self.source!r}")
        if not isinstance(self.params, dict):
            return [f"{self.op}: params must be an object"]

        # The rule from DECISIONS.md #6, enforced rather than trusted.
        if spec.family == SYMBOLIC:
            for k in self.params:
                if k in COORDINATE_KEYS:
                    errs.append(
                        f"{self.op}: {k!r} is a coordinate, and {self.op} is a "
                        "symbolic command -- geometry is the solver's output, "
                        "never an author's input")
        if spec.family == DIRECT and self.source == "agent":
            errs.append(
                f"{self.op}: direct commands carry coordinates, so an agent "
                "may not author one; use a symbolic command instead")

        allowed = set(spec.required) | set(spec.optional)
        for k in spec.required:
            if self.params.get(k) is None:
                errs.append(f"{self.op}: missing required param {k!r}")
        for k in self.params:
            if k not in allowed:
                errs.append(f"{self.op}: unexpected param {k!r} "
                            f"(allowed: {sorted(allowed)})")
        errs += _check_enums(self.op, self.params)
        if design is not None:
            errs += _check_refs(self, design)
        return errs


@dataclass
class Event:
    """What the user sees and what the model reads next turn."""
    seq: int
    command_id: str
    op: str
    source: str
    summary: str
    refs: list[str] = field(default_factory=list)   # touched element ids
    at: str = ""                                    # set by the log

    def to_dict(self) -> dict:
        return {"seq": self.seq, "command_id": self.command_id, "op": self.op,
                "source": self.source, "summary": self.summary,
                "refs": list(self.refs), "at": self.at}

    def bullet(self) -> str:
        who = {"user": "you", "agent": "assistant", "solver": "solver",
               "import": "import"}.get(self.source, self.source)
        return f"- {self.summary} ({who})"


# --------------------------------------------------------------------------
# the table
# --------------------------------------------------------------------------

SummaryFn = Callable[[dict, Optional[Design], Optional[Design]], str]


@dataclass(frozen=True)
class CommandSpec:
    op: str
    family: str
    required: tuple[str, ...]
    optional: tuple[str, ...]
    doc: str
    summary: SummaryFn
    # Which store function the optimistic client uses. `None` means the client
    # has no local shortcut and must wait for the server's projection.
    editor: Optional[str] = None


TABLE: dict[str, CommandSpec] = {}


def _add(spec: CommandSpec) -> None:
    if spec.op in TABLE:
        raise ValueError(f"duplicate command {spec.op!r}")
    TABLE[spec.op] = spec


# ---- summary helpers ------------------------------------------------------

def _mm_ft(mm: float) -> str:
    """Feet and inches, which is what Indian plans are quoted in."""
    inches = mm / 25.4
    ft = int(inches // 12)
    rem = int(round(inches - ft * 12))
    if rem == 12:
        ft, rem = ft + 1, 0
    return f"{ft}'{rem}\"" if rem else f"{ft}'"


def _m(mm: float) -> str:
    return f"{mm / 1000.0:.2f} m"


def _storey(design: Optional[Design], storey_id: str = "") -> Optional[Plan]:
    if design is None:
        return None
    if storey_id:
        return design.storey(storey_id)
    return design.active


def _room_label(design: Optional[Design], rid: Any) -> str:
    """What to call a room in a sentence the user reads.

    A freshly detected face has no name and the placeholder category
    "indoor", which is not a thing anyone calls a room -- "indoor renamed to
    Master Bedroom" reads like a bug even though nothing is wrong.
    """
    st = _storey(design)
    if st is None or not isinstance(rid, str):
        return str(rid)
    r = st.room(rid)
    if r is None:
        return rid
    if r.name:
        return r.name
    t = _rt.get(r.category)
    if t is not None and r.category not in ("indoor", "outdoor", "utility",
                                            "garage"):
        return t.display
    return "Unnamed room"


def _room_dims(design: Optional[Design], rid: Any) -> str:
    """`3.60 x 3.90 m` from the room's own bounding box."""
    st = _storey(design)
    if st is None or not isinstance(rid, str):
        return ""
    r = st.room(rid)
    if r is None or len(r.polygon) < 3:
        return ""
    xs = [p.x for p in r.polygon]
    ys = [p.y for p in r.polygon]
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    return f"{w / 1000.0:.2f} x {h / 1000.0:.2f} m"


def _catalog_label(cid: Any) -> str:
    """"Dining Table", not "dining_table" -- the user reads these."""
    try:
        from . import catalog
        if catalog.has(str(cid)):
            return catalog.get(str(cid)).name
    except Exception:
        # The catalogue is parsed out of the vendor TypeScript and cached, so
        # it can be unavailable. A worse label is better than a lost edit.
        pass
    return str(cid).replace("_", " ").title()


# Most commands need only a fixed phrase; these keep the table readable.
def _fixed(text: str) -> SummaryFn:
    return lambda p, before, after: text


def _wall_summary(verb: str) -> SummaryFn:
    return lambda p, before, after: f"{verb} wall {p.get('wall_id', '?')}"


# ---- spec level: change the brief, then re-solve ---------------------------
# These mirror `llm.py`'s spec ops. They are listed here so one table answers
# "what can change this document", but they act on the DesignSpec and are
# applied by `PatchBatch.apply_to_spec`, not by the geometry applier.

_SPEC_OPS = {
    "set_room_area": (("room_id", "min_sqft", "max_sqft"), (),
                      "Widen or shift a room's target area range, then re-solve."),
    "set_room_aspect": (("room_id", "max_aspect"), ("min_aspect",),
                        "Relax or tighten a room's long/short ratio limit."),
    "set_room_zone": (("room_id", "preferred_zone"), (),
                      "Move a room's preferred compass zone."),
    "set_room_priority": (("room_id", "priority"), (),
                          "Change how hard the solver fights for this room."),
    "add_room": (("room_id", "category"),
                 ("name", "min_sqft", "max_sqft", "max_aspect", "priority",
                  "optional", "attached_bath", "preferred_zone", "storey"),
                 "Add a programme entry, then re-solve."),
    "remove_room": (("room_id",), (), "Drop a programme entry, then re-solve."),
    "set_adjacency": (("a", "b", "kind"), ("relation", "reason"),
                      "Add or overwrite an adjacency requirement."),
    "remove_adjacency": (("a", "b"), ("relation",),
                         "Drop an adjacency constraint."),
    "set_entrance": ((), ("side", "zone", "via_foyer",
                          "avoid_direct_kitchen_view"),
                     "Change where the front door is, then re-solve."),
    "set_wet_grouping": (("value",), (), "Change wet-room grouping preference."),
    "set_storeys": (("value",), (), "Change the storey count, then re-solve."),
}

for _op, (_req, _opt, _doc) in _SPEC_OPS.items():
    _add(CommandSpec(
        op=_op, family=SYMBOLIC, required=_req, optional=_opt, doc=_doc,
        summary=(lambda op: lambda p, b, a: _spec_summary(op, p, b, a))(_op),
    ))


def _spec_summary(op: str, p: dict, before, after) -> str:
    if op == "set_room_area":
        return (f"{_room_label(before, p.get('room_id'))} target area "
                f"{p.get('min_sqft')}-{p.get('max_sqft')} sqft")
    if op == "set_room_aspect":
        return (f"{_room_label(before, p.get('room_id'))} max proportion "
                f"{p.get('max_aspect')}")
    if op == "set_room_zone":
        return (f"{_room_label(before, p.get('room_id'))} moved to the "
                f"{p.get('preferred_zone')} zone")
    if op == "set_room_priority":
        return (f"{_room_label(before, p.get('room_id'))} priority "
                f"{p.get('priority')}")
    if op == "add_room":
        t = _rt.get(str(p.get("category")))
        return f"{p.get('name') or (t.display if t else p.get('category'))} added to the brief"
    if op == "remove_room":
        return f"{_room_label(before, p.get('room_id'))} removed from the brief"
    if op == "set_adjacency":
        return (f"{_room_label(before, p.get('a'))} must be "
                f"{p.get('relation', 'adjacent')} to "
                f"{_room_label(before, p.get('b'))}")
    if op == "remove_adjacency":
        return (f"{_room_label(before, p.get('a'))}/"
                f"{_room_label(before, p.get('b'))} adjacency dropped")
    if op == "set_entrance":
        bits = [f"{k} {v}" for k, v in p.items() if v is not None]
        return "Entrance: " + (", ".join(bits) or "unchanged")
    if op == "set_wet_grouping":
        return f"Wet rooms grouped: {p.get('value')}"
    if op == "set_storeys":
        return f"{p.get('value')} storeys"
    return op


# ---- geometry: walls ------------------------------------------------------

_add(CommandSpec(
    op="add_wall", family=DIRECT,
    required=("wall_id", "start", "end"),
    optional=("thickness_mm", "height_mm"),
    doc="Draw a wall between two points. The id is supplied so replay is "
        "deterministic.",
    editor="addWall",
    summary=lambda p, b, a: f"Wall drawn, {_m(_seg_len(p))} long",
))

_add(CommandSpec(
    op="add_wall_between", family=SYMBOLIC,
    required=("wall_id", "start_ref", "end_ref"),
    optional=("thickness_mm", "height_mm"),
    doc="Draw a wall between two symbolic references ('w7:start', 'w7@0.5'). "
        "The applier resolves them, so no coordinate is authored.",
    editor="addWall",
    summary=lambda p, b, a: f"Wall added from {p.get('start_ref')} to {p.get('end_ref')}",
))

_add(CommandSpec(
    op="move_wall_endpoint", family=DIRECT,
    required=("wall_id", "endpoint", "position"),
    optional=(),
    doc="Drag one end of a wall. Emitted once on release, not per frame.",
    editor="moveWallEndpoint",
    summary=lambda p, b, a: f"Wall {p.get('wall_id')} reshaped",
))

_add(CommandSpec(
    op="move_wall_by", family=DIRECT,
    required=("wall_id", "dx", "dy"),
    optional=(),
    doc="Slide a whole wall by a vector.",
    editor="moveWallParallel",
    summary=lambda p, b, a: f"Wall {p.get('wall_id')} moved",
))

_add(CommandSpec(
    op="move_wall_parallel", family=SYMBOLIC,
    required=("wall_id", "direction", "distance_mm"),
    optional=(),
    doc="Slide a wall a stated distance in a compass direction. The applier "
        "resolves the bearing against Site.north_deg.",
    editor="moveWallParallel",
    summary=lambda p, b, a: (f"Wall {p.get('wall_id')} moved "
                             f"{p.get('distance_mm')} mm {p.get('direction')}"),
))

_add(CommandSpec(
    op="split_wall", family=SYMBOLIC,
    required=("wall_id", "at", "new_wall_id"),
    optional=(),
    doc="Split a wall at a parametric position. `at` is 'midpoint' or 0..1.",
    editor="splitWall",
    summary=lambda p, b, a: f"Wall {p.get('wall_id')} split",
))

_add(CommandSpec(
    op="update_wall", family=SYMBOLIC,
    required=("wall_id",),
    optional=("thickness_mm", "height_mm", "color", "texture",
              "interior_color", "interior_texture", "exterior_color",
              "exterior_texture"),
    doc="Change a wall's thickness, height, or finishes.",
    editor="updateWall",
    summary=lambda p, b, a: _update_wall_summary(p),
))

_add(CommandSpec(
    op="duplicate_wall", family=SYMBOLIC,
    required=("wall_id", "new_wall_id"), optional=(),
    doc="Copy a wall, offset by the editor's default nudge.",
    editor="duplicateWall",
    summary=_wall_summary("Duplicated"),
))


def _seg_len(p: dict) -> float:
    s, e = p.get("start") or {}, p.get("end") or {}
    dx = float(e.get("x", 0)) - float(s.get("x", 0))
    dy = float(e.get("y", 0)) - float(s.get("y", 0))
    return (dx * dx + dy * dy) ** 0.5


def _update_wall_summary(p: dict) -> str:
    wid = p.get("wall_id", "?")
    if p.get("thickness_mm") is not None:
        return f"Wall {wid} now {p['thickness_mm']} mm thick"
    if p.get("height_mm") is not None:
        return f"Wall {wid} now {_m(p['height_mm'])} tall"
    return f"Wall {wid} finish changed"


# ---- geometry: openings ---------------------------------------------------

_add(CommandSpec(
    op="add_door", family=SYMBOLIC,
    required=("opening_id", "wall_id", "at"),
    optional=("door_type", "width_mm", "kind"),
    doc="Add a door to a wall. `at` is a fraction 0..1 or a position word.",
    editor="addDoor",
    summary=lambda p, b, a: (f"{(p.get('door_type') or 'single').capitalize()} "
                             f"door added to wall {p.get('wall_id')}"),
))

_add(CommandSpec(
    op="add_window", family=SYMBOLIC,
    required=("opening_id", "wall_id", "at"),
    optional=("window_type", "width_mm", "sill_mm", "head_mm"),
    doc="Add a window to a wall.",
    editor="addWindow",
    summary=lambda p, b, a: (f"{(p.get('window_type') or 'standard').capitalize()} "
                             f"window added to wall {p.get('wall_id')}"),
))

_add(CommandSpec(
    op="update_opening", family=SYMBOLIC,
    required=("opening_id",),
    optional=("width_mm", "door_type", "window_type", "swing_direction",
              "flip_side", "at", "sill_mm", "head_mm"),
    doc="Change an opening's width, type, swing, or position on its wall.",
    editor="updateDoor",
    summary=lambda p, b, a: _update_opening_summary(p),
))

_add(CommandSpec(
    op="duplicate_opening", family=SYMBOLIC,
    required=("opening_id", "new_opening_id"), optional=(),
    doc="Copy an opening along its host wall.",
    editor="duplicateDoor",
    summary=lambda p, b, a: "Opening duplicated",
))


def _update_opening_summary(p: dict) -> str:
    oid = p.get("opening_id", "?")
    if p.get("width_mm") is not None:
        return f"Opening {oid} widened to {p['width_mm']} mm ({_mm_ft(p['width_mm'])})"
    if p.get("swing_direction") is not None or p.get("flip_side") is not None:
        return f"Opening {oid} swing changed"
    if p.get("at") is not None:
        return f"Opening {oid} moved along its wall"
    return f"Opening {oid} changed"


# ---- geometry: rooms ------------------------------------------------------

_add(CommandSpec(
    op="update_room", family=SYMBOLIC,
    required=("room_id",),
    optional=("name", "category", "room_class", "floor_texture", "color"),
    doc="Relabel or restyle a room. Never geometry -- a room is a face of the "
        "wall graph, so it changes when walls do.",
    editor="updateRoom",
    summary=lambda p, b, a: _update_room_summary(p, b, a),
))

_add(CommandSpec(
    op="move_room_label", family=DIRECT,
    required=("room_id", "offset_x", "offset_y"), optional=(),
    doc="Nudge a room's label off its centroid.",
    editor="updateRoom",
    summary=lambda p, b, a: f"{_room_label(b, p.get('room_id'))} label moved",
))


def _update_room_summary(p: dict, before, after) -> str:
    old = _room_label(before, p.get("room_id"))
    if p.get("name"):
        dims = _room_dims(after, p.get("room_id"))
        return f"{old} renamed to {p['name']}" + (f", {dims}" if dims else "")
    if p.get("category"):
        t = _rt.get(str(p["category"]))
        return f"{old} is now a {t.display if t else p['category']}"
    return f"{old} restyled"


# ---- objects: furniture, stairs, columns ----------------------------------

_add(CommandSpec(
    op="add_furniture", family=DIRECT,
    required=("furniture_id", "catalog_id", "position"),
    optional=("rotation", "width_mm", "depth_mm", "height_mm", "room_id"),
    doc="Place a catalogue item at a point.",
    editor="addFurniture",
    summary=lambda p, b, a: f"{_catalog_label(p.get('catalog_id'))} added",
))

_add(CommandSpec(
    op="place_furniture_in_room", family=SYMBOLIC,
    required=("furniture_id", "catalog_id", "room_id"),
    optional=("against", "facing", "beside"),
    doc="Ask for an item in a room with a relational anchor ('against the "
        "north wall'). The placement solver resolves coordinates.",
    editor=None,
    summary=lambda p, b, a: (f"{_catalog_label(p.get('catalog_id'))} added to "
                             f"{_room_label(a, p.get('room_id'))}"),
))

_add(CommandSpec(
    op="move_furniture", family=DIRECT,
    required=("furniture_id", "position"), optional=(),
    doc="Drag an item. Emitted on release.",
    editor="moveFurniture",
    summary=lambda p, b, a: f"{_furniture_label(b, p.get('furniture_id'))} moved",
))

_add(CommandSpec(
    op="update_furniture", family=SYMBOLIC,
    required=("furniture_id",),
    optional=("rotation", "width_mm", "depth_mm", "height_mm", "color",
              "material", "locked", "scale_x", "scale_y", "scale_z", "room_id"),
    doc="Rotate, resize, restyle, or lock an item.",
    editor="updateFurniture",
    summary=lambda p, b, a: _update_furniture_summary(p, b),
))

_add(CommandSpec(
    op="duplicate_furniture", family=SYMBOLIC,
    required=("furniture_id", "new_furniture_id"), optional=(),
    doc="Copy an item, offset by the editor's default nudge.",
    editor="duplicateFurniture",
    summary=lambda p, b, a: f"{_furniture_label(b, p.get('furniture_id'))} duplicated",
))

_add(CommandSpec(
    op="add_stair", family=DIRECT,
    required=("stair_id", "position"),
    optional=("rotation", "width_mm", "depth_mm", "riser_count", "direction",
              "stair_type", "room_id"),
    doc="Place a staircase. A stair is an object inside a room, not a room.",
    editor="addStair",
    summary=lambda p, b, a: "Staircase added",
))

_add(CommandSpec(
    op="update_stair", family=SYMBOLIC,
    required=("stair_id",),
    optional=("rotation", "width_mm", "depth_mm", "riser_count", "direction",
              "stair_type", "room_id"),
    doc="Change a staircase's geometry or type.",
    editor="updateStair",
    summary=lambda p, b, a: "Staircase changed",
))

_add(CommandSpec(
    op="move_stair", family=DIRECT,
    required=("stair_id", "position"), optional=(),
    doc="Drag a staircase.",
    editor="moveStair",
    summary=lambda p, b, a: "Staircase moved",
))

_add(CommandSpec(
    op="add_column", family=DIRECT,
    required=("column_id", "position"),
    optional=("shape", "size_mm", "height_mm", "rotation"),
    doc="Place a structural column.",
    editor="addColumn",
    summary=lambda p, b, a: f"{(p.get('shape') or 'round').capitalize()} column added",
))

_add(CommandSpec(
    op="update_column", family=SYMBOLIC,
    required=("column_id",),
    optional=("shape", "size_mm", "height_mm", "rotation", "color"),
    doc="Change a column's shape or size.",
    editor="updateColumn",
    summary=lambda p, b, a: "Column changed",
))

_add(CommandSpec(
    op="move_column", family=DIRECT,
    required=("column_id", "position"), optional=(),
    doc="Drag a column.",
    editor="moveColumn",
    summary=lambda p, b, a: "Column moved",
))


def _furniture_label(design: Optional[Design], fid: Any) -> str:
    st = _storey(design)
    if st is None or not isinstance(fid, str):
        return "Item"
    f = next((x for x in st.furniture if x.id == fid), None)
    return _catalog_label(f.catalog_id) if f else "Item"


def _update_furniture_summary(p: dict, before) -> str:
    label = _furniture_label(before, p.get("furniture_id"))
    if p.get("locked") is not None:
        return f"{label} {'locked' if p['locked'] else 'unlocked'}"
    if p.get("rotation") is not None:
        return f"{label} rotated to {int(float(p['rotation']))}°"
    if any(p.get(k) is not None for k in ("width_mm", "depth_mm", "height_mm")):
        return f"{label} resized"
    return f"{label} changed"


# ---- deletion -------------------------------------------------------------

_add(CommandSpec(
    op="remove_element", family=SYMBOLIC,
    required=("element_id",), optional=(),
    doc="Delete anything by id. Removing a wall cascades its openings.",
    editor="removeElement",
    summary=lambda p, b, a: _remove_summary(p, b),
))


def _remove_summary(p: dict, before) -> str:
    eid = str(p.get("element_id", "?"))
    st = _storey(before)
    if st is not None:
        if st.wall(eid):
            return f"Wall {eid} deleted"
        if st.room(eid):
            return f"{_room_label(before, eid)} deleted"
        if st.opening(eid):
            return f"Opening {eid} deleted"
        if any(f.id == eid for f in st.furniture):
            return f"{_furniture_label(before, eid)} removed"
        if any(s.id == eid for s in st.stairs):
            return "Staircase removed"
        if any(c.id == eid for c in st.columns):
            return "Column removed"
    return f"{eid} deleted"


# ---- presentation ---------------------------------------------------------

_add(CommandSpec(
    op="add_guide", family=DIRECT,
    required=("guide_id", "orientation", "position"), optional=(),
    doc="Drop a guide line.",
    editor="addGuide",
    summary=lambda p, b, a: f"{str(p.get('orientation','')).capitalize()} guide added",
))

_add(CommandSpec(
    op="move_guide", family=DIRECT,
    required=("guide_id", "position"), optional=(),
    doc="Slide a guide line.",
    editor="moveGuide",
    summary=lambda p, b, a: "Guide moved",
))

_add(CommandSpec(
    op="add_measurement", family=DIRECT,
    required=("measurement_id", "start", "end"), optional=(),
    doc="Drop an ad-hoc ruler.",
    editor="addMeasurement",
    summary=lambda p, b, a: f"Measured {_m(_seg_len(p))}",
))

_add(CommandSpec(
    op="add_dimension", family=DIRECT,
    required=("dimension_id", "start", "end"), optional=("offset", "label"),
    doc="Add a dimension string to the sheet.",
    editor="addAnnotation",
    summary=lambda p, b, a: f"Dimension added, {_m(_seg_len(p))}",
))

_add(CommandSpec(
    op="update_dimension", family=SYMBOLIC,
    required=("dimension_id",), optional=("label",),
    doc="Retext a dimension string.",
    editor="updateAnnotation",
    summary=lambda p, b, a: "Dimension relabelled",
))

_add(CommandSpec(
    op="add_text", family=DIRECT,
    required=("text_id", "position", "text"),
    optional=("font_size", "color", "rotation"),
    doc="Add a note to the sheet.",
    editor="addTextAnnotation",
    summary=lambda p, b, a: f"Note added: {str(p.get('text',''))[:40]}",
))

_add(CommandSpec(
    op="update_text", family=SYMBOLIC,
    required=("text_id",), optional=("text", "font_size", "color", "rotation"),
    doc="Edit a note.",
    editor="updateTextAnnotation",
    summary=lambda p, b, a: "Note edited",
))

_add(CommandSpec(
    op="move_text", family=DIRECT,
    required=("text_id", "position"), optional=(),
    doc="Drag a note.",
    editor="moveTextAnnotation",
    summary=lambda p, b, a: "Note moved",
))

_add(CommandSpec(
    op="add_entourage", family=DIRECT,
    required=("entourage_id", "def_id", "position", "width_mm"),
    optional=("rotation", "opacity"),
    doc="Place a presentation symbol (car, tree, figure).",
    editor="addEntourageItem",
    summary=lambda p, b, a: f"{str(p.get('def_id','symbol')).replace('-', ' ')} placed",
))

_add(CommandSpec(
    op="update_entourage", family=SYMBOLIC,
    required=("entourage_id",),
    optional=("width_mm", "rotation", "opacity", "locked"),
    doc="Resize or rotate a presentation symbol.",
    editor="updateEntourageItem",
    summary=lambda p, b, a: "Symbol changed",
))

_add(CommandSpec(
    op="move_entourage", family=DIRECT,
    required=("entourage_id", "position"), optional=(),
    doc="Drag a presentation symbol.",
    editor="moveEntourage",
    summary=lambda p, b, a: "Symbol moved",
))

_add(CommandSpec(
    op="set_background", family=DIRECT,
    required=(), optional=("data_url", "position", "scale", "opacity",
                           "rotation", "locked"),
    doc="Set or adjust the traced background image. No params clears it.",
    editor="setBackgroundImage",
    summary=lambda p, b, a: ("Background image removed" if not p
                             else "Background image set"),
))

_add(CommandSpec(
    op="group_elements", family=SYMBOLIC,
    required=("group_id", "element_ids"), optional=(),
    doc="Group elements so they move together.",
    editor="createGroup",
    summary=lambda p, b, a: f"{len(p.get('element_ids') or [])} elements grouped",
))

_add(CommandSpec(
    op="ungroup_elements", family=SYMBOLIC,
    required=("group_id",), optional=(),
    doc="Dissolve a group.",
    editor="ungroup",
    summary=lambda p, b, a: "Group dissolved",
))


# ---- document level -------------------------------------------------------

_add(CommandSpec(
    op="add_storey", family=SYMBOLIC,
    required=("storey_id",),
    optional=("name", "level", "copy_from", "id_map"),
    doc="Add a floor, optionally copying an existing one. When copying, "
        "`id_map` gives the old-id -> new-id mapping the author used, so both "
        "sides end up calling the copied walls the same thing.",
    editor="addFloor",
    summary=lambda p, b, a: f"{p.get('name') or 'Floor'} added",
))

_add(CommandSpec(
    op="remove_storey", family=SYMBOLIC,
    required=("storey_id",), optional=(),
    doc="Delete a floor.",
    editor="removeFloor",
    summary=lambda p, b, a: "Floor deleted",
))

_add(CommandSpec(
    op="set_active_storey", family=SYMBOLIC,
    required=("storey_id",), optional=(),
    doc="Switch which floor is being edited.",
    editor="setActiveFloor",
    summary=lambda p, b, a: _active_storey_summary(p, a),
))

_add(CommandSpec(
    op="rename_design", family=SYMBOLIC,
    required=("name",), optional=(),
    doc="Rename the design.",
    editor="updateProjectName",
    summary=lambda p, b, a: f"Renamed to {p.get('name')}",
))

_add(CommandSpec(
    op="set_site", family=SYMBOLIC,
    required=(), optional=("north_deg", "setbacks_mm"),
    doc="Set orientation or setbacks. The plot polygon comes from the "
        "envelope calculation, not from a command.",
    editor=None,
    summary=lambda p, b, a: (f"North set to {p['north_deg']}°"
                             if p.get("north_deg") is not None
                             else "Setbacks updated"),
))

_add(CommandSpec(
    op="replace_storey", family=SYMBOLIC,
    required=("storey_id",), optional=("reason",),
    doc="Swap a whole storey for freshly solved geometry. The payload is the "
        "solver's output, carried out of band -- this command records that it "
        "happened so the log stays a complete history.",
    editor=None,
    summary=lambda p, b, a: (f"Layout re-solved{': ' + str(p['reason']) if p.get('reason') else ''}"),
))


def _active_storey_summary(p: dict, after) -> str:
    st = _storey(after, str(p.get("storey_id", "")))
    return f"Switched to {st.name or 'floor'}" if st else "Switched floor"


# --------------------------------------------------------------------------
# derived views of the table
# --------------------------------------------------------------------------

SYMBOLIC_OPS = tuple(sorted(k for k, v in TABLE.items() if v.family == SYMBOLIC))
DIRECT_OPS = tuple(sorted(k for k, v in TABLE.items() if v.family == DIRECT))
SPEC_OPS = tuple(sorted(_SPEC_OPS))
GEOMETRY_OPS = tuple(sorted(set(TABLE) - set(_SPEC_OPS)))
AGENT_OPS = SYMBOLIC_OPS          # what an LLM is allowed to author


def catalogue(family: Optional[str] = None) -> str:
    """The vocabulary as prompt text, generated from the table.

    Generated rather than written out so the prompt cannot drift from what the
    applier accepts -- a class of bug that is invisible until the model emits a
    command nobody implemented.
    """
    lines = []
    for op in sorted(TABLE):
        s = TABLE[op]
        if family and s.family != family:
            continue
        req = ", ".join(s.required) or "-"
        opt = ", ".join(s.optional) or "-"
        lines.append(f"  {op} [{s.family}] required: {req} | optional: {opt}\n"
                     f"      {s.doc}")
    return "\n".join(lines)


def table_manifest() -> dict:
    """A language-neutral dump of the vocabulary.

    `tests/test_commands.py` compares this with the TypeScript module generated
    from it, so the browser and the service cannot disagree about what a
    command is called or what it takes.
    """
    return {
        "version": 1,
        "commands": {
            op: {"family": s.family,
                 "required": list(s.required),
                 "optional": list(s.optional),
                 "editor": s.editor,
                 "doc": s.doc}
            for op, s in sorted(TABLE.items())
        },
        "enums": {
            "door_types": list(DOOR_TYPES),
            "window_types": list(WINDOW_TYPES),
            "room_classes": list(ROOM_CLASSES),
            "column_shapes": list(COLUMN_SHAPES),
            "stair_types": list(STAIR_TYPES),
            "orientations": list(ORIENTATIONS),
            "endpoints": list(ENDPOINTS),
            "compass": list(COMPASS),
            "position_words": list(POSITION_WORDS),
            "sources": list(SOURCES),
        },
        "coordinate_keys": sorted(COORDINATE_KEYS),
    }


def manifest_json() -> str:
    return json.dumps(table_manifest(), indent=2, sort_keys=True)


# --------------------------------------------------------------------------
# validation helpers
# --------------------------------------------------------------------------

_ENUM_PARAMS: dict[str, tuple[str, ...]] = {
    "door_type": DOOR_TYPES,
    "window_type": WINDOW_TYPES,
    "room_class": ROOM_CLASSES,
    "shape": COLUMN_SHAPES,
    "stair_type": STAIR_TYPES,
    "orientation": ORIENTATIONS,
    "endpoint": ENDPOINTS,
    "direction": COMPASS,
}

_POSITIVE_MM = {
    "thickness_mm": (60, 500),
    "height_mm": (1, 10000),
    "width_mm": (1, 20000),
    "depth_mm": (1, 20000),
    "size_mm": (50, 3000),
    "sill_mm": (0, 3000),
    "head_mm": (100, 5000),
}


def _check_enums(op: str, p: dict) -> list[str]:
    errs: list[str] = []
    for key, allowed in _ENUM_PARAMS.items():
        v = p.get(key)
        # `direction` is a compass bearing on move_wall_parallel and up/down on
        # a stair, so it cannot be checked from one table.
        if key == "direction" and op in ("add_stair", "update_stair"):
            if v is not None and v not in ("up", "down"):
                errs.append(f"{op}: direction must be up|down")
            continue
        if v is not None and v not in allowed:
            errs.append(f"{op}: {key}={v!r} not in {allowed}")
    for key, (lo, hi) in _POSITIVE_MM.items():
        v = p.get(key)
        if v is None:
            continue
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            errs.append(f"{op}: {key} must be a number")
        elif not (lo <= v <= hi):
            errs.append(f"{op}: {key}={v} outside {lo}..{hi} mm")
    at = p.get("at")
    if at is not None:
        if isinstance(at, str):
            if at not in POSITION_WORDS and at != "midpoint":
                errs.append(f"{op}: at={at!r} must be a fraction or one of "
                            f"{POSITION_WORDS + ('midpoint',)}")
        elif isinstance(at, (int, float)) and not isinstance(at, bool):
            if not (0.0 <= float(at) <= 1.0):
                errs.append(f"{op}: at={at} must be within 0..1")
        else:
            errs.append(f"{op}: at must be a fraction or a position word")
    d = p.get("distance_mm")
    if d is not None:
        if not isinstance(d, (int, float)) or isinstance(d, bool):
            errs.append(f"{op}: distance_mm must be a number")
        elif not (10 <= abs(d) <= 5000):
            errs.append(f"{op}: distance_mm={d} outside 10..5000 mm; a repair "
                        "that large is a re-solve, not a nudge")
    return errs


_REF_PARAM_TARGET = {
    "wall_id": "walls", "room_id": "rooms", "opening_id": "openings",
    "furniture_id": "furniture", "stair_id": "stairs", "column_id": "columns",
}


def _check_refs(cmd: Command, design: Design) -> list[str]:
    """Does everything this command names still exist?

    Called at apply time, not at author time, which is what makes an agent
    patch computed against an older document safe: the ops whose referents
    survived apply, and the rest are rejected with a reason the user can read.
    """
    errs: list[str] = []
    st = _storey(design, cmd.storey_id)
    if st is None:
        return [f"{cmd.op}: no storey {cmd.storey_id or '(active)'}"]
    spec = TABLE[cmd.op]
    creates = {k for k in spec.required if k.startswith("new_")}
    for key, attr in _REF_PARAM_TARGET.items():
        v = cmd.params.get(key)
        if v is None or key in creates:
            continue
        # An `add_*` command supplies the id it is about to create.
        if cmd.op.startswith(("add_", "place_")) and key == _created_key(cmd.op):
            if any(getattr(x, "id", None) == v for x in getattr(st, attr, [])):
                errs.append(f"{cmd.op}: {key}={v!r} already exists")
            continue
        if not any(getattr(x, "id", None) == v for x in getattr(st, attr, [])):
            errs.append(f"{cmd.op}: no {key[:-3]} {v!r} on this storey")
    eid = cmd.params.get("element_id")
    if eid is not None and not _element_exists(st, str(eid)):
        errs.append(f"{cmd.op}: no element {eid!r} on this storey")
    return errs


def _created_key(op: str) -> str:
    return {
        "add_wall": "wall_id", "add_wall_between": "wall_id",
        "add_door": "opening_id", "add_window": "opening_id",
        "add_furniture": "furniture_id", "place_furniture_in_room": "furniture_id",
        "add_stair": "stair_id", "add_column": "column_id",
    }.get(op, "")


def _element_exists(st: Plan, eid: str) -> bool:
    if st.wall(eid) or st.room(eid) or st.opening(eid):
        return True
    for attr in ("furniture", "stairs", "columns"):
        if any(x.id == eid for x in getattr(st, attr, [])):
            return True
    pr = st.presentation
    for coll in (pr.guides, pr.measurements, pr.dimensions, pr.texts,
                 pr.groups, pr.entourage):
        if any(x.id == eid for x in coll):
            return True
    return False


def render_summary(cmd: Command, before: Optional[Design],
                   after: Optional[Design]) -> str:
    """The chat bullet for a command, generated from the command.

    Never written at the call site. Sixty call sites writing their own would
    drift within a week, and this text is not decoration: the user reads it and
    the model reads it next turn.
    """
    spec = TABLE.get(cmd.op)
    if spec is None:
        return cmd.description or cmd.op
    try:
        text = spec.summary(cmd.params, before, after)
    except Exception:
        # A summary is a nicety; a crash in one must not lose the edit.
        text = cmd.description or cmd.op
    return text or cmd.op


def refs_of(cmd: Command) -> list[str]:
    """Element ids a command touches, for highlighting and for `Event.refs`."""
    out: list[str] = []
    for key in (*_REF_PARAM_TARGET, "element_id", "group_id", "guide_id",
                "measurement_id", "dimension_id", "text_id", "entourage_id",
                "storey_id"):
        v = cmd.params.get(key)
        if isinstance(v, str):
            out.append(v)
    ids = cmd.params.get("element_ids")
    if isinstance(ids, Sequence) and not isinstance(ids, str):
        out += [str(x) for x in ids]
    return out
