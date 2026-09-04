"""Apply `llm.PatchOp` ops to a single `Plan` — the repair-loop path.

Named `apply_ops` rather than `apply` because there are two appliers and they
are not the same thing. This one takes `llm.PatchOp` objects and a `Plan`, and
is what `loop.repair` drives. `apply.py` takes `commands.Command` objects and a
`Design`, and is what the document service and the chat agent drive.

They overlap on geometry, which is a real duplication and should converge --
see AGENT_FINDINGS.md. The convergence has started at the sharpest edge: the
graph-aware wall move lives here as `drag_attached` and `apply.py` imports it
rather than keeping a second copy, because a wall move that leaves its
neighbours behind destroys every room on the storey and both modules had to
learn that separately.

Apply agent ops to a plan — the half that was missing.

`llm.py` declared 25 ops; only the 11 spec-level ones had an applier, so the 6
furniture ops and 8 geometry ops were parsed, validated, described to the user
and then silently dropped. An op the agent can emit but the system cannot
execute is worse than no op: the model reports success and nothing changes.

This module applies the FURNITURE ops by reusing `furnish.py`'s placement
machinery, so a symbolic anchor from the agent is resolved against the real room
polygon, door swings and clearances — the same path the rule-based furnisher
takes. Coordinates are still never authored by the model.

Geometry ops are applied by `apply_geometry_ops`, which mirrors the twelve
mutations in OpenPlan3D's `project.ts` (`addWall`, `moveWallParallel`,
`splitWall`, `addDoor`, `addWindow`, `updateDoor`, `updateRoom`,
`removeElement`, ...). Mirroring rather than inventing matters: the browser
applies the same op locally for interactivity, and the two implementations have
to agree or an optimistic client edit will diverge from the server's answer.

Room polygons are RE-DERIVED after any wall change rather than patched, because
rooms are faces of the wall graph — patching a polygon while its walls moved is
how the two get out of step.
"""
from __future__ import annotations
import copy
from dataclasses import dataclass, field
from typing import Any

from .ir import Furniture, P, Plan


@dataclass
class ApplyResult:
    plan: Plan
    applied: list[str] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)
    unsupported: list[tuple[str, str]] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.applied)


def _room_by_id_or_cat(plan: Plan, key: str):
    for r in plan.rooms:
        if r.id == key:
            return r
    for r in plan.rooms:
        if (r.category or "") == key:
            return r
    # "hall" is what a client calls the living room
    alias = {"hall": "living", "master": "master_bedroom"}.get(key)
    if alias:
        for r in plan.rooms:
            if (r.category or "") == alias:
                return r
    return None


def _no_room(plan: Plan, p: dict) -> str:
    """Why the room lookup failed, in terms the agent can fix.

    "no room None" was the old message when the op named the room under some
    other key; it told the agent nothing about which key or which rooms exist.
    """
    have = ", ".join(sorted({(r.category or r.id) for r in plan.rooms}))
    got = p.get("room_id")
    if got is None:
        stray = [k for k in ("room", "in", "name", "space") if k in p]
        extra = f" (found {stray[0]}={p[stray[0]]!r} instead)" if stray else ""
        return f"no room_id given{extra}; rooms on this plan: {have}"
    return f"no room {got!r}; rooms on this plan: {have}"


def _as_placed(F, plan: Plan, room_id: str) -> list:
    """Existing furniture in a room, as obstacles the placer understands."""
    out = []
    for f in plan.furniture:
        if f.room_id != room_id or f.height == 0:
            continue          # 2D symbols are annotations, not obstacles
        w, d, h = (f.width or 600), (f.depth or 600), (f.height or 600)
        fp = F.footprint(f.position.x, f.position.y, w, d, f.rotation)
        out.append(F._Placed(spec_key=f.catalog_id, catalog_id=f.catalog_id,
                             cx=f.position.x, cy=f.position.y, rot=f.rotation,
                             w=w, d=d, h=h, fp=fp,
                             pad=fp.buffer(F.ITEM_GAP_MM, join_style=2,
                                           mitre_limit=2.0),
                             clearance=None, score=0.0, why="existing"))
    return out


def _spec_for(F, params: dict[str, Any]):
    """Agent params -> a furnish Spec. Only symbolic fields are accepted; the
    coordinate ban in `llm.py` already rejects the rest."""
    d: dict[str, Any] = {
        "key": str(params.get("item")),
        "item": str(params.get("item")),
        "anchor": str(params.get("anchor", "wall")),
    }
    for src, dst in (("of", "of"), ("side", "side"), ("count", "count"),
                     ("align", "align"), ("clear_front_mm", "clear_front"),
                     ("gap_mm", "gap"), ("abut", "abut"),
                     ("avoid_window", "avoid_window")):
        if params.get(src) is not None:
            d[dst] = params[src]
    for src, dst in (("prefer", "prefer"), ("avoid", "avoid")):
        v = params.get(src)
        if v:
            d[dst] = tuple(v) if isinstance(v, (list, tuple)) else (v,)
    return F._spec_from_dict(d)


def apply_furniture_ops(plan: Plan, ops: list[Any]) -> ApplyResult:
    """Apply the furniture ops. Returns a NEW plan; the input is untouched."""
    from . import furnish as F

    out = copy.deepcopy(plan)
    res = ApplyResult(plan=out)
    mass = F.wall_mass(out)
    walls = {w.id: w for w in out.walls}
    nid = [len(out.furniture)]

    def _emit(room, placed) -> Furniture:
        nid[0] += 1
        return Furniture(id=f"ag{nid[0]}", catalog_id=placed.catalog_id,
                         position=P(round(placed.cx), round(placed.cy)),
                         rotation=round(placed.rot, 3), width=int(placed.w),
                         depth=int(placed.d), height=int(placed.h),
                         room_id=room.id)

    def _place(room, params, desc) -> bool:
        ctx = F.build_ctx(out, room, mass, walls)
        if ctx is None:
            res.rejected.append((desc, f"{room.name} has no usable interior"))
            return False
        try:
            spec = _spec_for(F, params)
        except Exception as e:
            res.rejected.append((desc, f"bad params: {type(e).__name__}: {e}"))
            return False
        # An unknown catalogue id raises out of `catalog.dims`, and an
        # uncaught raise here took the whole batch down instead of rejecting
        # one op. The exception already names the nearest real ids, which is
        # exactly what the agent needs to retry, so pass it through verbatim.
        try:
            placed, why = F._try_place(ctx, spec, _as_placed(F, out, room.id))
        except Exception as e:
            res.rejected.append((desc, f"{type(e).__name__}: {e}"))
            return False
        if placed is None:
            res.rejected.append((desc, f"nowhere it fits: {why}"))
            return False
        out.furniture.append(_emit(room, placed))
        return True

    for op in ops:
        name = getattr(op, "op", "")
        p = getattr(op, "params", {}) or {}
        desc = getattr(op, "description", "") or name

        if name == "place_item":
            room = _room_by_id_or_cat(out, str(p.get("room_id", "")))
            if room is None:
                res.rejected.append((desc, _no_room(out, p))); continue
            n = int(p.get("count") or 1)
            ok = sum(1 for _ in range(n) if _place(room, p, desc))
            if ok:
                res.applied.append(f"{desc} ({ok} placed in {room.name})")

        elif name == "remove_item":
            iid = str(p.get("item_id", ""))
            before = len(out.furniture)
            out.furniture = [f for f in out.furniture
                             if f.id != iid and f.catalog_id != iid]
            gone = before - len(out.furniture)
            (res.applied if gone else res.rejected).append(
                f"{desc} ({gone} removed)" if gone else (desc, f"no item {iid!r}"))

        elif name in ("move_item", "replace_item"):
            iid = str(p.get("item_id", ""))
            item = next((f for f in out.furniture
                         if f.id == iid or f.catalog_id == iid), None)
            if item is None:
                res.rejected.append((desc, f"no item {iid!r}")); continue
            room = next((r for r in out.rooms if r.id == item.room_id), None)
            if room is None:
                res.rejected.append((desc, "the item is not in a room")); continue
            out.furniture = [f for f in out.furniture if f is not item]
            np = dict(p)
            np["item"] = p.get("item") or item.catalog_id
            np.setdefault("anchor", "wall")
            if _place(room, np, desc):
                res.applied.append(f"{desc} (re-placed in {room.name})")
            else:
                out.furniture.append(item)          # put it back, unchanged

        elif name == "furnish_room":
            room = _room_by_id_or_cat(out, str(p.get("room_id", "")))
            if room is None:
                res.rejected.append((desc, _no_room(out, p))); continue
            pol: dict[str, Any] = {}
            if p.get("density"):
                pol["density"] = p["density"]
            for k in ("add", "drop", "swap"):
                if p.get(k):
                    pol[k] = p[k]
            try:
                fresh, rep = F.furnish(out, policy=pol or None, seed=0)
            except Exception as e:
                res.rejected.append((desc, f"{type(e).__name__}: {e}")); continue
            keep = [f for f in out.furniture if f.room_id != room.id]
            new = [f for f in fresh.furniture if f.room_id == room.id]
            out.furniture = keep + new
            res.applied.append(f"{desc} ({len(new)} items in {room.name})")

        elif name == "set_kitchen_layout":
            room = _room_by_id_or_cat(out, str(p.get("room_id", "kitchen")))
            if room is None:
                res.rejected.append((desc, "no kitchen")); continue
            pol = {"kitchen": {k: v for k, v in (
                ("counter_run", p.get("run")), ("hob_zone", p.get("hob_zone")),
                ("sink_zone", p.get("sink_zone")),
                ("fridge_zone", p.get("fridge_zone")),
                ("breakfast_counter", p.get("breakfast_counter"))) if v is not None}}
            try:
                fresh, rep = F.furnish(out, policy=pol, seed=0)
            except Exception as e:
                res.rejected.append((desc, f"{type(e).__name__}: {e}")); continue
            keep = [f for f in out.furniture if f.room_id != room.id]
            new = [f for f in fresh.furniture if f.room_id == room.id]
            out.furniture = keep + new
            res.applied.append(f"{desc} ({len(new)} items; run={p.get('run')}, "
                               f"hob={p.get('hob_zone')}, sink={p.get('sink_zone')})")

        elif name in ("update_wall", "move_wall_parallel", "split_wall",
                      "add_wall", "add_door", "add_window", "update_door",
                      "update_window", "update_room", "remove_element"):
            res.unsupported.append(
                (name, "geometry op; route it through apply_geometry_ops"))
        else:
            res.rejected.append((desc, f"not a furniture op: {name!r}"))
    return res

# ------------------------------------------------------------------- geometry
# Mirrors OpenPlan3D's project.ts. Every mutation is expressed symbolically by
# the agent -- a compass direction and a distance, a normalised position along a
# wall -- and turned into millimetres here, so the coordinate ban holds.
_DIR_VEC = {"N": (0, 1), "S": (0, -1), "E": (1, 0), "W": (-1, 0),
            "NE": (0.7071, 0.7071), "NW": (-0.7071, 0.7071),
            "SE": (0.7071, -0.7071), "SW": (-0.7071, -0.7071)}
# Defaults from the fork's own door/window palette.
_DOOR_W = {"single": 900, "double": 1500, "sliding": 1800, "french": 1500,
           "pocket": 900, "bifold": 1800, "opening": 1000, "garage": 2400}
_WIN_W = {"standard": 1200, "fixed": 1000, "casement": 800, "sliding": 1800,
          "bay": 2000}
_WIN_H = {"standard": 1200, "fixed": 1000, "casement": 1300, "sliding": 1200,
          "bay": 1500}
JAMB_MM = 100          # clear either side of an opening within its wall


def on_segment(px: int, py: int, w, tol: int = 30) -> bool:
    """Is (px,py) on wall w's centreline, endpoints included?"""
    ax, ay, bx, by = w.start.x, w.start.y, w.end.x, w.end.y
    ux, uy = bx - ax, by - ay
    L2 = ux * ux + uy * uy
    if L2 == 0:
        return abs(px - ax) <= tol and abs(py - ay) <= tol
    t = ((px - ax) * ux + (py - ay) * uy) / L2
    if t < -tol / (L2 ** 0.5) or t > 1 + tol / (L2 ** 0.5):
        return False
    cx, cy = ax + ux * t, ay + uy * t
    return (px - cx) ** 2 + (py - cy) ** 2 <= tol * tol


def collinear(a, b, tol: int = 30) -> bool:
    return on_segment(a.start.x, a.start.y, b, tol) and \
        on_segment(a.end.x, a.end.y, b, tol)


def drag_attached(plan: Plan, w, dx: int, dy: int) -> int:
    """Translate w by (dx,dy) and take every wall END that stood on it along.

    The solver emits long spanning walls with T-junctions mid-span, so shared
    endpoints are the exception, not the rule -- an earlier version only dragged
    coincident endpoints, reported "0 junctions followed", and tore the hall
    open. What matters is whether an end LIES ON the moved wall.
    """
    a0, b0 = (w.start.x, w.start.y), (w.end.x, w.end.y)
    attached: list[tuple[Any, str]] = []
    for o in plan.walls:
        if o is w or collinear(o, w):
            continue
        if on_segment(o.start.x, o.start.y, w):
            attached.append((o, "start"))
        if on_segment(o.end.x, o.end.y, w):
            attached.append((o, "end"))
    w.start = P(w.start.x + dx, w.start.y + dy)
    w.end = P(w.end.x + dx, w.end.y + dy)
    for o, which in attached:
        pt = o.start if which == "start" else o.end
        np_ = P(pt.x + dx, pt.y + dy)
        if which == "start":
            o.start = np_
        else:
            o.end = np_
    del a0, b0
    return len(attached)


def _face_ids(plan: Plan) -> set[str]:
    return {r.id for r in plan.rooms if len(r.polygon) >= 3}


def _next_id(existing: set[str], prefix: str) -> str:
    n = 0
    while f"{prefix}{n}" in existing:
        n += 1
    return f"{prefix}{n}"


def _rederive_rooms(plan: Plan) -> None:
    """Rooms are faces of the wall graph, so recompute rather than patch.

    Not `project._derive_room_polygons`: that one matches unclaimed faces to
    rooms by comparing face area against the room's STORED area, which is
    correct on the Project read-back path (nothing has moved) and wrong here
    (the stored area is what the room measured before the edit). On two
    similar rooms either side of a moved partition it swapped their labels --
    bath1 and bath2 traded places and both appeared to grow.

    Identity after a wall move follows POSITION, so match on the old centroid
    and recompute the area from the new face.
    """
    from shapely.geometry import LineString, Point
    from shapely.ops import polygonize, unary_union

    from shapely.geometry import Polygon

    was = {}
    for r in plan.rooms:
        if len(r.polygon) >= 3:
            c = Point(*Polygon([(q.x, q.y) for q in r.polygon]).centroid.coords[0])
            was[r.id] = c
        r.polygon = []

    segs = {w.id: LineString([w.start.as_tuple(), w.end.as_tuple()])
            for w in plan.walls if w.length > 0}
    if not segs:
        return
    faces = [f for f in polygonize(unary_union(list(segs.values()))) if f.area > 1e4]
    if not faces:
        return

    used: set[int] = set()

    def claim(r, i) -> None:
        used.add(i)
        f = faces[i]
        r.polygon = [P(round(x), round(y)) for x, y in f.exterior.coords[:-1]]
        r.area = int(round(f.area))          # derived, so never carried over

    # 1. the face that still contains where the room used to be
    for r in plan.rooms:
        c = was.get(r.id)
        if c is None:
            continue
        hit = next((i for i, f in enumerate(faces)
                    if i not in used and f.contains(c)), None)
        if hit is not None:
            claim(r, hit)

    # 2. the room's own wall set, for rooms whose centroid fell outside
    for r in plan.rooms:
        if len(r.polygon) >= 3:
            continue
        own = [segs[wid] for wid in r.wall_ids if wid in segs]
        if not own:
            continue
        best, score = None, 0.35
        for i, f in enumerate(faces):
            if i in used:
                continue
            bnd = f.boundary.buffer(30.0)
            cov = sum(l.length for l in own if bnd.covers(l)) / max(
                f.boundary.length, 1.0)
            if cov > score:
                best, score = i, cov
        if best is not None:
            claim(r, best)

    # 3. nearest remaining face to where the room was
    for r in plan.rooms:
        if len(r.polygon) >= 3:
            continue
        left = [i for i in range(len(faces)) if i not in used]
        if not left:
            break
        c = was.get(r.id)
        if c is None:
            continue
        claim(r, min(left, key=lambda i: faces[i].centroid.distance(c)))


def apply_geometry_ops(plan: Plan, ops: list[Any]) -> ApplyResult:
    """Apply the geometry ops. Returns a NEW plan; the input is untouched."""
    import math

    from .ir import Opening, Wall

    out = copy.deepcopy(plan)
    res = ApplyResult(plan=out)
    touched_walls = False

    def W(wid: str):
        return next((w for w in out.walls if w.id == wid), None)

    for op in ops:
        name = getattr(op, "op", "")
        p = getattr(op, "params", {}) or {}
        desc = getattr(op, "description", "") or name

        if name == "update_wall":
            w = W(str(p.get("wall_id", "")))
            if w is None:
                res.rejected.append((desc, f"no wall {p.get('wall_id')!r}")); continue
            if p.get("thickness_mm"):
                w.thickness = int(p["thickness_mm"])
            if p.get("height_mm"):
                w.height = int(p["height_mm"])
            res.applied.append(desc); touched_walls = True

        elif name == "move_wall_parallel":
            w = W(str(p.get("wall_id", "")))
            if w is None:
                res.rejected.append((desc, f"no wall {p.get('wall_id')!r}")); continue
            v = _DIR_VEC.get(str(p.get("direction", "")).upper())
            if v is None:
                res.rejected.append(
                    (desc, f"direction must be one of {sorted(_DIR_VEC)}")); continue
            d = int(p.get("distance_mm") or 0)
            if not d:
                res.rejected.append((desc, "distance_mm is zero")); continue
            # Only the component perpendicular to the wall moves it; a wall slid
            # along its own axis is not moved, it is just relabelled.
            ux, uy = w.end.x - w.start.x, w.end.y - w.start.y
            L = math.hypot(ux, uy) or 1.0
            nx, ny = -uy / L, ux / L
            proj = v[0] * nx + v[1] * ny
            if abs(proj) < 1e-6:
                res.rejected.append(
                    (desc, "that direction runs along the wall, so it would not "
                           "move it")); continue
            sgn = 1 if proj > 0 else -1
            dx, dy = round(nx * d * sgn), round(ny * d * sgn)
            moved = drag_attached(out, w, dx, dy)
            res.applied.append(f"{desc} ({d} mm, {moved} attached end(s) followed)")
            touched_walls = True

        elif name == "split_wall":
            w = W(str(p.get("wall_id", "")))
            if w is None:
                res.rejected.append((desc, f"no wall {p.get('wall_id')!r}")); continue
            t = float(p.get("at", 0.5))
            if not (0.02 < t < 0.98):
                res.rejected.append((desc, "`at` must be strictly inside 0..1")); continue
            mx = round(w.start.x + (w.end.x - w.start.x) * t)
            my = round(w.start.y + (w.end.y - w.start.y) * t)
            nid = _next_id({x.id for x in out.walls}, "w")
            tail = Wall(id=nid, start=P(mx, my), end=w.end,
                        thickness=w.thickness, height=w.height)
            w.end = P(mx, my)
            out.walls.append(tail)
            # Both halves need re-normalising, not just the far one: the stub
            # keeps id `w` but is now t x shorter, so an opening at 0.4 of the
            # old wall sits at 0.8 of the stub. Missing this slides every
            # opening before the cut towards the corner.
            for o in out.openings:
                if o.wall_id != w.id:
                    continue
                if o.position > t:
                    o.wall_id = nid
                    o.position = min(1.0, max(0.0, (o.position - t) / (1.0 - t)))
                else:
                    o.position = min(1.0, max(0.0, o.position / t))
            res.applied.append(f"{desc} (new wall {nid})"); touched_walls = True

        elif name == "add_wall":
            # Symbolic endpoints only: "w7:start", "w7:end", "w7@0.5".
            def _ref(sref: str):
                sref = str(sref)
                if "@" in sref:
                    wid, frac = sref.split("@", 1)
                    ww = W(wid)
                    if ww is None:
                        return None
                    f = float(frac)
                    return P(round(ww.start.x + (ww.end.x - ww.start.x) * f),
                             round(ww.start.y + (ww.end.y - ww.start.y) * f))
                if ":" in sref:
                    wid, which = sref.split(":", 1)
                    ww = W(wid)
                    if ww is None:
                        return None
                    return ww.start if which == "start" else ww.end
                return None
            a, b = _ref(p.get("start_ref")), _ref(p.get("end_ref"))
            if a is None or b is None:
                res.rejected.append(
                    (desc, "endpoints must be symbolic refs like 'w7:start' or "
                           "'w7@0.5' naming existing walls")); continue
            if a.as_tuple() == b.as_tuple():
                res.rejected.append((desc, "zero-length wall")); continue
            th = int(p.get("thickness_mm") or
                     (out.walls[0].thickness if out.walls else 115))
            nid = _next_id({x.id for x in out.walls}, "w")
            out.walls.append(Wall(id=nid, start=a, end=b, thickness=th,
                                  height=out.storey_height))
            res.applied.append(f"{desc} ({nid})"); touched_walls = True

        elif name in ("add_door", "add_window"):
            w = W(str(p.get("wall_id", "")))
            if w is None:
                res.rejected.append((desc, f"no wall {p.get('wall_id')!r}")); continue
            pos = float(p.get("position", 0.5))
            if not (0.0 <= pos <= 1.0):
                res.rejected.append((desc, "position must be 0..1")); continue
            is_win = name == "add_window"
            # `add_door` declares `door_type` (the editor's own name for it) and
            # `add_window` declares `type`. Accept either on both rather than
            # silently defaulting a door the agent explicitly typed.
            kind = str(p.get("type") or p.get("door_type")
                       or ("standard" if is_win else "single"))
            width = int(p.get("width_mm") or
                        (_WIN_W if is_win else _DOOR_W).get(kind, 900))
            if width + 2 * JAMB_MM > w.length:
                res.rejected.append(
                    (desc, f"a {width} mm opening plus jambs does not fit a "
                           f"{w.length:.0f} mm wall")); continue
            nid = _next_id({o.id for o in out.openings}, "o")
            sill = int(p.get("sill_mm") or (900 if is_win else 0))
            head = sill + int(p.get("height_mm") or
                              (_WIN_H.get(kind, 1200) if is_win else 2100))
            out.openings.append(Opening(
                id=nid, kind=("window" if is_win else "door"), wall_id=w.id,
                position=pos, width=width, sill=sill, head=head))
            res.applied.append(f"{desc} ({nid}, {width} mm on {w.id})")

        elif name in ("update_door", "update_window"):
            oid = str(p.get("door_id") or p.get("window_id") or p.get("opening_id", ""))
            o = next((x for x in out.openings if x.id == oid), None)
            if o is None:
                res.rejected.append((desc, f"no opening {oid!r}")); continue
            if p.get("width_mm"):
                w = W(o.wall_id)
                nw = int(p["width_mm"])
                if w is not None and nw + 2 * JAMB_MM > w.length:
                    res.rejected.append(
                        (desc, f"{nw} mm plus jambs exceeds its {w.length:.0f} mm "
                               "wall")); continue
                o.width = nw
            if p.get("position") is not None:
                o.position = min(1.0, max(0.0, float(p["position"])))
            if p.get("sill_mm") is not None:
                o.sill = int(p["sill_mm"])
            if p.get("height_mm"):
                o.head = o.sill + int(p["height_mm"])
            res.applied.append(desc)

        elif name == "update_room":
            r = _room_by_id_or_cat(out, str(p.get("room_id", "")))
            if r is None:
                res.rejected.append((desc, _no_room(out, p))); continue
            if p.get("name"):
                r.name = str(p["name"])
            if p.get("category"):
                from . import roomtypes as rt
                c = str(p["category"])
                if c not in rt.T:
                    res.rejected.append((desc, f"unknown room type {c!r}")); continue
                r.category = c
            res.applied.append(desc)

        elif name == "remove_element":
            eid = str(p.get("element_id", ""))
            nb = len(out.walls) + len(out.openings) + len(out.furniture)
            # An opening on a removed wall would be orphaned, so take both.
            out.openings = [o for o in out.openings
                            if o.id != eid and o.wall_id != eid]
            out.walls = [w for w in out.walls if w.id != eid]
            out.furniture = [f for f in out.furniture if f.id != eid]
            na = len(out.walls) + len(out.openings) + len(out.furniture)
            if na == nb:
                res.rejected.append((desc, f"nothing with id {eid!r}"))
            else:
                res.applied.append(f"{desc} ({nb - na} element(s))")
                touched_walls = True
        else:
            res.rejected.append((desc, f"not a geometry op: {name!r}"))

    if touched_walls:
        _rederive_rooms(out)
    return res


# Ops that can tear the wall graph, so each one is checked against the room
# faces it leaves behind rather than trusted.
_RISKY = ("move_wall_parallel", "add_wall", "remove_element", "update_wall")


def apply_geometry_ops_guarded(plan: Plan, ops: list[Any]) -> ApplyResult:
    """`apply_geometry_ops`, but a wall edit that destroys a room is reverted.

    Losing a face means two rooms merged or one vanished -- the plan is no
    longer the thing the agent was editing. Reverting and saying so gives the
    agent something it can act on; letting it through gives it a plan whose
    room list quietly disagrees with its walls.
    """
    cur = plan
    applied: list[str] = []
    rejected: list[tuple[str, str]] = []
    for op in ops:
        r = apply_geometry_ops(cur, [op])
        if r.rejected or not r.applied:
            rejected += r.rejected
            continue
        if getattr(op, "op", "") in _RISKY:
            before, after = _faces_of(cur), _face_ids(r.plan)
            lost = before - after
            if lost:
                rejected.append((
                    getattr(op, "description", "") or getattr(op, "op", ""),
                    f"would destroy room(s) {sorted(lost)}: the wall carries "
                    f"their enclosure. Move a different wall, or delete those "
                    f"rooms first if that is the intent."))
                continue
        cur, applied = r.plan, applied + r.applied
    return ApplyResult(plan=cur, applied=applied, rejected=rejected)


def _faces_of(plan: Plan) -> set[str]:
    return {r.id for r in plan.rooms if len(r.polygon) >= 3}


def apply_ops(plan: Plan, ops: list[Any]) -> ApplyResult:
    """Apply furniture and geometry ops together, geometry first.

    Geometry first because moving a wall changes the room a piece of furniture
    has to fit in; furnishing against the old shape and then moving the wall
    leaves items inside walls.
    """
    geo = [o for o in ops if getattr(o, "level", "") == "geometry"]
    fur = [o for o in ops if getattr(o, "level", "") == "furniture"]
    other = [o for o in ops if getattr(o, "level", "") not in ("geometry", "furniture")]

    cur = plan
    applied: list[str] = []
    rejected: list[tuple[str, str]] = []
    if geo:
        r = apply_geometry_ops_guarded(cur, geo)
        cur, applied, rejected = r.plan, applied + r.applied, rejected + r.rejected
    if fur:
        r = apply_furniture_ops(cur, fur)
        cur, applied, rejected = r.plan, applied + r.applied, rejected + r.rejected
    out = ApplyResult(plan=cur, applied=applied, rejected=rejected)
    out.unsupported = [(getattr(o, "op", "?"), "spec-level; use apply_spec_ops")
                       for o in other]
    return out


# ─────────────────────────────────────────────── validation attached to actions
# Checks are post-conditions of the op that could break them, not tools the
# agent has to remember to call. The mapping is deliberately narrow: running
# all ten families after moving one window buries the one finding that matters
# under nine the agent did not cause and cannot fix.
OP_CHECKS: dict[str, tuple[str, ...]] = {
    # An opening changes light, ventilation and the door graph, nothing else.
    "add_window":        ("GEO", "NBC", "DESIGN"),
    "update_window":     ("GEO", "NBC", "DESIGN"),
    "add_door":          ("GEO", "NBC", "TOPO", "SYNTAX", "DESIGN"),
    "update_door":       ("GEO", "NBC", "TOPO", "SYNTAX", "DESIGN"),
    # A wall edit resizes rooms, so almost everything is downstream of it.
    "add_wall":          ("GEO", "NBC", "BYLAW", "DESIGN", "TOPO", "ZONE", "SYNTAX"),
    "move_wall_parallel": ("GEO", "NBC", "BYLAW", "DESIGN", "TOPO", "ZONE", "SYNTAX"),
    "split_wall":        ("GEO", "TOPO", "SYNTAX"),
    "update_wall":       ("GEO", "NBC", "BYLAW"),
    "remove_element":    ("GEO", "NBC", "BYLAW", "DESIGN", "TOPO", "ZONE", "SYNTAX"),
    # Relabelling a room changes what it is REQUIRED to be, not its shape.
    "update_room":       ("TYPO", "TOPO", "ZONE", "VASTU", "BRIEF", "NBC"),
    # Furniture is judged on clearance and on the brief's must-place list.
    "place_item":        ("DESIGN", "BRIEF"),
    "move_item":         ("DESIGN", "BRIEF"),
    "remove_item":       ("DESIGN", "BRIEF"),
    "replace_item":      ("DESIGN", "BRIEF"),
    "furnish_room":      ("DESIGN", "BRIEF"),
    "set_kitchen_layout": ("DESIGN", "VASTU", "BRIEF"),
}


@dataclass
class CheckedResult(ApplyResult):
    """An ApplyResult that also carries what the edit did to the rule set."""
    warnings: list[str] = field(default_factory=list)
    reverted: list[tuple[str, list[str]]] = field(default_factory=list)


def _families_for(op: Any) -> tuple[str, ...]:
    return OP_CHECKS.get(getattr(op, "op", ""), ())


def _findings(plan: Plan, fams: tuple[str, ...], brief, profile, rules):
    """Run only `fams`, honouring the caller's RuleConfig on top."""
    from .policy import RuleConfig
    from .rules import validate
    base = rules if rules is not None else RuleConfig()
    sub = copy.deepcopy(base)
    sub.families = {f: (f in fams and base.families.get(f, True))
                    for f in set(list(base.families) + list(fams))}
    return validate(plan, brief, profile, rules=sub)


def _key(f) -> tuple:
    return (f.rule_id, tuple(f.element_ids))


def apply_checked(plan: Plan, ops: list[Any], *, brief: dict | None = None,
                  profile: Any = None, rules: Any = None,
                  revert_on_error: bool = True) -> CheckedResult:
    """Apply ops one at a time, each with its own checks as post-conditions.

    Two rules, which is what the design asks for:
      * a NEW error caused by this op reverts it, and the agent is told which
        rule and why -- so the plan it holds is never in a state the validator
        rejects for a reason the agent just created;
      * a new warning does not block anything, it is reported.

    Only findings the op INTRODUCED count. A plan that already has a narrow
    corridor must not make every later edit unapplicable -- that turns a
    pre-existing defect into a freeze, and the agent has no move that clears it.
    """
    cur = plan
    out = CheckedResult(plan=cur)
    for op in ops:
        name = getattr(op, "op", "")
        desc = getattr(op, "description", "") or name
        level = getattr(op, "level", "")
        fams = _families_for(op)
        before = {_key(f) for f in _findings(cur, fams, brief, profile, rules)} \
            if fams else set()

        if not level:                       # trust the op table over the caller
            from .llm import OP_TABLE
            level = (OP_TABLE.get(name) or {}).get("level", "")
        if level == "geometry":
            r = apply_geometry_ops(cur, [op])
        elif level == "furniture":
            r = apply_furniture_ops(cur, [op])
        else:
            out.unsupported.append(
                (name, f"level {level or 'unknown'!r}: spec ops change the "
                       "brief and need a re-solve, not an edit"))
            continue

        out.rejected += r.rejected
        out.unsupported += r.unsupported
        if not r.applied:
            continue

        if not fams:
            cur, out.applied = r.plan, out.applied + r.applied
            continue

        after = _findings(r.plan, fams, brief, profile, rules)
        new = [f for f in after if _key(f) not in before]
        errs = [f for f in new if f.severity == "error"]
        if errs and revert_on_error:
            out.reverted.append((desc, [f"{f.rule_id}: {f.detail}" for f in errs]))
            out.rejected.append((desc, "; ".join(
                f"{f.rule_id}: {f.detail}" for f in errs[:3])))
            continue
        cur = r.plan
        out.applied += r.applied
        out.warnings += [f"{f.rule_id}: {f.detail}" for f in new
                         if f.severity == "warn"]
    out.plan = cur
    return out
