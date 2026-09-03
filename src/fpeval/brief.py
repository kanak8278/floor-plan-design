"""Brief generation -- the input side of the eval harness.

Two generators, and they are deliberately not interchangeable.

`plan_to_brief(plan)` inverts a real plan into the brief a client might have
given, which manufactures (prompt, reference) pairs at scale from the 17,000-plan
ResPlan corpus. Two constraints on it are load-bearing:

  1. **ResPlan is scale-ambiguous** (DECISIONS.md #4). Coordinates are not
     metric; `wall_depth` is the only physical anchor and it implies ~226 mm
     walls with roughly +/-30% spread. A brief derived from ResPlan therefore
     uses *relative* language and room counts and states no absolute dimension.
     `has_absolute_dimension_claim()` is the enforcement, and the tests run it.
  2. **ResPlan's geography is unstated, and the signals point away from India**
     (sqft-denominated fields, two bathrooms modal for a two-bedroom unit).
     These briefs are NOT relabelled as Indian. Renaming a Gulf two-bed to
     "3BHK with pooja" changes exactly zero geometry and would yield a corpus
     that looks Indian and validates nothing. Vocabulary here stays neutral --
     "living room", not "hall"; no pooja, no BHK, no lakhs -- and every record
     carries its provenance.

`synthetic_indian_briefs(n)` samples the Indian market distribution directly:
plot sizes in feet, BHK configurations, facing, Vastu, family structure, budget
band. These have **no reference plan**, which is fine and is the point: 5 of the
6 reward terms (validity, bye-law, NBC, Vastu, programme fit) need no dataset at
all (DECISIONS.md #5). This is the primary eval set.
"""
from __future__ import annotations

import os
import pickle
import random
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional, Sequence

from .ir import Plan
from .spec import MM2_PER_SQFT

# --------------------------------------------------------------------------
# Corpus loading
# --------------------------------------------------------------------------

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RESPLAN_CANDIDATES = (
    os.path.join(_REPO, "data", "ResPlan.pkl"),
    "/tmp/resplan/data/ResPlan.pkl",
)
_RESPLAN_CACHE: Optional[list[dict]] = None


def resplan_path() -> str:
    for p in RESPLAN_CANDIDATES:
        if os.path.exists(p):
            return p
    raise FileNotFoundError(
        "ResPlan.pkl not found; looked in " + ", ".join(RESPLAN_CANDIDATES))


def load_resplan(*, cache: bool = True) -> list[dict]:
    """The raw 17k-plan list. ~258 MB pickle, so it is cached per process."""
    global _RESPLAN_CACHE
    if cache and _RESPLAN_CACHE is not None:
        return _RESPLAN_CACHE
    with open(resplan_path(), "rb") as fh:
        data = pickle.load(fh)
    if cache:
        _RESPLAN_CACHE = data
    return data


def resplan_plans(n: int, *, seed: int = 0, start: int = 0) -> list[Plan]:
    """Convert `n` ResPlan entries to IR, skipping any that fail conversion."""
    from .resplan import convert
    raw = load_resplan()
    idx = list(range(len(raw)))
    if seed is not None:
        random.Random(seed).shuffle(idx)
    out: list[Plan] = []
    for i in idx[start:]:
        try:
            out.append(convert(raw[i]))
        except Exception:
            continue
        if len(out) >= n:
            break
    return out


# --------------------------------------------------------------------------
# The absolute-dimension guard
# --------------------------------------------------------------------------

_UNIT = r"(?:sq\.?\s?(?:ft|feet|m|metres?|meters?)|sqft|sqm|ft|feet|foot|" \
        r"m2|m\u00b2|mm|cms?|centimet(?:re|er)s?|met(?:re|er)s?|yards?|" \
        r"gunta|cents?|acres?|m)"
_ABSOLUTE_PATTERNS = (
    # "120 sqft", "3.6 m", "12ft"
    re.compile(rf"\b\d+(?:\.\d+)?\s*{_UNIT}\b", re.I),
    # "30x40", "12 x 14", "30 by 40", "30*40"
    re.compile(r"\b\d+(?:\.\d+)?\s*(?:x|×|\*|by)\s*\d+(?:\.\d+)?\b", re.I),
    # "area of 1200", "1200 square"
    re.compile(r"\b\d+(?:\.\d+)?\s*squares?\b", re.I),
    re.compile(r"\barea\s+(?:of|is|about|around)?\s*\d", re.I),
)


def has_absolute_dimension_claim(text: str) -> bool:
    """True if the text asserts an absolute size. ResPlan briefs must not.

    Room *counts* are fine ("three bedrooms", "2 bathrooms"); it is the pairing
    of a number with a length/area unit, or an NxM plot quote, that is banned,
    because ResPlan cannot support such a claim to better than +/-30%.
    """
    return any(p.search(text or "") for p in _ABSOLUTE_PATTERNS)


def absolute_dimension_hits(text: str) -> list[str]:
    hits: list[str] = []
    for p in _ABSOLUTE_PATTERNS:
        hits += [m.group(0) for m in p.finditer(text or "")]
    return hits


# --------------------------------------------------------------------------
# (a) plan_to_brief
# --------------------------------------------------------------------------

# ResPlan's own category vocabulary. Kept neutral on purpose -- see the module
# docstring. Nothing here is Indianised.
_NEUTRAL_NAME = {
    "living": ("living room", "living rooms"),
    "kitchen": ("kitchen", "kitchens"),
    "bedroom": ("bedroom", "bedrooms"),
    "bathroom": ("bathroom", "bathrooms"),
    "balcony": ("balcony", "balconies"),
    "storage": ("storage room", "storage rooms"),
    # categories our own specs use, in case a non-ResPlan plan is passed
    "master_bedroom": ("main bedroom", "main bedrooms"),
    "toilet": ("WC", "WCs"),
    "dining": ("dining area", "dining areas"),
    "utility": ("utility space", "utility spaces"),
    "corridor": ("hallway", "hallways"),
    "staircase": ("stair", "stairs"),
    "store": ("store", "stores"),
    "parking": ("parking bay", "parking bays"),
    "sit_out": ("porch", "porches"),
    "study": ("study", "studies"),
}
_NUMWORD = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six",
            7: "seven", 8: "eight", 9: "nine", 10: "ten"}


def _count_phrase(n: int, cat: str) -> str:
    sing, plur = _NEUTRAL_NAME.get(cat, (cat.replace("_", " "),
                                         cat.replace("_", " ") + "s"))
    if n == 1:
        return f"a {sing}" if sing[0] not in "aeiou" else f"an {sing}"
    return f"{_NUMWORD.get(n, str(n))} {plur}"


def _room_counts(plan: Plan) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in plan.rooms:
        out[r.category] = out.get(r.category, 0) + 1
    return out


def _adjacent_pairs(plan: Plan) -> list[tuple[str, str]]:
    """Room-category pairs that share at least one wall."""
    seen: set[tuple[str, str]] = set()
    for i, a in enumerate(plan.rooms):
        wa = set(a.wall_ids)
        for b in plan.rooms[i + 1:]:
            if wa & set(b.wall_ids):
                key = tuple(sorted((a.category, b.category)))
                if key[0] != key[1]:
                    seen.add(key)  # type: ignore[arg-type]
    return sorted(seen)


def _relative_size_note(plan: Plan, rng: random.Random) -> Optional[str]:
    """Size language that survives a +/-30% scale ambiguity: ordinal only."""
    beds = sorted((r for r in plan.rooms if r.category == "bedroom"),
                  key=lambda r: -r.area)
    if len(beds) >= 2:
        ratio = beds[0].area / max(beds[-1].area, 1)
        if ratio > 1.35:
            return rng.choice([
                "One bedroom should be clearly the largest -- treat it as the "
                "main bedroom -- and the others can be noticeably smaller.",
                "We want one generous main bedroom and the rest more compact.",
            ])
        return rng.choice([
            "The bedrooms should come out roughly the same size as each other.",
            "No strong preference on which bedroom is bigger; similar sizes are fine.",
        ])
    living = next((r for r in plan.rooms if r.category == "living"), None)
    if living is not None:
        return rng.choice([
            "The living room should be the largest space in the layout.",
            "We'd like the living area to read as the main room of the home.",
        ])
    return None


def brief_facts(plan: Plan) -> dict:
    """The ground truth a brief is generated from -- used to score round-trips."""
    counts = _room_counts(plan)
    prov = dict(plan.provenance or {})
    return {
        "plan_id": plan.id,
        "room_counts": counts,
        "n_rooms": len(plan.rooms),
        "n_bedrooms": counts.get("bedroom", 0) + counts.get("master_bedroom", 0),
        "n_bathrooms": counts.get("bathroom", 0) + counts.get("toilet", 0),
        "has_balcony": counts.get("balcony", 0) > 0,
        "n_balconies": counts.get("balcony", 0),
        "n_storage": counts.get("storage", 0) + counts.get("store", 0),
        "adjacent_pairs": _adjacent_pairs(plan),
        "n_windows": sum(1 for o in plan.openings if o.kind == "window"),
        "has_front_door": any(o.kind == "front_door" for o in plan.openings),
        "mm_per_unit": prov.get("mm_per_unit"),
        "stated_area_m2": prov.get("stated_area_m2"),
    }


def plan_to_brief(plan: Plan, *, seed: Optional[int] = None,
                  include_provenance: bool = False) -> str:
    """A real plan -> the natural-language brief a client might have given.

    Deterministic (seeded off the plan id unless `seed` is passed) and template
    driven rather than LLM generated: it costs nothing, it is reproducible, and
    -- most importantly -- it cannot hallucinate a dimension the corpus does not
    support. Use `paraphrase_briefs()` if you want naturalistic prose on top;
    that path re-runs the absolute-dimension guard on the output.
    """
    rng = random.Random(seed if seed is not None else f"brief::{plan.id}")
    facts = brief_facts(plan)
    counts = facts["room_counts"]

    beds = facts["n_bedrooms"]
    baths = facts["n_bathrooms"]

    # -- opening ----------------------------------------------------------
    unit = rng.choice(["home", "apartment layout", "unit", "flat"])
    if beds:
        opening = rng.choice([
            f"We're planning a {_NUMWORD.get(beds, beds)}-bedroom {unit}.",
            f"Looking for a {_NUMWORD.get(beds, beds)}-bedroom {unit} layout.",
            f"I need a floor plan for a {_NUMWORD.get(beds, beds)}-bedroom {unit}.",
        ])
    else:
        opening = f"We're planning a small {unit} with no separate bedroom."

    # -- programme --------------------------------------------------------
    order = ["living", "kitchen", "dining", "bedroom", "master_bedroom",
             "bathroom", "toilet", "balcony", "storage", "store", "utility",
             "study", "corridor", "staircase"]
    parts = [_count_phrase(counts[c], c) for c in order if counts.get(c)]
    for c in sorted(counts):
        if c not in order:
            parts.append(_count_phrase(counts[c], c))
    if len(parts) > 1:
        programme = ", ".join(parts[:-1]) + f", and {parts[-1]}"
    else:
        programme = parts[0] if parts else "a single open space"
    prog_sentence = rng.choice([
        f"The rooms we need are {programme}.",
        f"It should have {programme}.",
        f"Room-wise: {programme}.",
    ])

    lines = [opening, prog_sentence]

    # -- bathroom expectation --------------------------------------------
    if beds and baths:
        if baths >= beds:
            lines.append(rng.choice([
                "We'd like enough bathrooms that the bedrooms aren't sharing.",
                "Ideally each bedroom has its own bathroom.",
            ]))
        elif baths == 1:
            lines.append("A single shared bathroom is acceptable.")

    # -- relative sizing --------------------------------------------------
    note = _relative_size_note(plan, rng)
    if note:
        lines.append(note)

    # -- adjacency (real topology from the plan) --------------------------
    pairs = facts["adjacent_pairs"]
    wants: list[str] = []
    if ("kitchen", "living") in pairs:
        wants.append("the kitchen opening onto the living room")
    if ("balcony", "living") in pairs:
        wants.append("a balcony off the living room")
    elif ("balcony", "bedroom") in pairs:
        wants.append("a balcony reachable from a bedroom")
    if ("bathroom", "bedroom") in pairs and baths:
        wants.append("at least one bathroom directly off a bedroom")
    if ("kitchen", "storage") in pairs or ("kitchen", "store") in pairs:
        wants.append("storage next to the kitchen")
    if wants:
        rng.shuffle(wants)
        joined = wants[0] if len(wants) == 1 else \
            ", ".join(wants[:-1]) + f", and {wants[-1]}"
        lines.append(rng.choice([
            f"We'd like {joined}.",
            f"Important to us: {joined}.",
            f"If possible, {joined}.",
        ]))

    # -- prohibitions -----------------------------------------------------
    if ("bathroom", "kitchen") not in pairs and baths and counts.get("kitchen"):
        lines.append(rng.choice([
            "Please keep the bathrooms away from the kitchen.",
            "No bathroom door opening into the kitchen.",
        ]))

    # -- light / circulation ---------------------------------------------
    if facts["n_windows"] >= max(3, len(plan.rooms)):
        lines.append(rng.choice([
            "Every habitable room should have an exterior window.",
            "Natural light in all the main rooms matters to us.",
        ]))
    if counts.get("corridor", 0) == 0 and len(plan.rooms) >= 5:
        lines.append("We'd rather not lose much space to corridors.")

    # -- deliberately relative closing -----------------------------------
    lines.append(rng.choice([
        "I don't have final dimensions yet -- proportions matter more than exact sizes.",
        "Sizes are flexible; the arrangement is what we care about.",
        "Treat the sizes as relative for now.",
    ]))

    text = " ".join(lines)
    if include_provenance:
        text += "\n\n" + provenance_footer(plan)
    return text


def provenance_footer(plan: Plan) -> str:
    prov = plan.provenance or {}
    mm = prov.get("mm_per_unit")
    return (
        "[provenance: derived from {src} plan {pid}. Geography UNSTATED and the "
        "corpus signals (sqft-denominated fields, two bathrooms modal for a "
        "two-bedroom unit) point away from India -- this is NOT an Indian brief "
        "and has not been relabelled as one. Scale is inferred from wall_depth "
        "at {mm} mm/unit assuming 226 mm walls, +/-30%, so no absolute dimension "
        "is asserted.]"
    ).format(src=prov.get("source", "unknown"),
             pid=prov.get("resplan_id", plan.id),
             mm=f"{mm:.3f}" if isinstance(mm, (int, float)) else "unknown")


def plan_to_brief_record(plan: Plan, *, seed: Optional[int] = None) -> dict:
    """Brief plus honest provenance plus the ground truth to score against."""
    return {
        "brief": plan_to_brief(plan, seed=seed),
        "facts": brief_facts(plan),
        "provenance": {
            "source": (plan.provenance or {}).get("source", "unknown"),
            "plan_id": plan.id,
            "geography": "unstated; corpus signals point away from India",
            "scale": "inferred from wall_depth (226 mm walls assumed), +/-30%",
            "vocabulary": "neutral international; NOT relabelled as Indian",
            "absolute_dimensions_asserted": False,
            "mm_per_unit": (plan.provenance or {}).get("mm_per_unit"),
        },
        "note": provenance_footer(plan),
    }


def briefs_from_resplan(n: int, *, seed: int = 0) -> list[dict]:
    """`n` (brief, facts, provenance) records straight from the corpus."""
    return [plan_to_brief_record(p) for p in resplan_plans(n, seed=seed)]


# --------------------------------------------------------------------------
# (b) synthetic_indian_briefs
# --------------------------------------------------------------------------
# The market distribution. Weights are the commonly quoted plot-size mix for
# small-plot residential work in Indian tier-1/tier-2 cities; they are a modelling
# choice, not measured data, and they are here so the choice is inspectable.

PLOTS: list[tuple[int, int, float]] = [
    (20, 30, 0.10),   # 600 sqft
    (25, 40, 0.09),   # 1000
    (30, 40, 0.22),   # 1200 -- the modal Indian plot
    (30, 50, 0.15),   # 1500
    (30, 30, 0.06),   # 900
    (40, 40, 0.06),   # 1600
    (40, 60, 0.16),   # 2400
    (50, 80, 0.08),   # 4000
    (25, 50, 0.05),   # 1250
    (60, 40, 0.03),   # 2400 wide
]
FACINGS = [("east", 0.30), ("north", 0.28), ("west", 0.22), ("south", 0.20)]
CITIES = [("bengaluru", 0.20), ("hyderabad", 0.14), ("chennai", 0.12),
          ("pune", 0.10), ("coimbatore", 0.07), ("kochi", 0.07),
          ("ahmedabad", 0.07), ("jaipur", 0.06), ("lucknow", 0.06),
          ("kolkata", 0.06), ("delhi", 0.05)]
FAMILIES = [
    "couple with one child", "couple with two children",
    "joint family -- us, our two kids and my parents",
    "newly married couple, planning for kids",
    "retired couple, one bedroom for guests",
    "family of four plus my mother who cannot climb stairs",
    "three brothers, one floor each",
    "family of five",
]
# (budget_band, sentence template). Split so non-numeric bands read naturally.
BUDGETS: list[tuple[Optional[str], str]] = [
    ("around 25 lakhs", "Budget is {b}."),
    ("25-30 lakhs", "Budget is {b}."),
    ("about 35 lakhs", "Budget is {b}."),
    ("40 lakh budget", "We have a {b}."),
    ("under 20 lakhs", "We need to stay {b}."),
    ("50-60 lakhs", "Budget is {b}."),
    ("roughly 1 crore", "Budget is {b}."),
    ("economy", "It has to be an {b} build, nothing fancy."),
    ("premium", "We want a {b} finish."),
    (None, ""), (None, ""), (None, ""),
]
# Measured off real Bengaluru builder unit sheets. These labels and this area
# stack are how buyers actually talk, and none of it appears in western
# datasets -- which is precisely why the synthetic set has to carry it.
UNIT_LABEL_FORMS = [
    "{b}BHK {t}T TYPE C{n}",
    "{b} BHK + {t} T - TYPE {n} G",
    "{b} BHK + {t} TOILETS + STUDY",
    "{b}BHK + SR + ST + PDR",
    "{b} BHK - {t}T",
]
# loading factor = super built-up / carpet. Measured market range 1.25-1.75.
LOADING_RANGE = (1.25, 1.75)
BUILDER_ROOM_WORDS = {
    "pooja": ["PUJA", "POOJA ROOM", "puja room"],
    "utility": ["UTILITY", "utility with a handwash", "UTILITY + HANDWASH"],
    "sit_out": ["SITOUT", "sit-out", "PORCH", "PATIO"],
    "store": ["ST (store)", "store room", "STORE"],
    "study": ["STUDY", "study nook"],
    "powder": ["PDR (powder room)", "PWD RM", "a powder room near the foyer"],
    "servant": ["SR (servant room)", "servant room with toilet"],
    "foyer": ["FOYER", "a proper foyer"],
    "balcony": ["two balconies", "three balconies", "BALCONY x2"],
}
VASTU_PHRASES = [
    "It must be vastu compliant.",
    "Please make it as per vastu.",
    "Vastu is important -- pooja in the north-east and kitchen in the south-east.",
    "We would prefer vastu compliance if it doesn't cost us space.",
    "Strictly vastu, my father-in-law will check the plan.",
    "Master bedroom should be in the south-west as per vastu.",
]


def _weighted(rng: random.Random, items: Sequence[tuple[Any, float]]) -> Any:
    total = sum(w for _, w in items)
    r = rng.random() * total
    acc = 0.0
    for v, w in items:
        acc += w
        if r <= acc:
            return v
    return items[-1][0]


def _plausible_bhk(rng: random.Random, area_sqft: int, storeys: int) -> int:
    """BHK conditioned on buildable area, so the corpus is not full of nonsense."""
    buildable = area_sqft * 0.6 * storeys
    if buildable < 420:
        return _weighted(rng, [(1, 0.5), (2, 0.5)])
    if buildable < 620:
        return _weighted(rng, [(1, 0.1), (2, 0.7), (3, 0.2)])
    if buildable < 950:
        return _weighted(rng, [(2, 0.40), (3, 0.53), (4, 0.07)])
    if buildable < 1600:
        return _weighted(rng, [(2, 0.18), (3, 0.60), (4, 0.22)])
    return _weighted(rng, [(2, 0.08), (3, 0.55), (4, 0.37)])


@dataclass
class SyntheticBrief:
    """A brief plus the parameters it was sampled from.

    The ground truth is what makes this an eval set rather than a demo: spec
    extraction can be scored on plot size, facing, bedroom count, storeys,
    Vastu, toilet count and site kind without any hand labelling.
    """
    brief: str
    plot_width_ft: Optional[int]
    plot_depth_ft: Optional[int]
    road_facing_side: Optional[str]
    city: Optional[str]
    bedrooms: int
    storeys: int
    vastu: bool
    extras: list[str] = field(default_factory=list)
    underdetermined: list[str] = field(default_factory=list)
    register: str = "plain"
    # apartment units have NO plot -- see DesignSpec.site_kind
    site_kind: str = "plot"
    toilets: int = 0
    half_bhk: bool = False
    unit_label: str = ""
    saleable_sqft: Optional[int] = None
    carpet_sqft: Optional[int] = None
    rera_carpet_sqft: Optional[int] = None

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        return d

    @property
    def expected_questions(self) -> bool:
        return bool(self.underdetermined)


def _sample_apartment(rng: random.Random, *, allow_underdetermined: bool = True
                      ) -> SyntheticBrief:
    """A buyer shopping a builder unit. There is no plot -- that is the point.

    About a fifth of real Indian residential briefs are of this shape, and they
    must not be forced into a plot-shaped spec: an apartment has no setbacks, no
    ground coverage and no FAR of its own, so a fabricated plot would silently
    invalidate every one of those checks. The bound is the quoted area stack.
    """
    beds = _weighted(rng, [(1, 0.10), (2, 0.38), (3, 0.42), (4, 0.10)])
    half = rng.random() < 0.12
    toilets = _weighted(rng, [(max(1, beds - 1), 0.35), (beds, 0.5),
                              (beds + 1, 0.15)])
    carpet = int(beds * rng.uniform(330, 420) + (60 if half else 0))
    loading = rng.uniform(*LOADING_RANGE)
    saleable = int(carpet * loading)
    rera = int(carpet * rng.uniform(0.90, 0.98))
    facing = _weighted(rng, FACINGS)
    city = _weighted(rng, CITIES)
    label = rng.choice(UNIT_LABEL_FORMS).format(
        b=f"{beds}.5" if half else beds, t=toilets, n=rng.randint(1, 20))

    missing: list[str] = []
    if allow_underdetermined and rng.random() < 0.15:
        missing = rng.choice([["facing"], ["area"], ["bhk"]])

    bits = []
    if "facing" not in missing:
        bits.append(f"{facing} facing")
    label_txt = label if "bhk" not in missing else "one of their units"
    opening = rng.choice([
        f"We are booking {label_txt} in a project in {city.title()}",
        f"Looking at {label_txt} on the 7th floor of a tower in {city.title()}",
        f"We have shortlisted {label_txt} in a gated community in {city.title()}",
    ])
    if bits:
        opening += f", {' '.join(bits)}"
    lines = [opening + "."]
    if "area" not in missing:
        lines.append(rng.choice([
            f"The builder quotes {saleable} sq ft saleable, {carpet} sq ft "
            f"carpet and {rera} sq ft RERA carpet.",
            f"Saleable area is {saleable} sq ft with {carpet} sq ft carpet.",
            f"They say {saleable} sq ft super built-up; carpet works out to "
            f"about {carpet} sq ft.",
        ]))
    else:
        lines.append("I don't have the area sheet with me yet.")
    lines.append(rng.choice([
        "We want to re-plan the interior layout without moving the structure.",
        "The developer allows internal changes before slab casting.",
        "Please re-work the internal layout for us.",
    ]))
    extras: list[str] = []
    for cat, prob in (("pooja", 0.5), ("utility", 0.6), ("powder", 0.2),
                      ("store", 0.25), ("study", 0.3), ("balcony", 0.5),
                      ("foyer", 0.2)):
        if rng.random() < prob:
            extras.append(cat)
    if extras:
        lines.append("We need " + ", ".join(
            rng.choice(BUILDER_ROOM_WORDS[c]) for c in extras) + ".")
    vastu = rng.random() < 0.35
    if vastu:
        lines.append(rng.choice(VASTU_PHRASES))

    return SyntheticBrief(
        brief=" ".join(lines),
        plot_width_ft=None, plot_depth_ft=None,      # correct: there is no plot
        road_facing_side=facing if "facing" not in missing else None,
        city=city, bedrooms=beds, storeys=1, vastu=vastu, extras=extras,
        underdetermined=missing, register="apartment",
        site_kind="apartment_unit", toilets=toilets, half_bhk=half,
        unit_label=label if "bhk" not in missing else "",
        saleable_sqft=saleable if "area" not in missing else None,
        carpet_sqft=carpet if "area" not in missing else None,
        rera_carpet_sqft=rera if "area" not in missing else None,
    )


def _sample_synthetic(rng: random.Random, *, allow_underdetermined: bool = True
                      ) -> SyntheticBrief:
    if rng.random() < 0.20:
        return _sample_apartment(rng,
                                 allow_underdetermined=allow_underdetermined)
    w, d = _weighted(rng, [((a, b), wt) for a, b, wt in PLOTS])
    facing = _weighted(rng, FACINGS)
    city = _weighted(rng, CITIES)
    storeys = _weighted(rng, [(1, 0.45), (2, 0.48), (3, 0.07)])
    beds = _plausible_bhk(rng, w * d, storeys)
    vastu = rng.random() < 0.45
    family = rng.choice(FAMILIES)
    budget, budget_tmpl = rng.choice(BUDGETS)

    half = rng.random() < 0.08
    toilets = _weighted(rng, [(max(1, (beds + 1) // 2), 0.45), (beds, 0.40),
                              (beds + 1, 0.15)])
    # ~30% of clients quote the builder-style label verbatim
    unit_label = ""
    if rng.random() < 0.30:
        unit_label = rng.choice(UNIT_LABEL_FORMS).format(
            b=f"{beds}.5" if half else beds, t=toilets, n=rng.randint(1, 20))

    extras: list[str] = []
    if rng.random() < 0.55:
        extras.append("pooja")
    if rng.random() < 0.45:
        extras.append("utility")
    if rng.random() < 0.40:
        extras.append("sit_out")
    if rng.random() < 0.55:
        extras.append("parking")
    if rng.random() < 0.25:
        extras.append("study")
    if rng.random() < 0.20:
        extras.append("store")
    if rng.random() < 0.15:
        extras.append("dining")
    if rng.random() < 0.12:
        extras.append("powder")
    if rng.random() < 0.10:
        extras.append("servant")
    if half:
        extras.append("study")
    if storeys > 1 and rng.random() < 0.30:
        extras.append("duplex")

    # ~15% of the set is deliberately underdetermined: the extractor must ask
    # rather than invent. This is the behaviour that matters most in production.
    missing: list[str] = []
    if allow_underdetermined and rng.random() < 0.15:
        missing = rng.choice([["plot"], ["facing"], ["plot", "facing"], ["bhk"]])

    plot_txt = f"{w}x{d}" if "plot" not in missing else None
    facing_txt = facing if "facing" not in missing else None
    bhk_txt = beds if "bhk" not in missing else None

    register = _weighted(rng, [("terse", 0.30), ("plain", 0.45),
                               ("verbose", 0.25)])
    extra_words = {
        "powder": rng.choice(BUILDER_ROOM_WORDS["powder"]),
        "servant": rng.choice(BUILDER_ROOM_WORDS["servant"]),
        "pooja": rng.choice(["pooja room", "puja room", "small mandir",
                             "pooja space", "PUJA"]),
        "utility": rng.choice(["utility", "wash area",
                               "utility area behind the kitchen", "UTILITY",
                               "UTILITY + HANDWASH"]),
        "sit_out": rng.choice(["sit-out", "portico", "front sit out",
                               "verandah", "SITOUT", "PORCH"]),
        "parking": rng.choice(["car parking", "covered car park",
                               "parking for one car", "two-wheeler and car parking"]),
        "study": rng.choice(["study room", "small study", "work-from-home corner"]),
        "store": rng.choice(["store room", "storage"]),
        "dining": rng.choice(["separate dining", "dining area"]),
    }
    ex = [extra_words[e] for e in extras if e in extra_words]

    frag_plot = f"{plot_txt} site" if plot_txt else "our site"
    frag_face = f"{facing_txt} facing" if facing_txt else ""
    if not bhk_txt:
        frag_bhk = "a few bedrooms"
    elif unit_label:
        frag_bhk = unit_label
    elif half:
        frag_bhk = f"{bhk_txt}.5 BHK"
    else:
        frag_bhk = rng.choice([f"{bhk_txt}BHK",
                               f"{bhk_txt} BHK + {toilets} T",
                               f"{bhk_txt} BHK with {toilets} toilets"])
    frag_storey = {1: "single floor", 2: "G+1", 3: "G+2"}[storeys]
    if "duplex" in extras and storeys == 2:
        frag_storey = "duplex"

    if register == "terse":
        bits = [b for b in [frag_face, frag_plot if plot_txt else ""] if b]
        s = (" ".join(bits) or "planning a house") + f", need {frag_bhk} {frag_storey}"
        if ex:
            s += " with " + ", ".join(ex)
        s += "."
        if vastu:
            s += " " + rng.choice(VASTU_PHRASES)
        if budget:
            s += " " + budget_tmpl.format(b=budget)
        brief = s
    elif register == "plain":
        if plot_txt:
            art = "an " if (frag_face or frag_plot).startswith(
                ("e", "a", "i", "o", "u")) else "a "
            lead = f"We have {art}{(frag_face + ' ') if frag_face else ''}{frag_plot}"
            s = [f"{lead} in {city.title()}."]
        else:
            s = [f"We have a plot in {city.title()}"
                 + (f", {frag_face}." if frag_face
                    else " -- I will confirm the exact dimensions later.")]
        s.append(f"We want {frag_bhk} on {frag_storey}.")
        if ex:
            s.append("Also need " + ", ".join(ex) + ".")
        s.append(f"Family: {family}.")
        if vastu:
            s.append(rng.choice(VASTU_PHRASES))
        if budget:
            s.append(budget_tmpl.format(b=budget))
        brief = " ".join(s)
    else:
        s = [f"Hello, we are planning to build our own house on "
             f"{'a ' + frag_plot if plot_txt else 'our plot'}"
             + (f" which is {frag_face}" if frag_face else "")
             + f" in {city.title()}."]
        s.append(f"The family is {family}, so we are looking at {frag_bhk}"
                 f" and we are okay with {frag_storey}.")
        if ex:
            s.append("Apart from the usual rooms we definitely want "
                     + ", ".join(ex) + ".")
        s.append(rng.choice([
            "The kitchen should be close to the dining and we don't want the "
            "toilet visible from the main door.",
            "My wife wants a big hall since we have a lot of guests.",
            "Please make sure the bedrooms get cross ventilation.",
            "We would like the master bedroom to have an attached bathroom.",
        ]))
        if vastu:
            s.append(rng.choice(VASTU_PHRASES))
        if budget:
            s.append(budget_tmpl.format(b=budget).rstrip(".")
                     + ", all inclusive.")
        brief = " ".join(s)

    return SyntheticBrief(
        brief=brief,
        plot_width_ft=w if plot_txt else None,
        plot_depth_ft=d if plot_txt else None,
        road_facing_side=facing if facing_txt else None,
        city=city if register != "terse" else None,
        bedrooms=beds,
        storeys=storeys,
        vastu=vastu,
        extras=extras,
        underdetermined=missing,
        register=register,
        site_kind="plot",
        toilets=toilets,
        half_bhk=half,
        unit_label=unit_label,
    )


def synthetic_indian_brief_records(
    n: int, *, seed: int = 0, allow_underdetermined: bool = True
) -> list[SyntheticBrief]:
    """`n` synthetic Indian briefs *with* the sampled ground truth."""
    rng = random.Random(seed)
    return [_sample_synthetic(rng, allow_underdetermined=allow_underdetermined)
            for _ in range(n)]


def synthetic_indian_briefs(n: int, *, seed: int = 0) -> list[str]:
    """`n` realistic Indian client briefs. No reference plans, by design.

    Scored purely on validator terms -- validity, bye-law, NBC, Vastu,
    programme fit -- five of which need no dataset. This is the primary eval set.
    """
    return [r.brief for r in synthetic_indian_brief_records(n, seed=seed)]


def synthetic_distribution_report(n: int = 2000, seed: int = 0) -> dict:
    """What the sampler actually produces. Cheap sanity on the eval set."""
    recs = synthetic_indian_brief_records(n, seed=seed)
    def tally(key):
        out: dict[Any, int] = {}
        for r in recs:
            v = getattr(r, key)
            out[v] = out.get(v, 0) + 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))
    extras: dict[str, int] = {}
    for r in recs:
        for e in r.extras:
            extras[e] = extras.get(e, 0) + 1
    return {
        "n": n,
        "plots": tally("plot_width_ft"),
        "facing": tally("road_facing_side"),
        "bedrooms": tally("bedrooms"),
        "storeys": tally("storeys"),
        "vastu_rate": round(sum(r.vastu for r in recs) / n, 3),
        "underdetermined_rate": round(
            sum(bool(r.underdetermined) for r in recs) / n, 3),
        "register": tally("register"),
        "site_kind": tally("site_kind"),
        "half_bhk_rate": round(sum(r.half_bhk for r in recs) / n, 3),
        "unit_label_rate": round(sum(bool(r.unit_label) for r in recs) / n, 3),
        "toilets": tally("toilets"),
        "extras_rate": {k: round(v / n, 3) for k, v in
                        sorted(extras.items(), key=lambda kv: -kv[1])},
        "mean_chars": round(sum(len(r.brief) for r in recs) / n, 1),
    }


# --------------------------------------------------------------------------
# Optional LLM paraphrase (bulk work -> sonnet)
# --------------------------------------------------------------------------

PARAPHRASE_SYSTEM = """\
You rewrite templated client briefs so they read like real messages from real
people -- varied length, varied register, occasional typos and run-on sentences.

Absolute rules:
1. Do not add, remove, or change any factual requirement: room counts, room
   types, directions, adjacency wishes, storey counts, budgets.
2. NEVER introduce a dimension, area, or plot size that is not already in the
   input. No square feet, no metres, no "30x40". If the input has no numbers
   with units, the output must have none either.
3. One rewritten brief per input, in the same order, and nothing else.
"""


def paraphrase_briefs(
    briefs: Sequence[str],
    *,
    model: str = "claude-sonnet-5",
    client: Any = None,
    usage: Any = None,
    batch: int = 8,
    forbid_absolute_dimensions: bool = True,
) -> list[str]:
    """Make templated briefs read naturally. Bulk work, so sonnet.

    Batched to keep spend down. If the paraphrase smuggles in an absolute
    dimension that the source did not have, the original is kept -- the guard
    outranks the prose.
    """
    from .llm import call_tool

    out: list[str] = []
    for i in range(0, len(briefs), batch):
        chunk = list(briefs[i:i + batch])
        schema = {
            "type": "object",
            "properties": {
                "briefs": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["briefs"],
            "additionalProperties": False,
        }
        user = "Rewrite each of these briefs:\n\n" + "\n\n".join(
            f"[{j + 1}]\n{b}" for j, b in enumerate(chunk))
        try:
            payload = call_tool(
                system=PARAPHRASE_SYSTEM, user=user,
                tool_name="emit_briefs",
                tool_description="Return the rewritten briefs in input order.",
                schema=schema, model=model, op="paraphrase_briefs",
                client=client, usage=usage, effort="low", max_tokens=8000,
            )
            got = payload.get("briefs") or []
        except Exception:
            got = []
        for j, src in enumerate(chunk):
            new = got[j] if j < len(got) and isinstance(got[j], str) else ""
            if not new.strip():
                out.append(src)
                continue
            if (forbid_absolute_dimensions
                    and not has_absolute_dimension_claim(src)
                    and has_absolute_dimension_claim(new)):
                out.append(src)          # guard wins
            else:
                out.append(new.strip())
    return out
