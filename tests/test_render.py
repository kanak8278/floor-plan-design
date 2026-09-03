"""Render tests against the real ResPlan corpus.

The renderer's failure modes are not "does it throw" — they are *layout* bugs:
labels drifting outside their room, two strings landing on top of each other,
an opening drawn on the wrong wall, geometry escaping the viewBox. So every
check here re-derives the answer from the IR (or from the SVG's own numbers)
instead of trusting the renderer's word for it:

  * text extents are re-estimated locally, not read from ``data-w``/``data-h``
    (those are then cross-checked, so a drift in the model shows up);
  * the world->sheet transform published on the root element is validated
    against the drawn room polygons before it is used for anything else;
  * openings are re-projected from ``(wall_id, position)`` and compared with
    where the glyph actually got drawn.

Run standalone for the corpus report:
    uv run --with shapely --with numpy python tests/test_render.py [N]
or under pytest (the corpus test then uses RENDER_N, default 320).
"""
from __future__ import annotations

import math
import os
import pickle
import statistics
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
from shapely.geometry import LineString, Point, Polygon, box as shp_box

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fpeval.ir import Opening, P, Plan, Room, Site, Wall           # noqa: E402
from fpeval.render import (ft_in, north_vector, render,            # noqa: E402
                           render_with_stats)
from fpeval.resplan import convert                                 # noqa: E402

PKL = os.environ.get("RESPLAN_PKL", "/tmp/resplan/data/ResPlan.pkl")
N_CORPUS = int(os.environ.get("RENDER_N", "320"))
OUT = ROOT / "out" / "render"
NS = "{http://www.w3.org/2000/svg}"
ASCENT, DESCENT = 0.76, 0.24          # must track fpeval.render
ANCHOR = {"start": 0.0, "middle": 0.5, "end": 1.0}


# ── SVG inspection ───────────────────────────────────────────────────────────
def walk(root, skip_defs: bool = True):
    """Yield every element outside <defs>; pattern children live in their own
    coordinate space and would poison viewBox checks."""
    stack = [root]
    while stack:
        el = stack.pop()
        if skip_defs and el.tag == f"{NS}defs":
            continue
        yield el
        stack.extend(list(el))


def text_boxes(root) -> list[dict]:
    out = []
    for el in walk(root):
        if el.tag != f"{NS}text":
            continue
        s = el.text or ""
        fs = float(el.get("font-size"))
        bold = el.get("font-weight") == "bold"
        x, y = float(el.get("x")), float(el.get("y"))
        w = sum(_cw(c) for c in s) * fs * (1.05 if bold else 1.0)
        h = fs * (ASCENT + DESCENT)
        x0 = x - w * ANCHOR[el.get("text-anchor", "start")]
        y0 = y - fs * ASCENT
        rot = float(el.get("data-rot") or 0.0)
        if abs(rot) > 1e-9:
            a = math.radians(rot)
            ca, sa = math.cos(a), math.sin(a)
            pts = [(x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h)]
            rp = [((px - x) * ca - (py - y) * sa + x, (px - x) * sa + (py - y) * ca + y)
                  for px, py in pts]
            box = (min(p[0] for p in rp), min(p[1] for p in rp),
                   max(p[0] for p in rp), max(p[1] for p in rp))
        else:
            box = (x0, y0, x0 + w, y0 + h)
        out.append({"box": box, "text": s, "cls": el.get("class") or "",
                    "owner": el.get("data-owner") or "", "fs": fs,
                    "declared": (float(el.get("data-w")), float(el.get("data-h")))})
    return out


_NARROW = set("'\".,:;!ilj|I()[]{}`")
_THIN = set("-tfrJ1/\\ ")
_WIDE = set("MW@mw%")


def _cw(ch: str) -> float:
    if ch in _NARROW:
        return 0.28
    if ch in _THIN:
        return 0.40
    if ch in _WIDE:
        return 0.90
    return 0.66 if (ch.isupper() or ch.isdigit()) else 0.55


def collisions(boxes, tol: float = 0.05):
    if len(boxes) < 2:
        return []
    a = np.asarray(boxes, dtype=float)
    x0, y0, x1, y1 = a[:, 0:1], a[:, 1:2], a[:, 2:3], a[:, 3:4]
    iw = np.minimum(x1, x1.T) - np.maximum(x0, x0.T)
    ih = np.minimum(y1, y1.T) - np.maximum(y0, y0.T)
    m = np.triu((iw > tol) & (ih > tol), 1)
    return [(int(i), int(j)) for i, j in np.argwhere(m)]


def viewbox(root) -> tuple[float, float, float, float]:
    return tuple(float(v) for v in root.get("viewBox").split())


_PATH_ARGS = {"M": 2, "L": 2, "A": 7, "Z": 0}


def path_points(d: str) -> list[tuple[float, float]]:
    """Coordinates of an absolute M/L/A/Z path — the only forms emitted."""
    toks, i, pts = d.replace(",", " ").split(), 0, []
    cmd = None
    while i < len(toks):
        t = toks[i]
        if t.upper() in _PATH_ARGS:
            cmd = t.upper()
            i += 1
            if cmd == "Z":
                continue
        n = _PATH_ARGS[cmd]
        args = [float(v) for v in toks[i:i + n]]
        i += n
        pts.append((args[-2], args[-1]))          # endpoint of M / L / A
    return pts


def drawn_points(root) -> list[tuple[float, float]]:
    """Every coordinate the renderer put on the sheet, text boxes included."""
    pts: list[tuple[float, float]] = []
    for el in walk(root):
        tag = el.tag[len(NS):]
        if tag == "line":
            pts += [(float(el.get("x1")), float(el.get("y1"))),
                    (float(el.get("x2")), float(el.get("y2")))]
        elif tag in ("polygon", "polyline"):
            for tk in el.get("points").split():
                x, y = tk.split(",")
                pts.append((float(x), float(y)))
        elif tag == "rect":
            x, y = float(el.get("x", 0)), float(el.get("y", 0))
            pts += [(x, y), (x + float(el.get("width")), y + float(el.get("height")))]
        elif tag == "circle":
            cx, cy, r = (float(el.get("cx")), float(el.get("cy")), float(el.get("r")))
            pts += [(cx - r, cy - r), (cx + r, cy + r)]
        elif tag == "path":
            pts += path_points(el.get("d"))
    for tb in text_boxes(root):
        b = tb["box"]
        pts += [(b[0], b[1]), (b[2], b[3])]
    return pts


def sheet_transform(root):
    """(fn, scale) from the root's published mapping. Validated by
    ``check_transform`` before any other check leans on it."""
    sc = float(root.get("data-scale"))
    ox, oy = float(root.get("data-ox")), float(root.get("data-oy"))
    wx, wy = float(root.get("data-world-x")), float(root.get("data-world-y"))
    flip = root.get("data-flip") == "1"
    maxy = float(root.get("data-world-maxy"))

    def fn(x, y):
        return (ox + (x - wx) / sc,
                oy + ((maxy - y) if flip else (y - wy)) / sc)
    return fn, sc


# ── checks ───────────────────────────────────────────────────────────────────
def check_transform(root, plan: Plan, tf) -> list[str]:
    """Room fills are emitted straight from the IR, so they pin the transform:
    push the IR polygons through the published mapping and the two sets must
    coincide. A wrong scale or origin cannot survive this.

    Compared as geometry, not vertex lists, because the renderer legitimately
    repairs invalid rings (buffer(0) reorders and dedupes vertices).
    """
    from shapely.ops import unary_union
    got = []
    for el in walk(root):
        if el.tag == f"{NS}polygon" and (el.get("class") or "").startswith("room"):
            pts = [tuple(float(v) for v in tk.split(",")) for tk in el.get("points").split()]
            if len(pts) >= 3:
                g = Polygon(pts)
                got.append(g if g.is_valid else g.buffer(0))
    want = []
    for r in plan.rooms:
        if len(r.polygon) >= 3:
            g = Polygon([tf(p.x, p.y) for p in r.polygon])
            want.append(g if g.is_valid else g.buffer(0))
    if not want:
        return [] if not got else ["room polygons drawn for a plan with none"]
    if not got:
        return ["no room polygons drawn"]
    a, b = unary_union(got), unary_union(want)
    inter, union = a.intersection(b).area, a.union(b).area
    iou = inter / union if union > 0 else 0.0
    errs = []
    if iou < 0.9995:
        errs.append(f"drawn rooms vs transformed IR rooms IoU {iou:.5f}")
    hd = a.hausdorff_distance(b)
    if hd > 0.05:
        errs.append(f"drawn rooms Hausdorff {hd:.3f} mm from transformed IR")
    return errs


def check_room_labels(root, plan: Plan, tf) -> tuple[int, list[str]]:
    """Every room label box must sit inside its own room polygon."""
    polys = {}
    for r in plan.rooms:
        if len(r.polygon) >= 3:
            g = Polygon([tf(p.x, p.y) for p in r.polygon])
            polys[r.id] = g if g.is_valid else g.buffer(0)
    errs, labelled = [], set()
    for tb in text_boxes(root):
        if "room-label" not in tb["cls"]:
            continue
        g = polys.get(tb["owner"])
        if g is None:
            errs.append(f"label {tb['text']!r} owned by unknown room {tb['owner']}")
            continue
        labelled.add(tb["owner"])
        if not g.buffer(1e-6).contains(shp_box(*tb["box"])):
            errs.append(f"label {tb['text']!r} escapes room {tb['owner']}")
    return len(labelled), errs


def check_openings(root, plan: Plan, tf) -> list[str]:
    """Re-project (wall_id, position) and compare with where the glyph landed."""
    errs = []
    seen = set()
    for el in walk(root):
        if el.tag != f"{NS}g" or "opening" not in (el.get("class") or ""):
            continue
        oid = el.get("data-opening")
        seen.add(oid)
        op = next((o for o in plan.openings if o.id == oid), None)
        if op is None:
            errs.append(f"unknown opening {oid}")
            continue
        w = plan.wall(el.get("data-wall"))
        if w is None or w.id != op.wall_id:
            errs.append(f"{oid}: host wall {el.get('data-wall')} != {op.wall_id}")
            continue
        seg = LineString([(w.start.x, w.start.y), (w.end.x, w.end.y)])
        wx, wy = float(el.get("data-wx")), float(el.get("data-wy"))
        exp = seg.interpolate(min(max(op.position, 0.0), 1.0), normalized=True)
        if seg.distance(Point(wx, wy)) > 0.02:
            errs.append(f"{oid}: not on host wall centreline")
        if math.hypot(wx - exp.x, wy - exp.y) > 0.02:
            errs.append(f"{oid}: position {op.position} not honoured")
        rev = next((c for c in el if c.tag == f"{NS}polygon"
                    and (c.get("class") or "") == "reveal"), None)
        if rev is None:
            errs.append(f"{oid}: no reveal drawn")
            continue
        pts = [tuple(float(v) for v in tk.split(",")) for tk in rev.get("points").split()]
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        sx, sy = tf(wx, wy)
        if math.hypot(cx - sx, cy - sy) > 0.05:
            errs.append(f"{oid}: glyph drawn {math.hypot(cx-sx, cy-sy):.3f} mm off host")
    return errs


def check_viewbox(root) -> list[str]:
    vx, vy, vw, vh = viewbox(root)
    bad = []
    for x, y in drawn_points(root):
        if not (vx - 0.01 <= x <= vx + vw + 0.01 and vy - 0.01 <= y <= vy + vh + 0.01):
            bad.append((round(x, 2), round(y, 2)))
    return [f"{len(bad)} points outside viewBox, e.g. {bad[:3]}"] if bad else []


def check_declared_extents(root) -> list[str]:
    errs = []
    for tb in text_boxes(root):
        w = tb["box"][2] - tb["box"][0]
        h = tb["box"][3] - tb["box"][1]
        dw, dh = tb["declared"]
        if abs(dw - max(w, h)) > 0.02 and abs(dw - min(w, h)) > 0.02:
            errs.append(f"declared width {dw} vs recomputed {w:.2f}/{h:.2f}"
                        f" for {tb['text']!r}")
    return errs


# ── synthetic findings (NOT a rules engine — fixtures only) ──────────────────
def synth_findings(plan: Plan) -> list[dict]:
    """Deterministic fixtures so the annotated overlay is exercised on every
    plan. These are made-up; nothing here evaluates the plan."""
    out = []
    doors = sorted([o for o in plan.openings if o.kind != "window"],
                   key=lambda o: (o.width, o.id))
    if doors:
        d = doors[0]
        out.append({"rule_id": "fixture.door.width", "severity": "high",
                    "element_ids": [d.id, d.wall_id],
                    "measured": d.width, "required": 900, "unit": "mm",
                    "detail": "Fixture finding: narrowest door on the plan."})
    baths = sorted([r for r in plan.rooms if r.category == "bathroom"],
                   key=lambda r: (r.area, r.id))
    if baths:
        out.append({"rule_id": "fixture.room.area", "severity": "medium",
                    "element_ids": [baths[0].id],
                    "measured": round(baths[0].area_m2, 2), "required": 3.0,
                    "unit": "m2", "detail": "Fixture finding: smallest wet area."})
    if plan.rooms:
        out.append({"rule_id": "fixture.label.crowding", "severity": "low",
                    "element_ids": [sorted(plan.rooms, key=lambda r: r.id)[0].id],
                    "detail": "Fixture finding: check label crowding by eye."})
    return out


# ── corpus driver ────────────────────────────────────────────────────────────
def load_plans(n: int) -> list[Plan]:
    raw = pickle.load(open(PKL, "rb"))
    plans, i = [], 0
    while len(plans) < n and i < len(raw):
        try:
            plans.append(convert(raw[i]))
        except Exception:
            pass
        i += 1
    return plans


def run_corpus(n: int = N_CORPUS, verbose: bool = False) -> dict:
    plans = load_plans(n)
    agg = {
        "plans": 0, "renders": 0, "render_errors": [], "xml_errors": [],
        "label_errors": [], "collisions": [], "opening_errors": [],
        "viewbox_errors": [], "transform_errors": [], "extent_errors": [],
        "unlabelled_rooms": 0, "rooms": 0, "times": {"presentation": [], "annotated": []},
        "sizes": {"presentation": [], "annotated": []}, "stats": [],
        "collision_plans": 0,
    }
    for plan in plans:
        agg["plans"] += 1
        agg["rooms"] += len(plan.rooms)
        for mode in ("presentation", "annotated"):
            fnds = synth_findings(plan) if mode == "annotated" else None
            t0 = time.perf_counter()
            try:
                svg, st = render_with_stats(plan, mode, findings=fnds)
            except Exception as e:                          # pragma: no cover
                agg["render_errors"].append(f"{plan.id}/{mode}: {type(e).__name__}: {e}")
                continue
            agg["times"][mode].append((time.perf_counter() - t0) * 1000.0)
            agg["sizes"][mode].append(len(svg))
            agg["renders"] += 1
            agg["stats"].append(st)
            try:
                root = ET.fromstring(svg)
            except ET.ParseError as e:
                agg["xml_errors"].append(f"{plan.id}/{mode}: {e}")
                continue
            tf, _ = sheet_transform(root)
            agg["transform_errors"] += [f"{plan.id}/{mode}: {m}"
                                        for m in check_transform(root, plan, tf)]
            n_lab, errs = check_room_labels(root, plan, tf)
            agg["label_errors"] += [f"{plan.id}/{mode}: {m}" for m in errs]
            if mode == "presentation":
                agg["unlabelled_rooms"] += len(plan.rooms) - n_lab
            tbs = text_boxes(root)
            cols = collisions([t["box"] for t in tbs])
            if cols:
                agg["collision_plans"] += 1
                agg["collisions"] += [
                    f"{plan.id}/{mode}: {tbs[i]['text']!r} x {tbs[j]['text']!r}"
                    for i, j in cols[:3]]
            agg["opening_errors"] += [f"{plan.id}/{mode}: {m}"
                                      for m in check_openings(root, plan, tf)]
            agg["viewbox_errors"] += [f"{plan.id}/{mode}: {m}"
                                      for m in check_viewbox(root)]
            agg["extent_errors"] += [f"{plan.id}/{mode}: {m}"
                                     for m in check_declared_extents(root)]
        if verbose and agg["plans"] % 50 == 0:
            print(f"  ... {agg['plans']} plans", flush=True)
    return agg


# ── pytest surface ───────────────────────────────────────────────────────────
def _pct(a, q):
    return statistics.quantiles(a, n=100)[q - 1] if len(a) > 2 else (a[0] if a else 0)


def test_corpus_renders_clean():
    agg = run_corpus(N_CORPUS)
    assert agg["plans"] >= 300, f"only {agg['plans']} plans loaded"
    assert agg["renders"] == 2 * agg["plans"]
    assert agg["render_errors"] == []
    assert agg["xml_errors"] == []
    assert agg["transform_errors"] == []
    assert agg["extent_errors"] == []
    assert agg["label_errors"] == []
    assert agg["collisions"] == []
    assert agg["opening_errors"] == []
    assert agg["viewbox_errors"] == []


def test_determinism_byte_identical():
    for plan in load_plans(25):
        f = synth_findings(plan)
        for mode in ("presentation", "annotated"):
            a = render(plan, mode, findings=f if mode == "annotated" else None)
            b = render(plan, mode, findings=f if mode == "annotated" else None)
            assert a == b, f"{plan.id}/{mode} not deterministic"
            assert a.encode() == b.encode()


def test_feet_inches_formatting():
    assert ft_in(0) == "0'-0\""
    assert ft_in(304.8) == "1'-0\""
    assert ft_in(3810) == "12'-6\""
    assert ft_in(3810 - 12) == "12'-6\""        # rounds to the nearest inch
    assert ft_in(12 * 304.8 - 1) == "12'-0\""   # 11'-12" must roll over


def test_north_vector_frames():
    # north_deg is the bearing of world +Y; at 0 the plan is drawn with north
    # down the page, which is literal and intended.
    nx, ny = north_vector(0.0, False)
    assert (round(nx, 6), round(ny, 6)) == (0.0, 1.0)
    nx, ny = north_vector(180.0, False)
    assert (round(nx, 6), round(ny, 6)) == (0.0, -1.0)
    nx, ny = north_vector(90.0, False)
    assert (round(nx, 6), round(ny, 6)) == (1.0, 0.0)
    assert north_vector(0.0, True)[1] == -1.0


def _synthetic_plot_plan() -> Plan:
    """12 x 9 m rectangular house on a 15 x 12 m plot with real setbacks —
    ResPlan carries neither, so the setback path needs its own fixture."""
    t = 230
    w, h = 12000, 9000
    walls = [
        Wall("w0", P(0, 0), P(w, 0), t), Wall("w1", P(w, 0), P(w, h), t),
        Wall("w2", P(w, h), P(0, h), t), Wall("w3", P(0, h), P(0, 0), t),
        Wall("w4", P(5000, 0), P(5000, h), t),
        Wall("w5", P(5000, 4500), P(w, 4500), t),
    ]
    rooms = [
        Room("r0", "Living", "living", ["w0", "w3", "w2", "w4"],
             [P(0, 0), P(5000, 0), P(5000, h), P(0, h)], 5000 * h),
        Room("r1", "Bedroom 1", "bedroom", ["w0", "w4", "w1", "w5"],
             [P(5000, 0), P(w, 0), P(w, 4500), P(5000, 4500)], 7000 * 4500),
        Room("r2", "Kitchen", "kitchen", ["w5", "w1", "w2", "w4"],
             [P(5000, 4500), P(w, 4500), P(w, h), P(5000, h)], 7000 * 4500),
    ]
    openings = [
        Opening("o0", "front_door", "w0", 0.20, 1050),
        Opening("o1", "door", "w4", 0.30, 900),
        Opening("o2", "door", "w5", 0.60, 800),
        Opening("o3", "window", "w2", 0.35, 1500, sill=900),
        Opening("o4", "window", "w1", 0.70, 1200, sill=900),
    ]
    plot = [P(-3000, -6000), P(15000, -6000), P(15000, 12000), P(-3000, 12000)]
    return Plan(id="synthetic-setbacks", walls=walls, openings=openings, rooms=rooms,
                site=Site(plot_polygon=plot, north_deg=112.5,
                          setbacks_mm={"front": 4500, "rear": 2000,
                                       "left": 2500, "right": 2000}),
                provenance={"source": "synthetic"})


def test_setbacks_and_north_render():
    plan = _synthetic_plot_plan()
    svg, st = render_with_stats(plan, "presentation")
    root = ET.fromstring(svg)
    assert st["labels_dropped"] == 0
    assert any("setback-line" in (e.get("class") or "") for e in walk(root))
    assert any("plot-boundary" in (e.get("class") or "") for e in walk(root))
    txt = [t["text"] for t in text_boxes(root)]
    assert any(t.startswith("FRONT") for t in txt), txt
    assert any(t.startswith("LEFT") for t in txt), txt
    assert any(t.startswith("REAR") for t in txt), txt
    assert any(t.startswith("RIGHT") for t in txt), txt
    assert "N" in txt
    tf, _ = sheet_transform(root)
    assert check_transform(root, plan, tf) == []
    assert check_openings(root, plan, tf) == []
    assert check_viewbox(root) == []
    assert collisions([t["box"] for t in text_boxes(root)]) == []


def test_empty_plan_does_not_crash():
    svg = render(Plan(id="empty"), "presentation")
    ET.fromstring(svg)
    assert "NO GEOMETRY" in svg


def test_unknown_mode_rejected():
    try:
        render(Plan(id="x"), "wireframe")
    except ValueError:
        return
    raise AssertionError("unknown mode must raise")


def test_findings_accept_dataclass_and_dict():
    from dataclasses import dataclass

    @dataclass
    class F:
        rule_id: str
        severity: str
        detail: str
        element_ids: list
        measured: int = 0
        required: int = 0

    plan = _synthetic_plot_plan()
    f = F("nbc.x", "high", "dataclass path", ["r0"], 800, 900)
    a = render(plan, "annotated", findings=[f])
    b = render(plan, "annotated", findings=[{"rule_id": "nbc.x", "severity": "high",
                                             "detail": "dataclass path",
                                             "element_ids": ["r0"], "measured": 800,
                                             "required": 900}])
    assert a == b, "dict and dataclass findings must render identically"
    assert "measured 800 mm vs required 900 mm" in a


def _big_plan(w: int, h: int) -> Plan:
    walls = [Wall("w0", P(0, 0), P(w, 0), 300), Wall("w1", P(w, 0), P(w, h), 300),
             Wall("w2", P(w, h), P(0, h), 300), Wall("w3", P(0, h), P(0, 0), 300)]
    rooms = [Room("r0", "Hall", "living", [w.id for w in walls],
                  [P(0, 0), P(w, 0), P(w, h), P(0, h)], w * h)]
    return Plan(id=f"big-{w}x{h}", walls=walls, rooms=rooms,
                openings=[Opening("o0", "front_door", "w0", 0.5, 1200)],
                site=Site(north_deg=45.0))


def test_scale_ladder_steps_down_for_large_plans():
    small = render_with_stats(_big_plan(12000, 9000), "presentation")[1]
    assert small["scale"] == 100.0
    big = render_with_stats(_big_plan(90000, 60000), "presentation")[1]
    assert big["scale"] > 100.0, big
    assert max(big["sheet_mm"]) <= 900.0, big["sheet_mm"]
    root = ET.fromstring(render(_big_plan(90000, 60000), "presentation"))
    assert f"SCALE 1:{int(big['scale'])}" in [t["text"] for t in text_boxes(root)]
    assert check_viewbox(root) == []


def test_flip_y_stays_consistent():
    plan = _synthetic_plot_plan()
    svg = render(plan, "presentation", flip_y=True)
    root = ET.fromstring(svg)
    assert root.get("data-flip") == "1"
    tf, _ = sheet_transform(root)
    assert check_transform(root, plan, tf) == []
    assert check_room_labels(root, plan, tf)[1] == []
    assert check_openings(root, plan, tf) == []
    assert check_viewbox(root) == []
    assert svg != render(plan, "presentation")     # it really is mirrored


# ── sample export + report ───────────────────────────────────────────────────
def write_samples() -> list[str]:
    OUT.mkdir(parents=True, exist_ok=True)
    plans = load_plans(60)
    picks = [
        ("presentation", plans[0], {}, None, "01_presentation"),
        ("presentation", max(plans[:40], key=lambda p: len(p.rooms)), {}, None,
         "02_presentation_most_rooms"),
        ("presentation", min(plans[:40], key=lambda p: len(p.rooms)), {}, None,
         "03_presentation_fewest_rooms"),
        ("presentation", plans[7], {"hatch_poche": True}, None,
         "04_presentation_hatched_poche"),
        ("annotated", plans[0], {}, synth_findings(plans[0]), "05_annotated"),
        ("annotated", plans[3], {}, synth_findings(plans[3]), "06_annotated"),
        ("presentation", _synthetic_plot_plan(), {}, None,
         "07_setbacks_north_112deg"),
        ("annotated", _synthetic_plot_plan(), {},
         synth_findings(_synthetic_plot_plan()), "08_annotated_synthetic"),
    ]
    paths = []
    for mode, plan, opts, fnds, name in picks:
        svg = render(plan, mode, findings=fnds, **opts)
        fp = OUT / f"{name}.svg"
        fp.write_text(svg)
        paths.append(str(fp))
    return paths


def main(argv) -> int:
    n = int(argv[1]) if len(argv) > 1 else N_CORPUS
    print(f"corpus: {PKL}, rendering {n} plans x 2 modes\n", flush=True)
    t0 = time.time()
    agg = run_corpus(n, verbose=True)
    el = time.time() - t0
    print(f"=== {agg['plans']} plans, {agg['renders']} renders, {el:.1f}s wall ===")
    for k in ("render_errors", "xml_errors", "transform_errors", "extent_errors",
              "label_errors", "collisions", "opening_errors", "viewbox_errors"):
        bad = agg[k]
        print(f"  {k:18s} {len(bad)}" + (f"   e.g. {bad[:2]}" if bad else "  OK"))
    print(f"  collision-free plans: {agg['plans'] * 2 - agg['collision_plans']}"
          f"/{agg['plans'] * 2}")
    print(f"  rooms: {agg['rooms']}, unlabelled (presentation): "
          f"{agg['unlabelled_rooms']} "
          f"({100.0 * agg['unlabelled_rooms'] / max(agg['rooms'], 1):.2f}%)")

    st = agg["stats"]
    def s(key, mode=None):
        return [x[key] for x in st if not x.get("empty")
                and (mode is None or x["mode"] == mode)]
    lab = {k: sum(s(f"labels_{k}")) for k in ("full", "reduced", "keyed", "dropped")}
    print(f"  label variants: {lab}")
    chain = sum(s("dim_chain_text")), sum(s("dim_chain_seg"))
    print(f"  dimension chain text placed: {chain[0]}/{chain[1]} "
          f"({100.0 * chain[0] / max(chain[1], 1):.1f}%)")
    wid = [x["wall_ids"] for x in st if x.get("wall_ids")]
    if wid:
        pl = sum(w["placed"] for w in wid); sk = sum(w["skipped"] for w in wid)
        print(f"  wall IDs placed: {pl}/{pl + sk} ({100.0 * pl / max(pl + sk, 1):.1f}%)")
    print(f"  openings skipped: {sum(s('openings_skipped'))}")
    print(f"  scales used: {sorted(set(s('scale')))}")
    for mode in ("presentation", "annotated"):
        t = sorted(agg["times"][mode]); z = sorted(agg["sizes"][mode])
        if not t:
            continue
        print(f"  {mode:12s} ms p50={_pct(t,50):.1f} p90={_pct(t,90):.1f} "
              f"p99={_pct(t,99):.1f} max={t[-1]:.1f} | KB p50={_pct(z,50)/1024:.1f} "
              f"p90={_pct(z,90)/1024:.1f} max={z[-1]/1024:.1f}")

    print("\n=== determinism ===")
    same = 0
    plans = load_plans(25)
    for p in plans:
        f = synth_findings(p)
        same += (render(p, "presentation") == render(p, "presentation")
                 and render(p, "annotated", findings=f)
                 == render(p, "annotated", findings=f))
    print(f"  byte-identical re-render: {same}/{len(plans)} plans (both modes)")

    for fn in (test_feet_inches_formatting, test_north_vector_frames,
               test_setbacks_and_north_render, test_empty_plan_does_not_crash,
               test_unknown_mode_rejected, test_findings_accept_dataclass_and_dict,
               test_scale_ladder_steps_down_for_large_plans,
               test_flip_y_stays_consistent):
        fn()
        print(f"  unit OK: {fn.__name__}")

    print("\n=== samples ===")
    for p in write_samples():
        print(f"  {p}")
    fatal = sum(len(agg[k]) for k in
                ("render_errors", "xml_errors", "transform_errors", "extent_errors",
                 "label_errors", "collisions", "opening_errors", "viewbox_errors"))
    return 0 if fatal == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
