"""Design-quality checks: the faults an architect would circle in red.

Distinct from `rules.py`'s NBC family, which asks "is this legal". These ask "is
this a good house" — and they exist because a plan can pass every dimensional
rule and still be unusable. Measured on our own generated suite: 11 circulation
errors across 6 plans while dimensional compliance sat at 100%.

Sourced from established practice rather than invented:

* **Zoning** — public / private / service, connected by circulation. Group
  related functions, separate conflicting ones.
  (learnarchitecture.net zoning guide, illustrarch, ArchitectureCourses.org)
* **Traffic-flow faults** — entrance opening straight into the living room with
  no buffer; bathroom visible from the dining table; kitchen far from where
  groceries arrive. (redesigndaily, plan7architect)
* **Bedroom faults** — the primary bedroom door opening off the main living
  area; no usable wall left for a wardrobe. (carolineondesign, dshelldesign)
* **Bathroom faults** — the WC being the first thing seen when the door opens;
  a common bath only reachable through a bedroom. (mymodernhome)
* **Kitchen** — the fridge/sink/hob work triangle, more than one way in and out,
  a store within reach. (mymodernhome, gharpedia NBC standards)
* **Indian specifics** — dining adjacent to or above a toilet is rejected
  outright; store rooms belong near the kitchen; ≥900 mm clear circulation
  between hall, dining and kitchen. (houseyog, happho, gharpedia)
* **Light** — a room lit from two sides reads better than one lit from one.
  (redesigndaily)

Every check is individually switchable via `policy.RuleConfig`, because these are
judgements. A user who disagrees should be able to silence one without losing the
arithmetic.
"""
from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Any

from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

PUBLIC = {"living", "dining", "foyer", "sitout"}
PRIVATE = {"bedroom", "master_bedroom", "study"}
SERVICE = {"kitchen", "utility", "store", "bathroom", "stair", "shaft"}

# A wardrobe needs a clear run; 1800 mm is a double-door unit.
WARDROBE_RUN_MM = 1800
# Work-triangle legs, standard guidance: no leg under 1.2 m or over 2.7 m,
# perimeter between 4 m and 8 m.
TRIANGLE_LEG_MIN, TRIANGLE_LEG_MAX = 1200, 2700
TRIANGLE_SUM_MIN, TRIANGLE_SUM_MAX = 4000, 8000
# "Kitchen far from where groceries arrive" — beyond this it is a real chore.
GROCERY_WALK_MM = 12000


@dataclass
class DFinding:
    rule_id: str
    severity: str
    weight: float
    detail: str
    element_ids: list[str]


def _cat(r) -> str:
    return (getattr(r, "category", "") or "")


def _poly(r) -> Polygon | None:
    pts = getattr(r, "polygon", None) or []
    if len(pts) < 3:
        return None
    p = Polygon([q.as_tuple() for q in pts])
    if not p.is_valid:
        p = p.buffer(0)
    return p if (p and not p.is_empty and p.area > 0) else None


def _wall_line(w) -> LineString:
    return LineString([w.start.as_tuple(), w.end.as_tuple()])


def _opening_point(plan, o):
    w = next((x for x in plan.walls if x.id == o.wall_id), None)
    if w is None:
        return None
    ln = _wall_line(w)
    return ln.interpolate(max(0.0, min(1.0, o.position)), normalized=True)


# ---------------------------------------------------------- bathroom sizing
# MEASURED, not recalled: 3,573 bathrooms across 1,500 real ResPlan plans.
#
#   area (m2)              p5 2.38 | p25 3.82 | median 4.64 | p75 5.48 | p95 7.43
#   share of carpet        p5 2.1% | median 4.0%            | p95 6.5%
#   share of its bedroom   p5 15%  | p25 22% | median 26%    | p75 30% | p95 40%
#
# Corroborated by the verified Indian builder plans in corpus/india: Brigade
# Lakecrest toilets 3.93 m2, Divyasree Shettigere 3.71 m2, the 3BHK+3T+Study
# 4.16 m2 -- all between p25 and the median.
#
# The bands below are the p5/p95 of that distribution, so a warning means "this
# is outside what 90% of real plans do", which is a defensible thing to say. NBC
# sets the legal floor at 2.8 m2 for a combined bath+WC; these are about
# proportion, not legality, which is why they are warnings.
BATH_AREA_MIN_M2 = 2.4        # p5
BATH_AREA_MAX_M2 = 7.5        # p95; above this in a normal house is indulgent
BATH_OF_BEDROOM_MIN = 0.15    # p5
BATH_OF_BEDROOM_MAX = 0.40    # p95
WET_SHARE_OF_CARPET_MAX = 0.13  # sum of all baths; ~2x the single-bath p95


def check_bath_proportion(rooms, polys, names, cats, adjacency) -> list[DFinding]:
    """Is each bathroom a sensible size, absolutely and relative to its bedroom?

    Both directions matter. A 1.8 m2 toilet is legal in some readings and
    miserable to use; a 9 m2 toilet in a 3BHK is area taken from the bedrooms.
    """
    out: list[DFinding] = []
    baths = [r for r in rooms if cats.get(r.id) == "bathroom"]
    if not baths:
        return out
    carpet = sum((r.area or 0) for r in rooms) / 1e6

    for b in baths:
        a = (b.area or 0) / 1e6
        if a <= 0:
            continue
        if a < BATH_AREA_MIN_M2:
            out.append(DFinding(
                "DESIGN.BATH_UNDERSIZED", "warn", 0.6,
                f"{names.get(b.id, b.id)} is {a:.1f} m²; 95% of real bathrooms are "
                f"above {BATH_AREA_MIN_M2} m² and NBC's floor for a combined "
                "bath+WC is 2.8 m²", [b.id]))
        elif a > BATH_AREA_MAX_M2:
            out.append(DFinding(
                "DESIGN.BATH_OVERSIZED", "warn", 0.5,
                f"{names.get(b.id, b.id)} is {a:.1f} m², above the {BATH_AREA_MAX_M2} m² "
                "95th percentile of real plans; that area is usually better spent "
                "on the rooms it serves", [b.id]))

        # Relative to the bedroom it actually opens off.
        served = [n for n in adjacency.get(b.id, ())
                  if cats.get(n) in ("bedroom", "master_bedroom")]
        for bid in served[:1]:
            bed = next((r for r in rooms if r.id == bid), None)
            ba = (bed.area or 0) / 1e6 if bed else 0
            if ba <= 0:
                continue
            frac = a / ba
            if frac > BATH_OF_BEDROOM_MAX:
                out.append(DFinding(
                    "DESIGN.BATH_DISPROPORTIONATE", "warn", 0.55,
                    f"{names.get(b.id, b.id)} is {100*frac:.0f}% of "
                    f"{names.get(bid, bid)} ({a:.1f} m² against {ba:.1f} m²); real "
                    f"plans sit at 26% and rarely pass {100*BATH_OF_BEDROOM_MAX:.0f}%",
                    [b.id, bid]))
            elif frac < BATH_OF_BEDROOM_MIN:
                out.append(DFinding(
                    "DESIGN.BATH_DISPROPORTIONATE", "warn", 0.4,
                    f"{names.get(b.id, b.id)} is only {100*frac:.0f}% of "
                    f"{names.get(bid, bid)}; below the 15% seen in real plans, so it "
                    "will feel like an afterthought", [b.id, bid]))

    wet = sum((b.area or 0) for b in baths) / 1e6
    if carpet > 0 and wet / carpet > WET_SHARE_OF_CARPET_MAX:
        out.append(DFinding(
            "DESIGN.WET_AREA_EXCESSIVE", "warn", 0.5,
            f"bathrooms total {wet:.1f} m², {100*wet/carpet:.0f}% of the carpet area; "
            f"real plans run about 4% per bath and rarely exceed "
            f"{100*WET_SHARE_OF_CARPET_MAX:.0f}% in total",
            [b.id for b in baths]))
    return out


# ------------------------------------------------------------------ balcony
def check_balcony(rooms, polys, names, cats, adjacency, footprint) -> list[DFinding]:
    """A balcony must open to the outside and hang off exactly one room.

    Both halves are topological, not cosmetic. A balcony with no edge on the
    building perimeter is an internal void -- it cannot open to anything, so it
    is not a balcony. And a balcony reached from two rooms is a through-route
    across the facade, which is not how one is used.
    """
    out: list[DFinding] = []
    bals = [r for r in rooms if cats.get(r.id) in ("balcony", "sitout", "patio")]
    for b in bals:
        poly = polys.get(b.id)
        if poly is None:
            continue
        # Exterior edge: part of its boundary must lie on the footprint's
        # boundary rather than inside it.
        opens_out = True
        if footprint is not None and not footprint.is_empty:
            shared = poly.boundary.intersection(footprint.boundary.buffer(220))
            opens_out = (not shared.is_empty) and shared.length >= 600
        if not opens_out:
            out.append(DFinding(
                "DESIGN.BALCONY_ENCLOSED", "error", 1.0,
                f"{names.get(b.id, b.id)} has no edge on the building perimeter, so "
                "it opens onto nothing; a balcony must face outside", [b.id]))

        nb = list(adjacency.get(b.id, ()))
        if not nb:
            out.append(DFinding(
                "DESIGN.BALCONY_NO_ACCESS", "error", 1.0,
                f"{names.get(b.id, b.id)} has no door; nothing reaches it", [b.id]))
        elif len(nb) > 1 and cats.get(b.id) == "balcony":
            out.append(DFinding(
                "DESIGN.BALCONY_THROUGH_ROUTE", "warn", 0.6,
                f"{names.get(b.id, b.id)} opens off {len(nb)} rooms "
                f"({', '.join(names.get(x, x) for x in nb[:3])}); a balcony hangs "
                "off one room, it is not a passage across the facade", [b.id] + nb[:3]))
        else:
            host = cats.get(nb[0], "")
            if host not in ("living", "dining", "bedroom", "master_bedroom",
                            "kitchen", "utility", "study", "foyer"):
                out.append(DFinding(
                    "DESIGN.BALCONY_ODD_HOST", "warn", 0.4,
                    f"{names.get(b.id, b.id)} opens off a {host or 'unknown'}; a "
                    "balcony normally hangs off a living room, a bedroom or the "
                    "kitchen utility", [b.id, nb[0]]))
    return out


def _footprint(polys):
    """Union of the rooms, as a stand-in for the building outline."""
    if not polys:
        return None
    try:
        return unary_union(list(polys.values()))
    except Exception:
        return None


def check(plan, *, adjacency: dict[str, set[str]] | None = None,
          entry_rooms: list[str] | None = None,
          typology: Any = None, brief: dict | None = None) -> list[DFinding]:
    """All design checks. `adjacency` and `entry_rooms` come from rules._Ctx so
    the door topology is computed once."""
    brief = brief or {}
    out: list[DFinding] = []
    rooms = list(plan.rooms)
    if not rooms:
        return out
    polys = {r.id: p for r in rooms if (p := _poly(r)) is not None}
    names = {r.id: r.name for r in rooms}
    cats = {r.id: _cat(r) for r in rooms}
    a = adjacency or {}
    entry = (entry_rooms or [None])[0]
    by_cat: dict[str, list[str]] = {}
    for r in rooms:
        by_cat.setdefault(_cat(r), []).append(r.id)

    circ = set(getattr(typology, "circulation", ("living", "dining", "foyer")))
    # `multi_kitchen` is declared on the scenario; the kind string is the
    # fallback for callers still passing a typology.py Typology.
    single_dwelling = not (getattr(typology, "multi_kitchen", False)
                           or getattr(typology, "kind", "") == "rental_floors")

    # ---- 1. one kitchen per dwelling ---------------------------------------
    kits = by_cat.get("kitchen", [])
    if single_dwelling and len(kits) > 1:
        out.append(DFinding(
            "DESIGN.MULTIPLE_KITCHENS", "error", 1.0,
            f"{len(kits)} kitchens in a single dwelling "
            f"({', '.join(names[k] for k in kits)}); one dwelling has one kitchen "
            "— unless this is a rental-floors building, in which case say so",
            kits))

    # ---- 2. storage exists -------------------------------------------------
    if not (by_cat.get("store") or by_cat.get("utility")):
        out.append(DFinding(
            "DESIGN.NO_STORAGE", "warn", 0.6,
            "no store or utility anywhere; missing storage is the most common "
            "reason a finished house feels cluttered", []))

    # ---- 3. store should be near the kitchen -------------------------------
    for sid in by_cat.get("store", []):
        if kits and sid in polys:
            d = min(polys[sid].distance(polys[k]) for k in kits if k in polys)
            if d > 6000:
                out.append(DFinding(
                    "DESIGN.STORE_FAR_FROM_KITCHEN", "warn", 0.4,
                    f"{names[sid]} is {d/1000:.1f} m from the kitchen; a dry store "
                    "belongs within reach of it", [sid] + kits))

    # ---- 4. entrance buffer ------------------------------------------------
    if entry is not None:
        ec = cats.get(entry, "")
        if ec in PRIVATE or ec == "kitchen":
            out.append(DFinding(
                "DESIGN.ENTRANCE_INTO_PRIVATE", "error", 1.0,
                f"the front door opens into {names.get(entry, entry)} "
                f"({ec}); an entrance must land in a public space", [entry]))
        elif ec == "living" and not by_cat.get("foyer") and not by_cat.get("sitout"):
            out.append(DFinding(
                "DESIGN.ENTRANCE_NO_BUFFER", "warn", 0.5,
                "the front door opens straight into the living room with no foyer "
                "or sitout; visitors and weather arrive in the seating area",
                [entry]))

    # ---- 5. master bedroom door off the living area ------------------------
    for mid in by_cat.get("master_bedroom", []) + by_cat.get("bedroom", [])[:1]:
        if any(cats.get(n) in ("living", "dining") for n in a.get(mid, ())):
            out.append(DFinding(
                "DESIGN.BEDROOM_OFF_LIVING", "warn", 0.6,
                f"{names.get(mid, mid)} opens directly off the living/dining area; "
                "a bedroom door in the main social space costs privacy", [mid]))
            break

    # ---- 6. bedrooms should cluster ----------------------------------------
    beds = [b for b in by_cat.get("bedroom", []) + by_cat.get("master_bedroom", [])
            if b in polys]
    if len(beds) >= 3:
        cents = [polys[b].centroid for b in beds]
        spread = max(c1.distance(c2) for c1 in cents for c2 in cents)
        diag = math.hypot(*(lambda b: (b[2]-b[0], b[3]-b[1]))(
            unary_union(list(polys.values())).bounds))
        if diag > 0 and spread / diag > 0.92:
            out.append(DFinding(
                "DESIGN.BEDROOMS_SCATTERED", "warn", 0.5,
                f"bedrooms span {spread/1000:.1f} m across a {diag/1000:.1f} m "
                "plan; the private zone should read as one cluster, not be "
                "spread through the house", beds))

    # ---- 7. a wall free for a wardrobe -------------------------------------
    for bid in beds:
        p = polys[bid]
        walls_of = [w for w in plan.walls
                    if _wall_line(w).buffer(120).intersects(p.boundary)]
        best = 0.0
        for w in walls_of:
            ln = _wall_line(w)
            shared = ln.intersection(p.boundary.buffer(120))
            seg = shared.length if not shared.is_empty else 0.0
            blocked = 0.0
            for o in plan.openings:
                if o.wall_id != w.id:
                    continue
                blocked += o.width
            best = max(best, seg - blocked)
        if walls_of and best < WARDROBE_RUN_MM:
            out.append(DFinding(
                "DESIGN.NO_WARDROBE_WALL", "warn", 0.5,
                f"{names.get(bid, bid)} has no clear wall run of "
                f"{WARDROBE_RUN_MM} mm (longest free run {best:.0f} mm); nowhere "
                "for a wardrobe", [bid]))

    # ---- 8. dining next to a toilet (rejected outright in Indian practice) --
    for did in by_cat.get("dining", []):
        bad = [n for n in a.get(did, ()) if cats.get(n) == "bathroom"]
        if bad:
            out.append(DFinding(
                "DESIGN.DINING_ABUTS_WC", "error", 0.9,
                f"{names.get(did, did)} has a toilet opening onto it "
                f"({', '.join(names.get(b, b) for b in bad)}); rejected outright "
                "in Indian practice", [did] + bad))

    # ---- 9. bathroom door in view of the dining table ----------------------
    for did in by_cat.get("dining", []):
        if did not in polys:
            continue
        dp = polys[did]
        for o in plan.openings:
            if o.kind == "window":
                continue
            pt = _opening_point(plan, o)
            if pt is None:
                continue
            host_rooms = [rid for rid, pp in polys.items()
                          if pp.buffer(200).contains(pt)]
            if did in host_rooms and any(cats.get(h) == "bathroom" for h in host_rooms):
                out.append(DFinding(
                    "DESIGN.WC_VISIBLE_FROM_DINING", "warn", 0.5,
                    "a toilet door is in view of the dining area", [did, o.id]))
                break

    # ---- 10. kitchen access ------------------------------------------------
    for kid in kits:
        doors = [n for n in a.get(kid, ())]
        if len(doors) < 2:
            out.append(DFinding(
                "DESIGN.KITCHEN_SINGLE_ACCESS", "warn", 0.35,
                f"{names.get(kid, kid)} has one way in and out; a second access "
                "makes it workable by two people and shortens the service route",
                [kid]))

    # ---- 11. groceries walk ------------------------------------------------
    park = by_cat.get("parking", [])
    if park and kits and park[0] in polys and kits[0] in polys:
        d = polys[park[0]].distance(polys[kits[0]])
        if d > GROCERY_WALK_MM:
            out.append(DFinding(
                "DESIGN.KITCHEN_FAR_FROM_PARKING", "warn", 0.35,
                f"kitchen is {d/1000:.1f} m from parking; every grocery trip "
                "crosses the house", park + kits))

    out += check_bath_proportion(rooms, polys, names, cats, a)
    out += check_balcony(rooms, polys, names, cats, a, _footprint(polys))

    # ---- 12. dual aspect ---------------------------------------------------
    for r in rooms:
        if _cat(r) not in ("living", "bedroom", "master_bedroom", "dining", "study"):
            continue
        p = polys.get(r.id)
        if p is None:
            continue
        dirs = set()
        for o in plan.openings:
            if o.kind != "window":
                continue
            w = next((x for x in plan.walls if x.id == o.wall_id), None)
            pt = _opening_point(plan, o)
            if w is None or pt is None or not p.buffer(250).contains(pt):
                continue
            ang = math.degrees(math.atan2(w.end.y - w.start.y, w.end.x - w.start.x)) % 180
            dirs.add(round(ang / 45.0))
        if len(dirs) == 1:
            out.append(DFinding(
                "DESIGN.SINGLE_ASPECT", "warn", 0.3,
                f"{r.name} is lit from one side only; two-sided light reads "
                "markedly better", [r.id]))
        elif not dirs and _cat(r) in ("living", "bedroom", "master_bedroom"):
            out.append(DFinding(
                "DESIGN.NO_WINDOW", "error", 1.0,
                f"{r.name} has no window at all", [r.id]))
    return out
