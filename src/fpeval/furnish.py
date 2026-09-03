"""Relational furnishing specs + a geometric placement solver.

**The LLM selects; the solver places.** Which items belong in a kitchen is a
closed-enum choice a model makes well. Where the hob goes is integer constraint
satisfaction against a polygon, and a sofa clipping a wall is something a
layperson spots in one second. So every coordinate in this module is computed
here, and the LLM-facing surface (`RULES`, a policy dict) is symbolic.

Why not the fork's `roomTemplates.ts`: it places furniture at absolute offsets
from the room centre, commented *"Default room size is 400x300 so extents are
+/-200 x +/-150"*, e.g. `bed_queen` at `y = -30`. On a CP-SAT 3.2x3.8 m bedroom
that bed is 850 mm inside the wall; on an L-shape it is outside the room. The
item *choices* there are sound, so they are reused; the mechanism is replaced by
anchors + clearances resolved against the real polygon.

Geometry notes that cost measurements to learn:
  * Room polygons in this IR tile at wall **centrelines** (both the ResPlan
    inversion and `solver.py` emit them that way), so the usable interior is
    `room.polygon - union(wall bodies)`. Insetting by a constant is wrong: wall
    thickness varies per wall, and `rules.py` already documents that some
    producers inset to wall *faces* instead. Subtracting the wall mass is
    correct under both conventions and is also what makes "no item overlaps a
    wall" true by construction rather than by assertion.
  * `rotation` 0 = facing +Y (ir.py), and every icon in `furnitureIcons.ts`
    draws its back at local -Y, so a wall-anchored item has its back plane on
    the wall and `rotation = atan2(-nx, ny)` for inward normal `n`.
  * Determinism: no RNG anywhere (the `seed` argument is accepted for API
    stability and recorded in the report). Candidates are enumerated on a fixed
    50 mm grid, scores are rounded to 6 dp, and every tie breaks on
    `(x, y, rotation)`. No iteration over a `set` — PYTHONHASHSEED would leak in.
"""
from __future__ import annotations

import copy
import json
import math
import time
from dataclasses import dataclass, field, asdict, replace
from typing import Any, Optional, Sequence

from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

from . import catalog, roomtypes
from .envelope import VASTU_BEARING
from .ir import Furniture, P, Plan, Room, Wall
from .rules import bearing_deg, zone_of_bearing

# ---------------------------------------------------------------- constants

GRID_MM = 50            # placement grid; matches solver.GRID_MM so nothing lands off-grid
MAX_SAMPLES = 40        # per wall run; 40 x 50 mm covers a 2 m slide window densely
WALL_TOL_MM = 8         # containment slop for integer rounding of a rotated rect
ITEM_GAP_MM = 25        # air between two bodies so the editor's 1 px stroke does not kiss
DOOR_JAMB_MM = 60       # keep the frame reveal clear
SWING_MIN_MM = 600      # a doorway you cannot walk through is blocked even with no leaf
SWING_MAX_MM = 1000     # cap: a 1800 sliding opening does not sweep 1800 of floor
WINDOW_KEEP_MM = 250    # inward strip a window needs for light and for opening the sash
WINDOW_TALL_MARGIN = 150  # item is "tall over a window" if height > sill + this
CIRC_MM = 600           # walkable width inside a room
CIRC_WET_MM = 450       # a 1200 mm toilet cannot offer 600 past a 900 shower tray
MIN_CLEAR_M2 = 0.9      # below this the room is not furnishable at all
COUNTER_MAX_MM = 1200   # one `counter` tile; longer runs are tiled
COUNTER_MIN_MM = 300    # a 300 filler is real joinery; below that, skip
DIRECTIONS8 = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


# ---------------------------------------------------------------- spec model

@dataclass(frozen=True)
class Spec:
    """One relational furnishing rule. Never contains a coordinate.

    `anchor` is how the item is attached, not where it is:
      wall      back plane flush to a wall run, facing into the room
      corner    flush to two runs at a convex corner
      beside    alongside `of`, on the same wall, `gap` apart
      front_of  in front of `of`, `gap` clear of its front face
      facing    wall-anchored, but required to look at `of`
      around    ringed about `of` (dining chairs)
      center    at the room's inscribed centre (a free-standing table)
      run       wall-anchored but restricted to the kitchen counter run
    """
    key: str
    item: str                                  # logical name or catalog id
    anchor: str = "wall"
    of: Optional[str] = None
    side: str = "both"                         # beside: left | right | both
    count: int = 1
    gap: int = 0
    clear_front: int = 0
    prefer: tuple[str, ...] = ()               # compass zones, scored not enforced
    avoid: tuple[str, ...] = ()
    optional: bool = False
    min_room_m2: float = 0.0                   # below this, drop without trying
    align: str = "any"                         # any | center | flush
    width: int = 0                             # mm override; 0 = catalogue/DIM_OVERRIDES
    depth: int = 0
    height: int = 0
    level: int = 0                             # 0 always, 1 normal+, 2 dense only
    away_from_door: int = 0                    # penalise centres nearer than this
    not_facing_door: bool = False              # WC privacy
    min_gap_to: tuple[tuple[str, int], ...] = ()   # (other spec key, mm) hard
    into: tuple[str, ...] = ()                 # clearance zones this may occupy
    avoid_window: Optional[bool] = None        # None = derive from height
    shrink_to: int = 0                         # retry narrower down to this (mm)
    abut: bool = False                         # may touch a neighbour (joinery)
    note: str = ""

    @property
    def catalog_id(self) -> str:
        return catalog.resolve(self.item)


def _dims(spec: Spec) -> tuple[int, int, int]:
    w, d, h = catalog.dims(spec.catalog_id)
    return (spec.width or w, spec.depth or d, spec.height or h)


# ---------------------------------------------------------------- rule table
# The default policy. Item choices follow `roomTemplates.ts` where they are
# sensible and Indian practice where they are not (600 mm kitchen platform, a
# wardrobe in every bedroom, a shower instead of a bathtub).

RULES: dict[str, tuple[Spec, ...]] = {
    "living": (
        Spec("sofa", "sofa", "wall", clear_front=750, align="center",
             min_room_m2=8.0, away_from_door=700, shrink_to=1400,
             note="longest free run; 750 in front is one person edging past. "
                  "Shrinks to 1400 (a loveseat) rather than leaving the room "
                  "empty: measured 2.5% of ResPlan living rooms have no 2 m "
                  "wall clear of a door swing"),
        Spec("tv_stand", "tv_stand", "facing", of="sofa", gap=2000, optional=True,
             min_room_m2=10.0, avoid_window=True, shrink_to=900,
             note="2.0 m is the shortest comfortable throw for a 43in panel"),
        Spec("coffee_table", "coffee_table", "front_of", of="sofa", gap=400,
             optional=True, min_room_m2=11.0, into=("sofa",),
             note="400 shin room; it is *meant* to sit in the sofa's clearance"),
        Spec("chair", "chair", "corner", optional=True, min_room_m2=16.0, level=1),
        Spec("bookshelf", "bookshelf", "wall", optional=True, min_room_m2=14.0,
             level=1, avoid_window=True, shrink_to=500),
        Spec("potted_plant", "potted_plant", "corner", optional=True,
             min_room_m2=18.0, level=2),
    ),
    "dining": (
        Spec("dining_table", "dining_table", "center", min_room_m2=7.0,
             note="free-standing; chairs need 750 all round, checked as clearance"),
        Spec("dining_chair", "dining_chair", "around", of="dining_table", count=4,
             gap=80, optional=True, min_room_m2=8.0, into=("dining_table",)),
        Spec("storage", "storage", "wall", optional=True, min_room_m2=14.0, level=1),
    ),
    "bedroom": (
        Spec("bed", "bed_queen", "wall", clear_front=750, align="center",
             min_room_m2=7.5, away_from_door=600,
             note="headboard to wall; 1500x2000 (see catalog.DIM_OVERRIDES)"),
        Spec("nightstand_a", "nightstand", "beside", of="bed", side="left", gap=50,
             optional=True, min_room_m2=9.0),
        Spec("nightstand_b", "nightstand", "beside", of="bed", side="right", gap=50,
             optional=True, min_room_m2=10.5),
        Spec("wardrobe", "wardrobe", "wall", prefer=("S", "W"), optional=True,
             min_room_m2=8.5, align="flush", avoid_window=True, clear_front=500,
             shrink_to=900,
             note="500 for a sliding shutter, which is what builders fit; "
                  "tall, so it must not cover a window"),
        Spec("dresser", "dresser", "wall", optional=True, min_room_m2=15.0, level=1,
             shrink_to=800),
    ),
    "master_bedroom": (
        Spec("bed", "bed_queen", "wall", width=1800, depth=2000, clear_front=750,
             align="center", min_room_m2=9.5, away_from_door=600,
             note="1800 king; the master is requested at 12-24 m2"),
        Spec("nightstand_a", "nightstand", "beside", of="bed", side="left", gap=50,
             optional=True, min_room_m2=11.0),
        Spec("nightstand_b", "nightstand", "beside", of="bed", side="right", gap=50,
             optional=True, min_room_m2=12.0),
        Spec("wardrobe", "wardrobe", "wall", width=1800, prefer=("S", "W"),
             optional=True, min_room_m2=11.0, align="flush", avoid_window=True,
             clear_front=500, shrink_to=900),
        Spec("dresser", "dresser", "wall", optional=True, min_room_m2=16.0, level=1,
             shrink_to=800),
        Spec("chair", "chair", "corner", optional=True, min_room_m2=20.0, level=2),
    ),
    "kitchen": (
        # The run itself is built by `_furnish_kitchen`; these are the fixtures
        # that sit *in* the run, so they are placed before the counter tiles.
        Spec("sink", "kitchen_sink", "run", depth=600, prefer=("NE", "N"),
             avoid=("SW",), min_room_m2=4.0, into=(),
             note="Vastu: water NE/N. Depth forced to the 600 platform line"),
        Spec("hob", "hob", "run", depth=600, prefer=("SE",), avoid=("NE", "SW"),
             min_room_m2=4.0, min_gap_to=(("sink", 600),),
             note="Agni SE; 600 fire/water separation is the rule practitioners state"),
        Spec("fridge", "fridge", "run", align="flush", optional=True,
             min_room_m2=5.5, avoid_window=True, min_gap_to=(("hob", 300),),
             note="at a run end; 1800 tall so it must not stand over a window"),
        Spec("washer_dryer", "washing_machine", "run", optional=True, note="dropped when a utility exists; the utility is where it belongs",
             min_room_m2=9.0, level=1),
    ),
    "bathroom": (
        Spec("wc", "squat_wc", "wall", clear_front=550, min_room_m2=1.6,
             prefer=("NW", "W"), avoid=("NE", "SE"), not_facing_door=True,
             note="NBC gives 2.8 m2 for a WC+bath; 1.6 is where the pan alone fits"),
        Spec("basin", "sink_b", "wall", clear_front=550, min_room_m2=2.2,
             optional=True, min_gap_to=(("wc", 150),)),
        Spec("shower", "shower", "corner", optional=True, min_room_m2=2.8,
             clear_front=0,
             note="900x900 corner cubicle. Exempting it from the window rule was "
                  "tried and reverted: the catalogue item is 2100 tall, so 36 of "
                  "6155 placements on ResPlan then stood a full-height enclosure "
                  "across a window. A tray-only shower needs a shorter item"),
        Spec("washer_dryer", "washing_machine", "wall", optional=True,
             min_room_m2=6.0, level=1),
    ),
    "study": (
        Spec("desk", "desk", "wall", clear_front=750, min_room_m2=4.5,
             note="back to wall; 750 is a pulled-out chair"),
        Spec("office_chair", "office_chair", "front_of", of="desk", gap=100,
             optional=True, min_room_m2=5.0, into=("desk",)),
        Spec("bookshelf", "bookshelf", "wall", optional=True, min_room_m2=6.5,
             avoid_window=True),
    ),
    "pooja": (
        Spec("mandir", "pooja_mandir", "wall", prefer=("NE", "E", "N"),
             avoid=("S", "SW"), align="center", min_room_m2=0.8,
             note="substituted item; see catalog.SUBSTITUTIONS['pooja_mandir']"),
    ),
    "utility": (
        Spec("washer_dryer", "washing_machine", "wall", min_room_m2=1.2,
             prefer=("NW", "W")),
        Spec("sink", "kitchen_sink", "wall", depth=600, optional=True,
             min_room_m2=2.6),
        Spec("storage", "storage", "wall", optional=True, min_room_m2=4.5, level=1),
    ),
    "store": (
        Spec("storage_a", "storage", "wall", align="flush", min_room_m2=1.0,
             shrink_to=500),
        Spec("storage_b", "storage", "wall", align="flush", optional=True,
             min_room_m2=3.5, shrink_to=500),
    ),
    "foyer": (
        Spec("shoe_rack", "shoe_rack", "wall", align="flush", optional=True,
             min_room_m2=1.6, away_from_door=900, shrink_to=500),
        Spec("side_table", "side_table", "corner", optional=True,
             min_room_m2=3.5, level=1),
    ),
    "balcony": (
        Spec("patio_chair_a", "patio_chair", "corner", optional=True,
             min_room_m2=1.6),
        Spec("patio_table", "patio_table", "wall", optional=True, min_room_m2=3.0,
             level=1),
        Spec("outdoor_pot", "outdoor_pot_small", "corner", optional=True,
             min_room_m2=2.2, level=1),
    ),
    "sitout": (
        Spec("bench", "bench_outdoor", "wall", align="center", optional=True,
             min_room_m2=2.0),
        Spec("patio_chair_a", "patio_chair", "corner", optional=True,
             min_room_m2=4.0, level=1),
        Spec("outdoor_pot", "outdoor_pot_large", "corner", optional=True,
             min_room_m2=5.0, level=1),
    ),
    "patio": (
        Spec("patio_table", "patio_table", "center", optional=True, min_room_m2=5.0),
        Spec("patio_chair_a", "patio_chair", "around", of="patio_table", count=4,
             gap=80, optional=True, min_room_m2=6.0, into=("patio_table",)),
    ),
    "parking": (
        Spec("car", "car_hatchback", "wall", align="center", min_room_m2=10.0,
             note="substituted: catalogue has no hatchback"),
        Spec("two_wheeler", "two_wheeler", "wall", align="flush", optional=True,
             min_room_m2=16.0, note="the Indian item the western catalogue does have"),
    ),
    "landscape": (
        Spec("tree", "tree_default", "corner", optional=True, min_room_m2=6.0),
        Spec("bush_a", "bush", "corner", optional=True, min_room_m2=4.0, level=1),
    ),
}

DEFAULT_KITCHEN = {
    "counter_run": "L",            # I | L | U
    "platform_depth_mm": 600,      # Indian granite platform
    "hob_sink_min_mm": 600,        # fire/water separation
    "hob_zone": ("SE",),
    "sink_zone": ("NE", "N"),
}


# ---------------------------------------------------------------- policy

@dataclass
class ResolvedPolicy:
    """The rule table after a policy dict has been merged into it.

    Kept as its own object with a printable diff so a config panel and a prompt
    patch are one pipeline: both produce a policy dict, both show the user the
    same "kitchen.counter_run: L -> U" line.
    """
    specs: dict[str, tuple[Spec, ...]]
    kitchen: dict[str, Any]
    density: str = "normal"
    diff: tuple[str, ...] = ()

    @property
    def level(self) -> int:
        return {"sparse": 0, "normal": 1, "dense": 2}.get(self.density, 1)


def _spec_from_dict(d: dict[str, Any]) -> Spec:
    fields = {f for f in Spec.__dataclass_fields__}
    bad = sorted(k for k in d if k not in fields)
    if bad:
        raise ValueError(f"unknown Spec fields {bad}; allowed {sorted(fields)}")
    kw = dict(d)
    for k in ("prefer", "avoid", "into"):
        if k in kw:
            kw[k] = tuple(kw[k])
    if "min_gap_to" in kw:
        kw["min_gap_to"] = tuple((str(a), int(b)) for a, b in kw["min_gap_to"])
    return Spec(**kw)


def resolve_policy(policy: dict[str, Any] | None = None) -> ResolvedPolicy:
    """Merge a policy dict over `RULES`, recording every change as a diff line.

    Accepted shape (all keys optional):

        {"density": "sparse|normal|dense",
         "kitchen": {"counter_run": "U", ...},
         "swaps": {"bed_queen": "bed_twin"},
         "rooms": {"bedroom": {"remove": ["dresser"],
                               "items": {"wardrobe": {"optional": False}},
                               "add": [{"key": "rug", "item": "rug",
                                        "anchor": "center", "optional": True}]}}}
    """
    pol = policy or {}
    unknown = sorted(k for k in pol if k not in ("density", "kitchen", "swaps",
                                                 "rooms"))
    if unknown:
        raise ValueError(f"unknown policy keys {unknown}")

    specs = {k: tuple(v) for k, v in RULES.items()}
    kitchen = dict(DEFAULT_KITCHEN)
    diff: list[str] = []

    density = pol.get("density", "normal")
    if density not in ("sparse", "normal", "dense"):
        raise ValueError(f"density must be sparse|normal|dense, got {density!r}")
    if density != "normal":
        diff.append(f"density: normal -> {density}")

    for k, v in sorted(pol.get("kitchen", {}).items()):
        if k not in kitchen:
            raise ValueError(f"unknown kitchen policy key {k!r}")
        if k.endswith("_zone"):
            v = tuple(v)
        if kitchen[k] != v:
            diff.append(f"kitchen.{k}: {kitchen[k]} -> {v}")
            kitchen[k] = v
    if kitchen["counter_run"] not in ("I", "L", "U"):
        raise ValueError("kitchen.counter_run must be I, L or U")

    swaps = {str(a): str(b) for a, b in sorted(pol.get("swaps", {}).items())}
    for a, b in swaps.items():
        catalog.get(catalog.resolve(b))                    # fail loudly, now
        diff.append(f"swap {a} -> {b}")
    if swaps:
        for rk in sorted(specs):
            specs[rk] = tuple(
                replace(s, item=swaps[s.item]) if s.item in swaps else s
                for s in specs[rk])

    for rk, edits in sorted(pol.get("rooms", {}).items()):
        if rk not in specs:
            raise ValueError(f"unknown furnish key {rk!r}; have {sorted(specs)}")
        bad = sorted(k for k in edits if k not in ("add", "remove", "items"))
        if bad:
            raise ValueError(f"rooms.{rk}: unknown keys {bad}")
        cur = list(specs[rk])
        for key in sorted(edits.get("remove", [])):
            hit = [s for s in cur if s.key == key]
            if not hit:
                raise ValueError(f"rooms.{rk}.remove: no spec keyed {key!r}")
            cur = [s for s in cur if s.key != key]
            diff.append(f"{rk}: -{key}")
        for key in sorted(edits.get("items", {})):
            over = edits["items"][key]
            hit = next((s for s in cur if s.key == key), None)
            if hit is None:
                raise ValueError(f"rooms.{rk}.items: no spec keyed {key!r}")
            new = hit
            for f in sorted(over):
                if f not in Spec.__dataclass_fields__:
                    raise ValueError(f"rooms.{rk}.items.{key}: unknown field {f!r}")
                val = over[f]
                if f in ("prefer", "avoid", "into"):
                    val = tuple(val)
                if getattr(new, f) != val:
                    diff.append(f"{rk}.{key}.{f}: {getattr(new, f)!r} -> {val!r}")
                    new = replace(new, **{f: val})
            cur = [new if s.key == key else s for s in cur]
        for d in edits.get("add", []):
            s = _spec_from_dict(d)
            if any(x.key == s.key for x in cur):
                raise ValueError(f"rooms.{rk}.add: duplicate key {s.key!r}")
            cur.append(s)
            diff.append(f"{rk}: +{s.key} ({s.item})")
        specs[rk] = tuple(cur)

    # A bad catalog id draws nothing in the editor, so validate the whole
    # resolved table up front rather than discovering it per-plan.
    catalog.require(sorted({s.catalog_id for v in specs.values() for s in v}),
                    "resolved furnishing policy")
    return ResolvedPolicy(specs=specs, kitchen=kitchen, density=density,
                          diff=tuple(diff))


# ---------------------------------------------------------------- report

@dataclass
class Placement:
    id: str
    room_id: str
    room_key: str
    spec_key: str
    catalog_id: str
    x: int
    y: int
    rotation: float
    width: int
    depth: int
    height: int
    score: float
    why: str


@dataclass
class Drop:
    room_id: str
    room_key: str
    spec_key: str
    catalog_id: str
    reason: str


@dataclass
class RoomResult:
    room_id: str
    name: str
    room_key: str
    area_m2: float
    clear_m2: float
    n_doors: int
    n_windows: int
    placed: tuple[str, ...]
    dropped: tuple[str, ...]
    ms: float


@dataclass
class FurnishReport:
    plan_id: str
    seed: int
    density: str
    policy_diff: tuple[str, ...]
    placements: list[Placement] = field(default_factory=list)
    drops: list[Drop] = field(default_factory=list)
    rooms: list[RoomResult] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)   # (room_id, why)
    ms: float = 0.0

    @property
    def n_placed(self) -> int: return len(self.placements)

    def by_room_key(self) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for p in self.placements:
            out.setdefault(p.room_key, {})
            out[p.room_key][p.catalog_id] = out[p.room_key].get(p.catalog_id, 0) + 1
        return out

    def drop_reasons(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for d in self.drops:
            head = d.reason.split(";")[0].split("(")[0].strip()
            out[head] = out.get(head, 0) + 1
        return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))

    def fingerprint(self) -> str:
        """Everything the solver decided, with wall-clock timings removed.

        `ms` fields legitimately vary run to run, so a byte-identity check has
        to be taken over the decisions, not over the whole report.
        """
        return json.dumps({
            "plan_id": self.plan_id, "seed": self.seed, "density": self.density,
            "policy_diff": list(self.policy_diff),
            "placements": [asdict(p) for p in self.placements],
            "drops": [asdict(d) for d in self.drops],
            "rooms": [{k: v for k, v in asdict(r).items() if k != "ms"}
                      for r in self.rooms],
            "skipped": [list(s) for s in self.skipped],
        }, sort_keys=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id, "seed": self.seed, "density": self.density,
            "policy_diff": list(self.policy_diff), "ms": round(self.ms, 3),
            "n_placed": len(self.placements), "n_dropped": len(self.drops),
            "placements": [asdict(p) for p in self.placements],
            "drops": [asdict(d) for d in self.drops],
            "rooms": [asdict(r) for r in self.rooms],
            "skipped": [list(s) for s in self.skipped],
        }

    def text(self) -> str:
        ls = [f"furnish {self.plan_id}: {len(self.placements)} placed, "
              f"{len(self.drops)} dropped, {self.ms:.1f} ms, density={self.density}"]
        for d in self.policy_diff:
            ls.append(f"  policy  {d}")
        for r in self.rooms:
            ls.append(f"  {r.name:<16} {r.room_key:<15} {r.area_m2:5.1f} m2 "
                      f"clear {r.clear_m2:5.1f}  +{len(r.placed)} -{len(r.dropped)}")
        for d in self.drops:
            ls.append(f"  drop {d.room_id}/{d.spec_key:<14} {d.reason}")
        return "\n".join(ls)


# ---------------------------------------------------------------- geometry

def _poly(pts: Sequence[P]) -> Polygon:
    return Polygon([(p.x, p.y) for p in pts])


def _largest(g) -> Optional[Polygon]:
    if g is None or g.is_empty:
        return None
    if isinstance(g, Polygon):
        return g
    parts = [p for p in getattr(g, "geoms", []) if isinstance(p, Polygon)
             and p.area > 0]
    if not parts:
        return None
    return max(parts, key=lambda p: (p.area, p.bounds))


def _parts(g) -> list[Polygon]:
    if g is None or g.is_empty:
        return []
    if isinstance(g, Polygon):
        return [g]
    return [p for p in getattr(g, "geoms", []) if isinstance(p, Polygon) and p.area > 0]


def _erode(p: Polygon, r: float):
    if r <= 0:
        return p
    return p.buffer(-r, join_style=2, mitre_limit=2.0)


def facing(rot: float) -> tuple[float, float]:
    """Unit vector the item looks along. 0 = +Y (ir.Furniture contract)."""
    a = math.radians(rot)
    return (-math.sin(a), math.cos(a))


def rot_for_normal(nx: float, ny: float) -> float:
    """Rotation that makes an item face along `n` (its back then hits the wall)."""
    r = math.degrees(math.atan2(-nx, ny)) % 360.0
    snap = round(r / 90.0) * 90.0 % 360.0
    return snap if abs(((r - snap + 180) % 360) - 180) < 0.5 else round(r, 6)


def footprint(cx: float, cy: float, w: float, d: float, rot: float) -> Polygon:
    """Exact rotated rectangle. Width runs along local X, depth along local Y."""
    a = math.radians(rot)
    ca, sa = math.cos(a), math.sin(a)
    hw, hd = w / 2.0, d / 2.0
    pts = []
    for lx, ly in ((-hw, -hd), (hw, -hd), (hw, hd), (-hw, hd)):
        pts.append((cx + lx * ca - ly * sa, cy + lx * sa + ly * ca))
    return Polygon(pts)


def _front_rect(cx: float, cy: float, w: float, d: float, rot: float,
                depth: float) -> Polygon:
    """The clearance slab in front of an item, same width, `depth` deep."""
    fx, fy = facing(rot)
    ox, oy = cx + fx * (d / 2.0 + depth / 2.0), cy + fy * (d / 2.0 + depth / 2.0)
    return footprint(ox, oy, w, depth, rot)


def _ang_gap(a: float, b: float) -> float:
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def zone_score(bearing: float, prefer: Sequence[str], avoid: Sequence[str]) -> float:
    """Continuous 0..1 zone preference, like `rules.vastu_score`.

    Continuous rather than in/out because a hob 20 deg off SE is not a failure
    and a hard test would drop the hob in most real kitchens.
    """
    s = 0.5
    if prefer:
        best = min(_ang_gap(bearing, VASTU_BEARING[z]) for z in prefer)
        s = max(0.0, 1.0 - best / 90.0)
    if avoid:
        near = min(_ang_gap(bearing, VASTU_BEARING[z]) for z in avoid)
        s *= max(0.0, min(1.0, near / 90.0))
    return s


# ---------------------------------------------------------------- room context

@dataclass
class _Run:
    """One straight stretch of usable wall face, with its inward normal."""
    ax: float
    ay: float
    ux: float
    uy: float
    nx: float
    ny: float
    length: float
    rot: float

    def at(self, s: float, back: float = 0.0) -> tuple[float, float]:
        return (self.ax + self.ux * s + self.nx * back,
                self.ay + self.uy * s + self.ny * back)

    def project(self, x: float, y: float) -> float:
        return (x - self.ax) * self.ux + (y - self.ay) * self.uy


@dataclass
class _Door:
    op_id: str
    x: float
    y: float                 # centre on the wall centreline
    nx: float
    ny: float                # inward normal
    width: int
    keepout: Polygon
    approach: tuple[float, float]


@dataclass
class _Win:
    op_id: str
    sill: int
    keepout: Polygon


@dataclass
class _Ctx:
    room: Room
    room_key: str
    furnish_key: str
    poly: Polygon
    clear: Polygon
    clear_pad: Polygon
    runs: list[_Run]
    doors: list[_Door]
    windows: list[_Win]
    door_keepout: Optional[Polygon]
    cx: float
    cy: float
    north: float
    circ_mm: float
    circ_baseline_ok: bool

    def bearing(self, x: float, y: float) -> float:
        return bearing_deg(x - self.cx, y - self.cy, self.north)

    def zone(self, x: float, y: float) -> str:
        return zone_of_bearing(self.bearing(x, y))


def wall_mass(plan: Plan) -> Optional[Polygon]:
    """Union of every wall body. Square caps so junction corners are covered."""
    bods = []
    for w in plan.walls:
        if w.length < 1.0 or w.thickness <= 0:
            continue
        from shapely.geometry import LineString
        bods.append(LineString([w.start.as_tuple(), w.end.as_tuple()])
                    .buffer(w.thickness / 2.0, cap_style=3, join_style=2))
    if not bods:
        return None
    return unary_union(bods)


def _room_key(room: Room) -> str:
    """Canonical type for a room from `name` and `category` together.

    Neither field alone is right. `envelope.bhk_programme` puts the only clue
    that a bedroom is the master in `name` ("Master Bedroom", category
    "bedroom"), so name has to win there. But `solver` names its circulation
    room "Hall" (canonical -> living) with category "passage" (-> foyer), and
    furnishing that as a living room puts a sofa in the corridor. Rule: when the
    two disagree across NBC classes, trust the machine-set category; when they
    agree on class, trust the more specific name.
    """
    kc = roomtypes.canonical(room.category or "")
    kn = roomtypes.canonical(room.name or "")
    if kc == kn:
        return kc
    if kc != "unknown" and kn != "unknown":
        tc, tn = roomtypes.T[kc], roomtypes.T[kn]
        return kc if tc.klass != tn.klass else kn
    if kc != "unknown":
        return kc
    if kn != "unknown":
        return kn
    return room.category if room.category in roomtypes.T else "unknown"


def _openings_for(plan: Plan, room: Room, poly: Polygon,
                  walls: dict[str, Wall]) -> tuple[list[_Door], list[_Win]]:
    """Probe perpendicular to each opening's host wall to see if it serves us.

    `Room.wall_ids` cannot answer this: a living room lists 16 walls and a long
    wall is shared by three rooms, so membership does not localise an opening.
    Same probing trick `rules._door_topology` uses, for the same reason.
    """
    doors: list[_Door] = []
    wins: list[_Win] = []
    for op in plan.openings:
        w = walls.get(op.wall_id)
        if w is None or w.length < 1.0:
            continue
        L = w.length
        ux = (w.end.x - w.start.x) / L
        uy = (w.end.y - w.start.y) / L
        t = min(max(op.position, 0.0), 1.0)
        px = w.start.x + t * (w.end.x - w.start.x)
        py = w.start.y + t * (w.end.y - w.start.y)
        half = w.thickness / 2.0
        inward = None
        for sgn in (1.0, -1.0):
            nx, ny = -uy * sgn, ux * sgn
            for off in (half + 120.0, half + 350.0, 60.0):
                if poly.contains(Point(px + nx * off, py + ny * off)):
                    inward = (nx, ny)
                    break
            if inward:
                break
        if inward is None:
            continue
        nx, ny = inward
        if op.kind in ("door", "front_door"):
            depth = half + min(SWING_MAX_MM, max(SWING_MIN_MM, op.width))
            span = op.width + 2 * DOOR_JAMB_MM
            mx, my = px + nx * depth / 2.0, py + ny * depth / 2.0
            ko = footprint(mx, my, span, depth, rot_for_normal(nx, ny))
            ap = (px + nx * (half + 300.0), py + ny * (half + 300.0))
            doors.append(_Door(op.id, px, py, nx, ny, op.width, ko, ap))
        elif op.kind == "window":
            depth = half + WINDOW_KEEP_MM
            mx, my = px + nx * depth / 2.0, py + ny * depth / 2.0
            ko = footprint(mx, my, float(op.width), depth, rot_for_normal(nx, ny))
            wins.append(_Win(op.id, op.sill, ko))
    doors.sort(key=lambda d: (round(d.x), round(d.y), d.op_id))
    wins.sort(key=lambda v: (round(v.keepout.centroid.x),
                             round(v.keepout.centroid.y), v.op_id))
    return doors, wins


def _runs_of(clear: Polygon) -> list[_Run]:
    """Straight edges of the usable region, each with a verified inward normal."""
    from shapely.geometry import polygon as _shp_poly
    ring = _shp_poly.orient(clear, 1.0).exterior          # CCW: interior is left
    cs = list(ring.coords)
    out: list[_Run] = []
    for i in range(len(cs) - 1):
        (x0, y0), (x1, y1) = cs[i], cs[i + 1]
        L = math.hypot(x1 - x0, y1 - y0)
        if L < 300.0:
            continue
        ux, uy = (x1 - x0) / L, (y1 - y0) / L
        nx, ny = -uy, ux
        mid = (x0 + ux * L / 2.0, y0 + uy * L / 2.0)
        if not clear.contains(Point(mid[0] + nx * 20.0, mid[1] + ny * 20.0)):
            nx, ny = uy, -ux
            if not clear.contains(Point(mid[0] + nx * 20.0, mid[1] + ny * 20.0)):
                continue                                   # a spike, not a wall
        out.append(_Run(x0, y0, ux, uy, nx, ny, L, rot_for_normal(nx, ny)))
    out.sort(key=lambda r: (-round(r.length, 3), round(r.ax), round(r.ay),
                            round(r.rot, 3)))
    return out


def build_ctx(plan: Plan, room: Room, mass: Optional[Polygon],
              walls: dict[str, Wall]) -> Optional[_Ctx]:
    """Usable interior + door/window keepouts for one room, or None if hopeless."""
    poly = _poly(room.polygon)
    if not poly.is_valid:
        poly = poly.buffer(0)
        poly = _largest(poly)
    if poly is None or poly.area <= 0:
        return None
    clear = _largest(poly.difference(mass)) if mass is not None else poly
    if clear is None or clear.area < MIN_CLEAR_M2 * 1e6:
        return None
    clear = clear.simplify(2.0, preserve_topology=True)
    clear = _largest(clear) or clear
    if not clear.is_valid or clear.area <= 0:
        return None

    key = _room_key(room)
    rt = roomtypes.get(key)
    fkey = (rt.furnish_key if rt else None) or ""
    doors, wins = _openings_for(plan, room, poly, walls)
    ko = unary_union([d.keepout for d in doors]) if doors else None
    circ = CIRC_WET_MM if (rt and rt.wet) else CIRC_MM
    base_ok = not _erode(clear, circ / 2.0).is_empty
    c = clear.centroid
    ctx = _Ctx(room=room, room_key=key, furnish_key=fkey, poly=poly, clear=clear,
               clear_pad=clear.buffer(WALL_TOL_MM, join_style=2, mitre_limit=2.0),
               runs=_runs_of(clear), doors=doors, windows=wins, door_keepout=ko,
               cx=c.x, cy=c.y, north=float(plan.site.north_deg), circ_mm=circ,
               circ_baseline_ok=base_ok)
    return ctx


# ---------------------------------------------------------------- placement

@dataclass
class _Placed:
    spec_key: str
    catalog_id: str
    cx: float
    cy: float
    rot: float
    w: int
    d: int
    h: int
    fp: Polygon
    pad: Polygon
    clearance: Optional[Polygon]
    score: float
    why: str


@dataclass
class _Cand:
    cx: float
    cy: float
    rot: float
    run: Optional[_Run]
    s: float
    align: str

    @property
    def sort_key(self) -> tuple:
        return (round(self.cx, 3), round(self.cy, 3), round(self.rot, 3))


def _dedupe(cands: list[_Cand]) -> list[_Cand]:
    """Round to integer mm *before* testing, then de-duplicate.

    Found the hard way: candidates tested as floats and rounded only on the way
    into `ir.P` drifted up to 0.5 mm, which flipped one `sink_b` from 9 990 mm2
    of door-swing overlap (accepted) to 10 029 mm2 (a reported violation). The
    IR is integer millimetres, so the search space must be too.
    """
    seen: dict[tuple, _Cand] = {}
    for c in cands:
        ci = _Cand(float(round(c.cx)), float(round(c.cy)), c.rot, c.run, c.s,
                   c.align)
        k = (round(ci.cx / 5.0), round(ci.cy / 5.0), round(ci.rot, 2))
        if k not in seen:
            seen[k] = ci
    return sorted(seen.values(), key=lambda c: c.sort_key)


def _wall_cands(runs: Sequence[_Run], w: float, d: float) -> list[_Cand]:
    out: list[_Cand] = []
    for run in runs:
        lo, hi = w / 2.0, run.length - w / 2.0
        if hi < lo - 1e-6:
            continue
        mid = (lo + hi) / 2.0
        picks = [(mid, "center"), (lo, "flush"), (hi, "flush")]
        if hi > lo:
            n = min(MAX_SAMPLES, max(1, int((hi - lo) // GRID_MM)))
            for i in range(n + 1):
                picks.append((lo + (hi - lo) * i / n, "any"))
        for s, al in picks:
            x, y = run.at(s, d / 2.0)
            out.append(_Cand(x, y, run.rot, run, s, al))
    return _dedupe(out)


def _corner_cands(runs: Sequence[_Run], w: float, d: float) -> list[_Cand]:
    """Flush into each convex corner: back on one run, one side on the other."""
    out: list[_Cand] = []
    for a in runs:
        for b in runs:
            if a is b:
                continue
            # b's start must coincide with a's end, or vice versa
            for (jx, jy) in ((a.ax + a.ux * a.length, a.ay + a.uy * a.length),
                             (a.ax, a.ay)):
                if math.hypot(jx - b.ax, jy - b.ay) > 30.0 and \
                   math.hypot(jx - (b.ax + b.ux * b.length),
                              jy - (b.ay + b.uy * b.length)) > 30.0:
                    continue
                for sgn in (1.0, -1.0):
                    sx, sy = a.ux * sgn, a.uy * sgn
                    x = jx + sx * (w / 2.0) + a.nx * (d / 2.0)
                    y = jy + sy * (w / 2.0) + a.ny * (d / 2.0)
                    out.append(_Cand(x, y, a.rot, a, a.project(x, y), "flush"))
    return _dedupe(out)


def _center_cands(ctx: _Ctx, w: float, d: float) -> list[_Cand]:
    out: list[_Cand] = []
    for rot in (0.0, 90.0):
        out.append(_Cand(ctx.cx, ctx.cy, rot, None, 0.0, "center"))
    # a small ring of offsets so a table can dodge a door swing
    for dx in (-600.0, 0.0, 600.0):
        for dy in (-600.0, 0.0, 600.0):
            if dx == 0.0 and dy == 0.0:
                continue
            out.append(_Cand(ctx.cx + dx, ctx.cy + dy, 0.0, None, 0.0, "any"))
    return _dedupe(out)


def _tall_over_window(ctx: _Ctx, fp: Polygon, height: int) -> Optional[str]:
    for wn in ctx.windows:
        if height > wn.sill + WINDOW_TALL_MARGIN and fp.intersects(wn.keepout):
            if fp.intersection(wn.keepout).area > 1e4:      # > 100 cm2 of overlap
                return wn.op_id
    return None


def _feasible(ctx: _Ctx, spec: Spec, cand: _Cand, w: int, d: int, h: int,
              placed: Sequence[_Placed], avoid_win: bool) -> tuple[Optional[Polygon], str]:
    fp = footprint(cand.cx, cand.cy, w, d, cand.rot)
    if not ctx.clear_pad.contains(fp):
        return None, "outside_room"
    if ctx.door_keepout is not None and fp.intersects(ctx.door_keepout):
        if fp.intersection(ctx.door_keepout).area > 1e4:
            return None, "door_swing"
    for pl in placed:
        # `abut` items are joinery: a counter tile butts flush against the hob,
        # so a 25 mm air gap would draw as a gap in the platform. Everything
        # else keeps the gap so the editor's 1 px strokes do not merge.
        if (fp.intersection(pl.fp).area > 1e4) if spec.abut else fp.intersects(pl.pad):
            return None, f"overlaps:{pl.spec_key}"
        if pl.clearance is not None and pl.spec_key not in spec.into \
                and fp.intersects(pl.clearance):
            if fp.intersection(pl.clearance).area > 1e4:
                return None, f"in_clearance:{pl.spec_key}"
    if avoid_win:
        hit = _tall_over_window(ctx, fp, h)
        if hit:
            return None, f"blocks_window:{hit}"
    for okey, mm in spec.min_gap_to:
        other = next((p for p in placed if p.spec_key == okey), None)
        if other is not None and fp.distance(other.fp) < mm - 1.0:
            return None, f"too_near:{okey}"
    if spec.not_facing_door and _faces_a_door(ctx, cand, d):
        return None, "faces_door"
    if spec.clear_front:
        cr = _front_rect(cand.cx, cand.cy, w, d, cand.rot, spec.clear_front)
        if not ctx.clear_pad.contains(cr):
            return None, "no_front_clearance"
        for pl in placed:
            if cr.intersects(pl.fp) and cr.intersection(pl.fp).area > 1e4:
                return None, f"front_clearance_hits:{pl.spec_key}"
    return fp, ""


def _faces_a_door(ctx: _Ctx, cand: _Cand, d: int) -> bool:
    """True if a door sits in the item's 45 deg forward cone within 2.5 m.

    Stated geometrically rather than as "not on the wall opposite the door",
    because in an L-shaped toilet the opposite wall is not the one you see.
    """
    fx, fy = facing(cand.rot)
    for dr in ctx.doors:
        vx, vy = dr.x - cand.cx, dr.y - cand.cy
        dist = math.hypot(vx, vy)
        if dist < 1.0 or dist > 2500.0:
            continue
        if (vx * fx + vy * fy) / dist > math.cos(math.radians(45.0)):
            return True
    return False


def _score(ctx: _Ctx, spec: Spec, cand: _Cand, fp: Polygon, w: int, d: int,
           h: int, placed: Sequence[_Placed]) -> float:
    sc = 0.0
    b = ctx.bearing(cand.cx, cand.cy)
    if spec.prefer or spec.avoid:
        sc += 3.0 * zone_score(b, spec.prefer, spec.avoid)
    if spec.align == "center":
        sc += 1.5 if cand.align == "center" else 0.0
    elif spec.align == "flush":
        sc += 1.5 if cand.align == "flush" else 0.0
    else:
        sc += {"flush": 0.40, "center": 0.30}.get(cand.align, 0.0)
    if cand.run is not None:
        sc += 0.8 * min(1.0, cand.run.length / 4000.0)
    if ctx.doors:
        dd = min(fp.distance(Point(dr.x, dr.y)) for dr in ctx.doors)
        sc += 0.7 * min(1.0, dd / 1500.0)
        if spec.away_from_door and dd < spec.away_from_door:
            sc -= 2.0 * (1.0 - dd / max(1.0, spec.away_from_door))
    for wn in ctx.windows:                    # never sit under a sill you block
        if fp.intersects(wn.keepout):
            sc -= 0.5 if h <= wn.sill + WINDOW_TALL_MARGIN else 3.0
    if spec.of:
        ref = next((p for p in placed if p.spec_key == spec.of), None)
        if ref is not None:
            gap = fp.distance(ref.fp)
            if spec.anchor == "facing":
                fx, fy = facing(cand.rot)
                vx, vy = ref.cx - cand.cx, ref.cy - cand.cy
                nrm = math.hypot(vx, vy) or 1.0
                sc += 2.5 * max(0.0, (vx * fx + vy * fy) / nrm)
                # ...and reward the two looking *at each other*: a TV the sofa
                # has its side to is worse than one 2 m further away in line.
                rfx, rfy = facing(ref.rot)
                sc += 1.5 * max(0.0, -(fx * rfx + fy * rfy))
                sc -= 1.2 * abs(gap - spec.gap) / 2000.0
            else:
                sc -= 0.8 * abs(gap - spec.gap) / 500.0
    return round(sc, 6)


def _circulation_ok(ctx: _Ctx, placed: Sequence[_Placed],
                    extra: Optional[Polygon] = None) -> bool:
    """Every door must still reach one shared walkable component.

    Skipped when the *empty* room already fails, so a 1.1 m wide toilet does not
    blame the furniture for geometry the solver handed us.
    """
    if not ctx.doors or not ctx.circ_baseline_ok:
        return True
    bods = [p.fp for p in placed]
    if extra is not None:
        bods.append(extra)
    if not bods:
        return True
    free = ctx.clear.difference(unary_union(bods))
    r = ctx.circ_mm / 2.0
    comps = _parts(_erode(free, r))
    if not comps:
        return False
    hits: list[int] = []
    for dr in ctx.doors:
        pt = Point(dr.approach)
        idx = -1
        best = r + 120.0
        for i, g in enumerate(comps):
            dist = g.distance(pt)
            if dist <= best:
                best, idx = dist, i
        if idx < 0:
            return False
        hits.append(idx)
    return len(set(hits)) <= 1


def _cands_for(ctx: _Ctx, spec: Spec, anc: str, w: int, d: int,
               use_runs: Sequence[_Run], ref: Optional[_Placed]) -> list[_Cand]:
    if anc in ("wall", "run", "facing"):
        return _wall_cands(use_runs, w, d)
    if anc == "corner":
        # corners first, but never *only* corners: an 8-candidate corner set in
        # an L-shaped room drops items that a wall slot would have taken.
        return _dedupe(_corner_cands(use_runs, w, d) + _wall_cands(use_runs, w, d))
    if anc == "center":
        return _center_cands(ctx, w, d)
    if anc == "beside":
        return _beside_cands(ref, spec, w, d)
    if anc == "front_of":
        return _front_cands(ref, spec, w, d)
    if anc == "around":
        return _around_cands(ref, spec, w, d)
    raise ValueError(f"unknown anchor {anc!r} on spec {spec.key!r}")


def _try_place(ctx: _Ctx, spec: Spec, placed: list[_Placed],
               runs: Optional[Sequence[_Run]] = None,
               dims_override: Optional[tuple[int, int, int]] = None,
               anchor: Optional[str] = None) -> tuple[Optional[_Placed], str]:
    """Enumerate -> filter -> score -> circulation-check. Deterministic throughout.

    Items with `shrink_to` are retried narrower in 300 mm steps before being
    dropped: a wardrobe is joinery built to the wall it lands on, so refusing a
    1800 carcass in a 3.0 m room and placing nothing is the wrong answer.
    """
    w0, d, h = dims_override or _dims(spec)
    cid = spec.catalog_id
    anc = anchor or spec.anchor
    avoid_win = spec.avoid_window
    if avoid_win is None:
        avoid_win = h >= 1200                 # a 1.2 m carcass already blocks a sill
    use_runs = list(runs if runs is not None else ctx.runs)

    ref = next((p for p in placed if p.spec_key == spec.of), None) if spec.of else None
    if spec.of and ref is None:
        return None, f"anchor_missing:{spec.of}"

    ladder = [w0]
    if spec.shrink_to and dims_override is None:
        x = w0 - 300
        while x >= spec.shrink_to:
            ladder.append(x)
            x -= 300

    why_final = "infeasible"
    for w in ladder:
        cands = _cands_for(ctx, spec, anc, w, d, use_runs, ref)
        if not cands:
            why_final = "no_candidate_position"
            continue
        ranked: list[tuple[tuple, _Cand, Polygon]] = []
        why_counts: dict[str, int] = {}
        for c in cands:
            fp, why = _feasible(ctx, spec, c, w, d, h, placed, bool(avoid_win))
            if fp is None:
                why_counts[why] = why_counts.get(why, 0) + 1
                continue
            sc = _score(ctx, spec, c, fp, w, d, h, placed)
            ranked.append(((-sc,) + c.sort_key, c, fp))
        if not ranked:
            top = sorted(why_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:2]
            why_final = "; ".join(f"{a}({b})" for a, b in top) or "infeasible"
            continue
        ranked.sort(key=lambda t: t[0])
        # Walk order matters more than score, so the top candidate is accepted
        # only if the room is still crossable; else fall down the ranking.
        for k, c, fp in ranked[:8]:
            if _circulation_ok(ctx, placed, fp):
                clearance = (_front_rect(c.cx, c.cy, w, d, c.rot, spec.clear_front)
                             if spec.clear_front else None)
                tag = f"{anc}@{ctx.zone(c.cx, c.cy)}"
                if w != w0:
                    tag += f" shrunk {w0}->{w}"
                return _Placed(spec.key, cid, c.cx, c.cy, c.rot, w, d, h, fp,
                               fp.buffer(ITEM_GAP_MM, join_style=2,
                                         mitre_limit=2.0),
                               clearance, -k[0], tag), ""
        why_final = f"blocks_circulation({ctx.circ_mm:.0f}mm)"
    return None, why_final


def _beside_cands(ref: _Placed, spec: Spec, w: float, d: float) -> list[_Cand]:
    """Alongside `of`, on the same wall plane, so a nightstand stays flush."""
    fx, fy = facing(ref.rot)
    ux, uy = fy, -fx                          # ref's local +X in world
    out = []
    sides = {"left": (-1.0,), "right": (1.0,), "both": (-1.0, 1.0)}[spec.side]
    for sgn in sides:
        off = ref.w / 2.0 + spec.gap + w / 2.0
        # Keep the two BACK planes coincident, not the two centres: the bed is
        # 2000 deep and the nightstand 400, so the offset is +f*(d - ref.d)/2.
        # The sign was inverted here and a render review caught it - the
        # nightstands sat 1600 mm off the wall, level with the foot of the bed.
        back = (d - ref.d) / 2.0
        x = ref.cx + ux * sgn * off + fx * back
        y = ref.cy + uy * sgn * off + fy * back
        out.append(_Cand(x, y, ref.rot, None, 0.0, "flush"))
    return _dedupe(out)


def _front_cands(ref: _Placed, spec: Spec, w: float, d: float) -> list[_Cand]:
    fx, fy = facing(ref.rot)
    ux, uy = fy, -fx
    out = []
    for lateral in (0.0, -300.0, 300.0):
        for extra in (0.0, 150.0, 300.0):
            off = ref.d / 2.0 + spec.gap + extra + d / 2.0
            out.append(_Cand(ref.cx + fx * off + ux * lateral,
                             ref.cy + fy * off + uy * lateral,
                             ref.rot, None, 0.0, "center"))
    return _dedupe(out)


def _around_cands(ref: _Placed, spec: Spec, w: float, d: float) -> list[_Cand]:
    """Chairs ringed about a table, each turned to face it."""
    fx, fy = facing(ref.rot)
    ux, uy = fy, -fx
    out = []
    long_off = ref.d / 2.0 + spec.gap + d / 2.0
    side_off = ref.w / 2.0 + spec.gap + d / 2.0
    for sgn, along in ((1.0, 0.0), (-1.0, 0.0)):
        for lat in (-ref.w / 4.0, ref.w / 4.0):
            x = ref.cx + fx * sgn * long_off + ux * lat
            y = ref.cy + fy * sgn * long_off + uy * lat
            out.append(_Cand(x, y, (ref.rot + (0.0 if sgn < 0 else 180.0)) % 360.0,
                             None, 0.0, "center"))
    for sgn in (1.0, -1.0):
        for lat in (0.0,):
            x = ref.cx + ux * sgn * side_off + fx * lat
            y = ref.cy + uy * sgn * side_off + fy * lat
            out.append(_Cand(x, y, (ref.rot + (90.0 if sgn > 0 else 270.0)) % 360.0,
                             None, 0.0, "center"))
    return _dedupe(out)


# ---------------------------------------------------------------- kitchen

def _pick_run_walls(ctx: _Ctx, shape: str) -> list[_Run]:
    """The counter run: longest usable wall, then its neighbours for L / U.

    Adjacency is by shared endpoint on the usable-region ring, so an L in a
    non-rectangular kitchen still turns the real corner.
    """
    if not ctx.runs:
        return []
    want = {"I": 1, "L": 2, "U": 3}[shape]
    a = ctx.runs[0]
    chosen = [a]
    if want > 1:
        def touches(r: _Run) -> float:
            ends_a = ((a.ax, a.ay), (a.ax + a.ux * a.length, a.ay + a.uy * a.length))
            ends_r = ((r.ax, r.ay), (r.ax + r.ux * r.length, r.ay + r.uy * r.length))
            return min(math.hypot(p[0] - q[0], p[1] - q[1])
                       for p in ends_a for q in ends_r)
        nbrs = [r for r in ctx.runs[1:] if touches(r) < 30.0 and
                abs(r.ux * a.ux + r.uy * a.uy) < 0.3]
        nbrs.sort(key=lambda r: (-round(r.length, 3), round(r.ax), round(r.ay)))
        chosen += nbrs[:want - 1]
    return chosen


def _free_intervals(run: _Run, blockers: Sequence[Polygon],
                    depth: float) -> list[tuple[float, float]]:
    """1-D gaps along a run not shadowed by anything within `depth` of it."""
    band = footprint(*run.at(run.length / 2.0, depth / 2.0), run.length + 4.0,
                     depth, run.rot)
    cut: list[tuple[float, float]] = []
    for g in blockers:
        if g is None or g.is_empty or not g.intersects(band):
            continue
        piece = g.intersection(band)
        if piece.is_empty:
            continue
        ss = [run.project(x, y) for x, y in _coords(piece)]
        if ss:
            cut.append((min(ss), max(ss)))
    cut.sort()
    free, s = [], 0.0
    for lo, hi in cut:
        if lo > s:
            free.append((s, min(lo, run.length)))
        s = max(s, hi)
    if s < run.length:
        free.append((s, run.length))
    return [(a, b) for a, b in free if b - a >= COUNTER_MIN_MM]


def _coords(g) -> list[tuple[float, float]]:
    if isinstance(g, Polygon):
        return list(g.exterior.coords)
    out: list[tuple[float, float]] = []
    for p in getattr(g, "geoms", []):
        out += _coords(p)
    if not out and hasattr(g, "coords"):
        out = list(g.coords)
    return out


def _furnish_kitchen(ctx: _Ctx, specs: Sequence[Spec], pol: ResolvedPolicy,
                     placed: list[_Placed]) -> list[tuple[Spec, str]]:
    """An I/L/U platform run with the sink and hob cut into it, not on top of it.

    Putting `sink_k` and `stove` *over* a `counter` item is what the fork's
    template does; it double-draws in 2D and reads as an overlap to any checker.
    Instead the run is a 1-D interval per wall, the fixtures claim slots, and
    `counter` tiles fill what is left. Vastu (`bylaws.DEFAULT_VASTU`) wants the
    hob SE and the sink NE/N, and practitioners state a 600 mm fire/water gap,
    so those are a scored preference plus one hard constraint.
    """
    shape = pol.kitchen["counter_run"]
    depth = int(pol.kitchen["platform_depth_mm"])
    runs = _pick_run_walls(ctx, shape)
    drops: list[tuple[Spec, str]] = []
    if not runs:
        return [(s, "no_usable_wall_run") for s in specs]

    by_key = {s.key: s for s in specs}
    order = ["sink", "hob", "fridge", "washer_dryer"]
    order += [s.key for s in specs if s.key not in order]
    for key in order:
        spec = by_key.get(key)
        if spec is None:
            continue
        if spec.optional and spec.level > pol.level:
            drops.append((spec, f"density={pol.density}"))
            continue
        if ctx.clear.area / 1e6 < spec.min_room_m2:
            drops.append((spec, f"room {ctx.clear.area/1e6:.1f} m2 "
                                f"< min {spec.min_room_m2:.1f}"))
            continue
        s2 = spec
        if key == "sink":
            s2 = replace(spec, prefer=tuple(pol.kitchen["sink_zone"]))
        elif key == "hob":
            s2 = replace(spec, prefer=tuple(pol.kitchen["hob_zone"]),
                         min_gap_to=(("sink", int(pol.kitchen["hob_sink_min_mm"])),))
        w, d, h = _dims(s2)
        if key in ("sink", "hob"):
            d = depth
        pl, why = _try_place(ctx, s2, placed, runs=runs,
                             dims_override=(w, d, h), anchor="run")
        if pl is None and key not in ("sink", "hob"):
            # A fridge or washing machine does not have to stand in the
            # platform run; Indian kitchens routinely put the fridge on the
            # opposite wall. Sink and hob do have to, so they get no fallback.
            pl, _ = _try_place(ctx, s2, placed, dims_override=(w, d, h),
                               anchor="wall")
        if pl is None:
            drops.append((spec, why))
        else:
            placed.append(pl)

    # tile the leftovers
    tile = Spec("counter", "platform", "run", depth=depth, abut=True)
    blockers = [p.fp for p in placed] + [d.keepout for d in ctx.doors]
    n = 0
    for run in runs:
        for lo, hi in _free_intervals(run, blockers, depth):
            span = hi - lo
            k = max(1, int(math.ceil(span / COUNTER_MAX_MM)))
            seg = span / k
            if seg < COUNTER_MIN_MM:
                continue
            for i in range(k):
                s0 = lo + i * seg
                cw = int(round(seg))
                cx, cy = run.at(s0 + seg / 2.0, depth / 2.0)
                cx, cy = float(round(cx)), float(round(cy))
                cand = _Cand(cx, cy, run.rot, run, s0 + seg / 2.0, "flush")
                fp, why = _feasible(ctx, tile, cand, cw, depth, 850, placed, False)
                if fp is None:
                    continue
                if not _circulation_ok(ctx, placed, fp):
                    continue
                n += 1
                placed.append(_Placed(f"counter_{n}", tile.catalog_id, cx, cy,
                                      run.rot, cw, depth, 850, fp,
                                      fp.buffer(ITEM_GAP_MM, join_style=2,
                                                mitre_limit=2.0),
                                      None, 0.0,
                                      f"run:{shape}@{ctx.zone(cx, cy)}"))
                blockers.append(fp)
    if n == 0:
        drops.append((tile, "no_free_run_interval"))
    return drops


# ---------------------------------------------------------------- entry point

def _bed_for(ctx: _Ctx, spec: Spec) -> Spec:
    """A 1500 queen does not fit a 2.4 m ResPlan bedroom; step down to a single."""
    if spec.item not in ("bed_queen",) or spec.width:
        return spec
    w, d, _ = catalog.dims("bed_queen")
    if ctx.clear.area / 1e6 < 8.5 or min(ctx.clear.bounds[2] - ctx.clear.bounds[0],
                                         ctx.clear.bounds[3] - ctx.clear.bounds[1]) \
            < d + 600:
        return replace(spec, item="bed_twin", note=spec.note + " [stepped to twin]")
    return spec


# Items a room only hosts when no better room exists. The kitchen and the
# utility both list the washing machine; when both rooms are present the utility
# must win, or a brief saying "washing machine in the utility" is silently
# violated (measured: it landed in the Kitchen on det-02 and det-06).
DEFERS_TO: dict[str, tuple[tuple[str, str], ...]] = {
    "kitchen": (("washer_dryer", "utility"),),
}


def _suppressed_items(plan: Plan, room_key: str) -> set[str]:
    present = {(r.category or "") for r in plan.rooms}
    return {item for item, better in DEFERS_TO.get(room_key, ())
            if better in present}


def furnish(plan: Plan, policy: dict[str, Any] | None = None,
            seed: int = 0) -> tuple[Plan, FurnishReport]:
    """Place furniture in every furnishable room. Returns a *new* plan.

    The input plan is deep-copied, so callers can diff before/after. Furniture
    already on the plan and flagged `locked` survives and is treated as an
    obstacle, which is how a user drag in the editor beats a re-solve.
    """
    t0 = time.perf_counter()
    pol = resolve_policy(policy)
    out = copy.deepcopy(plan)
    locked = [f for f in out.furniture if f.locked]
    out.furniture = []
    rep = FurnishReport(plan_id=plan.id, seed=seed, density=pol.density,
                        policy_diff=pol.diff)

    walls = {w.id: w for w in plan.walls}
    mass = wall_mass(plan)
    seq = 0
    for room in plan.rooms:
        t1 = time.perf_counter()
        key = _room_key(room)
        rt = roomtypes.get(key)
        fkey = (rt.furnish_key if rt else None) or ""
        if not fkey:
            rep.skipped.append((room.id, f"no furnish_key for {key!r}"))
            continue
        specs = pol.specs.get(fkey)
        if not specs:
            rep.skipped.append((room.id, f"no rules for furnish_key {fkey!r}"))
            continue
        ctx = build_ctx(plan, room, mass, walls)
        if ctx is None:
            rep.skipped.append((room.id, "usable area below 0.9 m2 after walls"))
            continue

        placed: list[_Placed] = []
        for f in locked:                        # user-pinned items are obstacles
            if f.room_id != room.id:
                continue
            w, d, h = (f.width, f.depth, f.height)
            if not (w and d):
                w, d, h = catalog.dims(f.catalog_id)
            fp = footprint(f.position.x, f.position.y, w, d, f.rotation)
            placed.append(_Placed(f"locked:{f.id}", f.catalog_id, f.position.x,
                                  f.position.y, f.rotation, w, d, h, fp,
                                  fp.buffer(ITEM_GAP_MM, join_style=2,
                                            mitre_limit=2.0),
                                  None, 0.0, "locked"))
        drops: list[tuple[Spec, str]] = []

        # Drop items this room only hosts in the absence of a better one.
        _defer = _suppressed_items(plan, fkey)
        if _defer:
            kept = []
            for sp in specs:
                if sp.key in _defer:
                    drops.append((sp, f"deferred to the {DEFERS_TO[fkey][0][1]}"))
                else:
                    kept.append(sp)
            specs = kept

        if fkey == "kitchen":
            drops += _furnish_kitchen(ctx, specs, pol, placed)
        else:
            clear_m2 = ctx.clear.area / 1e6
            for spec in specs:
                if spec.optional and spec.level > pol.level:
                    drops.append((spec, f"density={pol.density}"))
                    continue
                if clear_m2 < spec.min_room_m2:
                    drops.append((spec, f"room {clear_m2:.1f} m2 "
                                        f"< min {spec.min_room_m2:.1f}"))
                    continue
                s2 = _bed_for(ctx, spec) if spec.key == "bed" else spec
                if spec.anchor == "around" and spec.count > 1:
                    ref = next((p for p in placed if p.spec_key == spec.of), None)
                    if ref is None:
                        drops.append((spec, f"anchor_missing:{spec.of}"))
                        continue
                    got = 0
                    for i in range(spec.count):
                        ss = replace(s2, key=f"{spec.key}_{i+1}")
                        pl, why = _try_place(ctx, ss, placed)
                        if pl is not None:
                            placed.append(pl)
                            got += 1
                    if got == 0:
                        drops.append((spec, "no seat position free"))
                    continue
                pl, why = _try_place(ctx, s2, placed)
                if pl is None and s2.key == "bed" and s2.item == "bed_queen":
                    # Measured on a 3.9x2.6 m solver bedroom with a door mid-way
                    # along BOTH long walls: no 1500x2000 + 750 pose survives,
                    # but a 900x1900 single flush to a corner does. A kid's room
                    # with a single bed beats an empty room.
                    s3 = replace(s2, item="bed_twin", clear_front=600,
                                 note=s2.note + " [fell back to a single]")
                    pl, why3 = _try_place(ctx, s3, placed)
                    if pl is not None:
                        s2, why = s3, why3
                if pl is None:
                    drops.append((s2, why))
                else:
                    placed.append(pl)

        for pl in placed:
            if pl.spec_key.startswith("locked:"):
                continue
            seq += 1
            fid = f"fn{seq:04d}-{room.id}-{pl.spec_key}"
            out.furniture.append(Furniture(
                id=fid, catalog_id=pl.catalog_id,
                position=P(int(round(pl.cx)), int(round(pl.cy))),
                rotation=float(pl.rot), width=int(pl.w), depth=int(pl.d),
                height=int(pl.h), room_id=room.id, locked=False))
            rep.placements.append(Placement(
                id=fid, room_id=room.id, room_key=key, spec_key=pl.spec_key,
                catalog_id=pl.catalog_id, x=int(round(pl.cx)),
                y=int(round(pl.cy)), rotation=float(pl.rot), width=int(pl.w),
                depth=int(pl.d), height=int(pl.h), score=round(pl.score, 4),
                why=pl.why))
        for spec, why in drops:
            rep.drops.append(Drop(room.id, key, spec.key, spec.catalog_id, why))
        rep.rooms.append(RoomResult(
            room_id=room.id, name=room.name, room_key=key, area_m2=room.area_m2,
            clear_m2=round(ctx.clear.area / 1e6, 2), n_doors=len(ctx.doors),
            n_windows=len(ctx.windows),
            placed=tuple(p.spec_key for p in placed
                         if not p.spec_key.startswith("locked:")),
            dropped=tuple(s.key for s, _ in drops),
            ms=round((time.perf_counter() - t1) * 1000.0, 3)))

    out.furniture.extend(locked)
    rep.ms = round((time.perf_counter() - t0) * 1000.0, 3)
    return out, rep


# ---------------------------------------------------------------- checking

def required_keys(furnish_key: str, pol: ResolvedPolicy | None = None) -> list[str]:
    """Spec keys that are not `optional` for a room type, in table order."""
    pol = pol or resolve_policy(None)
    return [s.key for s in pol.specs.get(furnish_key, ()) if not s.optional]


def audit(plan: Plan, report: FurnishReport | None = None) -> dict[str, Any]:
    """Re-derive every invariant from the plan alone. Used by the tests.

    Deliberately independent of the solver's own bookkeeping: it rebuilds the
    wall mass, the room polygons and the door swings from the IR, so a bug in
    the placement code cannot hide itself in the audit.
    """
    walls = {w.id: w for w in plan.walls}
    mass = wall_mass(plan)
    rooms = {r.id: r for r in plan.rooms}
    polys: dict[str, Polygon] = {}
    clears: dict[str, Polygon] = {}
    keepouts: dict[str, Optional[Polygon]] = {}
    wins: dict[str, list[_Win]] = {}
    for r in plan.rooms:
        p = _poly(r.polygon)
        if not p.is_valid:
            p = _largest(p.buffer(0)) or p
        polys[r.id] = p
        c = _largest(p.difference(mass)) if mass is not None else p
        clears[r.id] = c if c is not None else Polygon()
        d, w = _openings_for(plan, r, p, walls)
        keepouts[r.id] = unary_union([x.keepout for x in d]) if d else None
        wins[r.id] = w

    fps: list[tuple[str, Polygon, Furniture]] = []
    v = {"outside_room": [], "wall_overlap": [], "item_overlap": [],
         "door_block": [], "window_block": [], "unknown_catalog": [],
         "no_room": []}
    for f in plan.furniture:
        if not catalog.has(f.catalog_id):
            v["unknown_catalog"].append(f.id)
            continue
        w, d, h = (f.width, f.depth, f.height)
        if not (w and d):
            w, d, h = catalog.dims(f.catalog_id)
        fp = footprint(f.position.x, f.position.y, w, d, f.rotation)
        fps.append((f.id, fp, f))
        rid = f.room_id
        if rid is None or rid not in rooms:
            v["no_room"].append(f.id)
            continue
        if not polys[rid].buffer(WALL_TOL_MM).contains(fp):
            v["outside_room"].append(f.id)
        if mass is not None and fp.intersection(mass).area > 1e4:
            v["wall_overlap"].append(f.id)
        ko = keepouts[rid]
        if ko is not None and fp.intersection(ko).area > 1e4:
            v["door_block"].append(f.id)
        for wn in wins[rid]:
            if h > wn.sill + WINDOW_TALL_MARGIN and \
                    fp.intersection(wn.keepout).area > 1e4:
                v["window_block"].append(f.id)
                break
    for i in range(len(fps)):
        for j in range(i + 1, len(fps)):
            if fps[i][2].room_id != fps[j][2].room_id:
                continue
            if fps[i][1].intersection(fps[j][1]).area > 1e4:
                v["item_overlap"].append(f"{fps[i][0]}|{fps[j][0]}")
    return {
        "n_furniture": len(plan.furniture),
        "violations": {k: sorted(set(x)) for k, x in v.items()},
        "n_violations": sum(len(set(x)) for x in v.values()),
    }
