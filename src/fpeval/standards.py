"""Dimensional standards as data, with the clause that authorises each number.

Separated from `rules.py` so a number can be audited without reading logic, and
so another city or code edition is a table swap. Every entry carries its source:
an unsourced dimension is someone's memory, and memory is what produced the
"2400 mm minimum width" I applied to a 20x30 plot before checking whether such
houses are actually built.

Sources
-------
* Staircase: NBC 2016 Part 3 Table 5 (rise/tread), Clause 17.5.5 (headroom),
  and the 2R+T comfort rule. Landing after at most 12 risers.
* Light and ventilation: NBC 2016 Part 3 Clause 8.2.5 and Part 8 Section 1.
* Clearances: Neufert, Architects' Data -- circulation and dining figures.
"""
from __future__ import annotations
from dataclasses import dataclass


# ---------------------------------------------------------------- staircases
@dataclass(frozen=True)
class StairStd:
    riser_max_mm: int
    tread_min_mm: int
    width_min_mm: int
    headroom_min_mm: int
    landing_min_mm: int            # 0 = "equal to the flight width"
    risers_per_flight_max: int
    two_r_plus_t: tuple[int, int]  # comfort band
    two_r_plus_t_ideal: tuple[int, int]
    handrail_mm: tuple[int, int]
    source: str


STAIRS: dict[str, StairStd] = {
    # Single-family dwelling.
    "residential": StairStd(
        riser_max_mm=190, tread_min_mm=250, width_min_mm=900,
        headroom_min_mm=2100, landing_min_mm=0, risers_per_flight_max=12,
        two_r_plus_t=(550, 700), two_r_plus_t_ideal=(600, 630),
        handrail_mm=(800, 900),
        source="NBC 2016 Part 3 Table 5; Cl 17.5.5; 2R+T comfort rule"),
    # More than one dwelling on the stair.
    "multi_family": StairStd(
        riser_max_mm=190, tread_min_mm=250, width_min_mm=1000,
        headroom_min_mm=2100, landing_min_mm=0, risers_per_flight_max=12,
        two_r_plus_t=(550, 700), two_r_plus_t_ideal=(600, 630),
        handrail_mm=(800, 900), source="NBC 2016 Part 3 Table 5"),
    # Apartment blocks: the stair is an escape route.
    "apartment": StairStd(
        riser_max_mm=190, tread_min_mm=250, width_min_mm=1250,
        headroom_min_mm=2100, landing_min_mm=0, risers_per_flight_max=12,
        two_r_plus_t=(550, 700), two_r_plus_t_ideal=(600, 630),
        handrail_mm=(800, 900),
        source="NBC 2016 Part 3 Table 5; evacuation width"),
}


# A flight's DEVELOPED GOING is not its footprint depth except for a straight
# run. Getting this wrong made every stair look non-compliant: a 3000 mm storey
# at NBC's 190 mm riser cap needs ~17 treads x 250 mm = 4250 mm of going, while
# the median ResPlan stair footprint is 2674 mm deep. Those stairs are not
# illegal, they are turning flights -- an L-shape folds the going across two
# runs, so the footprint is roughly half of it.
GOING_FACTOR = {"straight": 1.0, "l-shaped": 1.9, "u-shaped": 2.0, "spiral": 0.0}


def developed_going_mm(depth_mm: int, width_mm: int, stair_type: str) -> float:
    """Total run available, from the footprint and the flight shape."""
    k = GOING_FACTOR.get(stair_type, 1.0)
    if k == 0.0:
        return 0.0                              # spiral: geometry is angular
    if stair_type == "l-shaped":
        return depth_mm + max(width_mm, 0)      # two runs meeting at a landing
    return depth_mm * k


def stair_geometry(going_mm: float, riser_count: int, storey_mm: int
                   ) -> tuple[float, float, float]:
    """(riser_mm, tread_mm, 2R+T) from the DEVELOPED going, not the footprint."""
    r = storey_mm / max(riser_count, 1)
    t = going_mm / max(riser_count - 1, 1)
    return r, t, 2 * r + t


def required_going_mm(riser_count: int, tread_min_mm: int) -> float:
    return (riser_count - 1) * tread_min_mm


# ------------------------------------------------------- light & ventilation
@dataclass(frozen=True)
class VentStd:
    window_frac_of_floor: float    # aggregate glazed area / floor area
    openable_frac_of_window: float
    min_window_m2: float
    mechanical_ok: bool            # may an exhaust fan substitute?
    source: str


VENTILATION: dict[str, VentStd] = {
    "habitable": VentStd(0.10, 0.50, 0.0, False,
                         "NBC 2016 Part 3 Cl 8.2.5; Part 8 Sec 1 -- 1 m2 window per 10 m2 floor"),
    "kitchen":   VentStd(0.10, 0.50, 1.0, False,
                         "NBC 2016 Part 8 -- min 1 m2, at least 50% openable"),
    "bathroom":  VentStd(0.0, 1.00, 0.30, True,
                         "NBC 2016 Part 8 -- min 0.3 m2, 100% openable, or mechanical exhaust"),
}
# NBC 2016 Part 1 defines a habitable room by USE -- living, sleeping, eating,
# cooking -- which is why bath, WC, store and passage are excluded. A pooja room
# is a prayer niche and belongs with them: Indian plans routinely place it
# internally with no exterior wall at all. Listing it here demanded glazing in a
# 1.65 m square shrine, and the only wall left for the mandir was under that
# window, so the room shipped empty.
HABITABLE_VENT = ("living", "dining", "bedroom", "master_bedroom", "study")


# --------------------------------------------------------- contents area floor
# What a room's own contents need, independent of any code minimum. NBC has
# nothing to say about a utility, so nothing bounded it: a deeper topology
# search produced a 1.47 m2 utility, and a 600 x 650 washing machine plus its
# door swing does not fit in 1.47 m2. Derived from the fixtures and the
# clearances below, not from law, which is why it is a separate table.
CONTENTS_FLOOR_M2: dict[str, float] = {
    "utility": 3.5,     # 600x650 machine + 900 mm standing space + door swing
    "bathroom": 2.8,     # NBC combined bath+WC, which is also what fits
    "kitchen": 5.0,      # NBC
    "pooja": 1.2,        # 1000x500 mandir plus somewhere to stand
    "store": 1.2,
    # A 3000 mm storey needs 16 risers at the 190 mm cap, so 15 treads at the
    # 250 mm minimum = 3750 mm of going. Folded into two runs that is a well of
    # roughly 2100 x 1900 mm. At the old 2.0 m2 the flight did not fit and the
    # treads came out at 249 mm -- NBC.STAIR_TREAD on 8 plans.
    "stair": 4.0,
}


# ----------------------------------------------------------------- clearances
@dataclass(frozen=True)
class Clearance:
    mm: int
    why: str
    source: str


CLEARANCES: dict[str, Clearance] = {
    "primary_circulation": Clearance(
        900, "a main traffic path two people can pass on", "Neufert; NBC passage 0.9 m"),
    "secondary_circulation": Clearance(
        450, "minimal squeeze-past clearance", "Neufert"),
    "bed_side": Clearance(
        600, "getting in and out, and making the bed", "Neufert"),
    "bed_foot": Clearance(
        750, "walking past the foot of a bed", "Neufert"),
    "wardrobe_front": Clearance(
        900, "standing back far enough to open a double door", "Neufert"),
    "dining_seat_perimeter": Clearance(
        600, "table edge per seated diner", "Neufert"),
    "dining_chair_pullout": Clearance(
        900, "seated diner plus passage and serving behind", "Neufert"),
    "kitchen_aisle_single": Clearance(
        1050, "one cook, base unit to opposite wall", "Neufert"),
    "kitchen_aisle_double": Clearance(
        1200, "two cooks passing between opposing runs", "Neufert"),
    "wc_front": Clearance(600, "knee space in front of a WC", "Neufert"),
    "basin_front": Clearance(700, "standing at a basin", "Neufert"),
    "door_approach": Clearance(
        600, "space to stand clear of a swinging leaf", "Neufert"),
    "sofa_coffee_table": Clearance(
        400, "reach a coffee table from a seat", "Neufert"),
    "tv_viewing_min": Clearance(
        2000, "minimum comfortable viewing distance", "Neufert"),
}

# Kitchen work triangle: no leg under 1.2 m or over 2.7 m; perimeter 4-8 m.
TRIANGLE = {"leg_min_mm": 1200, "leg_max_mm": 2700,
            "sum_min_mm": 4000, "sum_max_mm": 8000,
            "source": "standard kitchen-design guidance (fridge / sink / hob)"}
# Fire and water must not sit side by side (Vastu, and practical splash).
HOB_SINK_MIN_MM = 600
