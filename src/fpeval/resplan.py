"""ResPlan -> canonical IR.

Key finding driving this implementation: recovering wall centrelines by
skeletonising ResPlan's `wall` polygon mass FAILS (median rebuild IoU 0.37,
17% slivers, junction blocks misread as thick walls).

The working inversion goes through the **room tiling**: ResPlan room polygons
tile `inner` cleanly (self-overlap ~0.00%) and 99.75% of their edges are
axis-aligned, so the shared edges between adjacent rooms *are* the interior
wall centrelines and the tiling's outer boundary is the exterior wall.

Three defects fixed relative to the first cut:

1. **Wall fragmentation.** `linemerge` breaks a chain at every degree>=3 node,
   so collinear segments meeting at a T-junction stayed separate walls (median
   48/plan, p90 78, against ~20-30 in a real plan). Replaced with a *collinear
   interval merge*: every noded boundary segment is bucketed by the infinite
   line it lies on, and the 1-D intervals inside a bucket are unioned. Runs may
   therefore span tees, which is what OpenPlan3D wants -- its `detectRooms` has
   an explicit `splitWallsAtTJunctions` pass.
2. **Opening hosting.** The old host search inferred the opening's axis from its
   oriented bounding box and rejected any wall more than 12 degrees off it. That
   fails on near-square door stubs (aspect ~1.3, axis undefined) and on
   openings sitting on a diagonal wall. Primary hosting is now geometric: the
   host is the wall whose *centreline chord through the opening polygon* is
   longest. Measured on 200 plans, 99.73% of openings have a wall centreline
   passing through them and the chord extent matches the OBB long side to
   0.0000 relative error, so this is strictly more robust at equal accuracy.
3. **`oriented_envelope` warnings.** These are NOT caused by degenerate
   polygons. GEOS' rotating-calipers divides by an edge slope, so it emits
   `divide by zero` / `invalid value` for *any axis-aligned* rectangle -- i.e.
   for ~all 29k openings in the first 2000 plans, while still returning the
   correct rectangle. Rather than filter the warning we compute the oriented
   bounding box ourselves (`_obb`), which is warning-free, faster on 5-point
   polygons, and lets us count genuinely degenerate inputs explicitly.
"""
from __future__ import annotations
import math
from typing import Any, Iterable
from shapely.geometry import Polygon, LineString, Point
from shapely.ops import unary_union, linemerge
from shapely.strtree import STRtree
from shapely import affinity

from .ir import Plan, Wall, Opening, Room, Site, P

ROOM_KEYS = ["living", "kitchen", "bedroom", "bathroom", "balcony", "storage"]
DISPLAY = {"living": "Living", "kitchen": "Kitchen", "bedroom": "Bedroom",
           "bathroom": "Bathroom", "balcony": "Balcony", "storage": "Storage"}
OPENING_KEYS = (("door", "door"), ("window", "window"), ("front_door", "front_door"))

# ResPlan coordinates are NOT metric. `wall_depth` is the only physical anchor:
# anchoring on it and treating the `area` field as truth implies ~226 mm walls
# (median over 4000 plans), consistent with 230 mm brick + plaster.
ASSUMED_WALL_MM = 226.0

SNAP_UNITS = 0.5          # coordinate-space snap grid (probed: 0.34% median area error)
COLLINEAR_TOL_DEG = 1.0
MIN_WALL_MM = 1.0         # shorter than this is a sliver, not a wall
MIN_OPENING_MM = 300      # narrowest opening we will emit
# Above this aspect ratio an opening polygon is a zero-thickness sliver, i.e. a
# corrupt label rather than a real opening. Measured max on a real opening: 12.
SLIVER_ASPECT = 50.0
ROOM_WALL_TOL_MM = 30.0   # how far a wall centreline may sit off a room edge
# Sub-wall-thickness jogs in the source room polygons are the dominant cause of
# wall fragmentation (see `_align_axes`). Coordinates closer than this collapse
# onto one support line. Tuned by sweep in tests/run_corpus.py --sweep-align.
AXIS_CLUSTER_MM = 75.0    # ~1/3 wall thickness; knee of the sweep
# Alignment may not make rooms double-claim space. Reverting on pure float
# noise costs plans their wall-count fix for nothing, so only a material
# increase in room-on-room overlap triggers a revert.
ALIGN_OVERLAP_SLACK = 0.002
# A wall merely *crossing* a room boundary overlaps the tolerance band by
# ~2*tol; a wall *bounding* the room overlaps by a full edge length. This
# threshold separates the two cases.
ROOM_WALL_MIN_OVERLAP_MM = 5.0 * ROOM_WALL_TOL_MM
AREA_EPS = 1e-9


def geoms(g) -> list:
    if g is None or getattr(g, "is_empty", True):
        return []
    return list(g.geoms) if hasattr(g, "geoms") else [g]


def new_degenerate_counters() -> dict[str, int]:
    """Explicit counters for every geometry we refuse to trust."""
    return {
        "room_non_polygon": 0,
        "room_zero_area": 0,
        "room_snap_failed": 0,
        "room_align_reverted": 0,
        "plan_align_reverted": 0,
        "opening_non_polygon": 0,
        "opening_zero_area": 0,
        "opening_invalid": 0,
        "opening_obb_degenerate": 0,
        "opening_sub_min_width": 0,
        "opening_wider_than_wall": 0,
        "opening_sliver_polygon": 0,
        "wall_sliver_dropped": 0,
        "plot_non_polygon": 0,
    }


def scale_mm_per_unit(plan: dict[str, Any]) -> float:
    wd = plan.get("wall_depth") or 0.0
    if wd <= 0:
        raise ValueError("plan has no usable wall_depth")
    return ASSUMED_WALL_MM / wd


# --------------------------------------------------------------------------
# oriented bounding box, without GEOS' oriented_envelope
# --------------------------------------------------------------------------

def _obb(poly: Polygon) -> tuple[float, float, float] | None:
    """Minimal-area bounding box of `poly` -> (long_side, short_side, angle).

    `angle` is the bearing of the *long* side in radians. Returns None if the
    input has no 2-D extent (collinear / repeated vertices), which is the only
    genuinely degenerate case.

    Implemented by rotating calipers over the convex hull. Unlike
    `shapely.oriented_envelope` this never divides by an edge slope, so it is
    silent on the axis-aligned rectangles that make up essentially all of
    ResPlan's opening polygons.
    """
    try:
        hull = poly.convex_hull
    except Exception:
        return None
    if hull.geom_type != "Polygon" or hull.is_empty:
        return None
    pts = list(hull.exterior.coords)[:-1]
    if len(pts) < 3:
        return None

    # Axis-aligned fast path: if every hull edge is axis-parallel the minimal
    # rectangle is the axis-aligned bounds. Covers >99% of ResPlan openings.
    axis_aligned = True
    for i in range(len(pts)):
        ax, ay = pts[i]
        bx, by = pts[(i + 1) % len(pts)]
        if abs(bx - ax) > 1e-9 and abs(by - ay) > 1e-9:
            axis_aligned = False
            break
    if axis_aligned:
        x0, y0, x1, y1 = hull.bounds
        dx, dy = x1 - x0, y1 - y0
        if dx <= 0.0 or dy <= 0.0:
            return None
        return (dx, dy, 0.0) if dx >= dy else (dy, dx, math.pi / 2.0)

    best: tuple[float, float, float, float] | None = None
    for i in range(len(pts)):
        ax, ay = pts[i]
        bx, by = pts[(i + 1) % len(pts)]
        ex, ey = bx - ax, by - ay
        L = math.hypot(ex, ey)
        if L < 1e-12:
            continue
        ux, uy = ex / L, ey / L
        us = [px * ux + py * uy for px, py in pts]
        vs = [-px * uy + py * ux for px, py in pts]
        du = max(us) - min(us)
        dv = max(vs) - min(vs)
        area = du * dv
        if best is None or area < best[0] - 1e-12:
            best = (area, du, dv, math.atan2(uy, ux))
    if best is None:
        return None
    _, du, dv, ang = best
    if du <= 0.0 or dv <= 0.0:
        return None
    if du >= dv:
        return du, dv, ang
    return dv, du, ang + math.pi / 2.0


# --------------------------------------------------------------------------
# room polygons
# --------------------------------------------------------------------------

def _snap_poly(g: Polygon, s: float) -> Polygon | None:
    def snap_ring(coords):
        return [(round(x / s) * s, round(y / s) * s) for x, y in coords]
    try:
        p = Polygon(snap_ring(g.exterior.coords),
                    [snap_ring(r.coords) for r in g.interiors]).buffer(0)
    except Exception:
        return None
    if p.is_empty or not p.is_valid:
        return None
    # buffer(0) can yield a MultiPolygon; take the largest part
    parts = geoms(p)
    parts = [q for q in parts if isinstance(q, Polygon) and q.area > AREA_EPS]
    return max(parts, key=lambda q: q.area) if parts else None


def _rooms_raw(plan: dict[str, Any], s: float,
               deg: dict[str, int] | None = None) -> list[tuple[str, Polygon]]:
    deg = deg if deg is not None else new_degenerate_counters()
    out = []
    for key in ROOM_KEYS:
        for g in geoms(plan.get(key)):
            if not isinstance(g, Polygon):
                deg["room_non_polygon"] += 1
                continue
            if g.area <= 1e-6:
                deg["room_zero_area"] += 1
                continue
            sp = _snap_poly(g, s)
            if sp is None or sp.area <= 1e-6:
                deg["room_snap_failed"] += 1
                continue
            out.append((key, sp))
    return out


# --------------------------------------------------------------------------
# axis alignment: collapse sub-wall-thickness jogs
# --------------------------------------------------------------------------

def _cluster_1d(weighted: dict[float, float], tol: float,
                max_span: float) -> dict[float, float]:
    """Single-linkage cluster of 1-D coordinates -> {value: representative}.

    The representative of a cluster is its heaviest member, where weight is the
    total length of axis-parallel room edges lying on that coordinate. That
    keeps long walls exactly where the data put them and moves only the jogs.
    `max_span` stops a staircase of evenly spaced coordinates (a discretised
    diagonal wall) from chain-collapsing onto a single line.
    """
    vals = sorted(weighted)
    out: dict[float, float] = {}
    i = 0
    while i < len(vals):
        j = i + 1
        while (j < len(vals) and vals[j] - vals[j - 1] <= tol
               and vals[j] - vals[i] <= max_span):
            j += 1
        group = vals[i:j]
        rep = max(group, key=lambda v: (weighted[v], -abs(v - group[0])))
        for v in group:
            out[v] = rep
        i = j
    return out


def _align_axes(rooms_typed: list[tuple[str, Polygon]], mm: float, tol_mm: float,
                deg: dict[str, int] | None = None) -> list[tuple[str, Polygon]]:
    """Snap near-coincident axis-parallel support lines together.

    Motivation, measured: ResPlan room polygons carry many sub-wall-thickness
    steps (18.6% of raw room edges are shorter than one wall thickness, 11.7%
    shorter than 50 mm). Each such jog forks one long wall into two walls on
    *different* support lines plus a stub, so the wall count multiplies. This
    -- not `linemerge` breaking at tees -- is the dominant fragmentation driver:
    the collinear interval merge alone only takes the median from 48 to 47.

    All rooms are rewritten against one shared coordinate vocabulary, so the
    tiling stays watertight and rooms keep sharing their edges exactly.
    """
    deg = deg if deg is not None else new_degenerate_counters()
    if tol_mm <= 0 or not rooms_typed:
        return rooms_typed
    tol = tol_mm / mm
    max_span = 2.0 * tol

    wx: dict[float, float] = {}   # x of vertical edges   -> total length
    wy: dict[float, float] = {}   # y of horizontal edges -> total length
    for _, poly in rooms_typed:
        for ring in [poly.exterior] + list(poly.interiors):
            cs = list(ring.coords)
            for k in range(len(cs) - 1):
                (ax, ay), (bx, by) = cs[k], cs[k + 1]
                if ax == bx:
                    wx[ax] = wx.get(ax, 0.0) + abs(by - ay)
                elif ay == by:
                    wy[ay] = wy.get(ay, 0.0) + abs(bx - ax)
                else:
                    wx.setdefault(ax, 0.0); wx.setdefault(bx, 0.0)
                    wy.setdefault(ay, 0.0); wy.setdefault(by, 0.0)
    mx = _cluster_1d(wx, tol, max_span)
    my = _cluster_1d(wy, tol, max_span)

    def fix(ring) -> list[tuple[float, float]]:
        pts = [(mx.get(x, x), my.get(y, y)) for x, y in ring.coords]
        ded = [pts[0]]
        for p in pts[1:]:
            if p != ded[-1]:
                ded.append(p)
        if ded[0] != ded[-1]:
            ded.append(ded[0])
        return ded

    out: list[tuple[str, Polygon]] = []
    for cat, poly in rooms_typed:
        aligned = None
        ext = fix(poly.exterior)
        if len(ext) >= 4:
            ints = [r for r in (fix(h) for h in poly.interiors) if len(r) >= 4]
            try:
                q = Polygon(ext, ints).buffer(0)
                parts = [g for g in geoms(q)
                         if isinstance(g, Polygon) and g.area > AREA_EPS]
                if parts:
                    cand = max(parts, key=lambda g: g.area)
                    # A room narrower than the tolerance in one direction can be
                    # crushed when its two bounding lines cluster together. Keep
                    # the unaligned polygon in that case: losing a room from the
                    # IR is far worse than one room re-fragmenting its walls.
                    if cand.area >= 0.75 * poly.area:
                        aligned = cand
            except Exception:
                aligned = None
        if aligned is None:
            deg["room_align_reverted"] += 1
            aligned = poly
        out.append((cat, aligned))

    # Alignment must not make two rooms materially double-claim space. Safety
    # net only: at tol=75 mm with ALIGN_OVERLAP_SLACK it fired on 0/1500 plans,
    # because ResPlan's own tiling already carries the overlap we see (p99
    # 2.3%, max 6.7% of room area, entirely inherited from the source).
    if _overlap_frac(out) > _overlap_frac(rooms_typed) + ALIGN_OVERLAP_SLACK:
        deg["plan_align_reverted"] = 1
        return rooms_typed
    return out


def _overlap_frac(rooms_typed: list[tuple[str, Polygon]]) -> float:
    tot = sum(p.area for _, p in rooms_typed)
    if tot <= 0:
        return 0.0
    try:
        u = unary_union([p for _, p in rooms_typed]).area
    except Exception:
        return 0.0
    return max(0.0, (tot - u) / tot)


# --------------------------------------------------------------------------
# wall extraction: collinear interval merge
# --------------------------------------------------------------------------

_QC = 1e6           # quantisation for the offset of a diagonal support line
_DIAG_TOL = 1e-6


def _line_key(a: tuple[float, float], b: tuple[float, float]):
    """Canonical identity of the infinite line through a and b.

    Coordinates are snapped to SNAP_UNITS, so the axis-aligned keys are exact
    integers and the two collinear halves of a wall interrupted by a tee land in
    the same bucket. Also returns the unit direction used to build 1-D
    intervals along that line.
    """
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    if dy == 0.0 and dx != 0.0:
        return ("H", round(ay / SNAP_UNITS)), (1.0, 0.0)
    if dx == 0.0 and dy != 0.0:
        return ("V", round(ax / SNAP_UNITS)), (0.0, 1.0)
    L = math.hypot(dx, dy)
    if L == 0.0:
        return None, None
    ux, uy = dx / L, dy / L
    # canonicalise the direction sign so a segment and its reverse agree
    if (ux < 0.0) or (ux == 0.0 and uy < 0.0):
        ux, uy = -ux, -uy
    nx, ny = -uy, ux
    c = nx * ax + ny * ay
    return ("D", round(nx * _QC), round(ny * _QC), round(c * _QC)), (ux, uy)


def _proj(p: tuple[float, float], d: tuple[float, float]) -> float:
    return p[0] * d[0] + p[1] * d[1]


def _elementary_segments(g) -> Iterable[tuple[tuple, tuple]]:
    for part in geoms(g):
        if not isinstance(part, LineString):
            continue
        cs = list(part.coords)
        for i in range(len(cs) - 1):
            if cs[i] != cs[i + 1]:
                yield cs[i], cs[i + 1]


class WallIndex:
    """Merged wall runs plus the support-line buckets used to build them.

    Keeping the buckets lets `_assign_room_walls` map a room's boundary edge
    back to its wall *exactly* (same line, containing interval) instead of
    guessing from a buffered midpoint test -- which a merged wall spanning a
    T-junction would fail, because its midpoint can sit outside the room.
    """

    def __init__(self) -> None:
        self.walls: list[Wall] = []
        # line key -> (unit direction, [(lo, hi, wall_id), ...]) in unit space
        self.groups: dict[Any, tuple[tuple[float, float], list]] = {}

    def find(self, key, lo: float, hi: float, tol: float) -> str | None:
        entry = self.groups.get(key)
        if entry is None:
            return None
        for elo, ehi, wid in entry[1]:
            if elo - tol <= lo and hi <= ehi + tol:
                return wid
        return None


def _extract_walls(rooms: list[Polygon], mm: float, thickness_mm: int,
                   deg: dict[str, int] | None = None,
                   split_crossings: bool = True) -> WallIndex:
    deg = deg if deg is not None else new_degenerate_counters()
    idx = WallIndex()
    if not rooms:
        return idx
    boundary = unary_union([r.boundary for r in rooms])   # nodes at all intersections

    # bucket every noded segment by its support line, as a 1-D interval
    buckets: dict[Any, tuple[tuple[float, float], list[tuple[float, float]]]] = {}
    for a, b in _elementary_segments(boundary):
        key, d = _line_key(a, b)
        if key is None:
            continue
        ta, tb = _proj(a, d), _proj(b, d)
        lo, hi = (ta, tb) if ta <= tb else (tb, ta)
        if key not in buckets:
            buckets[key] = (d, [])
        buckets[key][1].append((lo, hi))

    eps = SNAP_UNITS * 1e-3
    for key in sorted(buckets, key=lambda k: (str(k[0]), k[1:])):
        d, ivs = buckets[key]
        ivs.sort()
        merged: list[list[float]] = []
        for lo, hi in ivs:
            if merged and lo <= merged[-1][1] + eps:
                merged[-1][1] = max(merged[-1][1], hi)
            else:
                merged.append([lo, hi])
        # recover the 2-D endpoints of each merged run. The run lies on the line
        # {p : p = base + t*d}; base is any point on the line with t == 0.
        base = _line_base(key, d)
        entries = []
        for lo, hi in merged:
            a = (base[0] + d[0] * lo, base[1] + d[1] * lo)
            b = (base[0] + d[0] * hi, base[1] + d[1] * hi)
            if math.hypot(b[0] - a[0], b[1] - a[1]) * mm < MIN_WALL_MM:
                deg["wall_sliver_dropped"] += 1
                continue
            wid = f"w{len(idx.walls)}"
            idx.walls.append(Wall(
                id=wid,
                start=P(round(a[0] * mm), round(a[1] * mm)),
                end=P(round(b[0] * mm), round(b[1] * mm)),
                thickness=thickness_mm,
            ))
            entries.append((lo, hi, wid))
        if entries:
            idx.groups[key] = (d, entries)
    if split_crossings:
        _split_at_crossings(idx, mm)
    return idx


def _split_at_crossings(idx: WallIndex, mm: float) -> None:
    """Split merged runs where two walls cross with no endpoint at the crossing.

    Merging collinear runs across a **T**-junction is safe and preferred:
    OpenPlan3D's `detectRooms` runs `splitWallsAtTJunctions`, which splits a
    wall wherever another wall's *endpoint* lands on its interior.

    An **X**-crossing (the `+` where four rooms meet) has no endpoint at the
    crossing, so that pass cannot split it -- and the two faces either side
    then merge into one. Measured on 2,000 Projects through the real
    `detectRooms`: leaving crossings merged made it detect *fewer* rooms than
    the IR contains on 3.35% of plans. Pre-splitting here is the fix, and it
    costs only the handful of walls that actually cross.
    """
    walls = idx.walls
    if len(walls) < 2:
        return
    lines = [LineString([w.start.as_tuple(), w.end.as_tuple()]) for w in walls]
    tree = STRtree(lines)
    ends = [(w.start.as_tuple(), w.end.as_tuple()) for w in walls]
    eps = 1.0                      # mm; endpoints are integers
    cuts: dict[int, set[float]] = {}

    for i, li in enumerate(lines):
        for j in tree.query(li):
            j = int(j)
            if j <= i:
                continue
            lj = lines[j]
            try:
                inter = li.intersection(lj)
            except Exception:
                continue
            if inter.is_empty or inter.geom_type != "Point":
                continue          # collinear overlaps already merged; skip
            px, py = inter.x, inter.y
            interior = True
            for k in (i, j):
                for ex, ey in ends[k]:
                    if math.hypot(px - ex, py - ey) <= eps:
                        interior = False
                        break
                if not interior:
                    break
            if not interior:
                continue          # a tee: detectRooms handles it
            for k in (i, j):
                w = walls[k]
                L = w.length
                if L <= 0:
                    continue
                t = (((px - w.start.x) * (w.end.x - w.start.x)
                      + (py - w.start.y) * (w.end.y - w.start.y)) / (L * L))
                if eps / L < t < 1.0 - eps / L:
                    cuts.setdefault(k, set()).add(t)
    if not cuts:
        return

    new_walls: list[Wall] = []
    remap: dict[str, list[str]] = {}
    for k, w in enumerate(walls):
        ts = sorted(cuts.get(k, ()))
        pieces: list[tuple[tuple[int, int], tuple[int, int]]] = []
        prev = (w.start.x, w.start.y)
        for t in ts:
            px = round(w.start.x + t * (w.end.x - w.start.x))
            py = round(w.start.y + t * (w.end.y - w.start.y))
            if (px, py) != prev:
                pieces.append((prev, (px, py)))
                prev = (px, py)
        if prev != (w.end.x, w.end.y):
            pieces.append((prev, (w.end.x, w.end.y)))
        if not pieces:
            pieces = [((w.start.x, w.start.y), (w.end.x, w.end.y))]
        made = []
        for a, b in pieces:
            if math.hypot(b[0] - a[0], b[1] - a[1]) < MIN_WALL_MM:
                continue
            wid = f"w{len(new_walls)}"
            new_walls.append(Wall(id=wid, start=P(*a), end=P(*b),
                                  thickness=w.thickness, height=w.height))
            made.append(wid)
        remap[w.id] = made or []
    idx.walls = new_walls
    by_id = {w.id: w for w in new_walls}
    # rebuild the support-line buckets so `_assign_room_walls` still resolves
    for key, (d, entries) in list(idx.groups.items()):
        rebuilt = []
        for lo, hi, wid in entries:
            for nwid in remap.get(wid, []):
                w = by_id[nwid]
                ta = _proj((w.start.x / mm, w.start.y / mm), d)
                tb = _proj((w.end.x / mm, w.end.y / mm), d)
                rebuilt.append((min(ta, tb), max(ta, tb), nwid))
        idx.groups[key] = (d, sorted(rebuilt))


def _line_base(key, d: tuple[float, float]) -> tuple[float, float]:
    """A point on the support line whose projection onto `d` is 0."""
    if key[0] == "H":
        return (0.0, key[1] * SNAP_UNITS)          # d = (1, 0) -> proj = x
    if key[0] == "V":
        return (key[1] * SNAP_UNITS, 0.0)          # d = (0, 1) -> proj = y
    nx, ny, c = key[1] / _QC, key[2] / _QC, key[3] / _QC
    return (nx * c, ny * c)                        # foot of the perpendicular


# --------------------------------------------------------------------------
# openings
# --------------------------------------------------------------------------

def _wall_extent(w: Wall, geom) -> tuple[float, float] | None:
    """Extent of `geom`'s coordinates projected onto wall w, as (t_lo, t_hi)."""
    wx, wy = w.end.x - w.start.x, w.end.y - w.start.y
    ll = wx * wx + wy * wy
    if ll <= 0.0:
        return None
    ts = []
    for part in geoms(geom):
        cs = getattr(part, "coords", None)
        if cs is None:
            continue
        for x, y in cs:
            ts.append(((x - w.start.x) * wx + (y - w.start.y) * wy) / ll)
    if not ts:
        return None
    return min(ts), max(ts)


def _host_openings(plan: dict[str, Any], walls: list[Wall], mm: float,
                   thickness_mm: int,
                   deg: dict[str, int] | None = None) -> tuple[list[Opening], dict]:
    """Attach door/window polygons to a host wall as (wall_id, position, width).

    Primary rule: the host is the wall whose centreline chord through the
    opening polygon has the largest extent along that wall. ResPlan openings are
    wall-thickness x opening-width strips laid over the shared room edge, so the
    wall centreline runs straight down the middle of the strip and the chord
    extent *is* the opening width.

    Fallback rule (for openings the centrelines miss -- 0.27%): nearest wall by
    perpendicular distance, with a parallelism gate that is only applied when
    the opening's oriented box has a well-defined long axis (aspect >= 1.6).
    The old code always applied the gate, which is why near-square door stubs
    sitting *directly on* a wall were rejected.
    """
    deg = deg if deg is not None else new_degenerate_counters()
    openings: list[Opening] = []
    stats = {"total": 0, "hosted": 0, "by_kind": {}, "unhosted_kind": {},
             "via_chord": 0, "via_fallback": 0, "unhosted": []}
    tol = thickness_mm * 1.5 + 60.0    # perpendicular tolerance, mm

    lines = [LineString([w.start.as_tuple(), w.end.as_tuple()]) for w in walls]
    tree = STRtree(lines) if lines else None

    def bump(kind: str, field: str) -> None:
        d = stats["by_kind"].setdefault(kind, {"total": 0, "hosted": 0})
        d[field] += 1

    for kind, key in OPENING_KEYS:
        for g in geoms(plan.get(key)):
            stats["total"] += 1
            bump(kind, "total")
            if not isinstance(g, Polygon):
                deg["opening_non_polygon"] += 1
                stats["unhosted_kind"][kind] = stats["unhosted_kind"].get(kind, 0) + 1
                stats["unhosted"].append({"kind": kind, "reason": "non_polygon"})
                continue
            if g.area <= AREA_EPS:
                deg["opening_zero_area"] += 1
                stats["unhosted_kind"][kind] = stats["unhosted_kind"].get(kind, 0) + 1
                stats["unhosted"].append({"kind": kind, "reason": "zero_area"})
                continue
            if not g.is_valid:
                deg["opening_invalid"] += 1
                g = g.buffer(0)
                if not isinstance(g, Polygon) or g.is_empty:
                    stats["unhosted_kind"][kind] = stats["unhosted_kind"].get(kind, 0) + 1
                    stats["unhosted"].append({"kind": kind, "reason": "invalid"})
                    continue

            obb = _obb(g)
            if obb is None:
                deg["opening_obb_degenerate"] += 1
            gm = affinity.scale(g, xfact=mm, yfact=mm, origin=(0, 0))

            host = _host_by_chord(gm, walls, lines, tree)
            how = "chord"
            if host is None:
                host = _host_by_proximity(gm, obb, walls, mm, tol)
                how = "fallback"

            if host is None:
                stats["unhosted_kind"][kind] = stats["unhosted_kind"].get(kind, 0) + 1
                aspect = (obb[0] / obb[1]) if obb and obb[1] > 0 else None
                sliver = aspect is not None and aspect > SLIVER_ASPECT
                if sliver:
                    deg["opening_sliver_polygon"] += 1
                dmm, dang = _nearest_wall(gm, walls)
                stats["unhosted"].append({
                    "kind": kind,
                    "reason": ("sliver_polygon" if sliver
                               else "no_wall_within_tolerance"),
                    "centroid_units": [round(g.centroid.x, 3), round(g.centroid.y, 3)],
                    "obb_aspect": round(aspect, 3) if aspect is not None else None,
                    "nearest_wall_mm": None if dmm is None else round(dmm),
                    "nearest_wall_angle_deg": None if dang is None else round(dang, 1),
                })
                continue

            w, t, width_mm = host
            stats["hosted"] += 1
            bump(kind, "hosted")
            stats["via_chord" if how == "chord" else "via_fallback"] += 1
            width = int(round(width_mm))
            if width < MIN_OPENING_MM:
                deg["opening_sub_min_width"] += 1
                width = MIN_OPENING_MM
            # The opening must sit inside the wall run we actually emit. Wall
            # endpoints are rounded to integer mm and the width can have been
            # raised to MIN_OPENING_MM, so both can push the opening a few mm
            # past the end; clamp instead of shipping something OpenPlan3D
            # would draw hanging off the wall.
            wlen = w.length
            if width > wlen:
                deg["opening_wider_than_wall"] += 1
                width = max(1, int(wlen))
            half = 0.5 * width
            centre = min(max(t * wlen, half), max(wlen - half, half))
            t = centre / wlen if wlen > 0 else 0.5
            is_win = kind == "window"
            openings.append(Opening(
                id=f"o{len(openings)}", kind=kind, wall_id=w.id,
                position=round(t, 6), width=width,
                sill=900 if is_win else 0,
                head=2100,
            ))
    return openings, stats


def _host_by_chord(gm: Polygon, walls: list[Wall], lines: list[LineString],
                   tree) -> tuple[Wall, float, float] | None:
    if tree is None:
        return None
    best = None
    for i in tree.query(gm):
        i = int(i)
        try:
            inter = lines[i].intersection(gm)
        except Exception:
            continue
        if inter.is_empty:
            continue
        w = walls[i]
        ext = _wall_extent(w, inter)
        if ext is None:
            continue
        wl = w.length
        span = (ext[1] - ext[0]) * wl
        if span < MIN_WALL_MM:
            continue
        if best is None or span > best[0]:
            tmid = min(max(0.5 * (ext[0] + ext[1]), 0.0), 1.0)
            best = (span, w, tmid)
    if best is None:
        return None
    span, w, tmid = best
    return w, tmid, span


def _host_by_proximity(gm: Polygon, obb, walls: list[Wall], mm: float,
                       tol: float) -> tuple[Wall, float, float] | None:
    """Nearest-wall fallback. Parallelism is only demanded when the opening has
    a well-defined long axis; otherwise the axis is noise."""
    c = gm.centroid
    cx, cy = c.x, c.y
    if obb is None:
        long_mm, ang, aspect = math.sqrt(gm.area), None, 1.0
    else:
        long_mm = obb[0] * mm
        aspect = obb[0] / obb[1] if obb[1] > 0 else float("inf")
        ang = obb[2]
    gate = ang is not None and aspect >= 1.6

    best, best_dist = None, float("inf")
    for w in walls:
        wl = w.length
        if wl < MIN_WALL_MM:
            continue
        wx, wy = w.end.x - w.start.x, w.end.y - w.start.y
        if gate:
            wang = math.atan2(wy, wx)
            da = abs(math.degrees(wang - ang)) % 180.0
            da = min(da, 180.0 - da)
            if da > 12.0:
                continue
        t = ((cx - w.start.x) * wx + (cy - w.start.y) * wy) / (wl * wl)
        if not (-0.02 <= t <= 1.02):
            continue
        px, py = w.start.x + t * wx, w.start.y + t * wy
        dist = math.hypot(cx - px, cy - py)
        if dist < best_dist:
            best, best_dist = (w, min(max(t, 0.0), 1.0)), dist
    if best is None or best_dist > tol:
        return None
    w, t = best
    return w, t, long_mm


def _nearest_wall(gm: Polygon, walls: list[Wall]) -> tuple[float | None, float | None]:
    """Diagnostic for an unhosted opening: distance to and angle off the closest
    wall centreline. Lets the report say *why* something could not be hosted."""
    c = gm.centroid
    best = (None, None)
    bd = float("inf")
    for w in walls:
        L = w.length
        if L < MIN_WALL_MM:
            continue
        wx, wy = w.end.x - w.start.x, w.end.y - w.start.y
        t = min(max(((c.x - w.start.x) * wx + (c.y - w.start.y) * wy) / (L * L), 0.0), 1.0)
        d = math.hypot(c.x - (w.start.x + t * wx), c.y - (w.start.y + t * wy))
        if d < bd:
            bd = d
            best = (d, math.degrees(math.atan2(wy, wx)) % 180.0)
    return best


# --------------------------------------------------------------------------
# rooms
# --------------------------------------------------------------------------

def _assign_room_walls(rooms_typed: list[tuple[str, Polygon]], idx: WallIndex,
                       mm: float) -> list[Room]:
    """Map each room's boundary edges onto the merged wall runs.

    Exact path: an edge of the room boundary lies on some support line and its
    1-D interval is contained in exactly one merged run on that line, because
    the runs are maximal unions of the very segments the rooms contributed.
    Geometric path (diagonals whose key round-trips imperfectly): overlap length
    of the wall centreline with a tolerance band around the room boundary.
    """
    walls = idx.walls
    lines = [LineString([w.start.as_tuple(), w.end.as_tuple()]) for w in walls]
    tree = STRtree(lines) if lines else None
    tol = SNAP_UNITS * 1e-3
    # (mm-space tolerance band is built lazily in the fallback below)

    out: list[Room] = []
    counters: dict[str, int] = {}
    for i, (cat, poly) in enumerate(rooms_typed):
        counters[cat] = counters.get(cat, 0) + 1
        n = counters[cat]
        base = DISPLAY.get(cat, cat.title())
        name = base if n == 1 and cat in ("living", "kitchen") else f"{base} {n}"

        wids: list[str] = []
        seen: set[str] = set()
        missed = False
        rings = [poly.exterior] + list(poly.interiors)
        for ring in rings:
            cs = list(ring.coords)
            for j in range(len(cs) - 1):
                a, b = cs[j], cs[j + 1]
                if a == b:
                    continue
                key, d = _line_key(a, b)
                if key is None:
                    continue
                ta, tb = _proj(a, d), _proj(b, d)
                lo, hi = (ta, tb) if ta <= tb else (tb, ta)
                wid = idx.find(key, lo, hi, tol)
                if wid is None:
                    missed = True
                    continue
                if wid not in seen:
                    seen.add(wid)
                    wids.append(wid)

        if missed and tree is not None:
            # everything in mm space; `lines` and the tree are already mm
            bnd_mm = affinity.scale(poly.boundary, xfact=mm, yfact=mm,
                                    origin=(0, 0)).buffer(ROOM_WALL_TOL_MM)
            for k in tree.query(bnd_mm):
                k = int(k)
                if walls[k].id in seen:
                    continue
                try:
                    ov = lines[k].intersection(bnd_mm)
                except Exception:
                    continue
                if not ov.is_empty and ov.length >= ROOM_WALL_MIN_OVERLAP_MM:
                    seen.add(walls[k].id)
                    wids.append(walls[k].id)

        wids.sort(key=lambda s: int(s[1:]))
        pts = [P(round(x * mm), round(y * mm)) for x, y in poly.exterior.coords[:-1]]
        out.append(Room(id=f"r{i}", name=name, category=cat, wall_ids=wids,
                        polygon=pts, area=int(round(poly.area * mm * mm))))
    return out


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

def convert(plan: dict[str, Any], merge_collinear: bool = True,
            align_mm: float | None = None) -> Plan:
    """ResPlan plan dict -> canonical IR Plan.

    `merge_collinear=False` restores the pre-fix `linemerge` wall extraction and
    `align_mm=0` disables the axis-alignment pass. Both exist so the corpus
    runner can A/B the wall-count fix and prove face recovery does not regress;
    production callers should leave them at their defaults.
    """
    mm = scale_mm_per_unit(plan)
    thickness = int(round(ASSUMED_WALL_MM))
    deg = new_degenerate_counters()
    align = AXIS_CLUSTER_MM if align_mm is None else align_mm
    rooms_typed = _rooms_raw(plan, SNAP_UNITS, deg)
    if not rooms_typed:
        raise ValueError("no usable room polygons")
    rooms_typed = _align_axes(rooms_typed, mm, align, deg)
    if not rooms_typed:
        raise ValueError("axis alignment collapsed every room")
    rooms_poly = [p for _, p in rooms_typed]

    if merge_collinear:
        idx = _extract_walls(rooms_poly, mm, thickness, deg)
    else:
        idx = _extract_walls_linemerge(rooms_poly, mm, thickness, deg)
    walls = idx.walls
    openings, ostats = _host_openings(plan, walls, mm, thickness, deg)
    rooms = _assign_room_walls(rooms_typed, idx, mm)

    plot = []
    land = geoms(plan.get("land"))
    land = [g for g in land if isinstance(g, Polygon) and g.area > AREA_EPS]
    if land:
        big = max(land, key=lambda g: g.area)
        plot = [P(round(x * mm), round(y * mm)) for x, y in big.exterior.coords[:-1]]
    elif plan.get("land") is not None and geoms(plan.get("land")):
        deg["plot_non_polygon"] += 1

    return Plan(
        id=str(plan.get("id", "?")),
        walls=walls, openings=openings, rooms=rooms,
        site=Site(plot_polygon=plot, north_deg=0.0),
        provenance={"source": "ResPlan", "resplan_id": plan.get("id"),
                    "mm_per_unit": mm, "wall_depth_units": plan.get("wall_depth"),
                    "opening_stats": ostats,
                    "degenerate": deg,
                    "wall_merge": "collinear_interval" if merge_collinear else "linemerge",
                    "align_mm": align,
                    "stated_area_m2": plan.get("area")},
    )


# --------------------------------------------------------------------------
# superseded extractor, kept only as an A/B baseline for the corpus runner
# --------------------------------------------------------------------------

def _straight_runs(coords: list[tuple[float, float]],
                   tol_deg: float = COLLINEAR_TOL_DEG) -> list[tuple[tuple, tuple]]:
    """Split a polyline into maximal straight runs; return (start, end) pairs."""
    if len(coords) < 2:
        return []
    runs, start = [], 0
    for i in range(1, len(coords) - 1):
        ax, ay = coords[i - 1]; bx, by = coords[i]; cx, cy = coords[i + 1]
        a1 = math.atan2(by - ay, bx - ax)
        a2 = math.atan2(cy - by, cx - bx)
        d = abs(math.degrees(a2 - a1)) % 360.0
        d = min(d, 360.0 - d)
        if d > tol_deg:
            runs.append((coords[start], coords[i]))
            start = i
    runs.append((coords[start], coords[-1]))
    return [(a, b) for a, b in runs if a != b]


def _extract_walls_linemerge(rooms: list[Polygon], mm: float, thickness_mm: int,
                             deg: dict[str, int] | None = None) -> WallIndex:
    deg = deg if deg is not None else new_degenerate_counters()
    idx = WallIndex()
    if not rooms:
        return idx
    boundary = unary_union([r.boundary for r in rooms])
    merged = linemerge(boundary) if boundary.geom_type != "LineString" else boundary
    for ls in geoms(merged):
        if not isinstance(ls, LineString):
            continue
        for a, b in _straight_runs(list(ls.coords)):
            if math.hypot(b[0] - a[0], b[1] - a[1]) * mm < MIN_WALL_MM:
                deg["wall_sliver_dropped"] += 1
                continue
            key, d = _line_key(a, b)
            wid = f"w{len(idx.walls)}"
            idx.walls.append(Wall(
                id=wid,
                start=P(round(a[0] * mm), round(a[1] * mm)),
                end=P(round(b[0] * mm), round(b[1] * mm)),
                thickness=thickness_mm,
            ))
            if key is not None:
                ta, tb = _proj(a, d), _proj(b, d)
                lo, hi = (ta, tb) if ta <= tb else (tb, ta)
                if key not in idx.groups:
                    idx.groups[key] = (d, [])
                idx.groups[key][1].append((lo, hi, wid))
    return idx
