"""Gate: is this image a usable 2D architectural floor plan?

Needed because plan-hosting pages mix plans with elevations, sections, 3D renders,
site photos and detail drawings, and an extractor pointed at the wrong image will
still return confident-looking output. Cheapest reliable fix is to classify first
and refuse anything that is not an orthographic plan view.

Separately from *kind*, we record which verification affordances the drawing has
(printed dimensions, dual mm+feet units, plot dimensions, north arrow). A plan with
no printed dimensions is a real floor plan but is USELESS as ground truth, so
`usable` is a stricter test than `is_floor_plan`.
"""
from __future__ import annotations
import base64, mimetypes
from dataclasses import dataclass, asdict
from anthropic import Anthropic

MODEL = "claude-sonnet-5"

KINDS = ["floor_plan_2d", "floor_plan_3d_isometric", "elevation", "section",
         "site_plan", "3d_render_exterior", "3d_render_interior", "photograph",
         "detail_drawing", "brochure_or_collage", "other"]

TOOL = {
    "name": "classify_plan_image",
    "description": "Classify an image found on an architecture/real-estate page.",
    "input_schema": {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": KINDS},
            "confidence": {"type": "number", "description": "0..1"},
            "reasoning": {"type": "string", "description": "one sentence, what visual evidence decided it"},
            "is_orthographic_plan_view": {"type": "boolean",
                "description": "True only for a true top-down cut-through plan: walls as filled/double lines, door swing arcs, no perspective."},
            "has_room_labels": {"type": "boolean"},
            "has_printed_room_dimensions": {"type": "boolean"},
            "has_dual_units": {"type": "boolean",
                "description": "Room dimensions printed in BOTH mm and feet-inches."},
            "has_plot_dimensions": {"type": "boolean",
                "description": "Overall plot/site dimensions printed on the drawing."},
            "has_north_arrow": {"type": "boolean"},
            "has_scale_bar_or_ratio": {"type": "boolean"},
            "storey_label": {"type": ["string", "null"],
                "description": "e.g. 'GROUND FLOOR', 'FIRST FLOOR', null if none printed."},
            "appears_indian": {"type": "boolean",
                "description": "Evidence such as room names SITOUT/PUJA/UTILITY/PWD RM, dimensions in feet, BHK terminology, Vastu notes."},
            "indian_evidence": {"type": "array", "items": {"type": "string"}},
            "blocking_problems": {"type": "array", "items": {"type": "string"},
                "description": "Anything that would make extraction unreliable: illegible, watermarked, cropped, low resolution, multiple plans in one image."},
        },
        "required": ["kind", "confidence", "reasoning", "is_orthographic_plan_view",
                     "has_room_labels", "has_printed_room_dimensions", "has_dual_units",
                     "has_plot_dimensions", "has_north_arrow", "has_scale_bar_or_ratio",
                     "appears_indian", "blocking_problems"],
    },
}

PROMPT = """Classify this image. Judge only what is visibly present — do not speculate about what the page it came from might contain.

Key distinctions people get wrong:
- A **floor plan** is a top-down horizontal cut: walls read as thick filled bands or double lines, doors show swing arcs, rooms are enclosed areas seen from above. No perspective, no vanishing point.
- An **elevation** is a vertical face-on view of the outside: you see windows, roof, ground line. Frequently mistaken for a plan.
- A **section** is a vertical cut: you see floor slabs stacked and room heights.
- A **3D isometric/dollhouse plan** shows rooms from above but with visible wall height and perspective or axonometric projection. This is NOT an orthographic plan, even though it shows a layout.
- A **site plan** shows the plot, setbacks and building footprint but no interior room layout.

Then report which verification affordances the drawing carries. Be strict about `has_dual_units`: it is true only if you can see the same room annotated in BOTH millimetres and feet-inches.

List anything in `blocking_problems` that would make text extraction unreliable."""


@dataclass
class Classification:
    path: str
    kind: str
    usable: bool
    confidence: float
    reasoning: str
    raw: dict

    def as_dict(self) -> dict:
        d = asdict(self); d.pop("raw"); return {**d, **self.raw}


# The API accepts only these four. Real-estate sites serve .avif and .heic freely,
# so anything else is transcoded rather than rejected.
SUPPORTED = {"image/jpeg", "image/png", "image/gif", "image/webp"}


def _sniff(path: str) -> str | None:
    """Real format from magic bytes. Real-estate sites routinely serve a JPEG
    named .webp, and the API rejects a media_type that disagrees with the bytes."""
    with open(path, "rb") as f:
        h = f.read(16)
    if h[:3] == b"\xff\xd8\xff": return "image/jpeg"
    if h[:8] == b"\x89PNG\r\n\x1a\n": return "image/png"
    if h[:6] in (b"GIF87a", b"GIF89a"): return "image/gif"
    if h[:4] == b"RIFF" and h[8:12] == b"WEBP": return "image/webp"
    return None


def _b64(path: str) -> tuple[str, str]:
    mt = _sniff(path) or mimetypes.guess_type(path)[0] or "image/jpeg"
    if mt == "image/jpg":
        mt = "image/jpeg"
    if mt in SUPPORTED:
        return base64.standard_b64encode(open(path, "rb").read()).decode(), mt
    from io import BytesIO
    from PIL import Image
    im = Image.open(path)
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    buf = BytesIO()
    im.save(buf, format="PNG")
    return base64.standard_b64encode(buf.getvalue()).decode(), "image/png"


def classify(path: str, client: Anthropic | None = None,
             model: str = MODEL) -> Classification:
    c = client or Anthropic()
    data, mt = _b64(path)
    r = c.messages.create(
        model=model, max_tokens=1200, tools=[TOOL],
        tool_choice={"type": "tool", "name": "classify_plan_image"},
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": mt, "data": data}},
            {"type": "text", "text": PROMPT}]}])
    out = next(b.input for b in r.content if b.type == "tool_use")
    # `usable` is deliberately stricter than "is a floor plan": without printed
    # dimensions there is nothing to verify against, so the plan cannot be ground truth.
    usable = bool(
        out["kind"] == "floor_plan_2d"
        and out["is_orthographic_plan_view"]
        and out["has_room_labels"]
        and out["has_printed_room_dimensions"]
        and not out["blocking_problems"]
    )
    return Classification(path=path, kind=out["kind"], usable=usable,
                          confidence=out["confidence"], reasoning=out["reasoning"],
                          raw=out)
