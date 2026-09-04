"""The one applier. Every change to a design goes through here.

There is exactly one of these on purpose. The alternative -- the browser
mutating its `Project` in TypeScript while the service mutates the IR in Python
-- means two implementations of every edit and a class of bug where the two
drift silently in geometry. Here the browser applies optimistically using its
existing store mutators for responsiveness, but the answer that counts comes
from this module, and the client reconciles to it by state hash.

Units are **integer millimetres** throughout, matching the IR (DECISIONS.md
#1). A command created in the browser converts from centimetres before it is
sent, so quantisation happens once, at the edge, and never again.

Rooms are re-derived after anything that touches walls. That is not an
optimisation detail -- a room is a face of the wall graph, so moving a wall
*is* how you resize a room, and the identity machinery in `roomid.py` is what
keeps the name attached while the geometry underneath it changes.
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from typing import Any, Optional

from .commands import (
    Command, Event, TABLE, render_summary, refs_of, POSITION_WORDS,
)
from .ir import (
    Design, Plan, Wall, Opening, Room, Site, Stair, Furniture, Column, P,
    GuideLine, Measurement, DimAnnotation, TextAnnotation, ElementGroup,
    EntourageItem, BackgroundImage,
)
from .faces import rederive_rooms

# Commands after which the room faces have to be recomputed.
WALL_TOUCHING = frozenset({
    "add_wall", "add_wall_between", "move_wall_endpoint", "move_wall_by",
    "move_wall_parallel", "split_wall", "update_wall", "duplicate_wall",
    "remove_element", "replace_storey", "add_storey",
})

_POSITION_T = {"start": 0.0, "quarter": 0.25, "centre": 0.5,
               "three_quarter": 0.75, "end": 1.0, "midpoint": 0.5}


@dataclass
class ApplyResult:
    design: Design
    event: Optional[Event] = None
    errors: list[str] = field(default_factory=list)
    # Rooms the edit destroyed, so the caller can say "Bedroom 2 no longer
    # exists" rather than leaving the user to notice.
    rooms_gone: list[str] = field(default_factory=list)
    rooms_new: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class ApplyError(ValueError):
    pass


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

def apply_command(design: Design, cmd: Command, *,
                  payload: Any = None, seq: int = 0) -> ApplyResult:
    """Apply one command, returning a new design and the event it produced.

    The input design is never mutated: an event-sourced log has to be able to
    replay from any snapshot, and an applier that edits in place makes that
    a matter of caller discipline instead of a property of the code.

    Referential validation happens **here**, not when the command was authored.
    That is what makes an agent patch computed against an older document safe:
    the parts whose referents survived apply, and the rest come back with a
    reason a user can read.
    """
    spec = TABLE.get(cmd.op)
    if spec is None:
        return ApplyResult(design, None, [f"unknown command {cmd.op!r}"])

    errs = cmd.validate(design)
    if errs:
        return ApplyResult(design, None, errs)

    before = design
    after = copy.deepcopy(design)
    storey = after.storey(cmd.storey_id) if cmd.storey_id else after.active
    if storey is None and cmd.op not in ("add_storey", "rename_design"):
        return ApplyResult(design, None,
                           [f"{cmd.op}: no storey {cmd.storey_id or '(active)'}"])

    handler = _HANDLERS.get(cmd.op)
    if handler is None:
        return ApplyResult(design, None,
                           [f"{cmd.op}: recognised but not implemented"])
    try:
        handler(after, storey, cmd.params, payload)
    except ApplyError as exc:
        return ApplyResult(design, None, [f"{cmd.op}: {exc}"])
    except (KeyError, TypeError, ValueError) as exc:
        return ApplyResult(design, None,
                           [f"{cmd.op}: {type(exc).__name__}: {exc}"])

    gone: list[str] = []
    new: list[str] = []
    if cmd.op in WALL_TOUCHING and storey is not None:
        rooms, lost, fresh = rederive_rooms(storey)
        storey.rooms = rooms
        gone = [r.name or r.id for r in lost]
        new = [r.id for r in fresh]

    event = Event(
        seq=seq, command_id=cmd.id, op=cmd.op, source=cmd.source,
        summary=render_summary(cmd, before, after), refs=refs_of(cmd),
    )
    return ApplyResult(after, event, [], rooms_gone=gone, rooms_new=new)


def apply_all(design: Design, cmds: list[Command], *,
              start_seq: int = 1) -> tuple[Design, list[Event], list[tuple[Command, list[str]]]]:
    """Apply a batch, keeping what works and reporting what does not.

    Partial application is the point. A batch of six ops where two reference a
    wall the user just deleted should land four and explain two, rather than
    failing whole -- or, far worse, half-applying without saying so.
    """
    events: list[Event] = []
    rejected: list[tuple[Command, list[str]]] = []
    seq = start_seq
    for cmd in cmds:
        res = apply_command(design, cmd, seq=seq)
        if res.ok and res.event is not None:
            design = res.design
            events.append(res.event)
            seq += 1
        else:
            rejected.append((cmd, res.errors))
    return design, events, rejected


# --------------------------------------------------------------------------
# param readers
# --------------------------------------------------------------------------

def _pt(v: Any, what: str = "position") -> P:
    if not isinstance(v, dict) or "x" not in v or "y" not in v:
        raise ApplyError(f"{what} must be an object with x and y in mm")
    return P(int(round(float(v["x"]))), int(round(float(v["y"]))))


def _t(v: Any) -> float:
    """A parametric position: a fraction, or a word like 'centre'."""
    if isinstance(v, str):
        if v not in _POSITION_T:
            raise ApplyError(f"position {v!r} must be a fraction or one of "
                             f"{tuple(_POSITION_T)}")
        return _POSITION_T[v]
    f = float(v)
    if not (0.0 <= f <= 1.0):
        raise ApplyError(f"position {f} outside 0..1")
    return f


def _wall(st: Plan, wid: Any) -> Wall:
    w = st.wall(str(wid))
    if w is None:
        raise ApplyError(f"no wall {wid!r}")
    return w


def _find(items: list, oid: Any, what: str):
    x = next((i for i in items if i.id == str(oid)), None)
    if x is None:
        raise ApplyError(f"no {what} {oid!r}")
    return x


def _set_if(obj: Any, params: dict, mapping: dict[str, str]) -> None:
    """Copy the params that were supplied, leaving the rest alone.

    `None` means "not supplied" everywhere in this vocabulary, so a command
    cannot clear a field by omission -- which is what you want when a patch
    touches one property of a wall.
    """
    for key, attr in mapping.items():
        if params.get(key) is not None:
            setattr(obj, attr, params[key])


# --------------------------------------------------------------------------
# symbolic reference resolution
# --------------------------------------------------------------------------

def resolve_ref(st: Plan, ref: Any) -> P:
    """`w7:start`, `w7:end`, `w7@0.5` -> a point.

    This is the hinge that lets an agent add a wall without authoring a
    coordinate: it says *which* existing feature to build from, and the
    resolution happens here against the real geometry.
    """
    if not isinstance(ref, str) or ":" not in ref and "@" not in ref:
        raise ApplyError(f"{ref!r} is not a symbolic reference "
                         "('w7:start', 'w7:end', 'w7@0.5')")
    if "@" in ref:
        wid, _, frac = ref.partition("@")
        w = _wall(st, wid)
        try:
            t = float(frac)
        except ValueError:
            t = _t(frac)
        if not (0.0 <= t <= 1.0):
            raise ApplyError(f"{ref}: fraction outside 0..1")
        return P(round(w.start.x + t * (w.end.x - w.start.x)),
                 round(w.start.y + t * (w.end.y - w.start.y)))
    wid, _, end = ref.partition(":")
    w = _wall(st, wid)
    if end == "start":
        return w.start
    if end == "end":
        return w.end
    raise ApplyError(f"{ref}: endpoint must be 'start' or 'end'")


def _bearing_delta(direction: str, distance_mm: float, north_deg: float
                   ) -> tuple[int, int]:
    """A compass direction plus a magnitude into a displacement.

    The agent never sees a coordinate: it names a direction and a distance, and
    the resolution against the site's north happens here.
    """
    bearings = {"north": 0, "north_east": 45, "east": 90, "south_east": 135,
                "south": 180, "south_west": 225, "west": 270,
                "north_west": 315}
    if direction not in bearings:
        raise ApplyError(f"unknown direction {direction!r}")
    theta = math.radians(bearings[direction] - north_deg)
    return (round(distance_mm * math.sin(theta)),
            round(distance_mm * math.cos(theta)))


# --------------------------------------------------------------------------
# handlers: walls
# --------------------------------------------------------------------------

def _h_add_wall(d: Design, st: Plan, p: dict, _payload) -> None:
    st.walls.append(Wall(
        id=str(p["wall_id"]),
        start=_pt(p["start"], "start"), end=_pt(p["end"], "end"),
        thickness=int(p.get("thickness_mm") or 150),
        height=int(p.get("height_mm") or st.storey_height),
    ))


def _h_add_wall_between(d: Design, st: Plan, p: dict, _payload) -> None:
    st.walls.append(Wall(
        id=str(p["wall_id"]),
        start=resolve_ref(st, p["start_ref"]),
        end=resolve_ref(st, p["end_ref"]),
        thickness=int(p.get("thickness_mm") or 150),
        height=int(p.get("height_mm") or st.storey_height),
    ))


def _h_move_wall_endpoint(d: Design, st: Plan, p: dict, _payload) -> None:
    w = _wall(st, p["wall_id"])
    point = _pt(p["position"])
    if p["endpoint"] == "start":
        w.start = point
    else:
        w.end = point


def _h_move_wall_by(d: Design, st: Plan, p: dict, _payload) -> None:
    w = _wall(st, p["wall_id"])
    dx, dy = int(round(float(p["dx"]))), int(round(float(p["dy"])))
    w.start = P(w.start.x + dx, w.start.y + dy)
    w.end = P(w.end.x + dx, w.end.y + dy)


def _h_move_wall_parallel(d: Design, st: Plan, p: dict, _payload) -> None:
    w = _wall(st, p["wall_id"])
    dx, dy = _bearing_delta(str(p["direction"]), float(p["distance_mm"]),
                            st.site.north_deg)
    w.start = P(w.start.x + dx, w.start.y + dy)
    w.end = P(w.end.x + dx, w.end.y + dy)


def _h_split_wall(d: Design, st: Plan, p: dict, _payload) -> None:
    w = _wall(st, p["wall_id"])
    new_id = str(p["new_wall_id"])
    if st.wall(new_id) is not None:
        raise ApplyError(f"wall {new_id!r} already exists")
    t = _t(p["at"])
    if not (0.01 < t < 0.99):
        raise ApplyError(f"at={t} leaves a zero-length stub")
    mid = P(round(w.start.x + t * (w.end.x - w.start.x)),
            round(w.start.y + t * (w.end.y - w.start.y)))
    tail_start, tail_end = mid, w.end
    w.end = mid
    st.walls.insert(st.walls.index(w) + 1, Wall(
        id=new_id, start=tail_start, end=tail_end,
        thickness=w.thickness, height=w.height, color=w.color,
        texture=w.texture, interior_color=w.interior_color,
        interior_texture=w.interior_texture,
        exterior_color=w.exterior_color, exterior_texture=w.exterior_texture,
    ))
    # Re-host the openings. An opening past the split point belongs to the new
    # half, at a position rescaled into it -- dropping them, or leaving them on
    # a wall that no longer reaches them, would silently move doors.
    for o in st.openings:
        if o.wall_id != w.id:
            continue
        if o.position <= t:
            o.position = o.position / t if t else 0.0
        else:
            o.wall_id = new_id
            o.position = (o.position - t) / (1.0 - t)


def _h_update_wall(d: Design, st: Plan, p: dict, _payload) -> None:
    w = _wall(st, p["wall_id"])
    _set_if(w, p, {"thickness_mm": "thickness", "height_mm": "height",
                   "color": "color", "texture": "texture",
                   "interior_color": "interior_color",
                   "interior_texture": "interior_texture",
                   "exterior_color": "exterior_color",
                   "exterior_texture": "exterior_texture"})


def _h_duplicate_wall(d: Design, st: Plan, p: dict, _payload) -> None:
    w = _wall(st, p["wall_id"])
    new_id = str(p["new_wall_id"])
    if st.wall(new_id) is not None:
        raise ApplyError(f"wall {new_id!r} already exists")
    off = 200                      # mm, the editor's nudge
    st.walls.append(Wall(
        id=new_id, start=P(w.start.x + off, w.start.y + off),
        end=P(w.end.x + off, w.end.y + off),
        thickness=w.thickness, height=w.height, color=w.color,
    ))


# --------------------------------------------------------------------------
# handlers: openings
# --------------------------------------------------------------------------

_DOOR_WIDTH = {"single": 900, "double": 1500, "sliding": 1800, "french": 1500,
               "pocket": 900, "bifold": 1800, "opening": 1000, "garage": 2400}
_WINDOW_SIZE = {"standard": (1200, 900, 2100), "fixed": (1000, 900, 1900),
                "casement": (800, 900, 2200), "sliding": (1800, 900, 2100),
                "bay": (2000, 600, 2100)}


def _h_add_door(d: Design, st: Plan, p: dict, _payload) -> None:
    oid = str(p["opening_id"])
    if st.opening(oid) is not None:
        raise ApplyError(f"opening {oid!r} already exists")
    _wall(st, p["wall_id"])
    dtype = str(p.get("door_type") or "single")
    kind = str(p.get("kind") or "door")
    if kind not in ("door", "front_door"):
        raise ApplyError("kind must be door or front_door")
    st.openings.append(Opening(
        id=oid, kind=kind, wall_id=str(p["wall_id"]), position=_t(p["at"]),
        width=int(p.get("width_mm") or _DOOR_WIDTH.get(dtype, 900)),
        sill=0, head=2100,
        subtype="" if dtype == _default_door_subtype(kind) else dtype,
    ))


def _default_door_subtype(kind: str) -> str:
    return "single" if kind == "door" else "opening"


def _h_add_window(d: Design, st: Plan, p: dict, _payload) -> None:
    oid = str(p["opening_id"])
    if st.opening(oid) is not None:
        raise ApplyError(f"opening {oid!r} already exists")
    _wall(st, p["wall_id"])
    wtype = str(p.get("window_type") or "standard")
    w_default, sill_default, head_default = _WINDOW_SIZE.get(
        wtype, _WINDOW_SIZE["standard"])
    sill = int(p.get("sill_mm") if p.get("sill_mm") is not None else sill_default)
    head = int(p.get("head_mm") if p.get("head_mm") is not None else head_default)
    if head <= sill:
        raise ApplyError(f"head {head} must be above sill {sill}")
    st.openings.append(Opening(
        id=oid, kind="window", wall_id=str(p["wall_id"]), position=_t(p["at"]),
        width=int(p.get("width_mm") or w_default), sill=sill, head=head,
        subtype="" if wtype == "standard" else wtype,
    ))


def _h_update_opening(d: Design, st: Plan, p: dict, _payload) -> None:
    o = _find(st.openings, p["opening_id"], "opening")
    if p.get("width_mm") is not None:
        o.width = int(p["width_mm"])
    if p.get("at") is not None:
        o.position = _t(p["at"])
    if p.get("door_type") is not None:
        if o.kind == "window":
            raise ApplyError("door_type on a window")
        o.subtype = str(p["door_type"])
    if p.get("window_type") is not None:
        if o.kind != "window":
            raise ApplyError("window_type on a door")
        o.subtype = str(p["window_type"])
    if p.get("swing_direction") is not None:
        o.swing_direction = str(p["swing_direction"])
    if p.get("flip_side") is not None:
        o.flip_side = bool(p["flip_side"])
    if p.get("sill_mm") is not None:
        o.sill = int(p["sill_mm"])
    if p.get("head_mm") is not None:
        o.head = int(p["head_mm"])
    if o.head <= o.sill:
        raise ApplyError(f"head {o.head} must be above sill {o.sill}")


def _h_duplicate_opening(d: Design, st: Plan, p: dict, _payload) -> None:
    o = _find(st.openings, p["opening_id"], "opening")
    new_id = str(p["new_opening_id"])
    if st.opening(new_id) is not None:
        raise ApplyError(f"opening {new_id!r} already exists")
    copy_of = copy.deepcopy(o)
    copy_of.id = new_id
    copy_of.position = min(0.95, o.position + 0.1)
    st.openings.append(copy_of)


# --------------------------------------------------------------------------
# handlers: rooms
# --------------------------------------------------------------------------

def _h_update_room(d: Design, st: Plan, p: dict, _payload) -> None:
    r = _find(st.rooms, p["room_id"], "room")
    _set_if(r, p, {"name": "name", "category": "category",
                   "room_class": "room_class",
                   "floor_texture": "floor_texture", "color": "color"})
    # Naming a room is what makes it addressable; make sure it can be found
    # again after the walls around it change.
    if r.anchor is None and len(r.polygon) >= 3:
        from .roomid import centroid
        r.anchor = centroid(r.polygon)


def _h_move_room_label(d: Design, st: Plan, p: dict, _payload) -> None:
    r = _find(st.rooms, p["room_id"], "room")
    r.label_offset = P(int(round(float(p["offset_x"]))),
                       int(round(float(p["offset_y"]))))


# --------------------------------------------------------------------------
# handlers: objects
# --------------------------------------------------------------------------

def _h_add_furniture(d: Design, st: Plan, p: dict, _payload) -> None:
    fid = str(p["furniture_id"])
    if any(f.id == fid for f in st.furniture):
        raise ApplyError(f"furniture {fid!r} already exists")
    pos = _pt(p["position"])
    st.furniture.append(Furniture(
        id=fid, catalog_id=str(p["catalog_id"]), position=pos,
        rotation=float(p.get("rotation") or 0.0),
        width=int(p.get("width_mm") or 0), depth=int(p.get("depth_mm") or 0),
        height=int(p.get("height_mm") or 0),
        room_id=p.get("room_id") or _room_containing(st, pos),
    ))


def _h_place_furniture_in_room(d: Design, st: Plan, p: dict, payload) -> None:
    """The symbolic form: the agent names a room and an anchor, the placement
    solver returns the coordinate. Until it does, the room centroid is used --
    a visible, correctable position beats refusing the edit."""
    fid = str(p["furniture_id"])
    if any(f.id == fid for f in st.furniture):
        raise ApplyError(f"furniture {fid!r} already exists")
    r = _find(st.rooms, p["room_id"], "room")
    from .roomid import centroid
    pos = _pt(payload["position"]) if isinstance(payload, dict) and "position" \
        in payload else (r.anchor or centroid(r.polygon))
    st.furniture.append(Furniture(
        id=fid, catalog_id=str(p["catalog_id"]), position=pos, room_id=r.id))


def _room_containing(st: Plan, point: P) -> Optional[str]:
    from .roomid import point_in_polygon
    for r in st.rooms:
        if point_in_polygon(point, r.polygon):
            return r.id
    return None


def _h_move_furniture(d: Design, st: Plan, p: dict, _payload) -> None:
    f = _find(st.furniture, p["furniture_id"], "furniture")
    f.position = _pt(p["position"])
    f.room_id = _room_containing(st, f.position) or f.room_id


def _h_update_furniture(d: Design, st: Plan, p: dict, _payload) -> None:
    f = _find(st.furniture, p["furniture_id"], "furniture")
    _set_if(f, p, {"rotation": "rotation", "width_mm": "width",
                   "depth_mm": "depth", "height_mm": "height",
                   "color": "color", "material": "material",
                   "locked": "locked", "scale_x": "scale_x",
                   "scale_y": "scale_y", "scale_z": "scale_z",
                   "room_id": "room_id"})


def _h_duplicate_furniture(d: Design, st: Plan, p: dict, _payload) -> None:
    f = _find(st.furniture, p["furniture_id"], "furniture")
    new_id = str(p["new_furniture_id"])
    if any(x.id == new_id for x in st.furniture):
        raise ApplyError(f"furniture {new_id!r} already exists")
    dup = copy.deepcopy(f)
    dup.id = new_id
    dup.position = P(f.position.x + 200, f.position.y + 200)
    dup.locked = False
    st.furniture.append(dup)


def _h_add_stair(d: Design, st: Plan, p: dict, _payload) -> None:
    sid = str(p["stair_id"])
    if any(s.id == sid for s in st.stairs):
        raise ApplyError(f"stair {sid!r} already exists")
    pos = _pt(p["position"])
    st.stairs.append(Stair(
        id=sid, position=pos, rotation=float(p.get("rotation") or 0.0),
        width=int(p.get("width_mm") or 1000),
        depth=int(p.get("depth_mm") or 3000),
        riser_count=int(p.get("riser_count") or 14),
        direction=str(p.get("direction") or "up"),
        stair_type=str(p.get("stair_type") or "straight"),
        room_id=p.get("room_id") or _room_containing(st, pos),
    ))


def _h_update_stair(d: Design, st: Plan, p: dict, _payload) -> None:
    s = _find(st.stairs, p["stair_id"], "stair")
    _set_if(s, p, {"rotation": "rotation", "width_mm": "width",
                   "depth_mm": "depth", "riser_count": "riser_count",
                   "direction": "direction", "stair_type": "stair_type",
                   "room_id": "room_id"})


def _h_move_stair(d: Design, st: Plan, p: dict, _payload) -> None:
    s = _find(st.stairs, p["stair_id"], "stair")
    s.position = _pt(p["position"])
    s.room_id = _room_containing(st, s.position) or s.room_id


def _h_add_column(d: Design, st: Plan, p: dict, _payload) -> None:
    cid = str(p["column_id"])
    if any(c.id == cid for c in st.columns):
        raise ApplyError(f"column {cid!r} already exists")
    st.columns.append(Column(
        id=cid, position=_pt(p["position"]),
        rotation=float(p.get("rotation") or 0.0),
        shape=str(p.get("shape") or "round"),
        size=int(p.get("size_mm") or 300),
        height=int(p.get("height_mm") or st.storey_height),
    ))


def _h_update_column(d: Design, st: Plan, p: dict, _payload) -> None:
    c = _find(st.columns, p["column_id"], "column")
    _set_if(c, p, {"shape": "shape", "size_mm": "size", "height_mm": "height",
                   "rotation": "rotation", "color": "color"})


def _h_move_column(d: Design, st: Plan, p: dict, _payload) -> None:
    c = _find(st.columns, p["column_id"], "column")
    c.position = _pt(p["position"])


# --------------------------------------------------------------------------
# handlers: deletion
# --------------------------------------------------------------------------

def _h_remove_element(d: Design, st: Plan, p: dict, _payload) -> None:
    eid = str(p["element_id"])
    if st.wall(eid) is not None:
        st.walls = [w for w in st.walls if w.id != eid]
        # Cascade: an opening is parametric on its host wall, so it cannot
        # outlive it. Leaving orphans behind is how you get doors floating in
        # space that no rule can attribute to anything.
        st.openings = [o for o in st.openings if o.wall_id != eid]
        return
    for attr in ("openings", "rooms", "furniture", "stairs", "columns"):
        items = getattr(st, attr)
        if any(x.id == eid for x in items):
            setattr(st, attr, [x for x in items if x.id != eid])
            return
    pr = st.presentation
    for name in ("guides", "measurements", "dimensions", "texts", "groups",
                 "entourage"):
        items = getattr(pr, name)
        if any(x.id == eid for x in items):
            setattr(pr, name, [x for x in items if x.id != eid])
            return
    raise ApplyError(f"no element {eid!r}")


# --------------------------------------------------------------------------
# handlers: presentation
# --------------------------------------------------------------------------

def _h_add_guide(d: Design, st: Plan, p: dict, _payload) -> None:
    st.presentation.guides.append(GuideLine(
        id=str(p["guide_id"]), orientation=str(p["orientation"]),
        position=int(round(float(p["position"])))))


def _h_move_guide(d: Design, st: Plan, p: dict, _payload) -> None:
    g = _find(st.presentation.guides, p["guide_id"], "guide")
    g.position = int(round(float(p["position"])))


def _h_add_measurement(d: Design, st: Plan, p: dict, _payload) -> None:
    st.presentation.measurements.append(Measurement(
        id=str(p["measurement_id"]), start=_pt(p["start"], "start"),
        end=_pt(p["end"], "end")))


def _h_add_dimension(d: Design, st: Plan, p: dict, _payload) -> None:
    st.presentation.dimensions.append(DimAnnotation(
        id=str(p["dimension_id"]), start=_pt(p["start"], "start"),
        end=_pt(p["end"], "end"),
        offset=int(round(float(p.get("offset") or 400))),
        label=str(p.get("label") or "")))


def _h_update_dimension(d: Design, st: Plan, p: dict, _payload) -> None:
    a = _find(st.presentation.dimensions, p["dimension_id"], "dimension")
    if p.get("label") is not None:
        a.label = str(p["label"])


def _h_add_text(d: Design, st: Plan, p: dict, _payload) -> None:
    st.presentation.texts.append(TextAnnotation(
        id=str(p["text_id"]), position=_pt(p["position"]), text=str(p["text"]),
        font_size=int(p.get("font_size") or 16),
        color=str(p.get("color") or "#1e293b"),
        rotation=float(p.get("rotation") or 0.0)))


def _h_update_text(d: Design, st: Plan, p: dict, _payload) -> None:
    t = _find(st.presentation.texts, p["text_id"], "text")
    _set_if(t, p, {"text": "text", "font_size": "font_size",
                   "color": "color", "rotation": "rotation"})


def _h_move_text(d: Design, st: Plan, p: dict, _payload) -> None:
    t = _find(st.presentation.texts, p["text_id"], "text")
    t.position = _pt(p["position"])


def _h_add_entourage(d: Design, st: Plan, p: dict, _payload) -> None:
    st.presentation.entourage.append(EntourageItem(
        id=str(p["entourage_id"]), def_id=str(p["def_id"]),
        position=_pt(p["position"]), width=int(p["width_mm"]),
        rotation=float(p.get("rotation") or 0.0),
        opacity=float(p.get("opacity") if p.get("opacity") is not None else 1.0)))


def _h_update_entourage(d: Design, st: Plan, p: dict, _payload) -> None:
    e = _find(st.presentation.entourage, p["entourage_id"], "entourage item")
    _set_if(e, p, {"width_mm": "width", "rotation": "rotation",
                   "opacity": "opacity", "locked": "locked"})


def _h_move_entourage(d: Design, st: Plan, p: dict, _payload) -> None:
    e = _find(st.presentation.entourage, p["entourage_id"], "entourage item")
    e.position = _pt(p["position"])


def _h_set_background(d: Design, st: Plan, p: dict, _payload) -> None:
    if not p:
        st.presentation.background = None
        return
    bg = st.presentation.background
    if bg is None:
        if not p.get("data_url"):
            raise ApplyError("there is no background image to adjust")
        bg = BackgroundImage(data_url=str(p["data_url"]))
        st.presentation.background = bg
    if p.get("data_url") is not None:
        bg.data_url = str(p["data_url"])
    if p.get("position") is not None:
        bg.position = _pt(p["position"])
    _set_if(bg, p, {"scale": "scale", "opacity": "opacity",
                    "rotation": "rotation", "locked": "locked"})


def _h_group_elements(d: Design, st: Plan, p: dict, _payload) -> None:
    gid = str(p["group_id"])
    if any(g.id == gid for g in st.presentation.groups):
        raise ApplyError(f"group {gid!r} already exists")
    ids = [str(x) for x in (p.get("element_ids") or [])]
    if len(ids) < 2:
        raise ApplyError("a group needs at least two elements")
    st.presentation.groups.append(ElementGroup(id=gid, element_ids=ids))


def _h_ungroup_elements(d: Design, st: Plan, p: dict, _payload) -> None:
    gid = str(p["group_id"])
    if not any(g.id == gid for g in st.presentation.groups):
        raise ApplyError(f"no group {gid!r}")
    st.presentation.groups = [g for g in st.presentation.groups if g.id != gid]


# --------------------------------------------------------------------------
# handlers: document level
# --------------------------------------------------------------------------

def _h_add_storey(d: Design, st: Optional[Plan], p: dict, _payload) -> None:
    sid = str(p["storey_id"])
    if d.storey(sid) is not None:
        raise ApplyError(f"storey {sid!r} already exists")
    level = int(p["level"]) if p.get("level") is not None else (
        max((s.level for s in d.storeys), default=-1) + 1)
    src = d.storey(str(p["copy_from"])) if p.get("copy_from") else None
    if p.get("copy_from") and src is None:
        raise ApplyError(f"no storey {p['copy_from']!r} to copy")
    if src is not None:
        fresh = copy.deepcopy(src)
        fresh.id = sid
        fresh.level = level
        fresh.name = str(p.get("name") or "")
        # Presentation is sheet furniture for one drawing; copying a floor's
        # dimension strings onto the next one just makes them wrong.
        fresh.presentation = type(fresh.presentation)()
        # The author supplies the new wall ids. Minting our own would leave the
        # client and the service permanently disagreeing about what the walls
        # on this floor are called -- and every later command names them.
        id_map = {str(k): str(v) for k, v in dict(p.get("id_map") or {}).items()}
        if id_map:
            for w in fresh.walls:
                w.id = id_map.get(w.id, w.id)
            for o in fresh.openings:
                o.wall_id = id_map.get(o.wall_id, o.wall_id)
            for room in fresh.rooms:
                room.wall_ids = [id_map.get(x, x) for x in room.wall_ids]
    else:
        fresh = Plan(id=sid, level=level, name=str(p.get("name") or ""),
                     site=copy.deepcopy(d.site),
                     storey_height=(st.storey_height if st else 3000))
    d.storeys.append(fresh)
    d.storeys.sort(key=lambda s: s.level)


def _h_remove_storey(d: Design, st: Optional[Plan], p: dict, _payload) -> None:
    sid = str(p["storey_id"])
    if d.storey(sid) is None:
        raise ApplyError(f"no storey {sid!r}")
    if len(d.storeys) <= 1:
        raise ApplyError("a design needs at least one storey")
    d.storeys = [s for s in d.storeys if s.id != sid]
    if d.active_storey_id == sid:
        d.active_storey_id = d.storeys[0].id


def _h_set_active_storey(d: Design, st: Optional[Plan], p: dict, _payload) -> None:
    sid = str(p["storey_id"])
    if d.storey(sid) is None:
        raise ApplyError(f"no storey {sid!r}")
    d.active_storey_id = sid


def _h_rename_design(d: Design, st: Optional[Plan], p: dict, _payload) -> None:
    d.name = str(p["name"])


def _h_set_site(d: Design, st: Optional[Plan], p: dict, _payload) -> None:
    # The site belongs to the plot, so it is written to every storey rather
    # than to whichever one happens to be active.
    for s in d.storeys:
        if p.get("north_deg") is not None:
            s.site.north_deg = float(p["north_deg"])
        if p.get("setbacks_mm") is not None:
            s.site.setbacks_mm = {str(k): int(v)
                                  for k, v in dict(p["setbacks_mm"]).items()}


def _h_replace_storey(d: Design, st: Optional[Plan], p: dict, payload) -> None:
    """Swap in freshly solved geometry.

    The new storey arrives as `payload`, not in `params`: it is a solver
    output, and putting a wall list inside a symbolic command's params would
    make the no-coordinates rule unenforceable exactly where it matters most.
    """
    sid = str(p["storey_id"])
    old = d.storey(sid)
    if old is None:
        raise ApplyError(f"no storey {sid!r}")
    if not isinstance(payload, Plan):
        raise ApplyError("replace_storey needs a solved Plan as its payload")
    fresh = copy.deepcopy(payload)
    fresh.id = sid
    fresh.level = old.level
    fresh.name = old.name
    fresh.storey_height = old.storey_height
    # The sheet layer is the user's, not the solver's.
    fresh.presentation = copy.deepcopy(old.presentation)
    d.storeys[d.storeys.index(old)] = fresh


_HANDLERS: dict[str, Any] = {
    "add_wall": _h_add_wall,
    "add_wall_between": _h_add_wall_between,
    "move_wall_endpoint": _h_move_wall_endpoint,
    "move_wall_by": _h_move_wall_by,
    "move_wall_parallel": _h_move_wall_parallel,
    "split_wall": _h_split_wall,
    "update_wall": _h_update_wall,
    "duplicate_wall": _h_duplicate_wall,
    "add_door": _h_add_door,
    "add_window": _h_add_window,
    "update_opening": _h_update_opening,
    "duplicate_opening": _h_duplicate_opening,
    "update_room": _h_update_room,
    "move_room_label": _h_move_room_label,
    "add_furniture": _h_add_furniture,
    "place_furniture_in_room": _h_place_furniture_in_room,
    "move_furniture": _h_move_furniture,
    "update_furniture": _h_update_furniture,
    "duplicate_furniture": _h_duplicate_furniture,
    "add_stair": _h_add_stair,
    "update_stair": _h_update_stair,
    "move_stair": _h_move_stair,
    "add_column": _h_add_column,
    "update_column": _h_update_column,
    "move_column": _h_move_column,
    "remove_element": _h_remove_element,
    "add_guide": _h_add_guide,
    "move_guide": _h_move_guide,
    "add_measurement": _h_add_measurement,
    "add_dimension": _h_add_dimension,
    "update_dimension": _h_update_dimension,
    "add_text": _h_add_text,
    "update_text": _h_update_text,
    "move_text": _h_move_text,
    "add_entourage": _h_add_entourage,
    "update_entourage": _h_update_entourage,
    "move_entourage": _h_move_entourage,
    "set_background": _h_set_background,
    "group_elements": _h_group_elements,
    "ungroup_elements": _h_ungroup_elements,
    "add_storey": _h_add_storey,
    "remove_storey": _h_remove_storey,
    "set_active_storey": _h_set_active_storey,
    "rename_design": _h_rename_design,
    "set_site": _h_set_site,
    "replace_storey": _h_replace_storey,
}


# The programme layer -- the constraints the solver reads, as opposed to the
# drawn geometry -- is not wired into the document service. These commands are
# in the vocabulary, are offered to the agent, validate cleanly, and then fail
# at apply time with "recognised but not implemented".
#
# They are listed rather than quietly excluded because excluding them is what
# hid the gap: `unimplemented()` used to subtract `SPEC_OPS`, so the "every
# command has a handler" test passed while a third of the agent's vocabulary
# did nothing. `tests/probe_agent.py` found it from the outside, and the agent
# diagnosed it unaided -- "the whole programme layer is stubbed out in this
# build".
#
# Wiring them needs a `DesignSpec` on the document plus a re-solve path
# (`loop.repair` exists; nothing connects it to a `Document`).
KNOWN_UNIMPLEMENTED = frozenset({
    "add_room", "remove_room", "set_room_area", "set_room_aspect",
    "set_room_zone", "set_room_priority", "set_adjacency",
    "remove_adjacency", "set_entrance", "set_wet_grouping", "set_storeys",
})


def unimplemented() -> list[str]:
    """Every command in the vocabulary with no applier, spec ops included.

    A vocabulary entry with no applier is the exact shape of bug that only
    shows up when a model emits the command in front of a user.
    """
    return sorted(set(TABLE) - set(_HANDLERS))
