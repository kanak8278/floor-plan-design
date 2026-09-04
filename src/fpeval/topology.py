"""The topology domain model: zones, bathroom kinds, and a weighted adjacency
preference matrix per scenario.

Built because the solver had no relational vocabulary at all. Measured against
400 real ResPlan plans versus 90 of ours:

    metric                         real      ours
    living is the core            97.0%      3.3%
    public_score (median)        +1.426    -0.460
    living_relative (median)       2.53      1.01
    privacy_gradient (median)      0.39      1.02

Ours have no spatial hierarchy whatsoever. Those real-plan figures are the
targets in `SYNTAX_TARGETS` below.

Two design choices worth stating:

* **Weighted preferences, not binary pairs.** The layout-optimisation literature
  converges on an adjacency *preference matrix*; our solver took a list of
  required pairs at a flat penalty, which cannot express "mildly discouraged".
  One signed weight covers required / preferred / neutral / discouraged /
  forbidden.
* **Scenario = typology x size band.** A 20x30 2BHK cannot have a separate
  dining room and a 50x80 villa must. The same rule table for both is wrong in
  one direction or the other.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal

Zone = Literal["public", "private", "service", "circulation", "outdoor"]
Relation = Literal["direct", "open", "near", "separate", "any"]

ZONE_OF: dict[str, Zone] = {
    "servant": "private",
    "living": "public", "dining": "public", "foyer": "circulation",
    "sitout": "outdoor", "bedroom": "private", "master_bedroom": "private",
    "study": "private", "kitchen": "service", "utility": "service",
    "store": "service", "bathroom": "service", "shaft": "service",
    "stair": "circulation", "balcony": "outdoor", "patio": "outdoor",
    "parking": "outdoor", "landscape": "outdoor", "pooja": "public",
}

# Measured on 400 real ResPlan plans. These are what a good plan looks like.
SYNTAX_TARGETS = {
    "living_is_core": 0.95,      # fraction of plans where living is most integrated
    "living_relative_min": 2.00,  # real median 2.53
    "privacy_gradient_max": 0.55,  # real median 0.39
    "public_score_min": 0.80,     # real median +1.426
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

CIRCULATION = {"foyer", "stair", "living", "dining"}
PRIVATE_CATS = {"bedroom", "master_bedroom", "study"}


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

    @property
    def required(self) -> bool: return self.weight >= 0.9

    @property
    def forbidden(self) -> bool: return self.weight <= -0.9


# Universal, every scenario. Law and hygiene, not taste.
UNIVERSAL: tuple[Pref, ...] = (
    Pref("kitchen", "bathroom", -1.0, "separate",
         "NBC: a WC must not open into a kitchen"),
    Pref("pooja", "bathroom", -1.0, "separate",
         "a shrine must not share a door or wall with a toilet"),
    Pref("dining", "bathroom", -1.0, "separate",
         "rejected outright in Indian practice"),
    Pref("kitchen", "utility", 1.0, "direct",
         "the machine and the cylinder live off the kitchen"),
    Pref("kitchen", "store", 0.6, "direct", "dry store within reach"),
    Pref("bedroom", "bedroom", -0.5, "separate",
         "bedrooms opening into each other costs privacy"),
    Pref("pooja", "kitchen", -0.3, "separate", "usually kept apart"),
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
               Pref("parking", "sitout", 0.6, "near", "car to door")),
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
               Pref("parking", "sitout", 0.6, "near", "porch to door")),
        circulation=("living", "dining", "foyer", "stair"),
        entry_sequence=("sitout", "foyer", "living", "dining"), max_depth=4,
        expects=("foyer", "living", "dining", "kitchen", "utility", "store",
                 "pooja", "parking", "sitout", "stair"),
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
        expects=("living", "dining", "kitchen", "stair", "bathroom")),

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
        notes="Multiple kitchens are correct here, one per unit."),
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
    if band == "large" or (has_two_living and bedrooms >= 5):
        return SCENARIOS["villa"]
    if storeys >= 2:
        return SCENARIOS["duplex"]
    return SCENARIOS[{"compact": "house_compact", "standard": "house_standard",
                      "mid": "house_mid", "large": "villa"}[band]]


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
        if p.forbidden:
            for x in ha:
                for y in hb:
                    if x != y:
                        forb.append((x, y))
        elif p.required:
            req.append((ha[0], hb[0]))       # one representative pair
        elif abs(p.weight) > 0.05:
            soft.append((ha[0], hb[0], p.weight))
    return req, forb, soft
