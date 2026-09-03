"""Stable room identity, server side.

The twin of `vendor/openPlan3D/src/lib/utils/roomIdentity.ts`. Both exist
because both sides need it: the browser reconciles optimistically so the label
does not flicker mid-drag, and the service reconciles authoritatively because
it owns the document. Two implementations of one rule is a real risk, so
`tests/test_room_identity.py` runs the same cases through both and compares.

Rooms are faces of the wall graph, so their *geometry* is derived. Their
*identity* must not be. A room owns an anchor -- a point recorded when it was
first named -- and whichever face contains that point is that room. Splitting a
wall, adding a window, or nudging a wall does not move the point; only a change
that genuinely re-partitions the space does, which is when identity should
change anyway.

The rule this replaces matched rooms by their exact wall-id set, so
`splitWall()` alone was enough to turn "Master Bedroom" back into "Room 3".
"""
from __future__ import annotations
from dataclasses import dataclass, replace
from typing import Iterable, Optional, Sequence

from .ir import Room, P

# What belongs to the room as a thing, rather than to the face that revealed it.
CARRIED = ("name", "category", "room_class", "floor_texture", "color",
           "label_offset", "anchor")


@dataclass
class Reconciliation:
    rooms: list[Room]           # this pass's faces, wearing their real identity
    unmatched: list[Room]       # persisted rooms no face claimed -- held, not deleted
    fresh: list[Room]           # faces that matched nothing, so genuinely new


def point_in_polygon(p: P, poly: Sequence[P]) -> bool:
    """Ray casting, matching the TS implementation edge case for edge case."""
    if len(poly) < 3:
        return False
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        a, b = poly[i], poly[j]
        j = i
        if (a.y > p.y) == (b.y > p.y):
            continue
        x = (b.x - a.x) * (p.y - a.y) / (b.y - a.y) + a.x
        if p.x < x:
            inside = not inside
    return inside


def polygon_area(poly: Sequence[P]) -> float:
    if len(poly) < 3:
        return 0.0
    s = 0.0
    for i in range(len(poly)):
        j = (i + 1) % len(poly)
        s += poly[i].x * poly[j].y - poly[j].x * poly[i].y
    return abs(s / 2.0)


def centroid(poly: Sequence[P]) -> P:
    if not poly:
        return P(0, 0)
    return P(round(sum(p.x for p in poly) / len(poly)),
             round(sum(p.y for p in poly) / len(poly)))


def mint_room_id(anchor: P) -> str:
    """FNV-1a over the rounded centroid, so the same face gets the same id
    twice in a row. Must stay byte-compatible with `mintRoomId` in the TS."""
    h = 0x811C9DC5
    for ch in f"{anchor.x}|{anchor.y}":
        h ^= ord(ch)
        h = (h * 0x01000193) & 0xFFFFFFFF
    return f"room-{_base36(h)}"


def _base36(n: int) -> str:
    if n == 0:
        return "0"
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    out = []
    while n:
        n, r = divmod(n, 36)
        out.append(digits[r])
    return "".join(reversed(out))


def _same_wall_set(a: Iterable[str], b: Iterable[str]) -> bool:
    sa, sb = set(a), set(b)
    return bool(sa) and sa == sb


def reconcile_rooms(detected: Sequence[Room],
                    persisted: Sequence[Room]) -> Reconciliation:
    """Give each detected face the identity of the persisted room it is.

    `detected` rooms must carry polygons -- they are faces, and the polygon is
    how a face is recognised. Later entries in `persisted` win ties, so pass
    the saved rooms last.
    """
    polys = [list(r.polygon) for r in detected]
    areas = [polygon_area(p) for p in polys]
    cents = [centroid(p) for p in polys]

    claimed: dict[int, Room] = {}
    matched: set[str] = set()

    # Pass 1: anchors. When two anchors land in one face -- the user deleted a
    # dividing wall -- the room whose recorded area is closest wins and the
    # other is reported, not silently merged away.
    for room in persisted:
        if room.anchor is None:
            continue
        best, best_gap = -1, float("inf")
        for i, poly in enumerate(polys):
            if not point_in_polygon(room.anchor, poly):
                continue
            gap = abs(areas[i] - room.area)
            if gap < best_gap:
                best, best_gap = i, gap
        if best < 0:
            continue
        rival = claimed.get(best)
        if rival is not None:
            if abs(areas[best] - rival.area) <= best_gap:
                continue                      # incumbent fits better
            matched.discard(rival.id)
        claimed[best] = room
        matched.add(room.id)

    # Pass 2: exact wall sets, for rooms stored before anchors existed.
    for room in persisted:
        if room.id in matched:
            continue
        for i, face in enumerate(detected):
            if i in claimed:
                continue
            if _same_wall_set(room.wall_ids, face.wall_ids):
                claimed[i] = room
                matched.add(room.id)
                break

    rooms: list[Room] = []
    fresh: list[Room] = []
    for i, face in enumerate(detected):
        owner = claimed.get(i)
        # Geometry always comes from the face: it describes the plan as it is
        # now, not as it was when the room was named.
        out = replace(face)
        if owner is not None:
            out.id = owner.id
            for attr in CARRIED:
                value = getattr(owner, attr)
                if value not in (None, ""):
                    setattr(out, attr, value)
            if out.anchor is None or not point_in_polygon(out.anchor, polys[i]):
                out.anchor = cents[i]
        else:
            out.anchor = cents[i]
            out.id = mint_room_id(cents[i])
            fresh.append(out)
        rooms.append(out)

    return Reconciliation(
        rooms=rooms,
        unmatched=[r for r in persisted if r.id not in matched],
        fresh=fresh,
    )


def ensure_anchors(rooms: Sequence[Room]) -> None:
    """Give every room an anchor, in place. Called once when a document is
    adopted, so a plan built before anchors existed becomes addressable."""
    for r in rooms:
        if r.anchor is None and len(r.polygon) >= 3:
            r.anchor = centroid(r.polygon)
