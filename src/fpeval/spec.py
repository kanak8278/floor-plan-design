"""Typed design brief -- the sole contract between the LLM and the solver.

Governing principle (DECISIONS.md #6): the LLM emits *specifications*, never
coordinates. A `DesignSpec` is a declarative statement of intent in the units a
client actually uses (feet, room counts, compass directions). The CP-SAT solver
owns every millimetre.

Two things live here and nowhere else:
  * `DesignSpec.validate()` -- purely structural rejection (contradictions,
    impossible totals, unknown categories). Cheap; runs before the solver is
    ever invoked. It is NOT a bye-law / NBC / Vastu checker -- that is the
    validator's job and it needs geometry.
  * `DesignSpec.to_json_schema()` -- the strict JSON Schema the LLM is
    constrained to emit. Keeping the schema next to the dataclass is what stops
    the two drifting apart.

Indian context notes baked into the vocabulary:
  * Plots are quoted in feet: 20x30, 30x40, 30x50, 40x60, 50x80.
  * "3BHK" = 3 bedrooms + hall (living) + kitchen. The hall and kitchen are
    implied by the label, not extra rooms.
  * Typical extras: pooja room, utility (wash area), sit-out (covered porch),
    covered parking, staircase to a G+1 storey.
  * "East facing" means the *road* is to the east, which is the entrance side.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional

SPEC_VERSION = "1.0"

SQFT_PER_M2 = 10.763910416709722
MM2_PER_SQFT = 92_903_040  # (304.8 mm)^2
MM_PER_FT = 304.8

# --------------------------------------------------------------------------
# Controlled vocabularies
# --------------------------------------------------------------------------

SIDES = ("north", "east", "south", "west")
ZONES = ("N", "NE", "E", "SE", "S", "SW", "W", "NW", "centre")
WET_GROUPING = ("required", "preferred", "indifferent")
VASTU_STRICTNESS = ("none", "advisory", "strict")
ADJACENCY_KINDS = ("required", "prohibited")
ADJACENCY_RELATIONS = ("adjacent", "direct_access", "visual", "same_floor")


@dataclass(frozen=True)
class CategoryInfo:
    """Defaults for a room category. Areas are in square feet (carpet)."""
    min_sqft: float
    max_sqft: float
    wet: bool = False
    habitable: bool = False       # counts toward FAR / needs light+ventilation
    outdoor: bool = False
    max_aspect: float = 1.8


# Ranges are deliberately generous -- they are *fallbacks* used when the client
# did not state a size, and the solver treats them as soft bounds.
ROOM_CATEGORIES: dict[str, CategoryInfo] = {
    # habitable
    "living":         CategoryInfo(120, 280, habitable=True, max_aspect=2.0),
    "dining":         CategoryInfo(80, 170, habitable=True),
    "kitchen":        CategoryInfo(60, 150, wet=True, habitable=True, max_aspect=2.4),
    "bedroom":        CategoryInfo(100, 180, habitable=True),
    "master_bedroom": CategoryInfo(130, 250, habitable=True),
    "guest_bedroom":  CategoryInfo(100, 165, habitable=True),
    "study":          CategoryInfo(60, 130, habitable=True),
    "office":         CategoryInfo(80, 160, habitable=True),
    "servant":        CategoryInfo(55, 100, habitable=True),
    # wet / service
    "bathroom":       CategoryInfo(30, 65, wet=True, max_aspect=2.0),
    "toilet":         CategoryInfo(16, 38, wet=True, max_aspect=2.2),
    "utility":        CategoryInfo(24, 75, wet=True, max_aspect=2.6),
    # non-habitable
    "pooja":          CategoryInfo(9, 45, max_aspect=1.6),
    "store":          CategoryInfo(18, 65, max_aspect=2.4),
    "dress":          CategoryInfo(28, 75, max_aspect=2.4),
    "staircase":      CategoryInfo(55, 120, max_aspect=2.6),
    "foyer":          CategoryInfo(20, 65, max_aspect=2.4),
    "corridor":       CategoryInfo(15, 100, max_aspect=6.0),
    # outdoor / semi-outdoor
    "balcony":        CategoryInfo(24, 90, outdoor=True, max_aspect=4.0),
    "sit_out":        CategoryInfo(35, 130, outdoor=True, max_aspect=3.0),
    "terrace":        CategoryInfo(60, 400, outdoor=True, max_aspect=4.0),
    "parking":        CategoryInfo(120, 260, outdoor=True, max_aspect=2.6),
    "garage":         CategoryInfo(150, 280, max_aspect=2.4),
}

WET_CATEGORIES = frozenset(k for k, v in ROOM_CATEGORIES.items() if v.wet)
OUTDOOR_CATEGORIES = frozenset(k for k, v in ROOM_CATEGORIES.items() if v.outdoor)
HABITABLE_CATEGORIES = frozenset(k for k, v in ROOM_CATEGORIES.items() if v.habitable)
BEDROOM_CATEGORIES = frozenset({"bedroom", "master_bedroom", "guest_bedroom"})

# Heuristic only. The authoritative bye-law tables live in bylaws.py (not ours).
# `coverage` is the fraction of the plot that survives setbacks, measured off
# typical small-plot residential setbacks; used purely for the
# "is this programme physically impossible" check.
CITY_PROFILES: dict[str, dict[str, float]] = {
    "generic_in":  {"coverage": 0.60, "far": 1.75},
    "bengaluru":   {"coverage": 0.60, "far": 1.75},
    "chennai":     {"coverage": 0.60, "far": 1.50},
    "hyderabad":   {"coverage": 0.65, "far": 2.00},
    "pune":        {"coverage": 0.55, "far": 1.50},
    "mumbai":      {"coverage": 0.55, "far": 1.33},
    "delhi":       {"coverage": 0.65, "far": 1.80},
    "ahmedabad":   {"coverage": 0.60, "far": 1.80},
    "kochi":       {"coverage": 0.60, "far": 2.00},
    "kolkata":     {"coverage": 0.60, "far": 1.75},
    "jaipur":      {"coverage": 0.65, "far": 1.75},
    "lucknow":     {"coverage": 0.60, "far": 1.75},
    "coimbatore":  {"coverage": 0.60, "far": 1.50},
}

STYLE_PACKS = (
    "default_in", "south_in_contemporary", "kerala_traditional",
    "north_in_haveli", "modern_minimal", "budget_rcc",
)


# --------------------------------------------------------------------------
# Sub-structures
# --------------------------------------------------------------------------

@dataclass
class RoomSpec:
    """One entry in the room programme.

    `min_sqft`/`max_sqft` is a *range*, not a target: the solver needs slack to
    satisfy the tiling, and a client who says "master bedroom around 12x14"
    means "roughly 170 sqft", not "exactly 168".
    """
    id: str
    category: str
    name: str = ""
    min_sqft: Optional[float] = None
    max_sqft: Optional[float] = None
    min_aspect: float = 1.0            # long/short, lower bound (1.0 == square ok)
    max_aspect: Optional[float] = None  # long/short, upper bound
    priority: int = 3                  # 1 = must have, 5 = nice to have
    optional: bool = False
    attached_bath: bool = False        # bedroom with en-suite (very common in IN)
    preferred_zone: Optional[str] = None   # compass zone, e.g. "NE" for pooja
    storey: Optional[int] = None       # 0 = ground; None = solver decides
    notes: str = ""

    def __post_init__(self) -> None:
        info = ROOM_CATEGORIES.get(self.category)
        if info is not None:
            if self.min_sqft is None:
                self.min_sqft = info.min_sqft
            if self.max_sqft is None:
                self.max_sqft = info.max_sqft
            if self.max_aspect is None:
                self.max_aspect = info.max_aspect
        if self.max_aspect is None:
            self.max_aspect = 2.0
        if not self.name:
            self.name = self.category.replace("_", " ").title()

    # -- unit bridges to the solver ----------------------------------------
    @property
    def min_area_mm2(self) -> int:
        return int(round((self.min_sqft or 0.0) * MM2_PER_SQFT))

    @property
    def max_area_mm2(self) -> int:
        return int(round((self.max_sqft or 0.0) * MM2_PER_SQFT))

    @property
    def is_wet(self) -> bool:
        return self.category in WET_CATEGORIES


@dataclass
class Adjacency:
    """A topological requirement or prohibition between two programme entries.

    `a`/`b` are `RoomSpec.id` values, or a bare category name (which means "any
    room of this category") -- the latter is how clients actually speak
    ("kitchen next to dining", "no toilet off the kitchen").
    """
    a: str
    b: str
    kind: str = "required"          # required | prohibited
    relation: str = "adjacent"      # adjacent | direct_access | visual | same_floor
    reason: str = ""

    def key(self) -> tuple[str, str, str]:
        lo, hi = sorted((self.a, self.b))
        return (lo, hi, self.relation)


@dataclass
class EntranceSpec:
    side: Optional[str] = None          # north | east | south | west
    zone: Optional[str] = None          # finer: "NE" corner of the east face
    via_foyer: bool = False
    avoid_direct_kitchen_view: bool = False


@dataclass
class VastuSpec:
    enabled: bool = False
    strictness: str = "none"            # none | advisory | strict
    requirements: list[str] = field(default_factory=list)
    # e.g. ["pooja_northeast", "kitchen_southeast", "master_southwest",
    #       "no_toilet_northeast", "brahmasthan_clear"]


# --------------------------------------------------------------------------
# The spec
# --------------------------------------------------------------------------

@dataclass
class DesignSpec:
    """The whole design brief, typed.

    Everything the solver needs and nothing it does not. Plot dimensions are
    `Optional` on purpose: an underdetermined prompt must yield `None` plus a
    clarifying question, never an invented 30x40.
    """
    # -- site ---------------------------------------------------------------
    plot_width_ft: Optional[float] = None     # dimension along the road
    plot_depth_ft: Optional[float] = None     # dimension away from the road
    road_facing_side: Optional[str] = None    # "east" == "east facing site"
    north_deg: float = 0.0                    # bearing of +Y, deg CW from north
    city_profile: str = "generic_in"
    corner_plot: bool = False

    # -- programme ----------------------------------------------------------
    rooms: list[RoomSpec] = field(default_factory=list)
    adjacency: list[Adjacency] = field(default_factory=list)
    entrance: EntranceSpec = field(default_factory=EntranceSpec)
    wet_grouping: str = "preferred"
    storeys: int = 1

    # -- style / soft --------------------------------------------------------
    style_pack: str = "default_in"
    vastu: VastuSpec = field(default_factory=VastuSpec)
    budget_band: Optional[str] = None         # e.g. "25-35L", "economy", "premium"
    family: Optional[str] = None              # e.g. "couple + 2 kids + parents"
    notes: str = ""

    spec_version: str = SPEC_VERSION
    provenance: dict = field(default_factory=dict)

    # ---------------------------------------------------------------- derived
    @property
    def plot_area_sqft(self) -> Optional[float]:
        if self.plot_width_ft is None or self.plot_depth_ft is None:
            return None
        return self.plot_width_ft * self.plot_depth_ft

    @property
    def plot_width_mm(self) -> Optional[int]:
        if self.plot_width_ft is None:
            return None
        return int(round(self.plot_width_ft * MM_PER_FT))

    @property
    def plot_depth_mm(self) -> Optional[int]:
        if self.plot_depth_ft is None:
            return None
        return int(round(self.plot_depth_ft * MM_PER_FT))

    def room(self, rid: str) -> Optional[RoomSpec]:
        return next((r for r in self.rooms if r.id == rid), None)

    def rooms_of(self, *categories: str) -> list[RoomSpec]:
        cats = set(categories)
        return [r for r in self.rooms if r.category in cats]

    @property
    def bedroom_count(self) -> int:
        return len(self.rooms_of(*BEDROOM_CATEGORIES))

    @property
    def bhk_label(self) -> str:
        """The Indian shorthand this programme corresponds to."""
        return f"{self.bedroom_count}BHK"

    def estimated_buildable_sqft(self) -> Optional[float]:
        area = self.plot_area_sqft
        if area is None:
            return None
        prof = CITY_PROFILES.get(self.city_profile, CITY_PROFILES["generic_in"])
        return area * prof["coverage"] * max(self.storeys, 1)

    def required_min_sqft(self) -> float:
        """Sum of minimum areas of non-optional rooms, outdoor excluded."""
        return sum(
            (r.min_sqft or 0.0)
            for r in self.rooms
            if not r.optional and r.category not in OUTDOOR_CATEGORIES
        )

    # -------------------------------------------------------------- validate
    def validate(self) -> list[str]:
        """Blocking structural problems. Empty list == structurally sound.

        Purely combinatorial / arithmetic. No geometry, no bye-laws, no Vastu
        adjudication -- those need a plan and are the validator's job. The point
        of this method is to reject nonsense for free.
        """
        errs: list[str] = []

        # --- site ---------------------------------------------------------
        if self.plot_width_ft is None or self.plot_depth_ft is None:
            errs.append("plot dimensions unknown: plot_width_ft/plot_depth_ft "
                        "must both be given before solving")
        else:
            for label, v in (("plot_width_ft", self.plot_width_ft),
                             ("plot_depth_ft", self.plot_depth_ft)):
                if v <= 0:
                    errs.append(f"{label}={v} must be positive")
                elif v < 10:
                    errs.append(f"{label}={v} is below 10 ft; not a buildable plot")
                elif v > 500:
                    errs.append(f"{label}={v} exceeds 500 ft; implausible residential plot")

        if self.road_facing_side is not None and self.road_facing_side not in SIDES:
            errs.append(f"road_facing_side={self.road_facing_side!r} not one of {SIDES}")
        if not (0.0 <= self.north_deg < 360.0):
            errs.append(f"north_deg={self.north_deg} outside [0, 360)")
        if self.city_profile not in CITY_PROFILES:
            errs.append(f"unknown city_profile={self.city_profile!r}")
        if self.style_pack not in STYLE_PACKS:
            errs.append(f"unknown style_pack={self.style_pack!r}")
        if self.storeys < 1 or self.storeys > 4:
            errs.append(f"storeys={self.storeys} outside supported range 1..4")
        if self.wet_grouping not in WET_GROUPING:
            errs.append(f"wet_grouping={self.wet_grouping!r} not one of {WET_GROUPING}")

        # --- programme ----------------------------------------------------
        if not self.rooms:
            errs.append("room programme is empty")

        seen: set[str] = set()
        for r in self.rooms:
            if not r.id:
                errs.append("a room has an empty id")
            elif r.id in seen:
                errs.append(f"duplicate room id {r.id!r}")
            seen.add(r.id)

            if r.category not in ROOM_CATEGORIES:
                errs.append(f"room {r.id!r}: unknown category {r.category!r}")

            lo, hi = r.min_sqft or 0.0, r.max_sqft or 0.0
            if lo <= 0:
                errs.append(f"room {r.id!r}: min_sqft={lo} must be positive")
            if hi < lo:
                errs.append(f"room {r.id!r}: max_sqft={hi} < min_sqft={lo}")
            if lo > 0 and hi > 0 and hi / lo > 12.0:
                errs.append(f"room {r.id!r}: area range {lo}-{hi} sqft is so wide "
                            "it carries no information")
            if r.min_aspect < 1.0:
                errs.append(f"room {r.id!r}: min_aspect={r.min_aspect} < 1.0 "
                            "(aspect is long/short, so it cannot be below 1)")
            if r.max_aspect is not None and r.max_aspect < r.min_aspect:
                errs.append(f"room {r.id!r}: max_aspect={r.max_aspect} < "
                            f"min_aspect={r.min_aspect}")
            if not (1 <= r.priority <= 5):
                errs.append(f"room {r.id!r}: priority={r.priority} outside 1..5")
            if r.preferred_zone is not None and r.preferred_zone not in ZONES:
                errs.append(f"room {r.id!r}: preferred_zone={r.preferred_zone!r} "
                            f"not one of {ZONES}")
            if r.storey is not None and not (0 <= r.storey < self.storeys):
                errs.append(f"room {r.id!r}: storey={r.storey} outside "
                            f"0..{self.storeys - 1}")
            if r.attached_bath and r.category not in BEDROOM_CATEGORIES:
                errs.append(f"room {r.id!r}: attached_bath set on a "
                            f"{r.category!r}, which is not a bedroom")

        # --- adjacency contradictions -------------------------------------
        known = seen | set(ROOM_CATEGORIES)
        req: set[tuple[str, str, str]] = set()
        pro: set[tuple[str, str, str]] = set()
        for adj in self.adjacency:
            if adj.kind not in ADJACENCY_KINDS:
                errs.append(f"adjacency {adj.a}~{adj.b}: kind={adj.kind!r} "
                            f"not one of {ADJACENCY_KINDS}")
                continue
            if adj.relation not in ADJACENCY_RELATIONS:
                errs.append(f"adjacency {adj.a}~{adj.b}: relation={adj.relation!r} "
                            f"not one of {ADJACENCY_RELATIONS}")
            for end in (adj.a, adj.b):
                if end not in known:
                    errs.append(f"adjacency references unknown room/category {end!r}")
            if adj.a == adj.b:
                errs.append(f"adjacency {adj.a!r} to itself is meaningless")
            (req if adj.kind == "required" else pro).add(adj.key())
        for k in sorted(req & pro):
            errs.append(f"adjacency {k[0]}~{k[1]} ({k[2]}) is both required "
                        "and prohibited")

        # --- entrance -----------------------------------------------------
        e = self.entrance
        if e.side is not None and e.side not in SIDES:
            errs.append(f"entrance.side={e.side!r} not one of {SIDES}")
        if e.zone is not None and e.zone not in ZONES:
            errs.append(f"entrance.zone={e.zone!r} not one of {ZONES}")
        if (e.side and self.road_facing_side and not self.corner_plot
                and e.side != self.road_facing_side):
            errs.append(f"entrance.side={e.side!r} is not the road-facing side "
                        f"{self.road_facing_side!r} on a non-corner plot: "
                        "the entrance would open onto a neighbour")

        # --- vastu --------------------------------------------------------
        if self.vastu.strictness not in VASTU_STRICTNESS:
            errs.append(f"vastu.strictness={self.vastu.strictness!r} not one of "
                        f"{VASTU_STRICTNESS}")
        if self.vastu.enabled and self.vastu.strictness == "none":
            errs.append("vastu.enabled is true but strictness is 'none'")
        if not self.vastu.enabled and self.vastu.strictness != "none":
            errs.append(f"vastu.strictness={self.vastu.strictness!r} set while "
                        "vastu.enabled is false")

        # --- impossible totals --------------------------------------------
        buildable = self.estimated_buildable_sqft()
        need = self.required_min_sqft()
        if buildable is not None and need > buildable:
            errs.append(
                f"programme cannot fit: required minimum {need:.0f} sqft of "
                f"enclosed rooms exceeds the ~{buildable:.0f} sqft buildable on "
                f"a {self.plot_width_ft:.0f}x{self.plot_depth_ft:.0f} ft plot "
                f"over {self.storeys} storey(s) at {self.city_profile} coverage"
            )

        # --- multi-storey coherence ---------------------------------------
        if self.storeys > 1 and not self.rooms_of("staircase"):
            errs.append(f"storeys={self.storeys} but the programme has no staircase")

        return errs

    def advisories(self) -> list[str]:
        """Non-blocking smells. The solver can still run."""
        out: list[str] = []
        buildable = self.estimated_buildable_sqft()
        need = self.required_min_sqft()
        if buildable and need > 0.9 * buildable:
            out.append(f"tight fit: {need:.0f} sqft required vs ~{buildable:.0f} "
                       "sqft buildable; expect the solver to struggle")
        if self.rooms and not self.rooms_of("kitchen"):
            out.append("no kitchen in the programme")
        if self.rooms and not self.rooms_of("living"):
            out.append("no living/hall in the programme")
        nbed = self.bedroom_count
        nbath = len(self.rooms_of("bathroom", "toilet"))
        if nbed and nbath == 0:
            out.append(f"{nbed} bedroom(s) and no bathroom or toilet")
        if nbed >= 3 and nbath < 2:
            out.append(f"{nbed} bedrooms share only {nbath} wet room(s)")
        if self.vastu.enabled and not self.vastu.requirements:
            out.append("vastu enabled but no specific requirements listed")
        if self.road_facing_side is None:
            out.append("road_facing_side unknown; entrance placement is unconstrained")
        if self.plot_area_sqft and self.plot_area_sqft < 600 and nbed >= 3:
            out.append(f"{nbed}BHK on a {self.plot_area_sqft:.0f} sqft plot is "
                       "aggressive for the Indian market")
        return out

    # ------------------------------------------------------------ (de)serial
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "DesignSpec":
        """Build from the LLM's JSON. Tolerant of missing keys, strict on types."""
        d = dict(d or {})
        rooms = [
            RoomSpec(**{k: v for k, v in (r or {}).items()
                        if k in RoomSpec.__dataclass_fields__})
            for r in d.pop("rooms", []) or []
        ]
        adjacency = [
            Adjacency(**{k: v for k, v in (a or {}).items()
                         if k in Adjacency.__dataclass_fields__})
            for a in d.pop("adjacency", []) or []
        ]
        ent = d.pop("entrance", None) or {}
        entrance = EntranceSpec(**{k: v for k, v in ent.items()
                                   if k in EntranceSpec.__dataclass_fields__})
        vas = d.pop("vastu", None) or {}
        vastu = VastuSpec(**{k: v for k, v in vas.items()
                             if k in VastuSpec.__dataclass_fields__})
        kept = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        kept.pop("rooms", None)
        # None must not clobber a non-Optional default
        for k in ("north_deg", "city_profile", "wet_grouping", "storeys",
                  "style_pack", "spec_version", "corner_plot", "notes"):
            if kept.get(k) is None:
                kept.pop(k, None)
        return cls(rooms=rooms, adjacency=adjacency, entrance=entrance,
                   vastu=vastu, **kept)

    # ---------------------------------------------------------------- schema
    @staticmethod
    def to_json_schema(*, strict: bool = True) -> dict:
        """JSON Schema for constrained LLM emission.

        `strict=True` produces the shape Anthropic's `strict: true` tool use
        wants: every property listed in `required`, `additionalProperties:
        false`, optionality expressed as a nullable type union rather than an
        absent key. `strict=False` drops the nullable unions and only requires
        the genuinely mandatory keys (fallback for a model/endpoint that
        rejects unions).
        """
        def nullable(base: dict) -> dict:
            if not strict:
                return base
            t = base.get("type")
            out = dict(base)
            if isinstance(t, str):
                out["type"] = [t, "null"]
            if "enum" in out and None not in out["enum"]:
                out["enum"] = list(out["enum"]) + [None]
            return out

        def obj(props: dict, required: list[str]) -> dict:
            return {
                "type": "object",
                "properties": props,
                "required": sorted(props) if strict else required,
                "additionalProperties": False,
            }

        room = obj(
            {
                "id": {"type": "string",
                       "description": "short stable slug, e.g. 'bed1', 'pooja'"},
                "category": {"type": "string", "enum": sorted(ROOM_CATEGORIES),
                             "description": "controlled vocabulary; do not invent"},
                "name": {"type": "string",
                         "description": "display name as the client would say it"},
                "min_sqft": nullable({"type": "number",
                                      "description": "null = use category default"}),
                "max_sqft": nullable({"type": "number"}),
                "min_aspect": {"type": "number",
                               "description": "long/short lower bound, >= 1.0"},
                "max_aspect": nullable({"type": "number",
                                        "description": "long/short upper bound"}),
                "priority": {"type": "integer", "enum": [1, 2, 3, 4, 5],
                             "description": "1 = must have, 5 = nice to have"},
                "optional": {"type": "boolean"},
                "attached_bath": {"type": "boolean",
                                  "description": "bedrooms only; en-suite bathroom"},
                "preferred_zone": nullable({"type": "string", "enum": list(ZONES)}),
                "storey": nullable({"type": "integer",
                                    "description": "0 = ground; null = solver decides"}),
                "notes": {"type": "string"},
            },
            ["id", "category"],
        )

        adjacency = obj(
            {
                "a": {"type": "string",
                      "description": "room id, or a bare category name meaning 'any'"},
                "b": {"type": "string"},
                "kind": {"type": "string", "enum": list(ADJACENCY_KINDS)},
                "relation": {"type": "string", "enum": list(ADJACENCY_RELATIONS)},
                "reason": {"type": "string",
                           "description": "why, in the client's words"},
            },
            ["a", "b", "kind"],
        )

        entrance = obj(
            {
                "side": nullable({"type": "string", "enum": list(SIDES)}),
                "zone": nullable({"type": "string", "enum": list(ZONES)}),
                "via_foyer": {"type": "boolean"},
                "avoid_direct_kitchen_view": {"type": "boolean"},
            },
            [],
        )

        vastu = obj(
            {
                "enabled": {"type": "boolean"},
                "strictness": {"type": "string", "enum": list(VASTU_STRICTNESS)},
                "requirements": {
                    "type": "array", "items": {"type": "string"},
                    "description": "e.g. pooja_northeast, kitchen_southeast, "
                                   "master_southwest, no_toilet_northeast",
                },
            },
            ["enabled", "strictness"],
        )

        return obj(
            {
                "plot_width_ft": nullable({
                    "type": "number",
                    "description": "plot dimension ALONG the road, in feet. "
                                   "null if the client did not state it -- never guess."}),
                "plot_depth_ft": nullable({
                    "type": "number",
                    "description": "plot dimension AWAY from the road, in feet. "
                                   "null if not stated -- never guess."}),
                "road_facing_side": nullable({
                    "type": "string", "enum": list(SIDES),
                    "description": "'east facing site' means road_facing_side='east'"}),
                "north_deg": {"type": "number",
                              "description": "bearing of +Y in degrees clockwise "
                                             "from north; 0 unless stated"},
                "city_profile": {"type": "string", "enum": sorted(CITY_PROFILES),
                                 "description": "generic_in if no city given"},
                "corner_plot": {"type": "boolean"},
                "rooms": {"type": "array", "items": room,
                          "description": "the full programme. 3BHK => 3 bedrooms "
                                         "PLUS a living/hall PLUS a kitchen."},
                "adjacency": {"type": "array", "items": adjacency},
                "entrance": entrance,
                "wet_grouping": {"type": "string", "enum": list(WET_GROUPING)},
                "storeys": {"type": "integer",
                            "description": "1 for single floor; G+1 / duplex = 2"},
                "style_pack": {"type": "string", "enum": list(STYLE_PACKS)},
                "vastu": vastu,
                "budget_band": nullable({"type": "string",
                                         "description": "verbatim, e.g. '30 lakhs'"}),
                "family": nullable({"type": "string",
                                    "description": "household as described"}),
                "notes": {"type": "string",
                          "description": "client requirements this schema cannot hold"},
            },
            ["rooms"],
        )


# --------------------------------------------------------------------------
# Programme helpers -- the Indian shorthand expanded
# --------------------------------------------------------------------------

def bhk_programme(
    bedrooms: int,
    *,
    baths: Optional[int] = None,
    pooja: bool = False,
    utility: bool = False,
    sit_out: bool = False,
    parking: bool = False,
    dining: bool = False,
    study: bool = False,
    store: bool = False,
    storeys: int = 1,
    attached_master_bath: bool = True,
) -> list[RoomSpec]:
    """Expand "NBHK + extras" into an explicit programme.

    N BHK = N bedrooms + hall (living) + kitchen. The first bedroom becomes the
    master. Bath count defaults to the Indian norm of roughly one per two
    bedrooms, minimum one, plus a common toilet from 2BHK up.
    """
    rooms: list[RoomSpec] = [
        RoomSpec(id="living", category="living", name="Hall", priority=1),
        RoomSpec(id="kitchen", category="kitchen", name="Kitchen", priority=1),
    ]
    for i in range(max(bedrooms, 0)):
        if i == 0:
            rooms.append(RoomSpec(id="bed1", category="master_bedroom",
                                  name="Master Bedroom", priority=1,
                                  attached_bath=attached_master_bath))
        else:
            rooms.append(RoomSpec(id=f"bed{i + 1}", category="bedroom",
                                  name=f"Bedroom {i + 1}", priority=2))
    if baths is None:
        baths = max(1, (bedrooms + 1) // 2)
    for i in range(baths):
        rooms.append(RoomSpec(id=f"bath{i + 1}", category="bathroom",
                              name=f"Bathroom {i + 1}", priority=1 if i == 0 else 2))
    if bedrooms >= 2:
        rooms.append(RoomSpec(id="toilet1", category="toilet",
                              name="Common Toilet", priority=2))
    if dining:
        rooms.append(RoomSpec(id="dining", category="dining", priority=2))
    if pooja:
        rooms.append(RoomSpec(id="pooja", category="pooja", name="Pooja Room",
                              priority=2, preferred_zone="NE"))
    if utility:
        rooms.append(RoomSpec(id="utility", category="utility",
                              name="Utility", priority=3))
    if study:
        rooms.append(RoomSpec(id="study", category="study", priority=4))
    if store:
        rooms.append(RoomSpec(id="store", category="store", priority=4,
                              optional=True))
    if sit_out:
        rooms.append(RoomSpec(id="sitout", category="sit_out", name="Sit-out",
                              priority=3))
    if parking:
        rooms.append(RoomSpec(id="parking", category="parking",
                              name="Covered Parking", priority=2))
    if storeys > 1:
        rooms.append(RoomSpec(id="stair", category="staircase",
                              name="Staircase", priority=1))
    return rooms


DEFAULT_ADJACENCY = [
    Adjacency("kitchen", "living", "required", "direct_access",
              "kitchen must be reachable from the hall"),
    Adjacency("kitchen", "dining", "required", "adjacent", "serving"),
    Adjacency("toilet", "kitchen", "prohibited", "adjacent",
              "no WC opening onto a kitchen"),
    Adjacency("bathroom", "kitchen", "prohibited", "direct_access",
              "no bathroom door into a kitchen"),
    Adjacency("pooja", "bathroom", "prohibited", "adjacent",
              "pooja must not share a wall with a wet room"),
    Adjacency("pooja", "toilet", "prohibited", "adjacent",
              "pooja must not share a wall with a WC"),
]


def default_indian_spec(**kwargs: Any) -> DesignSpec:
    """A structurally valid starting spec, for tests and as a solver smoke input."""
    spec = DesignSpec(
        plot_width_ft=30.0, plot_depth_ft=40.0, road_facing_side="east",
        city_profile="bengaluru",
        rooms=bhk_programme(2, pooja=True, sit_out=True, parking=True),
        adjacency=[a for a in DEFAULT_ADJACENCY if a.b != "dining"],
        entrance=EntranceSpec(side="east", zone="E"),
    )
    for k, v in kwargs.items():
        setattr(spec, k, v)
    return spec
