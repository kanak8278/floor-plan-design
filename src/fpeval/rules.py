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
from dataclasses import dataclass, field
from typing import Iterable, Literal, Optional

from shapely.geometry import Polygon, LineString, Point, MultiPolygon
from shapely.ops import unary_union, polygonize
from shapely.prepared import prep

from .ir import Plan, Wall, Opening, Room, P
from .bylaws import (CityProfile, PlotBand, NBCMinima, VastuProfile, VastuRule,
                     BENGALURU, DIRECTIONS, CENTRE, SQFT_M2)

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

_PASSAGE_RE = re.compile(r"passage|corridor|hall\b|hallway|lobby|circulation", re.I)
_POOJA_RE = re.compile(r"pooja|puja|mandir|prayer", re.I)
_STAIR_RE = re.compile(r"stair|staircase", re.I)
_MASTER_RE = re.compile(r"master", re.I)
_WC_RE = re.compile(r"\bwc\b|water closet|toilet", re.I)

HABITABLE = {"living", "bedroom"}
NON_HABITABLE = {"balcony", "storage"}


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

    # walls
    for w in plan.walls:
        if w.length < MIN_WALL_MM:
            out.append(Finding("GEO.WALL_TOO_SHORT", "error", 0.5,
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

    seen = set(roots)
    stack = list(roots)
    while stack:
        cur = stack.pop()
        for nb in adj.get(cur, ()):
            if nb not in seen:
                seen.add(nb)
                stack.append(nb)

    cats = {r.id: r.category for r in ctx.rooms}
    names = {r.id: r.name for r in ctx.rooms}
    for rid in ctx.polys:
        if rid in seen:
            continue
        # Balconies and stores are routinely entered through a sliding unit the
        # source data labels a window, so they warn instead of erroring.
        soft = cats.get(rid) in NON_HABITABLE
        out.append(Finding(
            "GEO.UNREACHABLE_ROOM", "warn" if soft else "error",
            0.6 if soft else 1.0,
            f"room {names.get(rid, rid)!r} ({cats.get(rid)}) is not reachable "
            f"from the entrance through doors", [rid]))
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
           if r.category in HABITABLE and r.id in ctx.polys and not _is_passage(r)]
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

        if r.category in HABITABLE:
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

    cands = [rect(*sides)] if sides[0] == sides[1] else [rect(*sides), rect(*sides[::-1])]
    best, best_out = None, None
    for c in cands:
        g = c.intersection(plot)
        if best is None or g.area > best.area:
            best = g
    meta = {"front_mm": f, "rear_mm": r, "side_mm": s,
            "depth_mm": depth, "width_mm": width,
            "side_both": band.side.both_sides}
    return best, meta


def check_bylaws(ctx: _Ctx) -> list[Finding]:
    out: list[Finding] = []
    prof = ctx.profile

    plot = ctx.plot
    plot_area_m2 = ctx.brief.get("plot_area_m2")
    if plot is not None and plot_area_m2 is None:
        plot_area_m2 = plot.area / 1e6

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
        rules = [r for r in vp.rules if key in r.applies_to]
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
    if bs < 0.5:
        findings.append(Finding(
            "VASTU.BRAHMASTHAN", "warn", round(vp.brahmasthan_weight * (1 - bs), 3),
            f"central ninth (Brahmasthan) is {100*occ:.0f}% built over; "
            f"soft target <= {100*cap:.0f}%", [], round(occ, 3), cap))

    score = num / den if den > 0 else 1.0
    return round(score, 4), findings


# ------------------------------------------------------------------- entry ---

def validate(plan: Plan, brief: dict | None = None,
             profile: CityProfile | None = None) -> list[Finding]:
    """All four families, most severe first."""
    profile = profile or BENGALURU
    brief = brief or {}
    ctx = _build_ctx(plan, brief, profile)
    fs = check_geometry(ctx)
    fs += check_nbc(ctx)
    fs += check_bylaws(ctx)
    if brief.get("vastu", True):
        _s, vf = vastu_score(plan, profile, ctx)
        fs += vf
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
