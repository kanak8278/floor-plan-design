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

MODEL = "claude-sonnet-5"

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
4. Transcribe the area legend (SUPER BUILT-UP / CARPET / BALCONY) exactly, keeping sq.ft and sq.m separate.
5. Include every labelled space: rooms, toilets, balconies, utility, passage, foyer, sitout, pooja, and outdoor areas.
6. Put anything rotated, overlapping, cropped or illegible in `notes` and set the affected field to null rather than guessing.

If the image contains more than one distinct floor plan, say so in `notes` and transcribe only the largest."""


def extract(path: str, client: Anthropic | None = None, model: str = MODEL) -> dict:
    c = client or Anthropic()
    data, mt = _b64(path)
    r = c.messages.create(
        model=model, max_tokens=4000, tools=[TOOL],
        tool_choice={"type": "tool", "name": "plan_schedule"},
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": mt, "data": data}},
            {"type": "text", "text": PROMPT}]}])
    out = next(b.input for b in r.content if b.type == "tool_use")
    out["_usage"] = {"in": r.usage.input_tokens, "out": r.usage.output_tokens}
    return out
