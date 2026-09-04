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

from .spec import ROOM_CATEGORIES  # noqa: E402

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




# The contents floor lives in `standards`, with the clearances it comes from.
# It was duplicated here and applied only on this path, so a programme built
# straight from `envelope.bhk_programme` never got it.
from .standards import (CONTENTS_FLOOR_M2 as SERVICE_FLOOR_M2,  # noqa: E402
                        SERVICE_TARGET_M2, SERVICE_TARGET_CAP_M2,
                        MAX_ASPECT)


def cap_service_targets(prog) -> list[str]:
    """Clamp service-room targets AND set a hard ceiling.

    Clamping the target alone was not enough: the target is what the objective
    aims at, not a bound, so CP-SAT still grew a bathroom to 9.4 m2 when there
    was surplus. `max_area_m2` is the actual constraint.
    """
    notes = []
    for r in prog:
        floor = SERVICE_FLOOR_M2.get(r.category)
        if floor is not None:
            r.min_area_m2 = max(r.min_area_m2 or 0.0, floor)
            if r.target_m2 < floor:
                notes.append(f"{r.id}: target {r.target_m2:.1f} -> {floor:.1f} m² "
                             "(contents floor)")
                r.target_m2 = floor
        cap = SERVICE_TARGET_CAP_M2.get(r.category)
        if cap is None:
            continue
        r.max_area_m2 = cap
        if r.target_m2 > cap:
            notes.append(f"{r.id}: target {r.target_m2:.1f} -> {cap:.1f} m² (service cap)")
            r.target_m2 = cap
    return notes


def _target_m2(key: str, given: float | None) -> float:
    if given:
        return given
    if key in SERVICE_TARGET_M2:
        return SERVICE_TARGET_M2[key]
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
        # Balcony, sitout and patio ARE laid out: they occupy a perimeter cell of
        # the tiling and the solver already prices "no exterior edge" heavily, so
        # they land on the facade where they belong. Deferring them is why plans
        # that explicitly asked for two balconies had none.
        # Parking and landscape stay deferred -- they are SITE, placed against the
        # plot boundary by `entrance.place_site_elements`, not tiled with rooms.
        if key in ("landscape", "shaft", "parking"):
            warn.append(f"deferred '{getattr(r, 'id', '?')}' ({key}): "
                        "site element, placed against the plot boundary instead")
            continue
        t = rt.get(key)
        given = getattr(r, "min_sqft", None)
        target = _target_m2(key, (given / SQFT_M2) if given else None)
        zone = getattr(r, "preferred_zone", None) or (t.vastu_zone if t else None)
        is_entrance = not entrance_set and key == "living"
        entrance_set = entrance_set or is_entrance
        rq = RoomReq(
            id=getattr(r, "id", key), name=getattr(r, "name", "") or (t.display if t else key),
            category=key, target_m2=target,
            weight=1.0 if getattr(r, "priority", 3) <= 2 else 0.7,
            vastu_zone=zone, is_entrance=is_entrance,
            # Measured off the corpus rather than a flat 2.6 for every room.
            # See `standards.MAX_ASPECT`: no real bedroom in 89 transcribed
            # rooms exceeds 1.33, so a 2.6 cap licensed corridor-shaped
            # bedrooms that were the right area and unusable.
            max_aspect=MAX_ASPECT.get(key, 2.6),
        )
        # `RoomSpec.optional` was read nowhere on the solve path, so a room the
        # brief only guessed at was as mandatory as one the client asked for --
        # and an over-specified brief could only come back INFEASIBLE. Carried
        # as an attribute rather than a `RoomReq` field so nothing downstream
        # has to know about it unless it wants to.
        rq.optional = bool(getattr(r, "optional", False))   # declared field
        rq.attached_bath = bool(getattr(r, "attached_bath", False))
        # `given` is the brief's own figure. Recording that it was stated is
        # what lets `_allocate` keep it: the size was already computed here and
        # then thrown away one call later by the envelope's proportional budget.
        rq.size_stated = bool(getattr(r, "size_stated", False))
        # The brief's own area ceiling, which the solve path read nowhere. A
        # client who says "master 150-190 sqft" means the upper figure too.
        #
        # Only honoured when it is TIGHTER than the category default, because
        # `RoomSpec.__post_init__` fills `max_sqft` from `ROOM_CATEGORIES` and
        # that erases the difference between a figure the client gave and a
        # fallback we supplied. A narrower range is evidence someone narrowed
        # it; the default is evidence of nothing.
        #
        # This matters for habitable rooms specifically: `RoomReq.max_area_m2`
        # is deliberately None for them so they absorb the envelope's surplus
        # -- capping every one at its table maximum would send that surplus
        # back into the passage, which is the defect
        # DESIGN.CIRCULATION_OVERSIZED exists to catch.
        given_max = getattr(r, "max_sqft", None)
        spec_cat = str(getattr(r, "category", "") or "")
        info = ROOM_CATEGORIES.get(spec_cat)
        default_max = getattr(info, "max_sqft", None) if info else None
        if given_max and (default_max is None or given_max < default_max - 1e-6):
            cap = float(given_max) / SQFT_M2
            rq.max_area_m2 = (cap if rq.max_area_m2 is None
                              else min(rq.max_area_m2, cap))
        prog.append(rq)
    if relaxed:
        _apply_relaxed(prog)
        warn.append(relaxed_note(prog) or "relaxed profile requested but nothing to relax")
    return prog, warn


def shed_optional(prog: list[RoomReq]) -> tuple[list[RoomReq], list[str]]:
    """Split a programme into what was asked for and what was guessed.

    Shared by both solve paths on purpose: `generate.build` and `score.run`
    each need to retry without the nice-to-haves, and two copies of "which
    rooms may be dropped" is how they come to disagree. Returns (kept, shed).
    """
    kept = [r for r in prog if not getattr(r, "optional", False)]
    shed = [r.id for r in prog if getattr(r, "optional", False)]
    return kept, shed


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
        # Same split as above: outdoor ROOMS are tiled, site elements are not.
        if key in ("landscape", "shaft", "parking"):
            warn.append(f"deferred '{key}': site or vertical element, "
                        "not part of the single-storey room tiling")
            continue
        # `stair` used to be deferred with these, and the result was that a
        # brief saying "G+1 duplex with a staircase" produced a plan with no
        # stair anywhere -- 25 of the suite's BRIEF.ROOM_MISSING errors, all
        # true. The reason for deferring it does not apply: what wrecked wall
        # extraction was treating ResPlan's TREAD polygons as room faces during
        # conversion. A stairwell in a plan we generate is an ordinary room
        # with walls round it, and the flight goes inside it.
        for i in range(n):
            rid = key if n == 1 else f"{key}{i+1}"
            cat = key
            zone = (getattr(truth, "vastu_zones", {}) or {}).get(key) or t.vastu_zone
            # The first bedroom is the master: bigger, and SW under Vastu.
            cat = key
            if key == "bedroom" and i == 0:
                # It was given master TREATMENT (bigger target, SW zone) while
                # keeping category="bedroom", so `master_bedroom` never existed
                # and every adjacency asking for it failed by construction.
                cat = "master_bedroom"
                target = _target_m2("master_bedroom", None)
                zone = (getattr(truth, "vastu_zones", {}) or {}).get("master_bedroom") or "SW"
            else:
                target = _target_m2(key, None)
            prog.append(RoomReq(id=rid, name=f"{t.display} {i+1}" if n > 1 else t.display,
                                category=cat, target_m2=target, weight=1.0,
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


# --------------------------------------------------------------- spec -> brief
# The rule engine reads a `brief` dict; `check_brief` looks for its content under
# `brief["requirements"]`. Nothing built that dict from a DesignSpec: the only
# producer was `score.py`, which builds it from hand-written suite ground truth.
# So in production -- prompt -> extract_spec -> solve -> validate -- all six
# BRIEF.* rules received an empty dict and returned nothing, and the service
# passes `brief=None` outright. A stated client requirement was therefore neither
# an input to the solver nor a checkable assertion on the result.
#
# The mapping is mechanical; every field it needs is already on DesignSpec.

# Vastu requirement strings the extractor emits, e.g. "pooja_northeast".
_ZONE_WORDS = {
    "north": "N", "northeast": "NE", "north_east": "NE", "east": "E",
    "southeast": "SE", "south_east": "SE", "south": "S",
    "southwest": "SW", "south_west": "SW", "west": "W",
    "northwest": "NW", "north_west": "NW", "centre": "centre", "center": "centre",
}
# `relation` values `check_brief` can actually test. `visual` needs a sightline
# test and `same_floor` needs a storey field on Room; both are reported as
# unchecked rather than silently dropped, because a requirement that vanishes
# looks exactly like one that passed.
_DOOR_RELATIONS = ("direct_access",)
_TOUCH_RELATIONS = ("adjacent",)


# The vastu requirement strings use a shorter vocabulary than either
# `SPEC_TO_CANON` or `roomtypes` aliases: "master_southwest", not
# "master_bedroom_southwest". Adding "master" as a roomtypes alias would match
# it anywhere in a printed label, so the shorthand is resolved here instead.
_VASTU_ROOM_WORDS = {
    "master": "master_bedroom", "master_bed": "master_bedroom",
    "toilet": "bathroom", "bath": "bathroom", "wc": "bathroom",
    "hall": "living", "drawing": "living", "puja": "pooja",
    "stair": "stair", "staircase": "stair", "entrance": "foyer",
    "entry": "foyer", "main_door": "foyer",
}


def _vastu_zone_pairs(reqs) -> dict[str, str]:
    """["pooja_northeast", "no_toilet_northeast"] -> {"pooja": "NE"}.

    A `no_x_zone` string is a prohibition, not a placement, and the VASTU family
    already scores avoid-zones from `roomtypes.vastu_avoid`; encoding it here as
    a *requested* zone would invert its meaning.
    """
    out: dict[str, str] = {}
    for s in reqs or []:
        t = str(s).strip().lower()
        if t.startswith(("no_", "avoid_", "not_")):
            continue
        for word, zone in sorted(_ZONE_WORDS.items(), key=lambda kv: -len(kv[0])):
            if t.endswith("_" + word) or t.endswith(word):
                head = t[: len(t) - len(word)].rstrip("_")
                cat = canon(head)
                if cat == "unknown":
                    cat = _VASTU_ROOM_WORDS.get(head, "unknown")
                if cat == "unknown":
                    cat = rt.canonical(head.replace("_", " "))
                if cat != "unknown":
                    out[cat] = zone
                break
    return out


def brief_from_spec(spec: Any, *, typology: str | None = None) -> dict:
    """DesignSpec -> the `brief` dict the rule engine reads.

    Counts are per canonical category, so "3 BHK" arriving as two `bedroom`
    plus one `master_bedroom` asks for 3 bedrooms and `check_brief._ids`
    resolves the subtype. Optional rooms are excluded: a nice-to-have that did
    not fit is a trade-off the solver reports, not a brief failure.
    """
    rooms: dict[str, int] = {}
    zones: dict[str, str] = {}
    attached = 0
    for r in getattr(spec, "rooms", []) or []:
        if getattr(r, "optional", False):
            continue
        key = canon(getattr(r, "category", ""))
        if key == "unknown":
            continue
        # Count master_bedroom as a bedroom request too, matching how the suite
        # and `roomtypes.SUBTYPE_OF` treat it.
        rooms[key] = rooms.get(key, 0) + 1
        if getattr(r, "attached_bath", False):
            attached += 1
        z = getattr(r, "preferred_zone", None)
        if z:
            zones[key] = z

    ent = getattr(spec, "entrance", None)
    if ent is not None and getattr(ent, "via_foyer", False):
        rooms.setdefault("foyer", 1)

    door_pairs: list[list[str]] = []
    touch_pairs: list[list[str]] = []
    forbid_pairs: list[list[str]] = []
    unchecked: list[str] = []
    for adj in getattr(spec, "adjacency", []) or []:
        a, b = canon(getattr(adj, "a", "")), canon(getattr(adj, "b", ""))
        if a == "unknown" or b == "unknown" or a == b:
            continue
        rel = getattr(adj, "relation", "adjacent")
        prohibited = getattr(adj, "kind", "required") == "prohibited"
        if prohibited:
            # A prohibition on any relation is at least a prohibition on a door,
            # which is the strongest thing we can test.
            forbid_pairs.append([a, b])
        elif rel in _DOOR_RELATIONS:
            door_pairs.append([a, b])
        elif rel in _TOUCH_RELATIONS:
            touch_pairs.append([a, b])
        else:
            unchecked.append(f"{a}-{b} ({rel})")

    vs = getattr(spec, "vastu", None)
    zones.update(_vastu_zone_pairs(getattr(vs, "requirements", None)))

    area = getattr(spec, "unit_area", None)
    is_unit = getattr(spec, "site_kind", "plot") == "apartment_unit"
    w, d = getattr(spec, "plot_width_ft", None), getattr(spec, "plot_depth_ft", None)

    brief: dict = {
        "requirements": {
            "rooms": rooms,
            "adjacent": door_pairs,
            "touching": touch_pairs,
            "not_adjacent": forbid_pairs,
            "vastu_zones": zones,
            "must_place": [],
            "place_in": {},
            "unchecked_relations": unchecked,
        },
        "site_kind": getattr(spec, "site_kind", "plot"),
        "plot_area_sqft": (w * d) if (w and d) else None,
        "habitable_floors": int(getattr(spec, "storeys", 1) or 1),
        "attached_bath": attached,
        "vastu": bool(getattr(vs, "enabled", False)),
    }
    if typology:
        brief["typology"] = typology
    if is_unit and area is not None:
        brief["carpet_sqft"] = (getattr(area, "rera_carpet_sqft", None)
                                or getattr(area, "carpet_sqft", None))
    return brief


def spec_adjacency_pairs(spec: Any, prog: list[RoomReq]
                         ) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """The spec's OWN adjacency wishes as solver room-id pairs.

    `spec_to_programme` never read `spec.adjacency`, so "a balcony off the master
    bedroom" reached neither the solver nor the validator. Distinct hosts where
    the brief asks for several of one type: two balconies off two different
    rooms need two different hosts, not the same one twice.
    """
    by_cat: dict[str, list[str]] = {}
    for r in prog:
        by_cat.setdefault(r.category, []).append(r.id)
    room_ids = {r.id for r in prog}

    def pick(token: str, used: set[str]) -> str | None:
        # An agent's `set_adjacency` names room ids; an extracted brief names
        # categories. Accept either.
        if token in room_ids:
            return token
        cats = rt.subtypes_of(canon(token)) + rt.counts_as(canon(token))[1:]
        for c in cats:
            for i in by_cat.get(c, []):
                if i not in used:
                    return i
        for c in cats:
            if by_cat.get(c):
                return by_cat[c][0]
        return None

    def all_of(token: str) -> list[str]:
        if token in room_ids:
            return [token]
        c = canon(token)
        return [i for cat in rt.subtypes_of(c) + rt.counts_as(c)[1:]
                for i in by_cat.get(cat, [])]

    req: list[tuple[str, str]] = []
    forb: list[tuple[str, str]] = []
    used: set[str] = set()
    for adj in getattr(spec, "adjacency", []) or []:
        a, b = getattr(adj, "a", ""), getattr(adj, "b", "")
        if a == b or (a not in room_ids and canon(a) == "unknown") \
                or (b not in room_ids and canon(b) == "unknown"):
            continue
        if getattr(adj, "kind", "required") == "prohibited":
            # A prohibition applies to EVERY room it names -- "no toilet off
            # the kitchen" means no toilet off any kitchen -- so it is the
            # cross product, not one chosen host.
            forb += [(x, y) for x in all_of(a) for y in all_of(b) if x != y]
            continue
        if getattr(adj, "relation", "adjacent") not in _DOOR_RELATIONS + _TOUCH_RELATIONS:
            continue                            # `visual`/`same_floor`: no solver term
        x, y = pick(a, used), pick(b, used)
        if x and y and x != y:
            req.append((x, y))
            used.update((x, y))
    return req, forb


# ------------------------------------------------------- relational solve terms
# `topology.solver_pairs` turns a scenario's signed preference matrix into
# required / forbidden / weighted room-id pairs, and the solver has carried the
# machinery for all three since the start: `required_adjacency` is priced at 1e7
# in the cross-topology key, `forbidden_adjacency` removes the edge outright,
# and `soft_adjacency` is weighted by `w_soft_adj`.
#
# Only `score.py` ever populated them. `loop.py` and `service/app.py` built a
# LayoutSpec from programme + entrance + time limit and nothing else, so on both
# of those paths the 1e7 term multiplied an empty list and `w_soft_adj` weighted
# nothing -- the plan was solved with no relational objective at all and then
# validated against rules that assume one. The measured suite figures therefore
# described a configuration neither the repair loop nor the service ran.
#
# One helper, so the three callers cannot drift again.

def resolve_scenario(prog: list[RoomReq], *, site_kind: str = "plot",
                     plot_sqft: float | None = None,
                     carpet_sqft: float | None = None, storeys: int = 1,
                     scenario_key: str | None = None):
    """The topology scenario for a programme. Explicit key beats inference."""
    from . import topology as TP
    if scenario_key and scenario_key in TP.SCENARIOS:
        return TP.SCENARIOS[scenario_key]
    cats = [r.category for r in prog]
    return TP.resolve(
        site_kind=site_kind, plot_sqft=plot_sqft, carpet_sqft=carpet_sqft,
        storeys=int(storeys or 1),
        bedrooms=sum(1 for c in cats if c in ("bedroom", "master_bedroom")),
        kitchens=max(sum(1 for c in cats if c == "kitchen"), 1),
        has_two_living=sum(1 for c in cats if c == "living") >= 2)


def relational_pairs(prog: list[RoomReq], scenario, *, spec: Any = None
                     ) -> tuple[list[tuple[str, str]], list[tuple[str, str]],
                                list[tuple[str, str, float]]]:
    """(required, forbidden, weighted) for `LayoutSpec`, scenario + spec merged.

    The scenario supplies the defaults for the typology and size band; the spec
    supplies what this client actually asked for, and a client's explicit wish
    outranks a default -- so spec pairs are appended after and a forbidden pair
    wins over a required one carrying the same rooms. Silently solving for a
    pair the brief forbids is worse than dropping the default.
    """
    from . import topology as TP
    req, forb, soft = TP.solver_pairs(prog, scenario)
    if spec is not None:
        s_req, s_forb = spec_adjacency_pairs(spec, prog)
        req += s_req
        forb += s_forb
    forb_keys = {frozenset(p) for p in forb}
    req = [p for p in dict.fromkeys(req)
           if p[0] != p[1] and frozenset(p) not in forb_keys]
    forb = [p for p in dict.fromkeys(forb) if p[0] != p[1]]
    req_keys = {frozenset(p) for p in req}
    soft = [t for t in soft if t[0] != t[1] and frozenset(t[:2]) not in forb_keys
            and frozenset(t[:2]) not in req_keys]
    return req, forb, soft
