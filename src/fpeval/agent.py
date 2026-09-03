"""The chat agent: a conversation that edits the document.

A manual tool-use loop rather than the SDK's tool runner, because every step
has to be persisted to the command log and streamed to the pane as a domain
event, and a turn has to be interruptible mid-flight. The runner's per-turn
hooks would carry most of that, but not the "stop after this tool call because
the user typed something" case.

## What the model is allowed to do

Only `symbolic` commands (`commands.SYMBOLIC_OPS`). It names intent -- "add a
door at the centre of w7", "the master bedroom should be 150-190 sqft" -- and
never a coordinate. That is DECISIONS.md #6, and `Command.validate` enforces it
against `source="agent"` rather than trusting the prompt.

## Context layout, and why it is shaped this way

  * `tools` and `system` are frozen and sorted, so they form a stable cache
    prefix. The command catalogue is *generated* from the table, so the prompt
    cannot drift from what the applier accepts.
  * The transcript follows.
  * The volatile per-turn payload -- the plan digest and the events since the
    model last spoke -- goes in as a **mid-conversation system message**
    (`{"role": "system"}` inside `messages`). It preserves the cached prefix
    and carries operator authority, so a user message cannot forge plan state.
  * That payload is *state plus recent events*, never the whole event history.
    State carries correctness; history only carries intent, as in "make **it**
    bigger". An unbounded bullet list would eat the window for nothing.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence

from .commands import (
    Command, TABLE, SYMBOLIC_OPS, catalogue, SYMBOLIC,
)
from .document import Document, feed
from .ir import Design, Plan
from . import roomtypes as _rt

MODEL = "claude-opus-5"
MAX_TOKENS = 32000
MAX_STEPS = 8               # tool round trips before we stop and answer

SYSTEM = """\
You are the design assistant inside a floor-plan editor. You and the person you
are talking to are editing the same document: their mouse and your commands go
through one log, so anything they do appears in your context and anything you do
appears on their screen immediately.

## How you change the plan

You emit **symbolic commands**. You never emit a coordinate, a wall endpoint, or
a room polygon -- a CP-SAT solver owns all geometry and recomputes it. To move a
wall you name a compass direction and a distance; to place a door you name the
wall and a fraction along it. The applier resolves those against the real
geometry. A command carrying a coordinate is rejected before it runs.

Available commands:
{catalogue}

## How to work

- Prefer the smallest change that answers the request. One command is usually
  right; more than four means you should be asking a question instead.
- `description` on every command is what the user reads in their change feed.
  Write it for a homeowner, in the imperative, naming what changes and why:
  "Widen the master bedroom to 150-190 sqft so it meets the 2.4 m minimum".
- Read before you write. `get_plan` and `get_findings` are cheap; guessing which
  room someone means is not.
- Commands are applied and reported per-command. If two of five are refused you
  will be told why, and you should tell the user plainly rather than retrying
  blindly.
- If a request is ambiguous in a way that changes the answer, ask. If it is
  ambiguous in a way that does not, pick the sensible reading and say which.
- If something cannot be done with the available commands, say so. Do not
  invent a command name.

## How to answer

Plain sentences, no headers, no bullet lists unless you are enumerating
findings. Say what you changed and what it means for the plan. The user can
already see the list of changes -- do not read it back to them. If you changed
nothing, say that and why.
"""


# --------------------------------------------------------------------------
# the plan digest -- what the model reads as state
# --------------------------------------------------------------------------

def _ft_in(mm: float) -> str:
    inches = mm / 25.4
    ft = int(inches // 12)
    rem = int(round(inches - ft * 12))
    if rem == 12:
        ft, rem = ft + 1, 0
    return f"{ft}'{rem}\"" if rem else f"{ft}'"


def plan_digest(design: Design, *, max_walls: int = 60) -> str:
    """The current state, compact enough to send every turn.

    Rooms carry their id, because that is what a command names; and their
    dimensions in both metric and feet-inches, because Indian plans are quoted
    in feet and the model has to be able to answer "is that 12 by 14".
    """
    st: Optional[Plan] = design.active
    if st is None:
        return "The design has no storeys yet."

    lines: list[str] = []
    lines.append(f"DESIGN {design.name or design.id} -- "
                 f"{len(design.storeys)} storey(s), editing "
                 f"'{st.name or st.id}' (level {st.level})")
    if st.site.north_deg:
        lines.append(f"North: +Y axis is at bearing {st.site.north_deg:g} deg")
    if st.site.setbacks_mm:
        lines.append("Setbacks (mm): " + ", ".join(
            f"{k} {v}" for k, v in sorted(st.site.setbacks_mm.items())))

    lines.append(f"\nROOMS ({len(st.rooms)})")
    if not st.rooms:
        lines.append("  (none -- the wall graph encloses no faces yet)")
    for r in st.rooms:
        dims = ""
        if len(r.polygon) >= 3:
            xs = [p.x for p in r.polygon]
            ys = [p.y for p in r.polygon]
            w, h = max(xs) - min(xs), max(ys) - min(ys)
            dims = (f" {w/1000:.2f}x{h/1000:.2f} m "
                    f"({_ft_in(w)} x {_ft_in(h)})")
        t = _rt.get(r.category)
        klass = f" [{t.klass}]" if t else ""
        lines.append(f"  {r.id}: {r.name or '(unnamed)'} "
                     f"cat={r.category}{klass} "
                     f"{r.area_m2:.1f} m2 / {r.area_m2 * 10.7639:.0f} sqft"
                     f"{dims} walls={','.join(r.wall_ids[:6])}")

    lines.append(f"\nWALLS ({len(st.walls)})")
    for w in st.walls[:max_walls]:
        horiz = abs(w.end.y - w.start.y) < abs(w.end.x - w.start.x)
        lines.append(f"  {w.id}: {'horizontal' if horiz else 'vertical'} "
                     f"{w.length/1000:.2f} m, {w.thickness} mm thick")
    if len(st.walls) > max_walls:
        lines.append(f"  ... {len(st.walls) - max_walls} more")

    lines.append(f"\nOPENINGS ({len(st.openings)})")
    for o in st.openings:
        lines.append(f"  {o.id}: {o.kind}"
                     f"{'/' + o.subtype if o.subtype else ''} on {o.wall_id} "
                     f"at {o.position:.2f}, {o.width} mm wide")
    if not st.openings:
        lines.append("  (none)")

    if st.furniture:
        lines.append(f"\nFURNITURE ({len(st.furniture)})")
        for f in st.furniture[:30]:
            lines.append(f"  {f.id}: {f.catalog_id} in {f.room_id or '?'}"
                         f"{' (locked)' if f.locked else ''}")
    if st.stairs:
        lines.append(f"\nSTAIRS ({len(st.stairs)})")
        for s in st.stairs:
            lines.append(f"  {s.id}: {s.stair_type} {s.direction}, "
                         f"{s.riser_count} risers, in {s.room_id or '?'}")
    if st.columns:
        lines.append(f"\nCOLUMNS: {len(st.columns)}")
    return "\n".join(lines)


def turn_context(doc: Document, last_seen_seq: int,
                 findings: Sequence[Any] = ()) -> str:
    """The per-turn operator message: state, then what changed since.

    Sent as a `{"role": "system"}` entry inside `messages` rather than by
    editing the top-level system prompt, which would invalidate the cached
    prefix on every turn.
    """
    parts = ["CURRENT PLAN (authoritative -- this is the document, not a claim "
             "about it)", plan_digest(doc.design)]
    since = doc.events_since(last_seen_seq)
    if since:
        parts += ["", f"CHANGES SINCE YOUR LAST MESSAGE (seq {last_seen_seq} -> "
                      f"{doc.seq})", feed(since, limit=25)]
    else:
        parts += ["", "No changes since your last message."]
    if findings:
        parts += ["", "VALIDATOR FINDINGS"]
        parts += [f"- [{getattr(f, 'severity', '?')}] "
                  f"{getattr(f, 'rule_id', '')}: {getattr(f, 'message', f)}"
                  for f in findings[:25]]
    return "\n".join(parts)


# --------------------------------------------------------------------------
# tools
# --------------------------------------------------------------------------

def _symbolic_command_schema() -> dict:
    """One flat schema over the symbolic vocabulary.

    Not `strict`, and not a 30-way `oneOf`: strict tool use compiles the schema
    into a grammar and caps optional parameters at 24 and unions at 16, which a
    vocabulary this size exceeds -- the same wall `llm.py` documents hitting for
    the patch schema. Conformance is enforced locally instead, by
    `Command.validate`, and a rejected command comes back to the model as a
    tool result it can act on.
    """
    params: dict[str, Any] = {}
    for op in SYMBOLIC_OPS:
        spec = TABLE[op]
        for key in (*spec.required, *spec.optional):
            params.setdefault(key, {"type": ["string", "number", "boolean",
                                             "object", "array", "null"]})
    return {
        "type": "object",
        "properties": {
            "commands": {
                "type": "array",
                "description": "The symbolic commands to apply, in order.",
                "items": {
                    "type": "object",
                    "properties": {
                        "op": {"type": "string", "enum": list(SYMBOLIC_OPS)},
                        "description": {
                            "type": "string",
                            "description": "One line, imperative, for the user's "
                                           "change feed. Required.",
                        },
                        "params": {"type": "object", "properties": params},
                    },
                    "required": ["op", "description", "params"],
                },
            },
        },
        "required": ["commands"],
    }


TOOLS: list[dict] = [
    {
        "name": "get_plan",
        "description": "The current plan: rooms with ids, names, categories, "
                       "areas and dimensions; walls; openings; objects. Read "
                       "this before naming anything.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_findings",
        "description": "What the rules engine says about the plan right now: "
                       "NBC, local bye-laws, geometry, Vastu.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "apply_commands",
        "description": "Apply symbolic commands to the document. Each is "
                       "validated and applied independently: you will be told "
                       "which landed and why any were refused.",
        "input_schema": _symbolic_command_schema(),
    },
]


@dataclass
class ToolContext:
    doc: Document
    findings_fn: Callable[[], list[Any]]
    events: list[Any] = field(default_factory=list)
    rejected: list[Any] = field(default_factory=list)


def _run_tool(name: str, args: dict, ctx: ToolContext) -> str:
    if name == "get_plan":
        return plan_digest(ctx.doc.design)
    if name == "get_findings":
        fs = ctx.findings_fn()
        if not fs:
            return "No findings. The plan is clean against the rules engine."
        return "\n".join(
            f"[{getattr(f, 'severity', '?')}] {getattr(f, 'rule_id', '')}: "
            f"{getattr(f, 'message', f)}" for f in fs[:40])
    if name == "apply_commands":
        raw = args.get("commands") or []
        cmds = [Command(op=str(c.get("op", "")),
                        params=dict(c.get("params") or {}),
                        source="agent",
                        description=str(c.get("description") or ""))
                for c in raw]
        events, rejected = ctx.doc.apply_batch(cmds)
        ctx.events += events
        ctx.rejected += rejected
        lines = []
        if events:
            lines.append("APPLIED:")
            lines += [f"  seq {e.seq}: {e.summary}" for e in events]
        if rejected:
            lines.append("REFUSED (do not retry without changing something):")
            lines += [f"  {r.command.op}: {r.errors[0]}" for r in rejected]
        if not events and not rejected:
            lines.append("No commands were supplied.")
        lines.append(f"\nDocument is now at seq {ctx.doc.seq}.")
        return "\n".join(lines)
    return f"unknown tool {name}"


# --------------------------------------------------------------------------
# the loop
# --------------------------------------------------------------------------

@dataclass
class TurnResult:
    reply: str
    events: list[Any] = field(default_factory=list)
    rejected: list[Any] = field(default_factory=list)
    steps: int = 0
    usage: dict[str, int] = field(default_factory=dict)
    stopped_early: bool = False
    error: str = ""


def run_turn(
    doc: Document,
    transcript: list[dict],
    message: str,
    *,
    last_seen_seq: int = 0,
    findings_fn: Optional[Callable[[], list[Any]]] = None,
    client: Any = None,
    model: str = MODEL,
    max_steps: int = MAX_STEPS,
    effort: str = "high",
) -> TurnResult:
    """One user message in, one reply out, with the document edited in place.

    `transcript` is mutated: the user message, every assistant turn, and every
    tool result are appended, so the next call resumes with a cache-friendly
    prefix rather than a rebuilt history.
    """
    import anthropic

    client = client or anthropic.Anthropic()
    ctx = ToolContext(doc=doc, findings_fn=findings_fn or (lambda: []))

    system = [{
        "type": "text",
        "text": SYSTEM.format(catalogue=catalogue(SYMBOLIC)),
        # The prefix is frozen and the catalogue is generated from a sorted
        # table, so this is byte-stable across turns and processes.
        "cache_control": {"type": "ephemeral"},
    }]

    transcript.append({"role": "user", "content": message})
    # Operator channel: state cannot be forged from a user message, and the
    # cached prefix survives.
    transcript.append({
        "role": "system",
        "content": turn_context(doc, last_seen_seq, ctx.findings_fn()),
    })

    usage = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    steps = 0
    reply = ""

    while steps <= max_steps:
        try:
            with client.messages.stream(
                model=model,
                max_tokens=MAX_TOKENS,
                system=system,
                messages=transcript,
                tools=TOOLS,
                # `display` defaults to "omitted" on Opus 5, which streams
                # empty thinking blocks -- the pane would show a long pause
                # and nothing else. The summary is what the user reads.
                thinking={"type": "adaptive", "display": "summarized"},
                output_config={"effort": effort},
            ) as stream:
                resp = stream.get_final_message()
        except anthropic.APIStatusError as exc:
            return TurnResult(reply="", error=f"{exc.status_code}: {exc}",
                              events=ctx.events, rejected=ctx.rejected,
                              steps=steps, usage=usage)
        except Exception as exc:                       # transport, timeout
            return TurnResult(reply="", error=f"{type(exc).__name__}: {exc}",
                              events=ctx.events, rejected=ctx.rejected,
                              steps=steps, usage=usage)

        u = resp.usage
        usage["input"] += getattr(u, "input_tokens", 0) or 0
        usage["output"] += getattr(u, "output_tokens", 0) or 0
        usage["cache_read"] += getattr(u, "cache_read_input_tokens", 0) or 0
        usage["cache_write"] += getattr(u, "cache_creation_input_tokens", 0) or 0

        # Append the whole content, not just the text: thinking blocks have to
        # go back unchanged on the same model.
        transcript.append({"role": "assistant", "content": resp.content})

        if resp.stop_reason == "refusal":
            detail = getattr(resp, "stop_details", None)
            cat = getattr(detail, "category", None) if detail else None
            return TurnResult(
                reply="", error=f"refused ({cat or 'unspecified'})",
                events=ctx.events, rejected=ctx.rejected, steps=steps,
                usage=usage)

        text = "".join(b.text for b in resp.content
                       if getattr(b, "type", "") == "text")
        calls = [b for b in resp.content if getattr(b, "type", "") == "tool_use"]

        if not calls:
            reply = text.strip()
            break

        # All tool results go back in ONE user message. Splitting them teaches
        # the model to stop making parallel calls.
        results = []
        for call in calls:
            try:
                out = _run_tool(call.name, dict(call.input or {}), ctx)
                results.append({"type": "tool_result", "tool_use_id": call.id,
                                "content": out})
            except Exception as exc:
                results.append({"type": "tool_result", "tool_use_id": call.id,
                                "is_error": True,
                                "content": f"{type(exc).__name__}: {exc}"})
        transcript.append({"role": "user", "content": results})
        steps += 1
        if text.strip():
            reply = text.strip()          # keep the latest prose as a fallback

    stopped = steps > max_steps
    if stopped and not reply:
        reply = ("I ran out of steps working on that. Here is where the plan "
                 f"got to: {len(ctx.events)} change(s) applied.")
    return TurnResult(reply=reply or "(no reply)", events=ctx.events,
                      rejected=ctx.rejected, steps=steps, usage=usage,
                      stopped_early=stopped)


# --------------------------------------------------------------------------
# streaming
# --------------------------------------------------------------------------
#
# The pane needs three different things from a turn, and they arrive on
# different schedules: the reasoning summary (early, incrementally), the tool
# activity (in bursts), and the prose (last). A single blocking call gives the
# user a spinner for fifteen seconds and then a wall of text.
#
# So `stream_turn` yields typed dicts and `run_turn` above is the blocking
# convenience built on the same code path -- one implementation, so the two
# cannot disagree about what a turn does.
#
# One deliberate omission: `apply_commands` does NOT emit a tool row. Its
# effects are already emitted as `change` events, and rendering both shows the
# same edit twice. Since it is the tool the agent reaches for most, dropping
# the duplicate is most of the noise gone.

TOOL_LABELS = {
    "get_plan": "Read the plan",
    "get_findings": "Read code findings",
    "apply_commands": "Applied changes",
}


def stream_turn(
    doc: Document,
    transcript: list[dict],
    message: str,
    *,
    last_seen_seq: int = 0,
    findings_fn: Optional[Callable[[], list[Any]]] = None,
    client: Any = None,
    model: str = MODEL,
    max_steps: int = MAX_STEPS,
    effort: str = "high",
):
    """Yield the turn as it happens.

    Event shapes, all with a `type`:

      {"type": "thinking", "delta": str}          reasoning summary, incremental
      {"type": "thinking_end", "seconds": float}
      {"type": "tool", "name": str, "label": str, "state": "start"|"done",
       "detail": str}                             read-only tools only
      {"type": "change", "event": {...}}          a command landed
      {"type": "rejected", "op": str, "reason": str}
      {"type": "text", "delta": str}              the reply, incremental
      {"type": "done", "seq": int, "hash": str, "steps": int,
       "usage": {...}, "seconds": float}
      {"type": "error", "message": str}

    The caller is responsible for persisting; this function only mutates `doc`
    and `transcript`.
    """
    import anthropic

    client = client or anthropic.Anthropic()
    ctx = ToolContext(doc=doc, findings_fn=findings_fn or (lambda: []))

    system = [{
        "type": "text",
        "text": SYSTEM.format(catalogue=catalogue(SYMBOLIC)),
        "cache_control": {"type": "ephemeral"},
    }]

    transcript.append({"role": "user", "content": message})
    transcript.append({
        "role": "system",
        "content": turn_context(doc, last_seen_seq, ctx.findings_fn()),
    })

    usage = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    started = time.time()
    steps = 0
    seen_events = 0

    while steps <= max_steps:
        think_started: Optional[float] = None
        try:
            with client.messages.stream(
                model=model,
                max_tokens=MAX_TOKENS,
                system=system,
                messages=transcript,
                tools=TOOLS,
                thinking={"type": "adaptive", "display": "summarized"},
                output_config={"effort": effort},
            ) as stream:
                for ev in stream:
                    kind = getattr(ev, "type", "")
                    if kind == "content_block_start":
                        block = getattr(ev, "content_block", None)
                        if getattr(block, "type", "") == "thinking":
                            think_started = time.time()
                    elif kind == "content_block_delta":
                        delta = getattr(ev, "delta", None)
                        dtype = getattr(delta, "type", "")
                        if dtype == "thinking_delta":
                            text = getattr(delta, "thinking", "") or ""
                            if text:
                                yield {"type": "thinking", "delta": text}
                        elif dtype == "text_delta":
                            text = getattr(delta, "text", "") or ""
                            if text:
                                yield {"type": "text", "delta": text}
                    elif kind == "content_block_stop" and think_started is not None:
                        yield {"type": "thinking_end",
                               "seconds": round(time.time() - think_started, 1)}
                        think_started = None
                resp = stream.get_final_message()
        except anthropic.APIStatusError as exc:
            yield {"type": "error", "message": f"{exc.status_code}: {exc}"}
            return
        except Exception as exc:
            yield {"type": "error", "message": f"{type(exc).__name__}: {exc}"}
            return

        u = resp.usage
        usage["input"] += getattr(u, "input_tokens", 0) or 0
        usage["output"] += getattr(u, "output_tokens", 0) or 0
        usage["cache_read"] += getattr(u, "cache_read_input_tokens", 0) or 0
        usage["cache_write"] += getattr(u, "cache_creation_input_tokens", 0) or 0

        transcript.append({"role": "assistant", "content": resp.content})

        if resp.stop_reason == "refusal":
            detail = getattr(resp, "stop_details", None)
            cat = getattr(detail, "category", None) if detail else None
            yield {"type": "error",
                   "message": f"declined ({cat or 'unspecified'})"}
            return

        calls = [b for b in resp.content if getattr(b, "type", "") == "tool_use"]
        if not calls:
            break

        results = []
        for call in calls:
            name = str(call.name)
            writes = name == "apply_commands"
            if not writes:
                yield {"type": "tool", "name": name, "state": "start",
                       "label": TOOL_LABELS.get(name, name), "detail": ""}
            try:
                out = _run_tool(name, dict(call.input or {}), ctx)
                results.append({"type": "tool_result", "tool_use_id": call.id,
                                "content": out})
                if not writes:
                    yield {"type": "tool", "name": name, "state": "done",
                           "label": TOOL_LABELS.get(name, name),
                           "detail": _tool_detail(name, out)}
            except Exception as exc:
                results.append({"type": "tool_result", "tool_use_id": call.id,
                                "is_error": True,
                                "content": f"{type(exc).__name__}: {exc}"})
                if not writes:
                    yield {"type": "tool", "name": name, "state": "done",
                           "label": TOOL_LABELS.get(name, name),
                           "detail": f"failed: {exc}"}

        # A write tool speaks through its effects, which are what the user
        # actually needs to see and keep.
        for event in ctx.events[seen_events:]:
            yield {"type": "change", "event": event.to_dict()}
        seen_events = len(ctx.events)
        for r in ctx.rejected:
            yield {"type": "rejected", "op": r.command.op,
                   "reason": r.errors[0] if r.errors else "refused"}
        ctx.rejected.clear()

        transcript.append({"role": "user", "content": results})
        steps += 1

    yield {"type": "done", "seq": doc.seq, "hash": doc.hash, "steps": steps,
           "usage": usage, "seconds": round(time.time() - started, 1),
           "stopped_early": steps > max_steps}


def _tool_detail(name: str, output: str) -> str:
    """A few words of result, for the collapsed row. Not the whole output --
    that is what the plan and the findings badges are for."""
    if name == "get_findings":
        if output.startswith("No findings"):
            return "clean"
        n = len([l for l in output.splitlines() if l.strip()])
        return f"{n} finding{'s' if n != 1 else ''}"
    if name == "get_plan":
        rooms = next((l for l in output.splitlines() if l.startswith("ROOMS (")),
                     "")
        return rooms[len("ROOMS ("):-1] + " rooms" if rooms else ""
    return ""
