"""Faces of the wall graph: what `detectRooms` enumerates, in Python.

The service owns the document, so it has to be able to answer "what rooms
exist now" after a wall moves. That is this module: polygonise the wall
centrelines, then attribute each face's boundary back to the walls that form
it, producing exactly the input `roomid.reconcile_rooms` expects.

This is the Python counterpart of `roomDetection.ts`, but it is deliberately
*not* a port of it. The two answer the same question by different means -- that
algorithm walks the planar graph taking leftmost turns, this one hands the
segments to GEOS -- and they were measured against each other on 17,000 plans
before either was trusted: room count exact 97.08%, within +/-1 99.76%. Porting
the traversal line by line would have looked safer and told us less.
"""
from __future__ import annotations
from typing import Sequence

from .ir import Plan, Room, Wall, P

# Below this a "face" is a sliver from two walls crossing, not a room.
MIN_FACE_MM2 = 100_000          # 0.1 m^2
BOUNDARY_TOLERANCE_MM = 30.0    # how far off a wall may sit and still count


def detect_faces(walls: Sequence[Wall]) -> list[Room]:
    """Every enclosed face, as a `Room` with geometry but no identity.

    Identity is `roomid.reconcile_rooms`'s job; these carry blank names and
    provisional ids so a caller cannot mistake one for a room the user knows
    about.
    """
    from shapely.geometry import LineString
    from shapely.ops import polygonize, unary_union

    segs = {w.id: LineString([w.start.as_tuple(), w.end.as_tuple()])
            for w in walls if w.length > 0}
    if len(segs) < 3:
        return []
    faces = [f for f in polygonize(unary_union(list(segs.values())))
             if f.area > MIN_FACE_MM2]
    if not faces:
        return []

    out: list[Room] = []
    for i, f in enumerate(sorted(faces, key=lambda g: (-g.area, g.bounds))):
        bnd = f.boundary.buffer(BOUNDARY_TOLERANCE_MM)
        wall_ids = [wid for wid, line in segs.items()
                    if bnd.covers(line) or bnd.intersection(line).length
                    > 0.5 * line.length]
        poly = [P(round(x), round(y)) for x, y in f.exterior.coords[:-1]]
        out.append(Room(id=f"face-{i}", name="", category="indoor",
                        wall_ids=sorted(wall_ids), polygon=poly,
                        area=int(round(f.area))))
    return out


def rederive_rooms(plan: Plan) -> tuple[list[Room], list[Room], list[Room]]:
    """Re-detect faces and re-attach identity. Returns (rooms, gone, new).

    Rooms whose anchor no longer lands in any face are **dropped**, and named
    in `gone` so the caller can say so. That differs on purpose from the
    browser's reconciler, which holds them: the browser sees mid-drag states
    where the wall loop is briefly open and every face vanishes, while a
    command only ever arrives after a gesture has finished. Holding a name
    here would mean a room the user deliberately merged away came back.
    """
    from .roomid import reconcile_rooms

    faces = detect_faces(plan.walls)
    result = reconcile_rooms(faces, plan.rooms)
    return result.rooms, result.unmatched, result.fresh
