"""Bye-law / code tables as DATA, not code.

Why data: bye-laws are jurisdictional and mutable. BBMP revised setbacks in the
2020 RBBMP zoning regulations; NBC 2016 Part 3 supersedes NBC 2005. Anything
hard-coded into `if` statements rots and cannot be diffed by a compliance
reviewer. Every number below is therefore a field on a dataclass that
round-trips through JSON, so a new city (or a client override) is a data file.

Sources encoded here:
  * BBMP / RBBMP zoning regulations - setback, FAR and ground-coverage bands.
  * NBC 2016 Part 3 - habitable-room, kitchen, bath/WC and passage minima.
  * Vastu: no authority exists. Practitioners disagree, so the ruleset is a
    weighted table the client can edit; see `VastuProfile`.

All lengths in **integer millimetres**, areas in m^2, to match the IR.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Literal

SQFT_M2 = 0.09290304          # exact: 1 sqft = 0.09290304 m^2

SetbackMode = Literal["fixed", "frac_depth", "frac_width"]


# ---------------------------------------------------------------- setbacks ---

@dataclass
class Setback:
    """One setback requirement.

    BBMP expresses small-plot setbacks as absolute metres (0.9 m front on a
    20x30) but large-plot setbacks as a percentage of the plot dimension
    (12% of depth front, 8% of depth rear, 8% of width each side), so a single
    scalar cannot represent the table. `mode` selects which.
    """
    mode: SetbackMode
    value: float                  # mm if mode == "fixed", else a fraction
    both_sides: bool = True       # side setbacks: False => only one side required
    min_mm: int | None = None     # optional absolute floor under a percentage

    def resolve_mm(self, depth_mm: float, width_mm: float) -> int:
        if self.mode == "fixed":
            v = self.value
        elif self.mode == "frac_depth":
            v = self.value * depth_mm
        elif self.mode == "frac_width":
            v = self.value * width_mm
        else:                                             # pragma: no cover
            raise ValueError(f"bad setback mode {self.mode!r}")
        if self.min_mm is not None:
            v = max(v, self.min_mm)
        return int(round(v))


@dataclass
class PlotBand:
    """A plot-area band and everything the bye-law pins to it.

    Bands are half-open on the low side: `min_sqft < area <= max_sqft`, so the
    canonical 600 / 1200 / 2400 / 3875 sqft plots each land in the band whose
    upper bound they name.
    """
    key: str
    min_sqft: float
    max_sqft: float                 # inclusive; float('inf') for the top band
    label: str
    front: Setback
    rear: Setback
    side: Setback
    far: float
    max_ground_coverage: float      # fraction of plot area
    max_floors_label: str | None    # e.g. "G+2", "stilt+3"; None = not encoded
    max_habitable_floors: int | None = None   # excludes stilt; None = not encoded

    def contains(self, area_sqft: float) -> bool:
        return self.min_sqft < area_sqft <= self.max_sqft


# BBMP bands. The 600 and 1200 rows carry identical setbacks/FAR/coverage but
# differ in permitted height (G+2 vs stilt+3), so they must stay separate.
BBMP_BANDS: list[PlotBand] = [
    PlotBand(
        key="upto_600", min_sqft=0.0, max_sqft=600.0, label="<=600 sqft (20x30)",
        front=Setback("fixed", 900), rear=Setback("fixed", 700),
        side=Setback("fixed", 700, both_sides=False),
        far=1.75, max_ground_coverage=0.75,
        max_floors_label="G+2", max_habitable_floors=3,
    ),
    PlotBand(
        key="upto_1200", min_sqft=600.0, max_sqft=1200.0, label="<=1200 sqft (30x40)",
        front=Setback("fixed", 900), rear=Setback("fixed", 700),
        side=Setback("fixed", 700, both_sides=False),
        far=1.75, max_ground_coverage=0.75,
        max_floors_label="stilt+3", max_habitable_floors=3,
    ),
    PlotBand(
        key="upto_2400", min_sqft=1200.0, max_sqft=2400.0, label="<=2400 sqft (40x60)",
        front=Setback("frac_depth", 0.12), rear=Setback("frac_depth", 0.08),
        side=Setback("frac_width", 0.08, both_sides=True),
        far=1.75, max_ground_coverage=0.75,
        max_floors_label="stilt+4", max_habitable_floors=4,
    ),
    PlotBand(
        # 2400..3875 keeps the 2400-band ratios; the FAR/coverage step happens
        # at the 3875 sqft (~360 m^2) boundary in the BBMP table.
        key="upto_3875", min_sqft=2400.0, max_sqft=3875.0, label="<=3875 sqft",
        front=Setback("frac_depth", 0.12), rear=Setback("frac_depth", 0.08),
        side=Setback("frac_width", 0.08, both_sides=True),
        far=1.75, max_ground_coverage=0.75,
        max_floors_label="stilt+4", max_habitable_floors=4,
    ),
    PlotBand(
        key="above_3875", min_sqft=3875.0, max_sqft=float("inf"),
        label=">=3875 sqft (50x80 and up)",
        front=Setback("frac_depth", 0.12), rear=Setback("frac_depth", 0.08),
        side=Setback("frac_width", 0.08, both_sides=True),
        far=2.25, max_ground_coverage=0.65,
        # BBMP ties height above this band to road width, not plot area, so we
        # do NOT invent a number here. Reported as unchecked, never as a pass.
        max_floors_label=None, max_habitable_floors=None,
    ),
]


# -------------------------------------------------------------------- NBC ---

@dataclass
class NBCMinima:
    """NBC 2016 Part 3 dimensional minima. Areas m^2, widths/heights mm.

    `hab_min_area_single_room` is the 9.5 m^2 case: a one-room dwelling has to
    absorb sleeping + living, so the code raises the floor from 7.5.
    """
    hab_min_area_m2: float = 7.5
    hab_min_area_single_room_m2: float = 9.5
    hab_min_width_mm: int = 2400
    hab_min_ceiling_mm: int = 2750
    ac_room_min_ceiling_mm: int = 2400

    kitchen_min_area_m2: float = 5.0
    kitchen_min_width_mm: int = 1800
    kitchen_min_ceiling_mm: int = 2750

    bath_wc_combined_min_area_m2: float = 2.8
    bath_only_min_area_m2: float = 1.8
    wc_only_min_area_m2: float = 1.1
    bath_min_width_mm: int = 1200
    bath_min_ceiling_mm: int = 2100

    passage_min_width_mm: int = 900

    # Not a dimension: NBC forbids a WC opening *directly* into a kitchen or
    # into a room used for cooking / food storage.
    wc_may_open_into_kitchen: bool = False


# ------------------------------------------------------------------ vastu ---

DIRECTIONS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
CENTRE = "C"


@dataclass
class VastuRule:
    """One weighted zone preference.

    `prefer` scores 1.0, `avoid` scores 0.0, anything else scores
    `neutral_score`. Weight is relative within the profile. Practitioners
    disagree on nearly all of these, hence: editable, weighted, and never an
    `error` severity.
    """
    key: str
    applies_to: list[str]          # room categories or logical targets
    prefer: list[str]
    avoid: list[str]
    weight: float = 1.0
    neutral_score: float = 0.5
    detail: str = ""


@dataclass
class VastuProfile:
    rules: list[VastuRule]
    # Brahmasthan is the central ninth of the envelope. On a 600 sqft plot,
    # keeping it open costs ~11% of the buildable footprint, so it is soft by
    # construction: low default weight, tunable.
    brahmasthan_weight: float = 0.4
    brahmasthan_max_occupancy: float = 0.35   # fraction of the central ninth
    centre_fraction: float = 1.0 / 3.0        # central band as a fraction of extent

    def by_category(self, category: str) -> list[VastuRule]:
        return [r for r in self.rules if category in r.applies_to]

    def rule(self, key: str) -> VastuRule | None:
        return next((r for r in self.rules if r.key == key), None)


DEFAULT_VASTU = VastuProfile(rules=[
    VastuRule("entrance", ["front_door"], ["N", "NE", "E"], ["SW"], weight=1.5,
              detail="main entrance in N/E/NE; SW entrance is the strongest taboo"),
    VastuRule("kitchen", ["kitchen"], ["SE"], ["NE", "SW"], weight=1.2,
              detail="Agni corner is SE; NE and SW are held to be adverse"),
    VastuRule("master_bedroom", ["bedroom_master"], ["SW"], ["NE"], weight=1.0,
              detail="heaviest mass SW; master bedroom in NE is discouraged"),
    VastuRule("pooja", ["pooja"], ["NE"], ["S", "SW"], weight=0.8,
              detail="pooja in the NE Ishanya corner"),
    VastuRule("stairs", ["stairs"], ["SW", "NW"], ["NE", CENTRE], weight=0.6,
              detail="stairs SW or NW; never NE or over the Brahmasthan"),
    VastuRule("toilet", ["bathroom"], ["NW", "W"], ["NE", "SE"], weight=0.8,
              detail="toilets NW/W; NE (Ishanya) and SE (Agni) avoided"),
    VastuRule("bedroom_other", ["bedroom"], ["S", "W", "SW", "NW"], [], weight=0.3,
              neutral_score=0.6, detail="secondary bedrooms: weak preference only"),
    VastuRule("living", ["living"], ["N", "NE", "E"], [], weight=0.3,
              neutral_score=0.6, detail="living/drawing room towards N/E"),
])


# ------------------------------------------------------------ city profile ---

@dataclass
class CityProfile:
    """Everything jurisdiction-specific, in one serialisable object."""
    name: str
    bands: list[PlotBand]
    nbc: NBCMinima = field(default_factory=NBCMinima)
    vastu: VastuProfile = field(default_factory=lambda: DEFAULT_VASTU)

    rainwater_harvesting_min_sqft: float = 1200.0
    stilt_exempt_from_far: bool = True
    apartment_min_road_width_mm: int = 9000
    notes: dict[str, str] = field(default_factory=dict)

    def resolve(self, plot_area_sqft: float) -> PlotBand:
        """Applicable band for a plot area in sqft. Clamps below the first band."""
        for b in self.bands:
            if b.contains(plot_area_sqft):
                return b
        if plot_area_sqft <= 0:
            raise ValueError("plot_area_sqft must be > 0")
        return self.bands[-1]

    def resolve_m2(self, plot_area_m2: float) -> PlotBand:
        return self.resolve(plot_area_m2 / SQFT_M2)

    def requires_rainwater_harvesting(self, plot_area_sqft: float) -> bool:
        return plot_area_sqft >= self.rainwater_harvesting_min_sqft

    # --- serialisation: this is what makes "another city is a data file" true

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2))

    @staticmethod
    def from_dict(d: dict) -> "CityProfile":
        bands = [
            PlotBand(
                **{**b,
                   "front": Setback(**b["front"]),
                   "rear": Setback(**b["rear"]),
                   "side": Setback(**b["side"])}
            ) for b in d["bands"]
        ]
        vd = d.get("vastu")
        vastu = (VastuProfile(**{**vd, "rules": [VastuRule(**r) for r in vd["rules"]]})
                 if vd else DEFAULT_VASTU)
        return CityProfile(
            name=d["name"], bands=bands,
            nbc=NBCMinima(**d.get("nbc", {})), vastu=vastu,
            rainwater_harvesting_min_sqft=d.get("rainwater_harvesting_min_sqft", 1200.0),
            stilt_exempt_from_far=d.get("stilt_exempt_from_far", True),
            apartment_min_road_width_mm=d.get("apartment_min_road_width_mm", 9000),
            notes=d.get("notes", {}),
        )

    @staticmethod
    def from_json(path: str | Path) -> "CityProfile":
        return CityProfile.from_dict(json.loads(Path(path).read_text()))


BENGALURU = CityProfile(
    name="Bengaluru (BBMP)",
    bands=BBMP_BANDS,
    notes={
        "rainwater_harvesting": "mandatory for plots >= 1200 sqft (BBMP)",
        "stilt": "stilt parking floor is exempt from FAR",
        "apartment_road_width": "apartments need an abutting road >= 9 m",
        "height_above_3875sqft": "BBMP ties height to abutting road width, not "
                                 "plot area; not encoded, reported as unchecked",
        "setback_geometry": "percentage setbacks are taken on the plot's "
                            "oriented bounding box (depth = front-rear axis)",
    },
)

PROFILES: dict[str, CityProfile] = {"bengaluru": BENGALURU}


def load_profile(name_or_path: str) -> CityProfile:
    """Named built-in profile, or a JSON file for a city we have not built in."""
    key = name_or_path.lower()
    if key in PROFILES:
        return PROFILES[key]
    return CityProfile.from_json(name_or_path)
