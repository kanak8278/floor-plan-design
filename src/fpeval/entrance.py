"""The arrival sequence: compound gate, driveway, sitout, foyer, front door.

Two distinct things were being conflated, and only one of them existed.

* The **front door** is on the building envelope. The solver already places one
  (`is_entrance` + a `front_door` opening), and it is present on every plan.
* The **compound gate** is on the PLOT boundary. Nothing placed it, because the
  layout solver lays out rooms and the gate is not a room. So plans that
  explicitly asked for a gate (suite case park-04) had none, and the plot
  boundary sat there with no way through it.

Site elements therefore get their own pass, driven off the envelope rather than
the room tiling: a gate needs a boundary and a road side, not a slicing tree.

The foyer is a design DECISION, not a fixed part of the programme. A foyer costs
1.5-8 m2 of pure circulation. On a 600 sqft plot that is 4-8% of the carpet area
spent on a lobby, which is the wrong trade; on a villa, arriving straight into
the formal seating is the wrong trade. So it is decided from the area available
and the typology, and the reason is recorded so a client can overrule it.
"""
from __future__ import annotations
import math
from dataclasses import dataclass

from shapely.geometry import LineString, Point, Polygon

from .ir import Furniture, P

# A foyer below this is a vestibule nobody uses; above it, wasted area.
FOYER_MIN_M2, FOYER_MAX_M2 = 1.8, 8.0
# Below this carpet area a foyer is not worth its footprint.
FOYER_CARPET_FLOOR_M2 = {
    "house_compact": 1e9,        # never: every m2 is contested
    "house_standard": 78.0,      # ~840 sqft carpet before a lobby pays for itself
    "house_mid": 55.0,
    "villa": 0.0,                # always: guests must not arrive in the seating
    "duplex": 70.0,
    "rental_floors": 1e9,        # a shared stair lobby serves the role
    "studio": 1e9,
    "apartment_compact": 1e9,
    "apartment_standard": 85.0,  # builder plans show one from ~1100 sqft
    "apartment_large": 0.0,
}

GATE_WIDTH_MM = 3000        # a car gate; a pedestrian-only gate is 1000
PEDESTRIAN_GATE_MM = 1000
DRIVEWAY_WIDTH_MM = 3000


@dataclass
class FoyerDecision:
    wanted: bool
    reason: str
    source: str          # "brief" | "auto"


def decide_foyer(scenario_key: str, carpet_m2: float, *,
                 asked: bool | None = None) -> FoyerDecision:
    """Foyer or straight into the hall.

    `asked` is the client's explicit wish and always wins -- an auto-decision
    that overrides a stated preference is a bug, not a feature.
    """
    if asked is True:
        return FoyerDecision(True, "the brief asks for a foyer", "brief")
    if asked is False:
        return FoyerDecision(False, "the brief wants the door to open into the hall",
                             "brief")
    floor = FOYER_CARPET_FLOOR_M2.get(scenario_key, 78.0)
    if floor >= 1e8:
        return FoyerDecision(
            False, f"a foyer costs {FOYER_MIN_M2}-{FOYER_MAX_M2} m² of pure "
                   f"circulation, which this typology cannot spare", "auto")
    if carpet_m2 >= floor:
        return FoyerDecision(
            True, f"carpet area {carpet_m2:.0f} m² is above the {floor:.0f} m² "
                  "threshold where a lobby pays for itself", "auto")
    return FoyerDecision(
        False, f"carpet area {carpet_m2:.0f} m² is below the {floor:.0f} m² "
               "threshold; a lobby would cost more than it returns", "auto")


# ------------------------------------------------------------ site elements
def _road_edge(plot: Polygon, road_facing: str, north_deg: float = 0.0
               ) -> LineString | None:
    """The boundary segment facing the road.

    `road_facing` is a compass letter; +Y is north rotated by `north_deg`.
    """
    if plot is None or plot.is_empty:
        return None
    ang = {"N": 90.0, "E": 0.0, "S": 270.0, "W": 180.0}.get(
        (road_facing or "N")[0].upper(), 90.0)
    ang = math.radians(ang - north_deg)
    out = (math.cos(ang), math.sin(ang))
    ring = list(plot.exterior.coords)
    best, best_dot = None, -1e18
    for a, b in zip(ring, ring[1:]):
        mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
        c = plot.centroid
        dot = (mx - c.x) * out[0] + (my - c.y) * out[1]
        if dot > best_dot:
            best_dot, best = dot, LineString([a, b])
    return best


def place_site_elements(plan, *, road_facing: str = "N",
                        want_gate: bool = True, want_driveway: bool = True,
                        want_parking: bool = False,
                        pedestrian_only: bool = False) -> list[str]:
    """Put the gate, driveway and parking on the plot. Mutates `plan.furniture`.

    Aligned to the front door where there is one, so the gate, the drive and the
    door line up rather than being three unrelated objects.
    """
    notes: list[str] = []
    pts = plan.site.plot_polygon
    if len(pts) < 3:
        notes.append("no plot boundary; site elements skipped")
        return notes
    plot = Polygon([q.as_tuple() for q in pts])
    if not plot.is_valid:
        plot = plot.buffer(0)
    edge = _road_edge(plot, road_facing, plan.site.north_deg)
    if edge is None:
        notes.append("could not identify the road edge")
        return notes

    # Aim at the front door if one exists, else the middle of the road edge.
    aim = None
    for o in plan.openings:
        if o.kind != "front_door":
            continue
        w = next((x for x in plan.walls if x.id == o.wall_id), None)
        if w is None:
            continue
        ln = LineString([w.start.as_tuple(), w.end.as_tuple()])
        aim = ln.interpolate(max(0.0, min(1.0, o.position)), normalized=True)
        break
    target = edge.interpolate(edge.project(aim), normalized=False) if aim \
        else edge.interpolate(0.5, normalized=True)

    gw = PEDESTRIAN_GATE_MM if pedestrian_only else GATE_WIDTH_MM
    ex, ey = edge.coords[-1][0] - edge.coords[0][0], edge.coords[-1][1] - edge.coords[0][1]
    rot = math.degrees(math.atan2(ey, ex))

    n = len(plan.furniture)
    if want_gate:
        plan.furniture.append(Furniture(
            id=f"site{n}", catalog_id="fence_gate", position=P(round(target.x), round(target.y)),
            rotation=round(rot % 180.0, 2), width=gw, depth=100, height=1200,
            room_id=None))
        n += 1
        notes.append(f"gate {gw} mm on the {road_facing} boundary, aligned to the front door"
                     if aim else f"gate {gw} mm centred on the {road_facing} boundary")

    if want_driveway and aim is not None:
        mid = Point((target.x + aim.x) / 2, (target.y + aim.y) / 2)
        run = target.distance(aim)
        if run > 500:
            plan.furniture.append(Furniture(
                id=f"site{n}", catalog_id="driveway", position=P(round(mid.x), round(mid.y)),
                rotation=round(math.degrees(math.atan2(aim.y - target.y,
                                                       aim.x - target.x)) % 180.0, 2),
                width=DRIVEWAY_WIDTH_MM, depth=round(run), height=30, room_id=None))
            n += 1
            notes.append(f"driveway {run/1000:.1f} m from gate to door")

    if want_parking and aim is not None:
        # Beside the drive, inside the front setback.
        px = target.x + (aim.x - target.x) * 0.35 - ey / max(edge.length, 1) * 2600
        py = target.y + (aim.y - target.y) * 0.35 + ex / max(edge.length, 1) * 2600
        if plot.contains(Point(px, py)):
            plan.furniture.append(Furniture(
                id=f"site{n}", catalog_id="car_sedan", position=P(round(px), round(py)),
                rotation=round(math.degrees(math.atan2(aim.y - target.y,
                                                       aim.x - target.x)) % 360.0, 2),
                width=1800, depth=4500, height=1500, room_id=None))
            notes.append("car parked in the front setback beside the drive")
        else:
            notes.append("no room for a car inside the setback; parking omitted")
    return notes


# --------------------------------------------------------- service symbols
# The catalogue carries 8 electrical and 5 plumbing items at height 0 -- 2D
# symbols, not solids. `furnish.py` places none of them ("Nothing places rugs,
# lighting, or the 15 electrical/plumbing 2D symbols"), which is why 35 of the
# suite's `must_place` assertions failed. They are rule-based, not a packing
# problem: a ceiling point goes at the centroid, a switch beside the door, an
# outlet on a wall away from the door. So they get their own pass.
SYMBOL_RULES: dict[str, tuple[str, ...]] = {
    "living":         ("sym_ceiling_light", "sym_ceiling_fan", "sym_switch",
                       "sym_outlet", "sym_outlet"),
    "dining":         ("sym_pendant", "sym_switch", "sym_outlet"),
    "bedroom":        ("sym_ceiling_light", "sym_ceiling_fan", "sym_switch",
                       "sym_outlet", "sym_outlet"),
    "master_bedroom": ("sym_ceiling_light", "sym_ceiling_fan", "sym_switch",
                       "sym_outlet", "sym_outlet"),
    "study":          ("sym_ceiling_light", "sym_switch", "sym_outlet"),
    "kitchen":        ("sym_ceiling_light", "sym_switch", "sym_outlet",
                       "sym_water_supply", "sym_drain", "sym_gas_line"),
    "bathroom":       ("sym_ceiling_light", "sym_switch", "sym_water_supply",
                       "sym_drain", "sym_water_heater"),
    "utility":        ("sym_ceiling_light", "sym_switch", "sym_water_supply",
                       "sym_drain", "sym_washer_hookup"),
    "pooja":          ("sym_ceiling_light", "sym_switch"),
    "foyer":          ("sym_ceiling_light", "sym_switch", "sym_smoke"),
    "store":          ("sym_ceiling_light", "sym_switch"),
    "stair":          ("sym_ceiling_light", "sym_switch"),
    "balcony":        ("wall_sconce_outdoor",),
    "sitout":         ("wall_sconce_outdoor",),
}
CEILING = {"sym_ceiling_light", "sym_ceiling_fan", "sym_pendant",
           "sym_recessed_light", "sym_smoke"}
AT_DOOR = {"sym_switch"}
WET_POINT = {"sym_water_supply", "sym_drain", "sym_water_heater",
             "sym_washer_hookup", "sym_gas_line"}


def place_symbols(plan, *, catalog_has=None) -> int:
    """Place electrical and plumbing symbols. Returns how many were placed.

    Deliberately simple: these are annotations on a drawing, not objects that
    can collide. A ceiling point goes at the room's centroid; a switch sits
    inside the room beside its door; an outlet and a wet point go on a wall.
    """
    from shapely.geometry import LineString, Point, Polygon
    placed = 0
    n = len(plan.furniture)
    walls = {w.id: w for w in plan.walls}

    for room in plan.rooms:
        want = SYMBOL_RULES.get(room.category or "")
        if not want or len(room.polygon) < 3:
            continue
        poly = Polygon([q.as_tuple() for q in room.polygon])
        if not poly.is_valid:
            poly = poly.buffer(0)
        if poly.is_empty or poly.area <= 0:
            continue
        c = poly.centroid
        inner = poly.buffer(-350) or poly
        if inner.is_empty:
            inner = poly

        # doors of this room, for switch placement
        doors = []
        for o in plan.openings:
            if o.kind == "window":
                continue
            w = walls.get(o.wall_id)
            if w is None:
                continue
            pt = LineString([w.start.as_tuple(), w.end.as_tuple()]).interpolate(
                max(0.0, min(1.0, o.position)), normalized=True)
            if poly.buffer(250).contains(pt):
                doors.append(pt)

        # wall points for outlets and wet points, spread around the boundary
        ring = poly.buffer(-150).exterior if not poly.buffer(-150).is_empty else poly.exterior
        spread = [ring.interpolate(f, normalized=True) for f in (0.12, 0.38, 0.62, 0.88)]
        si = 0
        for item in want:
            if catalog_has is not None and not catalog_has(item):
                continue
            if item in CEILING:
                x, y = c.x, c.y
            elif item in AT_DOOR and doors:
                d = doors[0]
                near = inner.exterior.interpolate(
                    inner.exterior.project(d), normalized=False) \
                    if not inner.is_empty else d
                x, y = near.x, near.y
            else:
                pt = spread[si % len(spread)]
                si += 1
                x, y = pt.x, pt.y
            plan.furniture.append(Furniture(
                id=f"sym{n}", catalog_id=item, position=P(round(x), round(y)),
                rotation=0.0, width=150, depth=150, height=0, room_id=room.id))
            n += 1
            placed += 1
    return placed
