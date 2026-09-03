"""Furnishing tests: catalogue fidelity, placement invariants, robustness.

The failure modes worth testing are not "does it throw". They are geometric and
they are the ones a layperson spots in the editor: a sofa through a wall, a
wardrobe across the only window, a bed you cannot walk round, a `catalogId` the
editor draws as nothing. So every check here re-derives the answer from the
`Plan` alone (`furnish.audit`), never from the solver's own bookkeeping.

Two corpora, for two different reasons:
  * `solver.solve_layout` output is the real target - clean axis-aligned rooms
    with doors and windows the solver chose. Absolute sizes are metric and
    testable.
  * ResPlan is the robustness corpus - 17,000 real messy tilings, frequently
    non-rectangular, and scale-ambiguous by +/-30% (DECISIONS.md #4), so it can
    only test topology-invariant properties: containment, no overlap, doors
    clear. Never absolute areas.

Run standalone for the full report:
    uv run --with shapely --with numpy --with ortools python tests/test_furnish.py
or under pytest (corpus size from FURNISH_N, default 200):
    uv run --with shapely --with numpy --with ortools --with pytest \\
        pytest tests/test_furnish.py -q
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import pickle
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fpeval import catalog, roomtypes                             # noqa: E402
from fpeval.bylaws import BENGALURU                               # noqa: E402
from fpeval.envelope import CityProfileAdapter, bhk_programme     # noqa: E402
from fpeval.furnish import (                                      # noqa: E402
    RULES, WINDOW_TALL_MARGIN, _openings_for, _poly, audit, build_ctx,
    facing, footprint, furnish, required_keys, resolve_policy,
    rot_for_normal, wall_mass, zone_score)
from fpeval.ir import Opening, P, Plan, Room, Site, Wall          # noqa: E402
from fpeval.project import from_project, to_project              # noqa: E402
from fpeval.render import render                                  # noqa: E402
from fpeval.resplan import convert                               # noqa: E402
from fpeval.solver import LayoutSpec, solve_layout               # noqa: E402

PROF = CityProfileAdapter(BENGALURU)
PKL_CANDIDATES = [Path(os.environ.get("RESPLAN_PKL", "")) if
                  os.environ.get("RESPLAN_PKL") else None,
                  ROOT / "data" / "ResPlan.pkl",
                  Path("/tmp/resplan/data/ResPlan.pkl")]
PKL = next((p for p in PKL_CANDIDATES if p and p.exists()), None)
N_CORPUS = int(os.environ.get("FURNISH_N", "200"))
OUT = ROOT / "out" / "furnish"
NS = "{http://www.w3.org/2000/svg}"

# `render.py` is owned by the renderer agent and does NOT draw furniture (no
# reference to `plan.furniture` anywhere in it). Rather than add drawing there,
# the sample SVGs below are `render()`'s own output with a furniture layer
# appended through the transform the root element publishes.
RENDER_DRAWS_FURNITURE = "plan.furniture" in (ROOT / "src" / "fpeval"
                                              / "render.py").read_text()


# ══════════════════════════════════════════════════════════ helpers

def _digest(plan: Plan) -> str:
    return hashlib.sha256(
        json.dumps(to_project(plan), sort_keys=True).encode()).hexdigest()


def _solver_plan(w_ft: float, d_ft: float, bhk: int, facing_dir: str,
                 extras: bool = False) -> Plan:
    prog = bhk_programme(bhk, dining=extras, pooja=extras, utility=extras)
    res = solve_layout(w_ft, d_ft,
                       LayoutSpec(programme=prog, entrance_room="living",
                                  time_limit_s=30.0),
                       road_facing=facing_dir, profile=PROF,
                       plan_id=f"fz-{w_ft:.0f}x{d_ft:.0f}-{bhk}{facing_dir}")
    assert res.plan is not None, f"{w_ft}x{d_ft} {bhk}BHK -> {res.status}"
    return res.plan


_SOLVER_CACHE: dict[tuple, Plan] = {}


def solver_case(key: tuple) -> Plan:
    if key not in _SOLVER_CACHE:
        _SOLVER_CACHE[key] = _solver_plan(*key)
    return _SOLVER_CACHE[key]


SOLVER_CASES = [
    (30.0, 40.0, 2, "N", False),
    (30.0, 40.0, 3, "E", False),
    (40.0, 60.0, 3, "E", True),
    (40.0, 60.0, 4, "N", True),
]


def rect_plan(w: int, d: int, *, door_at: float = 0.5, window: bool = True,
              category: str = "bedroom", name: str = "Bedroom 1",
              north_deg: float = 0.0, thickness: int = 200) -> Plan:
    """One rectangular room whose polygon tiles at wall centrelines.

    Hand-built so the expected answer is arithmetic: the usable interior is
    exactly (w - thickness) x (d - thickness).
    """
    walls = [Wall("w0", P(0, 0), P(w, 0), thickness),
             Wall("w1", P(w, 0), P(w, d), thickness),
             Wall("w2", P(w, d), P(0, d), thickness),
             Wall("w3", P(0, d), P(0, 0), thickness)]
    ops = [Opening("d0", "door", "w0", door_at, 900, 0, 2100)]
    if window:
        ops.append(Opening("wi0", "window", "w2", 0.5, 1200, 900, 2100))
    room = Room("r0", name, category, ["w0", "w1", "w2", "w3"],
                [P(0, 0), P(w, 0), P(w, d), P(0, d)], w * d)
    return Plan(id=f"rect-{w}x{d}", walls=walls, openings=ops, rooms=[room],
                site=Site(plot_polygon=[P(0, 0), P(w, 0), P(w, d), P(0, d)],
                          north_deg=north_deg))


def item_of(plan: Plan, spec_key: str):
    return next((f for f in plan.furniture if f.id.endswith("-" + spec_key)), None)


def fp_of(f) -> Polygon:
    w, d, _ = (f.width, f.depth, f.height)
    if not (w and d):
        w, d, _ = catalog.dims(f.catalog_id)
    return footprint(f.position.x, f.position.y, w, d, f.rotation)


# ══════════════════════════════════════════════════════════ 1. catalogue

def test_catalogue_parses_the_real_ts():
    s = catalog.summary()
    assert s["n_items"] == 189, s          # the count the fork actually ships
    assert s["n_entourage"] == 12, s
    assert s["n_symbols"] == 13, s         # `symbol: true` -> 2D glyph only
    assert s["n_categories"] == 19, s


def test_dimensions_are_millimetres():
    sofa = catalog.get("sofa")
    assert (sofa.width, sofa.depth, sofa.height) == (2000, 900, 800)
    assert (sofa.width_cm, sofa.depth_cm) == (200.0, 90.0)
    # 600 platform depth: the one dimension an Indian kitchen rule leans on
    assert catalog.get("counter").depth == 600
    assert catalog.get("toilet").depth == 650


def test_missing_id_raises_loudly():
    try:
        catalog.get("squat_wc_indian")
    except catalog.CatalogError as e:
        assert "not in furnitureCatalog.ts" in str(e)
    else:
        raise AssertionError("a bad catalog id must raise, not return None")


def test_every_rule_id_exists_and_substitutions_resolve():
    ids = sorted({s.catalog_id for v in RULES.values() for s in v})
    catalog.require(ids, "RULES")
    for logical, (cid, why) in catalog.SUBSTITUTIONS.items():
        assert why, logical
        if cid:
            assert catalog.has(cid), (logical, cid)
        else:                              # recorded MISSING must raise, not lie
            try:
                catalog.resolve(logical)
            except catalog.CatalogError:
                pass
            else:
                raise AssertionError(f"{logical} is MISSING but resolved")


def test_dim_overrides_fix_the_swapped_bed():
    it = catalog.get("bed_queen")
    assert (it.width, it.depth) == (2000, 1500)        # the fork's swapped axes
    assert catalog.dims("bed_queen") == (1500, 2000, 500)


def test_cache_round_trips_and_matches_a_fresh_parse():
    fresh = catalog.build(write_cache=False)
    assert len(fresh["furniture"]) == 189
    ids = [d["id"] for d in fresh["furniture"]]
    assert ids == list(catalog.items())               # order preserved from TS


# ══════════════════════════════════════════════════════════ 2. orientation math

def test_facing_matches_the_ir_contract():
    assert facing(0.0) == (0.0, 1.0)                  # ir.py: 0 = facing +Y
    for rot, exp in ((90.0, (-1.0, 0.0)), (180.0, (0.0, -1.0)), (270.0, (1.0, 0.0))):
        got = facing(rot)
        assert abs(got[0] - exp[0]) < 1e-9 and abs(got[1] - exp[1]) < 1e-9, rot


def test_rot_for_normal_is_the_inverse_of_facing():
    for n, exp in (((0.0, 1.0), 0.0), ((-1.0, 0.0), 90.0),
                   ((0.0, -1.0), 180.0), ((1.0, 0.0), 270.0)):
        assert rot_for_normal(*n) == exp, n
    # a diagonal ResPlan wall must still come out exact
    r = rot_for_normal(-0.7071067811865476, 0.7071067811865476)
    assert abs(r - 45.0) < 1e-6


def test_footprint_swaps_extents_at_90_degrees():
    a = footprint(0, 0, 2000, 900, 0.0).bounds
    b = footprint(0, 0, 2000, 900, 90.0).bounds
    assert (round(a[2] - a[0]), round(a[3] - a[1])) == (2000, 900)
    assert (round(b[2] - b[0]), round(b[3] - b[1])) == (900, 2000)


def test_zone_score_is_continuous():
    assert zone_score(135.0, ("SE",), ()) == 1.0                  # dead on SE
    assert 0.4 < zone_score(180.0, ("SE",), ()) < 0.6             # 45 deg off
    assert zone_score(45.0, ("SE",), ()) == 0.0                   # 90 deg off
    assert zone_score(45.0, (), ("NE",)) == 0.0                   # avoided


# ══════════════════════════════════════════════════════════ 3. single-room truth

def test_bed_headboard_touches_the_wall():
    plan = rect_plan(3600, 4200)
    out, rep = furnish(plan)
    bed = item_of(out, "bed")
    assert bed is not None, rep.text()
    fx, fy = facing(bed.rotation)
    back = (bed.position.x - fx * bed.depth / 2.0,
            bed.position.y - fy * bed.depth / 2.0)
    mass = wall_mass(plan)
    # the back plane sits on a wall face, i.e. within a millimetre of the mass
    assert mass.distance(Point(back)) < 1.5, (bed, back)
    assert bed.rotation in (0.0, 90.0, 180.0, 270.0)


def test_sofa_faces_into_the_room():
    plan = rect_plan(4600, 5200, category="living", name="Living")
    out, rep = furnish(plan)
    sofa = item_of(out, "sofa")
    assert sofa is not None, rep.text()
    fx, fy = facing(sofa.rotation)
    c = _poly(plan.rooms[0].polygon).centroid
    v = (c.x - sofa.position.x, c.y - sofa.position.y)
    n = math.hypot(*v) or 1.0
    assert (v[0] * fx + v[1] * fy) / n > 0.3, "sofa must look inward"


def test_beside_items_share_the_wall_plane():
    """A nightstand must be flush with the same wall as the headboard.

    Regression: the back-plane offset had its sign inverted, which put both
    nightstands 1600 mm off the wall, level with the foot of the bed. Every
    invariant test still passed - only looking at the drawing found it.
    """
    plan = rect_plan(4000, 4600)
    out, rep = furnish(plan)
    bed = item_of(out, "bed")
    assert bed is not None, rep.text()
    fx, fy = facing(bed.rotation)
    bed_back = (bed.position.x - fx * bed.depth / 2.0,
                bed.position.y - fy * bed.depth / 2.0)
    mass = wall_mass(plan)
    n = 0
    for key in ("nightstand_a", "nightstand_b"):
        ns = item_of(out, key)
        if ns is None:
            continue
        n += 1
        assert ns.rotation == bed.rotation, (key, ns.rotation, bed.rotation)
        nb = (ns.position.x - fx * ns.depth / 2.0,
              ns.position.y - fy * ns.depth / 2.0)
        # coplanar with the headboard: zero component along the facing axis
        along = (nb[0] - bed_back[0]) * fx + (nb[1] - bed_back[1]) * fy
        assert abs(along) < 2.0, (key, along)
        assert mass.distance(Point(nb)) < 2.0, (key, nb)
        lateral = abs((ns.position.x - bed.position.x) * fy -
                      (ns.position.y - bed.position.y) * fx)
        assert abs(lateral - (bed.width + ns.width) / 2.0 - 50) < 2.0, \
            (key, lateral)
    assert n >= 1, "a 4.0x4.6 m bedroom must take at least one nightstand"


def test_nothing_lands_in_the_door_swing():
    # door dead centre of the 3 m wall: the bed must slide off it, not sit on it
    plan = rect_plan(3200, 4400, door_at=0.5)
    out, rep = furnish(plan)
    a = audit(out)
    assert a["violations"]["door_block"] == [], rep.text()
    assert len(out.furniture) >= 1, rep.text()


def test_tall_items_stay_off_windows():
    plan = rect_plan(3600, 4200)
    out, _ = furnish(plan)
    walls = {w.id: w for w in plan.walls}
    _, wins = _openings_for(plan, plan.rooms[0], _poly(plan.rooms[0].polygon), walls)
    assert wins, "fixture must have a window"
    for f in out.furniture:
        h = f.height or catalog.dims(f.catalog_id)[2]
        for wn in wins:
            if h > wn.sill + WINDOW_TALL_MARGIN:
                assert fp_of(f).intersection(wn.keepout).area <= 1e4, f


def test_wc_does_not_face_the_door():
    plan = rect_plan(2000, 2600, category="bathroom", name="Bathroom 1")
    out, rep = furnish(plan)
    wc = item_of(out, "wc")
    assert wc is not None, rep.text()
    fx, fy = facing(wc.rotation)
    walls = {w.id: w for w in plan.walls}
    doors, _ = _openings_for(plan, plan.rooms[0], _poly(plan.rooms[0].polygon), walls)
    for d in doors:
        v = (d.x - wc.position.x, d.y - wc.position.y)
        n = math.hypot(*v) or 1.0
        if n <= 2500.0:
            assert (v[0] * fx + v[1] * fy) / n <= math.cos(math.radians(45.0)), \
                "WC is aimed at the door"


def test_kitchen_keeps_fire_and_water_apart():
    plan = rect_plan(3000, 4000, category="kitchen", name="Kitchen")
    out, rep = furnish(plan)
    hob, sink = item_of(out, "hob"), item_of(out, "sink")
    assert hob is not None and sink is not None, rep.text()
    gap = fp_of(hob).distance(fp_of(sink))
    assert gap >= 600.0 - 1.0, f"hob/sink gap {gap:.0f} mm < 600"


def test_kitchen_run_follows_walls_and_tiles_the_leftovers():
    plan = rect_plan(3400, 4200, category="kitchen", name="Kitchen")
    out, rep = furnish(plan)
    tiles = [f for f in out.furniture if f.catalog_id == "counter"]
    assert len(tiles) >= 2, rep.text()
    mass = wall_mass(plan)
    for t in tiles:
        assert t.depth == 600, t                       # the Indian platform line
        fx, fy = facing(t.rotation)
        back = (t.position.x - fx * t.depth / 2.0, t.position.y - fy * t.depth / 2.0)
        assert mass.distance(Point(back)) < 1.5, t
    # an L run means at least two distinct rotations
    assert len({f.rotation for f in tiles}) >= 2, [f.rotation for f in tiles]
    # and the run must be a real length of platform, not one token tile
    assert sum(t.width for t in tiles) >= 1500


def test_hob_prefers_the_agni_corner():
    """north_deg=0 means +Y is north, so SE is (+x, -y) from the room centre."""
    plan = rect_plan(3600, 4400, category="kitchen", name="Kitchen",
                     north_deg=0.0, window=False)
    out, rep = furnish(plan)
    hob, sink = item_of(out, "hob"), item_of(out, "sink")
    assert hob is not None and sink is not None, rep.text()
    c = _poly(plan.rooms[0].polygon).centroid
    # the hob must be south of, or level with, the sink and no further west
    assert hob.position.y <= sink.position.y + 1, (hob.position, sink.position)
    assert zone_score(
        math.degrees(math.atan2(hob.position.x - c.x, hob.position.y - c.y)) % 360,
        ("SE",), ("NE", "SW")) >= 0.5


def test_optional_items_are_dropped_not_overlapped():
    small = rect_plan(2700, 3300)          # a bed fits; a wardrobe run does not
    out, rep = furnish(small)
    assert audit(out)["n_violations"] == 0, rep.text()
    assert item_of(out, "bed") is not None
    assert rep.drops, "a 2.7x3.3 m bedroom cannot take every optional item"
    for d in rep.drops:
        assert d.reason, d                 # every drop must say why


def test_input_plan_is_not_mutated():
    plan = rect_plan(3600, 4200)
    before = json.dumps(to_project(plan), sort_keys=True)
    out, _ = furnish(plan)
    assert plan.furniture == []
    assert json.dumps(to_project(plan), sort_keys=True) == before
    assert out is not plan and out.furniture


def test_locked_furniture_survives_and_blocks():
    from fpeval.ir import Furniture
    plan = rect_plan(3600, 4200)
    # against the left wall, clear of the w2 window the fixture puts at 0.5
    plan.furniture = [Furniture(id="user-1", catalog_id="wardrobe",
                                position=P(400, 2100), rotation=270.0,
                                width=1200, depth=600, height=2000,
                                room_id="r0", locked=True)]
    out, rep = furnish(plan)
    assert any(f.id == "user-1" and f.locked for f in out.furniture)
    assert audit(out)["n_violations"] == 0, rep.text()
    lock_fp = fp_of(plan.furniture[0])
    for f in out.furniture:
        if f.id != "user-1":
            assert lock_fp.intersection(fp_of(f)).area <= 1e4, f


# ══════════════════════════════════════════════════════════ 4. policy layering

def test_policy_merge_is_diffable():
    pol = resolve_policy({
        "density": "dense",
        "kitchen": {"counter_run": "U"},
        "swaps": {"bed_queen": "bed_twin"},
        "rooms": {"bedroom": {"remove": ["dresser"],
                              "items": {"wardrobe": {"optional": False}},
                              "add": [{"key": "rug", "item": "rug",
                                       "anchor": "center", "optional": True}]}},
    })
    d = list(pol.diff)
    assert "kitchen.counter_run: L -> U" in d, d
    assert "density: normal -> dense" in d, d
    assert "swap bed_queen -> bed_twin" in d, d
    assert "bedroom: -dresser" in d, d
    assert "bedroom: +rug (rug)" in d, d
    assert "bedroom.wardrobe.optional: True -> False" in d, d
    assert pol.kitchen["counter_run"] == "U"
    assert not [s for s in pol.specs["bedroom"] if s.key == "dresser"]
    assert [s for s in pol.specs["bedroom"] if s.item == "bed_twin"]
    assert resolve_policy(None).diff == ()


def test_policy_rejects_nonsense_loudly():
    for bad in ({"denisty": "dense"},
                {"density": "very"},
                {"kitchen": {"counter_run": "Z"}},
                {"kitchen": {"nope": 1}},
                {"rooms": {"bedrom": {}}},
                {"rooms": {"bedroom": {"remove": ["nosuchkey"]}}},
                {"rooms": {"bedroom": {"items": {"bed": {"nofield": 1}}}}},
                {"swaps": {"bed_queen": "bed_emperor"}}):
        try:
            resolve_policy(bad)
        except (ValueError, catalog.CatalogError):
            continue
        raise AssertionError(f"policy {bad} should have been refused")


def test_density_changes_item_count_monotonically():
    plan = solver_case(SOLVER_CASES[2])
    counts = []
    for dens in ("sparse", "normal", "dense"):
        out, _ = furnish(plan, {"density": dens})
        counts.append(len(out.furniture))
    assert counts[0] <= counts[1] <= counts[2], counts
    assert counts[0] < counts[2], counts


def test_counter_run_policy_changes_the_kitchen():
    plan = solver_case(SOLVER_CASES[2])
    outs = {}
    for shape in ("I", "L", "U"):
        out, _ = furnish(plan, {"kitchen": {"counter_run": shape}})
        tiles = [f for f in out.furniture
                 if f.catalog_id == "counter" and f.room_id == "kitchen"]
        outs[shape] = (len({t.rotation for t in tiles}), sum(t.width for t in tiles))
    assert outs["I"][0] == 1, outs
    assert outs["L"][0] >= 2, outs
    assert outs["U"][1] >= outs["I"][1], outs


# ══════════════════════════════════════════════════════════ 5. solver corpus

def test_solver_plans_have_zero_geometric_violations():
    for key in SOLVER_CASES:
        plan = solver_case(key)
        out, rep = furnish(plan)
        a = audit(out)
        assert a["violations"]["outside_room"] == [], (key, a)
        assert a["violations"]["wall_overlap"] == [], (key, a)
        assert a["violations"]["item_overlap"] == [], (key, a)
        assert a["violations"]["door_block"] == [], (key, a)
        assert a["violations"]["window_block"] == [], (key, a)
        assert a["violations"]["unknown_catalog"] == [], (key, a)
        assert a["violations"]["no_room"] == [], (key, a)
        assert len(out.furniture) >= 15, (key, len(out.furniture))


def test_solver_plans_get_their_required_items():
    misses = []
    for key in SOLVER_CASES:
        plan = solver_case(key)
        out, rep = furnish(plan)
        for r in rep.rooms:
            rt = roomtypes.get(r.room_key)
            fkey = rt.furnish_key if rt else None
            if not fkey:
                continue
            for k in required_keys(fkey):
                spec = next(s for s in RULES[fkey] if s.key == k)
                if r.clear_m2 < spec.min_room_m2:
                    continue               # legitimately too small; drop is right
                if k not in r.placed:
                    misses.append((key, r.name, r.room_key, k, r.clear_m2))
    assert not misses, misses


def test_solver_plans_survive_project_json():
    for key in SOLVER_CASES:
        out, _ = furnish(solver_case(key))
        proj = to_project(out)
        blob = json.dumps(proj)
        back = from_project(json.loads(blob))
        assert len(back.furniture) == len(out.furniture)
        for a, b in zip(out.furniture, back.furniture):
            assert (a.id, a.catalog_id, a.position, round(a.rotation, 6),
                    a.width, a.depth, a.height, a.room_id) == \
                   (b.id, b.catalog_id, b.position, round(b.rotation, 6),
                    b.width, b.depth, b.height, b.room_id)
        for f in proj["floors"][0]["furniture"]:
            assert catalog.has(f["catalogId"]), f["catalogId"]


def test_circulation_survives_furnishing():
    """Each door must still reach one shared 600 mm walkable component."""
    bad = []
    for key in SOLVER_CASES:
        plan = solver_case(key)
        out, _ = furnish(plan)
        walls = {w.id: w for w in plan.walls}
        mass = wall_mass(plan)
        for room in plan.rooms:
            ctx = build_ctx(plan, room, mass, walls)
            if ctx is None or not ctx.doors or not ctx.circ_baseline_ok:
                continue
            fps = [fp_of(f) for f in out.furniture if f.room_id == room.id]
            if not fps:
                continue
            free = ctx.clear.difference(unary_union(fps))
            er = free.buffer(-ctx.circ_mm / 2.0, join_style=2, mitre_limit=2.0)
            comps = ([er] if isinstance(er, Polygon) else list(er.geoms))
            comps = [g for g in comps if g.area > 0]
            hit = set()
            for d in ctx.doors:
                near = [i for i, g in enumerate(comps)
                        if g.distance(Point(d.approach)) <= ctx.circ_mm / 2.0 + 120]
                if not near:
                    bad.append((key, room.id, d.op_id, "unreachable"))
                else:
                    hit.add(near[0])
            if len(hit) > 1:
                bad.append((key, room.id, "doors in %d components" % len(hit), ""))
    assert not bad, bad


# ══════════════════════════════════════════════════════════ 6. ResPlan corpus

def _corpus_run(n: int, verbose: bool = False) -> dict:
    assert PKL is not None, "ResPlan.pkl not on disk"
    plans = pickle.load(open(PKL, "rb"))
    viol: dict[str, int] = {}
    per_key: dict[str, dict[str, int]] = {}
    drop_reasons: dict[str, int] = {}
    lat: list[float] = []
    n_items = n_rooms = n_skipped = 0
    req_ok = req_total = 0
    opt_placed = opt_total = 0
    convert_fail = furnish_fail = 0
    for i in range(min(n, len(plans))):
        try:
            plan = convert(plans[i])
        except Exception:
            convert_fail += 1
            continue
        try:
            t0 = time.perf_counter()
            out, rep = furnish(plan)
            lat.append((time.perf_counter() - t0) * 1000.0)
        except Exception:                                         # noqa: BLE001
            furnish_fail += 1
            if furnish_fail <= 3 and verbose:
                import traceback
                traceback.print_exc()
            continue
        a = audit(out)
        for k, v in a["violations"].items():
            if v:
                viol[k] = viol.get(k, 0) + len(v)
        n_items += len(out.furniture)
        n_rooms += len(rep.rooms)
        n_skipped += len(rep.skipped)
        for k, c in rep.drop_reasons().items():
            drop_reasons[k] = drop_reasons.get(k, 0) + c
        for p in rep.placements:
            per_key.setdefault(p.room_key, {})
            per_key[p.room_key][p.catalog_id] = \
                per_key[p.room_key].get(p.catalog_id, 0) + 1
        for r in rep.rooms:
            rt = roomtypes.get(r.room_key)
            fkey = rt.furnish_key if rt else None
            if not fkey:
                continue
            for s in RULES[fkey]:
                if r.clear_m2 < s.min_room_m2:
                    continue
                if s.optional:
                    opt_total += 1
                    opt_placed += s.key in r.placed
                else:
                    req_total += 1
                    req_ok += s.key in r.placed
    lat.sort()
    return {
        "n_plans": len(lat), "convert_fail": convert_fail,
        "furnish_fail": furnish_fail, "n_rooms": n_rooms, "n_items": n_items,
        "n_skipped_rooms": n_skipped, "violations": viol,
        "required_rate": (req_ok / req_total) if req_total else 1.0,
        "required_n": req_total,
        "optional_rate": (opt_placed / opt_total) if opt_total else 1.0,
        "optional_n": opt_total,
        "p50": lat[len(lat) // 2] if lat else 0.0,
        "p90": lat[int(0.90 * (len(lat) - 1))] if lat else 0.0,
        "p99": lat[int(0.99 * (len(lat) - 1))] if lat else 0.0,
        "max": lat[-1] if lat else 0.0,
        "drop_reasons": dict(sorted(drop_reasons.items(),
                                    key=lambda kv: (-kv[1], kv[0]))[:12]),
        "per_key": per_key,
    }


def test_resplan_corpus():
    if PKL is None:
        try:
            import pytest
            pytest.skip("ResPlan.pkl not on disk")
        except ImportError:
            return
    st = _corpus_run(N_CORPUS)
    assert st["n_plans"] >= min(N_CORPUS, 200) * 0.98, st
    assert st["furnish_fail"] == 0, st
    assert st["violations"] == {}, st["violations"]
    assert st["n_items"] / max(1, st["n_plans"]) > 8, st
    # a required item is only excused by the size gate, so the rate must be high
    assert st["required_rate"] > 0.93, (st["required_rate"], st["required_n"])


# ══════════════════════════════════════════════════════════ 7. determinism

_CHILD = r"""
import hashlib, json, pickle, sys
sys.path.insert(0, %(src)r)
from fpeval.furnish import furnish
from fpeval.project import to_project
from fpeval.resplan import convert
plans = pickle.load(open(%(pkl)r, "rb"))
h = hashlib.sha256()
for i in %(idx)r:
    out, rep = furnish(convert(plans[i]))
    h.update(json.dumps(to_project(out), sort_keys=True).encode())
    h.update(rep.fingerprint().encode())
print(h.hexdigest())
"""


def test_determinism_same_process():
    for key in SOLVER_CASES[:2]:
        plan = solver_case(key)
        a, ra = furnish(plan)
        b, rb = furnish(plan)
        assert _digest(a) == _digest(b), key
        assert ra.fingerprint() == rb.fingerprint(), key
    plan = rect_plan(3600, 4200)
    assert _digest(furnish(plan)[0]) == _digest(furnish(plan, None, seed=7)[0]), \
        "no RNG, so the seed must not move anything"


def cross_process_digests(n: int = 24) -> list[str]:
    """Same corpus, three PYTHONHASHSEEDs, three fresh interpreters."""
    idx = list(range(n))
    src = _CHILD % {"src": str(ROOT / "src"), "pkl": str(PKL), "idx": idx}
    outs = []
    for hs in ("0", "1", "12345"):
        env = dict(os.environ, PYTHONHASHSEED=hs)
        r = subprocess.run([sys.executable, "-c", src], capture_output=True,
                           text=True, env=env, cwd=str(ROOT))
        assert r.returncode == 0, r.stderr[-2000:]
        outs.append(r.stdout.strip())
    return outs


def test_determinism_across_processes():
    if PKL is None:
        try:
            import pytest
            pytest.skip("ResPlan.pkl not on disk")
        except ImportError:
            return
    outs = cross_process_digests()
    assert len(set(outs)) == 1, outs


# ══════════════════════════════════════════════════════════ 8. sample SVGs

def _overlay(svg: str, plan: Plan) -> str:
    """Append a furniture layer to `render()`'s SVG using its own transform.

    `render.py` does not draw furniture and is owned by another agent, so the
    layer is composed here instead of being added there.
    """
    root = ET.fromstring(svg)
    sc = float(root.get("data-scale"))
    ox, oy = float(root.get("data-ox")), float(root.get("data-oy"))
    wx, wy = float(root.get("data-world-x")), float(root.get("data-world-y"))
    maxy = float(root.get("data-world-maxy"))
    flip = root.get("data-flip") == "1"

    def t(x, y):
        return (ox + (x - wx) / sc, oy + ((maxy - y) if flip else (y - wy)) / sc)

    parts = ['<g class="furniture-overlay" fill-opacity="0.55" stroke="#334155" '
             'stroke-width="0.35">']
    for f in plan.furniture:
        it = catalog.get(f.catalog_id)
        pts = " ".join("%.2f,%.2f" % t(x, y)
                       for x, y in fp_of(f).exterior.coords[:-1])
        fx, fy = facing(f.rotation)
        hx, hy = t(f.position.x, f.position.y)
        tx, ty = t(f.position.x + fx * 260, f.position.y + fy * 260)
        parts.append(f'<polygon points="{pts}" fill="{it.color}"/>')
        parts.append(f'<line x1="{hx:.2f}" y1="{hy:.2f}" x2="{tx:.2f}" '
                     f'y2="{ty:.2f}" stroke="#0f172a" stroke-width="0.3"/>')
    parts.append("</g>")
    return svg.replace("</svg>", "".join(parts) + "</svg>")


def write_samples() -> list[Path]:
    OUT.mkdir(parents=True, exist_ok=True)
    written = []
    for key in SOLVER_CASES:
        plan = solver_case(key)
        out, rep = furnish(plan)
        svg = _overlay(render(out, "presentation"), out)
        ET.fromstring(svg)                              # must stay well-formed
        p = OUT / f"solver-{key[0]:.0f}x{key[1]:.0f}-{key[2]}bhk-{key[3]}.svg"
        p.write_text(svg)
        (OUT / (p.stem + ".report.json")).write_text(
            json.dumps(rep.to_dict(), indent=1))
        written.append(p)
    if PKL is not None:
        plans = pickle.load(open(PKL, "rb"))
        for i in (0, 3, 11):
            out, rep = furnish(convert(plans[i]))
            svg = _overlay(render(out, "presentation"), out)
            ET.fromstring(svg)
            p = OUT / f"resplan-{i:05d}.svg"
            p.write_text(svg)
            written.append(p)
    return written


def test_writes_sample_svgs():
    assert write_samples()


# ══════════════════════════════════════════════════════════ standalone report

def main() -> int:
    fails: list[str] = []
    print("=" * 78)
    print("catalogue")
    print("=" * 78)
    s = catalog.summary()
    print(f"  {s['n_items']} items, {s['n_symbols']} 2D symbols, "
          f"{s['n_categories']} categories, {s['n_entourage']} entourage defs")
    print(f"  source_hash {s['source_hash']}  cache {s['cache']}")
    print("  substitutions:")
    for k, (cid, why) in sorted(catalog.SUBSTITUTIONS.items()):
        print(f"    {k:<16} -> {cid or 'MISSING':<18} {why}")
    print("  dim overrides:")
    for k, v in sorted(catalog.DIM_OVERRIDES.items()):
        it = catalog.get(k)
        print(f"    {k:<12} {it.width}x{it.depth}x{it.height} -> "
              f"{v[0]}x{v[1]}x{v[2]}")

    unit = [fn for name, fn in sorted(globals().items())
            if name.startswith("test_") and name not in
            ("test_resplan_corpus", "test_determinism_across_processes",
             "test_solver_plans_have_zero_geometric_violations",
             "test_writes_sample_svgs")]
    print()
    print("=" * 78)
    print(f"unit + single-room + policy + solver ({len(unit)} tests)")
    print("=" * 78)
    for fn in unit:
        t0 = time.perf_counter()
        try:
            fn()
            print(f"  ok   {fn.__name__:<52}"
                  f"{(time.perf_counter()-t0)*1000:7.1f} ms")
        except Exception as e:                                    # noqa: BLE001
            print(f"  FAIL {fn.__name__:<52} {e}")
            fails.append(f"{fn.__name__}: {e}")

    print()
    print("=" * 78)
    print("solver-generated plans")
    print("=" * 78)
    print(f"  {'case':<22}{'rooms':>6}{'items':>7}{'viol':>6}{'ms':>8}  per-room")
    for key in SOLVER_CASES:
        try:
            plan = solver_case(key)
        except AssertionError as e:
            print(f"  {str(key):<22} SOLVER REFUSED: {e}")
            fails.append(f"solver {key}: {e}")
            continue
        out, rep = furnish(plan)
        a = audit(out)
        label = f"{key[0]:.0f}x{key[1]:.0f} {key[2]}BHK {key[3]}"
        print(f"  {label:<22}{len(plan.rooms):>6}{len(out.furniture):>7}"
              f"{a['n_violations']:>6}{rep.ms:>8.0f}")
        for r in rep.rooms:
            print(f"      {r.name:<17}{r.room_key:<15}{r.clear_m2:6.1f} m2  "
                  f"+{len(r.placed):<2d} -{len(r.dropped):<2d} "
                  f"{','.join(r.placed)}")
        if a["n_violations"]:
            print("      VIOLATIONS", a["violations"])
            fails.append(f"solver {key}: {a['violations']}")
        drops = {}
        for d in rep.drops:
            drops[d.reason.split("(")[0]] = drops.get(d.reason.split("(")[0], 0) + 1
        print(f"      drops: {drops}")

    print()
    print("=" * 78)
    print(f"ResPlan corpus (n={N_CORPUS})")
    print("=" * 78)
    if PKL is None:
        print("  SKIPPED: ResPlan.pkl not found")
    else:
        st = _corpus_run(N_CORPUS, verbose=True)
        print(f"  plans {st['n_plans']}  convert_fail {st['convert_fail']}  "
              f"furnish_fail {st['furnish_fail']}")
        print(f"  rooms {st['n_rooms']}  items {st['n_items']}  "
              f"({st['n_items']/max(1,st['n_plans']):.1f} per plan)  "
              f"rooms with no usable area {st['n_skipped_rooms']}")
        print(f"  VIOLATIONS {st['violations'] or 'none'} "
              f"-> {100.0*(1 - sum(st['violations'].values())/max(1,st['n_items'])):.3f}% clean")
        print(f"  required items placed {100*st['required_rate']:.2f}% "
              f"of {st['required_n']} eligible")
        print(f"  optional items placed {100*st['optional_rate']:.2f}% "
              f"of {st['optional_n']} eligible")
        print(f"  latency ms  p50 {st['p50']:.1f}  p90 {st['p90']:.1f}  "
              f"p99 {st['p99']:.1f}  max {st['max']:.1f}")
        print("  top drop reasons:")
        for k, v in st["drop_reasons"].items():
            print(f"    {v:>6}  {k}")
        print("  items per room type:")
        for rk in sorted(st["per_key"]):
            row = ", ".join(f"{k}x{v}" for k, v in
                            sorted(st["per_key"][rk].items(),
                                   key=lambda kv: (-kv[1], kv[0])))
            print(f"    {rk:<16} {row}")
        if st["violations"]:
            fails.append(f"resplan: {st['violations']}")
        if st["furnish_fail"]:
            fails.append(f"resplan: {st['furnish_fail']} exceptions")

    print()
    print("=" * 78)
    print("determinism")
    print("=" * 78)
    try:
        test_determinism_same_process()
        print("  ok   byte-identical on re-run in one process")
    except Exception as e:                                        # noqa: BLE001
        print(f"  FAIL same-process: {e}")
        fails.append(f"determinism same-process: {e}")
    if PKL is None:
        print("  SKIPPED cross-process (needs ResPlan.pkl)")
    else:
        try:
            dg = cross_process_digests()
            assert len(set(dg)) == 1, dg
            print("  ok   identical across 3 processes x PYTHONHASHSEED: "
                  f"{dg[0][:16]}")
        except Exception as e:                                    # noqa: BLE001
            print(f"  FAIL cross-process: {e}")
            fails.append(f"determinism cross-process: {e}")

    print()
    print("=" * 78)
    print("sample SVGs")
    print("=" * 78)
    print(f"  render.py draws furniture: {RENDER_DRAWS_FURNITURE} "
          f"(it does not; the overlay is composed in this test file)")
    try:
        for p in write_samples():
            print(f"  wrote {p.relative_to(ROOT)}  {p.stat().st_size/1024:.1f} kB")
    except Exception as e:                                        # noqa: BLE001
        print(f"  FAIL svg: {e}")
        fails.append(f"svg: {e}")

    print()
    print("=" * 78)
    if fails:
        print(f"FAIL ({len(fails)})")
        for f in fails:
            print("  -", f)
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
