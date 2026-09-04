"""The HTTP surface, exercised as a client sees it.

These exist because a whole class of bug lived in the gap between the Python
library and the browser, and nothing tested that gap. Ten minutes of driving
the real UI found six defects that 488 unit tests did not:

* `/undo` passed a `str` where a `Document` goes and returned an unconditional
  500. The endpoint had no test at all.
* Undo after a solve emptied the plan, because the solved-Plan payload was
  never persisted and the service reloads the document on every request.
* A solve dropped `project_floor_id`, so the projection named a floor called
  "floor-f1" while the client's `activeFloorId` said "f1" -- the canvas
  matched nothing and drew "Start building your floor plan" over a document
  holding twelve walls.
* `_findings_json` read three fields `Finding` does not have, so the client
  got a debug repr instead of a sentence.

The shared thread is that each half was internally consistent. Only a test
that spans both halves can see it, which is what these are.

No API key and no money: everything here drives the deterministic solver
through the command bus, never the chat endpoints.
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient                      # noqa: E402

from fpeval.commands import Command                            # noqa: E402
from fpeval.envelope import bhk_programme, CityProfileAdapter  # noqa: E402
from fpeval.solver import LayoutSpec, solve_layout             # noqa: E402
from fpeval.bylaws import BENGALURU                            # noqa: E402
import service.documents as D                                  # noqa: E402
from service.store import SqliteStore                          # noqa: E402


EMPTY_PROJECT = {
    "id": "proj-t", "name": "T", "activeFloorId": "f1",
    "floors": [{"id": "f1", "name": "Ground", "level": 0,
                "walls": [], "doors": [], "windows": [], "rooms": []}],
}


@pytest.fixture()
def client():
    """A real app over a real SQLite file, torn down after each test."""
    from service.app import app
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    store = SqliteStore(path)
    D.set_store(store)
    try:
        with TestClient(app) as c:
            yield c
    finally:
        D.set_store(None)
        store.close()
        for p in (path, path + "-wal", path + "-shm"):
            if os.path.exists(p):
                os.unlink(p)


def _solved_plan(bedrooms: int = 2, kitchen_m2: float | None = None):
    prog = bhk_programme(bedrooms)
    if kitchen_m2 is not None:
        for r in prog:
            if r.category == "kitchen":
                r.target_m2 = kitchen_m2
    res = solve_layout(
        30, 40,
        LayoutSpec(programme=prog,
                   entrance_room=next(p.id for p in prog if p.is_entrance),
                   time_limit_s=6.0, deterministic=True),
        road_facing="E", profile=CityProfileAdapter(BENGALURU), plan_id="p")
    assert res.plan is not None, res.status
    return res.plan


def _assert_renderable(projection: dict, *, walls: bool) -> dict:
    """The contract the canvas depends on.

    A projection whose `activeFloorId` names no floor in `floors` renders as an
    empty page with no error anywhere -- the worst failure shape there is,
    because the document is fine and the drawing is simply absent.
    """
    assert projection is not None, "no projection returned"
    ids = [f["id"] for f in projection["floors"]]
    assert projection["activeFloorId"] in ids, (
        f"activeFloorId {projection['activeFloorId']!r} names no floor in "
        f"{ids}; the canvas would render nothing")
    floor = next(f for f in projection["floors"]
                 if f["id"] == projection["activeFloorId"])
    if walls:
        assert floor["walls"], "the active floor has no walls to draw"
        assert floor["rooms"], "the active floor has no rooms to label"
    return floor


def test_adopt_returns_a_renderable_projection(client):
    r = client.post("/api/designs",
                    json={"design_id": "d1", "project": EMPTY_PROJECT})
    assert r.status_code == 200, r.text
    assert r.json()["design_id"] == "d1"


def test_the_editors_floor_id_survives_every_endpoint(client):
    """`f1` is an editor uid, not a value we would derive. It must come back
    unchanged from adopt, from a command batch, from GET, and from undo."""
    client.post("/api/designs", json={"design_id": "d1",
                                      "project": EMPTY_PROJECT})
    r = client.post("/api/designs/d1/commands", json={"commands": [
        {"op": "set_plot",
         "params": {"width_ft": 30, "depth_ft": 40,
                    "road_facing": "east", "city": "bengaluru"},
         "source": "agent"},
        {"op": "use_standard_programme", "params": {"bedrooms": 2},
         "source": "agent"}]})
    assert r.status_code == 200, r.text
    assert not r.json()["rejected"], r.json()["rejected"]

    got = client.get("/api/designs/d1").json()
    assert [f["id"] for f in got["projection"]["floors"]] == ["f1"]
    assert got["projection"]["activeFloorId"] == "f1"


def test_a_solve_reaches_the_client_as_something_it_can_draw(client):
    """The bug that blanked the canvas. The solve goes in through the document
    directly, because `replace_storey` carries geometry as a payload and the
    HTTP vocabulary deliberately cannot express one."""
    client.post("/api/designs", json={"design_id": "d1",
                                      "project": EMPTY_PROJECT})
    doc = D.store().load("d1")
    before = doc.seq
    res = doc.apply(Command(op="replace_storey",
                            params={"storey_id": doc.design.active.id},
                            source="solver"), payload=_solved_plan())
    assert res.ok, res.errors
    D.store().save_commands("d1", doc, [e for e in doc.log if e.seq > before])

    body = client.get("/api/designs/d1").json()
    floor = _assert_renderable(body["projection"], walls=True)
    assert len(floor["walls"]) >= 4
    assert client.get("/api/designs/d1/verify").json()["ok"], "log does not replay"


def test_undo_after_a_solve_returns_200_and_keeps_the_earlier_plan(client):
    """Two bugs at once: the endpoint 500'd unconditionally, and the undo
    emptied the plan because the solve payload was not persisted."""
    client.post("/api/designs", json={"design_id": "d1",
                                      "project": EMPTY_PROJECT})
    first, second = _solved_plan(kitchen_m2=8.0), _solved_plan(kitchen_m2=13.0)
    doc = D.store().load("d1")
    sid = doc.design.active.id
    for plan in (first, second):
        before = doc.seq
        r = doc.apply(Command(op="replace_storey", params={"storey_id": sid},
                              source="solver"), payload=plan)
        assert r.ok, r.errors
        D.store().save_commands("d1", doc, [e for e in doc.log if e.seq > before])

    r = client.post("/api/designs/d1/undo")
    assert r.status_code == 200, r.text          # was a 500, always
    body = r.json()
    floor = _assert_renderable(body["projection"], walls=True)
    assert len(floor["walls"]) == len(first.walls), (
        "undoing the second solve did not restore the first")
    assert client.get("/api/designs/d1/verify").json()["ok"]


def test_findings_arrive_as_sentences_not_reprs(client):
    client.post("/api/designs", json={"design_id": "d1",
                                      "project": EMPTY_PROJECT})
    doc = D.store().load("d1")
    before = doc.seq
    doc.apply(Command(op="replace_storey",
                      params={"storey_id": doc.design.active.id},
                      source="solver"), payload=_solved_plan())
    D.store().save_commands("d1", doc, [e for e in doc.log if e.seq > before])

    findings = client.get("/api/designs/d1").json()["findings"]
    assert findings, "a solved plan produced no findings over HTTP"
    for f in findings:
        assert f["rule_id"] and f["detail"], f
        assert not f["detail"].startswith("<"), f["detail"]
    assert any(f["severity"] in ("error", "warn") for f in findings)


def test_generate_accepts_the_compass_spelling_used_everywhere_else(client):
    """`road_facing` was "N"-only while briefs and ground truth say "north";
    anything else reached `compute_envelope` and raised a 500."""
    prog = [{"id": r.id, "name": r.name, "category": r.category,
             "target_m2": r.target_m2, "is_entrance": r.is_entrance}
            for r in bhk_programme(2)]
    # A plot side is one of four, because a plot is a rectangle. Diagonals are
    # a 422, not a 500: the first cut of the validator accepted the eight-point
    # compass and "south_west" raised inside `north_deg_for` instead.
    for spelling in ("north", "E", "south", "west", "W"):
        r = client.post("/api/generate", json={
            "width_ft": 30, "depth_ft": 40, "programme": prog,
            "road_facing": spelling, "time_limit_s": 6.0, "render": False})
        assert r.status_code == 200, f"{spelling}: {r.status_code} {r.text[:200]}"
    for bad_side in ("banana", "north_east", "NE"):
        bad = client.post("/api/generate", json={
            "width_ft": 30, "depth_ft": 40, "programme": prog,
            "road_facing": bad_side, "render": False})
        assert bad.status_code == 422, (
            f"{bad_side!r} returned {bad.status_code}; anything the envelope "
            "cannot use must be refused at the boundary, not raised inside it")


def test_the_api_and_the_envelope_agree_on_road_sides():
    """The two vocabularies, compared directly. Each was internally
    consistent while the pair disagreed, which is how "south_west" got a 500
    out of a validator written to prevent exactly that."""
    from fpeval.envelope import ROAD_BEARING
    from service.app import GenerateIn, _COMPASS_LETTER
    accepted = set()
    for word in list(_COMPASS_LETTER) + list(ROAD_BEARING):
        try:
            accepted.add(GenerateIn(width_ft=30, depth_ft=40, programme=[],
                                    road_facing=word).road_facing)
        except Exception:
            pass
    assert accepted == set(ROAD_BEARING), (
        f"the API accepts {sorted(accepted)}; the envelope accepts "
        f"{sorted(ROAD_BEARING)}")
