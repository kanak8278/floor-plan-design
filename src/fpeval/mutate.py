"""Deterministic plan perturbations that produce LABELLED violations.

Why this exists: there is no labelled corpus of bad Indian floor plans, and
asking an LLM to break a plan gives unlabelled, non-reproducible damage. So we
take a plan the validator calls clean, apply a *known* injury in code, and
assert the validator names that injury. This is the only thing that turns the
rules engine from an opinion into a tested component.

Contract for every function here:
  * pure - the input `Plan` is never touched (`copy.deepcopy` first);
  * deterministic - no randomness, no LLM, same input => same output;
  * returns `(mutated_plan, expected_rule_ids)` where the expected ids are a
    *subset* claim: the mutation may trip extra rules (shrinking a room also
    orphans its door), but every listed id MUST be reported.

`MutationNotApplicable` is raised rather than returning a no-op, so a test can
never silently pass on a plan that lacks the feature being broken.
"""
from __future__ import annotations

import copy
import math
from shapely import affinity
from shapely.geometry import Polygon, MultiPolygon, box

from .ir import Plan, Room, P
from .rules import (_build_ctx, room_polygon, clear_width_mm, bearing_deg,
                    _zone_bearing, _ang_gap, NON_HABITABLE, PRIVATE, _is_passage)
from .bylaws import BENGALURU, DIRECTIONS

Mutation = tuple[Plan, list[str]]


class MutationNotApplicable(Exception):
    """The input plan has no feature of the kind this mutation breaks."""


# ----------------------------------------------------------------- helpers ---

def _largest(g) -> Polygon:
    if isinstance(g, MultiPolygon):
        return max(g.geoms, key=lambda x: x.area)
    return g


def _write_poly(room: Room, poly: Polygon) -> None:
    """Push a shapely polygon back into the IR (integer mm) and refresh area."""
    poly = _largest(poly)
    room.polygon = [P(int(round(x)), int(round(y)))
                    for x, y in poly.exterior.coords[:-1]]
    room.area = int(round(poly.area))


def _room_by_id(plan: Plan, rid: str) -> Room:
    return next(r for r in plan.rooms if r.id == rid)


def _ctx(plan: Plan):
    return _build_ctx(plan, {}, BENGALURU)


def _room_door_ids(plan: Plan, rid: str) -> list[str]:
    c = _ctx(plan)
    return [oid for oid, a, b in c.door_edges if rid in (a, b)]


# ----------------------------------------------------------- (a) NBC width ---

def shrink_passage(plan: Plan, to_mm: int = 820) -> Mutation:
    """Squeeze the corridor to `to_mm` (< NBC's 900 mm clear width).

    Shrunk symmetrically about the corridor's long axis so doors along it stay
    on the polygon boundary - otherwise the failure would come from a detached
    doorway rather than from the width itself.
    """
    p = copy.deepcopy(plan)
    passages = [r for r in p.rooms if _is_passage(r) and len(r.polygon) >= 3]
    if not passages:
        raise MutationNotApplicable("plan has no passage/corridor room")
    r = max(passages, key=lambda x: x.area)
    poly = room_polygon(r)
    minx, miny, maxx, maxy = poly.bounds
    w, h = maxx - minx, maxy - miny
    if min(w, h) <= to_mm:
        raise MutationNotApplicable(f"passage already {min(w,h):.0f} mm wide")
    if w <= h:                       # narrow axis is x
        d = (w - to_mm) / 2.0
        clip = box(minx + d, miny, maxx - d, maxy)
    else:
        d = (h - to_mm) / 2.0
        clip = box(minx, miny + d, maxx, maxy - d)
    _write_poly(r, poly.intersection(clip))
    return p, ["NBC.PASSAGE_WIDTH"]


# Per-category NBC targets the shrink aims *below*, read from the profile so
# the mutation cannot drift out of sync with the table it is testing.
_SHRINK_TARGETS = {
    "bedroom":  ("hab_min_width_mm", "hab_min_area_m2",
                 ["NBC.HAB_MIN_AREA", "NBC.HAB_MIN_WIDTH"]),
    "living":   ("hab_min_width_mm", "hab_min_area_m2",
                 ["NBC.HAB_MIN_AREA", "NBC.HAB_MIN_WIDTH"]),
    "kitchen":  ("kitchen_min_width_mm", "kitchen_min_area_m2",
                 ["NBC.KITCHEN_MIN_AREA", "NBC.KITCHEN_MIN_WIDTH"]),
    "bathroom": ("bath_min_width_mm", "bath_wc_combined_min_area_m2",
                 ["NBC.BATH_MIN_AREA", "NBC.BATH_MIN_WIDTH"]),
}
SHRINK_MARGIN = 0.8       # land 20% under the limit, not on the tolerance


def shrink_room_below_min(plan: Plan, category: str = "bedroom") -> Mutation:
    """Scale one room about its centroid until it breaks both NBC minima.

    The target is `SHRINK_MARGIN` x the category's own minimum (from the
    `CityProfile` NBC table, not a literal), so the assertion never sits on a
    tolerance and a change to the table cannot silently defang this test.
    """
    p = copy.deepcopy(plan)
    if category not in _SHRINK_TARGETS:
        raise ValueError(f"no NBC minimum encoded for category {category!r}")
    wkey, akey, expect = _SHRINK_TARGETS[category]
    nbc = BENGALURU.nbc
    tw = getattr(nbc, wkey) * SHRINK_MARGIN
    ta = getattr(nbc, akey) * 1e6 * SHRINK_MARGIN

    cands = [r for r in p.rooms if r.category == category and len(r.polygon) >= 3]
    if not cands:
        raise MutationNotApplicable(f"plan has no {category} room")
    r = max(cands, key=lambda x: x.area)
    poly = room_polygon(r)
    w = clear_width_mm(poly)
    f = min(tw / max(w, 1.0), math.sqrt(ta / max(poly.area, 1.0)))
    if f >= 1.0:
        raise MutationNotApplicable(
            f"{category} {r.name!r} is already below the NBC minima "
            f"(width {w:.0f} mm, area {poly.area/1e6:.2f} m^2)")
    _write_poly(r, affinity.scale(poly, xfact=f, yfact=f, origin="centroid"))
    return p, expect


# --------------------------------------------------------- (b) geometry ------

def overlap_two_rooms(plan: Plan) -> Mutation:
    """Slide one room into the neighbour it shares the longest wall with."""
    p = copy.deepcopy(plan)
    polys = {r.id: room_polygon(r) for r in p.rooms}
    polys = {k: v for k, v in polys.items() if v is not None}
    if len(polys) < 2:
        raise MutationNotApplicable("need >= 2 valid rooms")
    ids = list(polys)
    best, best_len = None, 0.0
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = polys[ids[i]], polys[ids[j]]
            if a.distance(b) > 1.0:
                continue
            L = a.buffer(1.0).intersection(b.buffer(1.0)).length
            if L > best_len:
                best, best_len = (ids[i], ids[j]), L
    if best is None:
        raise MutationNotApplicable("no two rooms are adjacent")
    src, dst = polys[best[1]], polys[best[0]]
    minx, miny, maxx, maxy = src.bounds
    step = 0.35 * min(maxx - minx, maxy - miny)
    sc, dc = src.centroid, dst.centroid
    dx, dy = dc.x - sc.x, dc.y - sc.y
    n = math.hypot(dx, dy) or 1.0
    moved = affinity.translate(src, xoff=step * dx / n, yoff=step * dy / n)
    _write_poly(_room_by_id(p, best[1]), moved)
    return p, ["GEO.ROOM_OVERLAP"]


def disconnect_room(plan: Plan) -> Mutation:
    """Delete every door of one habitable room, so it becomes unreachable.

    Picks the room with the fewest doors that is neither an entry room nor a
    balcony/store (those only warn, by design), so the expected finding is an
    `error` and the test is meaningful.
    """
    p = copy.deepcopy(plan)
    c = _ctx(p)
    cats = {r.id: r.category for r in p.rooms}
    counts: dict[str, list[str]] = {}
    for oid, a, b in c.door_edges:
        counts.setdefault(a, []).append(oid)
        counts.setdefault(b, []).append(oid)
    # Private rooms first: `GEO.UNREACHABLE_ROOM` only reaches `error` severity
    # for a bedroom/bathroom (a doorless kitchen is read as an open threshold),
    # so picking one keeps this mutation a hard-failure test.
    cands = [(rid, oids) for rid, oids in counts.items()
             if rid not in c.entry_rooms and cats.get(rid) in PRIVATE]
    if not cands:
        cands = [(rid, oids) for rid, oids in counts.items()
                 if rid not in c.entry_rooms and cats.get(rid) not in NON_HABITABLE]
    if not cands:
        raise MutationNotApplicable("no interior non-balcony room with a door")
    rid, oids = min(cands, key=lambda t: (len(t[1]), t[0]))
    drop = set(oids)
    p.openings = [o for o in p.openings if o.id not in drop]
    return p, ["GEO.UNREACHABLE_ROOM"]


def widen_opening_beyond_wall(plan: Plan) -> Mutation:
    """Make a door wider than the wall that hosts it."""
    p = copy.deepcopy(plan)
    walls = {w.id: w for w in p.walls}
    for o in p.openings:
        w = walls.get(o.wall_id)
        if w is not None and w.length >= 1.0:
            o.width = int(round(w.length)) + 500
            return p, ["GEO.OPENING_TOO_WIDE"]
    raise MutationNotApplicable("no opening hosted on a usable wall")


# ------------------------------------------------------------- (c) bye-law ---

def exceed_coverage(plan: Plan, ratio: float = 0.9) -> Mutation:
    """Shrink the plot until ground coverage == `ratio` (> the 75%/65% cap).

    Mutating the plot rather than the building is deliberate: it changes one
    number the bye-law rule reads and leaves all NBC/geometry state untouched,
    so a failure can only come from the coverage rule. The plot is also marked
    surveyed, because the bye-law family refuses to run on an unsurveyed site.
    """
    p = copy.deepcopy(plan)
    c = _ctx(p)
    fp = c.footprint
    if fp is None or fp.is_empty:
        raise MutationNotApplicable("plan has no footprint")
    hull = fp.convex_hull
    target = fp.area / ratio
    s = math.sqrt(target / hull.area)
    newplot = affinity.scale(hull, xfact=s, yfact=s, origin="centroid")
    p.site.plot_polygon = [P(int(round(x)), int(round(y)))
                           for x, y in newplot.exterior.coords[:-1]]
    p.site.setbacks_mm = {"front": 900, "rear": 700, "side": 700}
    return p, ["BYLAW.GROUND_COVERAGE"]


# --------------------------------------------------------------- (d) vastu ---

def move_kitchen_to_zone(plan: Plan, zone: str = "NE") -> Mutation:
    """Swap the kitchen with whichever room already sits in `zone`.

    Swapping labels rather than moving geometry keeps the wall graph, the door
    topology and every area intact, so the only thing that can change is the
    vastu score - which is exactly what is being tested.
    """
    if zone not in DIRECTIONS:
        raise ValueError(f"zone must be one of {DIRECTIONS}")
    p = copy.deepcopy(plan)
    c = _ctx(p)
    kitchens = [r for r in p.rooms if r.category == "kitchen" and r.id in c.polys]
    if not kitchens:
        raise MutationNotApplicable("plan has no kitchen")
    k = max(kitchens, key=lambda r: c.polys[r.id].area)
    cx, cy = c.centre
    target_b = _zone_bearing(zone)

    def gap(r: Room) -> float:
        q = c.polys[r.id].centroid
        return _ang_gap(bearing_deg(q.x - cx, q.y - cy, p.site.north_deg), target_b)

    others = [r for r in p.rooms
              if r.id in c.polys and r.id != k.id and r.category != "kitchen"]
    if not others:
        raise MutationNotApplicable("no other room to swap the kitchen with")
    tgt = min(others, key=lambda r: (gap(r), r.id))
    if gap(tgt) > 22.5:
        # No room actually occupies `zone`; swapping would leave the kitchen in
        # a neighbouring octant, where the score can legitimately stay >= 0.5.
        raise MutationNotApplicable(
            f"no room lies in {zone} (nearest is {gap(tgt):.0f} deg off)")
    if gap(k) <= 22.5:
        raise MutationNotApplicable(f"kitchen is already in {zone}")
    k.category, tgt.category = tgt.category, k.category
    k.name, tgt.name = tgt.name, k.name
    return p, ["VASTU.KITCHEN"]


# ------------------------------------------------------------------- (e) NBC ---

def wc_into_kitchen(plan: Plan) -> Mutation:
    """Relabel the room a bathroom opens into as the kitchen.

    Same trick as `move_kitchen_to_zone`: geometry is untouched, so the only
    new fact is the adjacency NBC forbids.
    """
    p = copy.deepcopy(plan)
    c = _ctx(p)
    cats = {r.id: r.category for r in p.rooms}
    for oid, a, b in sorted(c.door_edges):
        for bath, other in ((a, b), (b, a)):
            if cats.get(bath) == "bathroom" and cats.get(other) != "bathroom":
                r = _room_by_id(p, other)
                r.category = "kitchen"
                r.name = "Kitchen"
                return p, ["NBC.WC_OPENS_INTO_KITCHEN"]
    raise MutationNotApplicable("no bathroom opens into another room")


ALL_MUTATIONS = {
    "shrink_passage": shrink_passage,
    "shrink_room_below_min": shrink_room_below_min,
    "overlap_two_rooms": overlap_two_rooms,
    "disconnect_room": disconnect_room,
    "move_kitchen_to_zone": move_kitchen_to_zone,
    "exceed_coverage": exceed_coverage,
    "wc_into_kitchen": wc_into_kitchen,
    "widen_opening_beyond_wall": widen_opening_beyond_wall,
}
