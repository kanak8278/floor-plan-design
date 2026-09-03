"""The single canonical room-type taxonomy.

Written because three vocabularies had drifted apart: ResPlan's 6 categories, the
17 in `plausible.py`, and free-string `category` on the solver's RoomReq. Anything
that needs to know "is this habitable", "does it count as carpet area", "what
belongs in it" must read this table rather than hard-coding a list.

Sources: NBC 2016 Part 3 for the habitable/wet classification and minima; observed
Indian builder drawings for the vocabulary itself (SITOUT, UTILITY, PWD RM, PUJA,
FOYER, HANDWASH, PHE SHAFT are all real labels absent from western datasets).
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Literal

# NBC-relevant behaviour class, not a room name.
Klass = Literal["habitable", "wet", "service", "circulation", "outdoor", "shaft", "vertical"]


@dataclass(frozen=True)
class RoomType:
    key: str
    display: str
    klass: Klass
    # --- area accounting ---
    carpet: bool                 # counts toward carpet area
    built_up: bool               # counts toward ground coverage / FAR
    # --- NBC minima (mm / m²); None = not legislated for this type ---
    min_area_m2: float | None
    min_width_mm: int | None
    ceiling_mm: int
    # --- design defaults ---
    target_m2: tuple[float, float] | None      # sensible request range
    max_aspect: float | None                   # None = exempt (a sitout is long and thin)
    vastu_zone: str | None                     # preferred 8-point zone
    vastu_avoid: tuple[str, ...] = ()
    needs_window: bool = False                 # NBC light/ventilation
    needs_own_door: bool = False               # privacy: an arch does not count
    wet: bool = False                          # plumbing; drives stacking
    # --- downstream mappings ---
    op3d_room_type: str = "indoor"             # OpenPlan3D RoomCategory
    floor_texture: str = "light-oak"           # OpenPlan3D material id
    furnish_key: str | None = None             # key into the furnishing rule table
    aliases: tuple[str, ...] = field(default_factory=tuple)
    short_aliases: tuple[str, ...] = field(default_factory=tuple)  # need word boundary


T: dict[str, RoomType] = {}


def _add(rt: RoomType) -> None:
    T[rt.key] = rt


# ---- habitable ------------------------------------------------------------
_add(RoomType("living", "Living", "habitable", True, True, 7.5, 2400, 2750,
              (12.0, 32.0), 2.6, "N", ("S",), needs_window=True,
              floor_texture="light-oak", furnish_key="living",
              aliases=("living", "drawing room", "lounge", "family room", "hall")))
_add(RoomType("dining", "Dining", "habitable", True, True, 7.5, 2400, 2750,
              (8.0, 22.0), 2.6, "W", (), needs_window=False,
              floor_texture="light-oak", furnish_key="dining",
              aliases=("dining", "dinning")))
_add(RoomType("bedroom", "Bedroom", "habitable", True, True, 7.5, 2400, 2750,
              (9.0, 20.0), 2.4, None, (), needs_window=True, needs_own_door=True,
              floor_texture="light-oak", furnish_key="bedroom",
              aliases=("bedroom", "bed room", "bed rm", "guest bed", "kids room", "children"),
              short_aliases=("br",)))
_add(RoomType("master_bedroom", "Master Bedroom", "habitable", True, True, 9.5, 2700, 2750,
              (12.0, 24.0), 2.4, "SW", ("NE",), needs_window=True, needs_own_door=True,
              floor_texture="light-oak", furnish_key="master_bedroom",
              aliases=("master bedroom", "master bed"), short_aliases=("mbr",)))
_add(RoomType("study", "Study", "habitable", True, True, 7.5, 2400, 2750,
              (6.0, 16.0), 2.6, "N", (), needs_window=True,
              floor_texture="light-oak", furnish_key="study",
              aliases=("study", "home office", "work room")))
_add(RoomType("kitchen", "Kitchen", "habitable", True, True, 5.0, 1800, 2750,
              (6.0, 16.0), 3.0, "SE", ("NE", "SW"), needs_window=True, wet=True,
              floor_texture="ceramic-gray", furnish_key="kitchen",
              aliases=("kitchen", "kitchan"), short_aliases=("kit",)))
_add(RoomType("pooja", "Pooja", "habitable", True, True, None, None, 2750,
              (1.0, 5.0), 3.0, "NE", ("S", "SW"),
              floor_texture="marble-white", furnish_key="pooja",
              aliases=("pooja", "puja", "prayer", "mandir")))

# ---- wet ------------------------------------------------------------------
_add(RoomType("bathroom", "Bathroom", "wet", True, True, 2.8, 1200, 2100,
              (2.8, 8.0), 3.2, "NW", ("NE", "SE"), needs_window=True, needs_own_door=True,
              wet=True, floor_texture="ceramic-white", furnish_key="bathroom",
              aliases=("bathroom", "bath room", "toilet", "washroom", "powder",
                       "pwd rm", "attached toilet", "common toilet", "handwash",
                       "hand wash", "wash basin"),
              short_aliases=("toi", "wc", "t&b", "bath")))
# A utility must hold a 600x650 machine plus ~900 mm of access, so it needs a
# CONTENTS-driven floor area (3.5 m2), not a wider wall: forcing 1500 mm of
# width instead made det-01 and det-25 infeasible outright.
_add(RoomType("utility", "Utility", "service", False, True, 3.5, 1000, 2100,
              (1.5, 8.0), 4.0, "NW", (), wet=True,
              op3d_room_type="utility", floor_texture="ceramic-gray", furnish_key="utility",
              aliases=("utility", "uitility", "utilty", "wash area", "service area")))

# ---- service / circulation -------------------------------------------------
_add(RoomType("store", "Store", "service", False, True, None, 500, 2100,
              (0.8, 6.0), 4.0, "SW", (),
              op3d_room_type="utility", floor_texture="concrete", furnish_key="store",
              aliases=("store", "storage", "closet", "wardrobe", "dress")))
_add(RoomType("foyer", "Foyer", "circulation", False, True, None, 900, 2750,
              (1.5, 8.0), None, "N", (),
              floor_texture="marble-white", furnish_key="foyer",
              aliases=("foyer", "entrance", "lobby", "passage", "corridor",
                       "hallway", "circulation"), short_aliases=("lift",)))
_add(RoomType("stair", "Staircase", "vertical", False, True, None, 900, 2750,
              (2.5, 12.0), 4.0, "SW", ("NE",),
              floor_texture="slate", furnish_key=None,
              aliases=("staircase", "stair", "steps")))
_add(RoomType("shaft", "Shaft", "shaft", False, True, None, 200, 0,
              (0.05, 4.0), None, None, (),
              op3d_room_type="utility", floor_texture="concrete", furnish_key=None,
              aliases=("phe shaft", "phe & hvac", "phe and hvac", "hvac", "vrv",
                       "duct", "shaft", "riser", "service shaft", "ac ledge",
                       "ac platform"), short_aliases=("phe", "ots")))

# ---- outdoor ---------------------------------------------------------------
_add(RoomType("balcony", "Balcony", "outdoor", False, True, None, 700, 2750,
              (1.5, 12.0), None, None, (),
              op3d_room_type="outdoor", floor_texture="slate", furnish_key="balcony",
              aliases=("balcony", "balcany", "balcone", "deck")))
_add(RoomType("sitout", "Sitout", "outdoor", False, True, None, 700, 2750,
              (2.0, 14.0), None, "E", (),
              op3d_room_type="outdoor", floor_texture="slate", furnish_key="sitout",
              aliases=("sitout", "sit out", "sit-out", "verandah", "veranda",
                       "porch", "portico")))
_add(RoomType("patio", "Patio", "outdoor", False, False, None, 700, 0,
              (2.0, 30.0), None, None, (),
              op3d_room_type="outdoor", floor_texture="slate", furnish_key="patio",
              aliases=("patio", "courtyard", "terrace", "open to sky")))
_add(RoomType("parking", "Parking", "outdoor", False, False, None, 2100, 2400,
              (12.5, 40.0), None, "NW", (),
              op3d_room_type="garage", floor_texture="concrete", furnish_key="parking",
              aliases=("parking", "car park", "garage", "car porch")))
_add(RoomType("landscape", "Landscape", "outdoor", False, False, None, 300, 0,
              (1.0, 200.0), None, None, (),
              op3d_room_type="outdoor", floor_texture="none", furnish_key="landscape",
              aliases=("landscape", "garden", "lawn", "planter")))

KEYS = tuple(T)
HABITABLE = tuple(k for k, v in T.items() if v.klass == "habitable")
WET = tuple(k for k, v in T.items() if v.wet)
CARPET = tuple(k for k, v in T.items() if v.carpet)
BUILT_UP = tuple(k for k, v in T.items() if v.built_up)
OUTDOOR = tuple(k for k, v in T.items() if v.klass == "outdoor")
# ResPlan only labels these six; everything else is unrepresentable in that corpus.
RESPLAN_KEYS = ("living", "kitchen", "bedroom", "bathroom", "balcony", "store")


def get(key: str) -> RoomType | None:
    return T.get(key)


def canonical(name: str) -> str:
    """Printed room label -> canonical key, or 'unknown'.

    Returns 'unknown' rather than guessing: an unknown is a visible gap in the
    taxonomy, a wrong guess silently corrupts every count downstream.
    """
    n = re.sub(r"[_\-./&]+", " ", (name or "").strip().lower())
    n = re.sub(r"\s*(?:no\.?)?\s*\d+\s*$", "", n).strip()
    n = re.sub(r"\s+", " ", n)
    if not n:
        return "unknown"
    # master bedroom must win over bedroom, so try longest alias first
    cands = sorted(((a, k) for k, v in T.items() for a in v.aliases),
                   key=lambda t: -len(t[0]))
    for alias, key in cands:
        if alias in n:
            return key
    for key, v in T.items():
        if any(re.search(rf"\b{re.escape(a)}\b", n) for a in v.short_aliases):
            return key
    return "unknown"
