"""Validator / rules engine over the canonical IR.

Runs inside a generate/critique loop, so the whole thing is one pass with a
single shared geometry context (`_Ctx`); measured 1.4 ms median for a 10-room
ResPlan plan (see tests/test_rules.py for the benchmark).

Four families, all reporting through one `Finding` record:
  (a) GEO.*   geometry/topology sanity - always an error when it fires, because
              a plan that fails these is not a building at all.
  (b) NBC.*   NBC 2016 Part 3 dimensional minima.
  (c) BYLAW.* jurisdictional envelope / coverage / FAR, from a `CityProfile`.
  (d) VASTU.* weighted, never an error, and continuous - see `vastu_score`.

"Minimum width" (NBC) is NOT the bounding-box width. It is measured here as the
**largest inscribed clear span**: erode the room polygon with mitred joins and
binary-search the radius at which it vanishes; width = 2r. For a rectangle this
returns the short side exactly; for an L-shaped room it returns the widest
clear span, which is what NBC's "minimum width" is protecting (a usable
dimension somewhere in the room), not the width of the narrowest nook.
Passage width is a *different* question - a pinch point defeats a corridor even
if it is wide elsewhere - so passages use an erosion-*connectivity* test:
erode by half the required width and check every doorway on the passage still
lies on one connected component.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, replace
from typing import Literal, Optional, Any

from shapely.geometry import Polygon, LineString, Point, MultiPolygon
from shapely.ops import unary_union, polygonize
from shapely.prepared import prep

from .ir import Plan, Wall, Room
from .bylaws import (CityProfile, PlotBand, VastuRule, BENGALURU,
                     DIRECTIONS, CENTRE, SQFT_M2)

Severity = Literal["error", "warn"]

# --- tolerances. Chosen against the corpus, not by taste. --------------------
MIN_WALL_MM = 100              # spec: walls shorter than this are modelling noise
DEGENERATE_AREA_MM2 = 250_000  # 0.25 m^2 - below any real room
OVERLAP_TOL_FRAC = 0.01        # room-room overlap under 1% of the smaller room
OVERLAP_TOL_MM2 = 50_000       # ...or under 0.05 m^2, is a shared-wall artefact
OUTSIDE_TOL_FRAC = 0.02        # 2% of a room may fall outside the wall envelope
ENVELOPE_SLOP_MM = 60          # wall-graph face union is grown by this before test
OPENING_WIDTH_SLACK_MM = 20    # an opening may equal its wall length, not exceed
DOOR_PROBE_MM = 25             # how far past a wall centreline we look for a room

# NB: "hall" is deliberately absent. In Indian usage "Hall" is the living room,
# not a corridor, so matching it would apply the 900 mm passage rule to the
# largest habitable room in the plan.
_PASSAGE_RE = re.compile(r"passage|corridor|hallway|lobby|circulation", re.I)
_POOJA_RE = re.compile(r"pooja|puja|mandir|prayer", re.I)
_STAIR_RE = re.compile(r"stair|staircase", re.I)
_MASTER_RE = re.compile(r"master", re.I)
_WC_RE = re.compile(r"\bwc\b|water closet|toilet", re.I)

# NBC 2016 Part 3: a habitable room is one used for living, sleeping, dining or
# study. These are the BASE categories; use `_is_habitable` to test a room,
# because a plan carries subtypes. Must agree with `envelope.NBC_MIN` and
# `standards.HABITABLE_VENT` -- a room habitable for glazing and not for area
# is a table drift, not a design.
#
# `kitchen` and `pooja` are deliberately absent: the kitchen has its own branch
# with its own minima, and a pooja room is `klass="habitable"` in the taxonomy,
# which is a different question from NBC's.
HABITABLE = {"living", "bedroom", "dining", "study", "servant"}
# `storage` is not a taxonomy key -- the room type is `store` -- so this set
# matched balconies and nothing else, and the softening its own use site
# describes ("balconies and stores are routinely entered through a sliding
# unit the source data labels a window, so they only ever warn") never applied
# to a store. Both spellings, and read through `_is_soft_entry` so subtypes
# count.
NON_HABITABLE = {"balcony", "store", "storage"}


def _is_habitable(category: str) -> bool:
    """Is this room habitable under NBC, subtypes included?

    A plain `category in HABITABLE` skipped `master_bedroom` entirely -- no
    minimum area, no minimum width, no ceiling height -- on every plan the
    system has ever produced. It also skipped `guest_bedroom`. The most
    important room in an Indian plan was the one room with no floor under it.
    """
    from . import roomtypes as rt
    return any(k in HABITABLE for k in rt.counts_as(category))
# Must have a real door, never an arch. Base categories only -- test with
# `_is_private`, because a flat `category in PRIVATE` misses `master_bedroom`,
# `guest_bedroom`, `toilet` and `wc`. That exempted the MASTER bedroom from the
# privacy rule written for bedrooms: a sealed-off master with its own en-suite
# reported `warn`, so a plan whose main bedroom cannot be entered at all read
# as advisory. Same shape as the `_is_habitable` bug above, in a second table.
PRIVATE = {"bedroom", "bathroom"}


def _is_private(category: str) -> bool:
    from . import roomtypes as rt
    return any(k in PRIVATE for k in rt.counts_as(category))


def _is_soft_entry(category: str) -> bool:
    """A room the corpus routinely enters through something not modelled as a
    door, so an absent door is a warning rather than a defect."""
    from . import roomtypes as rt
    return any(k in NON_HABITABLE for k in rt.counts_as(category))
# Shared boundary above which a doorless room is read as an open threshold
# (open-plan kitchen, dining split, entry arch) instead of an isolated room.
# 1200 mm is the narrowest arch that gets built: a door leaf is 900 mm, so a
# lower bar would let a bare 1 m wall stub count as an opening. Measured: at
# 2000 mm this rule called all 9 rooms of ResPlan plan 4849 landlocked because
# its entry hall meets the living room across a 1563 mm arch.
OPEN_THRESHOLD_MM = 1200


# ------------------------------------------------------------------ finding ---

@dataclass
class Finding:
    rule_id: str
    severity: Severity
    weight: float
    detail: str
    element_ids: list[str] = field(default_factory=list)
    measured: float | None = None
    required: float | None = None

    def __repr__(self) -> str:                      # terse; these get printed a lot
        m = "" if self.measured is None else f" [{self.measured:g}"
        m += "" if self.required is None else f" vs {self.required:g}"
        m += "]" if self.measured is not None else ""
        return f"<{self.severity[0].upper()} {self.rule_id} w={self.weight:.2f}{m}>"


_SEV_ORDER = {"error": 0, "warn": 1}


def sort_findings(fs: list[Finding]) -> list[Finding]:
    return sorted(fs, key=lambda f: (_SEV_ORDER[f.severity], -f.weight, f.rule_id))


# --------------------------------------------------------------- geometry ----

def room_polygon(r: Room) -> Polygon | None:
    if len(r.polygon) < 3:
        return None
    p = Polygon([q.as_tuple() for q in r.polygon])
    if not p.is_valid:
        p = p.buffer(0)
        if isinstance(p, MultiPolygon):
            p = max(p.geoms, key=lambda g: g.area) if p.geoms else None
    if p is None or p.is_empty or p.area <= 0:
        return None
    return p


def _is_axis_rect(p: Polygon) -> bool:
    """Cheap fast path: 99.75% of corpus room edges are axis-aligned."""
    xs, ys = p.exterior.xy
    if len(xs) > 5:
        return False
    minx, miny, maxx, maxy = p.bounds
    bbox = (maxx - minx) * (maxy - miny)
    return bbox > 0 and abs(p.area - bbox) / bbox < 1e-3


def clear_width_mm(p: Polygon, iters: int = 12) -> float:
    """Largest inscribed clear span, in mm (see module docstring for the choice).

    Erosion with mitred joins so convex corners are not rounded off: a 2400 mm
    square erodes to nothing at exactly r = 1200, giving width 2400.
    """
    minx, miny, maxx, maxy = p.bounds
    hi = min(maxx - minx, maxy - miny) / 2.0
    if hi <= 0:
        return 0.0
    if _is_axis_rect(p):
        return 2.0 * hi
    lo = 0.0
    for _ in range(iters):
        mid = (lo + hi) / 2.0
        e = p.buffer(-mid, join_style=2, mitre_limit=2.0)
        if e.is_empty or e.area <= 0:
            hi = mid
        else:
            lo = mid
    return 2.0 * lo


def _erode_parts(p: Polygon, r: float) -> list[Polygon]:
    e = p.buffer(-r, join_style=2, mitre_limit=2.0)
    if e.is_empty:
        return []
    gs = list(e.geoms) if isinstance(e, MultiPolygon) else [e]
    return [g for g in gs if g.area > 0]


def passage_clear_width_mm(p: Polygon, door_pts: list[tuple[float, float]],
                           required_mm: float) -> float:
    """Width of the *narrowest* point a walker must pass through, in mm.

    Semantics differ from `clear_width_mm` on purpose: a corridor with one
    820 mm pinch fails even if it is 1.5 m wide elsewhere. Implemented as an
    erosion-connectivity test - erode by r and require every doorway to still
    attach to one component - then binary-searched on r. With <2 doorways the
    connectivity question is vacuous and we fall back to the inscribed span.
    """
    if len(door_pts) < 2:
        return clear_width_mm(p)

    def passable(r: float) -> bool:
        parts = _erode_parts(p, r)
        if not parts:
            return False
        # A doorway is "attached" to the component nearest it; reject if the
        # gap exceeds the erosion radius plus slack (i.e. the throat is blocked).
        idx = []
        for dp in door_pts:
            pt = Point(dp)
            best, bd = -1, float("inf")
            for i, g in enumerate(parts):
                d = g.distance(pt)
                if d < bd:
                    best, bd = i, d
            if best < 0 or bd > r + required_mm:
                return False
            idx.append(best)
        return len(set(idx)) == 1

    minx, miny, maxx, maxy = p.bounds
    hi = min(maxx - minx, maxy - miny) / 2.0
    if hi <= 0 or not passable(0.0):
        return 0.0
    lo = 0.0
    for _ in range(11):
        mid = (lo + hi) / 2.0
        if passable(mid):
            lo = mid
        else:
            hi = mid
    return 2.0 * lo


def wall_line(w: Wall) -> LineString:
    return LineString([w.start.as_tuple(), w.end.as_tuple()])


# ------------------------------------------------------------------ context ---

@dataclass
class _Ctx:
    plan: Plan
    profile: CityProfile
    brief: dict
    rooms: list[Room]
    polys: dict[str, Polygon]            # room id -> polygon
    walls: dict[str, Wall]
    envelope: Optional[Polygon | MultiPolygon]   # wall-graph faces, grown
    footprint: Optional[Polygon | MultiPolygon]  # outer building outline
    plot: Optional[Polygon]
    door_edges: list[tuple[str, str, str]]       # (opening_id, room_a, room_b)
    entry_rooms: list[str]
    room_doors: dict[str, list[tuple[float, float]]]   # room id -> doorway points
    centre: tuple[float, float]
    bbox: tuple[float, float, float, float]


def _build_ctx(plan: Plan, brief: dict | None, profile: CityProfile) -> _Ctx:
    rooms = plan.rooms
    polys = {}
    for r in rooms:
        p = room_polygon(r)
        if p is not None:
            polys[r.id] = p
    walls = {w.id: w for w in plan.walls}

    lines = [wall_line(w) for w in plan.walls if w.length >= 1.0]
    envelope = footprint = None
    if lines:
        noded = unary_union(lines)
        faces = [f for f in polygonize(noded) if f.area > DEGENERATE_AREA_MM2 / 10]
        if faces:
            thick = max((w.thickness for w in plan.walls), default=200)
            core = unary_union(faces)
            envelope = core.buffer(ENVELOPE_SLOP_MM, join_style=2, mitre_limit=2.0)
            footprint = core.buffer(thick / 2.0, join_style=2, mitre_limit=2.0)
    if footprint is None and polys:
        thick = max((w.thickness for w in plan.walls), default=200)
        footprint = unary_union(list(polys.values())).buffer(
            thick / 2.0, join_style=2, mitre_limit=2.0)

    plot = None
    if len(plan.site.plot_polygon) >= 3:
        pp = Polygon([q.as_tuple() for q in plan.site.plot_polygon])
        if not pp.is_valid:
            pp = pp.buffer(0)
        if isinstance(pp, MultiPolygon) and pp.geoms:
            pp = max(pp.geoms, key=lambda g: g.area)
        if pp is not None and not pp.is_empty and pp.area > 0:
            plot = pp

    door_edges, entry_rooms, room_doors = _door_topology(plan, polys, walls)

    ref = footprint if footprint is not None else (plot if plot is not None else None)
    if ref is not None and not ref.is_empty:
        c = ref.centroid
        centre, bbox = (c.x, c.y), ref.bounds
    else:
        centre, bbox = (0.0, 0.0), (0.0, 0.0, 0.0, 0.0)

    return _Ctx(plan=plan, profile=profile, brief=brief or {}, rooms=rooms,
                polys=polys, walls=walls, envelope=envelope, footprint=footprint,
                plot=plot, door_edges=door_edges, entry_rooms=entry_rooms,
                room_doors=room_doors, centre=centre, bbox=bbox)


def _door_topology(plan: Plan, polys: dict[str, Polygon], walls: dict[str, Wall]):
    """Which rooms each door joins, by probing perpendicular to the host wall.

    Probing rather than trusting `Room.wall_ids`: a large living room lists 16
    walls, so wall membership does not identify a door's two sides. Probe
    offsets cover both room conventions in the wild - IR rooms that tile at
    wall *centrelines* (the ResPlan inversion) and rooms inset to wall *faces*.
    """
    items = list(polys.items())
    preps = [(rid, prep(p)) for rid, p in items]

    def room_at(x: float, y: float) -> str | None:
        pt = Point(x, y)
        for rid, pp in preps:
            if pp.contains(pt):
                return rid
        return None

    edges: list[tuple[str, str, str]] = []
    entries: list[str] = []
    room_doors: dict[str, list[tuple[float, float]]] = {}
    for op in plan.openings:
        if op.kind not in ("door", "front_door"):
            continue
        w = walls.get(op.wall_id)
        if w is None:
            continue
        L = w.length
        if L < 1.0:
            continue
        dx, dy = w.end.x - w.start.x, w.end.y - w.start.y
        ux, uy = dx / L, dy / L
        t = min(max(op.position, 0.0), 1.0)
        px, py = w.start.x + t * dx, w.start.y + t * dy
        sides: list[str | None] = []
        for sgn in (1.0, -1.0):
            hit = None
            for off in (DOOR_PROBE_MM, w.thickness / 2.0 + DOOR_PROBE_MM,
                        w.thickness + DOOR_PROBE_MM):
                hit = room_at(px - sgn * uy * off, py + sgn * ux * off)
                if hit is not None:
                    break
            sides.append(hit)
        a, b = sides
        for rid in (a, b):
            if rid is not None:
                room_doors.setdefault(rid, []).append((px, py))
        if a is not None and b is not None and a != b:
            edges.append((op.id, a, b))
        elif op.kind == "front_door":
            inner = a if a is not None else b
            if inner is not None:
                entries.append(inner)
        elif (a is None) != (b is None):
            # an exterior door that is not tagged front_door still lets you in
            entries.append(a if a is not None else b)          # type: ignore[arg-type]
    return edges, entries, room_doors


# ------------------------------------------------- (a) geometry / topology ---

def check_geometry(ctx: _Ctx) -> list[Finding]:
    out: list[Finding] = []
    plan = ctx.plan

    if not ctx.polys:
        # Explicit, because the generate loop can emit a wall network with no
        # faces and every other rule would then silently report nothing.
        out.append(Finding("GEO.NO_ROOMS", "error", 1.0,
                           f"plan has no room with a valid polygon "
                           f"({len(plan.rooms)} rooms declared)", []))

    # degenerate / missing polygons
    for r in ctx.rooms:
        p = ctx.polys.get(r.id)
        if p is None:
            out.append(Finding("GEO.ROOM_DEGENERATE", "error", 1.0,
                               f"room {r.name!r} has no valid polygon "
                               f"({len(r.polygon)} vertices)", [r.id]))
            continue
        if p.area < DEGENERATE_AREA_MM2:
            out.append(Finding("GEO.ROOM_DEGENERATE", "error", 1.0,
                               f"room {r.name!r} area {p.area/1e6:.3f} m^2 is "
                               f"below the {DEGENERATE_AREA_MM2/1e6:.2f} m^2 floor",
                               [r.id], p.area / 1e6, DEGENERATE_AREA_MM2 / 1e6))
        if r.area > 0 and abs(p.area - r.area) / max(p.area, 1.0) > 0.02:
            out.append(Finding("GEO.ROOM_AREA_MISMATCH", "warn", 0.4,
                               f"room {r.name!r} declares {r.area/1e6:.2f} m^2 but "
                               f"its polygon measures {p.area/1e6:.2f} m^2",
                               [r.id], p.area / 1e6, r.area / 1e6))

    # pairwise overlap. Rooms are faces of one wall graph, so any real overlap
    # is a modelling error; shared-edge slivers are filtered by both tolerances.
    ids = list(ctx.polys)
    for i in range(len(ids)):
        pi = ctx.polys[ids[i]]
        bi = pi.bounds
        for j in range(i + 1, len(ids)):
            pj = ctx.polys[ids[j]]
            bj = pj.bounds
            if bi[2] <= bj[0] or bj[2] <= bi[0] or bi[3] <= bj[1] or bj[3] <= bi[1]:
                continue
            inter = pi.intersection(pj).area
            if inter <= OVERLAP_TOL_MM2:
                continue
            if inter / min(pi.area, pj.area) <= OVERLAP_TOL_FRAC:
                continue
            out.append(Finding(
                "GEO.ROOM_OVERLAP", "error", 1.0,
                f"rooms overlap by {inter/1e6:.2f} m^2 "
                f"({100*inter/min(pi.area, pj.area):.1f}% of the smaller)",
                [ids[i], ids[j]], inter / 1e6, 0.0))

    # rooms escaping the wall envelope
    if ctx.envelope is not None:
        env = prep(ctx.envelope)
        for rid, p in ctx.polys.items():
            if env.contains(p):
                continue
            outside = p.difference(ctx.envelope).area
            if outside / p.area > OUTSIDE_TOL_FRAC:
                out.append(Finding(
                    "GEO.ROOM_OUTSIDE_ENVELOPE", "error", 0.9,
                    f"{100*outside/p.area:.1f}% of room {rid} lies outside the "
                    f"wall envelope ({outside/1e6:.2f} m^2)",
                    [rid], outside / p.area, OUTSIDE_TOL_FRAC))

    # Walls. A sub-100 mm wall cannot be built but it also does not make the
    # plan illegal, so this is a `warn`: measured 11 such stubs (60-97 mm) in
    # 200 verified ResPlan conversions, all artefacts of the 0.5-unit
    # coordinate snap. Erroring on them would reject good plans.
    for w in plan.walls:
        if w.length < MIN_WALL_MM:
            out.append(Finding("GEO.WALL_TOO_SHORT", "warn", 0.5,
                               f"wall {w.id} is {w.length:.0f} mm long",
                               [w.id], w.length, MIN_WALL_MM))

    # openings vs their host wall
    for op in plan.openings:
        w = ctx.walls.get(op.wall_id)
        if w is None:
            out.append(Finding("GEO.OPENING_ORPHAN", "error", 0.8,
                               f"opening {op.id} references missing wall "
                               f"{op.wall_id!r}", [op.id]))
            continue
        if op.width > w.length + OPENING_WIDTH_SLACK_MM:
            out.append(Finding(
                "GEO.OPENING_TOO_WIDE", "error", 0.8,
                f"{op.kind} {op.id} is {op.width} mm wide on a "
                f"{w.length:.0f} mm wall", [op.id, w.id], op.width, w.length))
        else:
            # must also fit *at its position*: centre +/- width/2 within the wall
            half = op.width / 2.0
            c = op.position * w.length
            if c - half < -OPENING_WIDTH_SLACK_MM or c + half > w.length + OPENING_WIDTH_SLACK_MM:
                out.append(Finding(
                    "GEO.OPENING_OVERRUNS_WALL", "warn", 0.5,
                    f"{op.kind} {op.id} centred at {c:.0f} mm spills past the ends "
                    f"of its {w.length:.0f} mm wall", [op.id, w.id], op.width, w.length))
        if not (-0.001 <= op.position <= 1.001):
            out.append(Finding("GEO.OPENING_OFF_WALL", "warn", 0.5,
                               f"opening {op.id} position {op.position:.3f} is off "
                               f"its host wall", [op.id], op.position, 1.0))

    out += _check_reachability(ctx)
    return out


def _check_reachability(ctx: _Ctx) -> list[Finding]:
    """Every room must be reachable from the front door through doors only.

    Windows and bare wall adjacency do not count; that is the whole point of
    the rule (a bathroom you can only see into is a plan bug, not a style).

    Two tiers, because the literal rule has a 10% false-positive rate on real
    plans. Doors-only reachability is computed first; anything it strands is
    then re-tested allowing *open thresholds* (>= `OPEN_THRESHOLD_MM` of shared
    boundary) as edges. Severity is assigned from which tier a room fails:

      error  landlocked - neither a door nor an open threshold, or
             a bedroom/bathroom with no door of its own (privacy is not
             satisfied by an arch, whatever the geometry allows);
      warn   walkable but only across an unmodelled opening - an open-plan
             kitchen or an entry arch - and balconies/stores in any case,
             since they are usually entered through a unit the data calls a
             window.

    Measured across 300 ResPlan conversions: the literal doors-only rule
    errored on 31 rooms in 10.3% of plans; every one inspected was either an
    open-plan kitchen (148 kitchens have no door polygon at all) or a room
    behind an arch (plan 4849's 1563 mm entry threshold stranded 7 rooms).
    The two-tier version errors on 8 rooms in 2.67% of plans, all
    bedrooms/bathrooms with no door edge - which is the finding we want.
    """
    out: list[Finding] = []
    if not ctx.polys:
        return out
    has_front = any(o.kind == "front_door" for o in ctx.plan.openings)
    if not has_front:
        out.append(Finding("GEO.NO_FRONT_DOOR", "error", 0.9,
                           "plan has no front_door opening", []))

    adj: dict[str, set[str]] = {rid: set() for rid in ctx.polys}
    for _oid, a, b in ctx.door_edges:
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)

    roots = [r for r in ctx.entry_rooms if r in adj]
    if not roots:
        # No usable entry: fall back to the largest room so the rule still
        # reports connectivity rather than flagging every room at once.
        if not adj:
            return out
        roots = [max(ctx.polys, key=lambda k: ctx.polys[k].area)]

    def bfs(graph: dict[str, set[str]]) -> set[str]:
        seen, stack = set(roots), list(roots)
        while stack:
            cur = stack.pop()
            for nb in graph.get(cur, ()):
                if nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        return seen

    door_seen = bfs(adj)
    if len(door_seen) == len(ctx.polys):
        return out

    # Second tier: open thresholds as edges (see the docstring for the numbers).
    focus = {rid for rid in ctx.polys if rid not in door_seen}
    shared = _open_threshold_edges(ctx, focus)
    wide = {rid: set() for rid in ctx.polys}
    for (a, b), L in shared.items():
        if L >= OPEN_THRESHOLD_MM:
            wide[a].add(b)
            wide[b].add(a)
    both = {rid: adj.get(rid, set()) | wide.get(rid, set()) for rid in ctx.polys}
    open_seen = bfs(both)

    has_door = set()
    for _oid, a, b in ctx.door_edges:
        has_door.add(a)
        has_door.add(b)

    cats = {r.id: r.category for r in ctx.rooms}
    # A room can carry a door and still be sealed off, if that door leads only
    # to another room in the same cut-off component. A master bedroom whose one
    # door opens into its own en-suite, with the pair walled away from the
    # hall, is the case that shipped: both rooms were in `has_door`, both were
    # walkable across some threshold, so both reported `warn` and a plan you
    # cannot enter the main bedroom of read as advisory.
    island = {rid for rid in ctx.polys
              if rid not in door_seen and rid in has_door}
    # Whether WE drew this plan. `_open_threshold_edges` treats a wide doorless
    # boundary as "probably a door nobody recorded", which is right for
    # ResPlan and for a hand-drawn Project and wrong for solver output, where
    # every opening is one we chose to place. Provenance is the only thing that
    # distinguishes the two, and it is already on the Plan.
    authored_here = (ctx.plan.provenance or {}).get("source") == "cpsat-solver"
    names = {r.id: r.name for r in ctx.rooms}
    for rid in ctx.polys:
        if rid in door_seen:
            continue
        widest = max((L for (a, b), L in shared.items() if rid in (a, b)),
                     default=0.0)
        if rid not in open_seen:
            # Balconies and stores are routinely entered through a sliding unit
            # the source data labels a window, so they only ever warn.
            soft = _is_soft_entry(cats.get(rid) or "")
            sev = "warn" if soft else "error"
            wt = 0.6 if soft else 1.0
            why = "landlocked: no door and no open threshold"
        elif _is_private(cats.get(rid) or "") and rid not in has_door:
            # Privacy is not negotiable: a bedroom or bathroom needs a real
            # door, so a doorless one stays an error even when it is walkable.
            sev, wt, why = "error", 1.0, "a bedroom/bathroom must have a door"
        elif _is_private(cats.get(rid) or "") and authored_here:
            # It HAS a door, and still no route from the entrance through
            # doors. On imported data that is usually an unmodelled opening --
            # erroring on it took this rule to a 10.3% plan-level false
            # positive rate on ResPlan, which is why the tier below exists.
            #
            # On our OWN output the premise does not hold. We placed every
            # opening in this plan, so a missing door is not a gap in the
            # record, it is a room you cannot walk into. The master bedroom of
            # a solved 2BHK reported `warn` on exactly this path: its one door
            # opened into its own en-suite and the pair was sealed off from the
            # hall, so `rid in has_door` was true and the plan shipped.
            sev, wt = "error", 1.0
            why = ("its only door(s) lead into the same cut-off group, so "
                   "there is no way in from the entrance"
                   if rid in island else
                   "no door on the route from the entrance, and this plan's "
                   "openings are all deliberate")
        else:
            sev, wt = "warn", 0.6
            why = (f"reached only across a {widest:.0f} mm open threshold "
                   f"(no door modelled)")
        out.append(Finding(
            "GEO.UNREACHABLE_ROOM", sev, wt,
            f"room {names.get(rid, rid)!r} ({cats.get(rid)}) is not reachable "
            f"from the entrance through doors - {why}", [rid],
            widest, float(OPEN_THRESHOLD_MM)))
    return out


def _poly_edges(p: Polygon) -> list[tuple[float, float, float, float]]:
    rings = [p.exterior] + list(p.interiors)
    out = []
    for r in rings:
        cs = list(r.coords)
        for (ax, ay), (bx, by) in zip(cs, cs[1:]):
            if ax != bx or ay != by:
                out.append((ax, ay, bx, by))
    return out


def _shared_edge_len(ea, eb, tol: float) -> float:
    """Length of boundary two rooms share, allowing `tol` of separation.

    Pure segment arithmetic rather than `buffer().intersection()`: the buffered
    version cost 2 ms/plan on its own (p99 9.8 ms, over the 10 ms budget) and
    its shared length was only an estimate. This is exact for the 99.75% of
    corpus edges that are axis-aligned and correct for the rest, and it is the
    reason a doorless-room check can sit inside a generate/critique loop.
    """
    total = 0.0
    for ax, ay, bx, by in ea:
        dx, dy = bx - ax, by - ay
        L = math.hypot(dx, dy)
        if L < 1.0:
            continue
        ux, uy = dx / L, dy / L
        nx, ny = -uy, ux
        for cx, cy, ex, ey in eb:
            fx, fy = ex - cx, ey - cy
            M = math.hypot(fx, fy)
            if M < 1.0:
                continue
            if abs(ux * fy - uy * fx) > 0.05 * M:      # not parallel (~3 deg)
                continue
            o1 = (cx - ax) * nx + (cy - ay) * ny
            o2 = (ex - ax) * nx + (ey - ay) * ny
            if abs(o1) > tol or abs(o2) > tol:
                continue
            t1 = (cx - ax) * ux + (cy - ay) * uy
            t2 = (ex - ax) * ux + (ey - ay) * uy
            lo = max(0.0, min(t1, t2))
            hi = min(L, max(t1, t2))
            if hi > lo:
                total += hi - lo
    return total


def _open_threshold_edges(ctx: _Ctx, focus: set[str]) -> dict[tuple[str, str], float]:
    """Shared boundary length, in mm, for every pair touching `focus`.

    Only pairs with an endpoint in `focus` (the door-unreachable rooms) are
    needed: any path from an unreachable room to the reachable set has at least
    one such endpoint on every edge, so restricting the pair set is exact, not
    an approximation. Cuts the work from n^2 to |focus| x n.
    """
    thick = max((w.thickness for w in ctx.plan.walls), default=200)
    tol = thick * 1.5                # rooms inset to wall faces sit a wall apart
    edges = {rid: _poly_edges(p) for rid, p in ctx.polys.items()}
    bounds = {rid: p.bounds for rid, p in ctx.polys.items()}
    out: dict[tuple[str, str], float] = {}
    for u in focus:
        bu = bounds.get(u)
        if bu is None:
            continue
        for v in ctx.polys:
            if v == u:
                continue
            key = (u, v) if u < v else (v, u)
            if key in out:
                continue
            bv = bounds[v]
            if (bu[2] + tol < bv[0] or bv[2] + tol < bu[0]
                    or bu[3] + tol < bv[1] or bv[3] + tol < bu[1]):
                continue
            L = _shared_edge_len(edges[u], edges[v], tol)
            if L > 0:
                out[key] = L
    return out


# ---------------------------------------------------- (b) NBC 2016 Part 3 ---

def _ceiling_mm(ctx: _Ctx, r: Room) -> int:
    hs = [ctx.walls[w].height for w in r.wall_ids if w in ctx.walls]
    return min(hs) if hs else ctx.plan.storey_height


def _is_passage(r: Room) -> bool:
    return bool(_PASSAGE_RE.search(r.name)) or r.category == "passage"


def check_nbc(ctx: _Ctx) -> list[Finding]:
    out: list[Finding] = []
    nbc = ctx.profile.nbc
    hab = [r for r in ctx.rooms
           if _is_habitable(r.category) and r.id in ctx.polys and not _is_passage(r)]
    single_room = len(hab) == 1
    ac_rooms = set(ctx.brief.get("air_conditioned_room_ids", []))

    for r in ctx.rooms:
        p = ctx.polys.get(r.id)
        if p is None:
            continue
        a_m2 = p.area / 1e6
        ceil = _ceiling_mm(ctx, r)

        if _is_passage(r):
            pts = ctx.room_doors.get(r.id, [])
            w = passage_clear_width_mm(p, pts, nbc.passage_min_width_mm)
            if w < nbc.passage_min_width_mm - 1:
                out.append(Finding(
                    "NBC.PASSAGE_WIDTH", "error", 0.8,
                    f"passage {r.name!r} clear width {w:.0f} mm "
                    f"(pinch point) < {nbc.passage_min_width_mm} mm",
                    [r.id], w, float(nbc.passage_min_width_mm)))
            continue

        if _is_habitable(r.category):
            req_a = (nbc.hab_min_area_single_room_m2 if single_room
                     else nbc.hab_min_area_m2)
            if a_m2 < req_a - 1e-6:
                out.append(Finding(
                    "NBC.HAB_MIN_AREA", "error", 1.0,
                    f"habitable room {r.name!r} is {a_m2:.2f} m^2 "
                    f"({'single-room dwelling' if single_room else 'multi-room'} "
                    f"minimum {req_a} m^2)", [r.id], a_m2, req_a))
            w = clear_width_mm(p)
            if w < nbc.hab_min_width_mm - 1:
                out.append(Finding(
                    "NBC.HAB_MIN_WIDTH", "error", 0.9,
                    f"habitable room {r.name!r} clear width {w:.0f} mm < "
                    f"{nbc.hab_min_width_mm} mm", [r.id], w,
                    float(nbc.hab_min_width_mm)))
            req_h = (nbc.ac_room_min_ceiling_mm if r.id in ac_rooms
                     else nbc.hab_min_ceiling_mm)
            if ceil < req_h - 1:
                out.append(Finding(
                    "NBC.HAB_CEILING", "error", 0.7,
                    f"habitable room {r.name!r} ceiling {ceil} mm < {req_h} mm",
                    [r.id], float(ceil), float(req_h)))

        elif r.category == "kitchen":
            if a_m2 < nbc.kitchen_min_area_m2 - 1e-6:
                out.append(Finding(
                    "NBC.KITCHEN_MIN_AREA", "error", 1.0,
                    f"kitchen {r.name!r} is {a_m2:.2f} m^2 < "
                    f"{nbc.kitchen_min_area_m2} m^2", [r.id], a_m2,
                    nbc.kitchen_min_area_m2))
            w = clear_width_mm(p)
            if w < nbc.kitchen_min_width_mm - 1:
                out.append(Finding(
                    "NBC.KITCHEN_MIN_WIDTH", "error", 0.9,
                    f"kitchen {r.name!r} clear width {w:.0f} mm < "
                    f"{nbc.kitchen_min_width_mm} mm", [r.id], w,
                    float(nbc.kitchen_min_width_mm)))
            if ceil < nbc.kitchen_min_ceiling_mm - 1:
                out.append(Finding(
                    "NBC.KITCHEN_CEILING", "error", 0.6,
                    f"kitchen {r.name!r} ceiling {ceil} mm < "
                    f"{nbc.kitchen_min_ceiling_mm} mm", [r.id], float(ceil),
                    float(nbc.kitchen_min_ceiling_mm)))

        elif r.category == "bathroom":
            fixt = _bath_fixtures(r, ctx)
            req_a = {"bath": nbc.bath_only_min_area_m2,
                     "wc": nbc.wc_only_min_area_m2,
                     "combined": nbc.bath_wc_combined_min_area_m2}[fixt]
            if a_m2 < req_a - 1e-6:
                out.append(Finding(
                    "NBC.BATH_MIN_AREA", "error", 1.0,
                    f"{fixt} {r.name!r} is {a_m2:.2f} m^2 < {req_a} m^2",
                    [r.id], a_m2, req_a))
            w = clear_width_mm(p)
            if w < nbc.bath_min_width_mm - 1:
                out.append(Finding(
                    "NBC.BATH_MIN_WIDTH", "error", 0.8,
                    f"{fixt} {r.name!r} clear width {w:.0f} mm < "
                    f"{nbc.bath_min_width_mm} mm", [r.id], w,
                    float(nbc.bath_min_width_mm)))
            if ceil < nbc.bath_min_ceiling_mm - 1:
                out.append(Finding(
                    "NBC.BATH_CEILING", "error", 0.6,
                    f"{fixt} {r.name!r} ceiling {ceil} mm < "
                    f"{nbc.bath_min_ceiling_mm} mm", [r.id], float(ceil),
                    float(nbc.bath_min_ceiling_mm)))

    out += _check_wc_into_kitchen(ctx)
    return out


def _bath_fixtures(r: Room, ctx: _Ctx) -> str:
    """bath-only / wc-only / combined, from the brief then the room name.

    Defaults to "combined" because that is what an Indian 2-3 BHK actually
    builds; guessing "wc" would silently drop the minimum from 2.8 to 1.1 m^2.
    """
    override = ctx.brief.get("bath_fixtures", {}).get(r.id)
    if override in ("bath", "wc", "combined"):
        return override
    n = r.name.lower()
    if _WC_RE.search(n) and "bath" not in n and "shower" not in n:
        return "wc"
    if ("bath" in n or "shower" in n) and not _WC_RE.search(n):
        # An Indian "Bathroom" is a combined bath+WC unless told otherwise.
        return "combined"
    return "combined"


def _check_wc_into_kitchen(ctx: _Ctx) -> list[Finding]:
    """NBC: no WC/bath may open *directly* into a kitchen or food store."""
    cats = {r.id: r.category for r in ctx.rooms}
    names = {r.id: r.name for r in ctx.rooms}
    out = []
    for oid, a, b in ctx.door_edges:
        pair = {cats.get(a), cats.get(b)}
        if pair == {"bathroom", "kitchen"}:
            out.append(Finding(
                "NBC.WC_OPENS_INTO_KITCHEN", "error", 1.0,
                f"door {oid} connects {names.get(a)!r} directly to "
                f"{names.get(b)!r}; a WC may not open into a kitchen",
                [oid, a, b]))
    return out


# ------------------------------------------------------------ (c) bye-laws ---

def _front_direction(ctx: _Ctx) -> tuple[float, float]:
    """Outward unit vector towards the road.

    Taken from the front door: the plot edge the entrance faces *is* the front
    for setback purposes. Fallback is -Y, which is the convention the IR's
    north_deg=0 default implies (plan drawn with the road at the bottom).
    """
    fd = next((o for o in ctx.plan.openings if o.kind == "front_door"), None)
    if fd is not None and ctx.footprint is not None:
        w = ctx.walls.get(fd.wall_id)
        if w is not None and w.length >= 1.0:
            dx, dy = w.end.x - w.start.x, w.end.y - w.start.y
            L = w.length
            nx, ny = -dy / L, dx / L
            t = min(max(fd.position, 0.0), 1.0)
            px, py = w.start.x + t * dx, w.start.y + t * dy
            cx, cy = ctx.centre
            if (px - cx) * nx + (py - cy) * ny < 0:
                nx, ny = -nx, -ny
            # snap to the dominant axis; bye-law setbacks are per plot edge
            if abs(nx) >= abs(ny):
                return (1.0 if nx > 0 else -1.0, 0.0)
            return (0.0, 1.0 if ny > 0 else -1.0)
    return (0.0, -1.0)


def buildable_polygon(plot: Polygon, band: PlotBand,
                      front_dir: tuple[float, float]) -> tuple[Polygon, dict]:
    """Plot minus setbacks.

    Percentage setbacks need a "depth" and a "width", which only exist for a
    rectangle, so they are taken on the plot's axis-aligned bounding box with
    the depth axis chosen as the one the front door faces. The inset rectangle
    is then intersected with the true plot polygon, which is conservative for
    non-rectangular plots. Documented as an approximation on purpose.
    """
    minx, miny, maxx, maxy = plot.bounds
    ex, ey = maxx - minx, maxy - miny
    along_x = abs(front_dir[0]) >= abs(front_dir[1])
    depth, width = (ex, ey) if along_x else (ey, ex)

    f = band.front.resolve_mm(depth, width)
    r = band.rear.resolve_mm(depth, width)
    s = band.side.resolve_mm(depth, width)
    # `both_sides=False` (BBMP small plots: one side setback only) - the
    # requirement is satisfiable by either side, so the envelope is inset on
    # one side and the check is run twice, once per side.
    sides = (s, s) if band.side.both_sides else (s, 0)

    def rect(s_lo: int, s_hi: int) -> Polygon:
        if along_x:
            lo_x = minx + (f if front_dir[0] < 0 else r)
            hi_x = maxx - (f if front_dir[0] > 0 else r)
            return Polygon([(lo_x, miny + s_lo), (hi_x, miny + s_lo),
                            (hi_x, maxy - s_hi), (lo_x, maxy - s_hi)])
        lo_y = miny + (f if front_dir[1] < 0 else r)
        hi_y = maxy - (f if front_dir[1] > 0 else r)
        return Polygon([(minx + s_lo, lo_y), (maxx - s_hi, lo_y),
                        (maxx - s_hi, hi_y), (minx + s_lo, hi_y)])

    cands = ([rect(*sides)] if sides[0] == sides[1]
             else [rect(*sides), rect(sides[1], sides[0])])
    best = None
    for c in cands:
        g = c.intersection(plot)
        if best is None or g.area > best.area:
            best = g
    meta = {"front_mm": f, "rear_mm": r, "side_mm": s,
            "depth_mm": depth, "width_mm": width,
            "side_both": band.side.both_sides}
    return best, meta



# --------------------------------------------------------------- circulation
# These exist because a plan can satisfy every dimensional rule and still be
# architecturally wrong. Measured on the generated suite: base-01 produced
# LIVING -> BEDROOM 1 -> HALL -> BEDROOM 3 -> BATHROOM, so the only toilet was
# reached by walking through two bedrooms, and a dead-end 89 sqft "Hall" sat in a
# corner. Every dimensional rule passed. The cause is that a minimum spanning
# tree over edge costs yields cheap CHAINS, when circulation wants a HUB -- so
# these rules score the shape of the door graph, not the sizes of the rooms.

_PRIVATE = {"bedroom", "master_bedroom"}

# Rooms a bedroom is allowed to be the only way into, with how many of each.
# Two baths behind one bedroom is a jack-and-jill and legitimate; three is a
# corridor wearing a bedroom's label. A dressing room counts here too -- it is
# the same relationship as an attached bath.
# One of each. A jack-and-jill bath is SHARED between two bedrooms -- one door
# each -- not two baths hanging off one bedroom, and DESIGN.MULTIPLE_ATTACHED
# _BATHS already calls the latter an error, so a cap of 2 here contradicted it.
_BEDROOM_APPENDAGES: dict[str, int] = {
    "bathroom": 1, "balcony": 1, "dressing": 1, "wardrobe": 1, "terrace": 1,
}

# Catalogue ids that ARE a parking space. `place_site_elements` puts a car on
# the driveway rather than creating a parking room.
_PARKING_ITEMS = frozenset((
    "car_sedan", "car_suv", "car_pickup", "motorcycle", "bike",
    "garage_door_double", "garage_door_single",
))

_CIRC = {"living", "dining", "foyer", "passage", "hall"}
_MAX_DEPTH_FROM_ENTRANCE = 3


def _adj(ctx: _Ctx) -> dict[str, set[str]]:
    a: dict[str, set[str]] = {r.id: set() for r in ctx.rooms}
    for _oid, x, y in ctx.door_edges:     # (opening_id, room_a, room_b)
        if x in a and y in a:
            a[x].add(y); a[y].add(x)
    return a


def _cat(ctx: _Ctx, rid: str) -> str:
    r = next((x for x in ctx.rooms if x.id == rid), None)
    return (r.category if r else "") or ""


def _reach_without(a: dict[str, set[str]], start: str, blocked: set[str]) -> set[str]:
    seen, stack = set(), [start]
    while stack:
        u = stack.pop()
        if u in seen or u in blocked:
            continue
        seen.add(u)
        stack.extend(a.get(u, ()))
    return seen


def check_circulation(ctx: _Ctx) -> list[Finding]:
    out: list[Finding] = []
    a = _adj(ctx)
    if not a:
        return out
    entries = [r for r in (ctx.entry_rooms or []) if r in a]
    entry = entries[0] if entries else None
    names = {r.id: r.name for r in ctx.rooms}

    # 1. A bedroom must not be a corridor. If removing it disconnects rooms that
    #    are not its own private appendages, traffic passes through someone's
    #    bedroom.
    #
    #    What counts as an appendage is measured. Over the 265 ResPlan plans
    #    with a connected door graph, the rooms hanging behind a private room
    #    are: bathroom 416, balcony 232, kitchen 6, living 1, bedroom 1. So an
    #    attached bath and a bedroom balcony are what real houses do -- and
    #    what the brief asks for. Exempting only the bath fired on 59% of real
    #    plans, the balcony being the entire false-positive population. A
    #    kitchen, living room or second bedroom behind a bedroom stays an
    #    error, per the tail of that distribution.
    # Only rooms that ARE reachable can be stranded by passing through one.
    # Removing a door cannot make a bedroom into a corridor; an unreachable
    # room is GEO.UNREACHABLE_ROOM's business.
    base = _reach_without(a, entry, set()) if entry is not None else set()
    for rid, nb in a.items():
        if _cat(ctx, rid) not in _PRIVATE or entry is None or rid == entry:
            continue
        reach = _reach_without(a, entry, {rid})
        stranded = [q for q in a if q != rid and q in base and q not in reach]
        appendages = [q for q in stranded
                      if _cat(ctx, q) in _BEDROOM_APPENDAGES]
        over_cap = any(
            sum(1 for q in stranded if _cat(ctx, q) == cat) > cap
            for cat, cap in _BEDROOM_APPENDAGES.items())
        if len(stranded) > len(appendages) or over_cap:
            lost = ", ".join(names.get(q, q) for q in stranded[:4])
            out.append(Finding(
                "DESIGN.BEDROOM_THROUGH_TRAFFIC", "error", 1.0,
                f"{names.get(rid, rid)} is the only route to {lost} — traffic "
                f"passes through a bedroom", [rid] + stranded[:4]))

    # 2. A bedroom with two or more bathroom doors. One attached bath is normal;
    #    two means the second bathroom has no independent access.
    for rid, nb in a.items():
        if _cat(ctx, rid) not in _PRIVATE:
            continue
        baths = [q for q in nb if _cat(ctx, q) == "bathroom"]
        if len(baths) > 1:
            out.append(Finding(
                "DESIGN.MULTIPLE_ATTACHED_BATHS", "error", 0.9,
                f"{names.get(rid, rid)} has {len(baths)} bathrooms opening off it "
                f"({', '.join(names.get(q, q) for q in baths)}); a second bath needs "
                "its own access", [rid] + baths))

    # 3. If the plan has exactly one bathroom it must be common -- reachable
    #    without entering a bedroom. Otherwise the household shares a toilet
    #    through a private room.
    baths = [r.id for r in ctx.rooms if (r.category or "") == "bathroom"]
    if len(baths) == 1 and entry is not None:
        bedrooms = {r.id for r in ctx.rooms if (r.category or "") in _PRIVATE}
        if baths[0] not in _reach_without(a, entry, bedrooms):
            out.append(Finding(
                "DESIGN.SOLE_BATH_VIA_BEDROOM", "error", 1.0,
                f"the only bathroom ({names.get(baths[0], baths[0])}) cannot be "
                "reached without walking through a bedroom", baths))

    # 3b. A habitable room reachable only through a bedroom is somebody's
    #     bedroom, whatever the label says. The bathroom case above was the
    #     only one covered, so a study or a store behind a bedroom door passed
    #     -- measured on real output, a `study` whose sole access was Bedroom 2.
    if entry is not None:
        bedrooms = {r.id for r in ctx.rooms if (r.category or "") in _PRIVATE}
        open_from_entry = _reach_without(a, entry, bedrooms)
        for r in ctx.rooms:
            cat = r.category or ""
            if cat in _PRIVATE or cat == "bathroom" or r.id not in ctx.polys:
                continue
            if not _is_habitable(cat) or r.id in open_from_entry:
                continue
            out.append(Finding(
                "DESIGN.HABITABLE_VIA_BEDROOM", "error", 0.9,
                f"{names.get(r.id, r.id)} can only be reached by walking "
                "through a bedroom, so it belongs to whoever sleeps there "
                "rather than to the household", [r.id]))

    # 4. A circulation space that leads nowhere is leftover area with a label on
    #    it, not a room. This is what the solver's filler cell produces.
    for rid, nb in a.items():
        if _cat(ctx, rid) not in _CIRC or rid == entry:
            continue
        if len(nb) <= 1:
            out.append(Finding(
                "DESIGN.DEAD_END_CIRCULATION", "warn", 0.6,
                f"{names.get(rid, rid)} is circulation with {len(nb)} door(s) — "
                "it leads nowhere and reads as leftover space", [rid]))

    # 5. Depth from the front door. Four-plus doors deep means a warren.
    if entry is not None:
        depth, frontier, seen = {entry: 0}, [entry], {entry}
        while frontier:
            nxt = []
            for u in frontier:
                for v in a.get(u, ()):
                    if v not in seen:
                        seen.add(v); depth[v] = depth[u] + 1; nxt.append(v)
            frontier = nxt
        deep = [(k, d) for k, d in depth.items() if d > _MAX_DEPTH_FROM_ENTRANCE]
        for rid, d in sorted(deep, key=lambda t: -t[1])[:4]:
            out.append(Finding(
                "DESIGN.TOO_DEEP", "warn", 0.5,
                f"{names.get(rid, rid)} is {d} doors from the entrance "
                f"(target <= {_MAX_DEPTH_FROM_ENTRANCE})", [rid]))
    return out



# ------------------------------------------------------------------ typology
# Below this, a connection between two rooms reads as a doorway; above it, as
# one continuous space. A judgement, not a measurement: Indian living-cum-dining
# is normally built with no wall at all or a 2.4-3.0 m cased opening, and 1800
# is the point where a person stops perceiving a threshold. Stated here so it
# is arguable in one place.
from .standards import OPEN_SPAN_MIN_MM  # noqa: E402


def _widest_link_mm(ctx: _Ctx, ha: list[str], hb: list[str]) -> float:
    """The widest single opening joining any room in `ha` to any in `hb`."""
    want = {(x, y) for x in ha for y in hb} | {(y, x) for x in ha for y in hb}
    by_id = {o.id: o for o in getattr(ctx.plan, "openings", ()) or ()}
    best = 0.0
    for oid, ra, rb in ctx.door_edges:
        if (ra, rb) not in want:
            continue
        o = by_id.get(oid)
        if o is not None:
            best = max(best, float(getattr(o, "width", 0) or 0))
    return best


def check_typology(ctx: _Ctx) -> list[Finding]:
    """Adjacency expectations that depend on the BUILDING, not on the room.

    A kitchen opening into the living room is correct in an 1,100 sqft apartment
    and wrong in a 4,000 sqft villa where the client is paying for a formal
    living room. So the expectations come from `typology.py`, and the typology
    comes from the brief (or is inferred when the brief does not say).
    """
    from . import typology as TY
    out: list[Finding] = []
    kind = ctx.brief.get("typology") or "auto"
    beds = sum(1 for r in ctx.rooms if (r.category or "") in ("bedroom", "master_bedroom"))
    kits = sum(1 for r in ctx.rooms if (r.category or "") == "kitchen")
    livs = sum(1 for r in ctx.rooms if (r.category or "") == "living")
    if kind == "auto":
        kind = TY.infer(site_kind=str(ctx.brief.get("site_kind", "plot")),
                        plot_sqft=ctx.brief.get("plot_area_sqft"),
                        storeys=int(ctx.brief.get("habitable_floors", 1)),
                        bedrooms=beds, kitchens=max(kits, 1),
                        has_two_living=livs >= 2)
    ty = TY.get(kind)
    out.append(Finding("TYPO.ASSUMED", "warn", 0.05,
                       f"typology taken as {ty.display}"
                       + ("" if ctx.brief.get("typology") else " (inferred, not stated)"),
                       []))

    by_cat: dict[str, list[str]] = {}
    for r in ctx.rooms:
        by_cat.setdefault(r.category or "", []).append(r.id)
    names = {r.id: r.name for r in ctx.rooms}
    a = _adj(ctx)

    def joined(ca: str, cb: str) -> bool:
        return any(y in a.get(x, ()) for x in by_cat.get(ca, []) for y in by_cat.get(cb, []))

    for rule in ty.expect:
        ha, hb = by_cat.get(rule.a), by_cat.get(rule.b)
        if not ha or not hb:
            continue                          # the room is not in this plan
        link = joined(rule.a, rule.b)
        sev = "error" if rule.weight >= 0.9 else "warn"
        # "open" is a stronger claim than "direct" and was checked as though it
        # were the same one, so a 900 mm door satisfied "sold as one
        # living-cum-dining space". It is not one space if you walk through a
        # doorway to get from half of it to the other half.
        if rule.relation == "open" and link:
            widest = _widest_link_mm(ctx, ha, hb)
            if widest < OPEN_SPAN_MIN_MM:
                out.append(Finding(
                    "TYPO.NOT_ACTUALLY_OPEN", sev, rule.weight,
                    f"{ty.display}: {rule.a} and {rule.b} are joined by a "
                    f"{widest:.0f} mm doorway, so they read as two rooms. "
                    f"{OPEN_SPAN_MIN_MM} mm or more is needed for them to be "
                    "one continuous space"
                    + (f" — {rule.why}" if rule.why else ""),
                    (ha + hb)[:4], widest, float(OPEN_SPAN_MIN_MM)))
            continue
        if rule.relation in ("direct", "open", "near") and not link:
            # `near` is satisfied by a shared boundary even with no door, so only
            # flag it when the rooms do not touch at all.
            if rule.relation == "near":
                touch = any(
                    ctx.polys[x].buffer(60).intersects(ctx.polys[y])
                    for x in ha for y in hb
                    if x in ctx.polys and y in ctx.polys)
                if touch:
                    continue
            out.append(Finding(
                "TYPO.MISSING_ADJACENCY", sev, rule.weight,
                f"{ty.display}: {rule.a} and {rule.b} should be {rule.relation}"
                + (f" — {rule.why}" if rule.why else ""),
                (ha + hb)[:4]))
        elif rule.relation == "separate" and link:
            pair = next(((x, y) for x in ha for y in hb if y in a.get(x, ())), None)
            out.append(Finding(
                "TYPO.FORBIDDEN_ADJACENCY", sev, rule.weight,
                f"{ty.display}: {names.get(pair[0], rule.a)} opens directly into "
                f"{names.get(pair[1], rule.b)}"
                + (f" — {rule.why}" if rule.why else ""),
                list(pair) if pair else []))

    # Entry sequence: each named stage that exists should come no later than the
    # next one, measured as door-depth from the front door.
    entries = [r for r in (ctx.entry_rooms or []) if r in a]
    if entries and ty.entry_sequence:
        depth, frontier, seen = {entries[0]: 0}, [entries[0]], {entries[0]}
        while frontier:
            nxt = []
            for u in frontier:
                for v in a.get(u, ()):
                    if v not in seen:
                        seen.add(v); depth[v] = depth[u] + 1; nxt.append(v)
            frontier = nxt
        stages = [(c, min((depth[i] for i in by_cat.get(c, []) if i in depth),
                          default=None)) for c in ty.entry_sequence]
        stages = [(c, d) for c, d in stages if d is not None]
        for (c1, d1), (c2, d2) in zip(stages, stages[1:]):
            if d1 > d2:
                out.append(Finding(
                    "TYPO.ENTRY_SEQUENCE", "warn", 0.5,
                    f"{ty.display}: expected {c1} before {c2} on entry, but {c2} "
                    f"is {d2} doors in and {c1} is {d1}", []))
    return out



# --------------------------------------------------- design / syntax / topology
def check_design_quality(ctx: _Ctx) -> list[Finding]:
    """The 14 architect's-red-pen checks in `design.py`."""
    from . import design as D
    from . import topology as TP
    sc = _scenario_for(ctx)
    fs = D.check(ctx.plan, adjacency=_adj(ctx), entry_rooms=ctx.entry_rooms,
                 typology=sc, brief=ctx.brief)
    return [Finding(f.rule_id, f.severity, f.weight, f.detail, f.element_ids)
            for f in fs]


def check_syntax(ctx: _Ctx) -> list[Finding]:
    """Space-syntax configuration: is the living room actually the core?

    Targets are measured off 400 real ResPlan plans (living is the core in 97%,
    living_relative 2.53, privacy_gradient 0.39). Our own output sat at 3.3% /
    1.01 / 1.02, i.e. no hierarchy at all, which is what these catch.
    """
    from . import syntax as SX
    a = _adj(ctx)
    meta = {r.id: (r.name, r.category or "") for r in ctx.rooms}
    ent = (ctx.entry_rooms or [None])[0]
    st = SX.analyse(a, meta, ent)
    ctx.brief["_syntax"] = st          # so callers can read the metrics
    return [Finding(rid, sev, w, det, ids) for rid, sev, w, det, ids in SX.check(st)]


def check_bathroom_topology(ctx: _Ctx) -> list[Finding]:
    """Classify every bathroom and compare against what the brief asked for.

    "3BHK with all attached baths" and "3BHK with a common bath" are different
    TOPOLOGIES, not different labels, so the brief's intent is checkable.
    """
    from . import topology as TP
    out: list[Finding] = []
    a = _adj(ctx)
    cat_of = {r.id: (r.category or "") for r in ctx.rooms}
    names = {r.id: r.name for r in ctx.rooms}
    ent = (ctx.entry_rooms or [None])[0]
    from .syntax import _bfs_depths as _bfs
    depth = _bfs(a, ent) if ent in a else {}
    beds = {i for i, c in cat_of.items() if c in TP.PRIVATE_CATS}

    kinds: dict[str, str] = {}
    for bid, c in cat_of.items():
        if c != "bathroom":
            continue
        touching = {b for b in beds
                    if bid in ctx.polys and b in ctx.polys
                    and ctx.polys[bid].buffer(60).intersects(ctx.polys[b])}
        t = TP.classify_bathroom(bid, names.get(bid, bid), set(a.get(bid, ())),
                                 cat_of, depth=depth.get(bid),
                                 adjacent_bedrooms=touching)
        kinds[bid] = t.kind
        if t.kind == "unreachable":
            out.append(Finding("TOPO.BATH_UNREACHABLE", "error", 1.0,
                               f"{t.name} has no door", [bid]))
        elif t.kind == "shared_attached" and len(t.bedrooms) > 2:
            out.append(Finding("TOPO.BATH_OVERSHARED", "error", 0.9,
                               f"{t.name} opens off {len(t.bedrooms)} bedrooms; a "
                               "shared bath serves two at most", [bid]))

    want = ctx.brief.get("attached_bath")
    if want is not None:
        got = sum(1 for k in kinds.values() if k in ("attached", "common_attached"))
        if got < int(want):
            out.append(Finding(
                "TOPO.ATTACHED_BATH_SHORTFALL", "error", 0.9,
                f"brief asks for {want} attached bathroom(s); the plan realises "
                f"{got} ({', '.join(sorted(set(kinds.values()))) or 'none'})",
                list(kinds)))

    # A household with bedrooms and no common-access toilet means guests use a
    # bedroom's bath.
    if beds and kinds and not any(
            k in ("common", "common_attached", "powder", "detached")
            for k in kinds.values()):
        out.append(Finding(
            "TOPO.NO_COMMON_BATH", "warn", 0.6,
            "every bathroom is en-suite; there is no toilet a guest can use "
            "without entering a bedroom", list(kinds)))
    ctx.brief["_bath_kinds"] = kinds
    return out


def _scenario_for(ctx: _Ctx):
    """Resolve the topology scenario once, from the brief or by inference."""
    from . import topology as TP
    key = ctx.brief.get("scenario")
    if key and key in TP.SCENARIOS:
        return TP.SCENARIOS[key]
    beds = sum(1 for r in ctx.rooms if (r.category or "") in TP.PRIVATE_CATS)
    kits = sum(1 for r in ctx.rooms if (r.category or "") == "kitchen")
    livs = sum(1 for r in ctx.rooms if (r.category or "") == "living")
    return TP.resolve(site_kind=str(ctx.brief.get("site_kind", "plot")),
                      plot_sqft=ctx.brief.get("plot_area_sqft"),
                      carpet_sqft=ctx.brief.get("carpet_sqft"),
                      storeys=int(ctx.brief.get("habitable_floors", 1)),
                      bedrooms=beds, kitchens=max(kits, 1),
                      has_two_living=livs >= 2)


# ---------------------------------------------------------------------------
# layout sense: the things a client notices first
# ---------------------------------------------------------------------------
#
# Defects visible at a glance on the drawing and invisible to the validator --
# the worst combination, because the plan looked checked.
#
# None of these is calibrated on ResPlan, deliberately: `brief.py` documents
# that corpus as having geography that "points away from India", so it cannot
# establish an Indian-market norm. ResPlan keeps one job here, in
# `test_false_positive_rate_on_real_plans` -- a rule that fires on real built
# houses ANYWHERE is suspect, whatever the market. That is soundness, not
# calibration.
#
# So each threshold rests on an Indian source -- the area bands in
# `spec.ROOM_CATEGORIES`, `spec.DEFAULT_ADJACENCY`, `typology.py`, the stated
# requirements in `suite/` -- or on a dimensional argument, and says which.
# Where no Indian source settles it, there is no rule.

# A passage is functional at roughly a metre wide; two people pass at 1.2 m.
# Beyond this it is not circulation, it is unassigned floor that the solver had
# nowhere else to put -- and `envelope.NBC_MIN` says so in as many words:
# "passage: filler hall/corridor absorbing leftover area".
PASSAGE_IS_A_ROOM_MM = 2100

# Rooms whose whole job is to be walked through. `_CIRC` above is the
# circulation CORE (living and dining are passed through too); this is the set
# that is purely circulation and therefore pure overhead.
_PURE_CIRC = {"foyer", "passage", "corridor", "stair", "staircase"}
# Measured on the hand-annotated real drawings: Godrej Woods 10.4%, the 30x40
# duplex about 12-13%. Warn past 18%, call it an error past 25%.
CIRCULATION_SHARE_MAX = 0.18
CIRCULATION_SHARE_HARD = 0.25

# Rooms that need a plumbing line.
_WET_ROOMS = {"kitchen", "bathroom", "wc", "toilet", "powder", "utility",
              "handwash"}
# One stack for the kitchen side, one for the bathrooms, is ordinary. A third
# is a cost the plan is paying for its own arrangement.
WET_GROUPS_MAX = 2


def _edge_share(ctx: _Ctx, a: str, b: str) -> bool:
    """Do two rooms share enough boundary for one stack to serve both?

    A plain buffered `intersects` is not enough: it returns True for rooms
    meeting at a single corner, where the buffered overlap is only a
    tol-by-tol square. Two rooms diagonally opposite a junction do not share a
    wall and cannot share a drain. So the shared run has to be long enough for
    a pipe to actually run along -- the same corner-kiss trap
    `spatial._edge_overlap` documents.
    """
    pa, pb = ctx.polys.get(a), ctx.polys.get(b)
    if pa is None or pb is None:
        return False
    inter = pa.buffer(TOUCH_TOL_MM).intersection(pb)
    if inter.is_empty:
        return False
    x0, y0, x1, y1 = inter.bounds
    return max(x1 - x0, y1 - y0) >= STACK_RUN_MIN_MM


# Half a wall plus slop: rooms are centreline faces, so two rooms sharing a
# 115 mm partition have polygons about that far apart.
TOUCH_TOL_MM = 150
# A shared run shorter than this is a corner, not a wall a pipe can follow.
STACK_RUN_MIN_MM = 600

# Service rooms a bathroom must not hide behind.
_SERVICE_ONLY = {"utility", "store", "storage", "kitchen", "shaft"}
# What counts as one public zone.
_PUBLIC = {"living", "dining", "foyer", "passage", "hall", "sitout"}


def check_layout_sense(ctx: _Ctx) -> list[Finding]:
    from . import roomtypes as rt
    out: list[Finding] = []
    a = _adj(ctx)
    cat = {r.id: (r.category or "") for r in ctx.rooms}
    name = {r.id: (r.name or r.id) for r in ctx.rooms}
    area = {r.id: (ctx.polys[r.id].area / 1e6)
            for r in ctx.rooms if r.id in ctx.polys}
    req = (ctx.brief.get("requirements") or {})

    def ids_of(*cats: str) -> list[str]:
        want = set()
        for c in cats:
            want |= set(rt.subtypes_of(c))
        return [r.id for r in ctx.rooms if cat[r.id] in want]

    baths = set(ids_of("bathroom"))
    beds = ids_of("bedroom")
    livings = ids_of("living")

    # ---- an en-suite the brief asked for is missing ---------------------
    # Fires ONLY when the brief asked. There is no Indian source for a default:
    # 127 of the 148 bedroom-bearing examples in `suite/` (86%) say nothing
    # about `attached_bath` and two state 0 explicitly. Silence is not evidence
    # of a norm in either direction, so no default finding is issued. Asking
    # for one and not getting it is still an error.
    if beds and baths:
        master = max(beds, key=lambda i: area.get(i, 0.0))
        asked = bool(req.get("attached_bath"))
        if asked and not (baths & a.get(master, set())):
            out.append(Finding(
                "DESIGN.NO_ENSUITE_MASTER", "error", 1.0,
                f"{name[master]} has no bathroom opening off it and the brief "
                f"asked for {req['attached_bath']} attached bath(s)",
                [master] + sorted(baths)[:2]))

    # ---- a bathroom you reach through the utility -----------------------
    # No measurement needed: a bathroom whose only door is off a service room
    # cannot be used without walking through the washing.
    for b in sorted(baths):
        nbrs = a.get(b, set())
        if not nbrs:
            continue
        if all(cat.get(n, "") in _SERVICE_ONLY for n in nbrs):
            via = ", ".join(sorted(name.get(n, n) for n in nbrs))
            out.append(Finding(
                "DESIGN.BATH_BEHIND_SERVICE", "error", 0.9,
                f"{name[b]} is only reachable through {via}; a bathroom must "
                "open off circulation or off the bedroom it serves",
                [b] + sorted(nbrs)[:2]))

    # ---- the living room is not the biggest room in the house -----------
    # Grounded in our own Indian area bands. In `spec.ROOM_CATEGORIES` the
    # living room tops out at 280 sqft, above every other habitable room --
    # master bedroom 250, bedroom 180, dining 170, kitchen 150, study 130 -- so
    # a plan where something else is larger has inverted the hierarchy the
    # brief itself encodes.
    hab = [i for i in area if _is_habitable(cat.get(i, ""))]
    if livings and len(hab) > 1:
        biggest = max(hab, key=lambda i: area[i])
        if cat.get(biggest, "") != "living":
            liv = max(livings, key=lambda i: area.get(i, 0.0))
            out.append(Finding(
                "DESIGN.LIVING_NOT_LARGEST", "error", 0.8,
                f"{name[liv]} is {area.get(liv, 0):.1f} m² but "
                f"{name[biggest]} is {area[biggest]:.1f} m²; the living room "
                "should be the largest habitable room",
                [liv, biggest], area.get(liv, 0.0), area[biggest]))

    # ---- circulation that is really unassigned floor --------------------
    # Two severities, because "wide" and "wrong" are different claims. A wide
    # corridor in a large house is not a defect -- width alone as an error
    # fired on 66 of 100 suite plans and destroyed the signal. The defect
    # signature is a passage handed MORE floor than the living room: the
    # surplus was parked in circulation instead of the social space.
    #
    # `solver.py` does this by construction, appending one `passage` room whose
    # target IS the slack, so the error case is that behaviour caught in the
    # act and the warn case is its ordinary consequence.
    liv_area = max((area.get(i, 0.0) for i in livings), default=0.0)
    for r in ctx.rooms:
        if not _is_passage(r) or r.id not in ctx.polys:
            continue
        a_m2 = area.get(r.id, 0.0)
        w = clear_width_mm(ctx.polys[r.id])
        if liv_area and a_m2 > liv_area:
            out.append(Finding(
                "DESIGN.CIRCULATION_OVERSIZED", "error", 0.8,
                f"{name[r.id]} is {a_m2:.1f} m², larger than the living room "
                f"at {liv_area:.1f} m². Surplus floor belongs in the social "
                "space, not in circulation",
                [r.id], a_m2, liv_area))
        elif w > PASSAGE_IS_A_ROOM_MM:
            out.append(Finding(
                "DESIGN.CIRCULATION_WIDE", "warn", 0.5,
                f"{name[r.id]} is {w:.0f} mm wide at its narrowest -- wider "
                "than circulation needs; that area would read better as part "
                "of the living or dining space",
                [r.id], w, float(PASSAGE_IS_A_ROOM_MM)))

    # ---- circulation in AGGREGATE ---------------------------------------
    # `CIRCULATION_OVERSIZED` compares one passage against the living room, so
    # a plan that splits its circulation across a passage, a stair and a foyer
    # passes every per-room check and still spends a fifth of the floor on
    # corridors. Measured: our own 30x40 output spent 21% (passage 78 + stair 66
    # + foyer 20 of 772 sqft carpet) where the real drawing spends about 12%,
    # and the hand-annotated Godrej Woods unit spends 10.4%.
    circ = [r.id for r in ctx.rooms
            if (r.category or "") in _PURE_CIRC and r.id in ctx.polys]
    carpet = sum(area.get(r.id, 0.0) for r in ctx.rooms if r.id in ctx.polys)
    if circ and carpet > 0:
        share = sum(area.get(i, 0.0) for i in circ) / carpet
        if share > CIRCULATION_SHARE_MAX:
            sev, wt = ("error", 0.9) if share > CIRCULATION_SHARE_HARD else ("warn", 0.6)
            out.append(Finding(
                "DESIGN.CIRCULATION_SHARE", sev, wt,
                f"circulation is {share:.0%} of the carpet area across "
                f"{len(circ)} space(s) ({', '.join(name[i] for i in circ)}); "
                f"real plans run 10-13%. That floor belongs in the rooms",
                circ, round(share, 3), CIRCULATION_SHARE_MAX))

    # ---- the public rooms are not one place -----------------------------
    # The complaint this came from: "living room and hall should be at one
    # place, that will make them big together; if we have small spaces in the
    # middle what is the usage". A public zone in two pieces means the client
    # has two half-sized social spaces instead of one good one.
    pub = [i for i in area if cat.get(i, "") in _PUBLIC]
    if len(pub) > 1:
        seen: set[str] = set()
        clusters = 0
        for i in pub:
            if i in seen:
                continue
            clusters += 1
            stack, group = [i], set()
            while stack:
                cur = stack.pop()
                if cur in group:
                    continue
                group.add(cur)
                for n in a.get(cur, ()):
                    if n in pub and n not in group:
                        stack.append(n)
            seen |= group
        if clusters > 1:
            out.append(Finding(
                "DESIGN.PUBLIC_CORE_SPLIT", "error", 0.9,
                f"the public rooms sit in {clusters} disconnected groups "
                f"({', '.join(name[i] for i in pub)}); living, dining and the "
                "hall should read as one continuous space",
                pub[:4], float(clusters), 1.0))

    # ---- wet rooms in more groups than one plumbing line can serve -----
    # Enforces `principles.P.WET_GROUPING`, whose `enforced_by` used to name a
    # parking rule -- so the principle was in the agent's prompt, the
    # `wet_grouping` field was in the brief, `set_wet_grouping` was in the
    # command vocabulary, and nothing enforced any of it.
    #
    # Two groups is normal: one line serving kitchen and utility, another
    # serving the bathrooms. Three means the plan pays for a third stack.
    wet = [i for i in area if cat.get(i, "") in _WET_ROOMS]
    if len(wet) > 2:
        groups, seen = 0, set()
        for i in wet:
            if i in seen:
                continue
            groups += 1
            stack, grp = [i], set()
            while stack:
                cur = stack.pop()
                if cur in grp:
                    continue
                grp.add(cur)
                for o in wet:
                    if o not in grp and _edge_share(ctx, cur, o):
                        stack.append(o)
            seen |= grp
        if groups > WET_GROUPS_MAX:
            want = str(req.get("wet_grouping") or "preferred")
            out.append(Finding(
                "DESIGN.WET_ROOMS_SPLIT",
                "error" if want == "required" else "warn",
                0.8 if want == "required" else 0.5,
                f"the {len(wet)} wet rooms sit in {groups} separate groups "
                f"({', '.join(name[i] for i in wet)}); each group needs its "
                "own plumbing stack",
                wet[:4], float(groups), float(WET_GROUPS_MAX)))

    # DESIGN.BEDROOM_FAR_FROM_BATH was here and is deliberately gone. It
    # warned when a bedroom was more than two doors from a bathroom, fired on
    # 72 of 100 plans, and rested on nothing: no Indian source says how far a
    # bedroom may be from a bath, and a compact plot house with two baths off
    # one passage will often exceed two doors by construction. The case that
    # is a real defect -- the only bath reachable through a bedroom -- is
    # already `DESIGN.SOLE_BATH_VIA_BEDROOM`.
    return out


def check_zoning(ctx: _Ctx) -> list[Finding]:
    """Public / private / service should read as zones, not be interleaved."""
    from . import topology as TP
    out: list[Finding] = []
    zones: dict[str, list[str]] = {}
    for r in ctx.rooms:
        cat = r.category or ""
        z = TP.ZONE_OF.get(cat)
        # A pooja is public but Vastu pins it to the NE, away from the living, so
        # counting it here reports a fragmented public zone on correct plans.
        if z and r.id in ctx.polys and cat not in TP.ZONE_FRAGMENTATION_EXEMPT:
            zones.setdefault(z, []).append(r.id)
    for z in ("private", "public"):
        ids = zones.get(z, [])
        if len(ids) < 2:
            continue
        # A zone is coherent if its members form one touching cluster.
        rem, comp = set(ids), []
        while rem:
            seed = rem.pop(); grp = {seed}; stack = [seed]
            while stack:
                u = stack.pop()
                for v in list(rem):
                    if ctx.polys[u].buffer(120).intersects(ctx.polys[v]):
                        rem.discard(v); grp.add(v); stack.append(v)
            comp.append(grp)
        if len(comp) > 1:
            out.append(Finding(
                f"ZONE.{z.upper()}_FRAGMENTED", "warn", 0.5,
                f"the {z} zone breaks into {len(comp)} separate clusters; related "
                "functions should group and conflicting ones separate",
                sorted(ids)))
    return out



# ------------------------------------------------------- dimensional standards
def check_standards(ctx: _Ctx) -> list[Finding]:
    """Staircase geometry and light/ventilation, from `standards.py`.

    Neither was checked before. Stairs were emitted with a riser count and never
    tested against NBC's 190 mm riser cap or 250 mm tread minimum, and no rule
    looked at glazed area at all -- so a windowless bedroom passed everything
    except a design-quality warning.
    """
    from . import standards as SD
    out: list[Finding] = []
    storeys = int(ctx.brief.get("habitable_floors", 1))
    units = int(ctx.brief.get("dwelling_units", 1))
    prof = ("apartment" if str(ctx.brief.get("site_kind")) == "apartment_unit"
            else "multi_family" if units > 1 else "residential")
    std = SD.STAIRS[prof]

    for st in getattr(ctx.plan, "stairs", None) or []:
        n = max(int(st.riser_count or 0), 2)
        going = SD.developed_going_mm(st.depth, st.width, st.stair_type)
        if going <= 0:
            continue                     # spiral: angular geometry, not checked
        r, t, c = SD.stair_geometry(going, n, ctx.plan.storey_height)
        need = SD.required_going_mm(n, std.tread_min_mm)
        if r > std.riser_max_mm:
            out.append(Finding("NBC.STAIR_RISER", "error", 1.0,
                f"riser {r:.0f} mm exceeds {std.riser_max_mm} mm "
                f"({n} risers over a {ctx.plan.storey_height} mm storey) — {std.source}",
                [st.id]))
        if t < std.tread_min_mm:
            out.append(Finding("NBC.STAIR_TREAD", "error", 1.0,
                f"tread {t:.0f} mm below {std.tread_min_mm} mm; a {n}-riser flight "
                f"needs {need:.0f} mm of going and this {st.stair_type} provides "
                f"{going:.0f} mm", [st.id]))
        if not (std.two_r_plus_t[0] <= c <= std.two_r_plus_t[1]):
            out.append(Finding("NBC.STAIR_COMFORT", "warn", 0.6,
                f"2R+T is {c:.0f} mm, outside the {std.two_r_plus_t[0]}-"
                f"{std.two_r_plus_t[1]} mm comfort band "
                f"(ideal {std.two_r_plus_t_ideal[0]}-{std.two_r_plus_t_ideal[1]})",
                [st.id]))
        if st.width < std.width_min_mm:
            out.append(Finding("NBC.STAIR_WIDTH", "error", 1.0,
                f"flight width {st.width} mm below {std.width_min_mm} mm for "
                f"{prof.replace('_', ' ')}", [st.id]))
        if n > std.risers_per_flight_max and st.stair_type == "straight":
            out.append(Finding("NBC.STAIR_NO_LANDING", "error", 0.9,
                f"{n} risers in one straight flight; NBC requires a landing after "
                f"at most {std.risers_per_flight_max}", [st.id]))

    # ---- light and ventilation ------------------------------------------
    win_area: dict[str, float] = {}
    for o in ctx.plan.openings:
        if o.kind != "window":
            continue
        w = ctx.walls.get(o.wall_id)
        if w is None:
            continue
        ln = wall_line(w)
        pt = ln.interpolate(max(0.0, min(1.0, o.position)), normalized=True)
        for rid, poly in ctx.polys.items():
            if poly.buffer(250).contains(pt):
                win_area[rid] = win_area.get(rid, 0.0) + o.width * max(o.head - o.sill, 0)
                break

    for r in ctx.rooms:
        cat = r.category or ""
        cls = ("kitchen" if cat == "kitchen"
               else "bathroom" if cat == "bathroom"
               else "habitable" if cat in SD.HABITABLE_VENT else None)
        if cls is None:
            continue
        v = SD.VENTILATION[cls]
        floor_m2 = (r.area or 0) / 1e6
        glazed_m2 = win_area.get(r.id, 0.0) / 1e6
        need = max(v.window_frac_of_floor * floor_m2, v.min_window_m2)
        if need <= 0:
            continue
        if glazed_m2 + 1e-9 < need:
            sev = "error" if not v.mechanical_ok else "warn"
            out.append(Finding(
                f"NBC.VENTILATION_{cls.upper()}", sev, 0.9,
                f"{r.name}: {glazed_m2:.2f} m² of window against {need:.2f} m² "
                f"required for {floor_m2:.1f} m² of floor"
                + (" (or a mechanical exhaust)" if v.mechanical_ok else "")
                + f" — {v.source}", [r.id]))
    return out



# ----------------------------------------------------------- brief conformance
# BRIEF answers "did we build what was asked for", as distinct from NBC ("is it
# legal") and DESIGN ("is it a good house"). Without it, an unmet request is
# visible only as a suite score -- 22 adjacency requests were quietly unmet
# with nothing in the plan's own findings, so neither the user nor the repair
# loop could see them.
#
# Severity follows how the brief said it: an explicit request unmet is an
# error, a merely implied preference is a warning.
def check_brief(ctx: _Ctx) -> list[Finding]:
    req = ctx.brief.get("requirements") or {}
    if not req:
        return out_empty()
    out: list[Finding] = []
    a = _adj(ctx)
    cat_of = {r.id: (r.category or "") for r in ctx.rooms}
    names = {r.id: r.name for r in ctx.rooms}
    by_cat: dict[str, list[str]] = {}
    for rid, c in cat_of.items():
        by_cat.setdefault(c, []).append(rid)

    def _ids(cat: str) -> list[str]:
        # A request is satisfied by the category itself or any of its
        # subtypes: three bedrooms may arrive as two `bedroom` plus one
        # `master_bedroom`, and asking for a bathroom is answered by a `wc`.
        from . import roomtypes as rt
        ids: list[str] = []
        for c in rt.subtypes_of(cat):
            ids += by_cat.get(c, [])
        # And the reverse for a brief that names the subtype: "master bedroom"
        # is satisfied by whichever bedroom the plan made the master, even if
        # it did not get the label.
        if not ids:
            for c in rt.counts_as(cat)[1:]:
                ids += by_cat.get(c, [])
        # Two things a brief asks for that the plan does not carry as ROOMS.
        # A stair is a `Stair` in the IR and parking is a car on the driveway
        # placed by `entrance.place_site_elements`, so counting rooms alone
        # reported both as missing on every plan that had them -- 29 of the 31
        # remaining BRIEF.ROOM_MISSING errors in the suite were a stair or a
        # parking space that was right there in the plan.
        # A brief may name a category the solver cannot represent. `bridge`
        # collapses twelve of them -- corridor and powder and toilet and
        # handwash and dress and terrace among others -- so "the brief asks
        # for 1 corridor(s); the plan has 0" was reported on a plan whose
        # corridor was sitting there labelled `foyer`. `roomtypes` knows
        # nothing about that mapping, so ask the module that owns it.
        if not ids:
            from .bridge import canon
            k = canon(cat)
            if k != cat and k != "unknown":
                ids += by_cat.get(k, [])
        if not ids and cat == "stair":
            ids = [f"stair:{i}" for i, _ in
                   enumerate(getattr(ctx.plan, "stairs", ()) or ())]
        if not ids and cat == "parking":
            ids = [f.id for f in (getattr(ctx.plan, "furniture", ()) or ())
                   if f.catalog_id in _PARKING_ITEMS]
        return list(dict.fromkeys(ids))

    # ---- rooms the brief asked for -------------------------------------
    for cat, want in (req.get("rooms") or {}).items():
        got = len(_ids(cat))
        if got < int(want):
            out.append(Finding(
                "BRIEF.ROOM_MISSING", "error", 1.0,
                f"the brief asks for {want} {cat.replace('_', ' ')}(s); the plan "
                f"has {got}", _ids(cat)))

    # ---- adjacency the brief asked for ---------------------------------
    for pair in (req.get("adjacent") or []):
        ca, cb = pair[0], pair[1]
        xs, ys = _ids(ca), _ids(cb)
        if not xs or not ys:
            continue                      # the missing-room finding covers it
        if not any(y in a.get(x, ()) for x in xs for y in ys):
            out.append(Finding(
                "BRIEF.ADJACENCY_UNMET", "error", 0.9,
                f"the brief asks for the {ca.replace('_', ' ')} to open off the "
                f"{cb.replace('_', ' ')}; there is no door between them",
                xs[:2] + ys[:2]))

    for pair in (req.get("not_adjacent") or []):
        ca, cb = pair[0], pair[1]
        hit = next(((x, y) for x in _ids(ca) for y in _ids(cb)
                    if y in a.get(x, ())), None)
        if hit:
            out.append(Finding(
                "BRIEF.FORBIDDEN_ADJACENCY", "error", 0.9,
                f"the brief asks for no door between the {ca.replace('_', ' ')} "
                f"and the {cb.replace('_', ' ')}, but {names.get(hit[0], hit[0])} "
                f"opens into {names.get(hit[1], hit[1])}", list(hit)))

    # ---- boundary adjacency, which is NOT a door ------------------------
    # `spec.Adjacency.relation` distinguishes `direct_access` (a door) from
    # `adjacent` (share a wall, door optional). Mapping both onto the door check
    # above would reject a correct plan: "pooja adjacent to the living" is
    # satisfied by a shared wall, and a door from a living room into a shrine is
    # not what was asked for.
    for pair in (req.get("touching") or []):
        ca, cb = pair[0], pair[1]
        xs, ys = _ids(ca), _ids(cb)
        if not xs or not ys:
            continue
        if any(y in a.get(x, ()) for x in xs for y in ys):
            continue                      # a door satisfies "adjacent" outright
        touch = any(ctx.polys[x].buffer(60).intersects(ctx.polys[y])
                    for x in xs for y in ys
                    if x in ctx.polys and y in ctx.polys)
        if not touch:
            out.append(Finding(
                "BRIEF.NOT_TOUCHING", "error", 0.8,
                f"the brief asks for the {ca.replace('_', ' ')} to sit next to "
                f"the {cb.replace('_', ' ')}; they neither share a wall nor a "
                "door", xs[:2] + ys[:2]))

    # ---- requirements we cannot test ------------------------------------
    # Reported, not dropped. A requirement that vanishes from the report looks
    # exactly like one that passed, which is the worst way for a check to fail.
    for what in (req.get("unchecked_relations") or []):
        out.append(Finding(
            "BRIEF.RELATION_UNCHECKED", "warn", 0.1,
            f"the brief states {what} and no check can test that relation yet; "
            "it is neither confirmed nor denied", []))

    # ---- items, and which room they belong in ---------------------------
    placed = {f.catalog_id for f in getattr(ctx.plan, "furniture", None) or []}
    for cid in (req.get("must_place") or []):
        if cid not in placed:
            out.append(Finding(
                "BRIEF.ITEM_MISSING", "warn", 0.6,
                f"the brief asks for '{cid}' and nothing was placed", []))

    for cid, room_cat in (req.get("place_in") or {}).items():
        hosts = set(_ids(room_cat))
        items = [f for f in (getattr(ctx.plan, "furniture", None) or [])
                 if f.catalog_id == cid]
        if not items:
            continue                      # the missing-item finding covers it
        if not any(f.room_id in hosts for f in items):
            where = sorted({names.get(f.room_id, str(f.room_id)) for f in items})
            out.append(Finding(
                "BRIEF.ITEM_MISPLACED", "warn", 0.6,
                f"the brief puts '{cid}' in the {room_cat.replace('_', ' ')}; it "
                f"is in {', '.join(where[:3])} instead", []))

    # ---- vastu zones the brief named ------------------------------------
    for cat, zone in (req.get("vastu_zones") or {}).items():
        ids = _ids(cat)
        if not ids:
            continue
        # The VASTU family already scores zones; this only reports that a
        # SPECIFICALLY REQUESTED one was not honoured, which is a brief failure
        # rather than a preference.
        zf = [f for f in ctx.brief.get("_vastu_findings", [])
              if set(f.element_ids) & set(ids)]
        if zf:
            out.append(Finding(
                "BRIEF.ZONE_UNMET", "warn", 0.7,
                f"the brief puts the {cat.replace('_', ' ')} in the {zone}; the "
                f"plan does not place it there", ids[:2]))
    return out


def out_empty() -> list[Finding]:
    return []


def check_bylaws(ctx: _Ctx) -> list[Finding]:
    out: list[Finding] = []
    prof = ctx.profile

    plot = ctx.plot
    plot_area_m2 = ctx.brief.get("plot_area_m2")
    if plot is not None and plot_area_m2 is None:
        plot_area_m2 = plot.area / 1e6

    # An apartment unit is not a site: there is nothing to set back from, no
    # ground to cover and no FAR to respect -- the tower's developer already
    # satisfied all three. Applying plot bye-laws to a unit manufactures
    # violations out of rules that do not apply to it, which is exactly what
    # happened on the suite's 11 apartment examples (coverage 99.8%, 21% of
    # footprint "outside the envelope").
    if str(ctx.brief.get("site_kind", "plot")) == "apartment_unit":
        out.append(Finding(
            "BYLAW.UNIT_NOT_A_SITE", "warn", 0.1,
            "apartment unit: setback, coverage and FAR belong to the tower, "
            "not to this unit; not checked", []))
        return out

    if plot is None or not ctx.brief.get("site_is_surveyed", bool(ctx.plan.site.setbacks_mm)):
        # Refusing to guess. ResPlan's `land` polygon is a building outline with
        # a thin margin (measured coverage 0.64-0.89, median 0.84, no setback
        # band), so scoring it as a surveyed plot would manufacture violations.
        out.append(Finding(
            "BYLAW.SITE_UNSPECIFIED", "warn", 0.2,
            "no surveyed plot (need site.setbacks_mm or "
            "brief['site_is_surveyed']); setback/coverage/FAR not checked", []))
        return out

    sqft = plot_area_m2 / SQFT_M2
    band = prof.resolve(sqft)
    fp = ctx.footprint
    if fp is None or fp.is_empty:
        return out
    fp_area = fp.area

    # --- setback envelope
    front_dir = _front_direction(ctx)
    envl, meta = buildable_polygon(plot, band, front_dir)
    if envl is not None and not envl.is_empty:
        outside = fp.difference(envl).area
        if outside > 0.02 * fp_area:
            out.append(Finding(
                "BYLAW.SETBACK_ENCROACH", "error", 1.0,
                f"{outside/1e6:.2f} m^2 of footprint ({100*outside/fp_area:.1f}%) "
                f"falls outside the buildable envelope "
                f"(front {meta['front_mm']}, rear {meta['rear_mm']}, "
                f"side {meta['side_mm']} mm; band {band.key})",
                [], outside / 1e6, 0.0))

    # --- ground coverage
    cov = fp_area / plot.area
    if cov > band.max_ground_coverage + 1e-6:
        out.append(Finding(
            "BYLAW.GROUND_COVERAGE", "error", 1.0,
            f"ground coverage {100*cov:.1f}% > {100*band.max_ground_coverage:.0f}% "
            f"cap for band {band.key}", [], cov, band.max_ground_coverage))

    # --- FAR
    floors = int(ctx.brief.get("habitable_floors", 1))
    has_stilt = bool(ctx.brief.get("stilt", False))
    built_m2 = ctx.brief.get("built_up_m2")
    if built_m2 is None:
        built_m2 = floors * fp_area / 1e6
        if has_stilt and not prof.stilt_exempt_from_far:
            built_m2 += fp_area / 1e6
    far = built_m2 / (plot.area / 1e6)
    if far > band.far + 1e-6:
        out.append(Finding(
            "BYLAW.FAR", "error", 1.0,
            f"FAR {far:.2f} > {band.far} for band {band.key} "
            f"({built_m2:.1f} m^2 built-up on {plot.area/1e6:.1f} m^2 plot"
            f"{', stilt exempt' if has_stilt and prof.stilt_exempt_from_far else ''})",
            [], far, band.far))

    # --- height
    if band.max_habitable_floors is None:
        out.append(Finding(
            "BYLAW.MAX_FLOORS_UNCHECKED", "warn", 0.2,
            f"band {band.key} ties permitted height to abutting road width, "
            f"not plot area; floor count not checked", []))
    elif floors > band.max_habitable_floors:
        out.append(Finding(
            "BYLAW.MAX_FLOORS", "error", 0.9,
            f"{floors} habitable floors > {band.max_floors_label} permitted for "
            f"band {band.key}", [], float(floors),
            float(band.max_habitable_floors)))

    # --- items that are compliance declarations, not geometry
    if prof.requires_rainwater_harvesting(sqft):
        rwh = ctx.brief.get("rainwater_harvesting")
        if rwh is False:
            out.append(Finding(
                "BYLAW.RWH_REQUIRED", "error", 0.7,
                f"rainwater harvesting is mandatory at {sqft:.0f} sqft "
                f"(>= {prof.rainwater_harvesting_min_sqft:.0f})", []))
        elif rwh is None:
            out.append(Finding(
                "BYLAW.RWH_UNDECLARED", "warn", 0.3,
                f"plot is {sqft:.0f} sqft so RWH is mandatory, but the brief "
                f"does not declare it; not verifiable from geometry", []))

    if str(ctx.brief.get("building_type", "")).lower() in ("apartment", "flats"):
        rw = ctx.brief.get("road_width_mm")
        if rw is None:
            out.append(Finding(
                "BYLAW.ROAD_WIDTH_UNDECLARED", "warn", 0.3,
                "apartment: abutting road width not declared; not verifiable "
                "from the plan", []))
        elif rw < prof.apartment_min_road_width_mm:
            out.append(Finding(
                "BYLAW.ROAD_WIDTH", "error", 0.9,
                f"apartment on a {rw} mm road; minimum "
                f"{prof.apartment_min_road_width_mm} mm", [], float(rw),
                float(prof.apartment_min_road_width_mm)))
    return out


# --------------------------------------------------------------- (d) vastu ---

def bearing_deg(dx: float, dy: float, north_deg: float) -> float:
    """Compass bearing of a plan-space vector.

    `north_deg` is the bearing of +Y, and plan x-y is right-handed, so +X sits
    at north_deg + 90. Hence bearing = north_deg + atan2(dx, dy).
    """
    return (north_deg + math.degrees(math.atan2(dx, dy))) % 360.0


def zone_of_bearing(b: float) -> str:
    return DIRECTIONS[int((b + 22.5) % 360.0 // 45.0)]


def _ang_gap(a: float, b: float) -> float:
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def _zone_bearing(z: str) -> float:
    return 45.0 * DIRECTIONS.index(z)


def _zone_score(b: float, rule: VastuRule, in_centre: bool) -> float:
    """Continuous 0..1 satisfaction.

    Linear angular falloff over 90 deg, so the preferred zone scores 1, an
    adjacent zone 0.5, and the opposite zone 0. Discrete octant labels would
    make the score jump 1.0 -> 0.0 across a 1-degree rotation of the plot,
    which is useless as a gradient for the generate/critique loop.
    """
    n = rule.neutral_score
    pref = max((max(0.0, 1.0 - _ang_gap(b, _zone_bearing(z)) / 90.0)
                for z in rule.prefer if z in DIRECTIONS), default=0.0)
    av = max((max(0.0, 1.0 - _ang_gap(b, _zone_bearing(z)) / 90.0)
              for z in rule.avoid if z in DIRECTIONS), default=0.0)
    if CENTRE in rule.avoid and in_centre:
        av = 1.0
    if CENTRE in rule.prefer and in_centre:
        pref = 1.0
    return min(1.0, max(0.0, n + (1.0 - n) * pref - n * av))


def _vastu_targets(ctx: _Ctx) -> list[tuple[str, str, tuple[float, float], list[str]]]:
    """(target_key, label, point, element_ids) for each thing vastu cares about."""
    tg = []
    fd = next((o for o in ctx.plan.openings if o.kind == "front_door"), None)
    if fd is not None:
        w = ctx.walls.get(fd.wall_id)
        if w is not None:
            t = min(max(fd.position, 0.0), 1.0)
            tg.append(("front_door", "main entrance",
                       (w.start.x + t * (w.end.x - w.start.x),
                        w.start.y + t * (w.end.y - w.start.y)), [fd.id]))
    beds = [r for r in ctx.rooms if r.category == "bedroom" and r.id in ctx.polys]
    master = next((r for r in beds if _MASTER_RE.search(r.name)), None)
    if master is None and beds:
        master = max(beds, key=lambda r: ctx.polys[r.id].area)
    for r in ctx.rooms:
        p = ctx.polys.get(r.id)
        if p is None:
            continue
        c = p.centroid
        pt = (c.x, c.y)
        if _POOJA_RE.search(r.name):
            tg.append(("pooja", r.name, pt, [r.id]))
            continue
        if _STAIR_RE.search(r.name):
            tg.append(("stairs", r.name, pt, [r.id]))
            continue
        if _is_passage(r):
            continue          # circulation has no vastu zone preference here
        if master is not None and r.id == master.id:
            tg.append(("bedroom_master", r.name, pt, [r.id]))
        elif r.category == "bedroom":
            tg.append(("bedroom", r.name, pt, [r.id]))
        elif r.category in ("kitchen", "bathroom", "living"):
            tg.append((r.category, r.name, pt, [r.id]))
    return tg


def vastu_score(plan: Plan, profile: CityProfile | None = None,
                ctx: _Ctx | None = None) -> tuple[float, list[Finding]]:
    """Continuous 0..1 vastu score plus per-rule findings.

    Never an `error`. The rules are a weighted, replaceable data table
    (`CityProfile.vastu`); practitioners disagree and clients override, so the
    engine's job is to report a gradient, not to refuse a plan.
    """
    profile = profile or BENGALURU
    ctx = ctx or _build_ctx(plan, None, profile)
    vp = profile.vastu
    north = plan.site.north_deg
    cx, cy = ctx.centre
    minx, miny, maxx, maxy = ctx.bbox
    if maxx <= minx or maxy <= miny:
        return 1.0, []

    cf = vp.centre_fraction
    c_minx = minx + (1 - cf) / 2 * (maxx - minx)
    c_maxx = maxx - (1 - cf) / 2 * (maxx - minx)
    c_miny = miny + (1 - cf) / 2 * (maxy - miny)
    c_maxy = maxy - (1 - cf) / 2 * (maxy - miny)
    centre_box = Polygon([(c_minx, c_miny), (c_maxx, c_miny),
                          (c_maxx, c_maxy), (c_minx, c_maxy)])

    findings: list[Finding] = []
    num = den = 0.0
    for key, label, pt, eids in _vastu_targets(ctx):
        # weight 0 disables a rule outright - that is how a client switches off
        # a preference they do not hold, without editing code.
        rules = [r for r in vp.rules if key in r.applies_to and r.weight > 0]
        for rule in rules:
            b = bearing_deg(pt[0] - cx, pt[1] - cy, north)
            in_c = centre_box.contains(Point(pt))
            s = _zone_score(b, rule, in_c)
            num += rule.weight * s
            den += rule.weight
            if s < 0.5:
                z = zone_of_bearing(b)
                findings.append(Finding(
                    f"VASTU.{rule.key.upper()}", "warn",
                    round(rule.weight * (1.0 - s), 3),
                    f"{label} is {z} (bearing {b:.0f} deg); "
                    f"prefer {'/'.join(rule.prefer) or 'n/a'}"
                    f"{', avoid ' + '/'.join(rule.avoid) if rule.avoid else ''}"
                    f" - {rule.detail}", eids, round(s, 3), 0.5))

    # Brahmasthan: soft by construction. On a 600 sqft plot keeping the central
    # ninth open costs ~11% of the buildable footprint, which is why this is a
    # tunable weight and not a constraint.
    occ_area = 0.0
    for rid, p in ctx.polys.items():
        occ_area += p.intersection(centre_box).area
    occ = occ_area / centre_box.area if centre_box.area > 0 else 0.0
    cap = vp.brahmasthan_max_occupancy
    bs = min(1.0, max(0.0, 1.0 - max(0.0, occ - cap) / max(1e-9, 1.0 - cap)))
    num += vp.brahmasthan_weight * bs
    den += vp.brahmasthan_weight
    if bs < 0.5 and vp.brahmasthan_weight > 0:
        findings.append(Finding(
            "VASTU.BRAHMASTHAN", "warn", round(vp.brahmasthan_weight * (1 - bs), 3),
            f"central ninth (Brahmasthan) is {100*occ:.0f}% built over; "
            f"soft target <= {100*cap:.0f}%", [], round(occ, 3), cap))

    score = num / den if den > 0 else 1.0
    return round(score, 4), findings


# ------------------------------------------------------------------- entry ---

def validate(plan: Plan, brief: dict | None = None,
             profile: CityProfile | None = None,
             rules: Any = None) -> list[Finding]:
    """All six families, most severe first, filtered by `rules`.

    `rules` is a `policy.RuleConfig`. It exists because these families are not
    the same kind of statement: GEO is arithmetic, NBC is law, VASTU is a client
    preference, DESIGN and TYPO are domain judgement. A user who disagrees with
    our judgement should be able to switch it off without losing the arithmetic.
    Passing None keeps everything on, which is the safe default.
    """
    profile = profile or BENGALURU
    brief = brief or {}
    ctx = _build_ctx(plan, brief, profile)
    fs = check_geometry(ctx)
    fs += check_nbc(ctx)
    fs += check_bylaws(ctx)
    fs += check_circulation(ctx)
    fs += check_typology(ctx)
    fs += check_design_quality(ctx)
    fs += check_layout_sense(ctx)
    fs += check_bathroom_topology(ctx)
    fs += check_zoning(ctx)
    fs += check_syntax(ctx)
    fs += check_standards(ctx)
    # Vastu BEFORE check_brief: `BRIEF.ZONE_UNMET` reads the vastu findings to
    # tell "a zone we prefer" from "a zone the client asked for and did not
    # get", and nothing wrote them. Running vastu last meant that rule read an
    # absent key on every plan and could never fire.
    if brief.get("vastu", True):
        _s, vf = vastu_score(plan, profile, ctx)
        ctx.brief["_vastu_findings"] = list(vf)
        fs += vf
    fs += check_brief(ctx)

    if rules is not None:
        kept: list[Finding] = []
        for f in fs:
            if not rules.allows(f.rule_id):
                continue
            sev = rules.severity_for(f.rule_id, f.severity)
            if sev == "off":
                continue
            if sev != f.severity:
                f = replace(f, severity=sev)
            kept.append(f)
        fs = kept
    return sort_findings(fs)


def report(plan: Plan, brief: dict | None = None,
           profile: CityProfile | None = None) -> dict:
    """Validator output shaped for the generate/critique loop's reward term."""
    profile = profile or BENGALURU
    brief = brief or {}
    ctx = _build_ctx(plan, brief, profile)
    fs = check_geometry(ctx) + check_nbc(ctx) + check_bylaws(ctx)
    vs, vf = vastu_score(plan, profile, ctx)
    fs = sort_findings(fs + vf)
    errs = [f for f in fs if f.severity == "error"]
    return {
        "findings": fs,
        "n_error": len(errs),
        "n_warn": len(fs) - len(errs),
        "error_weight": sum(f.weight for f in errs),
        "vastu_score": vs,
        "hard_ok": not errs,
    }
