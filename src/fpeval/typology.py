"""Building typology: what "correctly connected" means depends on the building.

A kitchen that opens straight into the living room is normal in a 1,100 sqft
apartment and wrong in a 4,000 sqft villa, where a client who asks for a formal
living room means one that guests see and the family's daily mess does not. So
adjacency expectations are per-typology data, not universal rules.

Sources: the Bengaluru builder unit plans ingested into `corpus/india/` (Brigade,
Prestige, Godrej, Divyasree) for apartment conventions, and standard Indian
plot-housing practice for the rest. Every entry is a DEFAULT a client can
override -- suite case vastu-07 exists precisely because a client's own
consultant outranks our table.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal

Kind = Literal["apartment_unit", "independent_house", "villa", "duplex",
               "rental_floors", "studio"]

# Relation vocabulary, deliberately small:
#   direct   -- a door between them
#   open     -- no wall, one continuous space
#   near     -- share a boundary, door optional
#   separate -- must NOT have a direct door
#   private  -- must not be on a route to anywhere else
Relation = Literal["direct", "open", "near", "separate"]


@dataclass(frozen=True)
class AdjRule:
    a: str
    b: str
    relation: Relation
    weight: float = 1.0          # 1.0 = expected; below 0.5 = nice to have
    why: str = ""


@dataclass
class Typology:
    kind: Kind
    display: str
    # Rooms that count as circulation for this typology. A leaf room should hang
    # off one of these rather than off another leaf.
    circulation: tuple[str, ...]
    expect: tuple[AdjRule, ...]
    # The sequence a visitor walks on entry. Checked as an ordering, not a path.
    entry_sequence: tuple[str, ...] = ()
    # Typical storey count, used only to sanity-check a brief.
    storeys: tuple[int, int] = (1, 2)
    max_depth_from_entrance: int = 3
    notes: str = ""


_COMMON = (
    AdjRule("kitchen", "utility", "direct", 1.0,
            "the washing machine and cylinder live off the kitchen"),
    AdjRule("kitchen", "bathroom", "separate", 1.0,
            "NBC: a WC must not open into a kitchen"),
    AdjRule("pooja", "bathroom", "separate", 1.0,
            "a shrine must not share a wall or door with a toilet"),
    AdjRule("pooja", "kitchen", "separate", 0.4,
            "usually kept apart, though less strictly than from a toilet"),
    # Mirrors of the three prohibitions added to `topology.UNIVERSAL` from the
    # review of the 89 suite plans. They have to exist in BOTH tables: the
    # solver reads `topology.Scenario.prefs`, while `TYPO.FORBIDDEN_ADJACENCY`
    # in `rules.py` reads `Typology.expect` from here. Carrying it in one place
    # only means the solver avoids a defect the validator cannot name.
    AdjRule("kitchen", "bedroom", "separate", 1.0,
            "cooking heat, smell and traffic inside a sleeping room. Measured: "
            "3 of 200 real ResPlan plans (1.5%), 24 of 89 of ours (27%)"),
    AdjRule("bathroom", "bathroom", "separate", 1.0,
            "a toilet whose only door is into another toilet leaves neither "
            "usable privately. Measured: 0 of 200 real ResPlan plans, "
            "13 of 89 of ours"),
    AdjRule("pooja", "stair", "separate", 1.0,
            "a shrine under or beside a staircase"),
)

TYPOLOGIES: dict[str, Typology] = {
    # Compact, one continuous public zone. Measured across the ingested builder
    # plans: living and dining are a single labelled space ("LIVING / DINING") or
    # directly open, and the kitchen opens off it with a utility behind.
    "apartment_unit": Typology(
        kind="apartment_unit", display="Apartment unit",
        circulation=("living", "dining", "foyer"),
        expect=_COMMON + (
            AdjRule("living", "dining", "open", 1.0,
                    "sold as one living-cum-dining space at this size"),
            AdjRule("dining", "kitchen", "direct", 1.0, "serving distance"),
            AdjRule("foyer", "living", "direct", 0.8, "entry lands in the living"),
            AdjRule("living", "balcony", "direct", 0.7, "the balcony is the view"),
        ),
        entry_sequence=("foyer", "living"),
        storeys=(1, 1),
        max_depth_from_entrance=3,
        notes="No plot, no outdoor programme; bounded by the quoted area."),

    # The default plot house. Hall is the hub everything opens off.
    "independent_house": Typology(
        kind="independent_house", display="Independent house",
        circulation=("living", "dining", "foyer"),
        expect=_COMMON + (
            AdjRule("living", "dining", "open", 0.8,
                    "usually one space, but a separating wall is acceptable"),
            AdjRule("dining", "kitchen", "direct", 1.0, "serving distance"),
            AdjRule("sitout", "foyer", "direct", 0.7, "covered arrival"),
            AdjRule("foyer", "living", "direct", 0.9, "hall is the hub"),
            AdjRule("parking", "sitout", "near", 0.5, "walk from car to door"),
        ),
        entry_sequence=("sitout", "foyer", "living"),
        storeys=(1, 3),
        max_depth_from_entrance=3),

    # Larger plot: the formal/family split is the defining feature and the thing
    # a client is paying for. Dining becomes its own room.
    "villa": Typology(
        kind="villa", display="Villa",
        circulation=("living", "dining", "foyer"),
        expect=_COMMON + (
            AdjRule("foyer", "living", "direct", 1.0,
                    "guests arrive into the formal living, not the family space"),
            AdjRule("living", "dining", "near", 0.9,
                    "adjacent but separable -- NOT one open space at this size"),
            AdjRule("dining", "kitchen", "direct", 1.0, "serving distance"),
            AdjRule("kitchen", "store", "direct", 0.6, "dry store off the kitchen"),
            AdjRule("living", "bedroom", "separate", 0.8,
                    "a bedroom door should not open into the formal living"),
            AdjRule("sitout", "foyer", "direct", 0.6, "portico arrival"),
        ),
        entry_sequence=("sitout", "foyer", "living", "dining"),
        storeys=(1, 3),
        max_depth_from_entrance=4,
        notes="Formal vs family living is the point; do not merge them."),

    # Public downstairs, private upstairs. The stair belongs to circulation.
    "duplex": Typology(
        kind="duplex", display="Duplex",
        circulation=("living", "dining", "foyer", "stair"),
        expect=_COMMON + (
            AdjRule("living", "stair", "direct", 1.0,
                    "the stair rises from the hall, not from a bedroom"),
            AdjRule("living", "dining", "open", 0.8, "one public zone below"),
            AdjRule("dining", "kitchen", "direct", 1.0, "serving distance"),
            AdjRule("stair", "bedroom", "separate", 0.7,
                    "the landing serves bedrooms; a stair must not land inside one"),
        ),
        entry_sequence=("sitout", "foyer", "living"),
        storeys=(2, 3),
        max_depth_from_entrance=4),

    # One let-out unit per floor. Each needs its own kitchen and its own door;
    # a rule capping kitchens at one would wrongly reject this.
    "rental_floors": Typology(
        kind="rental_floors", display="Rental floors",
        circulation=("living", "foyer", "stair"),
        expect=_COMMON + (
            AdjRule("stair", "foyer", "direct", 1.0,
                    "tenants reach their floor without entering another unit"),
            AdjRule("living", "kitchen", "direct", 0.9, "compact unit"),
        ),
        entry_sequence=("stair", "foyer", "living"),
        storeys=(2, 4),
        max_depth_from_entrance=3,
        notes="Multiple kitchens are correct here, one per unit."),

    "studio": Typology(
        kind="studio", display="Studio",
        circulation=("living",),
        expect=_COMMON + (
            AdjRule("living", "kitchen", "open", 1.0, "open kitchen by definition"),
            AdjRule("living", "bedroom", "open", 0.6, "often undivided"),
        ),
        entry_sequence=("living",),
        storeys=(1, 1),
        max_depth_from_entrance=2),
}


def get(kind: str) -> Typology:
    return TYPOLOGIES.get(kind, TYPOLOGIES["independent_house"])


def infer(*, site_kind: str = "plot", plot_sqft: float | None = None,
          storeys: int = 1, bedrooms: int = 0,
          kitchens: int = 1, has_two_living: bool = False) -> str:
    """Best-guess typology from a brief. Explicit beats inferred; this is only
    for briefs that do not say."""
    if site_kind == "apartment_unit":
        return "studio" if bedrooms <= 1 and kitchens <= 1 else "apartment_unit"
    if kitchens > 1 and storeys > 1:
        return "rental_floors"
    if plot_sqft and plot_sqft >= 3600 and (has_two_living or bedrooms >= 5):
        return "villa"
    if storeys >= 2:
        return "duplex"
    return "independent_house"
