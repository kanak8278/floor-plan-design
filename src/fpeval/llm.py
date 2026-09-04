"""The LLM layer: prompt -> DesignSpec, and findings -> symbolic patch.

Two operations, one rule.

The rule (DECISIONS.md #6): **the model emits specifications and symbolic
operations; it never emits coordinates.** Numeric constraint satisfaction is the
single thing language models are worst at and the single thing that makes a
floor plan buildable, so it belongs to CP-SAT. Everything in this module is
built to make coordinate emission *impossible*, not merely discouraged:

  * `extract_spec` is constrained by `DesignSpec.to_json_schema()`, which has no
    coordinate fields at all.
  * `PatchOp` has a closed op vocabulary. Geometry-level ops take symbolic
    references (`"w12:start"`, `"w12@0.5"`), compass directions, and deltas
    sourced from a validator finding. `PatchOp.validate()` actively rejects any
    parameter that looks like an absolute point.

Every call here is retryable, validates its own output against the schema before
returning, and logs token usage.

Model choice
------------
`MODEL_REASONING` (opus) for spec extraction and patch proposal -- both are
judgement calls where a wrong answer is expensive downstream. `MODEL_BULK`
(sonnet) for high-volume paraphrase/labelling work (see `brief.py`).
"""
from __future__ import annotations

import copy
import json
import math
import os
import random
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Iterable, Optional, Protocol, Sequence, runtime_checkable

from .spec import (
    ADJACENCY_KINDS, ADJACENCY_RELATIONS, CITY_PROFILES, ROOM_CATEGORIES, SIDES,
    STYLE_PACKS, VASTU_STRICTNESS, WET_GROUPING, ZONES,
    Adjacency, DesignSpec, EntranceSpec, RoomSpec, MM2_PER_SQFT,
)

MODEL_REASONING = "claude-opus-5"
MODEL_BULK = "claude-sonnet-5"
# Brief -> DesignSpec on the plan-generation path. Sonnet, because the job is
# structured transcription of a stated brief into a fixed schema, not open-ended
# reasoning: the geometry is CP-SAT's and the vocabulary is `spec.py`'s.
MODEL_GENERATE = "claude-sonnet-5"

# USD per 1M tokens (input, output). Cache reads are 0.1x input.
PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
}


# ==========================================================================
# Validator seam (owned by another agent -- Protocol + stub only)
# ==========================================================================

@dataclass
class Finding:
    """One validator complaint. This is the input to the refinement loop."""
    id: str
    severity: str                    # error | warn | info
    code: str                        # e.g. "NBC.ROOM_MIN_WIDTH", "VASTU.POOJA_ZONE"
    message: str
    refs: list[str] = field(default_factory=list)   # room/wall/opening ids
    suggestion: str = ""             # validator's own hint, if any

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Finding":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@runtime_checkable
class Validator(Protocol):
    """Thin seam. The real implementation lives in rules.py / bylaws.py."""

    def check(self, plan: Any, spec: DesignSpec) -> list[Finding]:  # pragma: no cover
        ...


class NullValidator:
    """Stub so this module is testable without the validator agent's code."""

    def check(self, plan: Any, spec: DesignSpec) -> list[Finding]:
        return []


# ==========================================================================
# Usage accounting
# ==========================================================================

@dataclass
class CallRecord:
    op: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    attempts: int = 1
    ok: bool = True
    seconds: float = 0.0

    @property
    def cost_usd(self) -> float:
        cin, cout = PRICES.get(self.model, (5.0, 25.0))
        return (
            self.input_tokens * cin / 1e6
            + self.cache_read_tokens * cin * 0.1 / 1e6
            + self.cache_write_tokens * cin * 1.25 / 1e6
            + self.output_tokens * cout / 1e6
        )


class UsageLog:
    """Append-only token/cost log. One per run; pass it into every call."""

    def __init__(self) -> None:
        self.records: list[CallRecord] = []

    def add(self, rec: CallRecord) -> CallRecord:
        self.records.append(rec)
        return rec

    def summary(self) -> dict:
        by_model: dict[str, dict[str, float]] = {}
        for r in self.records:
            m = by_model.setdefault(r.model, {"calls": 0, "in": 0, "out": 0,
                                              "cache_read": 0, "cost_usd": 0.0,
                                              "retries": 0, "failures": 0})
            m["calls"] += 1
            m["in"] += r.input_tokens
            m["out"] += r.output_tokens
            m["cache_read"] += r.cache_read_tokens
            m["cost_usd"] += r.cost_usd
            m["retries"] += r.attempts - 1
            m["failures"] += 0 if r.ok else 1
        return {
            "calls": len(self.records),
            "input_tokens": sum(r.input_tokens for r in self.records),
            "output_tokens": sum(r.output_tokens for r in self.records),
            "cache_read_tokens": sum(r.cache_read_tokens for r in self.records),
            "retries": sum(r.attempts - 1 for r in self.records),
            "failures": sum(0 if r.ok else 1 for r in self.records),
            "total_cost_usd": round(sum(r.cost_usd for r in self.records), 6),
            "wall_seconds": round(sum(r.seconds for r in self.records), 2),
            "by_model": {k: {kk: (round(vv, 6) if kk == "cost_usd" else int(vv))
                             for kk, vv in v.items()} for k, v in by_model.items()},
        }

    def __repr__(self) -> str:  # pragma: no cover
        return f"UsageLog({json.dumps(self.summary(), indent=2)})"


# ==========================================================================
# A minimal JSON Schema checker
# ==========================================================================
# Deliberately dependency-free and deliberately narrow: it validates exactly
# the constructs `to_json_schema()` emits (type, nullable unions, enum,
# required, additionalProperties, array items, nested objects). A model reply
# that fails this never reaches the solver.

def validate_against_schema(data: Any, schema: dict, path: str = "$") -> list[str]:
    errs: list[str] = []
    if "anyOf" in schema:
        branches = [validate_against_schema(data, s, path) for s in schema["anyOf"]]
        if any(not b for b in branches):
            return []
        return [f"{path}: matched no anyOf branch "
                f"({'; '.join(b[0] for b in branches if b)})"]
    types = schema.get("type")
    if isinstance(types, str):
        types = [types]

    def matches(t: str, v: Any) -> bool:
        if t == "null":
            return v is None
        if t == "boolean":
            return isinstance(v, bool)
        if t == "integer":
            return isinstance(v, int) and not isinstance(v, bool)
        if t == "number":
            return isinstance(v, (int, float)) and not isinstance(v, bool)
        if t == "string":
            return isinstance(v, str)
        if t == "array":
            return isinstance(v, list)
        if t == "object":
            return isinstance(v, dict)
        return True

    if types and not any(matches(t, data) for t in types):
        return [f"{path}: expected {'|'.join(types)}, got "
                f"{type(data).__name__} ({data!r:.60})"]

    if "enum" in schema and data not in schema["enum"]:
        errs.append(f"{path}: {data!r} not in enum {schema['enum']}")

    if isinstance(data, dict) and "object" in (types or ["object"]):
        props = schema.get("properties", {})
        for k in schema.get("required", []):
            if k not in data:
                errs.append(f"{path}: missing required key {k!r}")
        if schema.get("additionalProperties") is False:
            for k in data:
                if k not in props:
                    errs.append(f"{path}: unexpected key {k!r}")
        for k, v in data.items():
            if k in props:
                errs.extend(validate_against_schema(v, props[k], f"{path}.{k}"))

    if isinstance(data, list) and "items" in schema:
        for i, v in enumerate(data):
            errs.extend(validate_against_schema(v, schema["items"], f"{path}[{i}]"))

    return errs


# ==========================================================================
# Transport: one retryable, self-validating, metered tool call
# ==========================================================================

class LLMError(RuntimeError):
    pass


class LLMRefusal(LLMError):
    """The safety classifier declined the request (`stop_reason == "refusal"`).

    Measured: 1 of 20 neutral ResPlan-derived floor-plan briefs was declined
    with category "bio" on claude-sonnet-5 -- a plain false positive on text
    about bedrooms and bathrooms. It is stochastic, so it is retried; a caller
    evaluating a corpus should catch this and skip the item rather than treat it
    as a schema failure.
    """

    def __init__(self, message: str, category: Optional[str] = None) -> None:
        super().__init__(message)
        self.category = category


def get_client(**kwargs: Any):
    """Anthropic client. Credentials resolve from the environment."""
    import anthropic
    return anthropic.Anthropic(**kwargs)


def call_tool(
    *,
    system: str,
    user: str,
    tool_name: str,
    tool_description: str,
    schema: dict,
    model: str = MODEL_REASONING,
    op: str = "call_tool",
    client: Any = None,
    usage: Optional[UsageLog] = None,
    max_attempts: int = 3,
    max_tokens: int = 16000,
    effort: str = "high",
    strict: bool = True,
    temperature_hint: Optional[str] = None,
) -> dict:
    """Force one structured tool call and return its validated input dict.

    Retries on transport errors, on a malformed reply (no tool_use block), and
    on schema-validation failure -- in the last case the validation errors are
    fed back to the model, which is the cheapest possible repair loop.
    """
    import anthropic

    client = client or get_client()
    tool: dict[str, Any] = {
        "name": tool_name,
        "description": tool_description,
        "input_schema": schema,
    }
    if strict:
        tool["strict"] = True

    messages: list[dict] = [{"role": "user", "content": user}]
    t0 = time.time()
    in_tok = out_tok = cache_r = cache_w = 0
    last_err = "unknown"
    degraded = False
    refusal_cat: Optional[str] = None

    for attempt in range(1, max_attempts + 1):
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=[{"type": "text", "text": system,
                         "cache_control": {"type": "ephemeral"}}],
                messages=messages,
                tools=[tool],
                tool_choice={"type": "tool", "name": tool_name,
                             "disable_parallel_tool_use": True},
                output_config={"effort": effort},
            )
        except (anthropic.RateLimitError, anthropic.APITimeoutError,
                anthropic.APIConnectionError) as exc:
            last_err = f"{type(exc).__name__}: {exc}"
            if attempt == max_attempts:
                break
            time.sleep(min(2 ** attempt + random.random(), 30))
            continue
        except anthropic.APIStatusError as exc:
            last_err = f"{type(exc).__name__} {exc.status_code}: {exc}"
            msg = str(exc)
            # Strict tool use compiles the schema into a grammar and caps both
            # union-typed (16) and optional (24) parameter counts. A schema that
            # exceeds them is rejected outright; degrade to non-strict rather
            # than fail, since the local schema check plus the feedback retry
            # already enforce conformance.
            if (tool.get("strict") and not degraded
                    and ("optional parameters" in msg or "union types" in msg
                         or "grammar compilation" in msg
                         or "grammar is too large" in msg)):
                tool.pop("strict", None)
                degraded = True
                continue
            if exc.status_code >= 500 and attempt < max_attempts:
                time.sleep(min(2 ** attempt + random.random(), 30))
                continue
            break

        u = resp.usage
        in_tok += getattr(u, "input_tokens", 0) or 0
        out_tok += getattr(u, "output_tokens", 0) or 0
        cache_r += getattr(u, "cache_read_input_tokens", 0) or 0
        cache_w += getattr(u, "cache_creation_input_tokens", 0) or 0

        if resp.stop_reason == "refusal":
            cat = getattr(resp.stop_details, "category", None)
            last_err = f"refusal: {cat}"
            refusal_cat = cat
            if attempt < max_attempts:
                time.sleep(1.0 + random.random())
                continue
            break

        block = next((b for b in resp.content if b.type == "tool_use"
                      and b.name == tool_name), None)
        if block is None:
            last_err = f"no tool_use block (stop_reason={resp.stop_reason})"
            stray = [b for b in resp.content if b.type == "tool_use"]
            nudge: Any = f"You must call the {tool_name} tool. Do it now."
            if stray:
                nudge = [{"type": "tool_result", "tool_use_id": b.id,
                          "is_error": True,
                          "content": f"Wrong tool. Call {tool_name}."}
                         for b in stray]
            messages += [
                {"role": "assistant", "content": resp.content},
                {"role": "user", "content": nudge},
            ]
            if attempt < max_attempts:
                continue
            break

        # Tool inputs may be re-escaped by the model -- round-trip through json
        # rather than string-matching (see the SDK's parsing caveat).
        payload = json.loads(json.dumps(block.input))
        schema_errs = validate_against_schema(payload, schema)
        if not schema_errs:
            if usage is not None:
                usage.add(CallRecord(op=op, model=model, input_tokens=in_tok,
                                     output_tokens=out_tok,
                                     cache_read_tokens=cache_r,
                                     cache_write_tokens=cache_w,
                                     attempts=attempt, ok=True,
                                     seconds=time.time() - t0))
            return payload

        last_err = "schema: " + "; ".join(schema_errs[:6])
        # Every tool_use block in the turn needs a tool_result or the next
        # request 400s ("tool_use ids were found without tool_result blocks").
        # Measured failure mode: 1 call in ~50 emits two tool_use blocks even
        # under forced tool_choice, which is why the results are built from the
        # response rather than from `block` alone.
        results = [{
            "type": "tool_result",
            "tool_use_id": b.id,
            "is_error": True,
            "content": ("Your output failed schema validation:\n"
                        + "\n".join(f"- {e}" for e in schema_errs[:20])
                        + "\nCall the tool again with a conforming object.")
            if b.id == block.id else "Duplicate call ignored.",
        } for b in resp.content if b.type == "tool_use"]
        messages += [
            {"role": "assistant", "content": resp.content},
            {"role": "user", "content": results},
        ]
        if attempt == max_attempts:
            break

    if usage is not None:
        usage.add(CallRecord(op=op, model=model, input_tokens=in_tok,
                             output_tokens=out_tok, cache_read_tokens=cache_r,
                             cache_write_tokens=cache_w, attempts=max_attempts,
                             ok=False, seconds=time.time() - t0))
    if last_err.startswith("refusal"):
        raise LLMRefusal(
            f"{op} refused after {max_attempts} attempts: {last_err}", refusal_cat)
    raise LLMError(f"{op} failed after {max_attempts} attempts: {last_err}")


# ==========================================================================
# (a) extract_spec -- natural language -> DesignSpec
# ==========================================================================

EXTRACT_SYSTEM = """\
You convert an Indian residential client brief into a typed design specification.

You do NOT design the house. You do NOT emit any coordinates, dimensions in
millimetres, wall positions, or room positions. A CP-SAT solver owns all
geometry. Your only job is to state, precisely, what the client asked for.

First decide what kind of site this is:
- An independent house on land -> site_kind="plot", with plot_width_ft and
  plot_depth_ft in feet.
- A flat in a tower -> site_kind="apartment_unit". There is NO plot. Leave
  plot_width_ft and plot_depth_ft null and put the quoted area in unit_area
  instead. Do not invent a plot for an apartment: it would hand the solver a
  fictional site and make every setback and coverage rule meaningless.

Vocabulary you must read correctly:
- "30 by 40 site", "30x40", "30*40 site": plot 30 ft along the road x 40 ft deep.
  The FIRST number is the road-facing width. Never convert to metres.
- "east facing" / "east facing site": the ROAD is to the east. So
  road_facing_side="east" and the entrance is on the east side.
- "NBHK": N bedrooms PLUS a hall (category "living") PLUS a kitchen. A 3BHK
  programme therefore has at least 5 rooms before any extras. "BHK" never means
  N total rooms.
- "hall" = living room. "wash area" = utility. "sit-out"/"portico" = sit_out.
  "car park"/"car parking" = parking. "puja"/"pooja"/"mandir" = pooja.
  "attached bathroom"/"attached toilet" = attached_bath on that bedroom.
- "duplex" and "G+1" both mean storeys=2 (ground plus one). "G+2" is storeys=3.
  Any multi-storey programme needs a "staircase" room.
- "vastu compliant"/"as per vastu": set vastu.enabled=true, strictness="strict"
  when the client insists and "advisory" when they say "if possible"/"prefer".
  Populate vastu.requirements with the specific rules implied, using these
  tokens: pooja_northeast, kitchen_southeast, master_southwest,
  no_toilet_northeast, entrance_north, entrance_east, brahmasthan_clear,
  head_south_sleeping, water_northeast, staircase_southwest.
- Builder unit labels: "2BHK 2T TYPE C18", "3 BHK + 2 T - TYPE 3 G",
  "3 BHK + 3 TOILETS + STUDY", "4BHK + SR + ST + PDR", "3.5 BHK". Read them as:
  the "+ NT" / "N TOILETS" count is the number of bathrooms/toilets; SR =
  servant room, ST = store, PDR = powder room (category "powder"), PWD RM =
  powder room. "3.5 BHK" means 3 bedrooms plus a den or study too small to sell
  as a bedroom: set half_bhk=true and add a "study". Copy the label verbatim
  into unit_label.
- Other room labels that appear on real Indian plans: SITOUT (sit_out),
  UTILITY, PUJA/POOJA, FOYER, HANDWASH (handwash), TOI-1/TOI-2/TOI-3 (toilet),
  STUDY, BALCONY (two or three are normal), PATIO (patio), LANDSCAPE, PORCH
  (sit_out), PHE SHAFT / HVAC or VRV platform (category "shaft").
- The Indian area stack: SALEABLE > SUPER BUILT-UP > BUILT-UP > CARPET > RERA
  CARPET, plus BALCONY quoted separately. Put each figure the client gave into
  the matching unit_area field and set quoted_as to the one they led with. Never
  copy a saleable figure into carpet_sqft: the loading factor is 1.25-1.75, so
  they differ by up to 75%. Room areas in the programme are always CARPET.
- Dimensions may arrive dual-unit ("3.84m x 3.81m" and "12'7\"x12'6\"") or as
  feet-and-inches only. Convert to square feet for room areas; 12'6" = 12.5 ft.
- Lakh/crore budgets go verbatim into budget_band. Family descriptions go into
  family. Anything else the schema cannot hold goes into notes.

Hard rules:
1. NEVER invent a plot size. If the client did not state plot dimensions, set
   set plot_width_ft and plot_depth_ft to null and ASK for them with
   blocking=true. Guessing a plot size silently produces a plan for a plot that
   does not exist. The same applies to road_facing_side: null if unstated. But
   null means "the brief does not say" -- if the brief DOES say "east facing",
   fill it in. For an apartment unit, null plot dimensions are CORRECT and need
   no question; ask for the carpet area instead if no area was quoted.
1a. Facing direction is how Indian buyers filter first ("east facing 3BHK"), so
   treat it as a primary field, not an afterthought. For an apartment unit,
   road_facing_side means the main window/balcony orientation.
2. Only use categories from the enum. If the client wants something outside it
   (gym, home theatre, cellar), put the closest category and explain in notes.
3. Areas are square feet of CARPET area and are RANGES, not targets. If the
   client gives a size ("12x14 master"), centre a range on it (about +/-12%).
   If they give no size, use null for min_sqft/max_sqft and the category
   defaults apply.
4. Record adjacency the client actually asked for, plus prohibitions they imply
   ("toilet must not be next to the kitchen/pooja"). Do not pad the list with
   generic best practice.
5. The `rooms` array must NEVER be empty. Every brief implies at least a
   living space and a kitchen, and an empty programme is the single most common
   way this task is failed. If the brief is vague, emit your best-guess
   programme AND ask about it -- never emit nothing.
5a. Mark every room the brief did not actually mention with optional=true.
   Rule 5 is right that an empty programme is worse than a guess, but the
   solver treats a mandatory room as mandatory, so a generous guess becomes
   INFEASIBLE instead of a house. Measured: from "I have a 30x40 east facing
   site in Bengaluru. Need a 3BHK ground floor house" this produced eleven
   rooms -- adding a sitout, parking, foyer, dining, utility and pooja that
   nobody mentioned, all mandatory. The total area asked for was LOWER than
   the reference plan's, so it was not over-sizing: eleven rooms each carry an
   NBC minimum plus their own walls, and that does not fit 1200 sqft after
   setbacks. Five of ten suite prompts came back INFEASIBLE on cases that
   solve from ground truth.
   What "N BHK" names is mandatory: N bedrooms, the hall, the kitchen, and a
   bathroom. Anything the client said out loud is mandatory. Everything you
   added to round it out into a complete house is optional=true -- the solver
   will fit what it can and report what it dropped, which is a house plus a
   sentence rather than a refusal.
6. attached_bath applies to bedrooms only. A servant room with its own toilet
   is a separate "toilet" room, not attached_bath on the servant room.
7. Adjacency endpoints must be room ids from your own `rooms` array, or a bare
   category name. Never a compass zone, and never a room related to itself.
8. clarifying_questions are for things that materially change the plan and that
   you genuinely cannot infer. A fully specified brief gets an empty list. Do
   not ask about things you have already assumed -- put those in assumptions.
"""

EXTRACT_TOOL_DESC = (
    "Emit the typed design specification for this client brief, plus any "
    "clarifying questions and the assumptions you had to make."
)


SPEC_META_KEYS = ("clarifying_questions", "blocking_questions", "assumptions",
                  "underdetermined")


def extract_spec_tool_schema(strict: bool = False) -> dict:
    """Tool schema for spec extraction: the spec plus the questions it raises.

    `strict` defaults to False, and that is a measured decision, not laziness.
    Strict tool use compiles the schema into a grammar with three hard budgets
    (enum-plus-union rejected outright, >16 union-typed parameters rejected,
    and a total grammar size cap). Probed against the real API:

      * the bare spec schema with nullable unions -- ACCEPTED, at the size cap
      * the same schema nested under a "spec" key -- 400 "grammar is too large"
      * flat-merged with the four meta fields     -- 400, same error
      * union-free (absent-key optionality)       -- ACCEPTED

    So strict is only reachable by giving up explicit nulls, and giving those up
    cost real accuracy: on the 28-prompt suite the union-free encoding dropped
    `road_facing_side` on 9 of 27 briefs that stated it, because omitting an
    optional key is easier for the model than filling a required nullable one.

    Non-strict keeps the better encoding and loses nothing measurable: the reply
    is checked against this schema locally, a failure is fed back as a
    tool_result error, and the retry fixes it -- 100% conformance on the same
    suite. `strict=True` is kept for the day the limits rise, and `call_tool`
    degrades automatically if the API refuses it.
    """
    spec_schema = DesignSpec.to_json_schema(strict=True)
    props = {
        "spec": spec_schema,
        "clarifying_questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "blocking": {
                        "type": "boolean",
                        "description": "True ONLY if the solver cannot run "
                                       "without the answer (e.g. no plot size "
                                       "on a plot-based house). A blocking "
                                       "question halts the pipeline. False for "
                                       "questions that would merely improve "
                                       "the design.",
                    },
                },
                "required": ["question", "blocking"],
                "additionalProperties": False,
            },
            "description": "Empty if the brief is fully determined.",
        },
        "assumptions": {
            "type": "array", "items": {"type": "string"},
            "description": "Defaults you applied that the client did not state.",
        },
        "underdetermined": {
            "type": "boolean",
            "description": "True if the brief lacks information the solver needs.",
        },
    }
    return {
        "type": "object",
        "properties": props,
        "required": sorted(props),
        "additionalProperties": False,
    }


MISSING_PLOT_QUESTION = (
    "What are the plot dimensions in feet (width along the road x depth)?"
)
MISSING_FACING_QUESTION = (
    "Which direction does the plot face, i.e. which side is the road on?"
)


def extract_spec(
    prompt: str,
    *,
    model: str = MODEL_GENERATE,
    client: Any = None,
    usage: Optional[UsageLog] = None,
    max_attempts: int = 3,
    strict: bool = False,      # see extract_spec_tool_schema for why
    enforce_questions: bool = True,
    effort: str = "high",
) -> tuple[DesignSpec, list[str]]:
    """Natural-language brief -> (DesignSpec, clarifying questions).

    The returned questions are every question the model asked. The subset that
    genuinely halts the pipeline is in `spec.provenance["blocking_questions"]`;
    the split matters because a model that asks six polite questions about
    budget on a fully determined brief is not the same failure as one that
    cannot proceed.

    The spec may be structurally invalid (see `DesignSpec.validate()`) -- that
    is the correct outcome for an underdetermined brief, and the questions tell
    you what to ask. `enforce_questions` guarantees that a spec missing plot
    dimensions is accompanied by a question even if the model forgot to ask;
    when it has to do that it records the fact in
    `spec.provenance["contract_violations"]` so evaluation still sees the truth.
    """
    schema = extract_spec_tool_schema(strict=strict)
    user = f"Client brief:\n\n{prompt.strip()}"
    violations: list[str] = []

    # Measured defect: roughly 1 call in 24 returns a schema-valid spec with an
    # EMPTY room programme -- useless but not catchable by the schema. One
    # semantic retry with the defect named fixes it; the violation is recorded
    # either way so evaluation still sees it.
    for semantic_attempt in range(2):
        payload = call_tool(
            system=EXTRACT_SYSTEM,
            user=user,
            tool_name="emit_design_spec",
            tool_description=EXTRACT_TOOL_DESC,
            schema=schema,
            model=model,
            op="extract_spec",
            client=client,
            usage=usage,
            max_attempts=max_attempts,
            strict=strict,
            effort=effort,
        )
        spec = DesignSpec.from_dict(payload.get("spec") or {})
        if spec.rooms:
            break
        violations.append("empty room programme returned")
        user = (f"Client brief:\n\n{prompt.strip()}\n\n"
                "Your previous attempt returned an EMPTY room programme. Every "
                "brief implies at least a living space and a kitchen. Emit the "
                "full programme this time.")

    questions: list[str] = []
    blocking: list[str] = []
    for q in payload.get("clarifying_questions") or []:
        if isinstance(q, dict):
            text, is_blocking = str(q.get("question") or ""), bool(q.get("blocking"))
        else:
            text, is_blocking = str(q), True
        if not text.strip():
            continue
        questions.append(text.strip())
        if is_blocking:
            blocking.append(text.strip())

    if spec.plot_width_ft is None or spec.plot_depth_ft is None:
        if not blocking:
            violations.append("plot dimensions null but no blocking question asked")
            if enforce_questions:
                questions.append(MISSING_PLOT_QUESTION)
                blocking.append(MISSING_PLOT_QUESTION)
    if spec.road_facing_side is None and not questions and enforce_questions:
        questions.append(MISSING_FACING_QUESTION)
        blocking.append(MISSING_FACING_QUESTION)

    spec.provenance = {
        "source": "llm.extract_spec",
        "model": model,
        "prompt": prompt.strip(),
        "assumptions": payload.get("assumptions") or [],
        "underdetermined": bool(payload.get("underdetermined")),
        "clarifying_questions": questions,
        "blocking_questions": blocking,
        "contract_violations": violations,
    }
    return spec, questions


# ==========================================================================
# (b) The symbolic patch protocol
# ==========================================================================
# Two levels, and the distinction is load-bearing:
#
#   level="spec"     -- change the brief, then RE-SOLVE. Preferred, always. The
#                       solver re-establishes global consistency; a spec patch
#                       cannot produce an invalid plan, only an unsatisfiable
#                       one, which is a legible failure.
#   level="geometry"  -- mutate the plan directly through the editor's own
#                       mutation API. Fast, surgical, and dangerous: it can
#                       break constraints the solver had satisfied. Use only for
#                       local repairs a validator finding pinpoints.
#
# Geometry ops mirror OpenPlan3D's store mutations one-for-one so an AI edit is
# literally the same operation as a mouse edit, and a batch wrapped in
# beginUndoGroup()/endUndoGroup(description) collapses into one Ctrl-Z.

LEVEL_SPEC = "spec"
LEVEL_GEOMETRY = "geometry"
LEVEL_FURNITURE = "furniture"

DOOR_TYPES = ("single", "double", "sliding", "french", "pocket", "bifold",
              "opening", "garage")
WINDOW_TYPES = ("standard", "fixed", "casement", "sliding", "bay")
POSITION_WORDS = ("start", "quarter", "centre", "three_quarter", "end")
COMPASS_MOVE = ("north", "north_east", "east", "south_east",
                "south", "south_west", "west", "north_west")
EDITOR_ROOM_TYPES = ("indoor", "outdoor", "garage", "utility")

# op -> (level, required params, optional params, editor function)
OP_TABLE: dict[str, dict[str, Any]] = {
    # ---- spec level (preferred) -----------------------------------------
    "set_room_area": {
        "level": LEVEL_SPEC, "required": ("room_id", "min_sqft", "max_sqft"),
        "optional": (), "editor": None,
        "doc": "Widen or shift a room's target area range, then re-solve.",
    },
    "set_room_aspect": {
        "level": LEVEL_SPEC, "required": ("room_id", "max_aspect"),
        "optional": ("min_aspect",), "editor": None,
        "doc": "Relax or tighten a room's long/short ratio limit.",
    },
    "set_room_zone": {
        "level": LEVEL_SPEC, "required": ("room_id", "preferred_zone"),
        "optional": (), "editor": None,
        "doc": "Move a room's preferred compass zone (e.g. pooja to NE).",
    },
    "set_room_priority": {
        "level": LEVEL_SPEC, "required": ("room_id", "priority"),
        "optional": (), "editor": None,
        "doc": "Change how hard the solver fights for this room.",
    },
    "add_room": {
        "level": LEVEL_SPEC, "required": ("room_id", "category"),
        "optional": ("name", "min_sqft", "max_sqft", "max_aspect", "priority",
                     "optional", "attached_bath", "preferred_zone", "storey"),
        "editor": None, "doc": "Add a programme entry, then re-solve.",
    },
    "remove_room": {
        "level": LEVEL_SPEC, "required": ("room_id",), "optional": (),
        "editor": None, "doc": "Drop a programme entry, then re-solve.",
    },
    "set_adjacency": {
        "level": LEVEL_SPEC, "required": ("a", "b", "kind"),
        "optional": ("relation", "reason"), "editor": None,
        "doc": "Add or overwrite an adjacency requirement/prohibition.",
    },
    "remove_adjacency": {
        "level": LEVEL_SPEC, "required": ("a", "b"), "optional": ("relation",),
        "editor": None, "doc": "Drop an adjacency constraint that cannot be met.",
    },
    "set_entrance": {
        "level": LEVEL_SPEC, "required": (),
        "optional": ("side", "zone", "via_foyer", "avoid_direct_kitchen_view"),
        "editor": None, "doc": "Change where the front door is, then re-solve.",
    },
    "set_wet_grouping": {
        "level": LEVEL_SPEC, "required": ("value",), "optional": (),
        "editor": None, "doc": "Change wet-room grouping preference.",
    },
    "set_storeys": {
        "level": LEVEL_SPEC, "required": ("value",), "optional": (),
        "editor": None, "doc": "Change the storey count, then re-solve.",
    },
    # ---- furniture level ------------------------------------------------
    # Symbolic placement only. "Add a table to the centre of the hall" is
    # expressible; "put it at x=3200,y=1850" is not, and `_BANNED_PARAM_KEYS`
    # already rejects `centre` as a coordinate word -- so the anchor vocabulary
    # below IS the way to say it. Anchors and zone names are furnish.py's, so the
    # relational placement solver resolves them against the real room polygon,
    # door swings and clearances.
    "place_item": {
        "level": LEVEL_FURNITURE,
        "required": ("room_id", "item", "anchor"),
        "optional": ("prefer", "avoid", "of", "side", "count", "align",
                     "clear_front_mm", "gap_mm", "abut", "avoid_window", "note"),
        "editor": "addFurniture",
        "doc": "place_item(room_id, item, anchor) -- anchor is one of "
               "wall|corner|center|beside|facing|front_of|around|run. `prefer` "
               "takes compass zones (SE, NE, ...). `of` names another item for "
               "beside/facing/front_of. Coordinates are resolved by the solver.",
    },
    "remove_item": {
        "level": LEVEL_FURNITURE, "required": ("item_id",), "optional": (),
        "editor": "removeFurniture", "doc": "Remove one placed item.",
    },
    "move_item": {
        "level": LEVEL_FURNITURE, "required": ("item_id", "anchor"),
        "optional": ("prefer", "of", "side", "align", "clear_front_mm"),
        "editor": "moveFurniture",
        "doc": "Re-anchor an item symbolically; the solver recomputes where.",
    },
    "replace_item": {
        "level": LEVEL_FURNITURE, "required": ("item_id", "item"), "optional": (),
        "editor": "updateFurniture", "doc": "Swap one catalogue item for another.",
    },
    "furnish_room": {
        "level": LEVEL_FURNITURE, "required": ("room_id",),
        "optional": ("density", "add", "drop", "swap"),
        "editor": None,
        "doc": "Run the rule-based furnisher for one room. `density` is "
               "sparse|normal|full; add/drop/swap patch the rule table.",
    },
    "set_kitchen_layout": {
        "level": LEVEL_FURNITURE, "required": ("room_id",),
        "optional": ("run", "hob_zone", "sink_zone", "fridge_zone",
                     "breakfast_counter"),
        "editor": None,
        "doc": "set_kitchen_layout(room_id, run='L'|'U'|'I', hob_zone='SE', "
               "sink_zone='NE') -- the counter run and appliance zones. The "
               "600 mm hob/sink separation is enforced by the placer.",
    },
    # ---- geometry level (mirrors OpenPlan3D's project store) -------------
    "update_wall": {
        "level": LEVEL_GEOMETRY, "required": ("wall_id",),
        "optional": ("thickness_mm", "height_mm"), "editor": "updateWall",
        "doc": "updateWall(id, updates) -- thickness/height only.",
    },
    "move_wall_parallel": {
        "level": LEVEL_GEOMETRY, "required": ("wall_id", "direction", "distance_mm"),
        "optional": (), "editor": "moveWallParallel",
        "doc": "moveWallParallel(id, dx, dy) -- compass direction plus a "
               "magnitude taken from a finding; never absolute coordinates.",
    },
    "split_wall": {
        "level": LEVEL_GEOMETRY, "required": ("wall_id", "at"), "optional": (),
        "editor": "splitWall", "doc": "splitWall(id, t) -- t is parametric 0..1.",
    },
    "add_wall": {
        "level": LEVEL_GEOMETRY, "required": ("start_ref", "end_ref"),
        "optional": ("thickness_mm",), "editor": "addWall",
        "doc": "addWall(start, end) -- endpoints given as symbolic refs "
               "('w7:start', 'w7@0.5') resolved by the applier.",
    },
    "add_door": {
        "level": LEVEL_GEOMETRY, "required": ("wall_id", "position"),
        # `type` as well as `door_type`: `add_window` names it `type`, and one
        # vocabulary for two sibling ops is not worth the inconsistency.
        "optional": ("door_type", "type", "width_mm"), "editor": "addDoor",
        "doc": "addDoor(wallId, position, doorType) -- single|double|sliding|"
               "french|pocket|bifold|opening|garage.",
    },
    "add_window": {
        "level": LEVEL_GEOMETRY, "required": ("wall_id", "position"),
        "optional": ("type", "width_mm", "height_mm", "sill_mm"),
        "editor": "addWindow",
        "doc": "addWindow(wallId, position, type) -- standard|fixed|casement|"
               "sliding|bay. There was no window op at all before this.",
    },
    "update_window": {
        "level": LEVEL_GEOMETRY, "required": ("window_id",),
        "optional": ("width_mm", "height_mm", "sill_mm", "position"),
        "editor": "updateWindow", "doc": "updateWindow(id, updates).",
    },
    "update_door": {
        "level": LEVEL_GEOMETRY, "required": ("door_id",),
        "optional": ("width_mm", "door_type", "swing_direction", "flip_side",
                     "position"),
        "editor": "updateDoor", "doc": "updateDoor(id, updates).",
    },
    "update_room": {
        "level": LEVEL_GEOMETRY, "required": ("room_id",),
        # `category` is OUR taxonomy (bedroom, study, pooja, ...); `room_type`
        # is OpenPlan3D's four-way RoomCategory (indoor/outdoor/garage/utility)
        # and cannot express "make this bedroom a study". Only `room_type` was
        # declared, so relabelling a room's FUNCTION -- the thing a client
        # actually asks for -- was not expressible, even though the applier
        # already handled it.
        "optional": ("name", "category", "room_type"), "editor": "updateRoom",
        "doc": "updateRoom(id, updates) -- `category` retypes the room in our "
               "taxonomy, `room_type` sets the editor's indoor/outdoor class. "
               "Label and type only, never geometry.",
    },
    "remove_element": {
        "level": LEVEL_GEOMETRY, "required": ("element_id",), "optional": (),
        "editor": "removeElement",
        "doc": "removeElement(id) -- cascades doors/windows for a wall.",
    },
}

ANCHORS = ("wall", "corner", "center", "beside", "facing", "front_of",
           "around", "run")
ZONES = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
DENSITIES = ("sparse", "normal", "full")
KITCHEN_RUNS = ("I", "L", "U")

SPEC_OPS = tuple(k for k, v in OP_TABLE.items() if v["level"] == LEVEL_SPEC)
FURNITURE_OPS = tuple(k for k, v in OP_TABLE.items() if v["level"] == LEVEL_FURNITURE)
GEOMETRY_OPS = tuple(k for k, v in OP_TABLE.items() if v["level"] == LEVEL_GEOMETRY)

# Anything smelling of an absolute coordinate. This is the enforcement point
# for "the LLM never emits coordinates".
_BANNED_PARAM_KEYS = frozenset({
    "x", "y", "z", "x1", "y1", "x2", "y2", "cx", "cy", "start", "end",
    "start_x", "start_y", "end_x", "end_y", "point", "points", "polygon",
    "position_mm", "origin", "centre", "center", "coords", "coordinates",
    "dx", "dy", "left", "top", "bbox",
})


def _ref_ok(ref: Any) -> bool:
    """Symbolic endpoint reference: 'w12:start', 'w12:end', or 'w12@0.5'."""
    if not isinstance(ref, str) or not ref:
        return False
    if ":" in ref:
        wid, end = ref.split(":", 1)
        return bool(wid) and end in ("start", "end")
    if "@" in ref:
        wid, t = ref.split("@", 1)
        try:
            return bool(wid) and 0.0 <= float(t) <= 1.0
        except ValueError:
            return False
    return False


def _position_ok(v: Any) -> bool:
    if isinstance(v, str):
        return v in POSITION_WORDS
    if isinstance(v, bool):
        return False
    return isinstance(v, (int, float)) and 0.0 <= float(v) <= 1.0


def _position_t(v: Any) -> float:
    if isinstance(v, str):
        return {"start": 0.1, "quarter": 0.25, "centre": 0.5,
                "three_quarter": 0.75, "end": 0.9}[v]
    return float(v)


@dataclass
class PatchOp:
    """One independently validatable symbolic operation.

    Independently validatable is the point: a batch of six ops where two are
    nonsense yields four applied and two rejected with reasons, instead of a
    whole-batch failure or -- far worse -- a silently half-applied edit.
    """
    op: str
    params: dict = field(default_factory=dict)
    description: str = ""
    level: str = ""
    finding_ids: list[str] = field(default_factory=list)
    confidence: float = 0.5
    rationale: str = ""

    def __post_init__(self) -> None:
        entry = OP_TABLE.get(self.op)
        if entry and not self.level:
            self.level = entry["level"]

    # ------------------------------------------------------------ validation
    def validate(self, spec: Optional[DesignSpec] = None,
                 plan: Any = None) -> list[str]:
        errs: list[str] = []
        entry = OP_TABLE.get(self.op)
        if entry is None:
            return [f"unknown op {self.op!r}; allowed: {sorted(OP_TABLE)}"]
        if self.level and self.level != entry["level"]:
            errs.append(f"{self.op}: level={self.level!r} but this op is "
                        f"{entry['level']!r}")
        if not isinstance(self.params, dict):
            return [f"{self.op}: params must be an object"]

        # The rule, enforced.
        for k in self.params:
            if k.lower() in _BANNED_PARAM_KEYS:
                errs.append(f"{self.op}: parameter {k!r} is an absolute "
                            "coordinate; the LLM must not emit geometry")
        for v in self.params.values():
            if isinstance(v, dict) and {"x", "y"} & set(v):
                errs.append(f"{self.op}: nested point literal in params")

        allowed = set(entry["required"]) | set(entry["optional"])
        for k in entry["required"]:
            if self.params.get(k) is None:
                errs.append(f"{self.op}: missing required param {k!r}")
        for k in self.params:
            if k not in allowed:
                errs.append(f"{self.op}: unexpected param {k!r} "
                            f"(allowed: {sorted(allowed)})")
        if not entry["required"] and not self.params:
            errs.append(f"{self.op}: no parameters given, so it does nothing")
        if not self.description.strip():
            errs.append(f"{self.op}: description is required -- the user must be "
                        "shown a readable diff of what changed")
        if not (0.0 <= self.confidence <= 1.0):
            errs.append(f"{self.op}: confidence={self.confidence} outside 0..1")

        p = self.params
        # -- per-op semantics ---------------------------------------------
        if self.op in ("set_room_area",):
            lo, hi = p.get("min_sqft"), p.get("max_sqft")
            if isinstance(lo, (int, float)) and lo <= 0:
                errs.append(f"{self.op}: min_sqft must be positive")
            if isinstance(lo, (int, float)) and isinstance(hi, (int, float)) and hi < lo:
                errs.append(f"{self.op}: max_sqft {hi} < min_sqft {lo}")
        if self.op == "set_room_aspect":
            mn = p.get("min_aspect", 1.0)
            mx = p.get("max_aspect")
            if isinstance(mn, (int, float)) and mn < 1.0:
                errs.append(f"{self.op}: min_aspect must be >= 1.0")
            if isinstance(mx, (int, float)) and isinstance(mn, (int, float)) and mx < mn:
                errs.append(f"{self.op}: max_aspect < min_aspect")
        if "preferred_zone" in p and p["preferred_zone"] not in ZONES:
            errs.append(f"{self.op}: preferred_zone={p['preferred_zone']!r} "
                        f"not in {ZONES}")
        if "category" in p and p["category"] not in ROOM_CATEGORIES:
            errs.append(f"{self.op}: unknown category {p['category']!r}")
        if "priority" in p and p["priority"] not in (1, 2, 3, 4, 5):
            errs.append(f"{self.op}: priority must be 1..5")
        if "kind" in p and p["kind"] not in ADJACENCY_KINDS:
            errs.append(f"{self.op}: kind must be one of {ADJACENCY_KINDS}")
        if "relation" in p and p["relation"] not in ADJACENCY_RELATIONS:
            errs.append(f"{self.op}: relation must be one of {ADJACENCY_RELATIONS}")
        if self.op == "set_entrance":
            if p.get("side") is not None and p["side"] not in SIDES:
                errs.append(f"{self.op}: side must be one of {SIDES}")
            if p.get("zone") is not None and p["zone"] not in ZONES:
                errs.append(f"{self.op}: zone must be one of {ZONES}")
        if self.op == "set_wet_grouping" and p.get("value") not in WET_GROUPING:
            errs.append(f"{self.op}: value must be one of {WET_GROUPING}")
        if self.op == "set_storeys":
            v = p.get("value")
            if not isinstance(v, int) or isinstance(v, bool) or not (1 <= v <= 4):
                errs.append(f"{self.op}: value must be an integer 1..4")
        if self.op == "move_wall_parallel":
            if p.get("direction") not in COMPASS_MOVE:
                errs.append(f"{self.op}: direction must be one of {COMPASS_MOVE}")
            d = p.get("distance_mm")
            if not isinstance(d, (int, float)) or isinstance(d, bool):
                errs.append(f"{self.op}: distance_mm must be a number")
            elif not (10 <= abs(d) <= 5000):
                errs.append(f"{self.op}: distance_mm={d} outside 10..5000 mm; a "
                            "repair this large is a re-solve, not a nudge")
            if not self.finding_ids:
                errs.append(f"{self.op}: geometry nudges must cite the finding "
                            "they repair (finding_ids)")
        if self.op == "split_wall":
            at = p.get("at")
            if at == "midpoint":
                pass
            elif isinstance(at, (int, float)) and not isinstance(at, bool):
                if not (0.01 < float(at) < 0.99):
                    errs.append(f"{self.op}: at={at} must be within (0.01, 0.99)")
            else:
                errs.append(f"{self.op}: at must be 'midpoint' or a fraction")
        if self.op == "add_wall":
            for k in ("start_ref", "end_ref"):
                if not _ref_ok(p.get(k)):
                    errs.append(f"{self.op}: {k}={p.get(k)!r} is not a symbolic "
                                "reference ('w7:start', 'w7:end', 'w7@0.5')")
            if p.get("start_ref") == p.get("end_ref"):
                errs.append(f"{self.op}: start_ref and end_ref are identical")
        if self.op == "add_door":
            if not _position_ok(p.get("position")):
                errs.append(f"{self.op}: position must be a fraction 0..1 or one "
                            f"of {POSITION_WORDS}")
            dt = p.get("door_type") or p.get("type")
            if dt is not None and dt not in DOOR_TYPES:
                errs.append(f"{self.op}: door_type must be one of {DOOR_TYPES}")
        if self.op == "update_door":
            dt = p.get("door_type") or p.get("type")
            if dt is not None and dt not in DOOR_TYPES:
                errs.append(f"{self.op}: door_type must be one of {DOOR_TYPES}")
            if p.get("position") is not None and not _position_ok(p["position"]):
                errs.append(f"{self.op}: position must be a fraction 0..1 or a "
                            "position word")
            if p.get("swing_direction") is not None and \
                    p["swing_direction"] not in ("left", "right"):
                errs.append(f"{self.op}: swing_direction must be left|right")
        if self.op == "update_room" and p.get("room_type") is not None:
            if p["room_type"] not in EDITOR_ROOM_TYPES:
                errs.append(f"{self.op}: room_type must be one of "
                            f"{EDITOR_ROOM_TYPES}")
        for k in ("thickness_mm", "height_mm", "width_mm"):
            if k in p:
                v = p[k]
                if not isinstance(v, (int, float)) or isinstance(v, bool) or v <= 0:
                    errs.append(f"{self.op}: {k} must be a positive number")
                elif k == "thickness_mm" and not (60 <= v <= 500):
                    errs.append(f"{self.op}: thickness_mm={v} outside 60..500")
                elif k == "height_mm" and not (1800 <= v <= 5000):
                    errs.append(f"{self.op}: height_mm={v} outside 1800..5000")
                elif k == "width_mm" and not (450 <= v <= 3600):
                    errs.append(f"{self.op}: width_mm={v} outside 450..3600")

        # -- referential integrity, when we have a spec/plan to check against
        if spec is not None and self.level == LEVEL_SPEC:
            rid = p.get("room_id")
            if rid is not None and self.op != "add_room" and spec.room(rid) is None:
                errs.append(f"{self.op}: no room {rid!r} in the spec "
                            f"(have {[r.id for r in spec.rooms]})")
            if self.op == "add_room" and rid is not None and spec.room(rid):
                errs.append(f"add_room: room id {rid!r} already exists")
            known = {r.id for r in spec.rooms} | set(ROOM_CATEGORIES)
            for k in ("a", "b"):
                if p.get(k) is not None and p[k] not in known:
                    errs.append(f"{self.op}: {k}={p[k]!r} is not a room id or "
                                "category")
        if plan is not None and self.level == LEVEL_GEOMETRY:
            wall_ids = {w.id for w in getattr(plan, "walls", [])}
            room_ids = {r.id for r in getattr(plan, "rooms", [])}
            open_ids = {o.id for o in getattr(plan, "openings", [])}
            if p.get("wall_id") is not None and p["wall_id"] not in wall_ids:
                errs.append(f"{self.op}: no wall {p['wall_id']!r} in the plan")
            if p.get("room_id") is not None and p["room_id"] not in room_ids:
                errs.append(f"{self.op}: no room {p['room_id']!r} in the plan")
            if p.get("door_id") is not None and p["door_id"] not in open_ids:
                errs.append(f"{self.op}: no opening {p['door_id']!r} in the plan")
            if p.get("element_id") is not None and \
                    p["element_id"] not in (wall_ids | room_ids | open_ids):
                errs.append(f"{self.op}: no element {p['element_id']!r} in the plan")
            for k in ("start_ref", "end_ref"):
                ref = p.get(k)
                if isinstance(ref, str) and _ref_ok(ref):
                    wid = ref.split(":")[0].split("@")[0]
                    if wid not in wall_ids:
                        errs.append(f"{self.op}: {k} references unknown wall {wid!r}")
        return errs

    # -------------------------------------------------------------- transport
    def to_dict(self) -> dict:
        return {"op": self.op, "level": self.level, "params": dict(self.params),
                "description": self.description, "finding_ids": list(self.finding_ids),
                "confidence": self.confidence, "rationale": self.rationale}

    @classmethod
    def from_dict(cls, d: dict) -> "PatchOp":
        d = dict(d or {})
        return cls(
            op=str(d.get("op", "")),
            params=d.get("params") or {},
            description=str(d.get("description") or ""),
            level=str(d.get("level") or ""),
            finding_ids=list(d.get("finding_ids") or []),
            confidence=float(d.get("confidence", 0.5) or 0.0),
            rationale=str(d.get("rationale") or ""),
        )

    # ------------------------------------------------------------- rendering
    def diff_line(self) -> str:
        tag = "SPEC" if self.level == LEVEL_SPEC else "GEOM"
        cite = f" [{','.join(self.finding_ids)}]" if self.finding_ids else ""
        return f"[{tag}] {self.description}{cite}"

    def to_editor_call(self, north_deg: float = 0.0) -> Optional[tuple[str, list]]:
        """Render as an OpenPlan3D store call: (functionName, args).

        Units: OpenPlan3D's `Project` is float centimetres (DECISIONS.md #1), so
        millimetres are divided by 10 here. Compass directions are resolved
        against `Site.north_deg` -- the *applier* turns a direction plus a
        magnitude into a displacement, which is exactly the point: the model
        never saw a coordinate.
        """
        entry = OP_TABLE.get(self.op)
        if entry is None or entry["editor"] is None:
            return None
        p = self.params
        fn = entry["editor"]
        if self.op == "update_wall":
            upd: dict[str, float] = {}
            if p.get("thickness_mm") is not None:
                upd["thickness"] = p["thickness_mm"] / 10.0
            if p.get("height_mm") is not None:
                upd["height"] = p["height_mm"] / 10.0
            return (fn, [p["wall_id"], upd])
        if self.op == "move_wall_parallel":
            bearing = {"north": 0, "north_east": 45, "east": 90, "south_east": 135,
                       "south": 180, "south_west": 225, "west": 270,
                       "north_west": 315}[p["direction"]]
            theta = math.radians(bearing - north_deg)
            d_cm = p["distance_mm"] / 10.0
            return (fn, [p["wall_id"], round(d_cm * math.sin(theta), 4),
                         round(d_cm * math.cos(theta), 4)])
        if self.op == "split_wall":
            t = 0.5 if p["at"] == "midpoint" else float(p["at"])
            return (fn, [p["wall_id"], t])
        if self.op == "add_wall":
            return (fn, [{"$ref": p["start_ref"]}, {"$ref": p["end_ref"]}])
        if self.op == "add_door":
            return (fn, [p["wall_id"], round(_position_t(p["position"]), 4),
                         p.get("door_type") or p.get("type") or "single"])
        if self.op == "update_door":
            upd = {}
            if p.get("width_mm") is not None:
                upd["width"] = p["width_mm"] / 10.0
            if p.get("door_type") is not None:
                upd["type"] = p["door_type"]
            if p.get("swing_direction") is not None:
                upd["swingDirection"] = p["swing_direction"]
            if p.get("flip_side") is not None:
                upd["flipSide"] = bool(p["flip_side"])
            if p.get("position") is not None:
                upd["position"] = round(_position_t(p["position"]), 4)
            return (fn, [p["door_id"], upd])
        if self.op == "update_room":
            upd = {}
            if p.get("name") is not None:
                upd["name"] = p["name"]
            if p.get("room_type") is not None:
                upd["roomType"] = p["room_type"]
            return (fn, [p["room_id"], upd])
        if self.op == "remove_element":
            return (fn, [p["element_id"]])
        return None


@dataclass
class PatchBatch:
    """A proposed edit as a whole: one undo group, one user-visible diff."""
    ops: list[PatchOp] = field(default_factory=list)
    rejected: list[tuple[dict, list[str]]] = field(default_factory=list)
    description: str = "AI edit"
    notes: str = ""

    @property
    def spec_ops(self) -> list[PatchOp]:
        return [o for o in self.ops if o.level == LEVEL_SPEC]

    @property
    def geometry_ops(self) -> list[PatchOp]:
        return [o for o in self.ops if o.level == LEVEL_GEOMETRY]

    @property
    def requires_resolve(self) -> bool:
        return bool(self.spec_ops)

    def diff_lines(self) -> list[str]:
        """What the user is shown before accepting. Non-negotiable: if the user
        cannot see what the AI changed, they will not trust it."""
        out = [f"{self.description} "
               f"({len(self.spec_ops)} spec, {len(self.geometry_ops)} geometry"
               + (f", {len(self.rejected)} rejected" if self.rejected else "") + ")"]
        out += [f"  {o.diff_line()}" for o in self.ops]
        out += [f"  [REJECTED] {d.get('op', '?')}: {errs[0]}"
                for d, errs in self.rejected]
        if self.requires_resolve:
            out.append("  -> spec changed: the solver will re-run")
        return out

    def to_editor_script(self, north_deg: float = 0.0) -> str:
        """Geometry ops as an OpenPlan3D call sequence in one undo group."""
        calls = [c for c in (o.to_editor_call(north_deg) for o in self.geometry_ops)
                 if c is not None]
        if not calls:
            return ""
        lines = ["beginUndoGroup();"]
        for fn, args in calls:
            rendered = ", ".join(json.dumps(a) if not isinstance(a, str)
                                 else json.dumps(a) for a in args)
            lines.append(f"{fn}({rendered});")
        lines.append(f"endUndoGroup({json.dumps(self.description)});")
        return "\n".join(lines)

    def apply_to_spec(self, spec: DesignSpec) -> DesignSpec:
        """Apply the spec-level ops, returning a new spec to re-solve."""
        out = copy.deepcopy(spec)
        for o in self.spec_ops:
            p = o.params
            if o.op == "set_room_area":
                r = out.room(p["room_id"])
                if r:
                    r.min_sqft, r.max_sqft = float(p["min_sqft"]), float(p["max_sqft"])
            elif o.op == "set_room_aspect":
                r = out.room(p["room_id"])
                if r:
                    r.max_aspect = float(p["max_aspect"])
                    if p.get("min_aspect") is not None:
                        r.min_aspect = float(p["min_aspect"])
            elif o.op == "set_room_zone":
                r = out.room(p["room_id"])
                if r:
                    r.preferred_zone = p["preferred_zone"]
            elif o.op == "set_room_priority":
                r = out.room(p["room_id"])
                if r:
                    r.priority = int(p["priority"])
            elif o.op == "add_room":
                kw = {k: v for k, v in p.items()
                      if k in RoomSpec.__dataclass_fields__ and k != "room_id"}
                out.rooms.append(RoomSpec(id=p["room_id"], **kw))
            elif o.op == "remove_room":
                out.rooms = [r for r in out.rooms if r.id != p["room_id"]]
                out.adjacency = [a for a in out.adjacency
                                 if p["room_id"] not in (a.a, a.b)]
            elif o.op == "set_adjacency":
                new = Adjacency(a=p["a"], b=p["b"], kind=p["kind"],
                                relation=p.get("relation", "adjacent"),
                                reason=p.get("reason", ""))
                out.adjacency = [a for a in out.adjacency if a.key() != new.key()]
                out.adjacency.append(new)
            elif o.op == "remove_adjacency":
                rel = p.get("relation")
                out.adjacency = [
                    a for a in out.adjacency
                    if not ({a.a, a.b} == {p["a"], p["b"]}
                            and (rel is None or a.relation == rel))
                ]
            elif o.op == "set_entrance":
                for k in ("side", "zone", "via_foyer", "avoid_direct_kitchen_view"):
                    if p.get(k) is not None:
                        setattr(out.entrance, k, p[k])
            elif o.op == "set_wet_grouping":
                out.wet_grouping = p["value"]
            elif o.op == "set_storeys":
                out.storeys = int(p["value"])
        prov = dict(out.provenance or {})
        prov.setdefault("patches", []).append(
            {"description": self.description,
             "ops": [o.to_dict() for o in self.spec_ops]})
        out.provenance = prov
        return out

    def to_dict(self) -> dict:
        return {"description": self.description, "notes": self.notes,
                "ops": [o.to_dict() for o in self.ops],
                "rejected": [{"op": d, "errors": e} for d, e in self.rejected]}


def parse_patch_ops(
    raw: Sequence[dict],
    *,
    spec: Optional[DesignSpec] = None,
    plan: Any = None,
) -> tuple[list[PatchOp], list[tuple[dict, list[str]]]]:
    """Parse and validate a raw op list. Valid ops are kept, bad ones rejected.

    Per-op validation is what makes a partially wrong LLM answer useful.
    """
    good: list[PatchOp] = []
    bad: list[tuple[dict, list[str]]] = []
    for d in raw or []:
        try:
            op = PatchOp.from_dict(d)
        except (TypeError, ValueError) as exc:
            bad.append((dict(d or {}), [f"unparseable: {exc}"]))
            continue
        errs = op.validate(spec=spec, plan=plan)
        (good.append(op) if not errs else bad.append((op.to_dict(), errs)))
    return good, bad


# --------------------------------------------------------------------------
# Patch proposal
# --------------------------------------------------------------------------

def _op_catalogue() -> str:
    lines = []
    for name, e in OP_TABLE.items():
        req = ", ".join(e["required"]) or "-"
        opt = ", ".join(e["optional"]) or "-"
        lines.append(f"  {name} [{e['level']}] required: {req} | optional: {opt}\n"
                     f"      {e['doc']}")
    return "\n".join(lines)


PATCH_SYSTEM = """\
You repair a floor plan that a validator has complained about. You emit symbolic
operations only. You NEVER emit coordinates, absolute positions, room polygons,
or wall endpoints -- a CP-SAT solver owns all geometry and will recompute it.

Two levels of operation:

- spec-level ops change the design brief and trigger a full RE-SOLVE. Prefer
  these, strongly and by default. The solver re-establishes global consistency,
  so a spec patch can never produce an invalid plan -- at worst it becomes
  unsatisfiable, which is a legible failure the user can act on.
- geometry-level ops mutate the plan directly through the editor's mutation API.
  They are surgical and they can break constraints the solver had satisfied. Use
  one only when a finding pinpoints a purely local defect (a missing door, a
  mislabelled room, a wall 150 mm too far over) and cite the finding id.

Available operations:
{catalogue}

Rules:
1. One op per distinct change. Do not bundle.
2. Every op needs a `description` written for a homeowner, in the imperative,
   naming what changes and why: "Widen the master bedroom to 150-190 sqft so it
   meets the 2.4 m minimum width". The user sees this as a diff and rejects the
   batch with Ctrl-Z if it reads wrong. An op without a clear description is
   discarded.
3. Cite the finding ids each op addresses in `finding_ids`. Mandatory for
   geometry ops.
4. Set `confidence` honestly. Below 0.4 means "the user should look at this".
5. Address the error-severity findings first. If a finding cannot be fixed by
   any available op, say so in `notes` rather than inventing an op.
6. Do not propose more than 8 ops. If the plan needs more than that, the right
   answer is a small number of spec ops and a re-solve.
"""


def propose_patch_tool_schema(strict: bool = False) -> dict:
    """Tool schema for the patch batch.

    Measured, not aesthetic: strict tool use compiles the input schema into a
    grammar and caps unions at 16 and optional parameters at 24. One flat
    params object covering the whole op vocabulary needs ~38 optional fields,
    so this tool cannot be strict -- both encodings 400:

        "too many parameters with union types (38 ... limit: 16)"
        "too many optional parameters (38) ... (limit: 24)"

    Splitting params per op would need a 19-way oneOf, which strict mode does
    not support either. So the schema is plain-typed and unenforced at the API,
    and conformance is enforced locally instead: `validate_against_schema`
    gates the reply, a failure is fed back to the model as a tool_result error,
    and `PatchOp.validate()` rejects any parameter that does not belong to the
    op that was named. That last check is where the real discipline lives --
    it is also what rejects coordinate emission, which no JSON Schema could.
    `strict=True` is kept for the day the limits rise; `call_tool` degrades
    automatically if the API refuses it.
    """
    def nullable(base: dict) -> dict:
        return base

    params_props: dict[str, dict] = {
        "room_id": nullable({"type": "string"}),
        "category": nullable({"type": "string", "enum": sorted(ROOM_CATEGORIES)}),
        "name": nullable({"type": "string"}),
        "min_sqft": nullable({"type": "number"}),
        "max_sqft": nullable({"type": "number"}),
        "min_aspect": nullable({"type": "number"}),
        "max_aspect": nullable({"type": "number"}),
        "priority": nullable({"type": "integer", "enum": [1, 2, 3, 4, 5]}),
        "optional": nullable({"type": "boolean"}),
        "attached_bath": nullable({"type": "boolean"}),
        "preferred_zone": nullable({"type": "string", "enum": list(ZONES)}),
        "storey": nullable({"type": "integer"}),
        "a": nullable({"type": "string"}),
        "b": nullable({"type": "string"}),
        "kind": nullable({"type": "string", "enum": list(ADJACENCY_KINDS)}),
        "relation": nullable({"type": "string", "enum": list(ADJACENCY_RELATIONS)}),
        "reason": nullable({"type": "string"}),
        "side": nullable({"type": "string", "enum": list(SIDES)}),
        "zone": nullable({"type": "string", "enum": list(ZONES)}),
        "via_foyer": nullable({"type": "boolean"}),
        "avoid_direct_kitchen_view": nullable({"type": "boolean"}),
        "value": nullable({"type": "string",
                           "description": "for set_wet_grouping: "
                                          "required|preferred|indifferent"}),
        "wall_id": nullable({"type": "string"}),
        "door_id": nullable({"type": "string"}),
        "element_id": nullable({"type": "string"}),
        "thickness_mm": nullable({"type": "number"}),
        "height_mm": nullable({"type": "number"}),
        "width_mm": nullable({"type": "number"}),
        "direction": nullable({"type": "string", "enum": list(COMPASS_MOVE)}),
        "distance_mm": nullable({"type": "number",
                                 "description": "magnitude only, from a finding"}),
        "at": nullable({"type": "string",
                        "description": "'midpoint' or a fraction as a string"}),
        "start_ref": nullable({"type": "string",
                               "description": "'w7:start' | 'w7:end' | 'w7@0.5'"}),
        "end_ref": nullable({"type": "string"}),
        "position": nullable({"type": "string",
                              "description": "start|quarter|centre|"
                                             "three_quarter|end, or a fraction "
                                             "as a string"}),
        "door_type": nullable({"type": "string", "enum": list(DOOR_TYPES)}),
        "swing_direction": nullable({"type": "string", "enum": ["left", "right"]}),
        "flip_side": nullable({"type": "boolean"}),
        "room_type": nullable({"type": "string", "enum": list(EDITOR_ROOM_TYPES)}),
        # ---- furniture level -------------------------------------------
        # These were declared in OP_TABLE and missing here, which meant the
        # model had no field to put them in: every furniture op was
        # unexpressible through the one tool that proposes ops. Generated
        # from the table now, and `schema_param_gaps()` fails the build if
        # the two ever drift apart again.
        "item": nullable({"type": "string",
                          "description": "catalogue key, e.g. 'sofa', 'bed_queen'"}),
        "item_id": nullable({"type": "string",
                             "description": "id of a placed item, or its "
                                            "catalogue key if unique"}),
        "anchor": nullable({"type": "string", "enum": list(ANCHORS)}),
        "of": nullable({"type": "string",
                        "description": "the other item, for beside/facing/front_of"}),
        "prefer": nullable({"type": "array", "items": {"type": "string",
                                                       "enum": list(ZONES)}}),
        "avoid": nullable({"type": "array", "items": {"type": "string",
                                                      "enum": list(ZONES)}}),
        "count": nullable({"type": "integer"}),
        "align": nullable({"type": "string"}),
        "clear_front_mm": nullable({"type": "number"}),
        "gap_mm": nullable({"type": "number"}),
        "abut": nullable({"type": "boolean"}),
        "avoid_window": nullable({"type": "boolean"}),
        "note": nullable({"type": "string"}),
        "density": nullable({"type": "string", "enum": list(DENSITIES)}),
        "add": nullable({"type": "array", "items": {"type": "string"}}),
        "drop": nullable({"type": "array", "items": {"type": "string"}}),
        "swap": nullable({"type": "array", "items": {"type": "string"},
                          "description": "pairs, old then new"}),
        "run": nullable({"type": "string", "enum": list(KITCHEN_RUNS)}),
        "hob_zone": nullable({"type": "string", "enum": list(ZONES)}),
        "sink_zone": nullable({"type": "string", "enum": list(ZONES)}),
        "fridge_zone": nullable({"type": "string", "enum": list(ZONES)}),
        "breakfast_counter": nullable({"type": "boolean"}),
        # ---- openings --------------------------------------------------
        "window_id": nullable({"type": "string"}),
        "type": nullable({"type": "string",
                          "enum": sorted(set(DOOR_TYPES) | set(WINDOW_TYPES)),
                          "description": "door or window type, per the op"}),
        "sill_mm": nullable({"type": "number",
                             "description": "window sill height above floor"}),
    }
    params = {"type": "object", "properties": params_props,
              "required": [],          # see the docstring: union budget is 16
              "additionalProperties": False}

    op_props = {
        "op": {"type": "string", "enum": sorted(OP_TABLE)},
        "level": {"type": "string",
                  "enum": [LEVEL_SPEC, LEVEL_GEOMETRY, LEVEL_FURNITURE]},
        "params": params,
        "description": {"type": "string",
                        "description": "homeowner-readable, imperative, says why"},
        "finding_ids": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number"},
        "rationale": {"type": "string"},
    }
    op_schema = {"type": "object", "properties": op_props,
                 "required": sorted(op_props) if strict
                 else ["op", "level", "params", "description"],
                 "additionalProperties": False}
    props = {
        "description": {"type": "string",
                        "description": "one-line undo-group label"},
        "ops": {"type": "array", "items": op_schema},
        "notes": {"type": "string",
                  "description": "findings you could not address, and why"},
    }
    return {"type": "object", "properties": props,
            "required": sorted(props) if strict else ["ops", "description"],
            "additionalProperties": False}


def schema_param_gaps() -> dict[str, list[str]]:
    """Op params that `propose_patch_tool_schema` gives the model no field for.

    Empty is the only acceptable answer. An op whose params are absent here is
    declared but unreachable -- the model can name the op and then has nowhere
    to put its arguments, which is how all six furniture ops sat unusable
    through this tool while looking fully wired in `OP_TABLE`.
    """
    have = set(propose_patch_tool_schema()["properties"]["ops"]["items"]
               ["properties"]["params"]["properties"])
    out: dict[str, list[str]] = {}
    for name, e in OP_TABLE.items():
        miss = sorted((set(e["required"]) | set(e["optional"])) - have)
        if miss:
            out[name] = miss
    return out


def _coerce_op_params(d: dict) -> dict:
    """Undo the flat-schema compromises: drop nulls, restore numeric types."""
    d = dict(d or {})
    p = {k: v for k, v in (d.get("params") or {}).items() if v is not None}
    for k in ("at", "position"):
        if isinstance(p.get(k), str):
            try:
                p[k] = float(p[k])
            except ValueError:
                pass  # keeps 'midpoint' / position words
    for k in ("distance_mm", "thickness_mm", "height_mm", "width_mm",
              "sill_mm", "clear_front_mm", "gap_mm",
              "min_sqft", "max_sqft", "min_aspect", "max_aspect"):
        if isinstance(p.get(k), str):
            try:
                p[k] = float(p[k])
            except ValueError:
                pass
    d["params"] = p
    return d


def summarize_plan(plan: Any, *, max_walls: int = 120) -> str:
    """A symbolic description of a plan for the LLM to reason over.

    Reading geometry is fine; the model just must not write any. Areas are given
    in sqft (the client's unit) and wall lengths in mm with an orientation, so
    the model can talk about "the north wall of bedroom 2" by id.
    """
    lines = [f"plan {getattr(plan, 'id', '?')}: "
             f"{len(getattr(plan, 'rooms', []))} rooms, "
             f"{len(getattr(plan, 'walls', []))} walls, "
             f"{len(getattr(plan, 'openings', []))} openings"]
    site = getattr(plan, "site", None)
    if site is not None and getattr(site, "plot_polygon", None):
        xs = [p.x for p in site.plot_polygon]
        ys = [p.y for p in site.plot_polygon]
        lines.append(f"site: bbox {max(xs) - min(xs)}x{max(ys) - min(ys)} mm, "
                     f"north_deg={site.north_deg}")
    lines.append("rooms:")
    for r in getattr(plan, "rooms", []):
        lines.append(f"  {r.id} {r.name!r} category={r.category} "
                     f"area={r.area / MM2_PER_SQFT:.0f} sqft "
                     f"walls={','.join(r.wall_ids)}")
    walls = list(getattr(plan, "walls", []))
    lines.append(f"walls (first {min(len(walls), max_walls)}):")
    for w in walls[:max_walls]:
        dx, dy = w.end.x - w.start.x, w.end.y - w.start.y
        orient = "E-W" if abs(dy) < abs(dx) else "N-S"
        lines.append(f"  {w.id} {orient} length={w.length:.0f}mm "
                     f"thickness={w.thickness}mm")
    lines.append("openings:")
    for o in getattr(plan, "openings", []):
        lines.append(f"  {o.id} {o.kind} on {o.wall_id} at t={o.position:.2f} "
                     f"width={o.width}mm")
    return "\n".join(lines)


def _render_findings(findings: Iterable[Any]) -> str:
    out = []
    for f in findings:
        if isinstance(f, Finding):
            d = f.to_dict()
        elif isinstance(f, dict):
            d = f
        else:  # duck-typed
            d = {k: getattr(f, k, None)
                 for k in ("id", "severity", "code", "message", "refs", "suggestion")}
        refs = ",".join(d.get("refs") or [])
        sug = f" | validator hint: {d['suggestion']}" if d.get("suggestion") else ""
        out.append(f"  [{d.get('id')}] {str(d.get('severity', '?')).upper()} "
                   f"{d.get('code')}: {d.get('message')}"
                   + (f" (refs: {refs})" if refs else "") + sug)
    return "\n".join(out) if out else "  (none)"


def _render_spec_for_patch(spec: DesignSpec) -> str:
    lines = [
        f"plot: {spec.plot_width_ft}x{spec.plot_depth_ft} ft, "
        f"road on {spec.road_facing_side}, city={spec.city_profile}, "
        f"storeys={spec.storeys}, north_deg={spec.north_deg}",
        f"wet_grouping={spec.wet_grouping}, "
        f"entrance side={spec.entrance.side} zone={spec.entrance.zone}, "
        f"vastu={spec.vastu.strictness if spec.vastu.enabled else 'off'}"
        + (f" {spec.vastu.requirements}" if spec.vastu.enabled else ""),
        "programme:",
    ]
    for r in spec.rooms:
        lines.append(f"  {r.id} {r.category} {r.min_sqft:.0f}-{r.max_sqft:.0f} sqft "
                     f"aspect<={r.max_aspect} priority={r.priority}"
                     + (" optional" if r.optional else "")
                     + (f" zone={r.preferred_zone}" if r.preferred_zone else ""))
    if spec.adjacency:
        lines.append("adjacency:")
        for a in spec.adjacency:
            lines.append(f"  {a.a} {a.kind} {a.relation} {a.b}")
    return "\n".join(lines)


def propose_patch_batch(
    plan_summary: Any,
    findings: Sequence[Any],
    spec: DesignSpec,
    *,
    plan: Any = None,
    model: str = MODEL_REASONING,
    client: Any = None,
    usage: Optional[UsageLog] = None,
    max_attempts: int = 3,
    strict: bool = False,
    effort: str = "high",
) -> PatchBatch:
    """Findings -> a validated batch of symbolic ops, with rejects preserved.

    `strict` defaults to False because the op schema exceeds the strict-mode
    grammar budget (see `propose_patch_tool_schema`); passing True costs one
    wasted round-trip before `call_tool` degrades. Conformance is enforced
    locally regardless.
    """
    if not isinstance(plan_summary, str):
        plan_summary = summarize_plan(plan_summary)
    schema = propose_patch_tool_schema(strict=strict)
    user = (
        "DESIGN SPEC\n" + _render_spec_for_patch(spec)
        + "\n\nCURRENT PLAN\n" + plan_summary
        + "\n\nVALIDATOR FINDINGS\n" + _render_findings(findings)
        + "\n\nPropose the smallest set of operations that clears these findings."
    )
    payload = call_tool(
        system=PATCH_SYSTEM.format(catalogue=_op_catalogue()),
        user=user,
        tool_name="emit_patch",
        tool_description="Emit the symbolic patch that repairs the reported findings.",
        schema=schema,
        model=model,
        op="propose_patch",
        client=client,
        usage=usage,
        max_attempts=max_attempts,
        strict=strict,
        effort=effort,
    )
    raw = [_coerce_op_params(d) for d in (payload.get("ops") or [])]
    good, bad = parse_patch_ops(raw, spec=spec, plan=plan)
    return PatchBatch(ops=good, rejected=bad,
                      description=payload.get("description") or "AI edit",
                      notes=payload.get("notes") or "")


def propose_patch(
    plan_summary: Any,
    findings: Sequence[Any],
    spec: DesignSpec,
    **kwargs: Any,
) -> list[PatchOp]:
    """The refinement-loop entry point: findings in, valid symbolic ops out.

    Invalid ops are dropped, not raised -- use `propose_patch_batch` when you
    need the rejects (you do, for the diff the user is shown).
    """
    return propose_patch_batch(plan_summary, findings, spec, **kwargs).ops
