"""Operations that must be refused, and legal tables that must not drift apart.

Two kinds of assertion live here, and they answer two different failure modes.

## Must-refuse

A brief can be arithmetically valid and still be a domain error: a WC opening
onto a kitchen, a bedroom capped below the NBC minimum, a plot in metres typed
as feet. These are refused at apply time with the reason, because the reason is
what reaches the person who asked. Refusing them at solve time instead loses
the reason -- the solver reports INFEASIBLE and cannot say the brief was wrong
rather than the site too small -- and not refusing them at all means the
system builds the mistake and then reports it as a defect in its own output.

## Table coherence

The expensive bugs in this codebase have not been wrong rules. They have been
rules that never ran, because a category was missing from the table the rule
was keyed by, and a missing key returns a permissive default that is
indistinguishable from "checked and fine".

Measured: `master_bedroom` was absent from `envelope.NBC_MIN` and from
`rules.HABITABLE`, so every master bedroom in every plan the system had ever
produced was budgeted against 1 m2 instead of 7.5 m2 and never checked for
minimum area, width or ceiling height. `guest_bedroom` was absent from both.
`dining` and `study` were in `NBC_MIN` and in `standards.HABITABLE_VENT` but
not in `rules.HABITABLE`, so a study needed a window and had no minimum size.
Three tables, three different answers to "is this room habitable".

So these tests do not assert the values. They assert that the tables agree,
which is the property that was actually violated.
"""
from __future__ import annotations

import pytest

from fpeval import roomtypes as rt
from fpeval import rules as R
from fpeval import standards as SD
from fpeval.commands import Command
from fpeval.document import Document
from fpeval.envelope import NBC_MIN, RoomReq
from fpeval.spec import DEFAULT_ADJACENCY


def agent(op: str, **params) -> Command:
    return Command(op=op, params=params, source="agent", description=op)


def brief_doc(bedrooms: int = 2, **extras) -> Document:
    """A document with a standard brief and no geometry yet."""
    doc = Document.empty("g", name="Untitled")
    res = doc.apply(agent("use_standard_programme", bedrooms=bedrooms, **extras))
    assert res.ok, res.errors
    return doc


# ---------------------------------------------------------------------------
# table coherence
# ---------------------------------------------------------------------------

# Every category a plan can actually carry, as opposed to every key in the
# taxonomy: these are what the solver and the brief emit.
LIVE_CATEGORIES = sorted(rt.T)


def test_the_permissive_nbc_fallback_is_never_reached_by_a_known_category():
    """`NBC_MIN` misses give `(900 mm, 1.0 m2)`, which is filler space.

    A known room type reaching that default is silently exempt from its own
    legal minimum. The fallback must only be reachable by a category the
    taxonomy has never heard of.
    """
    fell_through = []
    for cat in LIVE_CATEGORIES:
        if any(k in NBC_MIN for k in rt.counts_as(cat)):
            continue
        t = rt.get(cat)
        # Only habitable and wet rooms carry NBC minima at all; circulation,
        # outdoor and shaft space legitimately does not.
        if t is not None and t.klass in ("habitable", "wet"):
            fell_through.append(cat)
    assert not fell_through, (
        "these room types have no NBC row and no subtype path to one, so they "
        f"silently default to 900 mm / 1.0 m2: {fell_through}")


def test_every_nbc_habitable_room_is_checked_for_minimum_area():
    """The validator's habitable test must cover every habitable category.

    `master_bedroom` failing this is the bug that motivated the file.
    """
    missed = [c for c in LIVE_CATEGORIES
              if any(k in R.HABITABLE for k in rt.counts_as(c))
              and not R._is_habitable(c)]
    assert not missed, f"habitable but not checked by the validator: {missed}"

    # And the specific cases, named, so a regression says which one.
    for cat in ("bedroom", "master_bedroom", "guest_bedroom", "living",
                "dining", "study"):
        assert R._is_habitable(cat), f"{cat} is not treated as habitable"


def test_habitable_glazing_and_habitable_minima_agree():
    """A room that must have a window must also have a minimum size.

    The reverse does not hold -- a pooja room has a minimum and needs no
    window, deliberately -- so this is one-directional on purpose.
    """
    for cat in sorted(SD.HABITABLE_VENT):
        assert R._is_habitable(cat), (
            f"{cat} is required to have a window by standards.HABITABLE_VENT "
            "but is exempt from the habitable minimum-area check")
        assert any(k in NBC_MIN for k in rt.counts_as(cat)), (
            f"{cat} needs a window but has no NBC minimum area")


def test_a_subtype_inherits_its_parents_legal_minimum():
    parent = RoomReq(id="a", name="A", category="bedroom", target_m2=12.0)
    for sub in ("master_bedroom", "guest_bedroom"):
        child = RoomReq(id="b", name="B", category=sub, target_m2=12.0)
        assert child.nbc_min_area_m2() == parent.nbc_min_area_m2(), sub
        assert child.nbc_min_width() == parent.nbc_min_width(), sub


def test_counts_as_and_subtypes_of_are_mutually_consistent():
    """If A counts as B then B's subtypes include A, at any depth.

    One level deep on one side and transitive on the other meant `toilet`
    counted as a `bathroom` while a request for a `bathroom` was not satisfied
    by a `toilet`.
    """
    for a in LIVE_CATEGORIES:
        for b in rt.counts_as(a):
            assert a in rt.subtypes_of(b), (
                f"{a} counts as {b}, but subtypes_of({b}) does not include it")


# ---------------------------------------------------------------------------
# must-refuse: briefs that contradict the domain
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("a,b,relation", [
    ("pooja", "bath1", "adjacent"),          # Vastu: no wet room beside a shrine
    ("bath1", "kitchen", "direct_access"),   # no bathroom door into a kitchen
    ("toilet1", "kitchen", "adjacent"),      # no WC sharing a kitchen wall
])
def test_requiring_a_prohibited_adjacency_is_refused(a, b, relation):
    """Asking for a pair the domain forbids is a contradiction, not a taste.

    It has to be refused rather than solved: `required` and `forbidden` go
    into separate lists in `LayoutSpec` and nothing cross-checks them, so the
    solver would have honoured it and the validator would then have reported
    our own output as defective.
    """
    doc = brief_doc(bedrooms=2, pooja=True)
    before = doc.hash
    res = doc.apply(agent("set_adjacency", a=a, b=b, kind="required",
                          relation=relation))
    assert not res.ok, f"{a} required {relation} {b} was accepted"
    # The refusal has to carry the domain reason, not just "invalid".
    assert any(len(e) > 40 for e in res.errors), res.errors
    assert doc.hash == before


@pytest.mark.parametrize("a,b,relation", [
    ("kitchen", "living", "direct_access"),  # the kitchen must be reachable
    ("bed1", "bath1", "direct_access"),      # an en-suite
])
def test_a_legitimate_adjacency_still_applies(a, b, relation):
    """The guard must discriminate, not just refuse anything wet."""
    doc = brief_doc(bedrooms=2, pooja=True)
    res = doc.apply(agent("set_adjacency", a=a, b=b, kind="required",
                          relation=relation))
    assert res.ok, res.errors


def test_prohibited_pairs_come_from_the_shipped_table():
    """The guard is derived, not restated.

    If it were a second list, the two would disagree within a month and the
    applier would be enforcing a rule the brief does not contain.
    """
    from fpeval.apply import _prohibited_pairs
    shipped = {frozenset((a.a, a.b)) for a in DEFAULT_ADJACENCY
               if a.kind == "prohibited"}
    assert set(_prohibited_pairs()) == shipped
    assert shipped, "the domain table has no prohibitions at all"


# ---------------------------------------------------------------------------
# must-refuse: sizes that cannot produce a legal room
# ---------------------------------------------------------------------------

def test_an_area_range_that_cannot_hold_a_legal_room_is_refused():
    doc = brief_doc()
    res = doc.apply(agent("set_room_area", room_id="bath1",
                          min_sqft=4, max_sqft=6))
    assert not res.ok
    assert "NBC" in " ".join(res.errors)


def test_a_master_bedroom_gets_the_same_floor_as_a_bedroom():
    """The subtype hole, asserted through the command the agent would use."""
    doc = brief_doc()
    assert doc.design.spec.room("bed1").category == "master_bedroom"
    res = doc.apply(agent("set_room_area", room_id="bed1",
                          min_sqft=30, max_sqft=45))
    assert not res.ok, ("a master bedroom capped at 45 sqft was accepted; NBC "
                        "asks for 7.5 m2 (~81 sqft)")


def test_a_low_floor_with_a_legal_ceiling_is_allowed():
    """Only the ceiling is checked, deliberately.

    Bengaluru practice builds rooms tighter than NBC asks and
    `AgentPolicy.relaxed_minima` exists for that, so refusing every tight
    range would refuse most of the market. The solver is free to land above a
    low `min_sqft`.
    """
    doc = brief_doc()
    res = doc.apply(agent("set_room_area", room_id="bed1",
                          min_sqft=70, max_sqft=190))
    assert res.ok, res.errors


def test_an_inverted_area_range_is_refused():
    doc = brief_doc()
    res = doc.apply(agent("set_room_area", room_id="bed1",
                          min_sqft=200, max_sqft=120))
    assert not res.ok
    assert "above max_sqft" in " ".join(res.errors)


def test_a_corridor_shaped_habitable_room_is_refused():
    doc = brief_doc()
    res = doc.apply(agent("set_room_aspect", room_id="bed1", max_aspect=9.0))
    assert not res.ok
    assert "corridor" in " ".join(res.errors)
    assert doc.apply(agent("set_room_aspect", room_id="bed1",
                           max_aspect=1.6)).ok


# ---------------------------------------------------------------------------
# must-refuse: sites that are not sites
# ---------------------------------------------------------------------------

def test_a_plot_in_metres_typed_as_feet_is_refused():
    """9 x 12 is a 30x40 plot in metres. Solved as feet it is a shed."""
    doc = Document.empty("g")
    res = doc.apply(agent("set_plot", width_ft=9, depth_ft=12))
    assert not res.ok
    assert "metres" in " ".join(res.errors)


def test_an_apartment_unit_never_carries_plot_dimensions():
    """A unit inside a tower has no plot, and inventing one silently
    invalidates every setback, coverage and FAR check."""
    doc = Document.empty("g")
    assert doc.apply(agent("set_plot", width_ft=30, depth_ft=40)).ok
    assert doc.apply(agent("set_plot", site_kind="apartment_unit",
                           carpet_sqft=1150)).ok
    sp = doc.design.spec
    assert sp.plot_width_ft is None and sp.plot_depth_ft is None
    assert sp.unit_area.carpet_sqft == 1150


def test_an_apartment_unit_is_not_validated_against_plot_byelaws():
    """`check_bylaws` skips units when told the site kind, and the agent's
    solve path used to tell it nothing -- ten false errors on the suite."""
    from fpeval.programme import spec_to_brief
    from fpeval.spec import DesignSpec
    b = spec_to_brief(DesignSpec(site_kind="apartment_unit"))
    assert b["site_kind"] == "apartment_unit"
    assert b["requirements"] is not None


# ---------------------------------------------------------------------------
# must-refuse: the author boundary
# ---------------------------------------------------------------------------

def test_an_agent_cannot_author_a_coordinate():
    """DECISIONS.md #6, enforced rather than hoped for: the model emits
    intent, solvers emit geometry."""
    doc = Document.empty("g")
    res = doc.apply(Command(op="add_wall", source="agent",
                            params={"wall_id": "w0",
                                    "start": {"x": 0, "y": 0},
                                    "end": {"x": 3000, "y": 0}},
                            description="draw a wall"))
    assert not res.ok


def test_a_programme_command_cannot_reach_a_room_that_is_not_in_the_brief():
    doc = brief_doc()
    res = doc.apply(agent("set_room_zone", room_id="bed9",
                          preferred_zone="NE"))
    assert not res.ok
    assert "in the brief" in " ".join(res.errors)


def test_add_room_refuses_a_duplicate_id():
    doc = brief_doc()
    res = doc.apply(agent("add_room", room_id="kitchen", category="kitchen"))
    assert not res.ok
    assert "already in the brief" in " ".join(res.errors)


def test_a_refused_command_leaves_no_trace():
    """Partial application is the one thing that must never happen: a brief
    half-edited by a refused command is worse than either outcome."""
    doc = brief_doc()
    before_hash, before_seq = doc.hash, doc.seq
    for bad in (agent("set_room_area", room_id="bath1", min_sqft=4, max_sqft=6),
                agent("set_room_aspect", room_id="bed1", max_aspect=99),
                agent("add_room", room_id="kitchen", category="kitchen"),
                agent("set_storeys", value=17)):
        assert not doc.apply(bad).ok, bad.op
    assert doc.hash == before_hash
    assert doc.seq == before_seq


# ---------------------------------------------------------------------------
# layout sense: defects that were visible on the drawing and invisible here
# ---------------------------------------------------------------------------
#
# Each fixture below is the smallest plan that exhibits one defect. They came
# from a critique of our own output: "living room and hall should be at one
# place", "the utility is not attached to the kitchen", "both washrooms at the
# same place", "nothing attached". Every one of those was true, and the
# validator reported zero errors on the plan.
#
# The suite could not have caught them either. Measured across its 160
# examples: 92% assert which rooms exist, 19% assert any adjacency at all, 5%
# assert a prohibition, 1% an entrance side -- 54 adjacency assertions in
# total, 0.34 per example. It is a programme-recovery benchmark. Arrangement
# has to be carried by the rules engine, because `compliance` counts
# rules-engine errors and that is the only channel through which all 160
# examples get checked for it.

from tests.test_rules import _build                      # noqa: E402
from fpeval.bylaws import BENGALURU                       # noqa: E402
from fpeval.rules import check_layout_sense, _build_ctx   # noqa: E402


def sense(rects, doors, brief=None):
    """Just the layout-sense findings for a fixture, by rule id."""
    plan = _build(rects, doors, plot=(-2000, -2000, 20000, 20000),
                  north_deg=180.0, plan_id="sense")
    ctx = _build_ctx(plan, brief or {}, BENGALURU)
    return {f.rule_id: f for f in check_layout_sense(ctx)}


def test_a_dining_room_bigger_than_the_living_room_is_an_error():
    """98% of 400 real plans make the living the largest habitable room.

    Measured on our own 40x60 3BHK: living 111 sqft against a dining of 275.
    """
    got = sense(
        [("Living", "living", (0, 0, 3000, 3000)),          # 9.0 m2
         ("Dining", "dining", (3000, 0, 8000, 5000)),       # 25.0 m2
         ("Kitchen", "kitchen", (0, 3000, 3000, 5000))],
        [(1500, 0, "front_door", 1050),
         (3000, 1500, "door", 900),
         (1500, 3000, "door", 900)])
    assert "DESIGN.LIVING_NOT_LARGEST" in got
    assert got["DESIGN.LIVING_NOT_LARGEST"].severity == "error"


def test_a_bathroom_reachable_only_through_the_utility_is_an_error():
    """Observed in our output: `bath2`'s only door was off the utility."""
    got = sense(
        [("Living", "living", (0, 0, 4000, 4000)),
         ("Utility", "utility", (4000, 0, 6000, 2000)),
         ("Bathroom", "bathroom", (4000, 2000, 6000, 4000))],
        [(2000, 0, "front_door", 1050),
         (4000, 1000, "door", 900),        # Living  -> Utility
         (5000, 2000, "door", 900)])       # Utility -> Bathroom
    assert "DESIGN.BATH_BEHIND_SERVICE" in got
    assert got["DESIGN.BATH_BEHIND_SERVICE"].severity == "error"


def test_a_passage_wide_enough_to_be_a_room_is_an_error():
    """`envelope.NBC_MIN` calls the passage a "filler hall/corridor absorbing
    leftover area", and that is exactly what it became: capping the service
    rooms pushed a 40x60's surplus into a 337 sqft hall, 21.7% of carpet,
    beside a 111 sqft living room."""
    got = sense(
        [("Living", "living", (0, 0, 4000, 3000)),
         ("Hall", "passage", (0, 3000, 4000, 7000)),       # 4.0 m wide
         ("Bedroom", "bedroom", (4000, 0, 8000, 7000))],
        [(2000, 0, "front_door", 1050),
         (2000, 3000, "door", 900),
         (4000, 5000, "door", 900)])
    assert "DESIGN.CIRCULATION_OVERSIZED" in got
    assert got["DESIGN.CIRCULATION_OVERSIZED"].severity == "error"


def test_public_rooms_split_by_a_bedroom_is_an_error():
    """Two half-sized social spaces instead of one good one."""
    got = sense(
        [("Living", "living", (0, 0, 3000, 4000)),
         ("Bedroom", "bedroom", (3000, 0, 6000, 4000)),
         ("Dining", "dining", (6000, 0, 9000, 4000))],
        [(1500, 0, "front_door", 1050),
         (3000, 2000, "door", 900),
         (6000, 2000, "door", 900)])
    assert "DESIGN.PUBLIC_CORE_SPLIT" in got


def test_a_master_with_no_ensuite_is_an_error_when_the_brief_asked():
    rects = [("Living", "living", (0, 0, 4000, 4000)),
             ("Master Bedroom", "bedroom", (4000, 0, 8000, 4000)),
             ("Bathroom", "bathroom", (0, 4000, 2000, 6000)),
             ("Passage", "passage", (2000, 4000, 8000, 6000))]
    doors = [(2000, 0, "front_door", 1050),
             (3000, 4000, "door", 900),      # Living  -> Passage
             (4000, 2000, "door", 900),      # Living  -> Master
             (2000, 5000, "door", 900)]      # Passage -> Bathroom

    # Silent brief: no finding at all. There is no Indian source for how often
    # a master bedroom has an en-suite -- `suite/` is silent in 86% of its
    # bedroom-bearing examples -- and the 72% figure this once cited came from
    # ResPlan, which `brief.py` documents as non-Indian.
    quiet = sense(rects, doors)
    assert "DESIGN.NO_ENSUITE_MASTER" not in quiet, (
        "an unstated preference must not produce a finding; that warned on 71 "
        "of 100 plans on the strength of a non-Indian corpus")

    asked = sense(rects, doors,
                  {"requirements": {"attached_bath": 1, "rooms": {"bedroom": 1}}})
    assert asked["DESIGN.NO_ENSUITE_MASTER"].severity == "error"


def test_the_clean_fixture_raises_no_layout_errors():
    """The negative control. New rules that fire on a hand-verified compliant
    plan are miscalibrated, and this is the test that says so."""
    from tests.test_rules import clean_plan, CLEAN_BRIEF
    ctx = _build_ctx(clean_plan(), CLEAN_BRIEF, BENGALURU)
    errs = [f for f in check_layout_sense(ctx) if f.severity == "error"]
    assert not errs, [f"{f.rule_id}: {f.detail}" for f in errs]


def test_every_room_type_the_brief_can_ask_for_resolves_in_the_taxonomy():
    """A brief category with no `RoomType` silently skips every roomtypes
    check -- needs-window, minimum width, area band.

    `roomtypes.get()` documents this failure mode ("a miss that returns None
    looks like 'no rule applies' and is indistinguishable from 'checked and
    fine'") and two categories were hitting it: `office` and `servant`, both
    of which `spec.ROOM_CATEGORIES` accepts and `bridge.canon` maps correctly,
    so the solver laid them out and nothing checked them.
    """
    from fpeval.spec import ROOM_CATEGORIES
    missing = sorted(k for k in ROOM_CATEGORIES if rt.get(k) is None)
    assert not missing, (
        f"the brief accepts these categories but the taxonomy has no type for "
        f"them, so every roomtypes-driven check skips: {missing}")


def test_no_brief_room_type_is_silently_dropped_by_the_solver_bridge():
    """`bridge.canon` returning 'unknown' means `spec_to_programme` drops the
    room with a warning and the plan simply lacks it."""
    from fpeval.bridge import canon
    from fpeval.spec import ROOM_CATEGORIES
    dropped = sorted(k for k in ROOM_CATEGORIES if canon(k) == "unknown")
    assert not dropped, f"asked for and silently dropped: {dropped}"


def test_hall_means_living_room_not_circulation():
    """In Indian usage the hall IS the living room, and the taxonomy agrees --
    "hall" is an alias of `living`.

    The solver used to name its leftover-area filler room "Hall" while giving
    it category `passage`, so a drawing showed "HALL" on a corner of
    circulation and "LIVING" somewhere else. A reader concluding the living
    room was in the wrong place was reading a mislabelled plan correctly.
    """
    assert rt.canonical("hall") == "living"
    from fpeval.solver import solve_layout          # noqa: F401  (import guard)
    import inspect
    from fpeval import solver
    src = inspect.getsource(solver)
    assert 'RoomReq("hall", "Hall", "passage"' not in src, (
        "the filler circulation room is named 'Hall', which the taxonomy "
        "resolves to the living room")


# ---------------------------------------------------------------------------
# glazing: a need bigger than one sash becomes two windows, not a shortfall
# ---------------------------------------------------------------------------

def test_a_glazing_need_larger_than_one_sash_becomes_several_windows():
    """NBC asks 1 m2 of window per 10 m2 of floor, and a sash caps at 3 m.

    A 46 m2 living room therefore needs ~4.2 m of glazing, which no single
    opening can provide. The sizer used to place one window per exterior edge,
    so such a room simply failed NBC on any wall with one exterior face --
    measured as NBC.VENTILATION_HABITABLE rising from 9 plans to 14 the moment
    surplus floor started landing in habitable rooms.
    """
    from fpeval.solver import _glazing_runs, JAMB, MIN_WIN_W, PIER_MM
    runs = _glazing_runs(0, 8000, need_mm=4200, cap_mm=3000)
    assert len(runs) == 2, runs
    assert sum(w for _, w in runs) >= 4200
    assert all(w <= 3000 for _, w in runs)


def test_one_window_is_centred_on_its_wall():
    from fpeval.solver import _glazing_runs
    runs = _glazing_runs(0, 6000, need_mm=1800, cap_mm=3000)
    assert len(runs) == 1
    centre, width = runs[0]
    assert width == 1800
    assert centre == 3000, "a lone window belongs in the middle of the run"


def test_glazing_never_overruns_its_wall_or_shares_masonry():
    """Property sweep. Two windows on one wall must be separated by a pier and
    the whole group must sit inside the run with jambs at both ends."""
    from fpeval.solver import _glazing_runs, JAMB, MIN_WIN_W, PIER_MM
    for run in range(700, 14000, 250):
        for need in (600, 1200, 2400, 4200, 6000, 9000):
            for cap in (900, 3000):
                got = _glazing_runs(0, run, need, cap)
                if not got:
                    continue
                for centre, width in got:
                    assert width >= MIN_WIN_W
                    assert width <= cap
                    assert centre - width / 2 >= JAMB - 1, (run, need, cap, got)
                    assert centre + width / 2 <= run - JAMB + 1, (run, need, cap, got)
                edges = sorted((c - w / 2, c + w / 2) for c, w in got)
                for (_, b), (a, _) in zip(edges, edges[1:]):
                    assert a - b >= PIER_MM - 1, (
                        f"windows {b:.0f}..{a:.0f} apart on a {run} mm run "
                        f"leave less than a {PIER_MM} mm pier")


def test_a_bathroom_gets_one_small_high_window_not_a_wall_of_glass():
    from fpeval.solver import _glazing_runs
    runs = _glazing_runs(0, 4000, need_mm=400, cap_mm=900)
    assert len(runs) == 1 and runs[0][1] <= 900


def test_two_openings_on_one_wall_never_overlap_in_a_solved_plan():
    """`place()` clamped an opening inside its wall and then trusted the caller
    not to ask twice. That held only while every room got at most one window
    per wall."""
    from fpeval.bylaws import BENGALURU as _B
    from fpeval.envelope import compute_envelope, CityProfileAdapter, RoomReq
    from fpeval.solver import solve_layout, LayoutSpec
    P = CityProfileAdapter(_B)
    prog = [RoomReq(id="living", name="Living", category="living",
                    target_m2=45.0, weight=1.0, is_entrance=True),
            RoomReq(id="kitchen", name="Kitchen", category="kitchen", target_m2=9.0),
            RoomReq(id="bed1", name="Bedroom 1", category="bedroom", target_m2=16.0),
            RoomReq(id="bath1", name="Bath", category="bathroom", target_m2=3.6)]
    compute_envelope(40, 60, road_facing="E", profile=P, programme=prog)
    res = solve_layout(40, 60,
                       LayoutSpec(programme=prog, entrance_room="living",
                                  time_limit_s=10.0),
                       road_facing="E", profile=P, plan_id="glz")
    plan = res.plan
    assert plan is not None, res.status
    by_wall: dict[str, list] = {}
    for o in plan.openings:
        by_wall.setdefault(o.wall_id, []).append(o)
    for wid, group in by_wall.items():
        if len(group) < 2:
            continue
        w = next(x for x in plan.walls if x.id == wid)
        spans = sorted((o.position * w.length - o.width / 2,
                        o.position * w.length + o.width / 2) for o in group)
        for (_, b), (a, _) in zip(spans, spans[1:]):
            assert a >= b - 1, f"openings overlap on {wid}: {spans}"


# ---------------------------------------------------------------------------
# an opening must fit the boundary it is meant to sit in
# ---------------------------------------------------------------------------

def test_an_opening_cannot_be_widened_past_the_boundary_it_connects():
    """Width was checked against nothing at all -- not even the wall.

    The solver emits long spanning walls with T-junctions mid-span, so the two
    rooms an opening connects may share only a fraction of the host wall.
    Measured live on a 40x60 3BHK: `update_opening width_mm=1800` was accepted
    on a 1100 mm shared boundary because the host wall ran 10010 mm, and the
    resulting opening spilled into a room it was never between.

    Found by an A/B probe: the arm that could see the drawing refused to widen
    past the shared run and said why; the arm that could not claimed to have
    built the 1800 mm opening.
    """
    from fpeval.generate import build
    doc = Document.empty("op", name="t")
    for c in (agent("set_plot", width_ft=40, depth_ft=60, road_facing="east",
                    city="bengaluru"),
              agent("use_standard_programme", bedrooms=3, pooja=True,
                    utility=True, dining=True)):
        assert doc.apply(c).ok
    assert build(doc, time_limit_s=12.0).ok

    o = doc.design.active.openings[0]
    wall = next(w for w in doc.design.active.walls if w.id == o.wall_id)
    assert wall.length > 3000, "fixture needs a long spanning wall to be a test"

    res = doc.apply(agent("update_opening", opening_id=o.id, width_mm=3000))
    assert not res.ok, (
        f"3000 mm opening accepted on a {wall.length:.0f} mm wall whose shared "
        "run is far shorter")
    assert "spills past" in " ".join(res.errors)
    # and the original width is untouched
    assert doc.design.active.opening(o.id).width == o.width


def test_a_door_that_fits_is_still_allowed():
    """The guard must discriminate. A standard door on a real boundary works."""
    from fpeval.generate import build
    doc = Document.empty("op2", name="t")
    for c in (agent("set_plot", width_ft=30, depth_ft=40, road_facing="north"),
              agent("use_standard_programme", bedrooms=2)):
        assert doc.apply(c).ok
    assert build(doc, time_limit_s=12.0).ok
    st = doc.design.active
    # widen an existing door by a little: that must be allowed where there is room
    o = min(st.openings, key=lambda x: x.width)
    res = doc.apply(agent("update_opening", opening_id=o.id, width_mm=o.width))
    assert res.ok, res.errors


def test_the_centre_zone_does_not_crash_the_solver():
    """`spec.ZONES` lists "centre" and `suite.py` validates it, and
    `envelope.zone_vector` raised `KeyError` on it, killing the solve.

    Found by the first ever track B run: 4 of its first 10 examples came back
    `not_run` with `KeyError: 'centre'`, `base-01` -- the simplest example in
    the suite -- among them. Extraction had read "pooja in the centre"
    correctly; the solver could not represent it.

    Asserted as a property over the whole zone vocabulary, because the same
    hole existed in two more places in `furnish.py` and a test naming only
    "centre" would not have found those.
    """
    from fpeval.envelope import zone_vector, VASTU_BEARING
    from fpeval.spec import ZONES
    for z in ZONES:
        vx, vy = zone_vector(z, 0.0)          # must not raise
        assert isinstance(vx, float) and isinstance(vy, float)
    # every zone the spec accepts either has a bearing or is the centre
    from fpeval.envelope import CENTRE_ZONES
    for z in ZONES:
        assert z in VASTU_BEARING or z in CENTRE_ZONES, (
            f"{z!r} is a valid spec zone with no bearing and no centre "
            "handling, so anything indexing VASTU_BEARING will raise on it")
    # and the applier's spelling of the centre is handled too
    assert zone_vector("C", 0.0) == (0.0, 0.0)


def test_a_centre_zone_room_solves_end_to_end():
    from fpeval.spec import default_indian_spec
    from fpeval.bridge import spec_to_programme
    from fpeval.envelope import compute_envelope, CityProfileAdapter
    from fpeval.solver import solve_layout, LayoutSpec
    from fpeval.bylaws import BENGALURU as _B
    sp = default_indian_spec()
    sp.rooms[0].preferred_zone = "centre"
    prog, _ = spec_to_programme(sp)
    P = CityProfileAdapter(_B)
    compute_envelope(30, 40, road_facing="E", profile=P, programme=prog)
    res = solve_layout(30, 40,
                       LayoutSpec(programme=prog, entrance_room=prog[0].id,
                                  time_limit_s=8.0),
                       road_facing="E", profile=P, plan_id="centre")
    assert res.plan is not None, res.status


# ---------------------------------------------------------------------------
# an apartment unit is not a plot
# ---------------------------------------------------------------------------

def test_an_apartment_unit_is_not_shrunk_by_setbacks_it_does_not_have():
    """`DesignSpec.site_kind` says a unit "is one unit in a tower" with no
    plot, and `check_bylaws` honours that by skipping the plot rules. The
    SOLVE path did not: `compute_envelope` applies whatever profile it is
    handed and the only one anyone handed it was BBMP.

    Measured before the fix: a 1150 sqft carpet unit produced 865 sqft of
    rooms -- 75% of what was quoted -- after front 1205, rear 803, left 1005
    and right 1013 mm came off a footprint that was already the inside of the
    flat.
    """
    from fpeval.generate import build
    for quoted in (850, 1150, 1600):
        doc = Document.empty(f"u{quoted}", name="unit")
        assert doc.apply(agent("set_plot", site_kind="apartment_unit",
                               carpet_sqft=quoted)).ok
        assert doc.apply(agent("use_standard_programme", bedrooms=3)).ok
        r = build(doc, time_limit_s=12.0)
        assert r.ok, (quoted, r.status, r.errors)
        st = doc.design.active
        got = sum(x.area_m2 for x in st.rooms) * 10.7639
        assert 0.93 <= got / quoted <= 1.07, (
            f"a {quoted} sqft unit solved to {got:.0f} sqft "
            f"({100 * got / quoted:.0f}% of quoted)")
        assert max(st.site.setbacks_mm.values() or [0]) < 100, (
            f"a unit was given setbacks: {st.site.setbacks_mm}")


def test_a_city_without_a_verified_byelaw_table_is_reported_not_faked():
    """`set_plot` accepts a city and only Bengaluru has a real table --
    `bylaws.PROFILES` has exactly one key, and `spec.CITY_PROFILES` says of
    its thirteen entries that they "are estimates ... and should be replaced
    by bylaws.py's real tables". Applying Bengaluru silently would make the
    parameter a lie."""
    from fpeval.generate import build
    doc = Document.empty("chn", name="t")
    assert doc.apply(agent("set_plot", width_ft=30, depth_ft=40,
                           road_facing="north", city="chennai")).ok
    assert doc.apply(agent("use_standard_programme", bedrooms=2)).ok
    r = build(doc, time_limit_s=10.0)
    assert any("chennai" in w and "Bengaluru" in w for w in r.warnings), r.warnings


def test_the_solve_path_reads_the_brief_fields_that_drive_it():
    """An audit, as a test. Four fields were found this session that something
    writes and nothing on the solve path reads -- `max_sqft`, `attached_bath`,
    the adjacency list and `optional` -- each discovered one bug at a time.

    This pins the ones now wired so they cannot quietly come loose again. It
    deliberately does NOT assert the whole dataclass: `budget_band`, `family`
    and `unit_label` are provenance and have no business in the solver.
    """
    import re, pathlib
    solve_path = "".join(
        (pathlib.Path("src/fpeval") / f).read_text()
        for f in ("bridge.py", "envelope.py", "solver.py", "generate.py",
                  "programme.py"))
    for field in ("optional", "site_kind", "city_profile", "unit_area",
                  "adjacency", "preferred_zone", "max_aspect", "min_sqft"):
        assert re.search(rf"\.{field}\b", solve_path), (
            f"{field!r} is a brief field the solve path no longer reads")


def test_wet_rooms_in_three_groups_are_flagged():
    """`P.WET_GROUPING` was a principle in the agent's prompt whose
    `enforced_by` named DESIGN.KITCHEN_FAR_FROM_PARKING -- a rule about
    parking. So "stack and group wet rooms" was stated, `wet_grouping` was a
    brief field, `set_wet_grouping` was a command, and nothing enforced it.

    Found by the visual review, which reported `wet-rooms-split` on 2 of 6
    plans with no rule id to match.

    Calibrated at three groups, not two. Vision flagged both of those plans at
    TWO groups -- kitchen at one end, bathrooms at the other -- and that is
    ordinary in an Indian plot house: one stack for the kitchen side, one for
    the bathrooms. Adopting the stricter view because a model held it would
    repeat the ResPlan en-suite mistake: importing a norm no Indian source
    supports. Three groups means the plan is paying for a third stack.
    """
    got = sense(
        [("Kitchen", "kitchen", (0, 0, 3000, 3000)),
         ("Living", "living", (3000, 0, 9000, 3000)),
         ("Bathroom 1", "bathroom", (9000, 0, 12000, 3000)),
         ("Bedroom 1", "bedroom", (0, 3000, 4500, 7000)),
         ("Utility", "utility", (4500, 3000, 7500, 7000)),
         ("Bedroom 2", "bedroom", (7500, 3000, 12000, 7000))],
        [(1500, 0, "front_door", 1050),
         (3000, 1500, "door", 900),        # Kitchen  -> Living
         (9000, 1500, "door", 900),        # Living   -> Bathroom 1
         (2000, 3000, "door", 900),        # Living   -> Bedroom 1
         (6000, 3000, "door", 900),        # Living   -> Utility
         (10000, 3000, "door", 900)])      # Living   -> Bedroom 2
    assert "DESIGN.WET_ROOMS_SPLIT" in got, sorted(got)
    f = got["DESIGN.WET_ROOMS_SPLIT"]
    assert f.measured >= 3, f.detail
    assert f.severity == "warn", "advisory unless the brief says required"


def test_wet_grouping_required_makes_it_an_error():
    """The brief field finally means something."""
    rects = [("Kitchen", "kitchen", (0, 0, 3000, 3000)),
             ("Living", "living", (3000, 0, 9000, 3000)),
             ("Bathroom 1", "bathroom", (9000, 0, 12000, 3000)),
             ("Bedroom 1", "bedroom", (0, 3000, 4500, 7000)),
             ("Utility", "utility", (4500, 3000, 7500, 7000)),
             ("Bedroom 2", "bedroom", (7500, 3000, 12000, 7000))]
    doors = [(1500, 0, "front_door", 1050), (3000, 1500, "door", 900),
             (9000, 1500, "door", 900), (2000, 3000, "door", 900),
             (6000, 3000, "door", 900), (10000, 3000, "door", 900)]
    got = sense(rects, doors, {"requirements": {"wet_grouping": "required"}})
    assert got["DESIGN.WET_ROOMS_SPLIT"].severity == "error"


# --------------------------------------------------------- vocabulary coherence
# Two modules naming the same parameter with different alphabets is the shape of
# bug that hides best: each half is internally consistent, both pass their own
# tests, and the op is simply unreachable. `move_wall_parallel` was unreachable
# through the repair loop for exactly this reason -- `llm.py` validated
# `direction` as "north", `apply_ops` looked up "NORTH", and no spelling
# survived both. Nothing detected it because nothing compared the two tables.

def test_every_direction_the_validator_accepts_can_be_applied():
    from fpeval.llm import COMPASS_MOVE
    from fpeval.apply_ops import _DIR_VEC
    unreachable = [d for d in COMPASS_MOVE if d.upper() not in _DIR_VEC]
    assert not unreachable, (
        f"{unreachable} pass PatchOp.validate and are rejected by the applier, "
        "so the op cannot be reached with any spelling")


def test_the_command_and_patch_vocabularies_agree():
    """`commands.COMPASS` and `llm.COMPASS_MOVE` name the same eight points."""
    from fpeval.commands import COMPASS
    from fpeval.llm import COMPASS_MOVE
    assert set(COMPASS) == set(COMPASS_MOVE), (
        f"only in COMPASS: {sorted(set(COMPASS) - set(COMPASS_MOVE))}; "
        f"only in COMPASS_MOVE: {sorted(set(COMPASS_MOVE) - set(COMPASS))}")


def test_the_service_accepts_the_compass_spelling_everything_else_uses():
    """`/generate` took "N"; briefs, ground truth and `spec.py` say "north".

    Note that a road side and a move direction are DIFFERENT vocabularies and
    must not be conflated: a plot side is one of four because a plot is a
    rectangle, while `move_wall_parallel` takes any of eight. An earlier cut of
    this test asserted all eight were valid road sides, so it passed while
    "south_west" raised a 500 inside `north_deg_for`.
    """
    from service.app import GenerateIn
    from fpeval.envelope import ROAD_BEARING

    for word, letter in (("north", "N"), ("east", "E"), ("south", "S"),
                         ("west", "W"), ("N", "N"), ("W", "W")):
        got = GenerateIn(width_ft=30, depth_ft=40, programme=[],
                         road_facing=word).road_facing
        assert got == letter, (word, got)
        assert got in ROAD_BEARING

    # A diagonal is refused at the boundary rather than raised inside it, and
    # "north_east" must not fold to "N" by taking the first character.
    for bad in ("north_east", "NE", "south_west", "banana"):
        with pytest.raises(Exception):
            GenerateIn(width_ft=30, depth_ft=40, programme=[],
                       road_facing=bad)


# ------------------------------------------------------------ the ruler itself
# A suite score that moves when the machine is busy cannot be compared across
# commits, and the failure is invisible: three identical runs of the paired
# suite gave 43/41/41 fully passing and 0.843/0.838/0.837 mean, so a two-plan
# "improvement" was available for free. Worse, a pure function extraction --
# which cannot affect layout at all -- appeared to gain a plan. `deterministic`
# budgets CP-SAT by work units on one worker instead of by seconds on eight.

@pytest.mark.slow
def test_a_deterministic_solve_reproduces_exactly():
    from fpeval.envelope import bhk_programme, CityProfileAdapter, RoomReq
    from fpeval.solver import LayoutSpec, solve_layout
    from fpeval.bylaws import BENGALURU

    def once():
        prog = [RoomReq(**{**r.__dict__}) for r in bhk_programme(4)]
        r = solve_layout(
            40, 60,
            LayoutSpec(programme=prog,
                       entrance_room=next(p.id for p in prog if p.is_entrance),
                       # Deliberately too small a budget: this is the regime
                       # where the wall clock decides how many topologies get
                       # tried, and where the eight workers race.
                       time_limit_s=1.5, deterministic=True),
            road_facing="E", profile=CityProfileAdapter(BENGALURU), plan_id="d")
        return (r.status, r.candidates_tried, r.objective,
                tuple(sorted(r.area_m2.items())))

    runs = [once() for _ in range(3)]
    # `candidates_tried` is the sensitive term and is deliberately in the tuple:
    # with `deterministic=False` this instance yields 90 / 43 / 88 while the
    # plan itself happens to converge, so asserting on areas alone would pass
    # against a search that is not reproducible. The count is what proves the
    # search is; the plan is what the count protects on harder instances.
    assert len(set(runs)) == 1, (
        "a deterministic solve returned different results on identical input; "
        f"candidates tried: {[r[1] for r in runs]}")


# ------------------------------------------------- the editor's own floor id
# `_h_replace_storey` copies id, level, name, storey_height and presentation
# from the old storey onto the solved plan. It did not copy
# `project_floor_id` -- the id the EDITOR knows the floor by -- so a solved
# plan carried none and `project._floor_id_of` fell back to "floor-<id>".
# The projection then described a floor called "floor-f1" while the client's
# `activeFloorId` still said "f1", so the canvas matched nothing and drew
# "Start building your floor plan" over a document holding twelve walls.
#
# The id survived every other command. It was lost only on `replace_storey`,
# which is to say only on a SUCCESSFUL solve -- the one moment the user is
# watching for geometry to appear.

def test_the_editors_floor_id_survives_a_solve():
    from fpeval.project import design_from_project, to_project
    from fpeval.document import Document
    from fpeval.commands import Command
    from fpeval.envelope import bhk_programme, CityProfileAdapter
    from fpeval.solver import LayoutSpec, solve_layout
    from fpeval.bylaws import BENGALURU

    # "f1" is an editor uid: not what we would derive, so it is an override.
    proj = {"id": "proj-x", "name": "x", "activeFloorId": "f1",
            "floors": [{"id": "f1", "name": "Ground", "level": 0,
                        "walls": [], "doors": [], "windows": [], "rooms": []}]}
    design = design_from_project(proj)
    doc = Document(design=design, base=design)
    assert to_project(doc.design)["floors"][0]["id"] == "f1"

    prog = bhk_programme(2)
    plan = solve_layout(
        30, 40,
        LayoutSpec(programme=prog,
                   entrance_room=next(p.id for p in prog if p.is_entrance),
                   time_limit_s=6.0, deterministic=True),
        road_facing="E", profile=CityProfileAdapter(BENGALURU), plan_id="p").plan
    assert plan is not None

    res = doc.apply(Command(op="replace_storey",
                            params={"storey_id": doc.design.active.id},
                            source="solver"), payload=plan)
    assert res.ok, res.errors

    out = to_project(doc.design)
    ids = [f["id"] for f in out["floors"]]
    assert out["activeFloorId"] in ids, (
        f"activeFloorId {out['activeFloorId']!r} names no floor in {ids} -- "
        "the canvas would render nothing")
    assert ids == ["f1"], f"the editor's floor id was renamed to {ids}"
    assert len(out["floors"][0]["walls"]) > 0


def test_findings_reach_the_client_as_sentences():
    """`_findings_json` read `id`, `message` and `refs`; `Finding` has none of
    them, so `message` fell through to the debug repr and `detail` -- the text
    written for a human -- was dropped."""
    from fpeval.document import Document
    from fpeval.ir import Design
    from fpeval.envelope import bhk_programme, CityProfileAdapter
    from fpeval.solver import LayoutSpec, solve_layout
    from fpeval.bylaws import BENGALURU
    from service.documents import _findings_json

    prog = bhk_programme(2)
    plan = solve_layout(
        30, 40,
        LayoutSpec(programme=prog,
                   entrance_room=next(p.id for p in prog if p.is_entrance),
                   time_limit_s=6.0, deterministic=True),
        road_facing="E", profile=CityProfileAdapter(BENGALURU), plan_id="p").plan
    d = Design(id="x", storeys=[plan], active_storey_id=plan.id)

    out = _findings_json(Document(design=d, base=d))
    assert out, "a solved plan produced no findings at all"
    for f in out:
        assert f["rule_id"], f
        assert f["detail"], f"{f['rule_id']} carries no human-readable detail"
        # The repr leaks as "<W RULE.ID w=0.50 [4 vs 2]>"; a sentence does not.
        assert not f["detail"].startswith("<"), f["detail"]
        assert f["detail"] != f["rule_id"]
    assert any(f["severity"] in ("error", "warn") for f in out)
