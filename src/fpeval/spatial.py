"""What the plan LOOKS like, for a reader that cannot see it.

`plan_digest` gives the model a bill of quantities: room areas, wall lengths,
openings as a fraction along a wall id. It contains no coordinates at all. To
work out that the kitchen touches a bathroom the model has to intersect
wall-id sets, and to work out which room faces the road it has to guess --
nothing in that text says where anything is.

So every spatial judgement it made was a guess, and it made a lot of them:
`move_wall_parallel` takes a compass direction, `set_room_zone` takes a
compass zone, and "the hall is in the wrong corner" is a spatial claim. This
module supplies the missing half in three forms, all text, because text is
cache-stable and costs nothing to regenerate:

* `plan_png`     -- the drawing itself, rasterised small. The gestalt, in the
                    form a human would look at.
* `room_facts`   -- per room: compass zone, which faces are exterior, which
                    rooms it touches, and which of those it can be walked to.

An earlier version drew a character grid instead. The image is both cheaper to
read and more faithful, and it costs about 490 tokens at 640 px -- less than
the grid did. The text half stays because it states compass zones and the
touches/connects split exactly, and making the model infer those from pixels
would be asking it to do arithmetic on a picture.

The touches/connects distinction is the one that changes behaviour. "Shares a
wall with the dining but has no door to it" is directly actionable -- it is an
`add_door` away -- and is invisible in a door graph alone.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

from .ir import Plan, Room

# Character cells are about twice as tall as they are wide, so two columns per
# unit of width keeps a square room looking square.
CELL_ASPECT = 2
DEFAULT_COLS = 68
# Two rooms "touch" when their boundaries overlap by at least a door's width;
# a corner kiss is not a shared wall.
TOUCH_MIN_MM = 800

_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
COMPASS_8 = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


def _bbox(r: Room) -> Optional[tuple[int, int, int, int]]:
    if not r.polygon or len(r.polygon) < 3:
        return None
    xs = [p.x for p in r.polygon]
    ys = [p.y for p in r.polygon]
    return int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))


def _plan_bbox(rooms: Iterable[Room]) -> Optional[tuple[int, int, int, int]]:
    boxes = [b for b in (_bbox(r) for r in rooms) if b]
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def compass_of(dx: float, dy: float, north_deg: float = 0.0) -> str:
    """Compass label for an offset, honouring where north actually is.

    `Site.north_deg` is the bearing of the +Y axis, so a plan drawn with north
    to the right has north_deg = 90 and "up the page" is west. Hardcoding
    +Y = north is wrong on three of the four facings.
    """
    import math
    if abs(dx) < 1 and abs(dy) < 1:
        return "C"
    # Screen angle measured clockwise from +Y, then rotated into world north.
    ang = math.degrees(math.atan2(dx, dy)) % 360.0
    ang = (ang + north_deg) % 360.0
    return COMPASS_8[int((ang + 22.5) % 360.0 // 45.0)]


def _edge_overlap(a: tuple[int, int, int, int],
                  b: tuple[int, int, int, int]) -> int:
    """Length of shared boundary between two boxes, 0 if they only kiss."""
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    tol = 250          # wall thickness plus slop; rooms are centreline faces
    if abs(ax1 - bx0) <= tol or abs(bx1 - ax0) <= tol:
        return max(0, min(ay1, by1) - max(ay0, by0))
    if abs(ay1 - by0) <= tol or abs(by1 - ay0) <= tol:
        return max(0, min(ax1, bx1) - max(ax0, bx0))
    return 0


def room_facts(plan: Plan) -> str:
    """Per-room spatial facts: where it sits, what it faces, what it touches.

    `touches` and `connects to` are reported separately on purpose. A room that
    shares a wall with the dining and has no door to it is one `add_door` from
    being fixed, and a door graph alone cannot say that -- which is why the
    agent kept proposing walls where an opening was wanted.
    """
    rooms = [r for r in plan.rooms if _bbox(r)]
    if not rooms:
        return "(no rooms yet)"
    bb = _plan_bbox(rooms)
    assert bb is not None
    px0, py0, px1, py1 = bb
    cx, cy = (px0 + px1) / 2, (py0 + py1) / 2
    north = plan.site.north_deg or 0.0
    boxes = {r.id: _bbox(r) for r in rooms}

    # doors, as room-to-room links
    doors: dict[str, set[str]] = {r.id: set() for r in rooms}
    for oid, a, b in _door_pairs(plan):
        if a in doors and b in doors:
            doors[a].add(b)
            doors[b].add(a)

    lines = ["ROOM POSITIONS (compass is true, not screen)"]
    for r in rooms:
        b = boxes[r.id]
        rx, ry = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        zone = compass_of(rx - cx, ry - cy, north)
        faces = []
        tol = 300
        if abs(b[3] - py1) <= tol: faces.append(compass_of(0, 1, north))
        if abs(b[1] - py0) <= tol: faces.append(compass_of(0, -1, north))
        if abs(b[2] - px1) <= tol: faces.append(compass_of(1, 0, north))
        if abs(b[0] - px0) <= tol: faces.append(compass_of(-1, 0, north))
        touch = sorted(o.id for o in rooms
                       if o.id != r.id
                       and _edge_overlap(b, boxes[o.id]) >= TOUCH_MIN_MM)
        conn = sorted(doors[r.id])
        walled = [t for t in touch if t not in conn]
        lines.append(
            f"  {r.id}: {zone} of the plan"
            + (f", exterior wall facing {'/'.join(faces)}" if faces
               else ", NO exterior wall (interior room, cannot have a window)")
            + f" | door to: {', '.join(conn) or 'nothing'}"
            + (f" | shares a wall but no door with: {', '.join(walled)}"
               if walled else ""))
    return "\n".join(lines)


def _door_pairs(plan: Plan) -> list[tuple[str, str, str]]:
    """(opening_id, room_a, room_b) for every door, via the rules context.

    Reuses the validator's own derivation so the model is told the same door
    graph the rules are judged against.
    """
    try:
        from .rules import _build_ctx
        from .bylaws import BENGALURU
        return list(_build_ctx(plan, {}, BENGALURU).door_edges)
    except Exception:
        return []


# 640 px keeps every room label, dimension and the north arrow legible while
# costing about 490 image tokens. 1000 px costs 1200 and reads no better.
PNG_WIDTH = 640


def plan_png(plan: Plan, *, width: int = PNG_WIDTH,
             mode: str = "presentation") -> Optional[bytes]:
    """The plan as a small PNG, or None if it cannot be drawn.

    Returns None rather than raising: a missing picture should degrade the
    context, not fail the turn.
    """
    if not plan.rooms:
        return None
    try:
        import resvg_py
        from .render import render
        svg = render(plan, mode)
        # resvg refuses an explicit pixel width on an SVG sized in mm, and the
        # renderer emits mm because it is a drawing meant for paper. Dropping
        # the unit leaves the viewBox to set the aspect and lets `width` fix
        # the raster size.
        svg = re.sub(r'width="([\d.]+)mm"', r'width="\1"', svg, count=1)
        svg = re.sub(r'height="([\d.]+)mm"', r'height="\1"', svg, count=1)
        return bytes(resvg_py.svg_to_bytes(svg_string=svg, width=width,
                                           background="#ffffff"))
    except Exception:
        return None


def spatial_block(plan: Plan) -> str:
    """The text half: exact compass zones, exposure, touches vs connects."""
    return room_facts(plan)
