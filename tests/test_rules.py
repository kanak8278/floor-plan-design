"""Tests for the rules engine.

Two controls, and the negative one is the important one:

  * POSITIVE - every `mutate.*` injury must be named by the validator.
  * NEGATIVE - a clean plan must produce ZERO errors. In a generate/critique
    loop a false positive is worse than a miss: it teaches the generator to
    avoid a legal move, and there is no dataset to appeal to.

Split by what each corpus can actually prove:

  * SYNTHETIC plans have exact millimetre dimensions, a surveyed plot and a
    declared brief, so absolute NBC / bye-law numbers are assertable.
  * ResPlan is scale-ambiguous - coordinates are not metric and the scale is
    inferred from `wall_depth` assuming 226 mm walls (+/-30%). Asserting
    "this bedroom is >= 7.5 m^2" on ResPlan would be asserting the scale guess.
    ResPlan is therefore used for geometry/topology rules and for measuring the
    false-positive rate, never for absolute metric pass/fail.

Run `python tests/test_rules.py [N]` for the corpus + latency report.
"""
from __future__ import annotations

import sys, os, time, pickle, statistics
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from shapely.geometry import Polygon
from shapely.ops import unary_union

from fpeval.ir import Plan, Wall, Opening, Room, Site, P
from fpeval import mutate
from fpeval.bylaws import BENGALURU, CityProfile
from fpeval.rules import (validate, report, vastu_score, clear_width_mm,
                          passage_clear_width_mm, room_polygon,
                          buildable_polygon, bearing_deg, zone_of_bearing)

RESPLAN_PKL = os.environ.get("RESPLAN_PKL", "/tmp/resplan/data/ResPlan.pkl")
WALL_MM = 230


# ===================================================================== synth ==

def _build(rects: list[tuple[str, str, tuple[int, int, int, int]]],
           doors: list[tuple[int, int, str, int]],
           plot: tuple[int, int, int, int] | None,
           north_deg: float, plan_id: str) -> Plan:
    """Assemble a Plan from axis-aligned room rects given on wall CENTRELINES.

    Walls come from noding the union of room boundaries - the same operation
    the ResPlan inversion uses - so the synthetic wall graph has the same
    topology properties as the real corpus and `polygonize` recovers the rooms.
    """
    polys = {name: Polygon([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
             for name, _cat, (x0, y0, x1, y1) in rects}
    noded = unary_union([p.boundary for p in polys.values()])
    segs = list(noded.geoms) if hasattr(noded, "geoms") else [noded]

    walls: list[Wall] = []
    for ls in segs:
        cs = list(ls.coords)
        for a, b in zip(cs, cs[1:]):
            if a == b:
                continue
            walls.append(Wall(id=f"w{len(walls)}",
                              start=P(int(a[0]), int(a[1])),
                              end=P(int(b[0]), int(b[1])),
                              thickness=WALL_MM, height=3000))
    # merge collinear runs so doors have a wall long enough to host them
    walls = _merge_collinear(walls)

    rooms: list[Room] = []
    for i, (name, cat, (x0, y0, x1, y1)) in enumerate(rects):
        p = polys[name]
        rooms.append(Room(id=f"r{i}", name=name, category=cat, wall_ids=[],
                          polygon=[P(x0, y0), P(x1, y0), P(x1, y1), P(x0, y1)],
                          area=int(p.area)))
    # room -> wall membership (used only for per-room ceiling height)
    for r in rooms:
        rp = polys[r.name].boundary.buffer(30.0)
        r.wall_ids = [w.id for w in walls
                      if rp.contains(Polygon([(w.start.x, w.start.y),
                                              (w.end.x, w.end.y),
                                              (w.start.x, w.start.y)]).centroid)]

    openings: list[Opening] = []
    for (dx, dy, kind, width) in doors:
        host, pos = None, 0.0
        for w in walls:
            L = w.length
            if L < 1:
                continue
            vx, vy = w.end.x - w.start.x, w.end.y - w.start.y
            t = ((dx - w.start.x) * vx + (dy - w.start.y) * vy) / (L * L)
            if not (0.0 <= t <= 1.0):
                continue
            px, py = w.start.x + t * vx, w.start.y + t * vy
            if abs(px - dx) < 1 and abs(py - dy) < 1:
                if host is None or L > host.length:
                    host, pos = w, t
        assert host is not None, f"door at {(dx, dy)} is not on any wall"
        assert width <= host.length, f"door {width} > wall {host.length}"
        openings.append(Opening(id=f"o{len(openings)}", kind=kind,
                                wall_id=host.id, position=round(pos, 6),
                                width=width, sill=0, head=2100))

    site = Site(north_deg=north_deg)
    if plot is not None:
        x0, y0, x1, y1 = plot
        site.plot_polygon = [P(x0, y0), P(x1, y0), P(x1, y1), P(x0, y1)]
        site.setbacks_mm = {"front": 0, "rear": 0, "side": 0}   # marks it surveyed
    return Plan(id=plan_id, walls=walls, openings=openings, rooms=rooms,
                site=site, storey_height=3000,
                provenance={"source": "synthetic"})


def _merge_collinear(walls: list[Wall]) -> list[Wall]:
    """Union 1-D intervals per infinite line, so a run can span T-junctions."""
    buckets: dict[tuple, list[tuple[float, float]]] = {}
    for w in walls:
        ax, ay, bx, by = w.start.x, w.start.y, w.end.x, w.end.y
        if ax == bx:
            key = ("v", ax)
            iv = tuple(sorted((ay, by)))
        elif ay == by:
            key = ("h", ay)
            iv = tuple(sorted((ax, bx)))
        else:
            key = ("d", ax, ay, bx, by)
            iv = (0.0, 1.0)
        buckets.setdefault(key, []).append(iv)
    out: list[Wall] = []
    for key, ivs in buckets.items():
        ivs.sort()
        merged = [list(ivs[0])]
        for lo, hi in ivs[1:]:
            if lo <= merged[-1][1] + 1e-9:
                merged[-1][1] = max(merged[-1][1], hi)
            else:
                merged.append([lo, hi])
        for lo, hi in merged:
            if key[0] == "v":
                s, e = P(int(key[1]), int(lo)), P(int(key[1]), int(hi))
            elif key[0] == "h":
                s, e = P(int(lo), int(key[1])), P(int(hi), int(key[1]))
            else:
                s, e = P(int(key[1]), int(key[2])), P(int(key[3]), int(key[4]))
            out.append(Wall(id=f"w{len(out)}", start=s, end=e,
                            thickness=WALL_MM, height=3000))
    return out


# House on wall centrelines, origin shifted so the footprint clears the
# BBMP setbacks of an 11.5 x 16.0 m (1981 sqft) plot: front 12% of depth =
# 1920, rear 8% = 1280, side 8% of width = 920 mm.
OX, OY = 1200, 2200


def clean_plan(north_deg: float = 180.0) -> Plan:
    """A deliberately NBC- and BBMP-compliant 3.5 BHK. The negative control.

    Every dimension is checked by hand: smallest habitable room 8.25 m^2 /
    2500 mm wide (limits 7.5 / 2400), kitchen 7.92 m^2 / 2200 mm (5.0 / 1800),
    baths 8.28 and 6.0 m^2 / 2300 and 2000 mm (2.8 / 1200), passage 1200 mm
    (900), ceiling 3000 mm (2750). Footprint 112.9 m^2 on a 184 m^2 plot =
    61.4% coverage and FAR 0.61 (caps 75% and 1.75).
    """
    o = lambda x, y: (x + OX, y + OY)
    R = [
        ("Living",         "living",   (*o(0, 0),        *o(5400, 4500))),
        ("Kitchen",        "kitchen",  (*o(5400, 0),     *o(9000, 2200))),
        ("Bathroom 1",     "bathroom", (*o(5400, 2200),  *o(9000, 4500))),
        ("Passage",        "living",   (*o(0, 4500),     *o(9000, 5700))),
        ("Bedroom 1",      "bedroom",  (*o(0, 5700),     *o(3000, 9000))),
        ("Master Bedroom", "bedroom",  (*o(3000, 5700),  *o(6500, 9600))),
        ("Bedroom 3",      "bedroom",  (*o(6500, 5700),  *o(9000, 9000))),
        ("Bedroom 4",      "bedroom",  (*o(3000, 9600),  *o(6500, 12000))),
        ("Bathroom 2",     "bathroom", (*o(0, 9000),     *o(3000, 11000))),
        ("Store",          "storage",  (*o(0, 11000),    *o(3000, 12000))),
        ("Balcony",        "balcony",  (*o(6500, 9000),  *o(9000, 12000))),
    ]
    D = [
        (*o(2700, 0),     "front_door", 1050),   # entrance -> Living
        (*o(2700, 4500),  "door", 900),          # Living   -> Passage
        (*o(7200, 4500),  "door", 900),          # Bath 1   -> Passage
        (*o(5400, 1100),  "door", 900),          # Living   -> Kitchen
        (*o(1500, 5700),  "door", 900),          # Passage  -> Bedroom 1
        (*o(4750, 5700),  "door", 900),          # Passage  -> Master
        (*o(7750, 5700),  "door", 900),          # Passage  -> Bedroom 3
        (*o(1500, 9000),  "door", 900),          # Bedroom1 -> Bath 2
        (*o(1500, 11000), "door", 900),          # Bath 2   -> Store
        (*o(7750, 9000),  "door", 900),          # Bedroom3 -> Balcony
        (*o(4750, 9600),  "door", 900),          # Master   -> Bedroom 4
    ]
    return _build(R, D, plot=(0, 0, 11500, 16000), north_deg=north_deg,
                  plan_id="synth-clean")


CLEAN_BRIEF = {"habitable_floors": 1, "stilt": False,
               "rainwater_harvesting": True, "building_type": "dwelling",
               "site_is_surveyed": True}


# ============================================================ unit: measures ==

def test_clear_width_rectangle_is_short_side():
    p = Polygon([(0, 0), (5000, 0), (5000, 2400), (0, 2400)])
    assert abs(clear_width_mm(p) - 2400) < 2


def test_clear_width_l_shape_reports_widest_clear_span():
    # 4 m x 4 m square with a 1 m x 1 m bite: the inscribed span stays 3 m,
    # which is the documented semantics (a usable dimension exists).
    p = Polygon([(0, 0), (4000, 0), (4000, 3000), (3000, 3000),
                 (3000, 4000), (0, 4000)])
    w = clear_width_mm(p)
    assert 2900 < w < 3100, w


def test_passage_width_finds_the_pinch_not_the_widest_point():
    # 4 m long corridor, 1.5 m wide at the ends, 0.7 m in the middle.
    p = Polygon([(0, 0), (4000, 0), (4000, 1500), (2600, 1500), (2600, 700),
                 (1400, 700), (1400, 1500), (0, 1500)])
    doors = [(50, 750), (3950, 750)]
    w = passage_clear_width_mm(p, doors, 900)
    assert w < 900, w                       # the pinch governs
    # ...and the inscribed span is the 1500 mm end bay (erosion converges
    # from below, so the measure under-reports by <0.5 mm at 12 iterations;
    # every rule compares against `limit - 1` to absorb exactly that).
    assert clear_width_mm(p) > 1395


def test_bearing_uses_plot_north():
    assert zone_of_bearing(bearing_deg(0, 1, 0.0)) == "N"
    assert zone_of_bearing(bearing_deg(1, 0, 0.0)) == "E"
    assert zone_of_bearing(bearing_deg(0, 1, 180.0)) == "S"
    assert zone_of_bearing(bearing_deg(1, 1, 0.0)) == "NE"


def test_profile_bands_resolve_and_round_trip(tmp_path):
    p = BENGALURU
    assert p.resolve(600).key == "upto_600"
    assert p.resolve(1200).key == "upto_1200"
    assert p.resolve(2400).key == "upto_2400"
    assert p.resolve(5000).key == "above_3875"
    assert p.resolve(600).far == 1.75 and p.resolve(5000).far == 2.25
    assert p.resolve(5000).max_ground_coverage == 0.65
    # percentage setbacks resolve against the plot dimensions
    b = p.resolve(2400)
    assert b.front.resolve_mm(18288, 12192) == int(round(0.12 * 18288))
    assert b.side.resolve_mm(18288, 12192) == int(round(0.08 * 12192))
    f = tmp_path / "city.json"
    p.to_json(f)
    q = CityProfile.from_json(f)
    assert q.resolve(2400).key == "upto_2400"
    assert q.nbc.hab_min_area_m2 == 7.5
    assert len(q.vastu.rules) == len(p.vastu.rules)


# ======================================================== NEGATIVE CONTROL ====

def test_clean_synthetic_plan_has_no_errors():
    fs = validate(clean_plan(), CLEAN_BRIEF, BENGALURU)
    errs = [f for f in fs if f.severity == "error"]
    assert errs == [], "\n".join(f"{f.rule_id}: {f.detail}" for f in errs)


def test_clean_plan_bylaws_pass_explicitly():
    r = report(clean_plan(), CLEAN_BRIEF, BENGALURU)
    ids = {f.rule_id for f in r["findings"]}
    for rid in ("BYLAW.SETBACK_ENCROACH", "BYLAW.GROUND_COVERAGE", "BYLAW.FAR",
                "BYLAW.SITE_UNSPECIFIED", "BYLAW.RWH_UNDECLARED"):
        assert rid not in ids, rid
    assert r["hard_ok"]


def test_clean_plan_is_fully_reachable():
    fs = validate(clean_plan(), CLEAN_BRIEF, BENGALURU)
    assert not [f for f in fs if f.rule_id == "GEO.UNREACHABLE_ROOM"]


def test_vastu_score_is_continuous_and_bounded():
    lo = hi = None
    for n in range(0, 360, 15):
        s, _ = vastu_score(clean_plan(north_deg=float(n)), BENGALURU)
        assert 0.0 <= s <= 1.0
        lo = s if lo is None else min(lo, s)
        hi = s if hi is None else max(hi, s)
    assert hi - lo > 0.05, "score does not respond to plot orientation"


def test_vastu_never_errors():
    for n in (0.0, 90.0, 180.0, 270.0):
        fs = validate(clean_plan(north_deg=n), CLEAN_BRIEF, BENGALURU)
        assert all(f.severity == "warn"
                   for f in fs if f.rule_id.startswith("VASTU."))


def test_degenerate_input_reports_instead_of_crashing():
    """The generate loop will hand us junk; it must get findings, not a traceback."""
    from fpeval.ir import Opening
    ids = {f.rule_id for f in validate(Plan(id="empty"))}
    assert "GEO.NO_ROOMS" in ids
    p = Plan(id="junk",
             walls=[Wall("w0", P(0, 0), P(0, 0), 230),
                    Wall("w1", P(0, 0), P(50, 0), 230)],
             openings=[Opening("o0", "door", "nope", 0.5, 900),
                       Opening("o1", "door", "w1", 2.5, 900)],
             rooms=[Room("r0", "X", "bedroom", [], [P(0, 0), P(1, 0), P(1, 1)], 1),
                    Room("r1", "Y", "bedroom", [], [P(0, 0)], 0)])
    ids = {f.rule_id for f in validate(p)}
    for rid in ("GEO.ROOM_DEGENERATE", "GEO.OPENING_ORPHAN",
                "GEO.OPENING_TOO_WIDE", "GEO.WALL_TOO_SHORT",
                "GEO.OPENING_OFF_WALL", "GEO.NO_FRONT_DOOR"):
        assert rid in ids, rid


def test_findings_sorted_most_severe_first():
    fs = validate(mutate.overlap_two_rooms(clean_plan())[0], CLEAN_BRIEF)
    sev = [f.severity for f in fs]
    assert sev == sorted(sev, key=lambda s: 0 if s == "error" else 1)


# ==================================================== bye-law rules by hand ===

def test_setback_encroachment_fires_when_the_plot_is_too_shallow():
    """Same house, plot depth cut 16.0 -> 14.0 m: 12% front + 8% rear no longer fit."""
    p = clean_plan()
    p.site.plot_polygon = [P(0, 0), P(11500, 0), P(11500, 14000), P(0, 14000)]
    ids = {f.rule_id for f in validate(p, CLEAN_BRIEF, BENGALURU)}
    assert "BYLAW.SETBACK_ENCROACH" in ids
    assert "BYLAW.GROUND_COVERAGE" not in ids     # 70% coverage, still under 75%


def test_far_fires_at_three_floors_but_not_at_one():
    p = clean_plan()
    one = {f.rule_id for f in validate(p, CLEAN_BRIEF, BENGALURU)}
    assert "BYLAW.FAR" not in one
    three = {f.rule_id for f in validate(p, {**CLEAN_BRIEF, "habitable_floors": 3})}
    assert "BYLAW.FAR" in three                   # 3 x 112.9 / 184 = 1.84 > 1.75
    assert "BYLAW.MAX_FLOORS" not in three        # band allows stilt+4


def test_max_floors_fires_above_the_band():
    ids = {f.rule_id for f in validate(clean_plan(),
                                       {**CLEAN_BRIEF, "habitable_floors": 6})}
    assert "BYLAW.MAX_FLOORS" in ids


def test_rwh_is_reported_as_undeclared_not_silently_passed():
    brief = {k: v for k, v in CLEAN_BRIEF.items() if k != "rainwater_harvesting"}
    ids = {f.rule_id for f in validate(clean_plan(), brief, BENGALURU)}
    assert "BYLAW.RWH_UNDECLARED" in ids          # 1981 sqft >= 1200
    ids = {f.rule_id for f in validate(clean_plan(),
                                       {**brief, "rainwater_harvesting": False})}
    assert "BYLAW.RWH_REQUIRED" in ids


def test_bylaws_are_skipped_not_guessed_without_a_surveyed_plot():
    p = clean_plan()
    p.site.plot_polygon = []
    p.site.setbacks_mm = {}
    ids = {f.rule_id for f in validate(p, {}, BENGALURU)}
    assert "BYLAW.SITE_UNSPECIFIED" in ids
    assert not [i for i in ids if i in ("BYLAW.GROUND_COVERAGE", "BYLAW.FAR",
                                        "BYLAW.SETBACK_ENCROACH")]


def test_buildable_polygon_honours_one_side_setback_bands():
    """A 600 sqft BBMP plot needs a setback on ONE side only, not both."""
    plot = Polygon([(0, 0), (6096, 0), (6096, 9144), (0, 9144)])   # 20 x 30 ft
    band = BENGALURU.resolve(600)
    assert band.side.both_sides is False
    envl, meta = buildable_polygon(plot, band, (0.0, -1.0))
    assert meta == {"front_mm": 900, "rear_mm": 700, "side_mm": 700,
                    "depth_mm": 9144.0, "width_mm": 6096.0, "side_both": False}
    # depth 9144 - 900 - 700; width 6096 - 700 (one side only)
    assert abs(envl.area - (9144 - 1600) * (6096 - 700)) < 1.0
    band24 = BENGALURU.resolve(2400)
    assert band24.side.both_sides is True
    e2, m2 = buildable_polygon(plot, band24, (0.0, -1.0))
    assert m2["front_mm"] == int(round(0.12 * 9144))
    assert e2.area < envl.area


# ================================================== vastu, rule by rule =======

def _rename(plan: Plan, old: str, new: str) -> Plan:
    for r in plan.rooms:
        if r.name == old:
            r.name = new
    return plan


def test_vastu_pooja_rule_fires_when_pooja_is_south():
    p = _rename(clean_plan(north_deg=180.0), "Store", "Pooja Room")
    fs = {f.rule_id for f in validate(p, CLEAN_BRIEF, BENGALURU)}
    assert "VASTU.POOJA" in fs


def test_vastu_stairs_rule_depends_on_plot_bearing():
    good = _rename(clean_plan(north_deg=180.0), "Balcony", "Staircase")
    bad = _rename(clean_plan(north_deg=0.0), "Balcony", "Staircase")
    assert "VASTU.STAIRS" not in {f.rule_id for f in validate(good, CLEAN_BRIEF)}
    assert "VASTU.STAIRS" in {f.rule_id for f in validate(bad, CLEAN_BRIEF)}


def test_brahmasthan_weight_is_tunable():
    import copy
    soft = copy.deepcopy(BENGALURU)
    soft.vastu.brahmasthan_weight = 0.0
    base, _ = vastu_score(clean_plan(), BENGALURU)
    off, fs = vastu_score(clean_plan(), soft)
    assert off > base, (off, base)                       # the penalty was real
    assert "VASTU.BRAHMASTHAN" not in {f.rule_id for f in fs}


def test_vastu_entrance_rule_prefers_north_east():
    # north_deg 0 puts the front door (at -Y) due south: the SW/S taboo zone.
    south = {f.rule_id for f in validate(clean_plan(north_deg=0.0), CLEAN_BRIEF)}
    north = {f.rule_id for f in validate(clean_plan(north_deg=180.0), CLEAN_BRIEF)}
    assert "VASTU.ENTRANCE" in south
    assert "VASTU.ENTRANCE" not in north


# =============================================== two-tier reachability ========

def _drop_doors_between(plan: Plan, a: str, b: str) -> Plan:
    """Remove the door(s) joining the rooms named `a` and `b`."""
    from fpeval.rules import _build_ctx
    ctx = _build_ctx(plan, {}, BENGALURU)
    ids = {r.name: r.id for r in plan.rooms}
    want = {ids[a], ids[b]}
    drop = {oid for oid, x, y in ctx.door_edges if {x, y} == want}
    assert drop, f"no door between {a!r} and {b!r}"
    plan.openings = [o for o in plan.openings if o.id not in drop]
    return plan


def test_open_plan_kitchen_warns_but_does_not_error():
    """A kitchen with no door but a 2200 mm opening to the living room is legal.

    This is the single biggest false-positive source on real plans: 148 of the
    kitchens in 300 ResPlan conversions have no door polygon at all.
    """
    p = _drop_doors_between(clean_plan(), "Living", "Kitchen")
    fs = validate(p, CLEAN_BRIEF, BENGALURU)
    unreach = [f for f in fs if f.rule_id == "GEO.UNREACHABLE_ROOM"]
    assert len(unreach) == 1 and unreach[0].severity == "warn", unreach
    assert not [f for f in fs if f.severity == "error"]


def test_bedroom_with_no_door_at_all_is_an_error():
    """Privacy is not satisfied by an arch, whatever the geometry allows."""
    p = _drop_doors_between(clean_plan(), "Passage", "Bedroom 1")
    p = _drop_doors_between(p, "Bedroom 1", "Bathroom 2")
    hard = [f for f in validate(p, CLEAN_BRIEF, BENGALURU)
            if f.rule_id == "GEO.UNREACHABLE_ROOM" and f.severity == "error"]
    assert len(hard) == 1, hard
    assert "must have a door" in hard[0].detail


def test_bedroom_behind_an_arch_but_with_its_own_door_only_warns():
    """Reaching a bedroom by walking through an open living/dining arch is legal.

    The bedroom still has a door; what is missing is a modelled opening on the
    route. Erroring here is what took the doors-only rule to a 10.3% plan-level
    false-positive rate on ResPlan.
    """
    p = _drop_doors_between(clean_plan(), "Passage", "Bedroom 1")
    fs = validate(p, CLEAN_BRIEF, BENGALURU)
    assert not [f for f in fs if f.severity == "error"]
    warns = [f for f in fs if f.rule_id == "GEO.UNREACHABLE_ROOM"]
    assert warns and all("open threshold" in f.detail for f in warns)


def test_doorless_store_only_warns():
    p = _drop_doors_between(clean_plan(), "Bathroom 2", "Store")
    fs = validate(p, CLEAN_BRIEF, BENGALURU)
    assert not [f for f in fs if f.severity == "error"]


# ======================================================== POSITIVE CONTROLS ===

SYNTH_MUTATIONS = [
    ("shrink_passage", lambda p: mutate.shrink_passage(p, to_mm=820)),
    ("shrink_room_below_min", lambda p: mutate.shrink_room_below_min(p, "bedroom")),
    ("shrink_kitchen_below_min", lambda p: mutate.shrink_room_below_min(p, "kitchen")),
    ("shrink_bath_below_min", lambda p: mutate.shrink_room_below_min(p, "bathroom")),
    ("overlap_two_rooms", mutate.overlap_two_rooms),
    ("disconnect_room", mutate.disconnect_room),
    ("move_kitchen_to_zone_NE", lambda p: mutate.move_kitchen_to_zone(p, "NE")),
    ("exceed_coverage", lambda p: mutate.exceed_coverage(p, ratio=0.9)),
    ("wc_into_kitchen", mutate.wc_into_kitchen),
    ("widen_opening_beyond_wall", mutate.widen_opening_beyond_wall),
]


@pytest.mark.parametrize("name,fn", SYNTH_MUTATIONS, ids=[n for n, _ in SYNTH_MUTATIONS])
def test_mutation_is_detected_on_synthetic(name, fn):
    base = clean_plan()
    bad, expected = fn(base)
    assert base.to_dict() == clean_plan().to_dict(), "mutation touched its input"
    got = {f.rule_id for f in validate(bad, CLEAN_BRIEF, BENGALURU)}
    missing = [e for e in expected if e not in got]
    assert not missing, f"{name}: missing {missing}; got {sorted(got)}"


TOPOLOGY_MUTATIONS = [
    ("overlap_two_rooms", mutate.overlap_two_rooms),
    ("disconnect_room", mutate.disconnect_room),
    ("widen_opening_beyond_wall", mutate.widen_opening_beyond_wall),
    ("wc_into_kitchen", mutate.wc_into_kitchen),
    ("move_kitchen_to_zone_NE", lambda p: mutate.move_kitchen_to_zone(p, "NE")),
    ("exceed_coverage", lambda p: mutate.exceed_coverage(p, ratio=0.9)),
]


def _resplan(n: int):
    if not Path(RESPLAN_PKL).exists():
        pytest.skip(f"{RESPLAN_PKL} not present")
    from fpeval.resplan import convert
    raws = pickle.load(open(RESPLAN_PKL, "rb"))
    out = []
    for raw in raws:
        if len(out) >= n:
            break
        try:
            out.append(convert(raw))
        except Exception:
            continue
    return out


@pytest.mark.parametrize("name,fn", TOPOLOGY_MUTATIONS,
                         ids=[n for n, _ in TOPOLOGY_MUTATIONS])
def test_topology_mutation_is_detected_on_real_plans(name, fn):
    """Same injuries on real geometry. Only scale-free rules are asserted."""
    plans = _resplan(25)
    applied = hit = 0
    for pl in plans:
        try:
            bad, expected = fn(pl)
        except mutate.MutationNotApplicable:
            continue
        applied += 1
        got = {f.rule_id for f in validate(bad, {"vastu": True})}
        hit += all(e in got for e in expected)
    assert applied >= 5, f"{name}: only applicable to {applied} plans"
    assert hit == applied, f"{name}: {hit}/{applied} detected"


def test_mutations_do_not_touch_the_input():
    for pl in _resplan(5):
        before = pl.to_dict()
        for fn in (mutate.overlap_two_rooms, mutate.disconnect_room,
                   mutate.widen_opening_beyond_wall, mutate.wc_into_kitchen,
                   mutate.exceed_coverage):
            try:
                fn(pl)
            except mutate.MutationNotApplicable:
                pass
        assert pl.to_dict() == before


# ============================================ false positives on real plans ===

# Scale-dependent rules cannot be asserted on ResPlan (DECISIONS.md #4): a
# +/-30% scale error moves every area by up to 69%. They are still *measured*
# below, and reported separately from the scale-free families.
SCALE_FREE = {
    "GEO.NO_ROOMS",
    "GEO.ROOM_OVERLAP", "GEO.ROOM_DEGENERATE", "GEO.ROOM_OUTSIDE_ENVELOPE",
    "GEO.ROOM_AREA_MISMATCH", "GEO.WALL_TOO_SHORT", "GEO.OPENING_ORPHAN",
    "GEO.OPENING_TOO_WIDE", "GEO.OPENING_OVERRUNS_WALL", "GEO.OPENING_OFF_WALL",
    "GEO.NO_FRONT_DOOR", "GEO.UNREACHABLE_ROOM",
    "NBC.WC_OPENS_INTO_KITCHEN",
}


def _fp_scan(plans):
    per_rule: dict[str, int] = {}
    per_family_plans: dict[str, int] = {}
    lat: list[float] = []
    for pl in plans:
        t0 = time.perf_counter()
        fs = validate(pl, {"vastu": True})
        lat.append((time.perf_counter() - t0) * 1000.0)
        fams = set()
        for f in fs:
            if f.severity != "error":
                continue
            per_rule[f.rule_id] = per_rule.get(f.rule_id, 0) + 1
            fams.add(f.rule_id.split(".")[0])
        for fam in fams:
            per_family_plans[fam] = per_family_plans.get(fam, 0) + 1
    return per_rule, per_family_plans, lat


# Per-rule budget for `error`-severity hits. Measured over 300 conversions,
# audited hit by hit; each surviving hit is a defect in the source geometry,
# not the rule misfiring:
#   GEO.ROOM_OVERLAP          16/300 (5.3% of plans) - every one is a small
#                                     room polygon duplicated *inside* a larger
#                                     one (plan 3467: a 1.57 m^2 Storage wholly
#                                     inside a 59.18 m^2 Living).
#   GEO.UNREACHABLE_ROOM       8/300 (2.7%) - all 8 are a bedroom or bathroom
#                                     with no door edge at all. Was 31/300
#                                     (10.3%) before the open-threshold tier.
#   GEO.ROOM_OUTSIDE_ENVELOPE  3/300 (1.0%) - balconies not enclosed by the
#                                     wall graph, so they form no face.
#   NBC.WC_OPENS_INTO_KITCHEN  5/300 (1.7%) - real source topology, 3 plans.
#   GEO.ROOM_DEGENERATE        1/300 (0.3%).
# Budgets are set ~2x the measured rate so `resplan.py` can keep evolving
# without this test becoming a tripwire on someone else's module.
FP_BUDGET = {
    "GEO.ROOM_OVERLAP": 0.10,
    "GEO.UNREACHABLE_ROOM": 0.06,
    "GEO.ROOM_OUTSIDE_ENVELOPE": 0.03,
    "GEO.ROOM_DEGENERATE": 0.02,
    "NBC.WC_OPENS_INTO_KITCHEN": 0.04,
}
FP_BUDGET_DEFAULT = 0.005


def test_false_positive_rate_on_real_plans():
    plans = _resplan(200)
    assert len(plans) >= 200
    per_rule, _fam, lat = _fp_scan(plans)
    n = len(plans)
    for rid in sorted(SCALE_FREE):
        rate = per_rule.get(rid, 0) / n
        budget = FP_BUDGET.get(rid, FP_BUDGET_DEFAULT)
        assert rate <= budget, (f"{rid} errors on {100*rate:.1f}% of real plans "
                                f"(budget {100*budget:.1f}%)")
    assert statistics.median(lat) < 10.0, f"median latency {statistics.median(lat):.1f} ms"


def test_room_overlap_findings_are_real_2d_regions_not_shared_edges():
    """The overlap rule must never fire on the wall centreline rooms share.

    ResPlan rooms tile `inner` and touch *along* centrelines, so a naive
    intersection test flags every adjacent pair. The invariant asserted here is
    converter-independent: each reported overlap must be a genuine 2-D region,
    i.e. its own inscribed clear width is >= 100 mm, not a boundary sliver.
    Audited separately: on the corpus as converted, every hit is a small room
    polygon duplicated *inside* a larger one (e.g. plan 3467, a 1.57 m^2 Storage
    wholly inside a 59.18 m^2 Living).
    """
    plans = _resplan(200)
    checked = 0
    for pl in plans:
        polys = {r.id: room_polygon(r) for r in pl.rooms}
        for f in validate(pl, {}):
            if f.rule_id != "GEO.ROOM_OVERLAP":
                continue
            a, b = f.element_ids
            inter = polys[a].intersection(polys[b])
            w = max((clear_width_mm(g) for g in
                     (inter.geoms if hasattr(inter, "geoms") else [inter])
                     if g.geom_type == "Polygon"), default=0.0)
            assert w >= 100.0, (
                f"plan {pl.id}: overlap of {a}/{b} is a {w:.0f} mm sliver; "
                f"the rule is firing on a shared edge")
            checked += 1
    assert checked > 0, "no overlaps found at all - tolerances may be too loose"


def test_short_walls_never_error():
    """A 60-97 mm snap artefact must not reject an otherwise legal plan."""
    for pl in _resplan(60):
        for f in validate(pl, {}):
            if f.rule_id == "GEO.WALL_TOO_SHORT":
                assert f.severity == "warn"


def test_latency_budget_on_a_ten_room_plan():
    plans = [p for p in _resplan(120) if 8 <= len(p.rooms) <= 12][:30]
    assert plans, "no 8-12 room plans found"
    lat = []
    for pl in plans:
        validate(pl, {"vastu": True})            # warm
        t0 = time.perf_counter()
        validate(pl, {"vastu": True})
        lat.append((time.perf_counter() - t0) * 1000.0)
    med = statistics.median(lat)
    assert med < 10.0, f"median {med:.2f} ms over {len(lat)} plans"


# ==================================================================== report ==

def _main(n: int = 250) -> None:
    from fpeval.resplan import convert
    print("=== synthetic negative control ===")
    r = report(clean_plan(), CLEAN_BRIEF, BENGALURU)
    print(f"  errors={r['n_error']} warns={r['n_warn']} "
          f"vastu={r['vastu_score']:.3f} hard_ok={r['hard_ok']}")
    for f in r["findings"]:
        print(f"    {f!r} {f.detail[:96]}")

    print("\n=== mutation detection (synthetic, exact dimensions) ===")
    for name, fn in SYNTH_MUTATIONS:
        bad, exp = fn(clean_plan())
        got = {f.rule_id for f in validate(bad, CLEAN_BRIEF, BENGALURU)}
        miss = [e for e in exp if e not in got]
        print(f"  {name:28s} expect={','.join(exp):46s} "
              f"{'OK' if not miss else 'MISS ' + str(miss)}")

    plans = []
    raws = pickle.load(open(RESPLAN_PKL, "rb"))
    conv_fail = 0
    for raw in raws:
        if len(plans) >= n:
            break
        try:
            plans.append(convert(raw))
        except Exception:
            conv_fail += 1
    print(f"\n=== mutation detection (ResPlan, {len(plans)} plans, "
          f"{conv_fail} conversion failures) ===")
    for name, fn in TOPOLOGY_MUTATIONS:
        applied = hit = 0
        for pl in plans[:60]:
            try:
                bad, exp = fn(pl)
            except mutate.MutationNotApplicable:
                continue
            applied += 1
            got = {f.rule_id for f in validate(bad, {"vastu": True})}
            hit += all(e in got for e in exp)
        rate = 100 * hit / max(applied, 1)
        print(f"  {name:28s} applicable={applied:3d}  detected={hit:3d} ({rate:.0f}%)")

    per_rule, fam, lat = _fp_scan(plans)
    N = len(plans)
    print(f"\n=== false positives on {N} real ResPlan plans (errors only) ===")
    print("  scale-free rules (these ARE false positives):")
    for rid in sorted(SCALE_FREE):
        c = per_rule.get(rid, 0)
        print(f"    {rid:34s} {c:4d} plan-hits  {100*c/N:6.2f}%")
    print("  scale-DEPENDENT rules (NOT assertable on ResPlan, listed for scale):")
    for rid in sorted(k for k in per_rule if k not in SCALE_FREE):
        c = per_rule[rid]
        print(f"    {rid:34s} {c:4d} plan-hits  {100*c/N:6.2f}%")
    print("  per-family share of plans with >=1 error:")
    for k in sorted(fam):
        print(f"    {k:8s} {100*fam[k]/N:6.2f}%")

    rooms = statistics.median([len(p.rooms) for p in plans])
    print(f"\n=== latency ({N} plans, median {rooms:.0f} rooms) ===")
    lat.sort()
    print(f"  p50={statistics.median(lat):.2f} ms  p90={lat[int(.9*len(lat))]:.2f} ms "
          f"  p99={lat[int(.99*len(lat))]:.2f} ms  max={lat[-1]:.2f} ms")


if __name__ == "__main__":
    _main(int(sys.argv[1]) if len(sys.argv) > 1 else 250)
