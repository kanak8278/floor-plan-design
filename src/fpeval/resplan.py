"""ResPlan -> canonical IR.

Key finding driving this implementation: recovering wall centrelines by
skeletonising ResPlan's `wall` polygon mass FAILS (median rebuild IoU 0.37,
17% slivers, junction blocks misread as thick walls).

The working inversion goes through the **room tiling**: ResPlan room polygons
tile `inner` cleanly (self-overlap ~0.00%) and 99.75% of their edges are
axis-aligned, so the shared edges between adjacent rooms *are* the interior
wall centrelines and the tiling's outer boundary is the exterior wall.
"""
from __future__ import annotations
import math
from typing import Any, Iterable
from shapely.geometry import Polygon, LineString, Point
from shapely.ops import unary_union, polygonize, linemerge

from .ir import Plan, Wall, Opening, Room, Site, P

ROOM_KEYS = ["living", "kitchen", "bedroom", "bathroom", "balcony", "storage"]
DISPLAY = {"living": "Living", "kitchen": "Kitchen", "bedroom": "Bedroom",
           "bathroom": "Bathroom", "balcony": "Balcony", "storage": "Storage"}

# ResPlan coordinates are NOT metric. `wall_depth` is the only physical anchor:
# anchoring on it and treating the `area` field as truth implies ~226 mm walls
# (median over 4000 plans), consistent with 230 mm brick + plaster.
ASSUMED_WALL_MM = 226.0

SNAP_UNITS = 0.5          # coordinate-space snap grid (probed: 0.34% median area error)
COLLINEAR_TOL_DEG = 1.0


def geoms(g) -> list:
    if g is None or getattr(g, "is_empty", True):
        return []
    return list(g.geoms) if hasattr(g, "geoms") else [g]


def scale_mm_per_unit(plan: dict[str, Any]) -> float:
    wd = plan.get("wall_depth") or 0.0
    if wd <= 0:
        raise ValueError("plan has no usable wall_depth")
    return ASSUMED_WALL_MM / wd


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
    parts = [q for q in parts if isinstance(q, Polygon) and q.area > 1e-9]
    return max(parts, key=lambda q: q.area) if parts else None


def _rooms_raw(plan: dict[str, Any], s: float) -> list[tuple[str, Polygon]]:
    out = []
    for key in ROOM_KEYS:
        for g in geoms(plan.get(key)):
            if not isinstance(g, Polygon) or g.area <= 1e-6:
                continue
            sp = _snap_poly(g, s)
            if sp is not None and sp.area > 1e-6:
                out.append((key, sp))
    return out


def _straight_runs(coords: list[tuple[float, float]],
                   tol_deg: float = COLLINEAR_TOL_DEG) -> list[tuple[tuple, tuple]]:
    """Split a polyline into maximal straight runs; return (start, end) pairs.

    Runs are allowed to span T-junctions — OpenPlan3D's detectRooms has an
    explicit splitWallsAtTJunctions pass, so long walls with tees are correct.
    """
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


def _extract_walls(rooms: list[Polygon], mm: float, thickness_mm: int) -> list[Wall]:
    boundary = unary_union([r.boundary for r in rooms])   # nodes at all intersections
    merged = linemerge(boundary) if boundary.geom_type != "LineString" else boundary
    walls: list[Wall] = []
    for ls in geoms(merged):
        if not isinstance(ls, LineString):
            continue
        for a, b in _straight_runs(list(ls.coords)):
            if math.hypot(b[0] - a[0], b[1] - a[1]) * mm < 1.0:   # sub-mm sliver
                continue
            walls.append(Wall(
                id=f"w{len(walls)}",
                start=P(round(a[0] * mm), round(a[1] * mm)),
                end=P(round(b[0] * mm), round(b[1] * mm)),
                thickness=thickness_mm,
            ))
    return walls


def _host_openings(plan: dict[str, Any], walls: list[Wall], mm: float,
                   thickness_mm: int) -> tuple[list[Opening], dict]:
    """Attach door/window polygons to a host wall as (wall_id, position, width).

    ResPlan door polygons are wall-thickness x opening-width strips (measured
    median area 71 units^2 ~= wall_depth 4.14 x 18 units), so the long side of
    the oriented bbox is the opening width and the short side is the wall.
    """
    openings: list[Opening] = []
    stats = {"total": 0, "hosted": 0, "unhosted_kind": {}}
    tol = thickness_mm * 1.5 + 60.0    # perpendicular tolerance, mm

    for kind, key in (("door", "door"), ("window", "window"), ("front_door", "front_door")):
        for g in geoms(plan.get(key)):
            if not isinstance(g, Polygon) or g.area <= 1e-9:
                continue
            stats["total"] += 1
            rect = g.minimum_rotated_rectangle
            cs = list(rect.exterior.coords)[:4]
            if len(cs) < 4:
                continue
            e1 = math.dist(cs[0], cs[1]); e2 = math.dist(cs[1], cs[2])
            long_len = max(e1, e2) * mm
            if e1 >= e2:
                d = (cs[1][0] - cs[0][0], cs[1][1] - cs[0][1])
            else:
                d = (cs[2][0] - cs[1][0], cs[2][1] - cs[1][1])
            ang = math.atan2(d[1], d[0])
            c = g.centroid
            cx, cy = c.x * mm, c.y * mm

            best, best_dist = None, float("inf")
            for w in walls:
                wl = w.length
                if wl < 1.0:
                    continue
                wx, wy = w.end.x - w.start.x, w.end.y - w.start.y
                wang = math.atan2(wy, wx)
                da = abs(math.degrees(wang - ang)) % 180.0
                da = min(da, 180.0 - da)
                if da > 12.0:              # must be roughly parallel
                    continue
                t = ((cx - w.start.x) * wx + (cy - w.start.y) * wy) / (wl * wl)
                if not (-0.02 <= t <= 1.02):
                    continue
                px, py = w.start.x + t * wx, w.start.y + t * wy
                dist = math.hypot(cx - px, cy - py)
                if dist < best_dist:
                    best, best_dist = (w, min(max(t, 0.0), 1.0)), dist

            if best is None or best_dist > tol:
                stats["unhosted_kind"][kind] = stats["unhosted_kind"].get(kind, 0) + 1
                continue
            w, t = best
            stats["hosted"] += 1
            is_win = kind == "window"
            openings.append(Opening(
                id=f"o{len(openings)}", kind=kind, wall_id=w.id, position=round(t, 6),
                width=max(int(round(long_len)), 300),
                sill=900 if is_win else 0,
                head=2100 if not is_win else 2100,
            ))
    return openings, stats


def _assign_room_walls(rooms_typed: list[tuple[str, Polygon]], walls: list[Wall],
                       mm: float) -> list[Room]:
    out: list[Room] = []
    counters: dict[str, int] = {}
    for idx, (cat, poly) in enumerate(rooms_typed):
        counters[cat] = counters.get(cat, 0) + 1
        n = counters[cat]
        base = DISPLAY.get(cat, cat.title())
        name = base if n == 1 and cat in ("living", "kitchen") else f"{base} {n}"
        bnd = poly.boundary.buffer(1.0 / mm * 30.0)   # ~30 mm tolerance in units
        wids = []
        for w in walls:
            mx = ((w.start.x + w.end.x) / 2.0) / mm
            my = ((w.start.y + w.end.y) / 2.0) / mm
            if bnd.contains(Point(mx, my)):
                wids.append(w.id)
        pts = [P(round(x * mm), round(y * mm)) for x, y in poly.exterior.coords[:-1]]
        out.append(Room(id=f"r{idx}", name=name, category=cat, wall_ids=wids,
                        polygon=pts, area=int(round(poly.area * mm * mm))))
    return out


def convert(plan: dict[str, Any]) -> Plan:
    """ResPlan plan dict -> canonical IR Plan."""
    mm = scale_mm_per_unit(plan)
    thickness = int(round(ASSUMED_WALL_MM))
    rooms_typed = _rooms_raw(plan, SNAP_UNITS)
    if not rooms_typed:
        raise ValueError("no usable room polygons")
    rooms_poly = [p for _, p in rooms_typed]

    walls = _extract_walls(rooms_poly, mm, thickness)
    openings, ostats = _host_openings(plan, walls, mm, thickness)
    rooms = _assign_room_walls(rooms_typed, walls, mm)

    land = geoms(plan.get("land"))
    plot = []
    if land:
        big = max(land, key=lambda g: g.area)
        plot = [P(round(x * mm), round(y * mm)) for x, y in big.exterior.coords[:-1]]

    return Plan(
        id=str(plan.get("id", "?")),
        walls=walls, openings=openings, rooms=rooms,
        site=Site(plot_polygon=plot, north_deg=0.0),
        provenance={"source": "ResPlan", "resplan_id": plan.get("id"),
                    "mm_per_unit": mm, "wall_depth_units": plan.get("wall_depth"),
                    "opening_stats": ostats,
                    "stated_area_m2": plan.get("area")},
    )
