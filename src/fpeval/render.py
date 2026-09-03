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
    return _parts(unary_union(bands))


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


def _rect_dims(poly: Polygon, tol: float) -> tuple[float, float] | None:
    """(w, h) if the polygon is an axis-aligned rectangle within tol, else None."""
    ring = list(poly.exterior.coords)[:-1]
    if len(ring) != 4:
        return None
    x0, y0, x1, y1 = poly.bounds
    if abs(poly.area - (x1 - x0) * (y1 - y0)) > tol * max(x1 - x0, y1 - y0):
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

    ORDER = ("defs", "sheet", "plot", "rooms", "poche", "openings", "dims",
             "labels", "panel", "overlay")

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
