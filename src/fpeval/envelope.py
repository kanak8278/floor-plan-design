"""Area-budget gate: plot dimensions -> buildable envelope -> per-room budget.

Runs *before* layout, because the first question a user arrives with ("can I
get a 3BHK on my 30x40?") is arithmetic, not geometry. If the programme does
not fit the coverage cap there is no point invoking CP-SAT, and the failure
message needs to name the number that broke — municipal scrutiny checks the
area statement before it looks at a single wall.

Two conventions worth stating once:
  * Indian plots are quoted in **feet** (20x30, 30x40, 40x60, 50x80). The IR is
    integer millimetres. 1 ft = 304.8 mm exactly and the conversion lives only
    in `ft_to_mm` / `mm_to_ft` below.
  * The plot is placed axis-aligned with the **road at y = 0** and depth along
    +Y, so the front setback is always the -Y edge. Compass orientation is then
    carried entirely by `Site.north_deg` (bearing of +Y, clockwise from north),
    which is what the Vastu scorer reads.

Bylaw tables are *not* owned here. `BylawProfile` is the narrow structural
interface this module needs; `src/fpeval/bylaws.py` supplies the real
`CityProfile` tables. `BBMPDefault` is a small local stand-in so this module
and its tests run standalone.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from typing import Protocol, runtime_checkable, Sequence

from .ir import P

# ---------------------------------------------------------------- units

FT_MM = 304.8                      # exact
SQFT_M2 = 0.09290304               # exact (0.3048^2)


def ft_to_mm(ft: float) -> int:
    return int(round(ft * FT_MM))


def mm_to_ft(mm: float) -> float:
    return mm / FT_MM


def mm2_to_m2(a: float) -> float:
    return a / 1_000_000.0


def mm2_to_sqft(a: float) -> float:
    return mm2_to_m2(a) / SQFT_M2


def sqft_to_m2(a: float) -> float:
    return a * SQFT_M2


def m2_to_sqft(a: float) -> float:
    return a / SQFT_M2


# ---------------------------------------------------------------- bylaw interface

@dataclass(frozen=True)
class PlotRules:
    """The complete bylaw answer for one plot. Every field is auditable."""
    front_mm: int
    rear_mm: int
    side_left_mm: int
    side_right_mm: int
    far: float
    coverage: float                  # ground coverage cap, 0..1 of plot area
    max_floors: int
    profile: str = "unknown"
    band: str = ""
    verified: bool = False           # True only where we have a checked citation
    rule_text: dict[str, str] = field(default_factory=dict)

    @property
    def setbacks_mm(self) -> dict[str, int]:
        return {"front": self.front_mm, "rear": self.rear_mm,
                "left": self.side_left_mm, "right": self.side_right_mm}


@runtime_checkable
class BylawProfile(Protocol):
    """Everything the area-budget gate needs from a city profile.

    Deliberately one method: setbacks, FAR, coverage and floor cap all vary
    together by plot-area band, so splitting them invites inconsistent bands.
    """
    name: str

    def rules_for(self, *, plot_area_sqft: float,
                  width_mm: int, depth_mm: int) -> PlotRules: ...


@dataclass
class BBMPDefault:
    """Minimal BBMP residential stand-in. Two bands are verified figures.

    Verified: 1200 sqft -> front 0.9 m, rear 0.7 m, one side 0.7 m, FAR 1.75,
    coverage 75%.  2400 sqft -> front 12% of depth, rear 8% of depth, 8% of
    width per side, FAR 1.75, coverage 75%.

    Everything between and above 2400 sqft reuses the percentage rule and is
    flagged `verified=False`; the real banded table is bylaws.py's job.
    """
    name: str = "BBMP-residential-default"
    single_side_setback: bool = True   # <=1200 sqft may abut on one side

    def rules_for(self, *, plot_area_sqft: float,
                  width_mm: int, depth_mm: int) -> PlotRules:
        if plot_area_sqft <= 1200.0 + 1e-6:
            right = 0 if self.single_side_setback else 700
            return PlotRules(
                front_mm=900, rear_mm=700, side_left_mm=700, side_right_mm=right,
                far=1.75, coverage=0.75, max_floors=3,
                profile=self.name, band="<=1200 sqft", verified=True,
                rule_text={
                    "front": "BBMP <=1200 sqft: front setback 0.9 m",
                    "rear": "BBMP <=1200 sqft: rear setback 0.7 m",
                    "left": "BBMP <=1200 sqft: one side setback 0.7 m",
                    "right": ("other side may abut (party wall)"
                              if self.single_side_setback
                              else "0.7 m applied to both sides (conservative)"),
                    "far": "BBMP residential FAR 1.75",
                    "coverage": "BBMP residential ground coverage 75%",
                    "max_floors": "G+2 assumed for <=1200 sqft residential",
                })
        verified = abs(plot_area_sqft - 2400.0) < 1.0
        band = "2400 sqft" if verified else ">1200 sqft (percentage rule)"
        note = "" if verified else " [EXTRAPOLATED, not a checked citation]"
        return PlotRules(
            front_mm=int(round(0.12 * depth_mm)),
            rear_mm=int(round(0.08 * depth_mm)),
            side_left_mm=int(round(0.08 * width_mm)),
            side_right_mm=int(round(0.08 * width_mm)),
            far=1.75, coverage=0.75, max_floors=4,
            profile=self.name, band=band, verified=verified,
            rule_text={
                "front": f"12% of plot depth ({mm_to_ft(depth_mm):.1f} ft){note}",
                "rear": f"8% of plot depth{note}",
                "left": f"8% of plot width ({mm_to_ft(width_mm):.1f} ft){note}",
                "right": f"8% of plot width{note}",
                "far": "BBMP residential FAR 1.75",
                "coverage": "BBMP residential ground coverage 75%",
                "max_floors": f"G+3 assumed{note}",
            })


class CityProfileAdapter:
    """Wraps `bylaws.CityProfile` into the narrow interface used here.

    `bylaws.py` is the canonical table and owns the bands; this module only
    needs four numbers plus citations, so the coupling stays one small adapter
    rather than an import graph. `BBMPDefault` remains for standalone runs.
    """

    def __init__(self, city) -> None:
        self.city = city
        self.name = getattr(city, "name", "city-profile")

    def rules_for(self, *, plot_area_sqft: float,
                  width_mm: int, depth_mm: int) -> PlotRules:
        band = self.city.resolve(plot_area_sqft)
        side = band.side.resolve_mm(depth_mm, width_mm)
        both = getattr(band.side, "both_sides", True)
        floors = band.max_habitable_floors
        return PlotRules(
            front_mm=band.front.resolve_mm(depth_mm, width_mm),
            rear_mm=band.rear.resolve_mm(depth_mm, width_mm),
            side_left_mm=side,
            side_right_mm=side if both else 0,
            far=band.far, coverage=band.max_ground_coverage,
            max_floors=floors if floors else 99,
            profile=self.name, band=band.label,
            verified=True,
            rule_text={
                "front": f"{self.name} {band.key}: front {band.front.mode}"
                         f" {band.front.value}",
                "rear": f"{self.name} {band.key}: rear {band.rear.mode}"
                        f" {band.rear.value}",
                "left": f"{self.name} {band.key}: side {band.side.mode}"
                        f" {band.side.value}",
                "right": (f"{self.name} {band.key}: side {band.side.mode}"
                          f" {band.side.value}") if both
                         else "one side may abut (party wall)",
                "far": f"{self.name} {band.key}: FAR {band.far}",
                "coverage": f"{self.name} {band.key}: coverage "
                            f"{band.max_ground_coverage:.0%}",
                "max_floors": (band.max_floors_label or
                               "height not encoded for this band "
                               "(BBMP ties it to road width) - UNCHECKED"),
            })


def default_profile() -> BylawProfile:
    """Canonical city table when `bylaws.py` is present, else the local stub."""
    try:
        from .bylaws import BENGALURU
    except Exception:
        return BBMPDefault()
    return CityProfileAdapter(BENGALURU)


# ---------------------------------------------------------------- room programme

# NBC 2016 minimums, as (min clear width mm, min carpet area m^2).
# These are *clear* (finished-face) dimensions, so the solver adds a wall
# allowance before applying them to centreline rectangles.
NBC_MIN: dict[str, tuple[int, float]] = {
    "living":   (2400, 7.5),
    "bedroom":  (2400, 7.5),
    "dining":   (2400, 7.5),
    "study":    (2400, 7.5),
    "kitchen":  (1800, 5.0),
    "bathroom": (1200, 2.8),     # combined bath + WC
    "wc":       (900,  1.1),
    "pooja":    (900,  1.0),
    "storage":  (1000, 1.2),
    "utility":  (1000, 1.2),
    "balcony":  (900,  1.2),
    "stair":    (900,  2.0),
    "passage":  (900,  1.0),   # filler hall/corridor absorbing leftover area
}
HABITABLE = {"living", "bedroom", "dining", "study"}
WET = {"kitchen", "bathroom", "wc", "utility"}

# Vastu zone preferences, as compass bearings. Kitchen -> SE (Agni), master
# bedroom -> SW, pooja -> NE (Brahmasthan corner), entry -> N/E/NE.
VASTU_BEARING = {"N": 0.0, "NE": 45.0, "E": 90.0, "SE": 135.0,
                 "S": 180.0, "SW": 225.0, "W": 270.0, "NW": 315.0}
VASTU_DEFAULT_ZONE = {
    "kitchen": "SE", "pooja": "NE", "living": "NE", "dining": "W",
    "bathroom": "NW", "wc": "NW", "utility": "NW", "storage": "SW",
}


@dataclass
class RoomReq:
    """One requested room. `weight` drives proportional area allocation."""
    id: str
    name: str
    category: str
    target_m2: float | None = None
    weight: float = 1.0
    min_area_m2: float | None = None      # None -> NBC table
    min_width_mm: int | None = None       # None -> NBC table
    # Upper bound on area. There was none, and the model had no ceiling either,
    # so a bathroom could absorb surplus indefinitely: wet-02 produced four at
    # 6.8 m2 (19% of carpet) against a measured real-plan norm of ~4% per bath.
    # Service rooms need a ceiling; habitable rooms genuinely should take the
    # surplus, so this stays None for them.
    max_area_m2: float | None = None
    max_aspect: float = 2.6
    vastu_zone: str | None = None         # None -> VASTU_DEFAULT_ZONE
    is_entrance: bool = False

    def _nbc_row(self) -> tuple[int, float]:
        """The NBC row for this category, inherited by subtypes.

        `NBC_MIN` is keyed by base category, so a `master_bedroom` used to miss
        every key and fall through to the `(900, 1.0)` default -- the row meant
        for unnamed filler space. Every master bedroom in every plan was
        therefore budgeted against a 1 m2 minimum and a 900 mm minimum width
        instead of NBC's 7.5 m2 and 2400 mm, and the most important room in an
        Indian plan was the one room with no floor under it.
        """
        from . import roomtypes as rt
        for key in rt.counts_as(self.category):
            row = NBC_MIN.get(key)
            if row is not None:
                return row
        return (900, 1.0)

    def nbc_min_width(self) -> int:
        if self.min_width_mm is not None:
            return self.min_width_mm
        return self._nbc_row()[0]

    def nbc_min_area_m2(self) -> float:
        """The binding area minimum: NBC, or what the contents need.

        A utility has no NBC minimum, so before the contents floor was
        consulted here nothing stopped the solver handing it 1.47 m2 -- less
        than a washing machine and its door swing. The floor is not law and is
        kept in a separate table (`standards.CONTENTS_FLOOR_M2`) for that
        reason, but it binds the layout exactly like a legislated minimum does.
        """
        if self.min_area_m2 is not None:
            return self.min_area_m2
        from .standards import CONTENTS_FLOOR_M2
        return max(self._nbc_row()[1],
                   CONTENTS_FLOOR_M2.get(self.category, 0.0))

    def zone(self) -> str | None:
        return self.vastu_zone or VASTU_DEFAULT_ZONE.get(self.category)


# Typical Indian mid-market targets, m^2 carpet.
_T = {"living": 18.0, "kitchen": 9.0, "master": 13.0, "bedroom": 11.0,
      "bathroom": 3.6, "dining": 10.0, "pooja": 2.2, "utility": 3.5}


def bhk_programme(n_bed: int, *, baths: int | None = None, dining: bool = False,
                  pooja: bool = False, utility: bool = False,
                  living_m2: float | None = None) -> list[RoomReq]:
    """Standard nBHK programme. `n_bed`BHK = n bedrooms + living + kitchen."""
    if n_bed < 1:
        raise ValueError("n_bed must be >= 1")
    baths = baths if baths is not None else max(1, min(n_bed, 2 + (n_bed >= 4)))
    reqs = [RoomReq("living", "Living", "living",
                    target_m2=living_m2 or _T["living"], weight=1.8,
                    vastu_zone="NE", is_entrance=True),
            RoomReq("kitchen", "Kitchen", "kitchen",
                    target_m2=_T["kitchen"], weight=0.95, vastu_zone="SE")]
    for i in range(n_bed):
        master = i == 0
        reqs.append(RoomReq(
            f"bed{i+1}", "Master Bedroom" if master else f"Bedroom {i+1}",
            "bedroom",
            target_m2=_T["master"] if master else _T["bedroom"],
            weight=1.35 if master else 1.1,
            vastu_zone="SW" if master else ("S" if i % 2 else "W")))
    for i in range(baths):
        reqs.append(RoomReq(f"bath{i+1}", f"Bathroom {i+1}", "bathroom",
                            target_m2=_T["bathroom"], weight=0.38,
                            vastu_zone="NW"))
    if dining:
        reqs.append(RoomReq("dining", "Dining", "dining",
                            target_m2=_T["dining"], weight=1.0, vastu_zone="W"))
    if pooja:
        reqs.append(RoomReq("pooja", "Pooja", "pooja",
                            target_m2=_T["pooja"], weight=0.25, vastu_zone="NE"))
    if utility:
        reqs.append(RoomReq("utility", "Utility", "utility",
                            target_m2=_T["utility"], weight=0.35, vastu_zone="NW"))
    return reqs


# ---------------------------------------------------------------- audit trail

@dataclass
class AuditStep:
    n: int
    label: str
    rule: str
    computation: str
    value: float
    unit: str

    def line(self) -> str:
        return (f"{self.n:>2}. {self.label:<34} {self.value:>14,.3f} {self.unit:<6}"
                f"  [{self.rule}]  {self.computation}")


@dataclass
class RoomBudget:
    id: str
    name: str
    category: str
    requested_m2: float
    budget_m2: float          # after scaling to the available footprint
    nbc_min_m2: float
    nbc_min_width_mm: int
    vastu_zone: str | None
    shortfall_m2: float       # >0 => budget is below the NBC minimum


@dataclass
class AreaStatement:
    # inputs
    width_ft: float
    depth_ft: float
    road_facing: str
    north_deg: float
    profile: str
    rules: PlotRules
    # plot / envelope
    plot_polygon: list[P]
    plot_area_mm2: int
    plot_area_sqft: float
    envelope_polygon: list[P]
    envelope_w_mm: int
    envelope_d_mm: int
    envelope_area_mm2: int
    # caps
    coverage_cap_mm2: int
    footprint_mm2: int
    footprint_polygon: list[P]
    footprint_w_mm: int
    footprint_d_mm: int
    binding_cap: str                  # "envelope" | "ground_coverage"
    extra_rear_setback_mm: int        # depth given up to meet the coverage cap
    far: float
    far_builtup_mm2: int
    implied_storeys: int
    permitted_storeys: int
    achievable_builtup_mm2: int
    # layout surface handed to the solver (footprint minus half exterior wall)
    tiling_polygon: list[P]
    tiling_w_mm: int
    tiling_d_mm: int
    tiling_area_mm2: int
    exterior_wall_mm: int
    interior_wall_mm: int
    # programme
    budgets: list[RoomBudget]
    slack_m2: float                   # layout area not claimed by the programme
    programme_min_m2: float
    programme_target_m2: float
    circulation_frac: float
    usable_m2: float
    verdict: str                      # FEASIBLE | TIGHT | INFEASIBLE
    reasons: list[str]
    steps: list[AuditStep]

    @property
    def feasible(self) -> bool:
        return self.verdict != "INFEASIBLE"

    def budget_m2(self, room_id: str) -> float:
        return next(b.budget_m2 for b in self.budgets if b.id == room_id)

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("plot_polygon", "envelope_polygon", "footprint_polygon",
                  "tiling_polygon"):
            d[k] = [[p.x, p.y] for p in getattr(self, k)]
        return d

    def format_table(self) -> str:
        head = (f"AREA STATEMENT  {self.width_ft:g} x {self.depth_ft:g} ft "
                f"({self.plot_area_sqft:.0f} sqft)  road: {self.road_facing}  "
                f"north_deg={self.north_deg:g}\nprofile: {self.profile}"
                f"  band: {self.rules.band}"
                f"{'' if self.rules.verified else '  (UNVERIFIED BAND)'}")
        body = "\n".join(s.line() for s in self.steps)
        rows = "\n".join(
            f"    {b.name:<18} {b.category:<9} req {b.requested_m2:>6.2f}"
            f"  budget {b.budget_m2:>6.2f}  nbc>= {b.nbc_min_m2:>5.2f}"
            f"  w>= {b.nbc_min_width_mm:>4} mm  vastu {b.vastu_zone or '-':<3}"
            f"{'  SHORTFALL' if b.shortfall_m2 > 1e-6 else ''}"
            for b in self.budgets)
        tail = f"VERDICT: {self.verdict}" + (
            "\n  - " + "\n  - ".join(self.reasons) if self.reasons else "")
        return f"{head}\n{'-' * 108}\n{body}\n  per-room budget:\n{rows}\n{tail}"


# ---------------------------------------------------------------- computation

ROAD_BEARING = {"N": 0.0, "E": 90.0, "S": 180.0, "W": 270.0}


def north_deg_for(road_facing: str, north_deg: float | None = None) -> float:
    """+Y is plot depth, so the front (-Y) edge faces the road.

    bearing(-Y) = north_deg + 180 must equal the road bearing.
    """
    if north_deg is not None:
        return float(north_deg) % 360.0
    rf = road_facing.upper()
    if rf not in ROAD_BEARING:
        raise ValueError(f"road_facing must be one of {sorted(ROAD_BEARING)}")
    return (ROAD_BEARING[rf] + 180.0) % 360.0


def zone_vector(zone: str, north_deg: float) -> tuple[float, float]:
    """Unit vector in plan coordinates pointing at a compass zone.

    With north_deg = bearing of +Y, a bearing b sits at clockwise angle
    (b - north_deg) from +Y, i.e. (sin, cos) of that angle.
    """
    theta = math.radians(VASTU_BEARING[zone] - north_deg)
    return (math.sin(theta), math.cos(theta))


def _rect(x0: int, y0: int, x1: int, y1: int) -> list[P]:
    return [P(x0, y0), P(x1, y0), P(x1, y1), P(x0, y1)]


def compute_envelope(width_ft: float, depth_ft: float, *,
                     road_facing: str = "N",
                     north_deg: float | None = None,
                     profile: BylawProfile | None = None,
                     programme: Sequence[RoomReq] | None = None,
                     circulation_frac: float = 0.11,
                     exterior_wall_mm: int = 230,
                     interior_wall_mm: int = 115,
                     floors: int | None = None) -> AreaStatement:
    """Plot (feet) + bylaw profile + programme -> auditable area statement."""
    profile = profile or default_profile()
    programme = list(programme or [])
    nd = north_deg_for(road_facing, north_deg)

    w = ft_to_mm(width_ft)
    d = ft_to_mm(depth_ft)
    plot_area = w * d
    plot_sqft = mm2_to_sqft(plot_area)
    rules = profile.rules_for(plot_area_sqft=plot_sqft, width_mm=w, depth_mm=d)

    steps: list[AuditStep] = []

    def step(label, rule, comp, value, unit):
        steps.append(AuditStep(len(steps) + 1, label, rule, comp, value, unit))

    step("plot size", "input", f"{width_ft:g} ft x {depth_ft:g} ft "
         f"@ {FT_MM} mm/ft = {w} x {d} mm", plot_sqft, "sqft")
    step("plot area", "w x d", f"{w} x {d} mm", mm2_to_m2(plot_area), "m2")

    sb = rules.setbacks_mm
    for key in ("front", "rear", "left", "right"):
        step(f"setback {key}", rules.band,
             rules.rule_text.get(key, ""), sb[key], "mm")

    ex0, ey0 = sb["left"], sb["front"]
    ex1, ey1 = w - sb["right"], d - sb["rear"]
    env_w, env_d = ex1 - ex0, ey1 - ey0
    if env_w <= 0 or env_d <= 0:
        raise ValueError(f"setbacks consume the whole plot ({env_w}x{env_d} mm)")
    env_area = env_w * env_d
    step("buildable envelope", "plot - setbacks",
         f"{env_w} x {env_d} mm", mm2_to_m2(env_area), "m2")

    cov_cap = int(plot_area * rules.coverage)
    step("ground coverage cap", f"{rules.coverage:.0%} of plot",
         rules.rule_text.get("coverage", ""), mm2_to_m2(cov_cap), "m2")

    # If coverage binds, give the depth back to the rear setback so the
    # footprint is legal by construction rather than by later trimming.
    extra_rear = 0
    fw, fd = env_w, env_d
    binding = "envelope"
    if env_area > cov_cap:
        binding = "ground_coverage"
        fd = cov_cap // fw
        extra_rear = env_d - fd
    footprint = fw * fd
    step("max footprint", f"min(envelope, coverage) -> {binding}",
         f"{fw} x {fd} mm" + (f" (+{extra_rear} mm to rear setback)"
                              if extra_rear else ""),
         mm2_to_m2(footprint), "m2")

    far_builtup = int(plot_area * rules.far)
    step("FAR allowance", f"FAR {rules.far}",
         rules.rule_text.get("far", ""), mm2_to_m2(far_builtup), "m2")

    implied = max(1, math.ceil(far_builtup / footprint)) if footprint else 0
    step("implied storeys", "ceil(FAR builtup / footprint)",
         f"ceil({mm2_to_m2(far_builtup):.2f} / {mm2_to_m2(footprint):.2f})",
         implied, "nos")
    permitted = min(implied, rules.max_floors) if floors is None else int(floors)
    step("storeys used", "min(implied, max_floors)"
         if floors is None else "caller override",
         f"max_floors={rules.max_floors}", permitted, "nos")
    achievable = min(far_builtup, footprint * permitted)
    step("achievable built-up", "min(FAR, footprint x storeys)",
         f"{mm2_to_m2(footprint):.2f} x {permitted}",
         mm2_to_m2(achievable), "m2")

    # Layout surface: room rectangles are centreline-to-centreline, so inset by
    # half the exterior wall to land its *outer face* on the setback line.
    half = exterior_wall_mm // 2
    tx0, ty0, tx1, ty1 = ex0 + half, ey0 + half, ex0 + fw - half, ey0 + fd - half
    tw, td = tx1 - tx0, ty1 - ty0
    t_area = tw * td
    step("layout rect (centrelines)", f"footprint inset {half} mm",
         f"{tw} x {td} mm", mm2_to_m2(t_area), "m2")

    usable = mm2_to_m2(t_area) * (1.0 - circulation_frac)
    step("usable after circulation", f"{circulation_frac:.0%} circulation",
         "centreline area x (1 - circ)", usable, "m2")

    # per-room budget: scale requested targets onto the usable area
    reqs = programme
    tgt = [(r.target_m2 if r.target_m2 is not None else 0.0) for r in reqs]
    wts = [max(r.weight, 1e-6) for r in reqs]
    tsum = sum(tgt)
    if tsum <= 0:
        wsum = sum(wts)
        tgt = [mm2_to_m2(t_area) * x / wsum for x in wts]
        tsum = sum(tgt)
    scale = (mm2_to_m2(t_area) / tsum) if tsum > 0 else 1.0
    alloc = _allocate(reqs, tgt, usable)
    budgets: list[RoomBudget] = []
    for r, t, b in zip(reqs, tgt, alloc):
        mn = r.nbc_min_area_m2()
        budgets.append(RoomBudget(
            r.id, r.name, r.category, requested_m2=t, budget_m2=b,
            nbc_min_m2=mn, nbc_min_width_mm=r.nbc_min_width(),
            vastu_zone=r.zone(), shortfall_m2=max(0.0, mn - b)))
    slack = (mm2_to_m2(t_area) - sum(alloc)) if reqs else 0.0
    if reqs:
        step("programme scale factor", "layout area / sum(targets)",
             f"{mm2_to_m2(t_area):.2f} / {tsum:.2f}", scale, "x")
        step("unallocated slack", "layout area - sum(capped budgets)",
             "absorbed by a filler hall/passage" if slack > 1.0 else "-",
             slack, "m2")

    prog_min = sum(b.nbc_min_m2 for b in budgets)
    prog_tgt = sum(b.requested_m2 for b in budgets)
    if reqs:
        step("programme NBC minimum", "sum of NBC minima",
             f"{len(reqs)} rooms", prog_min, "m2")
        step("programme requested", "sum of targets", f"{len(reqs)} rooms",
             prog_tgt, "m2")

    reasons: list[str] = []
    verdict = "FEASIBLE"
    if reqs:
        avail = mm2_to_m2(t_area)
        wall_loss = _wall_loss_estimate(len(reqs), tw, td, interior_wall_mm)
        carpet = avail - wall_loss
        step("est. wall mass loss", "interior partitions",
             f"{len(reqs)} rooms x {interior_wall_mm} mm", wall_loss, "m2")
        step("est. carpet available", "layout area - wall mass",
             f"{avail:.2f} - {wall_loss:.2f}", carpet, "m2")
        if prog_min > carpet:
            verdict = "INFEASIBLE"
            reasons.append(
                f"NBC minima total {prog_min:.2f} m2 exceed available carpet "
                f"{carpet:.2f} m2 (short by {prog_min - carpet:.2f} m2) on "
                f"{plot_sqft:.0f} sqft with {binding} binding")
        widest = max((r.nbc_min_width() for r in reqs), default=0)
        if widest + interior_wall_mm > min(tw, td):
            verdict = "INFEASIBLE"
            reasons.append(
                f"narrowest layout dimension {min(tw, td)} mm cannot host the "
                f"widest NBC minimum clear width {widest} mm")
        n_hab = sum(1 for r in reqs if r.category in HABITABLE)
        if verdict != "INFEASIBLE" and prog_tgt > avail:
            verdict = "TIGHT"
            reasons.append(
                f"requested {prog_tgt:.2f} m2 > layout area {avail:.2f} m2; "
                f"targets scaled by {scale:.3f}")
        if verdict != "INFEASIBLE" and any(b.shortfall_m2 > 1e-6 for b in budgets):
            verdict = "TIGHT"
            reasons.append("some scaled budgets fall below NBC minima; the "
                           "solver will have to take area from other rooms")
        step("habitable rooms", "count", f"of {len(reqs)}", n_hab, "nos")

    return AreaStatement(
        width_ft=float(width_ft), depth_ft=float(depth_ft),
        road_facing=road_facing.upper(), north_deg=nd, profile=rules.profile,
        rules=rules,
        plot_polygon=_rect(0, 0, w, d), plot_area_mm2=plot_area,
        plot_area_sqft=plot_sqft,
        envelope_polygon=_rect(ex0, ey0, ex1, ey1),
        envelope_w_mm=env_w, envelope_d_mm=env_d, envelope_area_mm2=env_area,
        coverage_cap_mm2=cov_cap, footprint_mm2=footprint,
        footprint_polygon=_rect(ex0, ey0, ex0 + fw, ey0 + fd),
        footprint_w_mm=fw, footprint_d_mm=fd, binding_cap=binding,
        extra_rear_setback_mm=extra_rear,
        far=rules.far, far_builtup_mm2=far_builtup, implied_storeys=implied,
        permitted_storeys=permitted, achievable_builtup_mm2=achievable,
        tiling_polygon=_rect(tx0, ty0, tx1, ty1),
        tiling_w_mm=tw, tiling_d_mm=td, tiling_area_mm2=t_area,
        exterior_wall_mm=exterior_wall_mm, interior_wall_mm=interior_wall_mm,
        budgets=budgets, slack_m2=slack, programme_min_m2=prog_min,
        programme_target_m2=prog_tgt, circulation_frac=circulation_frac,
        usable_m2=usable, verdict=verdict, reasons=reasons, steps=steps)


# How far a room may be inflated past its requested target when the plot is
# larger than the brief. A bathroom does not usefully triple; a living room
# does grow. Surplus beyond these caps becomes `slack_m2`, which the solver
# absorbs as a hall/passage instead of bloating wet rooms.
GROWTH_CAP = {"living": 1.8, "dining": 1.8, "bedroom": 1.6, "study": 1.6,
              "kitchen": 1.35, "bathroom": 1.20, "wc": 1.15, "pooja": 1.30,
              "utility": 1.30, "storage": 1.60, "balcony": 1.60, "passage": 99.0}


def _allocate(reqs: Sequence[RoomReq], targets: Sequence[float],
              available_m2: float) -> list[float]:
    """Fit requested targets into the available area.

    Over-supply: grow each room proportionally but no further than its
    `GROWTH_CAP`, leaving the remainder as slack.
    Under-supply: shrink proportionally, water-filling so no room drops below
    its NBC minimum (rooms already at the floor stop shrinking).
    """
    if not reqs:
        return []
    tsum = sum(targets)
    if tsum <= 0:
        return [available_m2 / len(reqs)] * len(reqs)
    if available_m2 >= tsum:
        s = available_m2 / tsum
        return [t * min(s, GROWTH_CAP.get(r.category, 1.4))
                for r, t in zip(reqs, targets)]
    mins = [r.nbc_min_area_m2() for r in reqs]
    out = list(targets)
    frozen = [False] * len(reqs)
    for _ in range(len(reqs) + 1):
        free = [i for i in range(len(reqs)) if not frozen[i]]
        if not free:
            break
        fixed = sum(out[i] for i in range(len(reqs)) if frozen[i])
        pool = available_m2 - fixed
        base = sum(targets[i] for i in free)
        s = (pool / base) if base > 0 else 0.0
        hit = False
        for i in free:
            v = targets[i] * s
            if v < mins[i]:
                out[i], frozen[i], hit = mins[i], True, True
            else:
                out[i] = v
        if not hit:
            break
    return out


def _wall_loss_estimate(n_rooms: int, tw: int, td: int, t_int: int) -> float:
    """Area eaten by interior partitions in a centreline tiling.

    A slicing tree of n leaves has n-1 cuts; each cut spans roughly the mean of
    the two side lengths. Rough, but it is only used for the feasibility gate.
    """
    if n_rooms <= 1:
        return 0.0
    span = 0.5 * (tw + td)
    return mm2_to_m2((n_rooms - 1) * span * t_int)
