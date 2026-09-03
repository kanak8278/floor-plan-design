"""DesignSpec / suite Truth -> solver programme.

The missing adapter. `spec.py` speaks 27 fine-grained categories (it distinguishes
`toilet` from `powder` from `handwash` because builder drawings do); `roomtypes.py`
and the solver speak 18 canonical ones. Mapping many->one here keeps the extra
detail available to the renderer and the area statement without forcing the solver
to reason about it.
"""
from __future__ import annotations
from typing import Any

from . import roomtypes as rt
from .envelope import RoomReq

# spec.py category -> canonical roomtypes key.
SPEC_TO_CANON: dict[str, str] = {
    "living": "living", "dining": "dining", "kitchen": "kitchen",
    "bedroom": "bedroom", "master_bedroom": "master_bedroom",
    "guest_bedroom": "bedroom",
    # A servant room is a bedroom-class space for layout purposes: it needs a
    # window, a door of its own, and habitable minima.
    "servant": "bedroom",
    "study": "study", "office": "study",
    "bathroom": "bathroom", "toilet": "bathroom", "powder": "bathroom",
    "handwash": "bathroom",
    "utility": "utility", "pooja": "pooja",
    "store": "store", "dress": "store",
    "staircase": "stair", "foyer": "foyer", "corridor": "foyer",
    "shaft": "shaft", "balcony": "balcony", "sit_out": "sitout",
    "terrace": "patio", "patio": "patio",
    "parking": "parking", "garage": "parking",
}

SQFT_M2 = 10.7639

# NBC 2016 Part 3 sets 2400 mm as the minimum width of a habitable room and
# 7.5 m2 as the minimum area. A rectangular tiling cannot fit a 2BHK on a 20x30
# site under either: measured, the NBC minimum AREAS total 30.3 m2, internal
# walls take ~4 m2, and the buildable tiling is 37.8 m2 -- within noise of
# impossible. The solver's binding groups were `min_area` and `max_aspect_hard`,
# not width, so relaxing width alone (my first attempt) changed nothing.
#
# Real 600 sqft Bengaluru houses are built with ~2100 mm rooms at 6.5-7 m2. This
# profile builds them and DECLARES the deviation; it does not reinterpret NBC.
RELAXED_MIN_WIDTH_MM: dict[str, int] = {
    "living": 2100, "bedroom": 2100, "master_bedroom": 2400,
    "dining": 2100, "study": 2100, "kitchen": 1650,
}
RELAXED_MIN_AREA_M2: dict[str, float] = {
    "bedroom": 6.5, "master_bedroom": 8.0, "living": 7.0,
    "dining": 6.0, "study": 5.5, "kitchen": 4.2, "bathroom": 2.2,
}
# Compact rooms are necessarily longer and thinner.
RELAXED_MAX_ASPECT = 3.2


def relaxed_note(prog) -> str | None:
    """What was relaxed, in words, for printing on the sheet.

    Printed because a relaxed plan is not NBC compliant and the sanctioning
    authority has to be told which clause it deviates from.
    """
    w = sorted({r.category for r in prog if r.category in RELAXED_MIN_WIDTH_MM})
    a = sorted({r.category for r in prog if r.category in RELAXED_MIN_AREA_M2})
    if not (w or a):
        return None
    bits = []
    if w:
        bits.append("min width -> " + ", ".join(
            f"{RELAXED_MIN_WIDTH_MM[c]}mm {c}" for c in w) + " (NBC 2400mm)")
    if a:
        bits.append("min area -> " + ", ".join(
            f"{RELAXED_MIN_AREA_M2[c]}m2 {c}" for c in a) + " (NBC 7.5m2 habitable)")
    return ("COMPACT-PLOT PROFILE, NOT NBC COMPLIANT: " + "; ".join(bits)
            + f"; max aspect {RELAXED_MAX_ASPECT}. Deviation must be declared to "
              "the sanctioning authority.")


def _apply_relaxed(prog) -> None:
    for r in prog:
        w = RELAXED_MIN_WIDTH_MM.get(r.category)
        if w is not None:
            r.min_width_mm = w
        a = RELAXED_MIN_AREA_M2.get(r.category)
        if a is not None:
            r.min_area_m2 = a
        r.max_aspect = max(r.max_aspect or 0.0, RELAXED_MAX_ASPECT)


def canon(category: str) -> str:
    if category in rt.T:
        return category
    return SPEC_TO_CANON.get(category, "unknown")


def _target_m2(key: str, given: float | None) -> float:
    if given:
        return given
    t = rt.get(key)
    if t and t.target_m2:
        lo, hi = t.target_m2
        return round((lo + hi) / 2.0, 1)
    return 9.0


def spec_to_programme(spec: Any, *, relaxed: bool = False
                      ) -> tuple[list[RoomReq], list[str]]:
    """DesignSpec -> solver programme. Returns (programme, warnings).

    Rooms the solver cannot lay out are dropped with a warning rather than
    silently: `landscape` has no walls, `shaft` is sub-minimum, and outdoor
    spaces on a rectangular footprint would eat the envelope.
    """
    prog: list[RoomReq] = []
    warn: list[str] = []
    entrance_set = False
    for r in getattr(spec, "rooms", []) or []:
        key = canon(getattr(r, "category", ""))
        if key == "unknown":
            warn.append(f"dropped room '{getattr(r, 'id', '?')}': "
                        f"unmapped category '{getattr(r, 'category', '')}'")
            continue
        if key in ("landscape", "shaft", "parking", "patio", "sitout", "balcony"):
            warn.append(f"deferred '{getattr(r, 'id', '?')}' ({key}): "
                        "outdoor/service space, not laid out by the rectangular solver")
            continue
        t = rt.get(key)
        given = getattr(r, "min_sqft", None)
        target = _target_m2(key, (given / SQFT_M2) if given else None)
        zone = getattr(r, "preferred_zone", None) or (t.vastu_zone if t else None)
        is_entrance = not entrance_set and key == "living"
        entrance_set = entrance_set or is_entrance
        prog.append(RoomReq(
            id=getattr(r, "id", key), name=getattr(r, "name", "") or (t.display if t else key),
            category=key, target_m2=target,
            weight=1.0 if getattr(r, "priority", 3) <= 2 else 0.7,
            vastu_zone=zone, is_entrance=is_entrance,
        ))
    if relaxed:
        _apply_relaxed(prog)
        warn.append(relaxed_note(prog) or "relaxed profile requested but nothing to relax")
    return prog, warn


def truth_to_programme(truth: Any, *, relaxed: bool = False
                       ) -> tuple[list[RoomReq], list[str]]:
    """Suite ground truth -> solver programme, with no LLM in the path.

    Exists so engine failures can be measured separately from extraction
    failures. Running only the end-to-end pipeline conflates the two.
    """
    prog: list[RoomReq] = []
    warn: list[str] = []
    counts: dict[str, int] = {}
    for src in (getattr(truth, "rooms", {}) or {}, getattr(truth, "rooms_min", {}) or {}):
        for key, n in src.items():
            counts[key] = max(counts.get(key, 0), int(n))
    counts.setdefault("living", 1)
    counts.setdefault("kitchen", 1)
    if counts.get("bedroom") and not counts.get("bathroom"):
        counts["bathroom"] = max(1, counts["bedroom"] // 2)

    for key, n in counts.items():
        t = rt.get(key)
        if not t:
            warn.append(f"unknown room type '{key}'")
            continue
        if key in ("landscape", "shaft", "parking", "patio", "sitout", "balcony", "stair"):
            warn.append(f"deferred '{key}': not laid out by the rectangular solver")
            continue
        for i in range(n):
            rid = key if n == 1 else f"{key}{i+1}"
            zone = (getattr(truth, "vastu_zones", {}) or {}).get(key) or t.vastu_zone
            # The first bedroom is the master: bigger, and SW under Vastu.
            if key == "bedroom" and i == 0:
                target = _target_m2("master_bedroom", None)
                zone = (getattr(truth, "vastu_zones", {}) or {}).get("master_bedroom") or "SW"
            else:
                target = _target_m2(key, None)
            prog.append(RoomReq(id=rid, name=f"{t.display} {i+1}" if n > 1 else t.display,
                                category=key, target_m2=target, weight=1.0,
                                vastu_zone=zone, is_entrance=(key == "living" and i == 0)))
    if relaxed:
        _apply_relaxed(prog)
        warn.append(relaxed_note(prog) or "relaxed profile requested but nothing to relax")
    return prog, warn

def typology_adjacency(prog, kind: str
                       ) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Typology expectations -> (required, forbidden) room-id pairs.

    This closes the largest gap in the system. The solver's topology scorer
    optimises only PER-ROOM properties -- min width, min area, area error,
    aspect, has-an-exterior-edge, Vastu compass pull -- plus `required_adjacency`
    at penalty 2500. That adjacency machinery existed and was never populated, so
    the solver had no way to express "the kitchen should be near the living
    room". The visible result: a 2400 sqft plan that put BEDROOM 1 between the
    kitchen and the hall, the only bathroom diagonally opposite the bedrooms, and
    the kitchen in the far corner from the living room.

    Only `direct` and `open` relations become hard requirements. `near` is a
    preference the scorer cannot express as a pair, and `separate` becomes a
    forbidden pair.
    """
    from . import typology as TY
    ty = TY.get(kind)
    by_cat: dict[str, list[str]] = {}
    for r in prog:
        by_cat.setdefault(r.category, []).append(r.id)

    req: list[tuple[str, str]] = []
    forb: list[tuple[str, str]] = []
    for rule in ty.expect:
        ha, hb = by_cat.get(rule.a), by_cat.get(rule.b)
        if not ha or not hb:
            continue
        # One representative pair per rule: forcing every bedroom adjacent to
        # every bathroom is unsatisfiable and would reject good topologies.
        pair = (ha[0], hb[0])
        if rule.relation in ("direct", "open") and rule.weight >= 0.8:
            req.append(pair)
        elif rule.relation == "separate" and rule.weight >= 0.8:
            for x in ha:
                for y in hb:
                    forb.append((x, y))

    # Beyond the typology table: every bathroom should touch either circulation
    # or a bedroom, so a toilet is never marooned across the house from the
    # rooms that use it.
    circ = [i for c in ty.circulation for i in by_cat.get(c, [])]
    beds = by_cat.get("master_bedroom", []) + by_cat.get("bedroom", [])
    for k, bid in enumerate(by_cat.get("bathroom", [])):
        anchor = beds[k] if k < len(beds) else (circ[0] if circ else None)
        if anchor:
            req.append((bid, anchor))
    return req, forb
