"""Headless SVG renderer: IR -> drawing, for humans and for a vision critic.

Two modes over one geometry pipeline:
  * ``presentation`` — sales/municipal drawing: wall poche, feet-inch dimension
    strings, door swings and window glyphs, north arrow, scale bar, plot and
    setback lines, area statement.
  * ``annotated``    — flat high-contrast render for a vision model, with wall
    and room IDs and ``findings`` highlighted. It exists for *presentation*
    defects only (label collisions, cramped proportions); geometric correctness
    is the rules engine's job and this renderer asserts nothing about it.

Why pure string generation: the server renders in-process under plain CPython,
so no DOM, no canvas, no browser. And why the obsessive formatting/sorting —
output must be byte-identical for identical input, which is what makes
golden-file regression tests possible. Every float goes through ``_n``, every
collection that reaches the output is explicitly ordered.

Sheet space is **paper millimetres**: at 1:100 one sheet mm is 100 world mm, so
``width="210mm"`` prints true to the stated scale. Text sizes are therefore real
drafting heights (2.5 mm nominal).

Coordinate convention: world +Y maps to sheet +Y (down the page). ResPlan, the
only current IR producer, is raster-derived (Y grows downward), so the identity
map keeps drawings un-mirrored; pass ``flip_y=True`` for a Y-up producer. The
north arrow is derived from ``site.north_deg`` in the same frame.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box as shp_box
from shapely.ops import polylabel, unary_union

from .ir import Opening, P, Plan, Room, Wall

SQFT_PER_M2 = 10.7639
MM_PER_INCH = 25.4

# ── sheet furniture (all in sheet mm) ────────────────────────────────────────
PAD_L = 34.0        # room for the left dimension chains
PAD_B = 34.0        # room for the bottom dimension chains
PAD_T = 13.0
PAD_R = 13.0
PANEL_W = 68.0
TITLE_H = 17.0
ROW = 4.1           # panel row pitch
DIM_CHAIN_OFF = 9.0     # first dimension line, from the outer wall face
DIM_TOTAL_OFF = 19.0    # overall dimension line
DIM_PLOT_OFF = 27.0     # plot / setback dimension line
SCALE_LADDER = (100.0, 200.0, 500.0, 1000.0)
SHEET_MAX = 900.0   # step the scale down rather than exceed this sheet edge


# --------------------------------------------------------------------- themes
# A theme is a palette plus a type scale, selected with `theme=` so the
# restrained version is not lost when a bolder one is wanted. `mono` is the
# original: correct for a drawing an architect will mark up. `bold` is for a
# client-facing sheet, where legibility on a phone screen beats drafting
# convention -- stronger zone-coded fills, near-black poche, larger bold room
# names.
#
# Fills are ZONE-CODED rather than arbitrary, so the colour carries information:
# public warm, private blush, service cool, wet blue, outdoor green,
# circulation grey. That way a glance shows the zoning the plan is judged on.
THEMES: dict[str, dict] = {
    "mono": {
        "palette": {
            "bg": "#ffffff", "ink": "#18181b", "hair": "#a1a1aa", "rule": "#52525b",
            "poche_fill": "#52525b", "poche_line": "#18181b", "hatch": "#71717a",
            "dim": "#3f3f46", "plot": "#8a7a55", "setback": "#b45309",
            "sym": "#3f3f46", "faint": "#d4d4d8", "sub": "#71717a",
        },
        "tint": {
            "living": "#f7f3ea", "dining": "#f7f3ea", "kitchen": "#eef2ef",
            "bedroom": "#f8f6f2", "master_bedroom": "#f8f6f2",
            "bathroom": "#ebf1f4", "balcony": "#f1f4ef", "store": "#f2f1ee",
        },
        "fonts": {"room": 2.7, "steps": (2.7, 2.3, 2.0, 1.75, 1.5),
                  "room_bold": True, "dim": 1.7, "overall": 2.2},
        "poche_weight": 0.35,
    },
    "bold": {
        "palette": {
            "bg": "#ffffff", "ink": "#0f172a", "hair": "#94a3b8", "rule": "#334155",
            "poche_fill": "#1e293b", "poche_line": "#0f172a", "hatch": "#475569",
            "dim": "#1e293b", "plot": "#a16207", "setback": "#c2410c",
            "sym": "#1e293b", "faint": "#cbd5e1", "sub": "#475569",
        },
        "tint": {
            # public — warm sand
            "living": "#f6e2b8", "dining": "#f6e7c6", "pooja": "#f5e0a8",
            # private — blush
            "bedroom": "#f2ddd6", "master_bedroom": "#eed3c8", "study": "#e9dcc9",
            # service — sage / cool
            "kitchen": "#cfe0cd", "utility": "#d9e2d6", "store": "#dcdcd4",
            # wet — blue
            "bathroom": "#c3dced",
            # outdoor — green
            "balcony": "#cfe6c4", "sitout": "#d6e9cb", "patio": "#dceccf",
            "landscape": "#d9ecd0", "parking": "#dddddd",
            # circulation — neutral
            "foyer": "#e8e8e4", "passage": "#ececea", "stair": "#d6dde6",
            "shaft": "#cbd5e1",
        },
        "fonts": {"room": 3.3, "steps": (3.3, 2.9, 2.5, 2.1, 1.7),
                  "room_bold": True, "dim": 2.0, "overall": 2.7},
        "poche_weight": 0.5,
    },
}
DEFAULT_THEME = "bold"

PALETTE = {
    "presentation": {
        "bg": "#ffffff", "ink": "#18181b", "hair": "#a1a1aa", "rule": "#52525b",
        "poche_fill": "#52525b", "poche_line": "#18181b", "hatch": "#71717a",
        "dim": "#3f3f46", "plot": "#8a7a55", "setback": "#b45309",
        "sym": "#3f3f46", "faint": "#d4d4d8", "sub": "#71717a",
    },
    "annotated": {
        "bg": "#ffffff", "ink": "#000000", "hair": "#000000", "rule": "#000000",
        "poche_fill": "#3f3f46", "poche_line": "#000000", "hatch": "#000000",
        "dim": "#000000", "plot": "#000000", "setback": "#000000",
        "sym": "#000000", "faint": "#9ca3af", "sub": "#000000",
    },
}

# Restrained category tints. Saturated fills read as a diagram, not a drawing.
ROOM_TINT = {
    "living": "#f7f3ea", "kitchen": "#eef2ef", "bedroom": "#f8f6f2",
    "bathroom": "#ebf1f4", "balcony": "#f1f4ef", "storage": "#f2f1ee",
}
SEVERITY = {
    "critical": "#b91c1c", "high": "#dc2626", "error": "#dc2626",
    "medium": "#d97706", "warn": "#d97706", "warning": "#d97706",
    "low": "#1d4ed8", "info": "#1d4ed8",
}
SEV_TAG = {"critical": "CRIT", "high": "HIGH", "error": "ERR", "medium": "MED",
           "warn": "WARN", "warning": "WARN", "low": "LOW", "info": "INFO"}


# ── formatting ───────────────────────────────────────────────────────────────
def _n(v: float) -> str:
    """Fixed 2dp, with -0.00 normalised. Byte-identical output depends on this."""
    s = f"{float(v):.2f}"
    return "0.00" if s in ("-0.00", "-0") else s


def _esc(s: Any) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def ft_in(mm: float) -> str:
    """Feet-and-inches to the nearest inch: Indian plots are quoted in feet."""
    inches = abs(mm) / MM_PER_INCH
    ft = int(inches // 12)
    rem = int(round(inches - ft * 12))
    if rem == 12:
        ft, rem = ft + 1, 0
    return f"{ft}'-{rem}\""


def sqft(mm2: float) -> int:
    return int(round(mm2 / 1_000_000.0 * SQFT_PER_M2))


def _grp(n: int) -> str:
    return f"{n:,}"


def _m2(mm2: float) -> str:
    return f"{mm2 / 1_000_000.0:.1f}"


# ── text metrics ─────────────────────────────────────────────────────────────
# Approximate Helvetica/Arial advances in em. Labels are laid out against these,
# so the numbers only need to be consistent and slightly conservative.
_NARROW = set("'\".,:;!ilj|I()[]{}`")
_THIN = set("-tfrJ1/\\ ")
_WIDE = set("MW@mw%")
ASCENT = 0.76
DESCENT = 0.24
LINE_PITCH = 1.32


def char_w(ch: str) -> float:
    if ch in _NARROW:
        return 0.28
    if ch in _THIN:
        return 0.40
    if ch in _WIDE:
        return 0.90
    if ch.isupper() or ch.isdigit():
        return 0.66
    return 0.55


def text_extents(text: str, font_size: float, bold: bool = False) -> tuple[float, float]:
    """Estimated (width, height) of a text run in sheet mm."""
    w = sum(char_w(c) for c in text) * font_size * (1.05 if bold else 1.0)
    return w, font_size * (ASCENT + DESCENT)


_ANCHOR_FRAC = {"start": 0.0, "middle": 0.5, "end": 1.0}


def text_box(x: float, y: float, text: str, font_size: float, anchor: str = "middle",
             bold: bool = False, rot: float = 0.0) -> tuple[float, float, float, float]:
    """Axis-aligned bounding box of a text run placed at baseline point (x, y)."""
    w, h = text_extents(text, font_size, bold)
    dx = w * _ANCHOR_FRAC.get(anchor, 0.5)
    x0, y0 = x - dx, y - font_size * ASCENT
    if abs(rot) > 1e-9:
        # only 90 deg rotations are emitted; rotate the box about (x, y)
        a = math.radians(rot)
        ca, sa = math.cos(a), math.sin(a)
        pts = [(x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h)]
        rp = [((px - x) * ca - (py - y) * sa + x, (px - x) * sa + (py - y) * ca + y)
              for px, py in pts]
        return (min(p[0] for p in rp), min(p[1] for p in rp),
                max(p[0] for p in rp), max(p[1] for p in rp))
    return x0, y0, x0 + w, y0 + h


Box = tuple[float, float, float, float]


class Placer:
    """Global label bookkeeper. Text collisions are the failure mode this
    renderer is judged on, so *every* text run is booked through here."""

    def __init__(self, pad: float = 0.5) -> None:
        self.boxes: list[Box] = []
        self.pad = pad

    def free(self, *boxes: Box) -> bool:
        for b in boxes:
            x0, y0, x1, y1 = b
            for a in self.boxes:
                if (x0 - self.pad < a[2] and a[0] - self.pad < x1
                        and y0 - self.pad < a[3] and a[1] - self.pad < y1):
                    return False
        # candidate boxes must also not collide with each other
        for i, b in enumerate(boxes):
            for c in boxes[i + 1:]:
                if (b[0] < c[2] and c[0] < b[2] and b[1] < c[3] and c[1] < b[3]):
                    return False
        return True

    def add(self, *boxes: Box) -> None:
        self.boxes.extend(tuple(float(v) for v in b) for b in boxes)

    def reserve(self, box: Box) -> None:
        self.add(box)


# ── element emitters ─────────────────────────────────────────────────────────
def _halo(box: Box, fill: str = "#ffffff", grow: float = 0.25) -> str:
    """Opaque backing for text that must stay legible over dark poche."""
    return _rect(box[0] - grow, box[1] - grow, box[2] - box[0] + 2 * grow,
                 box[3] - box[1] + 2 * grow, fill, cls="halo", opacity=0.9)


def _attrs(items: Sequence[tuple[str, Any]]) -> str:
    return "".join(f' {k}="{v}"' for k, v in items if v is not None)


def _line(x1: float, y1: float, x2: float, y2: float, stroke: str, sw: float,
          dash: str | None = None, opacity: float | None = None,
          cls: str | None = None, cap: str | None = None) -> str:
    return "<line" + _attrs([
        ("class", cls), ("x1", _n(x1)), ("y1", _n(y1)), ("x2", _n(x2)), ("y2", _n(y2)),
        ("stroke", stroke), ("stroke-width", _n(sw)), ("stroke-dasharray", dash),
        ("stroke-linecap", cap), ("stroke-opacity", None if opacity is None else _n(opacity)),
    ]) + "/>"


def _pts(pts: Iterable[tuple[float, float]]) -> str:
    return " ".join(f"{_n(x)},{_n(y)}" for x, y in pts)


def _polygon(pts, fill: str, stroke: str | None = None, sw: float = 0.0,
             opacity: float | None = None, cls: str | None = None,
             dash: str | None = None) -> str:
    return "<polygon" + _attrs([
        ("class", cls), ("points", _pts(pts)), ("fill", fill),
        ("fill-opacity", None if opacity is None else _n(opacity)),
        ("stroke", stroke), ("stroke-width", _n(sw) if stroke else None),
        ("stroke-dasharray", dash),
    ]) + "/>"


def _polyline(pts, stroke: str, sw: float, dash: str | None = None,
              cls: str | None = None) -> str:
    return "<polyline" + _attrs([
        ("class", cls), ("points", _pts(pts)), ("fill", "none"), ("stroke", stroke),
        ("stroke-width", _n(sw)), ("stroke-dasharray", dash),
    ]) + "/>"


def _rect(x: float, y: float, w: float, h: float, fill: str,
          stroke: str | None = None, sw: float = 0.0, cls: str | None = None,
          opacity: float | None = None) -> str:
    return "<rect" + _attrs([
        ("class", cls), ("x", _n(x)), ("y", _n(y)), ("width", _n(max(w, 0.0))),
        ("height", _n(max(h, 0.0))), ("fill", fill),
        ("fill-opacity", None if opacity is None else _n(opacity)),
        ("stroke", stroke), ("stroke-width", _n(sw) if stroke else None),
    ]) + "/>"


def _circle(cx: float, cy: float, r: float, fill: str, stroke: str | None = None,
            sw: float = 0.0, cls: str | None = None,
            opacity: float | None = None) -> str:
    return "<circle" + _attrs([
        ("class", cls), ("cx", _n(cx)), ("cy", _n(cy)), ("r", _n(r)), ("fill", fill),
        ("fill-opacity", None if opacity is None else _n(opacity)),
        ("stroke", stroke), ("stroke-width", _n(sw) if stroke else None),
    ]) + "/>"


def _path(d: str, fill: str = "none", stroke: str | None = None, sw: float = 0.0,
          cls: str | None = None, rule: str | None = None,
          opacity: float | None = None) -> str:
    return "<path" + _attrs([
        ("class", cls), ("d", d), ("fill", fill), ("fill-rule", rule),
        ("fill-opacity", None if opacity is None else _n(opacity)),
        ("stroke", stroke), ("stroke-width", _n(sw) if stroke else None),
    ]) + "/>"


def _text(x: float, y: float, s: str, fs: float, fill: str, anchor: str = "middle",
          bold: bool = False, rot: float = 0.0, cls: str | None = None,
          owner: str | None = None, extra: Sequence[tuple[str, Any]] = ()) -> str:
    """A text run. ``data-w``/``data-h``/``data-rot`` publish the layout engine's
    own extent model so tests can rebuild label boxes without guessing."""
    w, h = text_extents(s, fs, bold)
    tr = None if abs(rot) < 1e-9 else f"rotate({_n(rot)} {_n(x)} {_n(y)})"
    return "<text" + _attrs([
        ("class", cls), ("data-owner", owner), ("x", _n(x)), ("y", _n(y)),
        ("transform", tr), ("text-anchor", anchor),
        ("font-family", "Helvetica, Arial, sans-serif"), ("font-size", _n(fs)),
        ("font-weight", "bold" if bold else None), ("fill", fill),
        ("data-w", _n(w)), ("data-h", _n(h)), ("data-rot", _n(rot)),
        *list(extra),
    ]) + f">{_esc(s)}</text>"


# ── geometry ─────────────────────────────────────────────────────────────────
def _pl(pts: Sequence[P]) -> Polygon | None:
    if len(pts) < 3:
        return None
    try:
        g = Polygon([(p.x, p.y) for p in pts])
        if not g.is_valid:
            g = g.buffer(0)
        if isinstance(g, MultiPolygon):
            g = max(g.geoms, key=lambda q: q.area)
        return g if (isinstance(g, Polygon) and g.area > 0) else None
    except Exception:
        return None


def _parts(g) -> list[Polygon]:
    if g is None or g.is_empty:
        return []
    gs = list(g.geoms) if hasattr(g, "geoms") else [g]
    out = [p for p in gs if isinstance(p, Polygon) and p.area > 0]
    # sorted for determinism: shapely's set-op output order is not contractual
    return sorted(out, key=lambda p: (round(p.bounds[0], 6), round(p.bounds[1], 6),
                                      round(p.area, 3)))


def wall_poche(plan: Plan) -> list[Polygon]:
    """Buffer each centreline by half its thickness and union, so that corners
    and tees resolve into one solid mass instead of overlapping bars."""
    bands = []
    for w in plan.walls:
        if w.length < 1e-6 or w.thickness <= 0:
            continue
        bands.append(LineString([(w.start.x, w.start.y), (w.end.x, w.end.y)])
                     .buffer(w.thickness / 2.0, cap_style=2, join_style=2))
    if not bands:
        return []
    parts = _parts(unary_union(bands))
    # Raster-derived IR carries 1-3 mm staircase jitter along wall faces; at
    # 1:100 that is sub-0.03 mm of paper but the outline stroke still shows it
    # as a comb. Simplify below a tenth of a wall thickness.
    tol = _median_thickness(plan) * 0.12
    out = []
    for p in parts:
        q = p.simplify(tol, preserve_topology=True)
        out.extend(_parts(q) or [p])
    return _parts(unary_union(out)) if out else parts


def _median_thickness(plan: Plan) -> float:
    t = sorted(w.thickness for w in plan.walls if w.thickness > 0)
    return float(t[len(t) // 2]) if t else 226.0


def opening_world(plan: Plan, op: Opening) -> tuple[float, float, float, float, Wall] | None:
    """Resolve a parametric opening to (cx, cy, ux, uy, wall) in world mm."""
    w = plan.wall(op.wall_id)
    if w is None:
        return None
    dx, dy = w.end.x - w.start.x, w.end.y - w.start.y
    ln = math.hypot(dx, dy)
    if ln < 1e-6:
        return None
    t = min(max(float(op.position), 0.0), 1.0)
    return (w.start.x + dx * t, w.start.y + dy * t, dx / ln, dy / ln, w)


def _rect_dims(poly: Polygon, tol: float = 0.02) -> tuple[float, float] | None:
    """(w, h) if the polygon fills its bounding box to within ``tol``.

    Tested by area, not vertex count: snapping leaves collinear vertices on
    ResPlan rectangles, so a 4-corner test misses most genuine rectangles.
    """
    x0, y0, x1, y1 = poly.bounds
    bb = (x1 - x0) * (y1 - y0)
    if bb <= 0 or poly.interiors:
        return None
    if abs(poly.area - bb) > tol * bb:
        return None
    return x1 - x0, y1 - y0


def _inner(poly: Polygon, inset: float) -> Polygon | None:
    try:
        g = poly.buffer(-inset, join_style=2)
    except Exception:
        return None
    ps = _parts(g)
    return max(ps, key=lambda p: p.area) if ps else None


def _label_point(poly: Polygon) -> tuple[float, float]:
    """Pole of inaccessibility — the interior point furthest from any edge, which
    is where a label has the most room. Guaranteed inside, unlike the centroid."""
    try:
        tol = max(math.sqrt(poly.area) / 40.0, 1e-6)
        p = polylabel(poly, tolerance=tol)
        if poly.contains(p):
            return p.x, p.y
    except Exception:
        pass
    p = poly.representative_point()
    return p.x, p.y


# ── document / transform ─────────────────────────────────────────────────────
class _Doc:
    """Layered accumulator. Layer order fixes z-order, so drawing code can emit
    in whatever order is convenient and stay deterministic."""

    # `furniture` sits between rooms and poche so a piece touching a wall reads
    # under it, which is the plan convention.
    ORDER = ("defs", "sheet", "plot", "rooms", "furniture", "poche", "openings",
             "dims", "labels", "panel", "overlay")

    def __init__(self) -> None:
        self.layers: dict[str, list[str]] = {k: [] for k in self.ORDER}

    def add(self, layer: str, *els: str) -> None:
        self.layers[layer].extend(els)


@dataclass
class _Ctx:
    plan: Plan
    mode: str
    scale: float                 # world mm per sheet mm
    minx: float
    miny: float
    maxy: float
    flip: bool
    ink: dict
    tint: dict = field(default_factory=dict)
    fonts: dict = field(default_factory=dict)
    poche_weight: float = 0.35
    pad_l: float = PAD_L
    pad_t: float = PAD_T
    thick: float = 226.0

    def X(self, wx: float) -> float:
        return self.pad_l + (wx - self.minx) / self.scale

    def Y(self, wy: float) -> float:
        if self.flip:
            return self.pad_t + (self.maxy - wy) / self.scale
        return self.pad_t + (wy - self.miny) / self.scale

    def L(self, mm: float) -> float:
        return mm / self.scale

    def pt(self, wx: float, wy: float) -> tuple[float, float]:
        return self.X(wx), self.Y(wy)

    def ring(self, coords) -> list[tuple[float, float]]:
        return [self.pt(x, y) for x, y in coords]

    def sheet_poly(self, poly: Polygon) -> Polygon:
        """Same polygon in sheet mm — label fitting is all done in sheet space."""
        ext = self.ring(poly.exterior.coords)
        ints = [self.ring(r.coords) for r in poly.interiors]
        g = Polygon(ext, ints)
        return g if g.is_valid else g.buffer(0)


def _poly_path(ctx: _Ctx, polys: Sequence[Polygon]) -> str:
    """One even-odd path for a set of polygons with holes (the poche mass)."""
    out = []
    for p in polys:
        for ring in [p.exterior, *p.interiors]:
            cs = ctx.ring(ring.coords)
            if len(cs) < 3:
                continue
            out.append("M " + " L ".join(f"{_n(x)} {_n(y)}" for x, y in cs) + " Z")
    return " ".join(out)


# ── plan body ────────────────────────────────────────────────────────────────
def _draw_rooms(doc: _Doc, ctx: _Ctx, rooms: Sequence[tuple[Room, Polygon]]) -> None:
    tint = ctx.mode == "presentation"
    for room, poly in rooms:
        pts = ctx.ring(poly.exterior.coords)
        if len(pts) < 3:
            continue
        fill = (ctx.tint or ROOM_TINT).get(room.category, "#f4f4f5") if tint else "#ffffff"
        doc.add("rooms", _polygon(pts, fill, cls=f"room room-{room.category}"))
        if not tint:                      # annotated: outline every face
            doc.add("rooms", _polyline(pts + pts[:1], ctx.ink["faint"], 0.2))



# ------------------------------------------------------------------ furniture
# `plan.furniture` was previously ignored entirely, so every furniture example in
# the suite rendered as an empty room while scoring PASS. Drawn between the rooms
# and the poche so walls read over the top of a piece that touches them.
#
# Symbols (electrical, plumbing) have zero height and are drawn as glyphs rather
# than boxes; real furniture is drawn as its footprint with a facing tick, which
# is the plan convention.
_FURN_FILL = {
    "bathroom": "#e2e8f0", "kitchen": "#e5e7eb", "decor": "#eef2e6",
}
_SYMBOL_R = 1.1          # paper mm


def _furn_footprint(f, cat: dict) -> tuple[float, float]:
    w = f.width or cat.get("width_mm") or 600
    d = f.depth or cat.get("depth_mm") or 600
    return float(w), float(d)


def _draw_furniture(doc: _Doc, ctx: _Ctx, plan: Plan) -> int:
    items = list(getattr(plan, "furniture", None) or [])
    if not items:
        return 0
    try:
        from .catalog import get as cat_get
    except Exception:
        cat_get = None

    n = 0
    for f in items:
        info = {}
        if cat_get is not None:
            try:
                it = cat_get(f.catalog_id)
                info = {"width_mm": getattr(it, "width_mm", None),
                        "depth_mm": getattr(it, "depth_mm", None),
                        "height_mm": getattr(it, "height_mm", None),
                        "category": (getattr(it, "category", "") or "").lower()}
            except Exception:
                info = {}
        cx, cy = ctx.pt(f.position.x, f.position.y)

        # A zero-height catalogue item is a 2D symbol, not a solid.
        if (info.get("height_mm") or 1) <= 0:
            doc.add("furniture", _circle(cx, cy, _SYMBOL_R, "none",
                                         ctx.ink["sym"], 0.25))
            doc.add("furniture", _line(cx - _SYMBOL_R, cy, cx + _SYMBOL_R, cy,
                                       ctx.ink["sym"], 0.25))
            n += 1
            continue

        w_mm, d_mm = _furn_footprint(f, info)
        hw, hd = w_mm / 2.0, d_mm / 2.0
        a = math.radians(f.rotation or 0.0)
        ca, sa = math.cos(a), math.sin(a)
        corners = []
        for dx, dy in ((-hw, -hd), (hw, -hd), (hw, hd), (-hw, hd)):
            corners.append(ctx.pt(f.position.x + dx * ca - dy * sa,
                                  f.position.y + dx * sa + dy * ca))
        fill = _FURN_FILL.get(info.get("category", ""), "#ffffff")
        doc.add("furniture", _polygon(corners, fill, ctx.ink["sym"], 0.2,
                                      cls=f"furn furn-{f.catalog_id}"))
        # Facing tick on the front edge (local -Y), so orientation is legible.
        fx, fy = ctx.pt(f.position.x - (-hd) * sa, f.position.y + (-hd) * ca)
        doc.add("furniture", _line(cx, cy, fx, fy, ctx.ink["faint"], 0.18))
        n += 1
    return n


def _draw_poche(doc: _Doc, ctx: _Ctx, poche: Sequence[Polygon],
                hatch: bool = False) -> None:
    if not poche:
        return
    d = _poly_path(ctx, poche)
    if not d:
        return
    fill = "url(#hatch)" if hatch else ctx.ink["poche_fill"]
    doc.add("poche", _path(d, fill=fill, rule="evenodd", cls="poche"))
    doc.add("poche", _path(d, fill="none", stroke=ctx.ink["poche_line"],
                           sw=0.3 if ctx.mode == "presentation" else 0.25,
                           cls="poche-outline"))


def _swing_side(ctx: _Ctx, rooms: Sequence[tuple[Room, Polygon]], cx: float, cy: float,
                ux: float, uy: float, width: float, inward: bool) -> int:
    """+1/-1 normal side a leaf should swing to: into a room, the larger one if
    both sides are rooms. Deterministic because ``rooms`` arrives sorted."""
    nx, ny = -uy, ux
    probe = max(width * 0.6, 400.0)
    best, best_area = 1, -1.0
    hit = False
    for sign in (1, -1):
        p = Point(cx + nx * probe * sign, cy + ny * probe * sign)
        for _, poly in rooms:
            if poly.contains(p):
                hit = True
                if poly.area > best_area:
                    best, best_area = sign, poly.area
                break
    if not hit:
        return 1
    return best


def _draw_openings(doc: _Doc, ctx: _Ctx, rooms: Sequence[tuple[Room, Polygon]]) -> int:
    ink = ctx.ink
    drawn = 0
    for op in sorted(ctx.plan.openings, key=lambda o: o.id):
        res = opening_world(ctx.plan, op)
        if res is None:
            continue
        cx, cy, wux, wuy, wall = res
        sx, sy = ctx.pt(cx, cy)
        ux, uy = (wux, -wuy) if ctx.flip else (wux, wuy)
        nx, ny = -uy, ux
        hw = ctx.L(op.width) / 2.0
        th = max(ctx.L(wall.thickness) / 2.0, 0.25)
        if hw < 0.05:
            continue
        g = ["<g" + _attrs([
            ("class", f"opening opening-{op.kind}"), ("data-opening", _esc(op.id)),
            ("data-wall", _esc(op.wall_id)), ("data-kind", op.kind),
            ("data-wx", _n(cx)), ("data-wy", _n(cy)), ("data-t", _n(op.position)),
            ("data-width", _n(op.width)),
        ]) + ">"]
        # 1. knock the reveal out of the poche
        corners = [(sx - ux * hw + nx * th, sy - uy * hw + ny * th),
                   (sx + ux * hw + nx * th, sy + uy * hw + ny * th),
                   (sx + ux * hw - nx * th, sy + uy * hw - ny * th),
                   (sx - ux * hw - nx * th, sy - uy * hw - ny * th)]
        g.append(_polygon(corners, ink["bg"], cls="reveal"))
        # 2. jamb ticks
        for s in (-1.0, 1.0):
            jx, jy = sx + ux * hw * s, sy + uy * hw * s
            g.append(_line(jx + nx * th, jy + ny * th, jx - nx * th, jy - ny * th,
                           ink["sym"], 0.3))
        if op.kind == "window":
            gap = th * 0.92
            for o in (-gap, 0.0, gap):     # conventional 3-line glyph
                g.append(_line(sx - ux * hw + nx * o, sy - uy * hw + ny * o,
                               sx + ux * hw + nx * o, sy + uy * hw + ny * o,
                               ink["sym"], 0.20 if o == 0.0 else 0.28))
        else:
            side = _swing_side(ctx, rooms, cx, cy, wux, wuy, op.width,
                               op.kind == "front_door")
            if ctx.flip:
                side = -side
            # hinge on the jamb nearer a junction, so the leaf opens off the corner
            hs = _hinge_sign(ctx, wall, cx, cy, wux, wuy, op.width)
            hx, hy = sx + ux * hw * hs, sy + uy * hw * hs
            r = hw * 2.0
            odx, ody = nx * side, ny * side          # leaf open, 90 deg
            cdx, cdy = -ux * hs, -uy * hs            # leaf closed, in the wall
            a0 = math.atan2(ody, odx)
            a1 = math.atan2(cdy, cdx)
            sweep = 1 if ((a1 - a0) % (2 * math.pi)) < math.pi else 0
            g.append(_path(
                f"M {_n(hx + odx * r)} {_n(hy + ody * r)} "
                f"A {_n(r)} {_n(r)} 0 0 {sweep} "
                f"{_n(hx + cdx * r)} {_n(hy + cdy * r)}",
                stroke=ink["sym"], sw=0.18, cls="swing"))
            g.append(_line(hx, hy, hx + odx * r, hy + ody * r, ink["sym"],
                           0.45 if op.kind == "front_door" else 0.35, cls="leaf"))
            g.append(_circle(hx, hy, 0.28, ink["sym"], cls="hinge"))
        g.append("</g>")
        doc.add("openings", "".join(g))
        drawn += 1
    return drawn


def _hinge_sign(ctx: _Ctx, wall: Wall, cx: float, cy: float, ux: float, uy: float,
                width: float) -> float:
    """Which jamb takes the hinge: the one closer to a wall junction."""
    nodes = []
    for w in ctx.plan.walls:
        nodes.append((w.start.x, w.start.y))
        nodes.append((w.end.x, w.end.y))
    best = 1.0
    bd = float("inf")
    for s in (-1.0, 1.0):
        jx, jy = cx + ux * width / 2.0 * s, cy + uy * width / 2.0 * s
        d = min((math.hypot(jx - nx, jy - ny) for nx, ny in nodes), default=0.0)
        if d < bd - 1e-9:
            best, bd = s, d
    return best


# ── room labels ──────────────────────────────────────────────────────────────
@dataclass
class _LabelStats:
    full: int = 0
    reduced: int = 0
    keyed: int = 0
    dropped: int = 0


def _room_label_variants(ctx: _Ctx, room: Room, poly: Polygon, key: str,
                         carpet_mm2: float) -> list[list[tuple[str, bool, float]]]:
    """Label content, richest first. (text, bold, font-scale)."""
    name = room.name.upper()
    dims = _rect_dims(poly)
    area_txt = f"{_grp(sqft(carpet_mm2))} SQ FT"
    m2_txt = f"({_m2(carpet_mm2)} m²)"
    out: list[list[tuple[str, bool, float]]] = []
    if ctx.mode == "annotated":
        tag = f"{room.id} {name}"
        return [[(tag, True, 1.0), (area_txt, False, 0.82)],
                [(tag, True, 0.9)], [(room.id, True, 0.9)], [(key, True, 1.0)]]
    if dims:
        span = f"{ft_in(dims[0] - ctx.thick)} X {ft_in(dims[1] - ctx.thick)}"
        out.append([(name, True, 1.0), (span, False, 0.85), (area_txt, False, 0.85),
                    (m2_txt, False, 0.7)])
        out.append([(name, True, 1.0), (span, False, 0.85), (area_txt, False, 0.85)])
        out.append([(name, True, 1.0), (span, False, 0.8)])
    out.append([(name, True, 1.0), (area_txt, False, 0.85), (m2_txt, False, 0.7)])
    out.append([(name, True, 1.0), (area_txt, False, 0.85)])
    out.append([(name, True, 0.95)])
    out.append([(key, True, 1.1)])
    return out


def _place_room_labels(doc: _Doc, ctx: _Ctx, placer: Placer,
                       rooms: Sequence[tuple[Room, Polygon]], keys: dict[str, str],
                       carpet: dict[str, float]) -> _LabelStats:
    st = _LabelStats()
    ink = ctx.ink
    base_fonts = tuple(ctx.fonts.get("steps", (2.7, 2.3, 2.0, 1.75, 1.5)))
    # biggest rooms first: they carry the most information and should not lose it
    order = sorted(rooms, key=lambda rp: (-rp[1].area, rp[0].id))
    for room, poly in order:
        sp = ctx.sheet_poly(poly)
        if sp.is_empty:
            st.dropped += 1
            continue
        inner = _inner(sp, ctx.L(ctx.thick) / 2.0 + 0.35) or sp
        ax, ay = _label_point(inner)
        bx0, by0, bx1, by1 = inner.bounds
        offs = [(0.0, 0.0)]
        for fx, fy in ((0, -0.28), (0, 0.28), (-0.3, 0), (0.3, 0),
                       (-0.3, -0.28), (0.3, -0.28), (-0.3, 0.28), (0.3, 0.28)):
            offs.append((fx * (bx1 - bx0), fy * (by1 - by0)))
        variants = _room_label_variants(ctx, room, poly, keys[room.id],
                                        carpet.get(room.id, float(room.area)))
        placed = None
        for vi, variant in enumerate(variants):
            for f0 in base_fonts:
                lines = [(t, b, round(f0 * s, 2)) for t, b, s in variant]
                blk_h = sum(fs * LINE_PITCH for _, _, fs in lines)
                for ox, oy in offs:
                    cx, cy = ax + ox, ay + oy
                    y = cy - blk_h / 2.0
                    boxes, els = [], []
                    ok = True
                    for t, b, fs in lines:
                        y += fs * LINE_PITCH
                        bl = y - fs * (LINE_PITCH - 1.0) * 0.5
                        bb = text_box(cx, bl, t, fs, "middle", b)
                        if not inner.contains(shp_box(*bb)):
                            ok = False
                            break
                        boxes.append(bb)
                        els.append((cx, bl, t, fs, b))
                    if not ok or not placer.free(*boxes):
                        continue
                    placed = (vi, boxes, els)
                    break
                if placed:
                    break
            if placed:
                break
        if placed is None:
            st.dropped += 1
            continue
        vi, boxes, els = placed
        placer.add(*boxes)
        n_lines = len(els)
        if vi == 0:
            st.full += 1
        elif n_lines == 1 and els[0][2] == keys[room.id]:
            st.keyed += 1
        else:
            st.reduced += 1
        for i, (cx, bl, t, fs, b) in enumerate(els):
            doc.add("labels", _text(cx, bl, t, fs,
                                    ink["ink"] if i == 0 else ink["sub"],
                                    bold=b, cls="room-label", owner=room.id,
                                    extra=[("data-line", str(i))]))
    return st


# ── dimensions ───────────────────────────────────────────────────────────────
TICK = 1.1


def _h_dim(doc: _Doc, ctx: _Ctx, placer: Placer, x0: float, x1: float, y: float,
           label: str, ext_y: float | None = None, fs: float = 2.0,
           layer: str = "dims") -> bool:
    """Horizontal dimension: witness lines, oblique ticks, text in a line break."""
    ink = ctx.ink
    if x1 < x0:
        x0, x1 = x1, x0
    if x1 - x0 < 0.6:
        return False
    if ext_y is not None:
        for x in (x0, x1):
            doc.add(layer, _line(x, ext_y, x, y + math.copysign(1.6, y - ext_y),
                                 ink["hair"], 0.12, cls="witness"))
    tb = text_box((x0 + x1) / 2.0, y - 0.9, label, fs, "middle")
    show = placer.free(tb) and (tb[2] - tb[0]) < (x1 - x0) - 0.8
    if show:
        placer.add(tb)
        half = (tb[2] - tb[0]) / 2.0 + 0.7
        mx = (x0 + x1) / 2.0
        doc.add(layer, _line(x0, y, mx - half, y, ink["dim"], 0.18, cls="dim"))
        doc.add(layer, _line(mx + half, y, x1, y, ink["dim"], 0.18, cls="dim"))
        doc.add(layer, _text(mx, y - 0.9, label, fs, ink["dim"], cls="dim-text",
                             owner="dim"))
    else:
        doc.add(layer, _line(x0, y, x1, y, ink["dim"], 0.18, cls="dim"))
    for x in (x0, x1):
        doc.add(layer, _line(x - TICK / 2, y + TICK / 2, x + TICK / 2, y - TICK / 2,
                             ink["dim"], 0.25, cls="dim-tick"))
    return show


def _v_dim(doc: _Doc, ctx: _Ctx, placer: Placer, y0: float, y1: float, x: float,
           label: str, ext_x: float | None = None, fs: float = 2.0,
           layer: str = "dims") -> bool:
    ink = ctx.ink
    if y1 < y0:
        y0, y1 = y1, y0
    if y1 - y0 < 0.6:
        return False
    if ext_x is not None:
        for y in (y0, y1):
            doc.add(layer, _line(ext_x, y, x + math.copysign(1.6, x - ext_x), y,
                                 ink["hair"], 0.12, cls="witness"))
    tb = text_box(x - 0.9, (y0 + y1) / 2.0, label, fs, "middle", rot=-90.0)
    show = placer.free(tb) and (tb[3] - tb[1]) < (y1 - y0) - 0.8
    if show:
        placer.add(tb)
        half = (tb[3] - tb[1]) / 2.0 + 0.7
        my = (y0 + y1) / 2.0
        doc.add(layer, _line(x, y0, x, my - half, ink["dim"], 0.18, cls="dim"))
        doc.add(layer, _line(x, my + half, x, y1, ink["dim"], 0.18, cls="dim"))
        doc.add(layer, _text(x - 0.9, my, label, fs, ink["dim"], rot=-90.0,
                             cls="dim-text", owner="dim"))
    else:
        doc.add(layer, _line(x, y0, x, y1, ink["dim"], 0.18, cls="dim"))
    for y in (y0, y1):
        doc.add(layer, _line(x + TICK / 2, y - TICK / 2, x - TICK / 2, y + TICK / 2,
                             ink["dim"], 0.25, cls="dim-tick"))
    return show


def _merge_coords(vals: Sequence[float], tol: float) -> list[float]:
    out: list[float] = []
    for v in sorted(vals):
        if not out or v - out[-1] > tol:
            out.append(v)
    return out


def _dimension_plan(doc: _Doc, ctx: _Ctx, placer: Placer,
                    poche: Sequence[Polygon], content: Box) -> dict:
    """Overall dimensions on two sides plus a grid chain of wall positions.

    Dimension lines are offset from the *content* edge, not the building face,
    so that on a real site they fall outside the plot boundary instead of
    across the garden. Witness lines still start at the wall face.

    Chains carry a segment tick even where the text will not fit; a suppressed
    number is better than an overlapped one, and the overall dimension above it
    still closes the string.
    """
    if not poche:
        return {"chain_text": 0, "chain_seg": 0}
    fp = unary_union(poche)
    wx0, wy0, wx1, wy1 = fp.bounds
    sx0, sy0 = ctx.pt(wx0, wy0)
    sx1, sy1 = ctx.pt(wx1, wy1)
    if sy1 < sy0:
        sy0, sy1 = sy1, sy0
    tol = ctx.thick * 0.9
    vx = _merge_coords([wx0, wx1] + [w.start.x for w in ctx.plan.walls
                                     if abs(w.end.x - w.start.x) <= 1.0], tol)
    hy = _merge_coords([wy0, wy1] + [w.start.y for w in ctx.plan.walls
                                     if abs(w.end.y - w.start.y) <= 1.0], tol)
    vx = [v for v in vx if wx0 - tol <= v <= wx1 + tol]
    hy = [v for v in hy if wy0 - tol <= v <= wy1 + tol]

    stats = {"chain_text": 0, "chain_seg": 0}
    chain_y = content[3] + DIM_CHAIN_OFF
    for a, b in zip(vx, vx[1:]):
        stats["chain_seg"] += 1
        if _h_dim(doc, ctx, placer, ctx.X(a), ctx.X(b), chain_y, ft_in(b - a),
                  ext_y=sy1, fs=1.7):
            stats["chain_text"] += 1
    chain_x = content[0] - DIM_CHAIN_OFF
    for a, b in zip(hy, hy[1:]):
        stats["chain_seg"] += 1
        if _v_dim(doc, ctx, placer, ctx.Y(a), ctx.Y(b), chain_x, ft_in(b - a),
                  ext_x=sx0, fs=1.7):
            stats["chain_text"] += 1
    # overall, both sides
    _h_dim(doc, ctx, placer, sx0, sx1, content[3] + DIM_TOTAL_OFF,
           f"{ft_in(wx1 - wx0)}  ({int(round(wx1 - wx0))})", ext_y=sy1, fs=2.2)
    _v_dim(doc, ctx, placer, sy0, sy1, content[0] - DIM_TOTAL_OFF,
           f"{ft_in(wy1 - wy0)}  ({int(round(wy1 - wy0))})", ext_x=sx0, fs=2.2)
    return stats


# ── plot boundary and setbacks ───────────────────────────────────────────────
def _draw_plot(doc: _Doc, ctx: _Ctx, placer: Placer, plot: Polygon | None,
               setback_poly: Polygon | None) -> None:
    ink = ctx.ink
    if plot is None:
        return
    doc.add("plot", _polygon(ctx.ring(plot.exterior.coords), "none", ink["plot"],
                             0.4, cls="plot-boundary", dash="4,1.2,0.6,1.2"))
    if setback_poly is not None and not setback_poly.is_empty:
        for part in _parts(setback_poly):
            doc.add("plot", _polygon(ctx.ring(part.exterior.coords), "none",
                                     ink["setback"], 0.25, cls="setback-line",
                                     dash="2,1.4"))
    sb = ctx.plan.site.setbacks_mm or {}
    if not sb:
        return
    px0, py0, px1, py1 = plot.bounds
    # call the setback out on each side it is declared for, measured off the
    # plot line; only meaningful for axis-aligned plots, which is the norm.
    sides = (("front", "y", py0), ("rear", "y", py1),
             ("left", "x", px0), ("right", "x", px1))
    for name, axis, at in sides:
        d = sb.get(name)
        if not d:
            continue
        if axis == "y":
            inward = 1.0 if at == py0 else -1.0
            y_a, y_b = ctx.Y(at), ctx.Y(at + d * inward)
            x = ctx.X((px0 + px1) / 2.0) + (0.0 if at == py0 else 6.0)
            _v_dim(doc, ctx, placer, y_a, y_b, x, f"{name.upper()} {ft_in(d)}",
                   fs=1.7)
        else:
            inward = 1.0 if at == px0 else -1.0
            x_a, x_b = ctx.X(at), ctx.X(at + d * inward)
            y = ctx.Y((py0 + py1) / 2.0) + (0.0 if at == px0 else 6.0)
            _h_dim(doc, ctx, placer, x_a, x_b, y, f"{name.upper()} {ft_in(d)}",
                   fs=1.7)


def _setback_polygon(plot: Polygon | None, sb: dict) -> Polygon | None:
    """Buildable envelope. Per-side inset for an axis-aligned plot (the usual
    case), otherwise a uniform inward buffer at the smallest declared setback."""
    if plot is None or not sb:
        return None
    vals = [v for v in sb.values() if isinstance(v, (int, float)) and v > 0]
    if not vals:
        return None
    x0, y0, x1, y1 = plot.bounds
    rect = shp_box(x0, y0, x1, y1)
    if abs(rect.area - plot.area) < 0.01 * rect.area:
        nx0 = x0 + float(sb.get("left", min(vals)))
        nx1 = x1 - float(sb.get("right", min(vals)))
        ny0 = y0 + float(sb.get("front", min(vals)))
        ny1 = y1 - float(sb.get("rear", min(vals)))
        if nx1 <= nx0 or ny1 <= ny0:
            return None
        return shp_box(nx0, ny0, nx1, ny1)
    g = plot.buffer(-float(min(vals)), join_style=2)
    ps = _parts(g)
    return max(ps, key=lambda p: p.area) if ps else None


# ── north arrow, scale bar ───────────────────────────────────────────────────
def north_vector(north_deg: float, flip: bool) -> tuple[float, float]:
    """Sheet-space unit vector pointing to true north.

    ``site.north_deg`` is the bearing of world +Y, clockwise from north. In a
    Y-down sheet frame the standard rotation matrix *is* a visual clockwise
    rotation, so north = R(-north_deg) applied to +Y = (sin d, cos d). With
    ``flip_y`` the sheet Y axis is reversed, so the Y component negates.
    """
    a = math.radians(float(north_deg))
    nx, ny = math.sin(a), math.cos(a)
    return (nx, -ny) if flip else (nx, ny)


def _north_arrow(doc: _Doc, ctx: _Ctx, placer: Placer, cx: float, cy: float,
                 r: float = 7.5) -> float:
    ink = ctx.ink
    nx, ny = north_vector(ctx.plan.site.north_deg, ctx.flip)
    px, py = -ny, nx
    tipx, tipy = cx + nx * r, cy + ny * r
    tailx, taily = cx - nx * r * 0.82, cy - ny * r * 0.82
    doc.add("panel", _circle(cx, cy, r, "none", ink["hair"], 0.2))
    doc.add("panel", _polygon(
        [(tipx, tipy), (tailx + px * r * 0.36, taily + py * r * 0.36),
         (cx - nx * r * 0.42, cy - ny * r * 0.42),
         (tailx - px * r * 0.36, taily - py * r * 0.36)],
        ink["ink"], cls="north-arrow"))
    lb = text_box(cx + nx * (r + 2.6), cy + ny * (r + 2.6) + 0.9, "N", 3.2, "middle",
                  True)
    doc.add("panel", _text(cx + nx * (r + 2.6), cy + ny * (r + 2.6) + 0.9, "N", 3.2,
                           ink["ink"], bold=True, cls="north-label", owner="panel"))
    placer.add(lb)
    bearing = f"NORTH  {float(ctx.plan.site.north_deg):.0f}° (+Y BEARING)"
    tb = text_box(cx, cy + r + 6.5, bearing, 1.7, "middle")
    doc.add("panel", _text(cx, cy + r + 6.5, bearing, 1.7, ink["sub"], owner="panel"))
    placer.add(tb)
    return r * 2 + 12.0


_NICE = (250.0, 500.0, 1000.0, 2000.0, 5000.0, 10000.0, 20000.0)


def _scale_bar(doc: _Doc, ctx: _Ctx, placer: Placer, x: float, y: float,
               max_len: float = 50.0) -> float:
    ink = ctx.ink
    seg = _NICE[0]
    for c in _NICE:
        if 5.0 * c / ctx.scale <= max_len:
            seg = c
    sl = seg / ctx.scale
    h = 1.8
    for i in range(5):
        doc.add("panel", _rect(x + i * sl, y, sl, h,
                               ink["ink"] if i % 2 == 0 else ink["bg"],
                               ink["ink"], 0.15))
    for i in range(6):
        if i % 2 and i != 5:
            continue
        lab = f"{seg * i / 1000.0:g}" + (" m" if i == 5 else "")
        tb = text_box(x + i * sl, y + h + 2.6, lab, 1.7, "middle")
        if placer.free(tb):
            placer.add(tb)
            doc.add("panel", _text(x + i * sl, y + h + 2.6, lab, 1.7, ink["sub"],
                                   owner="panel"))
    st = f"SCALE 1:{int(round(ctx.scale))}"
    tb = text_box(x, y + h + 6.6, st, 2.1, "start", True)
    placer.add(tb)
    doc.add("panel", _text(x, y + h + 6.6, st, 2.1, ink["ink"], anchor="start",
                           bold=True, owner="panel"))
    return h + 9.0


# ── side panel ───────────────────────────────────────────────────────────────
def _fit(text: str, fs: float, max_w: float, bold: bool = False) -> str:
    """Truncate so a right-aligned value can never be overrun by its label."""
    if text_extents(text, fs, bold)[0] <= max_w:
        return text
    s = text
    while s and text_extents(s + "..", fs, bold)[0] > max_w:
        s = s[:-1]
    return (s + "..") if s else ""


def _wrap(text: str, fs: float, max_w: float) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if cur and text_extents(t, fs)[0] > max_w:
            lines.append(cur)
            cur = w
        else:
            cur = t
    if cur:
        lines.append(cur)
    return lines or [""]


def _row_h(row: tuple) -> float:
    k = row[0]
    return {"head": 5.4, "rule": 1.4, "gap": 2.2, "note": 3.0}.get(k, ROW)


def _panel_rows(ctx: _Ctx, rooms, keys, carpet, areas, findings) -> list[tuple]:
    rows: list[tuple] = [("head", "AREA STATEMENT"), ("rule",)]
    pa = areas.get("plot")
    if pa:
        rows.append(("kv", "Plot area", f"{_grp(sqft(pa))} SQ FT / {_m2(pa)} m²"))
    bu = areas.get("built_up", 0.0)
    rows.append(("kv", "Built-up (ground)",
                 f"{_grp(sqft(bu))} SQ FT / {_m2(bu)} m²"))
    if pa:
        rows.append(("kv", "Ground coverage", f"{100.0 * bu / pa:.1f} %"))
    cp = areas.get("carpet", 0.0)
    rows.append(("kv", "Carpet (rooms)", f"{_grp(sqft(cp))} SQ FT / {_m2(cp)} m²"))
    rows.append(("kv", "Wall thickness", f"{int(round(ctx.thick))} mm"))
    rows.append(("gap",))
    rows.append(("head", "ROOM SCHEDULE"), )
    rows.append(("rule",))
    for room, poly in sorted(rooms, key=lambda rp: rp[0].id):
        a = carpet.get(room.id, float(room.area))
        rows.append(("k3", keys[room.id], room.name.upper(),
                     f"{_grp(sqft(a))} SQ FT"))
    rows.append(("k3", "", "TOTAL", f"{_grp(sqft(cp))} SQ FT"))
    if findings:
        rows.append(("gap",))
        rows.append(("head", f"FINDINGS ({len(findings)})"))
        rows.append(("rule",))
        for i, f in enumerate(findings, 1):
            rows.append(("fhead", str(i), f["severity"], f["rule_id"]))
            if f["measure"]:
                rows.append(("note", "   " + f["measure"]))
            for ln in _wrap(f["detail"], 1.7, PANEL_W - 8.0)[:3]:
                rows.append(("note", "   " + ln))
            if f["element_ids"]:
                rows.append(("note", "   @ " + ", ".join(f["element_ids"][:6])))
    return rows


def _emit_panel(doc: _Doc, ctx: _Ctx, placer: Placer, x0: float, y0: float,
                rows: Sequence[tuple]) -> float:
    ink = ctx.ink
    w = PANEL_W - 8.0
    y = y0
    for row in rows:
        k = row[0]
        y += _row_h(row)
        if k == "head":
            doc.add("panel", _text(x0, y, row[1], 2.5, ink["ink"], anchor="start",
                                   bold=True, owner="panel"))
            placer.add(text_box(x0, y, row[1], 2.5, "start", True))
        elif k == "rule":
            doc.add("panel", _line(x0, y - 1.0, x0 + w, y - 1.0, ink["rule"], 0.25))
        elif k == "kv":
            val = row[2]
            vw = text_extents(val, 2.0)[0]
            lab = _fit(row[1], 2.0, w - vw - 2.0)
            doc.add("panel", _text(x0, y, lab, 2.0, ink["sub"], anchor="start",
                                   owner="panel"))
            doc.add("panel", _text(x0 + w, y, val, 2.0, ink["ink"], anchor="end",
                                   owner="panel"))
            placer.add(text_box(x0, y, lab, 2.0, "start"),
                       text_box(x0 + w, y, val, 2.0, "end"))
        elif k == "k3":
            key, lab, val = row[1], row[2], row[3]
            vw = text_extents(val, 2.0)[0]
            bold = lab == "TOTAL"
            lab = _fit(lab, 2.0, w - vw - 8.0, bold)
            if key:
                doc.add("panel", _text(x0 + 1.4, y, key, 1.9, ink["sub"],
                                       anchor="middle", owner="panel"))
                doc.add("panel", _circle(x0 + 1.4, y - 0.65, 1.9, "none",
                                         ink["hair"], 0.15))
                placer.add(text_box(x0 + 1.4, y, key, 1.9, "middle"))
            doc.add("panel", _text(x0 + 4.4, y, lab, 2.0, ink["ink"], anchor="start",
                                   bold=bold, owner="panel"))
            doc.add("panel", _text(x0 + w, y, val, 2.0, ink["ink"], anchor="end",
                                   bold=bold, owner="panel"))
            placer.add(text_box(x0 + 4.4, y, lab, 2.0, "start", bold),
                       text_box(x0 + w, y, val, 2.0, "end", bold))
        elif k == "fhead":
            idx, sev, rule = row[1], row[2], row[3]
            col = SEVERITY.get(sev, SEVERITY["info"])
            doc.add("panel", _circle(x0 + 1.6, y - 0.7, 1.9, col, cls="finding-key"))
            doc.add("panel", _text(x0 + 1.6, y, idx, 1.8, "#ffffff",
                                   anchor="middle", bold=True, owner="panel"))
            tag = f"{SEV_TAG.get(sev, sev.upper())}  {rule}"
            tag = _fit(tag, 2.0, w - 5.0, True)
            doc.add("panel", _text(x0 + 4.4, y, tag, 2.0, col, anchor="start",
                                   bold=True, owner="panel"))
            placer.add(text_box(x0 + 1.6, y, idx, 1.8, "middle", True),
                       text_box(x0 + 4.4, y, tag, 2.0, "start", True))
        elif k == "note":
            t = _fit(row[1], 1.7, w)
            doc.add("panel", _text(x0, y, t, 1.7, ink["sub"], anchor="start",
                                   owner="panel"))
            placer.add(text_box(x0, y, t, 1.7, "start"))
    return y - y0


# ── findings (annotated mode) ────────────────────────────────────────────────
_MEAS_KEYS = ("measured", "actual", "value", "got")
_REQ_KEYS = ("required", "requirement", "limit", "expected", "threshold", "min")


def normalise_findings(findings) -> list[dict]:
    """Accept dataclasses or dicts. The rules engine owns the Finding type; the
    renderer only needs four fields and must not import it."""
    out = []
    for f in findings or []:
        if isinstance(f, dict):
            get = lambda k, d=None: f.get(k, d)          # noqa: E731
        else:
            get = lambda k, d=None: getattr(f, k, d)     # noqa: E731
        sev = str(get("severity", "info") or "info").lower()
        unit = str(get("unit", "mm") or "")
        meas = next((get(k) for k in _MEAS_KEYS if get(k) is not None), None)
        req = next((get(k) for k in _REQ_KEYS if get(k) is not None), None)
        bits = []
        if meas is not None:
            bits.append(f"measured {meas}{(' ' + unit) if unit else ''}")
        if req is not None:
            bits.append(f"required {req}{(' ' + unit) if unit else ''}")
        out.append({
            "rule_id": str(get("rule_id", "?")),
            "severity": sev,
            "detail": str(get("detail", "") or ""),
            "element_ids": [str(e) for e in (get("element_ids") or [])],
            "measure": " vs ".join(bits),
        })
    return out


def _inside(box: Box, region: Box) -> bool:
    return (box[0] >= region[0] and box[1] >= region[1]
            and box[2] <= region[2] and box[3] <= region[3])


def _marker(doc: _Doc, ctx: _Ctx, placer: Placer, ax: float, ay: float, idx: str,
            col: str, extra_txt: str, region: Box) -> None:
    r = 2.3
    cands = [(0.0, 0.0)]
    for rad in (6.0, 10.0, 15.0):
        for a in range(0, 360, 45):
            th = math.radians(a)
            cands.append((math.cos(th) * rad, math.sin(th) * rad))
    # the callout is a bonus: the same measured-vs-required string is in the
    # findings legend, so drop it rather than push the marker somewhere useless
    variants = [extra_txt, ""] if extra_txt else [""]
    for txt, (ox, oy) in ((t, c) for t in variants for c in cands):
        mx, my = ax + ox, ay + oy
        boxes = [(mx - r, my - r, mx + r, my + r)]
        if txt:
            boxes.append(text_box(mx + r + 1.0, my + 0.7, txt, 1.8, "start"))
        if not all(_inside(b, region) for b in boxes) or not placer.free(*boxes):
            continue
        placer.add(*boxes)
        if ox or oy:
            doc.add("overlay", _line(ax, ay, mx, my, col, 0.2, dash="1.4,1",
                                     cls="leader"))
        doc.add("overlay", _circle(mx, my, r, col, "#ffffff", 0.3,
                                   cls="finding-marker"))
        doc.add("overlay", _text(mx, my + 0.7, idx, 2.0, "#ffffff", bold=True,
                                 cls="finding-marker-text", owner="finding"))
        if txt:
            doc.add("overlay", _halo(boxes[1]),
                    _text(mx + r + 1.0, my + 0.7, txt, 1.8, col, anchor="start",
                          cls="finding-callout", owner="finding"))
        return


def _overlay_findings(doc: _Doc, ctx: _Ctx, placer: Placer, findings: list[dict],
                      rooms: Sequence[tuple[Room, Polygon]], region: Box) -> dict:
    rmap = {r.id: p for r, p in rooms}
    omap = {o.id: o for o in ctx.plan.openings}
    wmap = {w.id: w for w in ctx.plan.walls}
    stats = {"highlighted": 0, "unresolved": 0}
    for i, f in enumerate(findings, 1):
        col = SEVERITY.get(f["severity"], SEVERITY["info"])
        anchors: list[tuple[float, float]] = []
        for eid in f["element_ids"]:
            if eid in wmap:
                w = wmap[eid]
                x1, y1 = ctx.pt(w.start.x, w.start.y)
                x2, y2 = ctx.pt(w.end.x, w.end.y)
                doc.add("overlay", _line(x1, y1, x2, y2, col,
                                         ctx.L(w.thickness) + 0.5, opacity=0.35,
                                         cls="hl-wall", cap="butt"))
                anchors.append(((x1 + x2) / 2.0, (y1 + y2) / 2.0))
            elif eid in rmap:
                sp = ctx.sheet_poly(rmap[eid])
                pts = list(sp.exterior.coords)
                doc.add("overlay", _polygon(pts, col, col, 0.4, opacity=0.12,
                                            cls="hl-room", dash="2,1.4"))
                anchors.append(_label_point(sp))
            elif eid in omap:
                res = opening_world(ctx.plan, omap[eid])
                if res is None:
                    stats["unresolved"] += 1
                    continue
                cx, cy = ctx.pt(res[0], res[1])
                doc.add("overlay", _circle(cx, cy, ctx.L(omap[eid].width) / 2 + 1.2,
                                           "none", col, 0.35, cls="hl-opening"))
                anchors.append((cx, cy))
            else:
                stats["unresolved"] += 1
        if not anchors:
            continue
        stats["highlighted"] += 1
        ax = sum(p[0] for p in anchors) / len(anchors)
        ay = sum(p[1] for p in anchors) / len(anchors)
        _marker(doc, ctx, placer, ax, ay, str(i), col, f["measure"], region)
    return stats


def _wall_ids(doc: _Doc, ctx: _Ctx, placer: Placer) -> dict:
    """Wall IDs for the critic. Skipped rather than overlapped when there is no
    room; a missing ID is recoverable, an unreadable pile of them is not."""
    stats = {"placed": 0, "skipped": 0}
    fs = 1.5
    for w in sorted(ctx.plan.walls, key=lambda w: w.id):
        if w.length < 1e-6:
            continue
        x1, y1 = ctx.pt(w.start.x, w.start.y)
        x2, y2 = ctx.pt(w.end.x, w.end.y)
        dx, dy = x2 - x1, y2 - y1
        ln = math.hypot(dx, dy) or 1.0
        ux, uy = dx / ln, dy / ln
        nx, ny = -uy, ux
        off = ctx.L(w.thickness) / 2.0 + 1.4
        done = False
        for t in (0.5, 0.35, 0.65, 0.22, 0.78):
            for s in (1.0, -1.0):
                bx = x1 + dx * t + nx * off * s
                by = y1 + dy * t + ny * off * s + fs * 0.35
                box = text_box(bx, by, w.id, fs, "middle")
                if not placer.free(box):
                    continue
                placer.add(box)
                doc.add("labels", _halo(box), _text(bx, by, w.id, fs, ctx.ink["ink"],
                                                    cls="wall-id", owner=w.id))
                done = True
                break
            if done:
                break
        stats["placed" if done else "skipped"] += 1
    return stats


# ── title block ──────────────────────────────────────────────────────────────
def _title_block(doc: _Doc, ctx: _Ctx, placer: Placer, x0: float, y0: float,
                 w: float, h: float, cells: Sequence[Sequence[str]]) -> None:
    ink = ctx.ink
    doc.add("panel", _rect(x0, y0, w, h, "none", ink["rule"], 0.3, cls="title-block"))
    n = len(cells)
    cw = w / n
    for i, lines in enumerate(cells):
        cx = x0 + cw * i
        if i:
            doc.add("panel", _line(cx, y0, cx, y0 + h, ink["rule"], 0.2))
        y = y0 + 4.2
        for j, ln in enumerate(lines):
            fs = 3.0 if (i == 0 and j == 0) else 1.9
            bold = i == 0 and j == 0
            t = _fit(ln, fs, cw - 4.0, bold)
            doc.add("panel", _text(cx + 2.0, y, t, fs,
                                   ink["ink"] if bold else ink["sub"],
                                   anchor="start", bold=bold, owner="title"))
            placer.add(text_box(cx + 2.0, y, t, fs, "start", bold))
            y += fs * 1.45 if j == 0 else 2.9


def _emit_title(doc: _Doc, ctx: _Ctx, placer: Placer, plan: Plan, mode: str,
                scale: float, fnds: list, sheet_w: float, sheet_h: float,
                opts: dict) -> None:
    caveat = ("SCALE INFERRED FROM RESPLAN wall_depth - DIMENSIONS INDICATIVE"
              if str(plan.provenance.get("source", "")).lower() == "resplan"
              else "DO NOT SCALE FROM PRINT")
    if mode == "presentation":
        cells = [
            [opts.get("title") or "GROUND FLOOR PLAN", f"PLAN {plan.id}",
             opts.get("subtitle") or "PROPOSED RESIDENCE"],
            ["DIMENSIONS", "FEET-INCHES, (mm) IN BRACKETS",
             f"AREAS IN SQ FT / m{chr(178)}"],
            [f"SCALE 1:{int(round(scale))}", caveat,
             f"STOREY HEIGHT {plan.storey_height} mm"],
        ]
    else:
        cells = [
            ["ANNOTATED CRITIQUE RENDER", f"PLAN {plan.id}",
             "PRESENTATION REVIEW ONLY"],
            ["THIS RENDER VERIFIES NO GEOMETRY", "METRIC RULES ARE CHECKED",
             "EXACTLY BY THE RULES ENGINE"],
            [f"SCALE 1:{int(round(scale))}", caveat, f"FINDINGS: {len(fnds)}"],
        ]
    _title_block(doc, ctx, placer, 2.0, sheet_h - TITLE_H - 2.0, sheet_w - 4.0,
                 TITLE_H, cells)


# ── assembly ─────────────────────────────────────────────────────────────────
def _hatch_defs(ink: dict) -> str:
    return (
        '<defs><pattern id="hatch" width="1.30" height="1.30" '
        'patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
        f'<rect width="1.30" height="1.30" fill="#e7e5e4"/>'
        f'<line x1="0" y1="0" x2="0" y2="1.30" stroke="{ink["hatch"]}" '
        'stroke-width="0.20"/></pattern></defs>'
    )


def _svg(mode: str, w: float, h: float, title: str, body: str,
         meta: Sequence[tuple[str, Any]] = ()) -> str:
    if mode == "presentation":
        size = f'width="{_n(w)}mm" height="{_n(h)}mm"'
    else:
        # a vision model needs pixels; keep the viewBox in sheet mm so all
        # geometry code is shared between modes.
        k = min(max(1400.0 / max(w, 1.0), 2.0), 6.0)
        size = f'width="{_n(w * k)}" height="{_n(h * k)}"'
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" version="1.1" {size} '
        f'viewBox="0 0 {_n(w)} {_n(h)}"{_attrs(meta)}>'
        f'<title>{_esc(title)}</title>{body}</svg>\n'
    )


def _empty(mode: str, plan: Plan) -> str:
    ink = PALETTE[mode]
    body = (_rect(0, 0, 120, 40, ink["bg"], ink["rule"], 0.3)
            + _text(60, 22, "NO GEOMETRY", 4.0, ink["ink"], bold=True)
            + _text(60, 30, f"plan {plan.id}", 2.2, ink["sub"]))
    return _svg(mode, 120.0, 40.0, f"plan {plan.id}", body,
                [("data-plan", _esc(plan.id)), ("data-mode", mode),
                 ("data-scale", "100.00"), ("data-ox", "0.00"), ("data-oy", "0.00"),
                 ("data-world-x", "0.00"), ("data-world-y", "0.00"),
                 ("data-world-maxy", "0.00"), ("data-flip", "0")])


def render_with_stats(plan: Plan, mode: str = "presentation", findings=None,
                      **opts) -> tuple[str, dict]:
    """``render`` plus layout telemetry (dropped labels, suppressed dims, ...).

    Split out because the numbers are what the test corpus asserts on; the SVG
    itself carries none of them, so output stays byte-stable.
    """
    mode = str(mode).lower()
    if mode not in PALETTE:
        raise ValueError(f"unknown mode {mode!r}")
    ink = PALETTE[mode]
    flip = bool(opts.get("flip_y", False))
    fnds = normalise_findings(findings) if mode == "annotated" else []

    rooms: list[tuple[Room, Polygon]] = []
    for r in sorted(plan.rooms, key=lambda r: r.id):
        g = _pl(r.polygon)
        if g is not None:
            rooms.append((r, g))
    poche = wall_poche(plan)
    thick = _median_thickness(plan)

    plot = _pl(plan.site.plot_polygon)
    if plot is not None:
        # ResPlan plot outlines are raster-traced (median 120 vertices of
        # single-pixel jitter). Simplifying is a drafting decision, not a
        # geometric one: the boundary must read as a line, not a comb.
        ps = _parts(plot.simplify(max(thick * 0.35, 40.0), preserve_topology=True))
        if ps:
            plot = max(ps, key=lambda p: p.area)
    setb = _setback_polygon(plot, plan.site.setbacks_mm or {})

    geo = [g for g in [*(p for _, p in rooms), *poche, plot] if g is not None]
    if not geo:
        return _empty(mode, plan), {"empty": True}
    minx = min(g.bounds[0] for g in geo)
    miny = min(g.bounds[1] for g in geo)
    maxx = max(g.bounds[2] for g in geo)
    maxy = max(g.bounds[3] for g in geo)

    scale = float(opts.get("scale") or 0.0)
    if scale <= 0:
        scale = SCALE_LADDER[-1]
        for cand in SCALE_LADDER:
            cw = (maxx - minx) / cand
            ch = (maxy - miny) / cand
            if max(PAD_L + cw + PAD_R + PANEL_W,
                   PAD_T + ch + PAD_B + TITLE_H) <= SHEET_MAX:
                scale = cand
                break

    # Theme resolution. `annotated` keeps its own high-contrast palette: it is
    # read by a vision model and by someone hunting faults, so colour there is
    # signal, not styling.
    _tname = str(opts.get("theme") or DEFAULT_THEME)
    _theme = THEMES.get(_tname, THEMES[DEFAULT_THEME])
    if mode == "presentation":
        ink = dict(_theme["palette"])
    ctx = _Ctx(plan=plan, mode=mode, scale=scale, minx=minx, miny=miny, maxy=maxy,
               flip=flip, ink=ink, tint=_theme["tint"], fonts=_theme["fonts"],
               poche_weight=float(_theme.get("poche_weight", 0.35)),
               thick=thick)
    cw = (maxx - minx) / scale
    ch = (maxy - miny) / scale

    carpet: dict[str, float] = {}
    for r, g in rooms:
        inner = _inner(g, thick / 2.0)
        carpet[r.id] = float(inner.area if inner is not None else g.area)
    keys = {r.id: str(i + 1) for i, (r, _) in enumerate(rooms)}
    footprint = unary_union([*poche, *(g for _, g in rooms)])
    areas = {
        "plot": float(plot.area) if plot is not None else None,
        "built_up": float(footprint.area) if not footprint.is_empty else 0.0,
        "carpet": float(sum(carpet.values())),
    }

    rows = _panel_rows(ctx, rooms, keys, carpet, areas, fnds)
    panel_h = sum(_row_h(r) for r in rows) + 46.0   # + north arrow and scale bar
    sheet_w = PAD_L + cw + PAD_R + PANEL_W
    sheet_h = max(PAD_T + ch + PAD_B, PAD_T + panel_h + 6.0) + TITLE_H

    doc = _Doc()
    placer = Placer()
    doc.add("defs", _hatch_defs(ink))
    doc.add("sheet", _rect(0, 0, sheet_w, sheet_h, ink["bg"]))
    doc.add("sheet", _rect(2.0, 2.0, sheet_w - 4.0, sheet_h - 4.0, "none",
                           ink["rule"], 0.4, cls="sheet-frame"))
    doc.add("sheet", _line(PAD_L + cw + PAD_R, 2.0, PAD_L + cw + PAD_R,
                           sheet_h - TITLE_H - 2.0, ink["rule"], 0.25))

    # Sheet furniture is fixed, so book it before anything that has to lay out
    # around it; the plan labels then never lose to a table row.
    panel_x = PAD_L + cw + PAD_R + 4.0
    used = _emit_panel(doc, ctx, placer, panel_x, PAD_T, rows)
    ny = PAD_T + used + 14.0
    _north_arrow(doc, ctx, placer, panel_x + 9.0, ny)
    _scale_bar(doc, ctx, placer, panel_x, ny + 18.0, max_len=PANEL_W - 12.0)
    _emit_title(doc, ctx, placer, plan, mode, scale, fnds, sheet_w, sheet_h, opts)
    region = (2.0, 2.0, PAD_L + cw + PAD_R, sheet_h - TITLE_H - 2.0)
    content = (PAD_L, PAD_T, PAD_L + cw, PAD_T + ch)

    _draw_rooms(doc, ctx, rooms)
    n_furn = _draw_furniture(doc, ctx, plan)
    _draw_poche(doc, ctx, poche, hatch=bool(opts.get("hatch_poche")))
    n_open = _draw_openings(doc, ctx, rooms)
    _draw_plot(doc, ctx, placer, plot, setb)

    lab = _place_room_labels(doc, ctx, placer, rooms, keys, carpet)
    fstats = (_overlay_findings(doc, ctx, placer, fnds, rooms, region)
              if fnds else {})
    wstats = _wall_ids(doc, ctx, placer) if mode == "annotated" else {}

    if mode == "presentation":
        dstats = _dimension_plan(doc, ctx, placer, poche, content)
    else:
        # a critic wants the envelope, not a full chain: fewer strings to read
        dstats = {"chain_text": 0, "chain_seg": 0}
        if poche:
            b = unary_union(poche).bounds
            sx0, sy0 = ctx.pt(b[0], b[1])
            sx1, sy1 = ctx.pt(b[2], b[3])
            _h_dim(doc, ctx, placer, sx0, sx1, content[3] + DIM_TOTAL_OFF,
                   f"{ft_in(b[2] - b[0])}  ({int(round(b[2] - b[0]))})",
                   ext_y=max(sy0, sy1), fs=2.2)
            _v_dim(doc, ctx, placer, min(sy0, sy1), max(sy0, sy1),
                   content[0] - DIM_TOTAL_OFF,
                   f"{ft_in(b[3] - b[1])}  ({int(round(b[3] - b[1]))})",
                   ext_x=sx0, fs=2.2)

    body = "".join("".join(doc.layers[k]) for k in _Doc.ORDER)
    # The world -> sheet mapping is part of the output contract: consumers need
    # it to hit-test a click, and tests need it to re-derive label positions.
    meta = [("data-plan", _esc(plan.id)), ("data-mode", mode),
            ("data-scale", _n(scale)), ("data-ox", _n(PAD_L)), ("data-oy", _n(PAD_T)),
            ("data-world-x", _n(minx)), ("data-world-y", _n(miny)),
            ("data-world-maxy", _n(maxy)), ("data-flip", "1" if flip else "0")]
    svg = _svg(mode, sheet_w, sheet_h, f"{mode} plan {plan.id}", body, meta)
    stats = {
        "empty": False, "mode": mode, "scale": scale,
        "sheet_mm": (round(sheet_w, 2), round(sheet_h, 2)),
        "n_rooms": len(rooms), "n_walls": len(plan.walls), "n_openings": n_open,
        "openings_skipped": len(plan.openings) - n_open,
        "labels_full": lab.full, "labels_reduced": lab.reduced,
        "labels_keyed": lab.keyed, "labels_dropped": lab.dropped,
        "dim_chain_seg": dstats["chain_seg"], "dim_chain_text": dstats["chain_text"],
        "wall_ids": wstats, "findings": fstats, "bytes": len(svg),
        "text_boxes": len(placer.boxes),
    }
    return svg, stats


def render(plan: Plan, mode: str = "presentation", findings=None, **opts) -> str:
    """Render `plan` to a standalone SVG string. Deterministic for equal input."""
    return render_with_stats(plan, mode, findings, **opts)[0]
