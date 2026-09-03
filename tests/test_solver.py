"""Tests for the area-budget gate and the CP-SAT layout solver.

Runs as pytest or standalone:
    uv run --with shapely --with numpy --with ortools python tests/test_solver.py
    uv run --with shapely --with numpy --with ortools --with pytest pytest tests/test_solver.py

Envelope arithmetic is asserted against values computed by hand from
1 ft = 304.8 mm, so a regression in the conversion or the setback table shows
up as a number mismatch rather than a shrug. Layout tests assert the things
that actually matter downstream: NBC minima, an exact tiling, coverage
compliance, reachability from the front door, and that the emitted Plan still
clears the same bar the ResPlan converter clears (`to_project`, `json.dumps`,
face recovery IoU).
"""
from __future__ import annotations

import json
import math
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from shapely.geometry import Polygon, box                      # noqa: E402
from shapely.ops import unary_union                            # noqa: E402

from fpeval.envelope import (BBMPDefault, RoomReq, bhk_programme,  # noqa: E402
                             compute_envelope, default_profile, ft_to_mm,
                             mm2_to_m2, mm2_to_sqft, north_deg_for,
                             sqft_to_m2, zone_vector)
from fpeval.metrics import face_recovery, ir_identity, room_face_match  # noqa: E402
from fpeval.project import from_project, to_project            # noqa: E402
from fpeval.solver import (GRID_MM, LayoutSpec, _wall_axis_coord,  # noqa: E402
                           solve_layout)

TOL = 1e-6


# --------------------------------------------------------------------------
# 1. units and orientation
# --------------------------------------------------------------------------

def test_feet_to_mm_is_exact():
    assert ft_to_mm(1) == 305           # 304.8 rounds to 305
    assert ft_to_mm(20) == 6096         # 20 * 304.8 exactly
    assert ft_to_mm(30) == 9144
    assert ft_to_mm(40) == 12192
    assert ft_to_mm(50) == 15240
    assert ft_to_mm(60) == 18288
    assert ft_to_mm(80) == 24384
    # 600 sqft is exactly 55.741824 m^2
    assert abs(sqft_to_m2(600) - 55.741824) < 1e-9
    assert abs(mm2_to_sqft(6096 * 9144) - 600.0) < 1e-6


def test_road_facing_sets_north_bearing():
    # plot depth runs along +Y with the road at y=0, so the front edge faces
    # -Y and bearing(+Y) = road bearing + 180
    assert north_deg_for("N") == 180.0
    assert north_deg_for("S") == 0.0
    assert north_deg_for("E") == 270.0
    assert north_deg_for("W") == 90.0
    assert north_deg_for("N", north_deg=37.0) == 37.0


def test_zone_vectors_are_unit_and_correctly_rotated():
    for zone in ("N", "NE", "E", "SE", "S", "SW", "W", "NW"):
        vx, vy = zone_vector(zone, 0.0)
        assert abs(math.hypot(vx, vy) - 1.0) < 1e-9
    # north_deg = 0 -> +Y is north, +X is east
    assert zone_vector("N", 0.0)[1] > 0.999
    assert zone_vector("E", 0.0)[0] > 0.999
    # road facing north (north_deg=180) -> north is -Y, i.e. toward the road
    assert zone_vector("N", 180.0)[1] < -0.999
    # kitchen SE on a north-facing plot must sit toward -X/-Y quadrant... check
    # it is at least 45 degrees from due north
    vx, vy = zone_vector("SE", 180.0)
    assert vy > 0.7 and vx < -0.7


# --------------------------------------------------------------------------
# 2. envelope arithmetic, against hand-computed values
# --------------------------------------------------------------------------

def _env(w, d, **kw):
    return compute_envelope(w, d, road_facing="N", profile=BBMPDefault(), **kw)


def test_envelope_20x30():
    """600 sqft. BBMP <=1200 band: front 900, rear 700, one side 700."""
    st = _env(20, 30)
    assert st.plot_area_mm2 == 6096 * 9144 == 55_741_824
    assert abs(st.plot_area_sqft - 600.0) < 1e-6
    assert st.rules.setbacks_mm == {"front": 900, "rear": 700,
                                    "left": 700, "right": 0}
    assert st.rules.verified is True
    # 6096-700-0 = 5396 wide, 9144-900-700 = 7544 deep
    assert (st.envelope_w_mm, st.envelope_d_mm) == (5396, 7544)
    assert st.envelope_area_mm2 == 5396 * 7544 == 40_707_424
    # coverage cap 75% of 55.741824 m^2 = 41.806368 m^2 > envelope -> envelope binds
    assert st.coverage_cap_mm2 == int(55_741_824 * 0.75) == 41_806_368
    assert st.binding_cap == "envelope"
    assert st.footprint_mm2 == 40_707_424
    assert st.extra_rear_setback_mm == 0
    # FAR 1.75 -> 97.548192 m^2; ceil(97.548192 / 40.707424) = 3
    assert st.far_builtup_mm2 == int(55_741_824 * 1.75) == 97_548_192
    assert st.implied_storeys == 3
    assert st.permitted_storeys == 3


def test_envelope_30x40():
    """1200 sqft — the verified BBMP anchor, and coverage binds here."""
    st = _env(30, 40)
    assert st.plot_area_mm2 == 9144 * 12192 == 111_483_648
    assert abs(st.plot_area_sqft - 1200.0) < 1e-6
    assert st.rules.far == 1.75 and st.rules.coverage == 0.75
    assert (st.envelope_w_mm, st.envelope_d_mm) == (8444, 10592)
    assert st.envelope_area_mm2 == 8444 * 10592 == 89_438_848
    cap = int(111_483_648 * 0.75)
    assert cap == 83_612_736
    # envelope 89.44 m^2 > cap 83.61 m^2 -> depth is given back to the rear
    assert st.binding_cap == "ground_coverage"
    assert st.footprint_d_mm == cap // 8444 == 9902
    assert st.footprint_w_mm == 8444
    assert st.footprint_mm2 == 8444 * 9902 <= cap
    assert st.extra_rear_setback_mm == 10592 - 9902 == 690
    assert st.far_builtup_mm2 == int(111_483_648 * 1.75) == 195_096_384
    assert st.implied_storeys == 3
    # layout rectangle = footprint inset by half the 230 mm exterior wall
    assert (st.tiling_w_mm, st.tiling_d_mm) == (8444 - 230, 9902 - 230)


def test_envelope_40x60():
    """2400 sqft — the verified percentage band."""
    st = _env(40, 60)
    assert st.plot_area_mm2 == 12192 * 18288 == 222_967_296
    assert abs(st.plot_area_sqft - 2400.0) < 1e-6
    assert st.rules.verified is True
    assert st.rules.front_mm == round(0.12 * 18288) == 2195
    assert st.rules.rear_mm == round(0.08 * 18288) == 1463
    assert st.rules.side_left_mm == st.rules.side_right_mm == round(0.08 * 12192) == 975
    assert (st.envelope_w_mm, st.envelope_d_mm) == (10242, 14630)
    assert st.envelope_area_mm2 == 10242 * 14630 == 149_840_460
    assert st.coverage_cap_mm2 == int(222_967_296 * 0.75) == 167_225_472
    assert st.binding_cap == "envelope"          # 149.84 < 167.23
    assert st.far_builtup_mm2 == int(222_967_296 * 1.75) == 390_192_768
    assert st.implied_storeys == 3                # ceil(390.19 / 149.84)


def test_envelope_50x80():
    """4000 sqft. Above 2400 the percentage rule is an EXTRAPOLATION and the
    statement must say so — that flag is the point of the audit trail."""
    st = _env(50, 80)
    assert st.plot_area_mm2 == 15240 * 24384 == 371_612_160
    assert abs(st.plot_area_sqft - 4000.0) < 1e-6
    assert st.rules.verified is False
    assert "EXTRAPOLATED" in st.rules.rule_text["front"]
    assert st.rules.front_mm == round(0.12 * 24384) == 2926
    assert st.rules.rear_mm == round(0.08 * 24384) == 1951
    assert st.rules.side_left_mm == round(0.08 * 15240) == 1219
    assert (st.envelope_w_mm, st.envelope_d_mm) == (12802, 19507)
    assert st.envelope_area_mm2 == 12802 * 19507 == 249_728_614
    assert st.binding_cap == "envelope"
    assert st.implied_storeys == 3


def test_audit_trail_is_complete_and_ordered():
    st = _env(30, 40, programme=bhk_programme(3, pooja=True))
    assert [s.n for s in st.steps] == list(range(1, len(st.steps) + 1))
    assert all(s.rule for s in st.steps)
    labels = [s.label for s in st.steps]
    for want in ("plot area", "buildable envelope", "ground coverage cap",
                 "max footprint", "FAR allowance", "implied storeys",
                 "achievable built-up", "layout rect (centrelines)"):
        assert want in labels, want
    json.dumps(st.to_dict())                       # must be serialisable
    assert "AREA STATEMENT" in st.format_table()


def test_water_filled_allocation_respects_nbc_floors():
    """On a tight plot the shrink must stop at the NBC minimum, not sail past."""
    st = _env(20, 30, programme=bhk_programme(1))
    assert st.verdict in ("TIGHT", "FEASIBLE")
    for b in st.budgets:
        assert b.budget_m2 >= b.nbc_min_m2 - 1e-9, b


def test_growth_is_capped_so_big_plots_do_not_bloat_wet_rooms():
    st = _env(50, 80, programme=bhk_programme(3, pooja=True))
    bath = next(b for b in st.budgets if b.category == "bathroom")
    assert bath.budget_m2 < 1.25 * bath.requested_m2
    assert st.slack_m2 > 10.0        # surplus is reported, not hidden in a bath


def test_canonical_bylaws_table_agrees_on_the_verified_bands():
    """bylaws.py owns the tables; the local stub is only for standalone runs.
    They must agree wherever the stub claims a verified citation, and where
    they diverge (above 3875 sqft) bylaws.py wins."""
    prof = default_profile()
    for w, d in ((20, 30), (30, 40), (30, 50), (40, 60)):
        a = compute_envelope(w, d, profile=prof)
        b = compute_envelope(w, d, profile=BBMPDefault())
        assert a.rules.setbacks_mm == b.rules.setbacks_mm, (w, d)
        assert a.rules.far == b.rules.far == 1.75, (w, d)
        assert a.rules.coverage == b.rules.coverage == 0.75, (w, d)
    # 50x80 = 4000 sqft falls in BBMP's >=3875 band: FAR 2.25, coverage 65%
    big = compute_envelope(50, 80, profile=prof)
    if big.rules.profile != BBMPDefault().name:
        assert big.rules.far == 2.25 and big.rules.coverage == 0.65
        assert big.binding_cap == "ground_coverage"
        assert big.footprint_mm2 <= big.coverage_cap_mm2


def test_envelope_gate_rejects_overstuffed_brief_arithmetically():
    st = _env(20, 30, programme=bhk_programme(5))
    assert st.verdict == "INFEASIBLE"
    assert any("NBC minima" in r for r in st.reasons)


# --------------------------------------------------------------------------
# 3. layout: structural invariants
# --------------------------------------------------------------------------

def _rooms(plan):
    return [Polygon([p.as_tuple() for p in r.polygon]) for r in plan.rooms]


def _assert_valid_tiling(res):
    """Rooms must tile the layout rectangle exactly: no overlap, no gap."""
    plan, st = res.plan, res.statement
    polys = _rooms(plan)
    assert len(polys) == len(plan.rooms)
    for i in range(len(polys)):
        for j in range(i + 1, len(polys)):
            assert polys[i].intersection(polys[j]).area < TOL, \
                f"rooms {i},{j} overlap"
    u = unary_union(polys)
    assert u.geom_type == "Polygon", "tiling is not simply connected"
    x0, y0, x1, y1 = u.bounds
    # the layout rectangle, snapped inward onto the solver grid
    ex0, ey0 = st.tiling_polygon[0].x, st.tiling_polygon[0].y
    ex1, ey1 = st.tiling_polygon[2].x, st.tiling_polygon[2].y
    gx0, gy0 = -(-ex0 // GRID_MM) * GRID_MM, -(-ey0 // GRID_MM) * GRID_MM
    gx1, gy1 = (ex1 // GRID_MM) * GRID_MM, (ey1 // GRID_MM) * GRID_MM
    assert (round(x0), round(y0), round(x1), round(y1)) == (gx0, gy0, gx1, gy1)
    assert abs(u.area - (gx1 - gx0) * (gy1 - gy0)) < TOL, "tiling has gaps"


def _assert_nbc(res):
    assert res.nbc_violations == [], res.nbc_violations
    for rid, (cw, ch) in res.clear_wh_mm.items():
        req = next(r for r in res.statement.budgets if r.id == rid) \
            if any(b.id == rid for b in res.statement.budgets) else None
        if req is not None:
            assert min(cw, ch) >= req.nbc_min_width_mm, (rid, cw, ch)
            assert res.area_m2[rid] >= req.nbc_min_m2 - 1e-9, rid


def _assert_coverage(res):
    """Built footprint (outer faces) must stay inside the coverage cap and the
    buildable envelope. Rooms are centrelines, so grow by half the ext wall."""
    st = res.statement
    u = unary_union(_rooms(res.plan))
    half = st.exterior_wall_mm // 2
    x0, y0, x1, y1 = u.bounds
    outer = box(x0 - half, y0 - half, x1 + half, y1 + half)
    assert outer.area <= st.coverage_cap_mm2 + TOL, \
        f"footprint {mm2_to_m2(outer.area):.2f} > cap {mm2_to_m2(st.coverage_cap_mm2):.2f}"
    env = Polygon([p.as_tuple() for p in st.envelope_polygon])
    assert env.buffer(1).contains(outer), "footprint breaches a setback"


def _assert_reachable(res):
    assert res.unreachable == [], res.unreachable
    fd = [o for o in res.plan.openings if o.kind == "front_door"]
    assert len(fd) == 1, "expected exactly one front door"
    doors = [o for o in res.plan.openings if o.kind == "door"]
    # a tree over n rooms needs n-1 interior doors
    assert len(doors) >= len(res.plan.rooms) - 1


def _assert_openings_fit_their_walls(res):
    for o in res.plan.openings:
        w = res.plan.wall(o.wall_id)
        assert w is not None, o.wall_id
        assert 0.0 <= o.position <= 1.0
        half = o.width / 2.0
        centre = o.position * w.length
        assert centre - half >= -TOL and centre + half <= w.length + TOL, \
            f"{o.id} runs off wall {w.id} ({w.length:.0f} mm)"


_PROG_KW = ("baths", "dining", "pooja", "utility", "living_m2")


def _solve(w, d, n, **kw):
    kw.setdefault("time_limit_s", 8.0)
    pk = {k: kw.pop(k) for k in list(kw) if k in _PROG_KW}
    prog = kw.pop("programme", None) or bhk_programme(
        n, pooja=pk.pop("pooja", n >= 3), dining=pk.pop("dining", n >= 4),
        utility=pk.pop("utility", n >= 4), **pk)
    spec = LayoutSpec(programme=prog,
                      required_adjacency=[("kitchen", "living")], **kw)
    return solve_layout(w, d, spec)


def test_2bhk_on_30x40():
    res = _solve(30, 40, 2)
    assert res.ok and res.status in ("OPTIMAL", "FEASIBLE"), res.message
    _assert_valid_tiling(res)
    _assert_nbc(res)
    _assert_coverage(res)
    _assert_reachable(res)
    _assert_openings_fit_their_walls(res)
    assert res.unmet_adjacency == []


def test_3bhk_on_30x40():
    res = _solve(30, 40, 3)
    assert res.ok and res.status in ("OPTIMAL", "FEASIBLE"), res.message
    assert len([r for r in res.plan.rooms if r.category == "bedroom"]) == 3
    _assert_valid_tiling(res)
    _assert_nbc(res)
    _assert_coverage(res)
    _assert_reachable(res)
    _assert_openings_fit_their_walls(res)
    assert res.unmet_adjacency == []


def test_3bhk_on_40x60_and_50x80():
    for w, d in ((40, 60), (50, 80)):
        res = _solve(w, d, 3)
        assert res.ok, (w, d, res.message)
        _assert_valid_tiling(res)
        _assert_nbc(res)
        _assert_coverage(res)
        _assert_reachable(res)


def test_wall_thickness_is_assigned_by_position():
    res = _solve(30, 40, 3)
    st = res.statement
    ts = {w.thickness for w in res.plan.walls}
    assert ts == {st.exterior_wall_mm, st.interior_wall_mm}
    u = unary_union(_rooms(res.plan))
    x0, y0, x1, y1 = u.bounds
    for w in res.plan.walls:
        on_edge = (w.start.x == w.end.x and w.start.x in (x0, x1)) or \
                  (w.start.y == w.end.y and w.start.y in (y0, y1))
        assert w.thickness == (st.exterior_wall_mm if on_edge
                               else st.interior_wall_mm)


def test_vastu_weight_moves_the_kitchen_southeast():
    """The Vastu term must actually steer, not just exist. On a north-facing
    plot (north_deg=180) south-east is +Y/-X, so raising the weight should push
    the kitchen centroid that way."""
    prog = bhk_programme(3, pooja=True)
    off = solve_layout(40, 60, LayoutSpec(programme=prog, w_vastu=0.0,
                                          time_limit_s=8, seed=3))
    on = solve_layout(40, 60, LayoutSpec(programme=prog, w_vastu=2.5,
                                         time_limit_s=8, seed=3))
    assert off.ok and on.ok

    def centroid(res, rid):
        r = next(r for r in res.plan.rooms if r.id == rid)
        p = Polygon([q.as_tuple() for q in r.polygon]).centroid
        return p.x, p.y

    ox, oy = centroid(off, "kitchen")
    nx, ny = centroid(on, "kitchen")
    vx, vy = zone_vector("SE", on.statement.north_deg)
    # projection onto the SE direction must not get worse
    assert (nx * vx + ny * vy) >= (ox * vx + oy * vy) - 1.0


# --------------------------------------------------------------------------
# 4. the important negative test
# --------------------------------------------------------------------------

def test_5bhk_on_20x30_is_infeasible_not_silently_bad():
    res = _solve(20, 30, 5)
    assert res.status == "INFEASIBLE"
    assert res.plan is None, "must not return a bad plan"
    assert res.infeasible_groups, "must name the unsatisfiable constraint set"
    assert "NBC minima" in res.message or "min_area" in res.infeasible_groups
    # the report has to carry the arithmetic, not just a verdict
    assert "m2" in res.message


def test_2bhk_on_20x30_reports_the_band_argument():
    """A 2BHK does not fit 600 sqft under NBC 2.4 m room widths: three rooms
    need >= 2.4 m clear and the 5.16 m layout width takes only one per band.
    The failure must name that, since it is what the LLM repairs on."""
    res = _solve(20, 30, 2, baths=1)
    assert res.status == "INFEASIBLE"
    assert res.plan is None
    assert res.topology_exhausted is True
    joined = " ".join(res.infeasible_groups)
    assert "clear width" in joined and "band" in joined, res.infeasible_groups
    assert "min_clear_width" in res.infeasible_groups


def test_short_budget_never_misreports_a_solvable_brief_as_infeasible():
    """INFEASIBLE must mean "cannot fit", not "ran out of time". A brief that
    solves in 8 s must not come back INFEASIBLE on a 1 s budget."""
    for w, d, n in ((30, 40, 3), (40, 60, 4), (50, 80, 5)):
        res = _solve(w, d, n, time_limit_s=1.0)
        assert res.status != "INFEASIBLE", (w, d, n, res.message)
        if res.status == "TIMEOUT":
            assert res.plan is None
            assert res.infeasible_groups == []
            assert "not proven infeasible" in res.message
        else:
            assert res.ok


def test_infeasible_and_timeout_carry_different_payloads():
    bad = _solve(20, 30, 5)
    assert bad.status == "INFEASIBLE" and bad.infeasible_groups
    good = _solve(30, 40, 2)
    assert good.ok and good.infeasible_groups == []


def test_solver_never_raises_on_absurd_input():
    for w, d, n in ((8, 8, 3), (10, 12, 1), (20, 30, 9)):
        res = _solve(w, d, n, time_limit_s=2.0)
        assert res.status in ("INFEASIBLE", "TIMEOUT", "FEASIBLE", "OPTIMAL")
        assert isinstance(res.message, str) and res.message


# --------------------------------------------------------------------------
# 5. IR fidelity — the same bar the ResPlan converter clears
# --------------------------------------------------------------------------

def test_plan_survives_to_project_and_json():
    res = _solve(30, 40, 3)
    proj = to_project(res.plan, name="test")
    blob = json.dumps(proj)                          # must not raise
    assert len(blob) > 1000
    back = from_project(json.loads(blob))
    ident = ir_identity(res.plan, back)
    assert ident["walls_equal"] and ident["openings_equal"]
    assert ident["rooms_equal"] and ident["plot_equal"]
    fl = proj["floors"][0]
    assert len(fl["walls"]) == len(res.plan.walls)
    assert len(fl["rooms"]) == len(res.plan.rooms)
    assert len(fl["doors"]) + len(fl["windows"]) == len(res.plan.openings)
    for d in fl["doors"] + fl["windows"]:
        assert d["wallId"] in {w["id"] for w in fl["walls"]}


def test_rooms_are_recoverable_as_faces_of_the_wall_graph():
    for w, d, n in ((30, 40, 2), (30, 40, 3), (40, 60, 3), (50, 80, 4)):
        res = _solve(w, d, n, time_limit_s=6.0)
        assert res.ok, (w, d, n, res.message)
        fr = face_recovery(res.plan)
        assert fr["ok"] and fr["area_iou"] > 0.99, (w, d, n, fr)
        m = room_face_match(res.plan)
        assert m["all_matched"], (w, d, n, m)
        assert m["extra_faces"] == 0, (w, d, n, m)
        assert m["min_iou"] > 0.99, (w, d, n, m)


def test_room_area_matches_its_polygon():
    res = _solve(30, 40, 3)
    for r in res.plan.rooms:
        poly = Polygon([p.as_tuple() for p in r.polygon])
        assert abs(poly.area - r.area) < 1.0, r.id


def test_provenance_carries_the_area_statement():
    res = _solve(30, 40, 3)
    prov = res.plan.provenance
    assert prov["source"] == "cpsat-solver"
    assert prov["grid_mm"] == GRID_MM
    assert "area_statement" in prov and "topology" in prov
    json.dumps(prov)


# --------------------------------------------------------------------------
# 6. pinned elements
# --------------------------------------------------------------------------

def test_pinned_walls_survive_a_resolve_and_unpinned_ones_do_not():
    """The control matters: the same changed brief without pins must move those
    walls, otherwise the test proves nothing about pinning."""
    prog = bhk_programme(3, pooja=True)
    base = solve_layout(30, 40, LayoutSpec(
        programme=prog, required_adjacency=[("kitchen", "living")],
        time_limit_s=8, seed=1))
    assert base.ok, base.message
    interior = [w for w in base.plan.walls
                if w.thickness == base.statement.interior_wall_mm]
    assert len(interior) >= 3
    pins = [interior[0].id, interior[2].id]
    pinned_geom = [_wall_axis_coord(base.plan.wall(i)) for i in pins]

    # a materially different brief: much bigger living, much smaller bedrooms
    prog2 = [RoomReq(**p.__dict__) for p in prog]
    for p in prog2:
        if p.id == "living":
            p.target_m2 = 26.0
        elif p.id.startswith("bed"):
            p.target_m2 = 8.5
    spec2 = LayoutSpec(programme=prog2,
                       required_adjacency=[("kitchen", "living")],
                       time_limit_s=8)

    pinned = solve_layout(30, 40, spec2, pinned_wall_ids=pins, previous=base)
    free = solve_layout(30, 40, spec2, previous=base)
    assert pinned.ok and free.ok

    got = {_wall_axis_coord(w) for w in pinned.plan.walls}
    assert all(g in got for g in pinned_geom), "a pinned wall moved"
    assert pinned.pinned_ok is True

    # the re-solve genuinely changed the plan, so the pin was load-bearing
    assert pinned.area_m2["living"] > base.area_m2["living"] + 1.0
    free_got = {_wall_axis_coord(w) for w in free.plan.walls}
    assert not all(g in free_got for g in pinned_geom), \
        "control failed: walls did not move even without pins"


def test_pinning_without_a_previous_result_is_a_no_op_not_a_crash():
    res = solve_layout(30, 40, LayoutSpec(programme=bhk_programme(2),
                                          time_limit_s=6),
                       pinned_wall_ids=["w3", "nonexistent"])
    assert res.ok
    assert res.pinned_ok is True


# --------------------------------------------------------------------------
# 7. performance
# --------------------------------------------------------------------------

MATRIX = [(20, 30, 1), (30, 40, 2), (30, 40, 3), (30, 50, 2), (30, 50, 3),
          (40, 60, 3), (40, 60, 4), (50, 80, 3), (50, 80, 4), (50, 80, 5)]


def test_solve_time_percentiles():
    times, rows = [], []
    for w, d, n in MATRIX:
        t0 = time.time()
        res = _solve(w, d, n, time_limit_s=10.0)
        wall = time.time() - t0
        times.append(wall)
        rows.append((w, d, n, res.status, wall, res.mean_abs_dev_pct,
                     res.max_abs_dev_pct,
                     len(res.rooms_without_window) if res.ok else -1))
        assert res.ok, (w, d, n, res.message)
    times.sort()
    p50 = statistics.median(times)
    p90 = times[max(0, int(0.9 * len(times)) - 1)]
    p95 = times[max(0, int(0.95 * len(times)) - 1)]
    print("\n  plot     BHK  status     wall_s  mean_dev  max_dev  no_window")
    for w, d, n, s, t, md, xd, nw in rows:
        print(f"  {w:>2}x{d:<3}  {n}    {s:<9} {t:6.2f}  {md:7.1f}%  "
              f"{xd:6.1f}%  {nw}")
    print(f"  solve time p50={p50:.2f}s p90={p90:.2f}s p95={p95:.2f}s "
          f"max={times[-1]:.2f}s over {len(times)} briefs")
    assert p50 < 6.0
    assert times[-1] < 15.0            # the 10 s budget plus emission overhead


# --------------------------------------------------------------------------

def _main() -> int:
    fns = [(k, v) for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    failed = []
    for name, fn in fns:
        t0 = time.time()
        try:
            fn()
            print(f"PASS {name}  ({time.time() - t0:.2f}s)")
        except AssertionError as e:
            failed.append((name, f"AssertionError: {e}"))
            print(f"FAIL {name}  ({time.time() - t0:.2f}s)\n     {e}")
        except Exception as e:  # noqa: BLE001
            failed.append((name, f"{type(e).__name__}: {e}"))
            print(f"ERROR {name}  ({time.time() - t0:.2f}s)\n     "
                  f"{type(e).__name__}: {e}")
    print(f"\n{len(fns) - len(failed)}/{len(fns)} passed")
    for name, err in failed:
        print(f"  {name}: {err}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
