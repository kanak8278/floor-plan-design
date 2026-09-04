"""Filling the holes in a brief, and saying out loud which ones were filled.

An agent asked to "build a 2BHK standard size" is not given a plot, a facing,
a storey count or a room list, and refusing until it has all four is the wrong
answer -- "standard size" is a request for us to choose. But choosing silently
is worse than refusing: a plan solved on an invented 30x40 looks exactly as
finished as one solved on the client's real plot, and nothing on the drawing
says which it is.

So every default here is returned as an `Assumption` carrying the value and
the reason, the caller states them, and the user overrides any of them in one
sentence. `policy.AskConfig.never_assume` lists plot dimensions for good
reason; this module does not weaken that rule so much as make the assumption
visible enough to argue with.

## Where the numbers come from

The plot sizes are the modal Indian residential sites, not an average: 20x30,
30x40 and 40x60 are what plots are actually sold as, and 30x40 (1200 sqft) is
the Bengaluru default. They are also the cases `tests/test_integration.py`
solves, so a default here is a size we have measured as feasible rather than
one that reads plausibly -- 20x30 holds a 1BHK and refuses a 3BHK, and the
table reflects that.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Optional

from .spec import (
    BEDROOM_CATEGORIES, DesignSpec, EntranceSpec, ROOM_CATEGORIES,
    bhk_programme,
)

# bedrooms -> (width_ft along the road, depth_ft). Feasibility measured, not
# assumed: see the module docstring.
STANDARD_PLOT_FT: dict[int, tuple[float, float]] = {
    1: (20.0, 30.0),      # 600 sqft
    2: (30.0, 40.0),      # 1200 sqft -- the modal Bengaluru site
    3: (30.0, 40.0),      # the same site; the common 3BHK in Bengaluru
    4: (40.0, 60.0),      # 2400 sqft
    5: (40.0, 60.0),
    6: (50.0, 80.0),
}

# When nobody says how many bedrooms. Two is the modal Indian unit and the
# thing "standard" most often means.
DEFAULT_BEDROOMS = 2
DEFAULT_ROAD_FACING = "north"
DEFAULT_CITY = "bengaluru"

# An apartment unit has no plot, so a footprint has to be derived from the
# quoted carpet area. Measured Bengaluru builder units run close to 5:4.
UNIT_ASPECT = 1.25
# Carpet is the sellable inside; the footprint the solver lays out is larger.
#
# Measured against our OWN output rather than borrowed, because the quantity
# being predicted is what this solver produces. Our rooms are FACES of the
# wall graph, so a room polygon runs to the wall CENTRELINE and already
# contains half of every wall around it -- the sum of room areas is therefore
# close to the footprint, not to RERA carpet, and inflating by a
# carpet-to-builtup ratio double-counts the walls.
#
# The old 1.18 came from the ResPlan-derived unit set, which `brief.py`
# documents as a corpus whose "signals point away from India" -- and it
# overshot: a 1150 sqft unit solved to 1298 sqft of rooms, 113% of what was
# quoted. Calibrated on this solver: footprint 1355 sqft produced 1298 sqft of
# faces, a ratio of 0.958, so the factor that lands on the quoted figure is
# 1/0.958 = 1.044.
CARPET_TO_FOOTPRINT = 1.04
# Room areas -> footprint, when the brief sizes its own rooms. Their sum is
# CLEAR floor; the footprint has to add internal walls and the circulation that
# links them. 1.25 is walls (~8%) plus circulation (~15%), which is what the
# hand-annotated real drawings measure.
PROGRAMME_TO_FOOTPRINT = 1.25


@dataclass(frozen=True)
class Assumption:
    """One fact nobody gave us, the value used, and why that value."""
    field: str
    value: object
    because: str

    def __str__(self) -> str:
        return f"{self.field} = {self.value} ({self.because})"


def bedroom_count(spec: DesignSpec) -> int:
    return sum(1 for r in spec.rooms if r.category in BEDROOM_CATEGORIES)


def resolve(spec: DesignSpec) -> tuple[DesignSpec, list[Assumption]]:
    """A solvable brief, plus every default that had to be invented to get one.

    Never mutates the caller's spec: the brief is what the user reviews and
    edits, and quietly rewriting it during a solve would mean the document no
    longer says what they asked for. Assumptions are the solver's, and they
    stay the solver's until the user adopts one.
    """
    sp = copy.deepcopy(spec)
    made: list[Assumption] = []

    if not sp.rooms:
        n = DEFAULT_BEDROOMS
        sp.rooms = bhk_programme(n)
        made.append(Assumption(
            "programme", f"{n}BHK",
            "no rooms in the brief; used the standard hall, kitchen, "
            f"{n} bedrooms and baths"))

    beds = bedroom_count(sp) or DEFAULT_BEDROOMS

    if sp.storeys < 1:
        sp.storeys = 1
        made.append(Assumption("storeys", 1, "a house is one floor unless said"))

    if not sp.city_profile or sp.city_profile == "generic_in":
        sp.city_profile = DEFAULT_CITY
        made.append(Assumption("city", DEFAULT_CITY,
                               "bye-laws have to come from somewhere; this is "
                               "the profile the service is calibrated on"))

    if sp.site_kind == "apartment_unit":
        w, d, note = _unit_footprint(sp)
        if w and d and not (sp.plot_width_ft and sp.plot_depth_ft):
            sp.plot_width_ft, sp.plot_depth_ft = w, d
            made.append(Assumption("footprint", f"{w:.0f} x {d:.0f} ft", note))
    elif not (sp.plot_width_ft and sp.plot_depth_ft):
        w, d = STANDARD_PLOT_FT.get(beds, STANDARD_PLOT_FT[DEFAULT_BEDROOMS])
        sp.plot_width_ft, sp.plot_depth_ft = w, d
        made.append(Assumption(
            "plot", f"{w:.0f} x {d:.0f} ft ({w * d:.0f} sqft)",
            f"the standard site for a {beds}BHK; say the real dimensions and "
            "it will be re-solved"))

    if not sp.road_facing_side:
        sp.road_facing_side = DEFAULT_ROAD_FACING
        made.append(Assumption("road_facing", DEFAULT_ROAD_FACING,
                               "the facing changes the whole layout, so this "
                               "one is worth correcting if it is wrong"))

    if sp.entrance is None:
        sp.entrance = EntranceSpec()
    if not sp.entrance.side:
        sp.entrance.side = sp.road_facing_side
        made.append(Assumption("entrance", sp.road_facing_side,
                               "the front door faces the road"))

    # A room with no bounds competes for area against rooms that have them and
    # loses, which shows up as one absurdly thin room rather than as an error.
    for r in sp.rooms:
        # `size_stated` is set in `RoomSpec.__post_init__`; by here min_sqft is
        # already filled and the question can no longer be asked.
        info = ROOM_CATEGORIES.get(r.category)
        if info is None:
            continue
        if r.min_sqft is None:
            r.min_sqft = info.min_sqft
        if r.max_sqft is None:
            r.max_sqft = info.max_sqft
        if r.max_aspect is None:
            r.max_aspect = info.max_aspect

    return sp, made


def _unit_footprint(sp: DesignSpec) -> tuple[Optional[float], Optional[float], str]:
    """Width and depth for an apartment unit, from whatever area was quoted."""
    q = sp.unit_area
    # `AreaQuote.resolved_carpet_sqft` is the one place that knows how to read a
    # quote. This function had a second, shorter chain with different factors
    # and NO `saleable_sqft` branch -- so the commonest Indian way to quote a
    # flat ("1150 sq.ft." meaning saleable) fell through to "no area quoted"
    # and got a default 30x40 footprint regardless of what was asked for.
    carpet = q.resolved_carpet_sqft()
    if carpet is not None:
        src = ("the quoted carpet area" if (q.carpet_sqft or q.rera_carpet_sqft)
               else f"the quoted {q.quoted_as or 'gross'} area, less loading")
    else:
        beds = bedroom_count(sp) or DEFAULT_BEDROOMS
        w, d = STANDARD_PLOT_FT.get(beds, STANDARD_PLOT_FT[DEFAULT_BEDROOMS])
        return w, d, (f"no area quoted for the unit; used a {beds}BHK footprint")
    # Balconies are not carpet -- every Indian area table quotes them on a
    # separate line -- and on the real drawings they cantilever off the slab
    # outside the enclosed block. The solver tiles them INSIDE the footprint,
    # so their area has to be added or the programme cannot fit: measured on
    # the Godrej Woods unit, 719 sqft carpet + 76 sqft of balcony was solved
    # against a 748 sqft rectangle and came back infeasible on four groups.
    foot = (carpet + (q.balcony_sqft or 0.0)) * CARPET_TO_FOOTPRINT
    # The quote is a CEILING, not a target to fill. When the brief names its own
    # room sizes and they total less than the quote, draw the smaller house: an
    # architect does not inflate rooms to reach a lease line. Before this, a
    # 2.5BHK brief that specified all ten rooms (932 sqft) against a 1300 sqft
    # quote had 271 sqft of surplus distributed into the rooms, which is where
    # a 424 sqft living room and a passage larger than the hall came from.
    # `size_stated`, not `min_sqft`: `RoomSpec.__post_init__` fills `min_sqft`
    # on every room from the category default, so testing it directly answers
    # "the brief sized its own rooms" YES always -- which shrank a 850 sqft
    # unit's footprint to the 586 sqft of category minimums and made it
    # unsolvable. Same trap the flag itself was written to avoid.
    sized = [r for r in sp.rooms if r.size_stated]
    asked = sum(r.min_sqft or 0.0 for r in sized)
    if asked and len(sized) >= max(3, len(sp.rooms) - 1):
        want = asked * PROGRAMME_TO_FOOTPRINT
        if want < foot:
            foot, src = want, (f"{src}, reduced to the {asked:.0f} sqft the "
                               "brief actually asks for")
    d = (foot / UNIT_ASPECT) ** 0.5
    return round(d * UNIT_ASPECT, 1), round(d, 1), f"derived from {src}"


def summarise(made: list[Assumption]) -> str:
    """The assumption list as one block of prose for the model to relay."""
    if not made:
        return "Nothing was assumed; the brief was complete."
    return ("Assumed, because the brief did not say -- state any of these and "
            "it will be re-solved:\n"
            + "\n".join(f"  - {a}" for a in made))


def spec_to_brief(spec: DesignSpec) -> dict:
    """A brief in the shape `rules.validate` reads.

    Two separate things were lost by passing `brief=None` from the agent's
    solve path, and neither announced itself.

    `check_brief` returns immediately without `requirements`, so the entire
    BRIEF family -- "did we build what was actually asked for" -- was silent
    on every plan the agent produced. A solve that dropped a bedroom scored
    the same as one that did not.

    And `check_bylaws` needs `site_kind` to know a plot from an apartment
    unit. Told nothing, it applies setback and coverage rules to a unit inside
    a tower, which has no plot and no setbacks of its own. Measured on the
    suite, that alone was ten false errors.
    """
    rooms: dict[str, int] = {}
    for r in spec.rooms:
        if r.optional:
            continue
        rooms[r.category] = rooms.get(r.category, 0) + 1
    adjacent = [[a.a, a.b] for a in spec.adjacency if a.kind == "required"]
    not_adjacent = [[a.a, a.b] for a in spec.adjacency if a.kind == "prohibited"]
    zones = {r.category: r.preferred_zone for r in spec.rooms if r.preferred_zone}
    area = None
    if spec.plot_width_ft and spec.plot_depth_ft:
        area = spec.plot_width_ft * spec.plot_depth_ft
    return {
        "requirements": {
            "rooms": rooms,
            "adjacent": adjacent,
            "not_adjacent": not_adjacent,
            "must_place": [],
            "place_in": {},
            "vastu_zones": zones,
            "attached_bath": sum(1 for r in spec.rooms if r.attached_bath) or None,
            # Drives DESIGN.WET_ROOMS_SPLIT's severity. `set_wet_grouping` has
            # been in the vocabulary the whole time with nothing reading it.
            "wet_grouping": spec.wet_grouping,
        },
        "site_kind": spec.site_kind,
        "plot_area_sqft": area,
        "habitable_floors": max(1, int(spec.storeys)),
    }
