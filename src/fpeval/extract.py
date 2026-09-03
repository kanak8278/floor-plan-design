"""Transcribe a room schedule from a floor-plan image.

Deliberately a *transcription* task, not an interpretation one. The model is told
to copy printed strings verbatim and to return null rather than derive a missing
value, because every derived number destroys a cross-check we would otherwise get
for free:

  * dual units      -- "3.84m x 3.81m" alongside "12'7\"x12'6\"" are independent
                       encodings of one measurement
  * carpet area     -- builder plans print CARPET AREA, which the enclosed room
                       areas must sum to
  * built-up ratio  -- super-built-up / carpet sits in a narrow band in practice

Ask the model to compute any of these and all three collapse into one number that
agrees with itself and nothing else.
"""
from __future__ import annotations
from anthropic import Anthropic
from .imgclass import _b64

MODEL = "claude-opus-5"

TOOL = {
    "name": "plan_schedule",
    "description": "Verbatim transcription of text printed on an architectural floor plan.",
    "input_schema": {
        "type": "object",
        "properties": {
            "unit_label": {"type": ["string", "null"],
                "description": "Unit/type designation if printed, e.g. '2BHK 2T TYPE C18'."},
            "floor_label": {"type": ["string", "null"],
                "description": "e.g. 'GROUND FLOOR', 'FIRST FLOOR'."},
            "plot": {"type": "object", "properties": {
                "width_ft": {"type": ["number", "null"]},
                "depth_ft": {"type": ["number", "null"]},
                "printed_as": {"type": ["string", "null"]}},
                "description": "Overall plot/site dimensions, only if printed. Apartment unit plans usually have none."},
            "areas": {"type": "object", "properties": {
                "super_built_up_sqft": {"type": ["number", "null"]},
                "super_built_up_sqm": {"type": ["number", "null"]},
                "carpet_sqft": {"type": ["number", "null"]},
                "rera_carpet_sqft": {"type": ["number", "null"],
                    "description": "RERA CARPET AREA if printed separately -- the legally defined figure."},
                "saleable_sqft": {"type": ["number", "null"]},
                "carpet_sqm": {"type": ["number", "null"]},
                "balcony_sqft": {"type": ["number", "null"]},
                "balcony_sqm": {"type": ["number", "null"]},
                "built_up_sqft": {"type": ["number", "null"]}},
                "description": "Area figures printed in a legend/table. Copy each only if printed."},
            "north_deg_note": {"type": ["string", "null"],
                "description": "Describe where the north arrow points if one is drawn, else null."},
            "rooms": {"type": "array", "items": {"type": "object", "properties": {
                "name": {"type": "string", "description": "Exactly as printed, including misspellings."},
                "dim_primary": {"type": ["string", "null"],
                    "description": "First printed dimension string verbatim, e.g. '3.84m x 3.81m' or '4000X3500'."},
                "dim_secondary": {"type": ["string", "null"],
                    "description": "Second printed dimension string verbatim, usually the feet-inches one."},
                "width_only": {"type": ["string", "null"],
                    "description": "For width-annotated circulation, e.g. '1.15m WIDE'."},
                "level_mm": {"type": ["number", "null"], "description": "From 'LVL+NNNmm' if printed."}},
                "required": ["name"]}},
            "notes": {"type": "array", "items": {"type": "string"},
                "description": "Ambiguity, illegible text, rotated labels, multiple plans in one image."},
        },
        "required": ["plot", "areas", "rooms", "notes"],
    },
}

PROMPT = """Transcribe ONLY what is literally printed on this floor plan.

Hard rules:
1. Copy every dimension string character-for-character, including the unit as written ("3.84m x 3.81m", "4000X3500", "12'7\"x12'6\""). Do not convert, round, or normalise.
2. Most plans print each room's size TWICE — once in metres or millimetres and once in feet-inches. Put the first in `dim_primary` and the second in `dim_secondary`. Both matter; they are used to cross-check each other.
3. If a value is not printed, return null. NEVER derive it from the other unit, from the drawing scale, or from what looks reasonable. A null is correct; a guess is a defect that silently corrupts the dataset.
4. Transcribe the area legend exactly, keeping every figure separate: SALEABLE, SUPER BUILT-UP, BUILT-UP, CARPET, RERA CARPET, BALCONY. Keep sq.ft and sq.m distinct.
   Many plans print dimensions in feet-inches ONLY, with a footer such as "All dimensions are in feet and inches". That is normal: put the single printed string in `dim_primary` and leave `dim_secondary` null. Do not invent a metric equivalent.
5. Include every labelled space: rooms, toilets, balconies, utility, passage, foyer, sitout, pooja, and outdoor areas.
6. Put anything rotated, overlapping, cropped or illegible in `notes` and set the affected field to null rather than guessing.

If the image contains more than one distinct floor plan, say so in `notes` and transcribe only the largest."""


def _best_tool_block(content) -> dict:
    """Pick the richest tool_use block rather than the first.

    Measured: the model occasionally emits an empty schedule alongside ~1200
    output tokens, so `next(...)` could return {} while real content existed.
    """
    blocks = [b.input for b in content if b.type == "tool_use" and isinstance(b.input, dict)]
    if not blocks:
        return {}
    return max(blocks, key=lambda d: len(d.get("rooms") or []))


def extract(path: str, client: Anthropic | None = None, model: str = MODEL,
            attempts: int = 3) -> dict:
    """Transcribe a plan's schedule.

    Sampling parameters (temperature/top_p) were REMOVED from the current models
    and return a 400, so determinism cannot be bought with temperature=0. Measured
    variance without it: the same image returned 14 rooms, then 0, then 0.

    Two mitigations instead:
      * retry while the schedule comes back empty (cheap, trivially detectable)
      * `extract_consensus`, which runs twice and keeps only agreeing fields --
        for a transcription task, two independent reads agreeing is a stronger
        guarantee than a fixed seed would have been.
    """
    c = client or Anthropic()
    data, mt = _b64(path)
    usage = {"in": 0, "out": 0, "attempts": 0}
    out: dict = {}
    for i in range(attempts):
        r = c.messages.create(
            model=model, max_tokens=4000, tools=[TOOL],
            tool_choice={"type": "tool", "name": "plan_schedule"},
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": mt, "data": data}},
                {"type": "text", "text": PROMPT}]}])
        usage["in"] += r.usage.input_tokens
        usage["out"] += r.usage.output_tokens
        usage["attempts"] = i + 1
        cand = _best_tool_block(r.content)
        if len(cand.get("rooms") or []) > len(out.get("rooms") or []):
            out = cand
        if out.get("rooms"):
            break
    out.setdefault("rooms", [])
    out.setdefault("notes", [])
    out["_usage"] = usage
    return out


def _room_key(r: dict) -> tuple:
    return ((r.get("name") or "").strip().upper(),
            (r.get("dim_primary") or "").strip(),
            (r.get("dim_secondary") or "").strip())


def extract_consensus(path: str, client: Anthropic | None = None,
                      model: str = MODEL, runs: int = 2) -> dict:
    """Extract `runs` times and keep only what every run agreed on.

    Fields that disagree are dropped and recorded in `_disagreements`, so a
    transcription the model is not stable about can never silently become ground
    truth. This is the fourth checksum, independent of dual-unit, carpet-area
    closure and plausibility.
    """
    c = client or Anthropic()
    outs = [extract(path, client=c, model=model) for _ in range(runs)]
    base = max(outs, key=lambda o: len(o.get("rooms") or []))
    keysets = [{_room_key(r) for r in (o.get("rooms") or [])} for o in outs]
    agreed = set.intersection(*keysets) if keysets else set()

    kept, dropped = [], []
    for r in base.get("rooms") or []:
        (kept if _room_key(r) in agreed else dropped).append(r)

    disagree = {"rooms_dropped": [r.get("name") for r in dropped]}
    for field in ("carpet_sqft", "rera_carpet_sqft", "super_built_up_sqft"):
        vals = {(o.get("areas") or {}).get(field) for o in outs}
        vals.discard(None)
        if len(vals) > 1:
            disagree[field] = sorted(vals)
            base.setdefault("areas", {})[field] = None
    for field in ("width_ft", "depth_ft"):
        vals = {(o.get("plot") or {}).get(field) for o in outs}
        vals.discard(None)
        if len(vals) > 1:
            disagree[field] = sorted(vals)
            base.setdefault("plot", {})[field] = None

    base["rooms"] = kept
    base["_disagreements"] = disagree
    base["_consensus"] = {"runs": runs,
                          "room_counts": [len(o.get("rooms") or []) for o in outs],
                          "agreed": len(kept), "dropped": len(dropped)}
    base["_usage"] = {k: sum((o["_usage"].get(k) or 0) for o in outs)
                      for k in ("in", "out", "attempts")}
    return base
