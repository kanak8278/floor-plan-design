"""Semantic plausibility of an EXTRACTED room schedule.

Distinct from `rules.py`, and the distinction matters: `rules.py` asks "is this
generated design compliant?" against a Plan with real geometry. This module asks
"is this transcription trustworthy?" against a name+dimension list with no geometry
at all. Same-looking numbers, opposite purpose -- here a violation means we
probably misread the drawing, not that the drawing is bad.

Three layers, in increasing power:
  1. dual-unit cross-check (imgcorpus)  -- catches digit-level misreads
  2. per-room-type bands (below)        -- catches label/dimension swaps
  3. cross-room coherence (below)       -- catches structural nonsense that
                                          per-room checks accept individually
Layer 3 is the one that earns its keep: every room can look individually sane
while the set is impossible (5 kitchens, a 60 m^2 "toilet", rooms summing past
the plot).
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field

# ---- canonicalisation ------------------------------------------------------
# Extractions carry names exactly as printed, including misspellings ("UITILITY")
# and Indian-specific spaces absent from western datasets.
_ALIASES: dict[str, tuple[str, ...]] = {
    "bedroom":   ("bedroom", "bed room", "bed rm", "mbr", "master bed", "guest bed", "bhk"),
    "living":    ("living", "hall", "drawing", "lounge", "family"),
    "dining":    ("dining", "dinning"),
    "kitchen":   ("kitchen", "kitchan", "kit"),
    "bathroom":  ("bath", "toilet", "wc", "w.c", "washroom", "pwd rm", "powder",
                  "attached toilet", "common toilet", "t&b"),
    "balcony":   ("balcony", "balcany", "balcone", "deck"),
    "sitout":    ("sitout", "sit out", "sit-out", "verandah", "veranda", "porch", "portico"),
    "utility":   ("utility", "uitility", "utilty", "wash area", "wash", "service"),
    "pooja":     ("pooja", "puja", "prayer", "mandir"),
    "foyer":     ("foyer", "entry", "entrance", "lobby", "passage", "corridor", "hallway"),
    "stair":     ("stair", "staircase", "steps"),
    "store":     ("store", "storage", "closet", "wardrobe", "dress"),
    "study":     ("study", "office", "work"),
    "parking":   ("parking", "car park", "garage", "car porch"),
    "patio":     ("patio", "courtyard", "terrace", "open to sky", "ots"),
    "landscape": ("landscape", "garden", "lawn", "planter"),
}
# Spaces that are not enclosed habitable rooms; excluded from built-up area
# and exempt from aspect-ratio checks (a sitout is legitimately long and thin).
NON_HABITABLE = {"landscape", "patio", "parking", "sitout", "balcony", "stair", "foyer"}
UNBUILT = {"landscape", "patio", "parking"}


def canonical(name: str) -> str:
    n = re.sub(r"[_\-.]+", " ", (name or "").strip().lower())
    n = re.sub(r"\s*\d+\s*$", "", n).strip()          # BEDROOM_1 -> bedroom
    for cat, keys in _ALIASES.items():
        if any(k in n for k in keys):
            return cat
    return "unknown"


# ---- per-room plausibility bands ------------------------------------------
# min/max area (m^2), min short side (mm), max aspect ratio.
# Lower bounds are NBC 2016 Part 3 where it legislates; upper bounds and aspect
# limits are real-world Indian residential practice and exist to catch misreads,
# not to judge design.
@dataclass(frozen=True)
class Band:
    area_min: float
    area_max: float
    short_min_mm: int
    aspect_max: float | None

BANDS: dict[str, Band] = {
    "bedroom":  Band(6.0,  35.0, 2100, 2.4),
    "living":   Band(8.0,  60.0, 2100, 2.6),
    "dining":   Band(5.0,  40.0, 1800, 2.6),
    "kitchen":  Band(3.5,  25.0, 1500, 3.0),
    "bathroom": Band(0.9,  12.0,  850, 3.2),
    "utility":  Band(1.0,  15.0,  800, 4.0),
    "pooja":    Band(0.5,   8.0,  600, 3.0),
    "store":    Band(0.4,  15.0,  500, 4.0),
    "study":    Band(3.0,  25.0, 1500, 2.6),
    "stair":    Band(1.5,  20.0,  800, 4.0),
    "foyer":    Band(0.8,  20.0,  800, None),
    "sitout":   Band(0.8,  30.0,  700, None),
    "balcony":  Band(0.8,  30.0,  700, None),
    "patio":    Band(0.8,  60.0,  700, None),
    "parking":  Band(6.0,  60.0, 2100, None),
    "landscape":Band(0.2, 400.0,  300, None),
}


@dataclass
class Issue:
    check: str
    severity: str            # error | warn
    detail: str
    room: str | None = None


@dataclass
class PlausibilityReport:
    ok: bool
    issues: list[Issue] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    areas: dict = field(default_factory=dict)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]


def _area_m2(dim: tuple[int, int]) -> float:
    return dim[0] * dim[1] / 1_000_000.0


def check(rooms: list[dict], plot_area_m2: float | None = None,
          coverage_cap: float = 0.75) -> PlausibilityReport:
    """`rooms`: [{'name': str, 'dim_mm': (w,d) tuple or None}, ...]"""
    issues: list[Issue] = []
    cats: dict[str, int] = {}
    sized: list[tuple[str, str, tuple[int, int]]] = []

    for r in rooms:
        cat = canonical(r.get("name", ""))
        cats[cat] = cats.get(cat, 0) + 1
        dim = r.get("dim_mm")
        if not dim:
            continue
        sized.append((cat, r.get("name", "?"), tuple(dim)))
        if cat == "unknown":
            issues.append(Issue("unknown_room_type", "warn",
                                f"room name '{r.get('name')}' did not map to a known category",
                                r.get("name")))
            continue
        b = BANDS.get(cat)
        if not b:
            continue
        a = _area_m2(dim)
        short, long = min(dim), max(dim)
        if a < b.area_min:
            issues.append(Issue("area_below_band", "error",
                f"{cat} {a:.1f} m² below plausible floor {b.area_min} m²", r.get("name")))
        if a > b.area_max:
            issues.append(Issue("area_above_band", "error",
                f"{cat} {a:.1f} m² above plausible ceiling {b.area_max} m² — likely a misread or merged label",
                r.get("name")))
        if short < b.short_min_mm:
            issues.append(Issue("width_below_band", "error",
                f"{cat} short side {short} mm below {b.short_min_mm} mm", r.get("name")))
        if b.aspect_max and short and long / short > b.aspect_max:
            issues.append(Issue("aspect_implausible", "error",
                f"{cat} aspect {long/short:.1f}:1 exceeds {b.aspect_max}:1 — reads as circulation, not a room",
                r.get("name")))

    # No measurable room means nothing was actually checked. Report that as a
    # refusal rather than a pass -- otherwise the gate approves unverifiable plans.
    if not sized:
        issues.append(Issue("no_measurable_rooms", "error",
            f"{len(rooms)} rooms labelled but none carry a parseable dimension — "
            "nothing to check, so this cannot serve as ground truth"))

    # ---- layer 3: cross-room coherence -----------------------------------
    n_bed = cats.get("bedroom", 0)
    n_bath = cats.get("bathroom", 0)
    n_kit = cats.get("kitchen", 0)

    if n_kit > 2:
        issues.append(Issue("too_many_kitchens", "error",
            f"{n_kit} kitchens — a single dwelling has one"))
    if n_bed and n_bath > n_bed + 2:
        issues.append(Issue("bath_bed_ratio", "warn",
            f"{n_bath} bathrooms for {n_bed} bedrooms is unusual"))
    if n_bed and n_bath == 0:
        issues.append(Issue("no_bathroom", "warn",
            f"{n_bed} bedrooms but no bathroom found — labels likely incomplete"))

    built = [(c, n, d) for c, n, d in sized if c not in UNBUILT]
    built_m2 = sum(_area_m2(d) for _, _, d in built)
    total_m2 = sum(_area_m2(d) for _, _, d in sized)

    if built:
        biggest = max(built, key=lambda t: _area_m2(t[2]))
        frac = _area_m2(biggest[2]) / built_m2 if built_m2 else 0
        if frac > 0.40:
            issues.append(Issue("one_room_dominates", "error",
                f"'{biggest[1]}' is {100*frac:.0f}% of built-up area — usually a merged or phantom label",
                biggest[1]))

    beds = [_area_m2(d) for c, _, d in sized if c == "bedroom"]
    if len(beds) >= 2 and max(beds) / min(beds) > 4.0:
        issues.append(Issue("bedroom_spread", "warn",
            f"bedroom areas span {min(beds):.1f}–{max(beds):.1f} m², a 4x+ spread"))

    if plot_area_m2:
        if total_m2 > plot_area_m2 * 1.05:
            issues.append(Issue("exceeds_plot", "error",
                f"labelled area {total_m2:.1f} m² exceeds plot {plot_area_m2:.1f} m² — scale or units wrong"))
        cov = built_m2 / plot_area_m2
        if cov > coverage_cap * 1.25:
            issues.append(Issue("coverage_implausible", "error",
                f"built-up/plot = {cov:.2f}, far above the {coverage_cap:.2f} cap"))
        elif cov > coverage_cap:
            issues.append(Issue("coverage_over_cap", "warn",
                f"built-up/plot = {cov:.2f} exceeds the {coverage_cap:.2f} cap"))
        elif cov < 0.15:
            issues.append(Issue("coverage_too_low", "warn",
                f"built-up/plot = {cov:.2f} — most rooms probably went unlabelled"))

    if not n_bed and not n_kit:
        issues.append(Issue("not_a_dwelling", "error",
            "no bedroom and no kitchen — this is not a residential unit plan"))

    return PlausibilityReport(
        ok=not any(i.severity == "error" for i in issues),
        issues=issues, counts=cats,
        areas={"built_up_m2": round(built_m2, 1), "all_labelled_m2": round(total_m2, 1),
               "plot_m2": round(plot_area_m2, 1) if plot_area_m2 else None,
               "coverage": round(built_m2 / plot_area_m2, 3) if plot_area_m2 else None},
    )


SQFT_PER_M2 = 10.7639


def check_area_closure(rooms: list[dict], areas: dict,
                       tol: float = 0.18) -> list[Issue]:
    """Third checksum: printed CARPET AREA vs the sum of enclosed room areas.

    Independent of the dual-unit check -- that one validates each room in
    isolation, this one validates the set. A plan can pass every per-room check
    while a room is missing entirely, and only this catches it.

    Tolerance is wide (18%) because "carpet area" excludes internal wall
    footprint and its exact definition varies by builder and by RERA state rules.
    """
    out: list[Issue] = []
    if not areas:
        return out
    carpet_m2 = areas.get("carpet_sqm")
    if carpet_m2 is None and areas.get("carpet_sqft"):
        carpet_m2 = areas["carpet_sqft"] / SQFT_PER_M2
    if not carpet_m2:
        return out

    enclosed = sum(_area_m2(tuple(r["dim_mm"])) for r in rooms
                   if r.get("dim_mm") and canonical(r.get("name", "")) not in NON_HABITABLE)
    if enclosed <= 0:
        return out
    err = abs(enclosed - carpet_m2) / carpet_m2
    if err > tol:
        out.append(Issue("carpet_area_mismatch",
                         "error" if err > tol * 2 else "warn",
                         f"enclosed rooms sum to {enclosed:.1f} m² but printed carpet area is "
                         f"{carpet_m2:.1f} m² ({100*err:.0f}% off) — a room is likely missing or misread"))

    sup_m2 = areas.get("super_built_up_sqm")
    if sup_m2 is None and areas.get("super_built_up_sqft"):
        sup_m2 = areas["super_built_up_sqft"] / SQFT_PER_M2
    if sup_m2 and carpet_m2:
        ratio = sup_m2 / carpet_m2
        # Indian loading factors run ~1.25-1.75; outside that the two figures
        # are not the pair we think they are.
        if not (1.15 <= ratio <= 1.90):
            out.append(Issue("loading_factor_implausible", "warn",
                f"super-built-up/carpet = {ratio:.2f}, outside the usual 1.15-1.90 band"))
    return out
