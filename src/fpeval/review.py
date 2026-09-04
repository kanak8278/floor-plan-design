"""A third check: look at the drawing and say what is wrong with it.

The rules engine and this disagree in a useful direction. The engine reads the
door graph and the dimensions, so it is good at connectivity and arithmetic
and it only knows the defects someone has written a rule for. Looking at the
drawing is the opposite: proportion and position read at a glance, and
connectivity reads badly -- measured on one plan, a visual pass caught a
landlocked bathroom and two size inversions and MISSED
`DESIGN.BEDROOM_THROUGH_TRAFFIC`, the worst defect on the sheet, because door
swings are hard to trace at 640 px.

So this is not a substitute and not a second opinion to average with the
first. Its job is the **third bucket** of `cross_reference`: anything the
drawing shows and the engine has no rule for is a candidate rule. That turns a
non-deterministic checker into a way of finding the rules we have not written,
which is the only role a paid, unrepeatable check can honestly hold in a suite
that has to be reproducible.

Nothing here belongs in a unit test: it costs money and does not repeat.
`scripts/review_visual.py` runs it on demand and aggregates the third bucket.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from .ir import Plan
from .spatial import plan_png, room_facts

MODEL = "claude-opus-5"

# Below this, two findings sharing a room are not talking about the same thing.
# 0.34 is one room in common out of two on each side, or two out of four.
MATCH_MIN_JACCARD = 0.34

SYSTEM = """\
You are reviewing an Indian residential floor plan drawing the way a senior \
architect reviews a junior's sheet: looking for what a client would complain \
about on first sight.

You are given the drawing and a text list of room positions. Report only what \
you can actually see or read. Do not infer door connectivity from the picture \
unless the swing is unambiguous -- the position list is authoritative for what \
opens onto what, and guessing at it is the one thing this review is bad at.

Report each problem once, with the room ids it involves. Prefer specific \
observations about size, position and grouping over general advice. If the \
plan is fine in some respect, do not invent a problem to fill the list.

Indian norms that matter here: the hall IS the living room and should be the \
biggest space in the house; the kitchen wants the south-east and a utility \
directly off it; a pooja room wants the north-east and must not share a wall \
with a toilet; wet rooms group for one plumbing line; the front door should \
not open straight onto the kitchen or into a bedroom.\
"""

FINDINGS_TOOL = {
    "name": "report_findings",
    "description": "Report what is wrong with the plan as drawn.",
    "input_schema": {
        "type": "object",
        "properties": {
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        # Free text on purpose. A closed enum could only name
                        # the defects we already have rules for, which would
                        # make the third bucket impossible to fill.
                        "category": {
                            "type": "string",
                            "description": "A short kebab-case slug, e.g. "
                                           "living-too-small, kitchen-far-from-dining.",
                        },
                        "severity": {"type": "string",
                                     "enum": ["error", "warn", "note"]},
                        "room_ids": {"type": "array",
                                     "items": {"type": "string"},
                                     "description": "Ids from the position list."},
                        "what": {"type": "string",
                                 "description": "One sentence, naming the "
                                                "measurement or position you read."},
                    },
                    "required": ["category", "severity", "room_ids", "what"],
                },
            },
        },
        "required": ["findings"],
    },
}


@dataclass
class VisualFinding:
    category: str
    severity: str
    room_ids: list[str] = field(default_factory=list)
    what: str = ""

    def to_dict(self) -> dict:
        return {"category": self.category, "severity": self.severity,
                "room_ids": list(self.room_ids), "what": self.what}


def visual_review(plan: Plan, *, client: Any = None, model: str = MODEL,
                  width: int = 640) -> tuple[list[VisualFinding], dict]:
    """Show the drawing, get structured findings back. Returns (findings, usage)."""
    png = plan_png(plan, width=width)
    if png is None:
        return [], {}
    import anthropic
    client = client or anthropic.Anthropic()
    msg = client.messages.create(
        model=model, max_tokens=4000,
        system=SYSTEM,
        tools=[FINDINGS_TOOL],
        tool_choice={"type": "tool", "name": "report_findings"},
        messages=[{"role": "user", "content": [
            {"type": "image",
             "source": {"type": "base64", "media_type": "image/png",
                        "data": base64.standard_b64encode(png).decode("ascii")}},
            {"type": "text", "text": room_facts(plan)},
            {"type": "text", "text": "Review this plan."},
        ]}],
    )
    out: list[VisualFinding] = []
    for block in msg.content:
        if getattr(block, "type", None) != "tool_use":
            continue
        for f in (block.input or {}).get("findings", []):
            out.append(VisualFinding(
                category=str(f.get("category", "")),
                severity=str(f.get("severity", "note")),
                room_ids=[str(x) for x in (f.get("room_ids") or [])],
                what=str(f.get("what", ""))))
    u = msg.usage
    usage = {"input": getattr(u, "input_tokens", 0),
             "output": getattr(u, "output_tokens", 0)}
    return out, usage


def cross_reference(visual: Sequence[VisualFinding],
                    rule_findings: Sequence[Any]) -> dict[str, list]:
    """Three buckets, and the third one is the point.

    Matching is by room overlap, not by category name: the two checkers have no
    shared vocabulary and never will, since one reports
    `DESIGN.LIVING_NOT_LARGEST` and the other reports whatever slug it thought
    of.

    Overlap is scored by Jaccard and the BEST match wins, with a floor. A first
    version took the first rule finding with any room in common, and
    `DESIGN.BEDROOM_THROUGH_TRAFFIC` -- which lists every room on the through
    route -- soaked up every visual finding on the plan. That made `agreed`
    meaningless (`bathroom-count-excessive ~ BEDROOM_THROUGH_TRAFFIC`), emptied
    `vision_only` spuriously, and pushed `LIVING_NOT_LARGEST` into `rules_only`
    while the finding that actually matched it, `hall-not-biggest-space`, was
    paired with something else. A cross-reference that mismatches is worse than
    none: it manufactures agreement and hides the third bucket, which is the
    only bucket worth having.
    """
    ruled = [(f, {str(x) for x in (getattr(f, "element_ids", None) or [])})
             for f in rule_findings]
    agreed, vision_only = [], []
    matched_rules: set[int] = set()
    for v in visual:
        vs = set(v.room_ids)
        best_i, best_j = None, 0.0
        for i, (f, ids) in enumerate(ruled):
            if not (vs and ids):
                continue
            j = len(vs & ids) / len(vs | ids)
            if j > best_j:
                best_i, best_j = i, j
        # A single shared room out of six on each side is not agreement.
        if best_i is None or best_j < MATCH_MIN_JACCARD:
            vision_only.append(v)
        else:
            matched_rules.add(best_i)
            agreed.append((v, ruled[best_i][0], round(best_j, 2)))
    rules_only = [f for i, (f, _) in enumerate(ruled) if i not in matched_rules]
    return {"agreed": agreed, "vision_only": vision_only,
            "rules_only": rules_only}
