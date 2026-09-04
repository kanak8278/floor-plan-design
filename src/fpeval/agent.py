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

import base64

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence

from .commands import (
    Command, TABLE, SYMBOLIC_OPS, SPEC_OPS, catalogue, SYMBOLIC, _ENUM_PARAMS,
)
from .document import Document, feed
from .ir import Design, Plan
from . import roomtypes as _rt
from .principles import prompt_block
from .spatial import plan_png, spatial_block

MODEL = "claude-opus-5"
MAX_TOKENS = 32000
MAX_STEPS = 8               # tool round trips before we stop and answer

SYSTEM = """\
You are the design assistant inside a floor-plan editor. You and the person you
are talking to are editing the same document: their mouse and your commands go
through one log, so anything they do appears in your context and anything you do
appears on their screen immediately.

## Two layers, and which one to use

A design has a **brief** (the programme: the plot, the room list, target areas,
zones, adjacencies) and **geometry** (the walls, doors and windows actually
drawn). They are separate, and both are in your context every turn.

To create or substantially rearrange a plan, edit the brief and then call
`solve_layout`. That compiles the brief into geometry with a CP-SAT solver.
This is the only way to make a floor from nothing: there is no first wall to
draw, wall references resolve only against walls that already exist, and you
may not author coordinates.

To adjust a plan that already exists, edit the geometry directly -- move a
wall, add a window, rename a room. Do not re-solve for a change a single
command makes, because solving discards the current layout and produces a new
one, losing anything the user has arranged by hand.

Editing the brief does NOT move a wall. The plan on screen stays as it was
until you solve, and you will be told when the two are out of step.

## How you change the plan

You emit **symbolic commands**. You never emit a coordinate, a wall endpoint, or
a room polygon -- a CP-SAT solver owns all geometry and recomputes it. To move a
wall you name a compass direction and a distance; to place a door you name the
wall and a fraction along it. The applier resolves those against the real
geometry. A command carrying a coordinate is rejected before it runs.

Available commands:
{catalogue}

## Reading the plan

You are shown the plan as a drawing, and given the same floor in text. Look
at the drawing first -- it is what the person you are talking to is looking
at, and it is the only place the arrangement is visible as a whole. The room
and wall lists give exact sizes and the ids that commands name. The room
positions list gives each room's compass zone, which of its walls are
exterior, what it has a door to, and what it merely shares a wall with; trust
that list over your reading of the picture for anything you have to be precise
about.

That last distinction is the one to act on. "Shares a wall but no door" is one
`add_door` away from fixed. "NO exterior wall" means the room can never have a
window, so a ventilation finding against it needs the layout changed, not an
opening added.

Do not guess a compass direction. `move_wall_parallel` and `set_room_zone`
both take one, and the room positions list tells you the answer.

## What makes a plan good

{principles}

## What to assume, and what to ask

Default rather than ask when a sensible default exists and getting it wrong is
cheap to correct: one floor unless storeys are mentioned, standard wall
thicknesses, door and window sizes, the front door on the road side, the usual
Indian room sizes. "A 2BHK" fully determines the bedroom count; build it.

The plot is different. A plan solved on the wrong plot looks exactly as
finished as one solved on the right plot, and nothing on the drawing says
which it is. So when the plot is not stated you may proceed on the standard
site for that many bedrooms -- `solve_layout` will pick one and tell you --
but you must say in your reply which plot you used and that it is an
assumption. The same goes for the facing. Never present an assumed dimension
as though the user gave it.

If the user asks for something the site cannot hold, the solver says
INFEASIBLE and names the constraints that clash. Report that as the answer it
is, with what would have to give, rather than quietly dropping a room.

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
    # The id, not just the display name. Commands name ids, and this line used
    # to print `st.name or st.id` -- so on a storey called "Ground Floor" the
    # id never appeared anywhere in the context. The agent guessed
    # "Ground Floor", "ground_floor", "ground", "storey_0" and "s0" in one
    # turn and burned five refusals on a value nobody had given it.
    lines.append(f"DESIGN {design.name or design.id} -- "
                 f"{len(design.storeys)} storey(s), editing "
                 f"'{st.name}' id={st.id} (level {st.level})")
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


def brief_digest(design: Design) -> str:
    """The programme, which is a separate thing from the drawn geometry.

    Room ids here are what every `set_room_*` command names, and they are not
    the same set as the room ids in the plan: before a solve the plan has none,
    and after one the solver may have added circulation the brief never asked
    for. Printing both, labelled, is the only way the distinction survives
    contact with a model.
    """
    sp = getattr(design, "spec", None)
    if sp is None or (not sp.rooms and not sp.plot_width_ft):
        return ("BRIEF: empty. Nothing has been asked for yet -- "
                "`use_standard_programme` is the fastest way to start, and "
                "`solve_layout` will assume a standard 2BHK if you call it "
                "on an empty brief.")
    lines = ["BRIEF (the programme; `solve_layout` compiles this into "
             "geometry)"]
    if sp.site_kind == "apartment_unit":
        q = sp.unit_area
        area = q.carpet_sqft or q.builtup_sqft or q.super_builtup_sqft
        lines.append(f"  site: apartment unit"
                     + (f", {area:g} sqft" if area else ", area not stated"))
    else:
        if sp.plot_width_ft and sp.plot_depth_ft:
            lines.append(f"  plot: {sp.plot_width_ft:g} x {sp.plot_depth_ft:g} ft "
                         f"({sp.plot_width_ft * sp.plot_depth_ft:g} sqft)"
                         f"{', corner' if sp.corner_plot else ''}")
        else:
            lines.append("  plot: NOT STATED (solve_layout will assume the "
                         "standard site for the bedroom count and say so)")
    lines.append(f"  road facing: {sp.road_facing_side or 'not stated'}"
                 f" | storeys: {sp.storeys} | city: {sp.city_profile}")
    ent = sp.entrance
    if ent is not None and (ent.side or ent.via_foyer):
        lines.append(f"  entrance: {ent.side or 'not stated'}"
                     f"{', via a foyer' if ent.via_foyer else ''}")
    lines.append(f"  rooms ({len(sp.rooms)}):")
    for r in sp.rooms:
        rng = (f"{r.min_sqft:g}-{r.max_sqft:g} sqft"
               if r.min_sqft and r.max_sqft else "size not set")
        bits = [rng, f"priority {r.priority}"]
        if r.preferred_zone:
            bits.append(f"{r.preferred_zone} zone")
        if r.attached_bath:
            bits.append("en-suite")
        if r.optional:
            bits.append("optional")
        lines.append(f"    {r.id}: {r.name or r.category} "
                     f"cat={r.category} -- {', '.join(bits)}")
    if sp.adjacency:
        lines.append(f"  adjacency ({len(sp.adjacency)}):")
        for a in sp.adjacency:
            lines.append(f"    {a.a} {a.kind} {a.relation} {a.b}"
                         + (f" -- {a.reason}" if a.reason else ""))
    return "\n".join(lines)


def _stale(doc: Document) -> bool:
    """Has the brief changed since the geometry was last solved from it?

    Worth stating explicitly rather than leaving the model to infer it: a brief
    edit does not move a wall, so after `set_room_area` the plan on screen is
    still the old one, and an agent that does not know that will report the
    room as resized when nothing has changed.
    """
    last_solve = last_spec = 0
    for e in doc.log:
        if e.command.op == "replace_storey":
            last_solve = e.seq
        elif e.command.op in SPEC_OPS:
            last_spec = e.seq
    return last_spec > last_solve


def turn_context(doc: Document, last_seen_seq: int,
                 findings: Sequence[Any] = ()) -> str:
    """The per-turn operator message: state, then what changed since.

    `plan_digest` carries no coordinates -- walls are
    "vertical 14.40 m" with no position and openings are a fraction along a
    wall id -- so before this the model was reasoning about arrangement from a
    bill of quantities, and every spatial judgement it made was a guess.

    Sent as a `{"role": "system"}` entry inside `messages` rather than by
    editing the top-level system prompt, which would invalidate the cached
    prefix on every turn.
    """
    parts = ["CURRENT PLAN (authoritative -- this is the document, not a claim "
             "about it)", plan_digest(doc.design)]
    # Where things actually are. `plan_digest` carries no coordinates at all --
    # walls are "vertical 14.40 m" with no position, openings are a fraction
    # along a wall id -- so every spatial judgement the model made was a guess,
    # including the compass direction that `move_wall_parallel` requires. This
    # is the half it was missing.
    st = doc.design.active
    if st is not None and st.rooms:
        parts += ["", spatial_block(st)]
    parts += ["", brief_digest(doc.design)]
    if _stale(doc):
        parts += ["", "The brief has changed since the geometry was solved. "
                      "The plan above is the OLD layout. Call `solve_layout` "
                      "to bring it in line, or say why you are not going to."]
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


def plan_image_message(plan: Optional[Plan]) -> Optional[dict]:
    """The drawing, as its own user-role message.

    It cannot go in the operator message with the rest of the state: the API
    rejects an image there -- "role 'system' supports text, tool_addition, and
    tool_removal blocks only". That turns out to be the right split anyway.
    The text state is authoritative and lives in the operator channel where a
    user message cannot forge it; the picture is a *rendering* of that state,
    so a lower-authority channel is where it belongs, and the prompt already
    tells the model to trust the text over its reading of the image wherever
    precision matters.
    """
    png = plan_png(plan) if plan is not None else None
    if png is None:
        return None
    return {
        "role": "user",
        "content": [
            {"type": "text",
             "text": "The current plan as drawn -- this is what the person "
                     "you are talking to is looking at. The north arrow is on "
                     "the sheet."},
            {"type": "image",
             "source": {"type": "base64", "media_type": "image/png",
                        "data": base64.standard_b64encode(png).decode("ascii")}},
        ],
    }


# --------------------------------------------------------------------------
# tools
# --------------------------------------------------------------------------

# Every JSON type, for a parameter whose shape genuinely varies by command.
ANY_TYPE: dict[str, Any] = {
    "type": ["string", "number", "boolean", "object", "array", "null"]
}

# Per-parameter shapes for the flat union.
#
# The schema is one flat property bag across 40 commands rather than a 40-way
# `oneOf`, for the reason `_symbolic_command_schema` documents: strict tool use
# compiles the schema to a grammar and caps unions at 16. But flattening the
# *structure* did not require flattening the *types*, and collapsing every
# parameter to "any of the six JSON types" left the model guessing -- it sent
# `setbacks_mm: 1200` where a per-side mapping was wanted, got a raw
# `TypeError` back, and spent a turn recovering. A name is listed here only
# when it means the same kind of thing on every command that takes it.
PARAM_TYPES: dict[str, dict[str, Any]] = {
    "setbacks_mm": {
        "type": "object",
        "description": "Millimetres per side, e.g. "
                       "{\"front\": 1500, \"rear\": 1000, \"left\": 900, "
                       "\"right\": 900}. Not a single number.",
        "additionalProperties": {"type": "integer"},
    },
    "north_deg": {"type": "number",
                  "description": "Bearing of the +Y axis, degrees clockwise "
                                 "from north."},
    "width_ft": {"type": "number", "description": "Plot frontage in feet."},
    "depth_ft": {"type": "number", "description": "Plot depth in feet."},
    "carpet_sqft": {"type": "number"},
    "bedrooms": {"type": "integer", "minimum": 1, "maximum": 6},
    "baths": {"type": "integer", "minimum": 0, "maximum": 6},
    "storeys": {"type": "integer", "minimum": 1, "maximum": 4},
    "min_sqft": {"type": "number"},
    "max_sqft": {"type": "number"},
    "min_aspect": {"type": "number"},
    "max_aspect": {"type": "number"},
    # 1..5, not a word. The applier accepts "high" as well because a model
    # reaches for it, but the schema should ask for the number.
    "priority": {"type": "integer", "minimum": 1, "maximum": 5,
                 "description": "1 = must have, 5 = nice to have."},
    "width_mm": {"type": "integer"},
    "height_mm": {"type": "integer"},
    "thickness_mm": {"type": "integer"},
    "sill_mm": {"type": "integer"},
    "head_mm": {"type": "integer"},
    "depth_mm": {"type": "integer"},
    "distance_mm": {"type": "integer"},
    "riser_count": {"type": "integer"},
    "rotation": {"type": "number"},
    "opacity": {"type": "number", "minimum": 0, "maximum": 1},
    "corner_plot": {"type": "boolean"},
    "optional": {"type": "boolean"},
    "attached_bath": {"type": "boolean"},
    "via_foyer": {"type": "boolean"},
    "avoid_direct_kitchen_view": {"type": "boolean"},
    "locked": {"type": "boolean"},
    "flip_side": {"type": "boolean"},
    "pooja": {"type": "boolean"},
    "utility": {"type": "boolean"},
    "sit_out": {"type": "boolean"},
    "parking": {"type": "boolean"},
    "dining": {"type": "boolean"},
    "study": {"type": "boolean"},
    "store": {"type": "boolean"},
    # Ids and free text.
    "room_id": {"type": "string"}, "wall_id": {"type": "string"},
    "opening_id": {"type": "string"}, "furniture_id": {"type": "string"},
    "stair_id": {"type": "string"}, "column_id": {"type": "string"},
    "element_id": {"type": "string"}, "storey_id": {"type": "string"},
    "catalog_id": {"type": "string"}, "def_id": {"type": "string"},
    "name": {"type": "string"}, "reason": {"type": "string"},
    "text": {"type": "string"}, "category": {"type": "string"},
    "value": ANY_TYPE,
    # `at` is a fraction along a wall OR a position word, and `direction` is a
    # compass bearing on a wall and up/down on a stair, so both stay open.
    "at": ANY_TYPE, "direction": ANY_TYPE,
    # Vocabularies the applier checks but the schema had no way to state.
    "side": {"type": "string", "enum": ["north", "east", "south", "west"]},
    "road_facing": {"type": "string",
                    "enum": ["north", "east", "south", "west"]},
    "facing": {"type": "string", "enum": ["north", "east", "south", "west"]},
    "site_kind": {"type": "string", "enum": ["plot", "apartment_unit"]},
    "kind": {"type": "string", "enum": ["required", "prohibited"]},
    "relation": {"type": "string",
                 "enum": ["adjacent", "direct_access", "visual", "same_floor"]},
    "preferred_zone": {"type": "string",
                       "enum": ["N", "NE", "E", "SE", "S", "SW", "W", "NW", "C"]},
    "zone": {"type": "string",
             "enum": ["N", "NE", "E", "SE", "S", "SW", "W", "NW", "C"]},
    "a": {"type": "string",
          "description": "A room id from the brief, or a room category."},
    "b": {"type": "string",
          "description": "A room id from the brief, or a room category."},
    "start_ref": {"type": "string",
                  "description": "An existing wall endpoint or point along it: "
                                 "'w3:start', 'w3:end', 'w3@0.5'. There is no "
                                 "way to reference the site or the envelope -- "
                                 "on an empty floor use solve_layout instead "
                                 "of trying to draw the first wall."},
    "end_ref": {"type": "string", "description": "As start_ref."},
    "storey": {"type": "integer", "minimum": 0,
               "description": "Which floor this room belongs on; 0 is ground."},
}

# Enum parameters, taken from the table the applier validates against so the
# two cannot disagree. `direction` is excluded because it means a compass
# bearing on a wall and up/down on a stair, which one enum cannot say.
PARAM_TYPES.update({
    key: {"type": "string", "enum": list(allowed)}
    for key, allowed in _ENUM_PARAMS.items()
    if key != "direction" and key not in PARAM_TYPES
})


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
            params.setdefault(key, dict(PARAM_TYPES.get(key, ANY_TYPE)))
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
        "name": "solve_layout",
        "description": "Compile the brief into geometry: run the layout "
                       "solver and replace the floor with the result. This is "
                       "how a plan is created from nothing and how a brief "
                       "edit becomes visible. Anything the brief does not say "
                       "is assumed from Indian norms and reported back to you "
                       "-- relay those assumptions to the user, they cannot "
                       "see them. Slow (seconds), so do not call it after "
                       "every single edit; batch the brief changes, then "
                       "solve once.",
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "One line for the change feed, e.g. "
                                   "\"Lay out the 2BHK on the 30x40 plot\".",
                },
            },
        },
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
    if name == "solve_layout":
        from .generate import build, report
        r = build(ctx.doc, reason=str(args.get("reason") or ""))
        if r.event is not None:
            ctx.events.append(r.event)
        return report(r)
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
        "text": SYSTEM.format(catalogue=catalogue(SYMBOLIC),
                              principles=prompt_block()),
        # The prefix is frozen and the catalogue is generated from a sorted
        # table, so this is byte-stable across turns and processes.
        "cache_control": {"type": "ephemeral"},
    }]

    transcript.append({"role": "user", "content": message})
    # Operator channel: state cannot be forged from a user message, and the
    # cached prefix survives.
    # The drawing first, then the state. The operator message has to be last:
    # "role 'system' must precede an 'assistant' message or end the array".
    # Which also puts the authoritative text after the picture, so where the
    # two disagree the model reads the correction second.
    img = plan_image_message(doc.design.active)
    if img is not None:
        transcript.append(img)
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
        "text": SYSTEM.format(catalogue=catalogue(SYMBOLIC),
                              principles=prompt_block()),
        "cache_control": {"type": "ephemeral"},
    }]

    transcript.append({"role": "user", "content": message})
    # The drawing first, then the state. The operator message has to be last:
    # "role 'system' must precede an 'assistant' message or end the array".
    # Which also puts the authoritative text after the picture, so where the
    # two disagree the model reads the correction second.
    img = plan_image_message(doc.design.active)
    if img is not None:
        transcript.append(img)
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
