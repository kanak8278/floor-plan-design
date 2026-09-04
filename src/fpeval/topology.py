"""Zones, bathroom kinds, and a weighted adjacency preference matrix per scenario.

Built because the solver had no relational vocabulary at all. Measured against
400 real ResPlan plans versus 90 of ours -- living is the core in 97.0% of real
plans and 3.3% of ours; median public_score +1.426 against -0.460. Ours had no
spatial hierarchy whatsoever. Those figures are the targets in `SYNTAX_TARGETS`.

**What ResPlan can and cannot settle.** It is South Asian, so the region is
right, but it labels only six room types -- living, kitchen, bedroom, bathroom,
balcony, storage (`resplan.py:52`) -- and it is unit-level and single-floor,
median 110 m². So it settles the *skeleton*: is the living room the core, does
privacy grade with depth. It settles nothing about pooja, sitout, utility,
store, foyer, servant, parking or the compound -- which is to say nothing about
the part of this file that is Indian rather than generic. Those preferences come
from Indian plot-housing practice and the Bengaluru builder plans behind
`typology.py`. Do not reach for a ResPlan figure to justify a rule about a room
ResPlan cannot see.

Three design choices worth stating:

* **Weighted preferences, not binary pairs.** One signed weight covers
  required / preferred / neutral / discouraged / forbidden. A list of required
  pairs at a flat penalty cannot express "mildly discouraged".
* **Scenario = typology x size band.** A 20x30 2BHK cannot have a separate
  dining room and a 50x80 villa must. One table for both is wrong in one
  direction or the other.
* **Indian-centric by construction, not by calibration.** The prohibitions are
  the ones Indian practice holds -- a WC off the kitchen, a shrine sharing a
  wall with a toilet or sitting under the stair, a toilet off the dining, a
  bedroom door into the kitchen -- and the typologies are the ones that exist
  on Indian plots, including rental floors and the joint-family house with two
  kitchens on one floor. Where a rule has no measured backing its `why` says
  so rather than borrowing a foreign number.

Stilt parking is deliberately absent from the scenarios: it is an area and
height question, and `bylaws.py` carries it as `stilt+3` / `stilt+4` with
`max_habitable_floors` excluding the stilt. Nothing topological changes.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal

Zone = Literal["public", "private", "service", "circulation", "outdoor"]
Relation = Literal["direct", "open", "near", "separate", "any"]

# `rules.py` reads this with `.get()`, so a category missing here is not an error
# -- it is silently invisible to every ZONE rule. `passage` was missing while all
# 89 suite plans contain one, so the corridor counted towards no zone at all.
# Covers the union of `roomtypes.py` keys and `spec.py` CATEGORIES, checked
# rather than assumed. Note the two vocabularies disagree on spelling --
# roomtypes says `sitout` and `stair`, spec says `sit_out` and `staircase` --
# so both spellings are listed until that drift is resolved.
ZONE_OF: dict[str, Zone] = {
    # public
    "living": "public", "dining": "public", "hall": "public",
    "pooja": "public",
    # `family` is not yet a real type: it needs a `roomtypes.py` entry and a
    # `spec.py` category before the villa's formal/family prefs below can fire.
    # Listed here so the zone is right the day it lands.
    "family": "public",
    # private
    "bedroom": "private", "master_bedroom": "private",
    "guest_bedroom": "private", "study": "private", "office": "private",
    "servant": "private", "dress": "private",
    # service
    "kitchen": "service", "utility": "service", "store": "service",
    "bathroom": "service", "toilet": "service", "powder": "service",
    "handwash": "service", "shaft": "service",
    # circulation -- every one of these was absent, including `passage`, which
    # all 89 suite plans contain
    "foyer": "circulation", "stair": "circulation", "staircase": "circulation",
    "passage": "circulation", "corridor": "circulation",
    # outdoor
    "sitout": "outdoor", "sit_out": "outdoor", "balcony": "outdoor",
    "patio": "outdoor", "terrace": "outdoor", "parking": "outdoor",
    "garage": "outdoor", "landscape": "outdoor",
}

# A pooja room is public in the sense that guests use it, but Vastu pins it to
# the NE corner, which is usually nowhere near the living room. Counting it as a
# public room therefore reports a fragmented public zone on plans that are
# correct. It keeps its zone for reasoning and is excluded from the contiguity
# test only.
ZONE_FRAGMENTATION_EXEMPT: frozenset[str] = frozenset({"pooja"})

# Measured on 400 real ResPlan plans -- South Asian, so the right region, but
# read the scope limit before trusting a number here on a plot house:
#
#   * ResPlan labels SIX room types (`resplan.py:52` -- living, kitchen, bedroom,
#     bathroom, balcony, storage). Pooja, sitout, utility, store, foyer and study
#     are unrepresentable in it (`roomtypes.py:153`). Everything that makes this
#     file Indian rather than generic is therefore UNCALIBRATED: these figures
#     validate the skeleton, not the vocabulary.
#   * ResPlan is unit-level and single-floor, median 110 m². These are flats. A
#     30x40 plot house carries a sitout, parking and a compound that no ResPlan
#     plan has.
#
# So they are the apartment/compact baseline, not a universal target. Scenarios
# that are meant to read differently override them via `Scenario.syntax_targets`
# -- a villa splitting formal from family living HAS two weaker public cores, by
# design, and must not be graded against a single-core figure.
SYNTAX_TARGETS = {
    "living_is_core": 0.95,      # fraction of plans where living is most integrated
    "living_relative_min": 2.00,  # real median 2.53
    "privacy_gradient_max": 0.55,  # real median 0.39
    "public_score_min": 0.80,     # real median +1.426
}

# Formal + family living is the point of these typologies, so integration spreads
# across two public rooms instead of concentrating in one. Relaxed, not dropped.
SYNTAX_TARGETS_TWO_PUBLIC = {
    "living_is_core": 0.95,
    "living_relative_min": 1.40,
    "privacy_gradient_max": 0.70,
    "public_score_min": 0.50,
}


# --------------------------------------------------------------- bathroom kinds
# The vocabulary a client actually uses, formalised as door-graph signatures.
# "3BHK with all attached baths" and "3BHK with a common bath" are different
# topologies, not different labels, and the difference is checkable.
BathKind = Literal[
    "attached",        # exactly one door, from one bedroom. En-suite, private to it.
    "shared_attached",  # two doors, one from each of two bedrooms, none to
                        # circulation. The Jack-and-Jill.
    "common",          # doors only from circulation. Serves the household and guests.
    "common_attached",  # a door from circulation AND a door from a bedroom. Both
                        # at once -- what a client means by "common but attached".
    "detached",        # no bedroom adjacency at all; reached from circulation,
                        # often a servant or outside WC.
    "powder",          # WC-only, off circulation, shallow. Guest toilet.
    "unreachable",     # no door. A defect.
]

# Same omission as ZONE_OF had: a bath opening off a `passage` scored
# from_circulation=False and reached "common" down the fallback branch instead of
# the circulation branch. Every suite plan routes its baths off a passage.
CIRCULATION = {"foyer", "stair", "staircase", "living", "dining", "hall",
               "passage", "corridor"}
PRIVATE_CATS = {"bedroom", "master_bedroom", "guest_bedroom", "study",
                "office", "servant", "dress"}


@dataclass
class BathTopology:
    room_id: str
    name: str
    kind: BathKind
    bedrooms: tuple[str, ...]        # bedrooms with a door into it
    from_circulation: bool
    depth_from_entrance: int | None
    detail: str = ""


def classify_bathroom(bid: str, name: str, neighbours: set[str],
                      cat_of: dict[str, str], depth: int | None = None,
                      wc_only: bool = False,
                      adjacent_bedrooms: set[str] | None = None) -> BathTopology:
    """Classify one bathroom from its door neighbours.

    `adjacent_bedrooms` are bedrooms it merely *touches* without a door; used to
    tell a genuinely detached WC from one that simply has no door yet.
    """
    beds = tuple(sorted(n for n in neighbours if cat_of.get(n) in PRIVATE_CATS))
    circ = any(cat_of.get(n) in CIRCULATION for n in neighbours)

    if not neighbours:
        kind: BathKind = "unreachable"
        detail = "no door at all"
    elif beds and circ:
        kind = "common_attached"
        detail = (f"reached from circulation and from {len(beds)} bedroom(s); "
                  "serves guests and one bedroom privately")
    elif len(beds) >= 2 and not circ:
        kind = "shared_attached"
        detail = (f"shared between {', '.join(beds)} with no access from "
                  "circulation — a Jack-and-Jill")
    elif len(beds) == 1 and not circ:
        kind = "attached"
        detail = f"en-suite to {beds[0]}, private to it"
    elif circ and not beds:
        if wc_only and (depth is not None and depth <= 2):
            kind = "powder"
            detail = "WC-only off circulation near the entrance — a guest toilet"
        elif adjacent_bedrooms is not None and not adjacent_bedrooms:
            # `detached` needs POSITIVE evidence that no bedroom is even
            # adjacent. Absence of information is not evidence: defaulting to
            # detached mislabelled every ordinary common bath.
            kind = "detached"
            detail = "no bedroom adjacency at all; a separate or service toilet"
        else:
            kind = "common"
            detail = "reached from circulation only; serves the household"
    else:
        kind = "common"
        detail = "reached from circulation"
    return BathTopology(room_id=bid, name=name, kind=kind, bedrooms=beds,
                        from_circulation=circ, depth_from_entrance=depth,
                        detail=detail)


# ----------------------------------------------------------- preference matrix
@dataclass(frozen=True)
class Pref:
    """A signed adjacency preference.

    weight  +1.0  required   -- must have a door
            +0.6  preferred  -- should, and the scorer rewards it
            +0.3  nice
             0.0  neutral
            -0.5  discouraged
            -1.0  forbidden  -- must NOT have a door
    """
    a: str
    b: str
    weight: float
    relation: Relation = "direct"
    why: str = ""
    # A prohibition the solver must EXCLUDE rather than price. Reserved for law
    # and hygiene. `solver.NBC_FORBIDDEN` already carries the principle for the
    # one pair it hard-codes -- "a code prohibition is not a cost: exclude the
    # edge, and if that strands a room the TOPOLOGY is wrong" -- but
    # `solver_pairs` used to hand EVERY -1.0 preference over as a hard
    # exclusion, taste included. Measured on a 50x80 5BHK villa: the
    # living-bedroom rule became five hard exclusions, no bedroom could hang
    # off the living room, and with no foyer or stair in the programme the door
    # assignment had nowhere left to put them -- so the solver fell back to
    # topologies with an interior habitable room and the plan gained
    # DESIGN.NO_WINDOW and NBC.VENTILATION_HABITABLE. Buying design quality
    # with legality is exactly what the 2e7 > 1e7 priority order exists to
    # prevent, and a hard exclusion routes around that order entirely.
    hard: bool = False

    @property
    def required(self) -> bool: return self.weight >= 0.9

    @property
    def forbidden(self) -> bool: return self.weight <= -0.9


# Universal, every scenario. Law and hygiene, not taste.
UNIVERSAL: tuple[Pref, ...] = (
    Pref("kitchen", "bathroom", -1.0, "separate",
         "NBC: a WC must not open into a kitchen", hard=True),
    Pref("pooja", "bathroom", -1.0, "separate",
         "a shrine must not share a door or wall with a toilet", hard=True),
    Pref("dining", "bathroom", -1.0, "separate",
         "rejected outright in Indian practice", hard=True),
    Pref("kitchen", "utility", 1.0, "direct",
         "the machine and the cylinder live off the kitchen"),
    Pref("kitchen", "store", 0.6, "direct", "dry store within reach"),
    Pref("bedroom", "bedroom", -0.5, "separate",
         "bedrooms opening into each other costs privacy"),
    Pref("pooja", "kitchen", -0.3, "separate", "usually kept apart"),
    # --- added from the review of the 89 plans in out/suite_svg. Each was
    # --- absent, and each turned up as a real defect at scale.
    Pref("pooja", "stair", -1.0, "separate",
         "a shrine under or beside a staircase; the one prohibition no Indian "
         "client waives", hard=True),
    Pref("kitchen", "bedroom", -1.0, "separate",
         "cooking heat, smell and traffic inside a sleeping room. Measured: 3 of "
         "200 real ResPlan plans (1.5%), 24 of 89 of ours (27%), and 15 of ours "
         "made it the kitchen's ONLY way in. Priced, not excluded: a hard edge "
         "here strands the kitchen on tight plots"),
    Pref("bathroom", "bathroom", -1.0, "separate",
         "a toilet whose only door is into another toilet; neither can then be "
         "used privately. Measured: 0 of 200 real ResPlan plans, 13 of 89 of ours"),
)


@dataclass
class Scenario:
    key: str
    display: str
    typology: str
    size_band: str                       # compact | standard | mid | large
    prefs: tuple[Pref, ...]
    circulation: tuple[str, ...]
    entry_sequence: tuple[str, ...] = ()
    max_depth: int = 3
    # Rooms this scenario expects to exist; absence is a programme gap, not an error.
    expects: tuple[str, ...] = ()
    # More than one kitchen is CORRECT here, so #54 must not fire. Previously
    # inferred from `kind == "rental_floors"`, which missed the joint-family
    # house: two kitchens on ONE floor is not a rental building and was rejected.
    multi_kitchen: bool = False
    # Overrides SYNTAX_TARGETS where this scenario is meant to read differently.
    syntax_targets: dict[str, float] | None = None
    notes: str = ""

    def matrix(self) -> dict[frozenset, Pref]:
        m: dict[frozenset, Pref] = {}
        for p in UNIVERSAL + self.prefs:      # scenario overrides universal
            m[frozenset((p.a, p.b))] = p
        return m

    def weight(self, a: str, b: str) -> float:
        p = self.matrix().get(frozenset((a, b)))
        return p.weight if p else 0.0


def _apt(band: str, extra: tuple[Pref, ...] = (), expects=()) -> Scenario:
    return Scenario(
        key=f"apartment_{band}", display=f"Apartment unit ({band})",
        typology="apartment_unit", size_band=band,
        prefs=(Pref("living", "dining", 1.0, "open",
                    "sold as one living-cum-dining space"),
               Pref("dining", "kitchen", 1.0, "direct", "serving distance"),
               Pref("foyer", "living", 0.8, "direct", "entry lands in the living"),
               Pref("living", "balcony", 0.6, "direct", "the balcony is the view"),
               Pref("living", "bedroom", -0.3, "separate",
                    "acceptable in a compact unit, not ideal")) + extra,
        circulation=("living", "dining", "foyer"),
        entry_sequence=("foyer", "living"), max_depth=3, expects=expects)


SCENARIOS: dict[str, Scenario] = {
    "studio": Scenario(
        key="studio", display="Studio", typology="studio", size_band="compact",
        prefs=(Pref("living", "kitchen", 1.0, "open", "open kitchen by definition"),
               Pref("living", "bedroom", 0.6, "open", "often undivided"),
               Pref("living", "bathroom", 0.3, "direct", "only one route exists")),
        circulation=("living",), entry_sequence=("living",), max_depth=2,
        expects=("living", "kitchen", "bathroom")),

    "apartment_compact": _apt("compact", expects=("living", "kitchen", "bathroom", "balcony")),
    "apartment_standard": _apt("standard",
        extra=(Pref("kitchen", "utility", 1.0, "direct", "utility balcony behind"),),
        expects=("living", "dining", "kitchen", "utility", "bathroom", "balcony")),
    "apartment_large": _apt("large",
        extra=(Pref("living", "dining", 0.6, "near",
                    "at this size they separate; do not force one space"),
               Pref("foyer", "living", 1.0, "direct", "a real foyer exists"),
               Pref("living", "bedroom", -0.6, "separate",
                    "a bedroom door in the formal living is a fault at this size")),
        expects=("foyer", "living", "dining", "kitchen", "utility", "store",
                 "bathroom", "balcony")),

    # 20x30 and similar. No dining room; the hall does both jobs.
    "house_compact": Scenario(
        key="house_compact", display="Compact house (<= 800 sqft plot)",
        typology="independent_house", size_band="compact",
        prefs=(Pref("living", "kitchen", 0.8, "direct",
                    "no room for a dining; the hall serves"),
               Pref("living", "bedroom", -0.2, "separate",
                    "unavoidable at this size"),
               Pref("sitout", "living", 0.4, "direct", "front step")),
        circulation=("living", "foyer"), entry_sequence=("sitout", "living"),
        max_depth=2, expects=("living", "kitchen", "bathroom"),
        notes="Relaxed NBC minima apply; deviation must be declared."),

    # 30x40 / 30x50. The canonical case.
    "house_standard": Scenario(
        key="house_standard", display="Independent house (standard plot)",
        typology="independent_house", size_band="standard",
        prefs=(Pref("living", "dining", 0.8, "open",
                    "usually one space; a wall is acceptable"),
               Pref("dining", "kitchen", 1.0, "direct", "serving distance"),
               Pref("foyer", "living", 0.9, "direct", "the hall is the hub"),
               Pref("sitout", "foyer", 0.7, "direct", "covered arrival"),
               Pref("parking", "sitout", 0.5, "near", "car to door"),
               Pref("living", "bedroom", -0.4, "separate",
                    "prefer bedrooms off a passage, not off the hall")),
        circulation=("living", "dining", "foyer"),
        entry_sequence=("sitout", "foyer", "living"), max_depth=3,
        expects=("living", "kitchen", "bathroom", "parking")),

    # 40x60. Dining becomes its own room, utility appears.
    "house_mid": Scenario(
        key="house_mid", display="Independent house (mid plot)",
        typology="independent_house", size_band="mid",
        prefs=(Pref("living", "dining", 0.7, "near",
                    "adjacent but separable at this size"),
               Pref("dining", "kitchen", 1.0, "direct", "serving distance"),
               Pref("foyer", "living", 1.0, "direct", "a real foyer"),
               Pref("kitchen", "utility", 1.0, "direct", "utility yard behind"),
               Pref("living", "bedroom", -0.6, "separate",
                    "bedrooms belong off a passage"),
               Pref("sitout", "foyer", 0.7, "direct", "portico"),
               Pref("parking", "sitout", 0.6, "near", "car to door"),
               Pref("landscape", "living", 0.4, "near",
                    "the garden is what the living room looks at"),
               Pref("landscape", "sitout", 0.5, "near",
                    "the sitout faces the garden, not the compound wall")),
        circulation=("living", "dining", "foyer"),
        entry_sequence=("sitout", "foyer", "living", "dining"), max_depth=3,
        expects=("foyer", "living", "dining", "kitchen", "utility", "store",
                 "parking", "sitout")),

    # 50x80+. Formal/family split is the defining feature.
    "villa": Scenario(
        key="villa", display="Villa (large plot)",
        typology="villa", size_band="large",
        prefs=(Pref("foyer", "living", 1.0, "direct",
                    "guests arrive into the formal living"),
               Pref("living", "dining", 0.7, "near",
                    "adjacent but separable -- NOT one open space"),
               Pref("dining", "kitchen", 1.0, "direct", "serving distance"),
               Pref("kitchen", "store", 0.8, "direct", "dry store off the kitchen"),
               Pref("kitchen", "utility", 1.0, "direct", "service yard"),
               Pref("living", "bedroom", -1.0, "separate",
                    "a bedroom door must not open into the formal living"),
               Pref("pooja", "foyer", 0.5, "near", "shrine near arrival"),
               Pref("sitout", "foyer", 0.6, "direct", "portico"),
               Pref("parking", "sitout", 0.6, "near", "porch to door"),
               # The formal/family split the notes call the point of the
               # typology. It was stated in prose and expressible nowhere: there
               # was no `family` room and `expects` listed one living.
               Pref("family", "living", -0.4, "separate",
                    "the family living is what the formal living is NOT; a door "
                    "between them puts guests in the family's room"),
               Pref("family", "dining", 0.6, "near",
                    "the family sits where it eats"),
               Pref("family", "bedroom", 0.3, "near",
                    "the family living belongs on the bedroom side")),
        circulation=("living", "dining", "foyer", "stair"),
        entry_sequence=("sitout", "foyer", "living", "dining"), max_depth=4,
        expects=("foyer", "living", "family", "dining", "kitchen", "utility",
                 "store", "pooja", "parking", "sitout", "stair"),
        syntax_targets=SYNTAX_TARGETS_TWO_PUBLIC,
        notes="Formal vs family living is the point; do not merge them."),

    "duplex": Scenario(
        key="duplex", display="Duplex (G+1)",
        typology="duplex", size_band="standard",
        prefs=(Pref("living", "stair", 1.0, "direct",
                    "the stair rises from the hall, never from a bedroom"),
               Pref("living", "dining", 0.8, "open", "one public zone below"),
               Pref("dining", "kitchen", 1.0, "direct", "serving distance"),
               Pref("stair", "bedroom", -0.8, "separate",
                    "the landing serves bedrooms; it must not land inside one"),
               Pref("foyer", "living", 0.9, "direct", "hall is the hub")),
        circulation=("living", "dining", "foyer", "stair"),
        entry_sequence=("sitout", "foyer", "living"), max_depth=4,
        expects=("living", "dining", "kitchen", "stair", "bathroom"),
        syntax_targets=SYNTAX_TARGETS_TWO_PUBLIC),

    "rental_floors": Scenario(
        key="rental_floors", display="Rental floors",
        typology="rental_floors", size_band="standard",
        prefs=(Pref("stair", "foyer", 1.0, "direct",
                    "tenants reach their floor without crossing another unit"),
               Pref("living", "kitchen", 0.9, "direct", "compact unit"),
               Pref("living", "stair", -0.5, "separate",
                    "a common stair should not open into a private living room")),
        circulation=("living", "foyer", "stair"),
        entry_sequence=("stair", "foyer", "living"), max_depth=3,
        expects=("living", "kitchen", "bathroom", "stair"),
        multi_kitchen=True,
        notes="Multiple kitchens are correct here, one per unit."),

    # ---------- 7. joint family: two kitchens on ONE floor ----------
    # Two married brothers or a parent generation cooking separately under one
    # roof, sharing the living room. Common in Indian plot housing and it was
    # unrepresentable: `resolve()` only reached `rental_floors` when
    # storeys > 1, so this fell to a house scenario and #54
    # DESIGN.MULTIPLE_KITCHENS rejected a correct plan as an error.
    "joint_family": Scenario(
        key="joint_family", display="Joint family house (shared living)",
        typology="independent_house", size_band="mid",
        prefs=(Pref("living", "dining", 0.7, "near", "the shared public zone"),
               Pref("dining", "kitchen", 1.0, "direct", "serving distance"),
               Pref("kitchen", "kitchen", 0.4, "near",
                    "two kitchens share a plumbing and gas line if adjacent"),
               Pref("kitchen", "utility", 1.0, "direct", "one yard can serve both"),
               Pref("foyer", "living", 0.9, "direct", "one shared arrival"),
               Pref("living", "bedroom", -0.5, "separate",
                    "each family's bedrooms belong off their own passage"),
               Pref("sitout", "foyer", 0.6, "direct", "covered arrival"),
               Pref("parking", "sitout", 0.6, "near", "car to door")),
        circulation=("living", "dining", "foyer"),
        entry_sequence=("sitout", "foyer", "living"), max_depth=3,
        expects=("foyer", "living", "dining", "kitchen", "utility", "store",
                 "pooja", "parking", "sitout"),
        multi_kitchen=True,
        syntax_targets=SYNTAX_TARGETS_TWO_PUBLIC,
        notes="Two kitchens on one floor is the definition, not a defect. "
              "Distinct from rental_floors: the living zone is SHARED."),
}


def size_band(plot_sqft: float | None, carpet_sqft: float | None = None) -> str:
    a = plot_sqft or (carpet_sqft * 1.6 if carpet_sqft else None)
    if a is None:
        return "standard"
    if a <= 800:
        return "compact"
    if a <= 1600:
        return "standard"
    if a <= 3200:
        return "mid"
    return "large"


def resolve(*, site_kind: str = "plot", plot_sqft: float | None = None,
            carpet_sqft: float | None = None, storeys: int = 1,
            bedrooms: int = 0, kitchens: int = 1,
            has_two_living: bool = False) -> Scenario:
    """Pick the scenario. Explicit typology beats this; it is for briefs that
    do not say."""
    band = size_band(plot_sqft, carpet_sqft)
    if site_kind == "apartment_unit":
        if bedrooms <= 1 and (carpet_sqft or 0) <= 650:
            return SCENARIOS["studio"]
        return SCENARIOS[f"apartment_{band if band in ('compact','large') else 'standard'}"]
    if kitchens > 1 and storeys > 1:
        return SCENARIOS["rental_floors"]
    if kitchens > 1:
        # One floor, two kitchens: a joint family, not a rental building.
        return SCENARIOS["joint_family"]
    if band == "large" or (has_two_living and bedrooms >= 5):
        return SCENARIOS["villa"]
    if storeys >= 2:
        return SCENARIOS["duplex"]
    return SCENARIOS[{"compact": "house_compact", "standard": "house_standard",
                      "mid": "house_mid", "large": "villa"}[band]]


def open_pairs(prog, scenario: Scenario) -> list[tuple[str, str]]:
    """Room-id pairs the scenario wants as one continuous space.

    `solver_pairs` folds `relation="open"` into `required`, which only makes
    the two rooms touch. Being one space is a wider claim than sharing a wall,
    and it needs a wider opening -- see `standards.OPEN_SPAN_MIN_MM`.
    """
    by_cat: dict[str, list[str]] = {}
    for r in prog:
        by_cat.setdefault(r.category, []).append(r.id)
    out: list[tuple[str, str]] = []
    for p in scenario.matrix().values():
        if p.relation != "open" or p.forbidden:
            continue
        ha, hb = by_cat.get(p.a), by_cat.get(p.b)
        if ha and hb and ha[0] != hb[0]:
            out.append((ha[0], hb[0]))
    return out


def solver_pairs(prog, scenario: Scenario
                 ) -> tuple[list[tuple[str, str]], list[tuple[str, str]],
                            list[tuple[str, str, float]]]:
    """Scenario -> (required, forbidden, weighted) room-id pairs for the solver.

    `weighted` carries the soft preferences the topology scorer should reward;
    required/forbidden stay as the hard pairs the existing model understands.
    """
    by_cat: dict[str, list[str]] = {}
    for r in prog:
        by_cat.setdefault(r.category, []).append(r.id)
    req: list[tuple[str, str]] = []
    forb: list[tuple[str, str]] = []
    soft: list[tuple[str, str, float]] = []
    for p in scenario.matrix().values():
        ha, hb = by_cat.get(p.a), by_cat.get(p.b)
        if not ha or not hb:
            continue
        if p.forbidden and p.hard:
            for x in ha:
                for y in hb:
                    if x != y:
                        forb.append((x, y))
        elif p.forbidden:
            # Strongly discouraged, not excluded. Every pair gets the penalty,
            # not one representative: the point of "no bedroom door in the
            # formal living" is that it holds for all five bedrooms.
            for x in ha:
                for y in hb:
                    if x != y:
                        soft.append((x, y, p.weight))
        elif p.required:
            req.append((ha[0], hb[0]))       # one representative pair
        elif abs(p.weight) > 0.05:
            # Two DISTINCT rooms. `bedroom`-`bedroom` took ha[0] and hb[0] from
            # the same list and emitted a self-pair, which weights nothing.
            x = ha[0]
            y = next((i for i in hb if i != x), None)
            if y is not None:
                soft.append((x, y, p.weight))
    return req, forb, soft
