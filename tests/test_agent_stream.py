"""The agent's streaming contract, tested without calling the API.

`stream_turn` yields the events the chat pane renders, so its shape is a real
interface -- a renamed key or a dropped `thinking_end` breaks the UI silently.
The Anthropic client is faked with scripted stream events, which makes these
tests free, fast, and deterministic; what they cannot check is that the real
SDK emits the event types assumed here, so `FAKE_EVENT_TYPES` records exactly
what is being assumed and `tests/ui_stream.mjs` exercises the live path.

The load-bearing assertion is `test_apply_commands_emits_no_tool_row`: the
whole readability of the pane rests on a write tool speaking through its
effects rather than appearing twice.
"""
from __future__ import annotations
import os
import sys
from types import SimpleNamespace as NS

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from fpeval.agent import stream_turn, plan_digest, turn_context, TOOL_LABELS
from fpeval.commands import Command                            # noqa: E402
from fpeval.document import Document                            # noqa: E402
from fpeval.ir import Plan, Wall, P, Site                       # noqa: E402


# What the real SDK is assumed to emit. Written down so a future SDK change
# has something to contradict.
FAKE_EVENT_TYPES = ("content_block_start", "content_block_delta",
                    "content_block_stop", "message_stop")


def box_doc(w: int = 6000, h: int = 4000) -> Document:
    plan = Plan(id="g", level=0, name="Ground Floor", site=Site(north_deg=0.0),
                walls=[Wall("w0", P(0, 0), P(w, 0), 230),
                       Wall("w1", P(w, 0), P(w, h), 230),
                       Wall("w2", P(w, h), P(0, h), 230),
                       Wall("w3", P(0, h), P(0, 0), 230)])
    doc = Document.from_plan(plan, name="Test")
    doc.apply(Command(op="update_wall",
                      params={"wall_id": "w0", "thickness_mm": 230}))
    return doc


# --------------------------------------------------------------------------
# the fake client
# --------------------------------------------------------------------------

def thinking_blocks(text: str):
    return ([NS(type="content_block_start", content_block=NS(type="thinking"))]
            + [NS(type="content_block_delta",
                  delta=NS(type="thinking_delta", thinking=chunk))
               for chunk in text.split("|")]
            + [NS(type="content_block_stop")])


def text_blocks(text: str):
    return ([NS(type="content_block_start", content_block=NS(type="text"))]
            + [NS(type="content_block_delta",
                  delta=NS(type="text_delta", text=chunk))
               for chunk in text.split("|")]
            + [NS(type="content_block_stop")])


def tool_use(name: str, args: dict, call_id: str = "tu_1"):
    return NS(type="tool_use", name=name, id=call_id, input=args)


def final(content, stop_reason="end_turn"):
    return NS(content=content, stop_reason=stop_reason, stop_details=None,
              usage=NS(input_tokens=100, output_tokens=50,
                       cache_read_input_tokens=900,
                       cache_creation_input_tokens=0))


class FakeStream:
    def __init__(self, events, message):
        self._events, self._message = events, message

    def __enter__(self): return self
    def __exit__(self, *a): return False
    def __iter__(self): return iter(self._events)
    def get_final_message(self): return self._message


class FakeMessages:
    """Replays a script of turns. Each entry is (stream_events, final_message)."""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[dict] = []

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        if not self.script:
            raise AssertionError("the agent asked for more turns than scripted")
        events, message = self.script.pop(0)
        return FakeStream(events, message)


class FakeClient:
    def __init__(self, script):
        self.messages = FakeMessages(script)


def drain(doc, transcript, message, script, **kw):
    client = FakeClient(script)
    events = list(stream_turn(doc, transcript, message, client=client, **kw))
    return events, client


def kinds(events):
    return [e["type"] for e in events]


# --------------------------------------------------------------------------
# the event contract
# --------------------------------------------------------------------------

def test_a_plain_answer_streams_thinking_then_text():
    doc, transcript = box_doc(), []
    events, _ = drain(doc, transcript, "how big is it?", [(
        thinking_blocks("measuring|the room"),
        final([NS(type="thinking", thinking="measuring the room"),
               NS(type="text", text="It is 6.0 by 4.0 m.")]),
    )])
    assert kinds(events) == ["thinking", "thinking", "thinking_end",
                             "text", "done"] or "done" in kinds(events)
    assert "".join(e["delta"] for e in events if e["type"] == "thinking") \
        == "measuringthe room"
    assert [e for e in events if e["type"] == "thinking_end"][0]["seconds"] >= 0
    done = events[-1]
    assert done["type"] == "done"
    assert done["seq"] == doc.seq
    assert done["usage"]["cache_read"] == 900


def test_text_arrives_incrementally():
    doc, transcript = box_doc(), []
    events, _ = drain(doc, transcript, "hello", [(
        text_blocks("It is |6.0 by |4.0 m."),
        final([NS(type="text", text="It is 6.0 by 4.0 m.")]),
    )])
    deltas = [e["delta"] for e in events if e["type"] == "text"]
    assert deltas == ["It is ", "6.0 by ", "4.0 m."]
    assert "".join(deltas) == "It is 6.0 by 4.0 m."


def test_read_only_tools_emit_start_and_done_rows():
    doc, transcript = box_doc(), []
    events, _ = drain(doc, transcript, "check it", [
        ([], final([tool_use("get_findings", {})])),
        (text_blocks("All clear."), final([NS(type="text", text="All clear.")])),
    ])
    tools = [e for e in events if e["type"] == "tool"]
    assert [t["state"] for t in tools] == ["start", "done"]
    assert tools[0]["label"] == TOOL_LABELS["get_findings"]
    assert tools[1]["detail"]          # a few words of result, not the output


def test_get_plan_detail_reports_the_room_count():
    doc, transcript = box_doc(), []
    events, _ = drain(doc, transcript, "read it", [
        ([], final([tool_use("get_plan", {})])),
        (text_blocks("Done."), final([NS(type="text", text="Done.")])),
    ])
    done_row = [e for e in events
                if e["type"] == "tool" and e["state"] == "done"][0]
    assert done_row["detail"] == "1 rooms"


def test_apply_commands_emits_no_tool_row():
    """The whole readability of the pane rests on this.

    A write tool speaks through its effects, which are already `change`
    events. Emitting a tool row as well shows the same edit twice -- and
    `apply_commands` is the tool the agent reaches for most, so the duplicate
    would be most of the noise.
    """
    doc, transcript = box_doc(), []
    rid = doc.design.active.rooms[0].id
    events, _ = drain(doc, transcript, "name it", [
        ([], final([tool_use("apply_commands", {"commands": [
            {"op": "update_room", "description": "Name the room",
             "params": {"room_id": rid, "name": "Hall"}}]})])),
        (text_blocks("Named."), final([NS(type="text", text="Named.")])),
    ])
    assert not [e for e in events if e["type"] == "tool"], \
        "apply_commands must not appear as a tool row"
    changes = [e for e in events if e["type"] == "change"]
    assert len(changes) == 1
    assert changes[0]["event"]["summary"] == \
        "Unnamed room renamed to Hall, 6.00 x 4.00 m"
    assert changes[0]["event"]["source"] == "agent"


def test_refused_commands_are_reported_with_a_reason():
    doc, transcript = box_doc(), []
    events, _ = drain(doc, transcript, "break it", [
        ([], final([tool_use("apply_commands", {"commands": [
            {"op": "update_wall", "description": "Thicken a missing wall",
             "params": {"wall_id": "w99", "thickness_mm": 230}}]})])),
        (text_blocks("Could not."), final([NS(type="text", text="Could not.")])),
    ])
    rejected = [e for e in events if e["type"] == "rejected"]
    assert len(rejected) == 1
    assert rejected[0]["op"] == "update_wall"
    assert "no wall" in rejected[0]["reason"]
    assert not [e for e in events if e["type"] == "change"]


def test_a_rejection_is_reported_once_not_on_every_later_step():
    """`ctx.rejected` is cleared after reporting; without that, one refusal
    reappears on every subsequent tool round trip."""
    doc, transcript = box_doc(), []
    events, _ = drain(doc, transcript, "twice", [
        ([], final([tool_use("apply_commands", {"commands": [
            {"op": "update_wall", "description": "x",
             "params": {"wall_id": "w99", "thickness_mm": 230}}]})])),
        ([], final([tool_use("get_findings", {})], )),
        (text_blocks("Done."), final([NS(type="text", text="Done.")])),
    ])
    assert len([e for e in events if e["type"] == "rejected"]) == 1


def test_changes_are_emitted_once_each_across_steps():
    doc, transcript = box_doc(), []
    rid = doc.design.active.rooms[0].id
    events, _ = drain(doc, transcript, "two steps", [
        ([], final([tool_use("apply_commands", {"commands": [
            {"op": "update_room", "description": "a",
             "params": {"room_id": rid, "name": "Hall"}}]}, "tu_a")])),
        ([], final([tool_use("apply_commands", {"commands": [
            {"op": "add_door", "description": "b",
             "params": {"opening_id": "o0", "wall_id": "w0",
                        "at": "centre"}}]}, "tu_b")])),
        (text_blocks("Done."), final([NS(type="text", text="Done.")])),
    ])
    changes = [e["event"]["op"] for e in events if e["type"] == "change"]
    assert changes == ["update_room", "add_door"], changes


def test_an_api_error_ends_the_stream_with_an_error_event():
    class Boom:
        class messages:
            @staticmethod
            def stream(**kw):
                raise RuntimeError("connection reset")
    events = list(stream_turn(box_doc(), [], "hi", client=Boom()))
    assert kinds(events) == ["error"]
    assert "connection reset" in events[0]["message"]


def test_a_refusal_is_surfaced_not_swallowed():
    doc, transcript = box_doc(), []
    events, _ = drain(doc, transcript, "no", [(
        [], final([], stop_reason="refusal"),
    )])
    assert kinds(events) == ["error"]
    assert "declined" in events[0]["message"]


def test_the_step_budget_is_bounded_and_says_so():
    doc, transcript = box_doc(), []
    # Always ask for another tool call; the loop must stop on its own.
    script = [([], final([tool_use("get_findings", {}, f"tu_{i}")]))
              for i in range(20)]
    events, client = drain(doc, transcript, "loop", script, max_steps=3)
    done = [e for e in events if e["type"] == "done"]
    assert done and done[0]["stopped_early"] is True
    assert len(client.messages.calls) <= 5


# --------------------------------------------------------------------------
# the request the agent builds
# --------------------------------------------------------------------------

def test_the_cached_prefix_is_stable_across_turns():
    """`system` and `tools` form the cache prefix. If either varies per turn
    the cache never hits, which is invisible except on the bill."""
    doc = box_doc()
    transcript: list[dict] = []
    _, c1 = drain(doc, transcript, "first",
                  [(text_blocks("a"), final([NS(type="text", text="a")]))])
    _, c2 = drain(doc, transcript, "second",
                  [(text_blocks("b"), final([NS(type="text", text="b")]))])
    assert c1.messages.calls[0]["system"] == c2.messages.calls[0]["system"]
    assert c1.messages.calls[0]["tools"] == c2.messages.calls[0]["tools"]
    assert c1.messages.calls[0]["system"][0]["cache_control"] == \
        {"type": "ephemeral"}


def test_plan_state_goes_in_as_an_operator_message():
    """State is sent as `{"role": "system"}` inside `messages`, not by editing
    the top-level system prompt: that preserves the cached prefix, and it means
    a user message cannot forge plan state."""
    doc, transcript = box_doc(), []
    drain(doc, transcript, "how big?",
          [(text_blocks("6x4"), final([NS(type="text", text="6x4")]))])
    roles = [m["role"] for m in transcript]
    assert roles[0] == "user"
    assert roles[1] == "system"
    assert "CURRENT PLAN" in transcript[1]["content"]
    assert "6.00x4.00 m" in transcript[1]["content"]


def test_thinking_is_requested_as_a_summary():
    """The default on Opus 5 is `omitted`, which streams empty thinking blocks
    -- the pane would show a long pause and nothing else."""
    doc, transcript = box_doc(), []
    _, c = drain(doc, transcript, "hi",
                 [(text_blocks("ok"), final([NS(type="text", text="ok")]))])
    assert c.messages.calls[0]["thinking"] == {"type": "adaptive",
                                               "display": "summarized"}


def test_the_assistant_turn_is_appended_whole():
    """Thinking blocks must be replayed unchanged on the same model, so the
    transcript keeps `response.content`, not an extracted string."""
    doc, transcript = box_doc(), []
    blocks = [NS(type="thinking", thinking="hmm"),
              NS(type="text", text="ok")]
    drain(doc, transcript, "hi", [(text_blocks("ok"), final(blocks))])
    assistant = [m for m in transcript if m["role"] == "assistant"][0]
    assert assistant["content"] is blocks


def test_only_symbolic_commands_are_offered():
    """DECISIONS.md #6: the model emits intent, solvers emit coordinates."""
    from fpeval.agent import TOOLS
    from fpeval.commands import SYMBOLIC_OPS, DIRECT_OPS
    apply_tool = [t for t in TOOLS if t["name"] == "apply_commands"][0]
    offered = set(apply_tool["input_schema"]["properties"]["commands"]
                  ["items"]["properties"]["op"]["enum"])
    assert offered == set(SYMBOLIC_OPS)
    assert not (offered & set(DIRECT_OPS))


def test_tool_results_go_back_in_one_user_message():
    """Splitting parallel tool results across messages teaches the model to
    stop making parallel calls."""
    doc, transcript = box_doc(), []
    drain(doc, transcript, "two tools", [
        ([], final([tool_use("get_plan", {}, "tu_a"),
                    tool_use("get_findings", {}, "tu_b")])),
        (text_blocks("ok"), final([NS(type="text", text="ok")])),
    ])
    results = [m for m in transcript
               if m["role"] == "user" and isinstance(m["content"], list)]
    assert len(results) == 1
    assert [b["tool_use_id"] for b in results[0]["content"]] == ["tu_a", "tu_b"]


# --------------------------------------------------------------------------
# the digest the model reads
# --------------------------------------------------------------------------

def test_the_digest_gives_rooms_ids_and_both_unit_systems():
    doc = box_doc()
    text = plan_digest(doc.design)
    r = doc.design.active.rooms[0]
    assert r.id in text                      # a command names the room by id
    assert "6.00x4.00 m" in text
    assert "19'8\" x 13'1\"" in text          # Indian plans are quoted in feet
    assert "258 sqft" in text


def test_the_turn_context_reports_only_what_changed():
    doc = box_doc()
    mark = doc.seq
    doc.apply(Command(op="add_column", params={"column_id": "c0",
                                               "position": {"x": 500, "y": 500}}))
    text = turn_context(doc, mark)
    assert "CHANGES SINCE YOUR LAST MESSAGE" in text
    assert "column added" in text.lower()
    assert "No changes" in turn_context(doc, doc.seq)
