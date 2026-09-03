"""The command bus: one algebra, one applier, one log.

Three properties matter more than any individual handler, and each has a test
whose failure means the whole mechanism is unsound rather than one edit being
wrong:

  `test_every_command_has_a_handler` -- a vocabulary entry with no applier is
  the exact shape of bug that only surfaces when a model emits the command in
  front of a user.

  `test_replay_reproduces_the_document` -- if any command reads a clock, mints
  an id, or depends on iteration order, undo and time travel are quietly broken
  everywhere. Replaying to the same hash is the only way to know.

  `test_symbolic_commands_reject_coordinates` -- DECISIONS.md #6 says the model
  emits intent and solvers emit geometry. Now that manual edits share the log,
  that has to be a property of the command type, not a convention.
"""
from __future__ import annotations
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from fpeval.commands import (                                  # noqa: E402
    Command, TABLE, SYMBOLIC, DIRECT, SYMBOLIC_OPS, DIRECT_OPS, SPEC_OPS,
    catalogue, table_manifest, render_summary, refs_of, COORDINATE_KEYS,
)
from fpeval.apply import apply_command, apply_all, unimplemented, resolve_ref
from fpeval.document import Document, state_hash, feed                # noqa: E402
from fpeval.ir import Design, Plan, Wall, Room, P, Site                # noqa: E402


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

def box_doc(w: int = 6000, h: int = 4000) -> Document:
    """A closed rectangle: four walls, one room once faces are derived."""
    plan = Plan(
        id="g", level=0, name="Ground Floor", site=Site(north_deg=0.0),
        walls=[
            Wall("w0", P(0, 0), P(w, 0), 230),
            Wall("w1", P(w, 0), P(w, h), 230),
            Wall("w2", P(w, h), P(0, h), 230),
            Wall("w3", P(0, h), P(0, 0), 230),
        ],
    )
    doc = Document.from_plan(plan, name="Test")
    # Derive the initial room set the same way any wall edit would.
    doc.apply(Command(op="update_wall", params={"wall_id": "w0",
                                                "thickness_mm": 230}))
    return doc


def divided_doc() -> Document:
    doc = box_doc()
    doc.apply(Command(op="add_wall", params={
        "wall_id": "w4", "start": {"x": 3000, "y": 0}, "end": {"x": 3000, "y": 4000},
    }))
    return doc


# --------------------------------------------------------------------------
# the three properties
# --------------------------------------------------------------------------

def test_every_command_has_a_handler():
    assert unimplemented() == [], (
        "these commands are in the vocabulary with no applier: "
        f"{unimplemented()}")


def test_replay_reproduces_the_document():
    doc = box_doc()
    cmds = [
        Command(op="add_wall", params={"wall_id": "w4",
                                       "start": {"x": 3000, "y": 0},
                                       "end": {"x": 3000, "y": 4000}}),
        Command(op="add_door", params={"opening_id": "o0", "wall_id": "w4",
                                       "at": "centre"}),
        Command(op="add_window", params={"opening_id": "o1", "wall_id": "w1",
                                         "at": 0.5, "window_type": "bay"}),
        Command(op="add_furniture", params={"furniture_id": "f0",
                                            "catalog_id": "sofa",
                                            "position": {"x": 1500, "y": 2000}}),
        Command(op="split_wall", params={"wall_id": "w0", "at": "midpoint",
                                         "new_wall_id": "w0b"}),
        Command(op="add_text", params={"text_id": "t0",
                                       "position": {"x": 3000, "y": 200},
                                       "text": "GROUND FLOOR"}),
    ]
    for c in cmds:
        res = doc.apply(c)
        assert res.ok, (c.op, res.errors)
    assert doc.verify_log() == []


def test_symbolic_commands_reject_coordinates():
    """An agent naming a coordinate must be refused, by type not by habit."""
    doc = box_doc()
    bad = Command(op="update_wall", source="agent",
                  params={"wall_id": "w0", "position": {"x": 10, "y": 10}})
    res = doc.apply(bad)
    assert not res.ok
    assert any("coordinate" in e for e in res.errors), res.errors


def test_agents_cannot_author_direct_commands():
    doc = box_doc()
    res = doc.apply(Command(op="move_wall_endpoint", source="agent", params={
        "wall_id": "w0", "endpoint": "start", "position": {"x": 5, "y": 5}}))
    assert not res.ok
    assert any("may not author" in e for e in res.errors), res.errors


def test_users_can_author_the_same_edit_directly():
    doc = box_doc()
    res = doc.apply(Command(op="move_wall_endpoint", source="user", params={
        "wall_id": "w0", "endpoint": "start", "position": {"x": 100, "y": 0}}))
    assert res.ok, res.errors
    assert doc.design.active.wall("w0").start == P(100, 0)


@pytest.mark.parametrize("op", SYMBOLIC_OPS)
def test_no_symbolic_command_declares_a_coordinate_param(op):
    """The guard above only works if the vocabulary itself is clean."""
    spec = TABLE[op]
    named = (set(spec.required) | set(spec.optional)) & COORDINATE_KEYS
    # `at`, `distance_mm`, and refs are parametric, not absolute; they are not
    # in COORDINATE_KEYS. Anything that is, is a mistake in the table.
    assert not named, f"{op} declares coordinate params {sorted(named)}"


# --------------------------------------------------------------------------
# rooms follow walls, and keep their names
# --------------------------------------------------------------------------

def test_a_closed_box_yields_one_room():
    doc = box_doc()
    assert len(doc.design.active.rooms) == 1
    assert doc.design.active.rooms[0].area == pytest.approx(24_000_000, rel=1e-6)


def test_naming_then_splitting_a_wall_keeps_the_name():
    """The end-to-end version of the identity fix, through the command bus."""
    doc = box_doc()
    rid = doc.design.active.rooms[0].id
    assert doc.apply(Command(op="update_room", params={
        "room_id": rid, "name": "Master Bedroom",
        "category": "master_bedroom"})).ok

    res = doc.apply(Command(op="split_wall", params={
        "wall_id": "w0", "at": "midpoint", "new_wall_id": "w0b"}))
    assert res.ok, res.errors
    rooms = doc.design.active.rooms
    assert len(rooms) == 1
    assert rooms[0].name == "Master Bedroom"
    assert rooms[0].id == rid
    assert rooms[0].category == "master_bedroom"
    assert res.rooms_gone == []


def test_dividing_a_room_reports_the_new_one():
    doc = box_doc()
    rid = doc.design.active.rooms[0].id
    doc.apply(Command(op="update_room", params={"room_id": rid, "name": "Hall"}))
    res = doc.apply(Command(op="add_wall", params={
        "wall_id": "w4", "start": {"x": 3000, "y": 0},
        "end": {"x": 3000, "y": 4000}}))
    assert res.ok, res.errors
    rooms = doc.design.active.rooms
    assert len(rooms) == 2
    assert [r.name for r in rooms].count("Hall") == 1
    assert len(res.rooms_new) == 1


def test_merging_rooms_names_what_was_lost():
    """A deliberate merge must be reported, so the chat can say so."""
    doc = divided_doc()
    rooms = doc.design.active.rooms
    assert len(rooms) == 2
    for r, name in zip(rooms, ("Bedroom 1", "Bedroom 2")):
        doc.apply(Command(op="update_room", params={"room_id": r.id, "name": name}))
    res = doc.apply(Command(op="remove_element",
                            params={"element_id": "w4"}))
    assert res.ok, res.errors
    assert len(doc.design.active.rooms) == 1
    assert res.rooms_gone and res.rooms_gone[0] in ("Bedroom 1", "Bedroom 2")


# --------------------------------------------------------------------------
# handlers whose failure would be silent
# --------------------------------------------------------------------------

def test_splitting_a_wall_rehosts_its_openings():
    """A door two thirds along a split wall must end up on the far half, at
    the right place on it. Dropping it, or leaving it on a wall that no longer
    reaches it, moves a door without telling anyone."""
    doc = box_doc()
    doc.apply(Command(op="add_door", params={
        "opening_id": "o0", "wall_id": "w0", "at": 0.75}))
    doc.apply(Command(op="add_door", params={
        "opening_id": "o1", "wall_id": "w0", "at": 0.25}))
    assert doc.apply(Command(op="split_wall", params={
        "wall_id": "w0", "at": 0.5, "new_wall_id": "w0b"})).ok

    st = doc.design.active
    near = st.opening("o1")
    far = st.opening("o0")
    assert near.wall_id == "w0" and near.position == pytest.approx(0.5)
    assert far.wall_id == "w0b" and far.position == pytest.approx(0.5)
    # And they are still at the same absolute place on the plan.
    for oid, x in (("o1", 1500), ("o0", 4500)):
        o = st.opening(oid)
        w = st.wall(o.wall_id)
        assert w.start.x + o.position * (w.end.x - w.start.x) == pytest.approx(x)


def test_deleting_a_wall_cascades_its_openings():
    doc = box_doc()
    doc.apply(Command(op="add_door", params={"opening_id": "o0",
                                             "wall_id": "w0", "at": 0.5}))
    assert doc.design.active.opening("o0") is not None
    doc.apply(Command(op="remove_element", params={"element_id": "w0"}))
    assert doc.design.active.opening("o0") is None


def test_move_wall_parallel_resolves_against_north():
    """The agent names a direction; the applier turns it into a displacement.

    `Site.north_deg` is the bearing of the **+Y axis**, clockwise from north.
    So `north_deg = 0` puts north at +Y, and `north_deg = 90` points +Y east,
    which puts +X at bearing 180 (south) and therefore north at -X. Getting
    that backwards would move every agent-requested wall the wrong way, which
    is exactly why the model is not allowed to compute it.
    """
    doc = box_doc()
    doc.apply(Command(op="set_site", params={"north_deg": 0.0}))
    doc.apply(Command(op="move_wall_parallel", source="agent", params={
        "wall_id": "w0", "direction": "north", "distance_mm": 500}))
    north_up = doc.design.active.wall("w0").start

    doc2 = box_doc()
    doc2.apply(Command(op="set_site", params={"north_deg": 90.0}))
    doc2.apply(Command(op="move_wall_parallel", source="agent", params={
        "wall_id": "w0", "direction": "north", "distance_mm": 500}))
    north_right = doc2.design.active.wall("w0").start

    assert north_up != north_right
    assert north_up == P(0, 500)            # +Y is north
    assert north_right == P(-500, 0)        # +Y is east, so north is -X


def test_symbolic_wall_between_refs_needs_no_coordinate():
    doc = box_doc()
    res = doc.apply(Command(op="add_wall_between", source="agent", params={
        "wall_id": "w4", "start_ref": "w0@0.5", "end_ref": "w2@0.5"}))
    assert res.ok, res.errors
    w = doc.design.active.wall("w4")
    assert w.start == P(3000, 0) and w.end == P(3000, 4000)
    assert len(doc.design.active.rooms) == 2


@pytest.mark.parametrize("ref,expected", [
    ("w0:start", P(0, 0)),
    ("w0:end", P(6000, 0)),
    ("w0@0.25", P(1500, 0)),
    ("w0@centre", P(3000, 0)),
])
def test_resolve_ref(ref, expected):
    doc = box_doc()
    assert resolve_ref(doc.design.active, ref) == expected


def test_resolve_ref_rejects_nonsense():
    doc = box_doc()
    from fpeval.apply import ApplyError
    for bad in ("w0", "nope:start", "w0:middle", "w0@2.0", 42):
        with pytest.raises(ApplyError):
            resolve_ref(doc.design.active, bad)


def test_window_head_must_be_above_its_sill():
    doc = box_doc()
    res = doc.apply(Command(op="add_window", params={
        "opening_id": "o0", "wall_id": "w0", "at": 0.5,
        "sill_mm": 2100, "head_mm": 900}))
    assert not res.ok
    assert any("above sill" in e for e in res.errors), res.errors


def test_copying_a_storey_does_not_copy_its_dimension_strings():
    """Sheet furniture belongs to one drawing. Copying a floor's dimension
    strings onto the next one just makes them wrong."""
    doc = box_doc()
    doc.apply(Command(op="add_dimension", params={
        "dimension_id": "a0", "start": {"x": 0, "y": 0},
        "end": {"x": 6000, "y": 0}}))
    assert doc.apply(Command(op="add_storey", params={
        "storey_id": "f1", "name": "First Floor", "copy_from": "g"})).ok
    first = doc.design.storey("f1")
    assert len(first.walls) == 4
    assert first.presentation.dimensions == []


def test_a_design_keeps_at_least_one_storey():
    doc = box_doc()
    res = doc.apply(Command(op="remove_storey", params={"storey_id": "g"}))
    assert not res.ok
    assert any("at least one storey" in e for e in res.errors)


# --------------------------------------------------------------------------
# rejection is informative, and partial
# --------------------------------------------------------------------------

def test_a_batch_lands_what_it_can_and_explains_the_rest():
    """The conflict story: an agent patch computed before the user deleted a
    wall should apply the parts that still make sense."""
    doc = box_doc()
    cmds = [
        Command(op="add_door", source="agent",
                params={"opening_id": "o0", "wall_id": "w0", "at": "centre"},
                description="Add a door to the front wall"),
        Command(op="update_wall", source="agent",
                params={"wall_id": "w9", "thickness_mm": 230},
                description="Thicken a wall that no longer exists"),
        Command(op="update_room", source="agent",
                params={"room_id": doc.design.active.rooms[0].id,
                        "name": "Living"},
                description="Name the room"),
    ]
    events, rejected = doc.apply_batch(cmds)
    assert len(events) == 2
    assert len(rejected) == 1
    assert rejected[0].command.params["wall_id"] == "w9"
    assert "no wall" in rejected[0].errors[0]
    assert doc.design.active.rooms[0].name == "Living"


def test_unknown_command_is_named_not_swallowed():
    doc = box_doc()
    res = doc.apply(Command(op="teleport_wall", params={}))
    assert not res.ok
    assert "unknown command" in res.errors[0]


def test_unexpected_param_is_rejected():
    doc = box_doc()
    res = doc.apply(Command(op="update_wall",
                            params={"wall_id": "w0", "thickness": 230}))
    assert not res.ok
    assert any("unexpected param" in e for e in res.errors)


def test_a_failed_command_leaves_the_document_untouched():
    doc = box_doc()
    before_hash, before_seq = doc.hash, doc.seq
    assert not doc.apply(Command(op="update_wall",
                                 params={"wall_id": "nope",
                                         "thickness_mm": 230})).ok
    assert doc.hash == before_hash
    assert doc.seq == before_seq


def test_applying_does_not_mutate_the_input_design():
    """An applier that edits in place makes replay a matter of caller
    discipline rather than a property of the code."""
    doc = box_doc()
    snapshot = doc.design.to_dict()
    apply_command(doc.design, Command(op="add_wall", params={
        "wall_id": "wX", "start": {"x": 0, "y": 0}, "end": {"x": 10, "y": 10}}))
    assert doc.design.to_dict() == snapshot


# --------------------------------------------------------------------------
# history
# --------------------------------------------------------------------------

def test_undo_rebuilds_rather_than_inverting():
    doc = box_doc()
    rid = doc.design.active.rooms[0].id
    doc.apply(Command(op="update_room", params={"room_id": rid, "name": "Hall"}))
    marked = doc.hash
    doc.apply(Command(op="add_wall", params={
        "wall_id": "w4", "start": {"x": 3000, "y": 0},
        "end": {"x": 3000, "y": 4000}}))
    assert len(doc.design.active.rooms) == 2

    event = doc.undo()
    assert event is not None and event.op == "add_wall"
    assert doc.hash == marked
    assert len(doc.design.active.rooms) == 1
    assert doc.design.active.rooms[0].name == "Hall"


def test_at_seq_walks_back_to_any_point():
    doc = box_doc()
    first = doc.seq                       # box_doc() already applied one command
    hashes = [doc.hash]
    for i in range(6):
        doc.apply(Command(op="add_column", params={
            "column_id": f"c{i}", "position": {"x": 500 + 400 * i, "y": 500}}))
        hashes.append(doc.hash)
    for i, want in enumerate(hashes):
        assert state_hash(doc.at_seq(first + i)) == want, i


def test_at_seq_zero_is_the_document_before_any_command():
    doc = box_doc()
    base = doc.at_seq(0)
    assert base.active.rooms == []        # faces are derived, not authored
    assert len(base.active.walls) == 4


def test_state_hash_ignores_float_noise():
    """0.1 + 0.2 on one machine must hash the same as 0.30000000000000004 on
    another, or the optimistic client resyncs forever."""
    a = box_doc().design
    b = box_doc().design
    b.storeys[0].site.north_deg = 0.1 + 0.2
    a.storeys[0].site.north_deg = 0.3
    assert state_hash(a) == state_hash(b)


def test_snapshots_do_not_change_the_answer():
    doc = box_doc()
    from fpeval import document as D
    original = D.SNAPSHOT_EVERY
    D.SNAPSHOT_EVERY = 3
    try:
        for i in range(10):
            doc.apply(Command(op="add_column", params={
                "column_id": f"c{i}", "position": {"x": 500, "y": 500 + 300 * i}}))
        assert doc.snapshots, "no snapshot was taken"
        assert doc.verify_log() == []
        assert state_hash(doc.at_seq(doc.seq)) == doc.hash
    finally:
        D.SNAPSHOT_EVERY = original


# --------------------------------------------------------------------------
# what the user and the model read
# --------------------------------------------------------------------------

def test_summaries_read_like_a_changelog():
    doc = box_doc()
    rid = doc.design.active.rooms[0].id
    doc.apply(Command(op="update_room", params={"room_id": rid,
                                                "name": "Middle Bedroom"}))
    doc.apply(Command(op="add_furniture", params={
        "furniture_id": "f0", "catalog_id": "dining_table",
        "position": {"x": 3000, "y": 2000}}))
    doc.apply(Command(op="add_door", params={
        "opening_id": "o0", "wall_id": "w0", "at": "centre",
        "door_type": "sliding"}))
    text = feed(doc.events)
    assert "renamed to Middle Bedroom" in text
    assert "6.00 x 4.00 m" in text          # dimensions come from the geometry
    assert "Dining Table added" in text     # catalogue display name, not the id
    assert "Sliding door added" in text
    assert all(line.startswith("- ") for line in text.splitlines())


def test_summary_is_generated_not_taken_from_the_author():
    """`description` is the agent's rationale; the bullet is derived from the
    command. If the two were the same field, sixty call sites would drift."""
    doc = box_doc()
    doc.apply(Command(op="update_wall", source="agent",
                      params={"wall_id": "w0", "thickness_mm": 300},
                      description="whatever the model felt like writing"))
    assert doc.events[-1].summary == "Wall w0 now 300 mm thick"


def test_events_since_is_what_the_agent_reads():
    doc = box_doc()
    mark = doc.seq
    doc.apply(Command(op="add_column", params={"column_id": "c0",
                                               "position": {"x": 500, "y": 500}}))
    doc.apply(Command(op="add_column", params={"column_id": "c1",
                                               "position": {"x": 900, "y": 500}}))
    assert [e.op for e in doc.events_since(mark)] == ["add_column", "add_column"]
    assert doc.events_since(doc.seq) == []


def test_feed_truncates_from_the_front():
    doc = box_doc()
    for i in range(30):
        doc.apply(Command(op="add_column", params={
            "column_id": f"c{i}", "position": {"x": 500, "y": 500 + 200 * i}}))
    text = feed(doc.events, limit=5)
    assert "earlier changes" in text.splitlines()[0]
    assert len(text.splitlines()) == 6


def test_event_records_what_it_touched():
    doc = box_doc()
    rid = doc.design.active.rooms[0].id
    doc.apply(Command(op="update_room", params={"room_id": rid, "name": "X"}))
    assert rid in doc.events[-1].refs


# --------------------------------------------------------------------------
# the vocabulary as a published interface
# --------------------------------------------------------------------------

def test_manifest_covers_the_table():
    m = table_manifest()
    assert set(m["commands"]) == set(TABLE)
    for op, entry in m["commands"].items():
        assert entry["family"] in (SYMBOLIC, DIRECT)
        assert isinstance(entry["required"], list)


def test_catalogue_text_is_generated_from_the_table():
    """The prompt has to be generated, or it drifts from what the applier
    accepts -- invisible until a model emits a command nobody implemented."""
    text = catalogue(SYMBOLIC)
    for op in SYMBOLIC_OPS:
        assert f"  {op} [" in text
    for op in DIRECT_OPS:
        assert f"  {op} [" not in text


def test_spec_and_geometry_ops_are_disjoint_and_complete():
    assert set(SPEC_OPS) < set(TABLE)
    assert set(SYMBOLIC_OPS) | set(DIRECT_OPS) == set(TABLE)
    assert not (set(SYMBOLIC_OPS) & set(DIRECT_OPS))


def test_command_round_trips_through_json():
    c = Command(op="add_door", params={"opening_id": "o0", "wall_id": "w0",
                                       "at": "centre"},
                source="agent", finding_ids=["NBC.DOOR_WIDTH"],
                description="Add the missing door", confidence=0.8)
    assert Command.from_dict(c.to_dict()).to_dict() == c.to_dict()


def test_document_projection_carries_seq_and_hash():
    doc = box_doc()
    proj = doc.projection()
    assert proj[".fpeval_doc"]["seq"] == doc.seq
    assert proj[".fpeval_doc"]["hash"] == doc.hash
    assert len(proj["floors"]) == 1


def test_adopting_a_project_gives_every_room_an_anchor():
    doc = box_doc()
    doc.apply(Command(op="update_room", params={
        "room_id": doc.design.active.rooms[0].id, "name": "Hall"}))
    adopted = Document.from_project(doc.projection())
    assert adopted.design.active.rooms[0].name == "Hall"
    assert all(r.anchor is not None for r in adopted.design.active.rooms)
    assert adopted.seq == 0            # a fresh log over an existing design


def test_room_class_confusion_gets_an_actionable_error():
    """Observed live: the agent wanted to set the room's *type* and reached
    for `room_class`, burning two commands on a bare enum error that did not
    say what to use instead. `category` is the 18-type taxonomy every rule
    reads; `room_class` is only OpenPlan3D's four-value floor bucket."""
    doc = box_doc()
    rid = doc.design.active.rooms[0].id
    res = doc.apply(Command(op="update_room", source="agent",
                            params={"room_id": rid, "room_class": "bedroom"}))
    assert not res.ok
    msg = " ".join(res.errors)
    assert "category='bedroom'" in msg, msg
    assert "room *type*" in msg


def test_a_real_room_class_override_still_works():
    """The param is not useless -- the editor's own room-type dropdown sets
    it, and a user marking a room as outdoor must still get through."""
    doc = box_doc()
    rid = doc.design.active.rooms[0].id
    res = doc.apply(Command(op="update_room",
                            params={"room_id": rid, "room_class": "outdoor"}))
    assert res.ok, res.errors
    assert doc.design.active.rooms[0].room_class == "outdoor"


def test_the_generated_catalogue_explains_the_difference():
    """The catalogue *is* the prompt, so this is where the model learns it."""
    text = catalogue(SYMBOLIC)
    assert "18-type taxonomy" in text
    assert "floor-rendering bucket" in text
