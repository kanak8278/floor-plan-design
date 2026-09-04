"""Does a design actually survive a restart?

That is the whole point of this layer, and it is not provable by inspection.
Every test here closes the store and reopens it from the same file, because
the failure mode being guarded against -- a document that looks fine in the
process that created it and is wrong or absent in the next one -- is invisible
otherwise.

The strongest test is `test_a_reopened_log_still_verifies`: a document is only
really persisted if replaying its stored log reproduces its stored hash.
"""
from __future__ import annotations
import json
import os
import sys
import tempfile

import pytest

# `tests/conftest.py` puts `src` on the path and pins the cwd to the
# repo root, so nothing here needs to.
ROOT = os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))

from fpeval.commands import Command                            # noqa: E402
from fpeval.document import Document, state_hash                # noqa: E402
from fpeval.ir import Plan, Wall, P, Site                       # noqa: E402
from service.store import (                                     # noqa: E402
    SqliteStore, MemoryStore, open_store, DesignRow,
)


@pytest.fixture
def db_path():
    with tempfile.TemporaryDirectory() as d:
        yield os.path.join(d, "designs.db")


def box_doc(w: int = 6000, h: int = 4000, name: str = "Test") -> Document:
    plan = Plan(id="g", level=0, name="Ground Floor", site=Site(north_deg=0.0),
                walls=[Wall("w0", P(0, 0), P(w, 0), 230),
                       Wall("w1", P(w, 0), P(w, h), 230),
                       Wall("w2", P(w, h), P(0, h), 230),
                       Wall("w3", P(0, h), P(0, 0), 230)])
    doc = Document.from_plan(plan, name=name)
    doc.apply(Command(op="update_wall",
                      params={"wall_id": "w0", "thickness_mm": 230}))
    return doc


def edited_doc() -> Document:
    doc = box_doc()
    rid = doc.design.active.rooms[0].id
    for cmd in [
        Command(op="update_room", params={"room_id": rid,
                                          "name": "Master Bedroom",
                                          "category": "master_bedroom"}),
        Command(op="add_wall", params={"wall_id": "w4",
                                       "start": {"x": 3000, "y": 0},
                                       "end": {"x": 3000, "y": 4000}}),
        Command(op="add_door", source="agent",
                params={"opening_id": "o0", "wall_id": "w4", "at": "centre"},
                description="Connect the two rooms"),
    ]:
        assert doc.apply(cmd).ok
    return doc


# --------------------------------------------------------------------------
# survival
# --------------------------------------------------------------------------

def test_a_design_survives_a_restart(db_path):
    doc = edited_doc()
    store = SqliteStore(db_path)
    store.create("d1", doc)
    store.close()

    reopened = SqliteStore(db_path)
    back = reopened.load("d1")
    assert back is not None
    assert back.seq == doc.seq
    assert back.hash == doc.hash
    assert state_hash(back.design) == state_hash(doc.design)
    assert [e.op for e in back.events] == [e.op for e in doc.events]
    assert [e.summary for e in back.events] == [e.summary for e in doc.events]
    reopened.close()


def test_a_reopened_log_still_verifies(db_path):
    """The real test of persistence: replaying the *stored* log has to
    reproduce the *stored* hash. Anything less means the two halves were
    written by different code paths and only happen to agree."""
    store = SqliteStore(db_path)
    store.create("d1", edited_doc())
    store.close()

    back = SqliteStore(db_path).load("d1")
    assert back is not None
    assert back.verify_log() == []


def test_room_identity_survives_a_restart(db_path):
    """Names are the chat's vocabulary. A reload that loses them loses the
    conversation's referents."""
    store = SqliteStore(db_path)
    store.create("d1", edited_doc())
    store.close()

    back = SqliteStore(db_path).load("d1")
    names = sorted(r.name for r in back.design.active.rooms)
    assert "Master Bedroom" in names
    assert all(r.anchor is not None for r in back.design.active.rooms)


def test_commands_appended_after_a_reopen_continue_the_log(db_path):
    store = SqliteStore(db_path)
    doc = box_doc()
    store.create("d1", doc)
    store.close()

    store2 = SqliteStore(db_path)
    doc2 = store2.load("d1")
    before = doc2.seq
    res = doc2.apply(Command(op="add_column",
                             params={"column_id": "c0",
                                     "position": {"x": 500, "y": 500}}))
    assert res.ok
    store2.save_commands("d1", doc2, [e for e in doc2.log if e.seq > before])
    store2.close()

    doc3 = SqliteStore(db_path).load("d1")
    assert doc3.seq == before + 1
    assert doc3.verify_log() == []
    assert [c.id for c in doc3.design.active.columns] == ["c0"]


def test_saving_only_the_new_entries_is_enough(db_path):
    """The service persists just the tail of each batch, so that a long session
    is not quadratic. That has to leave the same result as saving everything."""
    store = SqliteStore(db_path)
    doc = box_doc()
    store.create("d1", doc)
    for i in range(12):
        before = doc.seq
        doc.apply(Command(op="add_column", params={
            "column_id": f"c{i}", "position": {"x": 400 * i, "y": 500}}))
        store.save_commands("d1", doc, [e for e in doc.log if e.seq > before])
    store.close()

    back = SqliteStore(db_path).load("d1")
    assert back.seq == doc.seq
    assert back.hash == doc.hash
    assert back.verify_log() == []
    assert len(back.design.active.columns) == 12


def test_undo_does_not_come_back_after_a_reopen(db_path):
    """Undo rebuilds rather than inverting, so storage has to be rewound. If
    it is not, a reload resurrects the command the user just undid."""
    store = SqliteStore(db_path)
    doc = edited_doc()
    store.create("d1", doc)
    doc.undo()
    store.truncate_after("d1", doc.seq, doc)
    store.close()

    back = SqliteStore(db_path).load("d1")
    assert back.seq == doc.seq
    assert back.hash == doc.hash
    assert back.design.active.opening("o0") is None
    assert back.verify_log() == []


def test_snapshots_are_written_through(db_path):
    from fpeval import document as D
    original = D.SNAPSHOT_EVERY
    D.SNAPSHOT_EVERY = 3
    try:
        store = SqliteStore(db_path)
        doc = box_doc()
        store.create("d1", doc)
        for i in range(9):
            before = doc.seq
            doc.apply(Command(op="add_column", params={
                "column_id": f"c{i}", "position": {"x": 400, "y": 400 * i}}))
            store.save_commands("d1", doc, [e for e in doc.log if e.seq > before])
        store.close()

        back = SqliteStore(db_path).load("d1")
        assert back.snapshots, "no snapshot was persisted"
        # And replay from a snapshot still lands on the right state.
        assert state_hash(back.at_seq(back.seq)) == back.hash
        assert back.verify_log() == []
    finally:
        D.SNAPSHOT_EVERY = original


# --------------------------------------------------------------------------
# transcripts
# --------------------------------------------------------------------------

def test_a_transcript_survives_with_its_block_structure(db_path):
    """Thinking blocks and tool results have to come back in a form the API
    accepts as `messages` input. Flattening a transcript to plain text would
    make the next turn lose the reasoning it was continuing."""
    transcript = [
        {"role": "user", "content": "widen the bedroom"},
        {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "the bedroom is 3.0 m wide"},
            {"type": "text", "text": "Widening it."},
            {"type": "tool_use", "id": "tu_1", "name": "apply_commands",
             "input": {"commands": [{"op": "set_room_area", "description": "x",
                                     "params": {"room_id": "r0"}}]}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu_1",
             "content": "APPLIED: seq 4"},
        ]},
    ]
    store = SqliteStore(db_path)
    store.create("d1", box_doc())
    store.save_transcript("d1", transcript)
    store.close()

    back = SqliteStore(db_path).load_transcript("d1")
    assert back == transcript


def test_sdk_content_objects_are_serialised(db_path):
    """Anthropic content blocks are SDK objects, not dicts. Storing them raw
    would throw; storing `str(block)` would come back unusable."""
    class FakeBlock:
        def model_dump(self):
            return {"type": "text", "text": "hello"}

    store = SqliteStore(db_path)
    store.create("d1", box_doc())
    store.save_transcript("d1", [{"role": "assistant",
                                  "content": [FakeBlock()]}])
    store.close()
    back = SqliteStore(db_path).load_transcript("d1")
    assert back == [{"role": "assistant",
                     "content": [{"type": "text", "text": "hello"}]}]


def test_replacing_a_design_drops_its_conversation(db_path):
    store = SqliteStore(db_path)
    store.create("d1", edited_doc())
    store.save_transcript("d1", [{"role": "user", "content": "hello"}])
    store.replace("d1", box_doc())
    assert store.load_transcript("d1") == []
    assert store.load("d1").seq == 1
    store.close()


# --------------------------------------------------------------------------
# housekeeping
# --------------------------------------------------------------------------

def test_listing_reports_what_is_stored(db_path):
    store = SqliteStore(db_path)
    store.create("a", box_doc(name="Alpha"))
    store.create("b", edited_doc())
    store.save_transcript("b", [{"role": "user", "content": "hi"}])
    rows = {r.design_id: r for r in store.list()}
    assert set(rows) == {"a", "b"}
    assert rows["a"].name == "Alpha"
    assert rows["b"].messages == 1
    assert rows["b"].seq == 4
    store.close()


def test_delete_removes_everything(db_path):
    store = SqliteStore(db_path)
    store.create("d1", edited_doc())
    store.save_transcript("d1", [{"role": "user", "content": "hi"}])
    store.delete("d1")
    assert store.load("d1") is None
    assert store.load_transcript("d1") == []
    assert store.list() == []
    store.close()


def test_missing_design_is_none_not_an_error(db_path):
    store = SqliteStore(db_path)
    assert store.load("nope") is None
    assert store.load_transcript("nope") == []
    store.close()


def test_reopening_an_existing_file_does_not_wipe_it(db_path):
    """`CREATE TABLE IF NOT EXISTS` on every open is only safe if it really is
    idempotent -- a schema script that dropped and recreated would erase a
    deployment on restart."""
    store = SqliteStore(db_path)
    store.create("d1", edited_doc())
    store.close()
    for _ in range(3):
        again = SqliteStore(db_path)
        assert again.load("d1") is not None
        again.close()


# --------------------------------------------------------------------------
# store selection
# --------------------------------------------------------------------------

def test_open_store_honours_memory():
    assert isinstance(open_store(":memory:"), MemoryStore)


def test_open_store_refuses_postgres_rather_than_falling_back():
    """A deployment that thinks it has Postgres and actually has a file on an
    ephemeral container disk loses data quietly. Refuse instead."""
    with pytest.raises(NotImplementedError):
        open_store("postgres://user@host/db")


def test_open_store_creates_the_parent_directory(db_path):
    nested = os.path.join(os.path.dirname(db_path), "a", "b", "designs.db")
    store = open_store(nested)
    store.create("d1", box_doc())
    store.close()
    assert os.path.exists(nested)


def test_memory_store_satisfies_the_same_contract():
    store = MemoryStore()
    doc = edited_doc()
    store.create("d1", doc)
    assert store.load("d1") is doc
    store.save_transcript("d1", [{"role": "user", "content": "hi"}])
    assert store.list()[0].messages == 1
    store.delete("d1")
    assert store.load("d1") is None


# --------------------------------------------------------------------------
# the service uses it
# --------------------------------------------------------------------------

def test_the_service_reads_and_writes_through_the_store(db_path):
    """End to end through the endpoint functions, with a real file, because
    the wiring is where a persistence layer usually goes wrong."""
    from service import documents as D
    from service.documents import AdoptIn, CommandsIn, CommandIn

    store = SqliteStore(db_path)
    D.set_store(store)
    try:
        proj = box_doc().projection()
        adopted = D.adopt(AdoptIn(project=proj, design_id="svc1"))
        assert adopted["design_id"] == "svc1"
        rid = adopted["rooms"][0]["id"]

        reply = D.post_commands("svc1", CommandsIn(commands=[
            CommandIn(op="update_room",
                      params={"room_id": rid, "name": "Hall"}),
        ]))
        assert [e["summary"] for e in reply["events"]] == \
            ["Unnamed room renamed to Hall, 6.00 x 4.00 m"]

        # A fresh store over the same file is what a restart looks like.
        store.close()
        D.set_store(SqliteStore(db_path))
        again = D.get_design("svc1")
        assert again["seq"] == reply["seq"]
        assert again["hash"] == reply["hash"]
        assert [e["summary"] for e in again["events"]] == \
            [e["summary"] for e in reply["events"]] or again["events"]
        assert D.verify("svc1")["ok"]
    finally:
        D.set_store(None)


def test_reattaching_after_a_restart_keeps_the_log(db_path):
    """The bug this guards: `adopt` used to replace a document the service was
    already holding, so the assistant remembered edits the document had lost.

    Note what adoption does and does not carry. A client Project is a *state
    snapshot*, not a log, so adopting one starts at seq 0 -- there is nothing
    in it to recover a history from. The log survives a restart because it is
    in the store under the same `design_id`, which is exactly why re-attach
    must not overwrite it with the client's copy.
    """
    from service import documents as D
    from service.documents import AdoptIn, CommandsIn, CommandIn

    store = SqliteStore(db_path)
    D.set_store(store)
    try:
        proj = box_doc().projection()
        adopted = D.adopt(AdoptIn(project=proj, design_id="svc2"))
        assert adopted["seq"] == 0          # a snapshot carries no history
        rid = adopted["rooms"][0]["id"]

        D.post_commands("svc2", CommandsIn(commands=[
            CommandIn(op="update_room",
                      params={"room_id": rid, "name": "Master Bedroom"}),
            CommandIn(op="add_wall", params={
                "wall_id": "w4", "start": {"x": 3000, "y": 0},
                "end": {"x": 3000, "y": 4000}}),
        ]))
        seq = D.get_design("svc2")["seq"]
        assert seq == 2

        store.close()
        D.set_store(SqliteStore(db_path))
        # On reload the client sends its own copy, which is behind by two
        # commands. The service must keep its own.
        again = D.adopt(AdoptIn(project=proj, design_id="svc2"))
        assert again["reattached"] is True
        assert again["seq"] == seq
        assert len(again["events"]) == seq
        assert again["projection"] is not None
        assert any(r["name"] == "Master Bedroom" for r in again["rooms"])
        assert D.verify("svc2")["ok"]

        # And an explicit replace really does discard it.
        replaced = D.adopt(AdoptIn(project=proj, design_id="svc2", replace=True))
        assert replaced["seq"] == 0
        assert D.transcript("svc2")["messages"] == []
    finally:
        D.set_store(None)


# ------------------------------------------------- output-only block fields
# `model_dump()` emits every field the SDK object carries, and some are
# output-only. A text block comes back with `parsed_output`, a tool_use block
# with `caller` and `toolset_name`. Storing them is harmless; replaying them is
#
#     messages.2.content.0.text.parsed_output: Extra inputs are not permitted
#
# a hard 400 that only bites on the SECOND turn, because turn one hands the SDK
# its own objects and turn two replays what was persisted. Found by driving the
# real UI, not by any test: `MemoryStore` used to keep the SDK objects
# untouched, so it was the one store that never exercised the serialiser.

def test_output_only_block_fields_are_not_replayed(db_path):
    class ParsedTextBlock:
        def model_dump(self):
            return {"type": "text", "text": "done", "citations": None,
                    "parsed_output": {"rooms": 7}}

    class ToolUseWithCaller:
        def model_dump(self):
            return {"type": "tool_use", "id": "tu_1", "name": "solve_layout",
                    "input": {}, "caller": None, "toolset_name": None}

    store = SqliteStore(db_path)
    store.create("d1", box_doc())
    store.save_transcript("d1", [{"role": "assistant", "content": [
        ParsedTextBlock(), ToolUseWithCaller(),
        {"type": "thinking", "thinking": "...", "signature": "sig"}]}])
    store.close()

    blocks = SqliteStore(db_path).load_transcript("d1")[0]["content"]
    assert blocks[0] == {"type": "text", "text": "done"}
    assert blocks[1] == {"type": "tool_use", "id": "tu_1",
                         "name": "solve_layout", "input": {}}
    # A thinking block's signature is NOT output-only: drop it and the next
    # turn on the same model is rejected for the opposite reason.
    assert blocks[2] == {"type": "thinking", "thinking": "...",
                         "signature": "sig"}


def test_an_unknown_block_type_is_passed_through(db_path):
    """A server-tool block we do not model is better sent as-is than emptied."""
    store = SqliteStore(db_path)
    store.create("d1", box_doc())
    store.save_transcript("d1", [{"role": "assistant", "content": [
        {"type": "web_search_tool_result", "tool_use_id": "s1",
         "content": [{"type": "web_search_result", "url": "http://x"}]}]}])
    store.close()
    back = SqliteStore(db_path).load_transcript("d1")[0]["content"][0]
    assert back["type"] == "web_search_tool_result"
    assert back["content"] == [{"type": "web_search_result", "url": "http://x"}]


def test_both_stores_sanitise_identically(db_path):
    """`MemoryStore` is what tests run against, so it must not be the lenient
    one. It held the SDK's objects untouched, which is why this class of bug
    reached the deployed path unseen."""
    class Blk:
        def model_dump(self):
            return {"type": "text", "text": "hi", "parsed_output": {"a": 1}}

    msgs = [{"role": "assistant", "content": [Blk()]}]
    sq = SqliteStore(db_path); sq.create("d1", box_doc())
    sq.save_transcript("d1", msgs)
    mem = MemoryStore(); mem.create("d1", box_doc())
    mem.save_transcript("d1", msgs)
    assert mem.load_transcript("d1") == sq.load_transcript("d1")
    sq.close()


# ------------------------------------------------------ solve payload on disk
# `replace_storey` carries a solved `Plan` as its payload because CP-SAT cannot
# be re-solved identically during replay. The payload was deliberately not
# persisted, on the argument that the cost was only "undo across a process
# restart". That understated it: the service loads the document from the store
# on EVERY request, so the payload was never available after the request that
# created it. Undoing the second of two solves emptied the plan -- 13 walls to
# 0 -- and `verify_log` reported thirteen problems that were all one lost floor.

def test_a_solve_payload_survives_a_restart(db_path):
    from fpeval.envelope import bhk_programme, CityProfileAdapter
    from fpeval.solver import LayoutSpec, solve_layout
    from fpeval.bylaws import BENGALURU

    prog = bhk_programme(2)
    res = solve_layout(
        30, 40,
        LayoutSpec(programme=prog,
                   entrance_room=next(p.id for p in prog if p.is_entrance),
                   time_limit_s=6.0, deterministic=True),
        road_facing="E", profile=CityProfileAdapter(BENGALURU), plan_id="p1")
    assert res.plan is not None

    doc = box_doc()
    store = SqliteStore(db_path)
    store.create("d1", doc)
    before = doc.seq
    sid = doc.design.active.id
    r = doc.apply(Command(op="replace_storey", params={"storey_id": sid},
                          source="solver"), payload=res.plan)
    assert r.ok, r.errors          # assert the apply, not just the storage
    store.save_commands("d1", doc, [e for e in doc.log if e.seq > before])
    store.close()

    back = SqliteStore(db_path).load("d1")
    entry = back.log[-1]
    assert entry.command.op == "replace_storey"
    assert entry.payload is not None, "the solved Plan was not persisted"
    assert len(entry.payload.walls) == len(res.plan.walls)
    # The point of persisting it: replay reproduces the document.
    assert back.verify_log() == []


def test_undoing_the_second_solve_keeps_the_first(db_path):
    """The failure this whole column exists for."""
    from fpeval.envelope import bhk_programme, CityProfileAdapter
    from fpeval.solver import LayoutSpec, solve_layout
    from fpeval.bylaws import BENGALURU

    def solve(kitchen_m2: float):
        prog = bhk_programme(2)
        for r in prog:
            if r.category == "kitchen":
                r.target_m2 = kitchen_m2
        return solve_layout(
            30, 40,
            LayoutSpec(programme=prog,
                       entrance_room=next(p.id for p in prog if p.is_entrance),
                       time_limit_s=6.0, deterministic=True),
            road_facing="E", profile=CityProfileAdapter(BENGALURU),
            plan_id="p").plan

    first, second = solve(8.0), solve(13.0)
    assert first is not None and second is not None

    doc = box_doc()
    store = SqliteStore(db_path)
    store.create("d1", doc)
    sid = doc.design.active.id
    for plan in (first, second):
        before = doc.seq
        r = doc.apply(Command(op="replace_storey", params={"storey_id": sid},
                              source="solver"), payload=plan)
        assert r.ok, r.errors
        store.save_commands("d1", doc, [e for e in doc.log if e.seq > before])
    store.close()

    # A new process, exactly as every request is.
    reopened = SqliteStore(db_path)
    doc2 = reopened.load("d1")
    assert len(doc2.design.active.walls) == len(second.walls)
    doc2.undo()
    walls = len(doc2.design.active.walls)
    assert walls == len(first.walls), (
        f"undoing the second solve left {walls} walls; the first solve's "
        f"{len(first.walls)} should have come back")
    reopened.close()
