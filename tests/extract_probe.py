"""Probe: can a vision model extract a verifiable schedule from an Indian plan image?

The verification hinge: Indian plans annotate rooms in BOTH mm and feet-inches.
Those are two independent encodings of one number, so they cross-check each other
-- and they catch drafting errors in the source drawing, not just model errors.
"""
import base64, json, os, re, sys
from anthropic import Anthropic

IMG = sys.argv[1] if len(sys.argv) > 1 else "corpus/india/raw/happho-30x40-duplex-gf.jpg"
MODEL = "claude-sonnet-5"

SCHEMA = {
    "name": "plan_schedule",
    "description": "Literal transcription of text printed on an architectural floor plan.",
    "input_schema": {
        "type": "object",
        "properties": {
            "plot": {"type": "object", "properties": {
                "width_ft": {"type": ["number","null"]}, "depth_ft": {"type": ["number","null"]},
                "printed_as": {"type": ["string","null"]}}},
            "floor_label": {"type": ["string","null"]},
            "rooms": {"type": "array", "items": {"type": "object", "properties": {
                "name": {"type": "string"},
                "dim_mm": {"type": ["string","null"], "description": "exactly as printed, e.g. '4000X3500'; null if absent"},
                "dim_ft": {"type": ["string","null"], "description": "exactly as printed, e.g. \"18'1\\\"X11'8\\\"\"; null if absent"},
                "level_mm": {"type": ["number","null"], "description": "from LVL+NNNmm; null if absent"}},
                "required": ["name"]}},
            "notes": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["plot", "rooms"],
    },
}

PROMPT = """Transcribe ONLY what is literally printed on this floor plan. Do not infer, estimate, or compute anything.

Rules:
- Copy dimension strings character-for-character as printed. If a room has no printed dimension, use null. Never derive one from the other unit or from the drawing scale.
- Include every labelled space, including circulation and outdoor ones (foyer, porch, sitout, patio, utility, landscape, etc.).
- Put anything ambiguous or illegible in `notes` rather than guessing.

Accuracy of transcription matters far more than completeness. A null is correct; a guess is a defect."""

img = base64.standard_b64encode(open(IMG,"rb").read()).decode()
c = Anthropic()
r = c.messages.create(model=MODEL, max_tokens=3000, tools=[SCHEMA],
    tool_choice={"type":"tool","name":"plan_schedule"},
    messages=[{"role":"user","content":[
        {"type":"image","source":{"type":"base64","media_type":"image/jpeg","data":img}},
        {"type":"text","text":PROMPT}]}])
data = next(b.input for b in r.content if b.type=="tool_use")
print(json.dumps(data, indent=1))
print(f"\n[tokens in={r.usage.input_tokens} out={r.usage.output_tokens}]", file=sys.stderr)
json.dump(data, open("out/extract_probe.json","w"), indent=1)
