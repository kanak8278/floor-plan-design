"""Room identity: the Python rule, and its conformance with the TypeScript one.

Two implementations exist because both sides need the rule -- the browser
reconciles optimistically so a label does not flicker mid-drag, the service
reconciles authoritatively because it owns the document. That duplication is
the standing risk in this design, so the last test in this file pushes
byte-identical faces through both and compares the answers.

Units differ on purpose: the IR is integer millimetres, `Project` is float
centimetres, and each side is fed its own. A conformance run that passed only
because both sides used the same units would not be testing the thing that
actually breaks in production.
"""
from __future__ import annotations
import json
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from fpeval.ir import Room, P                                  # noqa: E402
from fpeval.roomid import (                                    # noqa: E402
    reconcile_rooms, point_in_polygon, polygon_area, centroid, mint_room_id,
    ensure_anchors,
)

MM_PER_CM = 10


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def rect(x0: int, y0: int, x1: int, y1: int) -> list[P]:
    return [P(x0, y0), P(x1, y0), P(x1, y1), P(x0, y1)]


def face(rid: str, poly: list[P], walls: list[str]) -> Room:
    """A detected face: geometry and a wall list, no identity worth keeping."""
    return Room(id=rid, name="", category="indoor", wall_ids=walls,
                polygon=poly, area=int(polygon_area(poly)))


BOX = rect(0, 0, 4000, 3000)            # 4.0 x 3.0 m
LEFT = rect(0, 0, 2000, 3000)
RIGHT = rect(2000, 0, 4000, 3000)


# --------------------------------------------------------------------------
# the rule
# --------------------------------------------------------------------------

def test_name_survives_a_wall_split():
    """The defect this replaces: `splitWall` changed the wall set, and the
    editor matched rooms *by* the wall set."""
    detected = [face("f0", BOX, ["w0", "w1", "w2", "w3"])]
    named = reconcile_rooms(detected, []).rooms[0]
    named.name = "Master Bedroom"
    named.category = "master_bedroom"

    # After the split the wall set is different in every element that matters.
    after = reconcile_rooms([face("f9", BOX, ["w0a", "w0b", "w1", "w2", "w3"])],
                            [named])
    assert len(after.rooms) == 1
    assert after.rooms[0].name == "Master Bedroom"
    assert after.rooms[0].id == named.id
    assert after.rooms[0].category == "master_bedroom"
    assert after.rooms[0].wall_ids == ["w0a", "w0b", "w1", "w2", "w3"]
    assert after.unmatched == []


def test_geometry_is_refreshed_even_though_identity_is_not():
    detected = [face("f0", BOX, ["w0"])]
    named = reconcile_rooms(detected, []).rooms[0]
    named.name = "Hall"
    shrunk = rect(0, 0, 3000, 3000)
    after = reconcile_rooms([face("f1", shrunk, ["w0", "w4"])], [named])
    assert after.rooms[0].name == "Hall"
    assert after.rooms[0].polygon == shrunk
    assert after.rooms[0].area == int(polygon_area(shrunk))


def test_dividing_a_room_keeps_identity_on_the_anchored_side():
    whole = reconcile_rooms([face("f0", BOX, ["w0"])], []).rooms[0]
    whole.name = "Living"
    whole.anchor = P(1000, 1500)            # left of the new wall

    after = reconcile_rooms([face("a", LEFT, ["w0", "w4"]),
                             face("b", RIGHT, ["w1", "w4"])], [whole])
    keepers = [r for r in after.rooms if r.name == "Living"]
    assert len(keepers) == 1
    assert keepers[0].id == whole.id
    assert keepers[0].polygon == LEFT
    assert len(after.fresh) == 1
    assert after.fresh[0].name == ""


def test_merging_reports_the_loser_rather_than_dropping_it():
    both = reconcile_rooms([face("a", LEFT, ["w0", "w4"]),
                            face("b", RIGHT, ["w1", "w4"])], []).rooms
    both[0].name, both[1].name = "Bedroom 1", "Bedroom 2"

    after = reconcile_rooms([face("m", BOX, ["w0", "w1", "w2", "w3"])], both)
    assert len(after.rooms) == 1
    assert after.rooms[0].name in ("Bedroom 1", "Bedroom 2")
    assert len(after.unmatched) == 1
    assert after.unmatched[0].name != after.rooms[0].name


def test_open_wall_graph_does_not_destroy_names():
    """Mid-drag the loop is briefly broken and no face exists. A name must not
    be a casualty of a gesture that has not finished."""
    named = reconcile_rooms([face("f0", BOX, ["w0"])], []).rooms[0]
    named.name = "Master"
    after = reconcile_rooms([], [named])
    assert after.rooms == []
    assert [r.name for r in after.unmatched] == ["Master"]


def test_legacy_room_without_an_anchor_matches_by_wall_set():
    legacy = Room(id="room-1-1717171717171", name="Kitchen", category="kitchen",
                  wall_ids=["w0", "w1", "w2", "w3"], polygon=[], area=0)
    after = reconcile_rooms([face("f0", BOX, ["w0", "w1", "w2", "w3"])], [legacy])
    assert after.rooms[0].name == "Kitchen"
    assert after.rooms[0].id == legacy.id
    assert after.rooms[0].anchor is not None      # and it is addressable now


def test_ids_are_deterministic():
    a = reconcile_rooms([face("f0", BOX, ["w0"])], []).rooms[0].id
    b = reconcile_rooms([face("f0", BOX, ["w0"])], []).rooms[0].id
    assert a == b
    assert not any(part.isdigit() and len(part) == 13 for part in a.split("-"))


def test_ensure_anchors_backfills_in_place():
    rooms = [Room("r0", "Living", "living", [], BOX, int(polygon_area(BOX)))]
    assert rooms[0].anchor is None
    ensure_anchors(rooms)
    assert rooms[0].anchor == centroid(BOX)


@pytest.mark.parametrize("point,inside", [
    (P(2000, 1500), True),
    (P(-1, 1500), False),
    (P(4001, 1500), False),
    (P(2000, -1), False),
])
def test_point_in_polygon(point, inside):
    assert point_in_polygon(point, BOX) is inside


def test_point_in_polygon_rejects_a_degenerate_ring():
    assert point_in_polygon(P(0, 0), [P(0, 0), P(1, 1)]) is False


# --------------------------------------------------------------------------
# conformance with the TypeScript rule
# --------------------------------------------------------------------------

CONFORMANCE_CASES: list[tuple[str, list[Room], list[Room]]] = [
    # (name, faces, persisted)
    ("split", [face("f9", BOX, ["w0a", "w0b", "w1", "w2", "w3"])], []),
    ("fresh_box", [face("f0", BOX, ["w0", "w1", "w2", "w3"])], []),
    ("two_faces", [face("a", LEFT, ["w0", "w4"]),
                   face("b", RIGHT, ["w1", "w4"])], []),
    ("no_faces", [], []),
]


def _with_persisted() -> list[tuple[str, list[Room], list[Room]]]:
    """Cases that need a persisted room built by a first reconcile pass."""
    named = reconcile_rooms([face("f0", BOX, ["w0", "w1", "w2", "w3"])], []).rooms[0]
    named.name = "Master Bedroom"

    left_anchored = reconcile_rooms([face("f0", BOX, ["w0"])], []).rooms[0]
    left_anchored.name = "Living"
    left_anchored.anchor = P(1000, 1500)

    two = reconcile_rooms([face("a", LEFT, ["w0", "w4"]),
                           face("b", RIGHT, ["w1", "w4"])], []).rooms
    two[0].name, two[1].name = "Bedroom 1", "Bedroom 2"

    legacy = Room(id="room-legacy", name="Kitchen", category="kitchen",
                  wall_ids=["w0", "w1", "w2", "w3"], polygon=[], area=0)

    return [
        ("keeps_name_after_split",
         [face("f9", BOX, ["w0a", "w0b", "w1", "w2", "w3"])], [named]),
        ("divide_keeps_anchored_side",
         [face("a", LEFT, ["w0", "w4"]), face("b", RIGHT, ["w1", "w4"])],
         [left_anchored]),
        ("merge_reports_loser",
         [face("m", BOX, ["w0", "w1", "w2", "w3"])], two),
        ("legacy_wall_set_match",
         [face("f0", BOX, ["w0", "w1", "w2", "w3"])], [legacy]),
        ("open_graph_holds_name", [], [named]),
    ]


def _to_cm_payload(cases) -> str:
    """The same cases in the units the browser uses."""
    def room_cm(r: Room, *, as_face: bool) -> dict:
        out = {
            "id": r.id,
            "name": r.name,
            "walls": list(r.wall_ids),
            "floorTexture": r.floor_texture or "hardwood",
            # Project stores area in m^2; the IR stores mm^2.
            "area": round(r.area / 1_000_000.0, 6),
        }
        if r.anchor is not None:
            out["anchor"] = {"x": r.anchor.x / MM_PER_CM,
                             "y": r.anchor.y / MM_PER_CM}
        return out

    payload = []
    for name, faces, persisted in cases:
        payload.append({
            "name": name,
            "faces": [{"room": room_cm(f, as_face=True),
                       "polygon": [{"x": p.x / MM_PER_CM, "y": p.y / MM_PER_CM}
                                   for p in f.polygon]}
                      for f in faces],
            "persisted": [room_cm(r, as_face=False) for r in persisted],
        })
    return json.dumps(payload)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_python_and_typescript_agree():
    """The same faces through both rules must give the same identities.

    Compared: which id each face ends up with, the name it carries, which
    persisted rooms were reported unmatched, and which faces were called fresh.
    Anchors are compared after unit conversion.
    """
    cases = CONFORMANCE_CASES + _with_persisted()
    driver = os.path.join(ROOT, "tests", "js", "reconcile_driver.ts")
    proc = subprocess.run([shutil.which("node"), driver],
                          input=_to_cm_payload(cases), text=True,
                          capture_output=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-2000:]
    ts = json.loads(proc.stdout)
    assert len(ts) == len(cases)

    for (name, faces, persisted), got in zip(cases, ts):
        assert got["name"] == name
        py = reconcile_rooms(faces, persisted)
        assert [r.id for r in py.rooms] == [r["id"] for r in got["rooms"]], \
            f"{name}: room ids diverged"
        assert [r.name for r in py.rooms] == [r["name"] for r in got["rooms"]], \
            f"{name}: room names diverged"
        assert [r.id for r in py.unmatched] == got["unmatched"], \
            f"{name}: unmatched diverged"
        assert [r.id for r in py.fresh] == got["fresh"], \
            f"{name}: fresh diverged"
        for r, g in zip(py.rooms, got["rooms"]):
            if r.anchor is None:
                assert g["anchor"] is None, f"{name}: anchor presence diverged"
            else:
                assert g["anchor"] is not None, f"{name}: anchor presence diverged"
                assert r.anchor.x == round(g["anchor"]["x"] * MM_PER_CM), \
                    f"{name}: anchor x diverged"
                assert r.anchor.y == round(g["anchor"]["y"] * MM_PER_CM), \
                    f"{name}: anchor y diverged"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_minted_ids_match_across_languages():
    """`mint_room_id` and `mintRoomId` must be the same function.

    The ids go in the document and into chat transcripts, so a Python id and a
    TypeScript id for the same face have to be the same string -- otherwise the
    optimistic client and the authoritative server disagree about what the user
    just named.
    """
    cases = [("fresh_box", [face("f0", BOX, ["w0"])], []),
             ("two_faces", [face("a", LEFT, ["w0"]), face("b", RIGHT, ["w1"])], [])]
    driver = os.path.join(ROOT, "tests", "js", "reconcile_driver.ts")
    proc = subprocess.run([shutil.which("node"), driver],
                          input=_to_cm_payload(cases), text=True,
                          capture_output=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-2000:]
    for (_, faces, _), got in zip(cases, json.loads(proc.stdout)):
        expected = [mint_room_id(centroid(f.polygon)) for f in faces]
        assert expected == [r["id"] for r in got["rooms"]]


# --------------------------------------------------------------------------
# the tie-break, which is contract and not detail
# --------------------------------------------------------------------------

def test_a_later_persisted_room_wins_an_exact_tie():
    """`reconcile_rooms` documents that later entries win ties, and callers
    depend on it: the browser passes last pass's on-screen rooms first and the
    floor's *saved* rooms last, and the two have identical areas.

    This was wrong for a while -- the comparison kept the incumbent on a tie --
    and the symptom was that a room the assistant had just renamed went on
    reading "Room 1" on the plan while the chat said otherwise. Exactly the
    two-systems failure the shared document exists to prevent.
    """
    detected = [face("f0", BOX, ["w0", "w1", "w2", "w3"])]
    stale = reconcile_rooms(detected, []).rooms[0]        # unnamed, anchored
    saved = Room(id="room-saved", name="Master Bedroom",
                 category="master_bedroom", wall_ids=list(stale.wall_ids),
                 polygon=list(BOX), area=stale.area, anchor=stale.anchor)

    after = reconcile_rooms(detected, [stale, saved])      # saved passed last
    assert after.rooms[0].name == "Master Bedroom"
    assert after.rooms[0].id == "room-saved"
    assert [r.id for r in after.unmatched] == [stale.id]


def test_a_strictly_better_fit_still_wins():
    """The tie-break must not become "last one always wins" -- a room whose
    recorded area actually matches the face should keep it."""
    left = face("a", LEFT, ["w0", "w4"])
    good = Room(id="room-good", name="Right fit", category="bedroom",
                wall_ids=["w0", "w4"], polygon=list(LEFT),
                area=int(polygon_area(LEFT)), anchor=P(1000, 1500))
    wrong = Room(id="room-wrong", name="Wrong fit", category="bedroom",
                 wall_ids=["w0", "w4"], polygon=list(BOX),
                 area=int(polygon_area(BOX)), anchor=P(1000, 1500))
    after = reconcile_rooms([left], [good, wrong])         # worse fit last
    assert after.rooms[0].name == "Right fit"
    assert [r.id for r in after.unmatched] == ["room-wrong"]
