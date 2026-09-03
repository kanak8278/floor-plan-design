"""Round-trip and fidelity metrics for the ResPlan -> IR -> Project pipeline.

Metric-design note (matters for reading the numbers): **face count vs room
count is not a soundness metric on ResPlan.** ResPlan labels only
living/kitchen/bedroom/bathroom/balcony/storage. It has no circulation
category, so hallways, entry vestibules and the voids between building blocks
carry no label at all. Any face-enumeration pass -- ours, or OpenPlan3D's
`detectRooms` -- correctly finds those as faces, and the count then exceeds the
number of labelled rooms. Confirmed two ways: (a) in the real OpenPlan3D editor
every mismatch on 40 plans was the editor finding *more* rooms, never fewer,
with generically named phantom rooms over the unlabelled voids; (b)
`unlabelled_space()` below measures the void directly.

So the primary soundness metric is `room_face_match()`: every labelled IR room
must map 1:1 onto some recovered face with IoU > 0.99. Extra faces are counted
and attributed to unlabelled space, not scored as errors.
"""
from __future__ import annotations
import os
from pathlib import Path
from shapely.geometry import Polygon, LineString, Point
from shapely.ops import unary_union, polygonize
from shapely.strtree import STRtree
from shapely import affinity, make_valid

from .ir import Plan
from .resplan import geoms, ROOM_KEYS, scale_mm_per_unit

MIN_FACE_MM2 = 1e4          # 0.01 m^2 -- below this a face is a noding sliver


def _valid(g):
    """Repair a geometry enough for set operations.

    Some ResPlan room polygons are self-intersecting (measured: 1 plan in
    17,000 raises `side location conflict` straight out of `unary_union`), so
    every metric that unions *source* geometry has to go through this.
    """
    if g is None or getattr(g, "is_empty", True):
        return g
    if getattr(g, "is_valid", True):
        return g
    try:
        r = make_valid(g)
    except Exception:
        try:
            r = g.buffer(0)
        except Exception:
            return g
    return r


def _iou(a, b) -> float:
    """IoU that survives a topologically broken input (1 plan in 17,000)."""
    for ga, gb in ((a, b), (_valid(a.buffer(0)), _valid(b.buffer(0)))):
        try:
            inter = ga.intersection(gb).area
            u = ga.area + gb.area - inter
            return inter / u if u > 0 else 0.0
        except Exception:
            continue
    return float("nan")


def _union(gs):
    gs = [_valid(g) for g in gs if g is not None and not getattr(g, "is_empty", True)]
    if not gs:
        return None
    try:
        return unary_union(gs)
    except Exception:
        try:
            return unary_union([g.buffer(0) for g in gs])
        except Exception:
            return None


# --------------------------------------------------------------------------
# corpus location
# --------------------------------------------------------------------------

def default_pkl_path() -> str:
    """Locate ResPlan.pkl: env override, then the repo copy, then /tmp.

    The repo-vendored `data/ResPlan.pkl` is authoritative; `/tmp/resplan/...`
    is only a fallback for shells that still have the old scratch copy.
    """
    env = os.environ.get("RESPLAN_PKL")
    if env:
        return env
    here = Path(__file__).resolve()
    for root in [here.parents[2], Path.cwd()]:
        p = root / "data" / "ResPlan.pkl"
        if p.exists():
            return str(p)
    return "/tmp/resplan/data/ResPlan.pkl"


def default_utils_path() -> str:
    """Locate ResPlan's own resplan_utils.py (for `plan_to_graph`)."""
    env = os.environ.get("RESPLAN_UTILS")
    if env:
        return env
    here = Path(__file__).resolve()
    for root in [here.parents[2], Path.cwd()]:
        p = root / "data" / "resplan_utils.py"
        if p.exists():
            return str(p)
    return "/tmp/resplan/resplan_utils.py"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _room_polys(plan: Plan) -> list[tuple[str, Polygon]]:
    out = []
    for r in plan.rooms:
        if len(r.polygon) < 3:
            continue
        p = Polygon([q.as_tuple() for q in r.polygon])
        if not p.is_valid:
            p = p.buffer(0)
            if p.geom_type != "Polygon" or p.is_empty:
                continue
        if p.area > 0:
            out.append((r.id, p))
    return out


def wall_faces(plan: Plan) -> list[Polygon]:
    """Faces of the wall centreline graph -- what detectRooms enumerates."""
    lines = [LineString([w.start.as_tuple(), w.end.as_tuple()]) for w in plan.walls]
    if not lines:
        return []
    noded = _union(lines)
    if noded is None:
        return []
    return [f for f in polygonize(noded) if f.area > MIN_FACE_MM2]


# --------------------------------------------------------------------------
# round trip
# --------------------------------------------------------------------------

def ir_identity(a: Plan, b: Plan) -> dict:
    """Exact-match check for IR -> Project -> IR."""
    wa = {w.id: (w.start.as_tuple(), w.end.as_tuple(), w.thickness) for w in a.walls}
    wb = {w.id: (w.start.as_tuple(), w.end.as_tuple(), w.thickness) for w in b.walls}
    oa = {o.id: (o.kind, o.wall_id, round(o.position, 6), o.width, o.sill, o.head)
          for o in a.openings}
    ob = {o.id: (o.kind, o.wall_id, round(o.position, 6), o.width, o.sill, o.head)
          for o in b.openings}
    ra = {r.id: (r.name, r.category, tuple(r.wall_ids)) for r in a.rooms}
    rb = {r.id: (r.name, r.category, tuple(r.wall_ids)) for r in b.rooms}
    return {
        "walls_equal": wa == wb,
        "openings_equal": oa == ob,
        "rooms_equal": ra == rb,
        "plot_equal": [p.as_tuple() for p in a.site.plot_polygon]
                      == [p.as_tuple() for p in b.site.plot_polygon],
        "n_walls": len(a.walls), "n_openings": len(a.openings), "n_rooms": len(a.rooms),
    }


# --------------------------------------------------------------------------
# face recovery
# --------------------------------------------------------------------------

def face_recovery(plan: Plan) -> dict:
    """Can rooms be re-derived as faces of the wall centreline graph?

    `face_count_exact` is retained for continuity with earlier runs but is NOT a
    soundness signal on ResPlan -- see the module docstring and
    `room_face_match`.
    """
    faces = wall_faces(plan)
    if not plan.walls:
        return {"ok": False, "reason": "no walls"}
    rp = _room_polys(plan)
    if not rp:
        return {"ok": False, "reason": "no room polygons"}
    tgt = _union([p for _, p in rp])
    got = _union(faces) if faces else None
    if tgt is None or tgt.is_empty:
        return {"ok": False, "reason": "room union failed"}
    iou = 0.0
    if got is not None and not got.is_empty:
        iou = _iou(tgt, got)
    return {"ok": True, "n_faces": len(faces), "n_rooms": len(rp),
            "face_count_exact": len(faces) == len(rp),
            "face_count_within_1": abs(len(faces) - len(rp)) <= 1,
            "extra_faces": len(faces) - len(rp),
            "area_iou": iou}


def room_face_match(plan: Plan, iou_thresh: float = 0.99) -> dict:
    """1:1 matching of every labelled IR room onto a recovered face.

    Greedy by descending IoU, so no face may serve two rooms. This is the
    metric that actually says whether the wall graph reproduces the plan;
    surplus faces are reported separately as candidate unlabelled space.
    """
    rp = _room_polys(plan)
    faces = wall_faces(plan)
    if not rp:
        return {"ok": False, "reason": "no room polygons"}
    if not faces:
        return {"ok": True, "n_rooms": len(rp), "n_faces": 0, "matched": 0,
                "all_matched": False, "extra_faces": -len(rp),
                "min_iou": 0.0, "ious": []}

    tree = STRtree(faces)
    cands: list[tuple[float, int, int]] = []
    for ri, (_, rpoly) in enumerate(rp):
        for fi in tree.query(rpoly):
            fi = int(fi)
            f = faces[fi]
            inter = rpoly.intersection(f).area
            if inter <= 0:
                continue
            union = rpoly.area + f.area - inter
            if union > 0:
                cands.append((inter / union, ri, fi))
    cands.sort(reverse=True)

    room_iou = [0.0] * len(rp)
    used_r: set[int] = set()
    used_f: set[int] = set()
    for iou, ri, fi in cands:
        if ri in used_r or fi in used_f:
            continue
        used_r.add(ri); used_f.add(fi)
        room_iou[ri] = iou
    matched = sum(1 for x in room_iou if x >= iou_thresh)
    spare = [round(faces[i].area / 1e6, 4)          # m^2
             for i in range(len(faces)) if i not in used_f]
    spare.sort(reverse=True)
    return {"ok": True, "n_rooms": len(rp), "n_faces": len(faces),
            "matched": matched, "all_matched": matched == len(rp),
            "extra_faces": len(faces) - len(rp),
            "unmatched_faces": len(spare),
            "unmatched_face_area_m2": round(sum(spare), 4),
            "unmatched_face_areas_m2": spare[:8],
            "min_iou": min(room_iou) if room_iou else 0.0,
            "ious": [round(x, 6) for x in room_iou]}


# --------------------------------------------------------------------------
# unlabelled space (the corridor question)
# --------------------------------------------------------------------------

def unlabelled_space(raw: dict, plan: Plan) -> dict:
    """How much of ResPlan's `inner` envelope carries no room label?

    Returns the void's area fraction plus, for each sizeable component, the
    minimum width (via the largest inscribed circle: 2 * the maximum negative
    buffer that survives) and the number of labelled rooms it touches. A
    corridor is narrow (min width ~ 1-1.5 m) and touches several rooms; a
    "void between blocks" is wide and touches few.
    """
    inner = raw.get("inner")
    if inner is None or getattr(inner, "is_empty", True):
        return {"ok": False, "reason": "no inner"}
    mm = scale_mm_per_unit(raw)
    src = [g for k in ROOM_KEYS for g in geoms(raw.get(k))
           if isinstance(g, Polygon) and g.area > 1e-6]
    if not src:
        return {"ok": False, "reason": "no rooms"}
    rooms_u = _union(src)
    inner_u = _union(geoms(inner))
    if rooms_u is None or inner_u is None or inner_u.is_empty:
        return {"ok": False, "reason": "union failed"}
    wd = float(raw.get("wall_depth") or 4.0)

    try:
        void = inner_u.difference(rooms_u.buffer(wd * 0.5))
    except Exception:
        return {"ok": False, "reason": "difference failed"}

    parts = [g for g in geoms(void) if isinstance(g, Polygon)]
    # a component must be at least one wall-thickness square to be real space
    parts = [g for g in parts if g.area >= wd * wd]
    parts.sort(key=lambda g: -g.area)

    comps = []
    for g in parts[:8]:
        # min width via largest inscribed circle, bisected on the buffer radius
        lo, hi = 0.0, max(g.bounds[2] - g.bounds[0], g.bounds[3] - g.bounds[1]) / 2.0
        for _ in range(18):
            mid = 0.5 * (lo + hi)
            if g.buffer(-mid).is_empty:
                hi = mid
            else:
                lo = mid
        touching = sum(1 for s in src if g.buffer(wd * 0.75).intersects(s))
        comps.append({
            "area_m2": round(g.area * mm * mm / 1e6, 3),
            "min_width_mm": round(2.0 * lo * mm),
            "rooms_touched": touching,
            "corridor_like": bool(2.0 * lo * mm <= 2000 and touching >= 3),
        })

    total = sum(g.area for g in parts)
    return {
        "ok": True,
        "inner_area_m2": round(inner_u.area * mm * mm / 1e6, 3),
        "labelled_area_m2": round(rooms_u.area * mm * mm / 1e6, 3),
        "unlabelled_frac": total / inner_u.area if inner_u.area > 0 else 0.0,
        "unlabelled_area_m2": round(total * mm * mm / 1e6, 3),
        "n_components": len(parts),
        "components": comps,
        "any_corridor_like": any(c["corridor_like"] for c in comps),
    }


# --------------------------------------------------------------------------
# source fidelity
# --------------------------------------------------------------------------

def source_fidelity(raw: dict, plan: Plan) -> dict:
    """Compare the IR against the original ResPlan geometry (in mm)."""
    mm = scale_mm_per_unit(raw)
    src = []
    for k in ROOM_KEYS:
        for g in geoms(raw.get(k)):
            if isinstance(g, Polygon) and g.area > 1e-6:
                src.append(g)
    if not src:
        return {"ok": False}
    src_u = _union(src)
    if src_u is None or src_u.is_empty:
        return {"ok": False, "reason": "source union failed"}
    src_area_mm2 = src_u.area * mm * mm
    ir_polys = [p for _, p in _room_polys(plan)]
    ir_u = _union(ir_polys) if ir_polys else None
    if ir_u is None or ir_u.is_empty:
        return {"ok": False, "reason": "IR union failed"}
    src_mm = affinity.scale(src_u, xfact=mm, yfact=mm, origin=(0, 0))
    iou = _iou(src_mm, ir_u)
    return {
        "ok": True,
        "n_rooms_src": len(src), "n_rooms_ir": len(plan.rooms),
        "room_count_equal": len(src) == len(plan.rooms),
        "area_iou": iou,
        "total_area_err": abs(ir_u.area - src_area_mm2) / src_area_mm2,
    }


# --------------------------------------------------------------------------
# openings
# --------------------------------------------------------------------------

def opening_hosting(plan: Plan) -> dict:
    """Hosting rate broken down by opening kind, from the converter's own log."""
    st = plan.provenance.get("opening_stats", {}) or {}
    by_kind = st.get("by_kind", {}) or {}
    out = {"total": st.get("total", 0), "hosted": st.get("hosted", 0),
           "via_chord": st.get("via_chord", 0),
           "via_fallback": st.get("via_fallback", 0),
           "by_kind": {}, "unhosted": st.get("unhosted", [])}
    for kind, d in by_kind.items():
        t, h = d.get("total", 0), d.get("hosted", 0)
        out["by_kind"][kind] = {"total": t, "hosted": h,
                                "rate": h / t if t else float("nan")}
    return out


def opening_geometry(plan: Plan) -> dict:
    """Structural checks on the parametric openings and walls.

    Mirrors the assertions in tests/js/verify.ts so a failure is caught on both
    sides of the language boundary.
    """
    walls = {w.id: w for w in plan.walls}
    bad_wall = bad_pos = bad_fit = 0
    bad_thickness = bad_height = 0
    bad_room_wall = 0
    for w in plan.walls:
        if w.thickness <= 0:
            bad_thickness += 1
        if w.height <= 0:
            bad_height += 1
    for o in plan.openings:
        w = walls.get(o.wall_id)
        if w is None:
            bad_wall += 1
            continue
        if not (0.0 <= o.position <= 1.0):
            bad_pos += 1
        L = w.length
        if L <= 0:
            bad_fit += 1
            continue
        half = 0.5 * o.width
        centre = o.position * L
        # the opening must lie inside the wall run, allowing 1 mm of rounding
        if centre - half < -1.0 or centre + half > L + 1.0:
            bad_fit += 1
    for r in plan.rooms:
        for wid in r.wall_ids:
            if wid not in walls:
                bad_room_wall += 1
    return {
        "n_walls": len(plan.walls), "n_openings": len(plan.openings),
        "orphan_opening_wall": bad_wall,
        "position_out_of_range": bad_pos,
        "opening_overruns_wall": bad_fit,
        "wall_thickness_non_positive": bad_thickness,
        "wall_height_non_positive": bad_height,
        "room_wall_id_missing": bad_room_wall,
        "clean": not any((bad_wall, bad_pos, bad_fit, bad_thickness,
                          bad_height, bad_room_wall)),
    }


# --------------------------------------------------------------------------
# walls
# --------------------------------------------------------------------------

def wall_stats(plan: Plan) -> dict:
    L = sorted(w.length for w in plan.walls)
    n = len(L)
    def pc(p: float) -> float:
        if not L:
            return float("nan")
        return L[min(n - 1, int(round(p / 100.0 * (n - 1))))]
    return {
        "n_walls": n,
        "n_rooms": len(plan.rooms),
        "walls_per_room": n / len(plan.rooms) if plan.rooms else float("nan"),
        "len_p10_mm": pc(10), "len_median_mm": pc(50), "len_p90_mm": pc(90),
        "n_under_400mm": sum(1 for x in L if x < 400),
        "n_under_1000mm": sum(1 for x in L if x < 1000),
        "total_len_mm": sum(L),
        "distinct_thickness": len({w.thickness for w in plan.walls}),
    }


def degenerate_counters(plan: Plan) -> dict[str, int]:
    return dict(plan.provenance.get("degenerate", {}) or {})


# --------------------------------------------------------------------------
# adjacency graph agreement vs ResPlan's own plan_to_graph
# --------------------------------------------------------------------------

_UTILS = None


def load_resplan_utils(path: str | None = None):
    """Import ResPlan's resplan_utils.py by file path (it is not a package)."""
    global _UTILS
    if _UTILS is not None:
        return _UTILS
    import importlib.util
    p = path or default_utils_path()
    spec = importlib.util.spec_from_file_location("resplan_utils", p)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load resplan_utils from {p}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _UTILS = mod
    return mod


def ir_adjacency(plan: Plan) -> dict:
    """Room adjacency implied by the IR: which rooms does each opening join?

    An opening's midpoint on its host wall is stepped perpendicular to either
    side; whichever room polygon contains the stepped point is on that side.
    The room tiling meets exactly on the wall centreline, so a step of a few
    tens of mm already lands inside the neighbour.

    Living rooms are contracted to a single `living` node, matching ResPlan's
    `plan_to_graph` convention of unioning all living parts into one node.
    """
    rp = _room_polys(plan)
    cat = {r.id: r.category for r in plan.rooms}
    node = {rid: ("living" if cat.get(rid) == "living" else rid) for rid, _ in rp}
    polys = [p for _, p in rp]
    ids = [rid for rid, _ in rp]
    tree = STRtree(polys) if polys else None
    walls = {w.id: w for w in plan.walls}

    def room_at(x: float, y: float) -> str | None:
        if tree is None:
            return None
        pt = Point(x, y)
        for i in tree.query(pt):
            i = int(i)
            if polys[i].covers(pt):
                return node[ids[i]]
        return None

    edges: dict[tuple[str, str], str] = {}
    outside: set[str] = set()
    unresolved = 0
    for o in plan.openings:
        w = walls.get(o.wall_id)
        if w is None:
            unresolved += 1
            continue
        L = w.length
        if L <= 0:
            unresolved += 1
            continue
        dx, dy = (w.end.x - w.start.x) / L, (w.end.y - w.start.y) / L
        cx = w.start.x + dx * o.position * L
        cy = w.start.y + dy * o.position * L
        nx, ny = -dy, dx
        # Sample across the opening's width, not just at its midpoint: an
        # opening that sits at a corner has its midpoint normal landing in a
        # third room or outside the tiling, which loses the edge. Majority vote
        # over the samples on each side is stable there.
        span = 0.4 * o.width
        votes: list[dict[str, int]] = [{}, {}]
        for u in (-span, -0.5 * span, 0.0, 0.5 * span, span):
            px, py = cx + dx * u, cy + dy * u
            for side, sgn in ((0, 1.0), (1, -1.0)):
                for step in (60.0, 150.0, 300.0):
                    r = room_at(px + nx * step * sgn, py + ny * step * sgn)
                    if r is not None:
                        votes[side][r] = votes[side].get(r, 0) + 1
                        break
        a = max(votes[0], key=votes[0].get) if votes[0] else None
        b = max(votes[1], key=votes[1].get) if votes[1] else None
        if a is not None and a == b:
            # both normals landed in the same room (opening inside an L-shaped
            # room, or a corner) -- fall back to the runner-up on either side
            alt0 = sorted((v, k) for k, v in votes[0].items() if k != a)
            alt1 = sorted((v, k) for k, v in votes[1].items() if k != a)
            if alt1:
                b = alt1[-1][1]
            elif alt0:
                a = alt0[-1][1]
        kind = {"door": "via_door", "window": "via_window",
                "front_door": "direct"}[o.kind]
        if a is not None and b is not None and a != b:
            key = (a, b) if a < b else (b, a)
            edges.setdefault(key, kind)
        elif o.kind == "front_door":
            for side in (a, b):
                if side is not None:
                    outside.add(side)
        elif a is None and b is None:
            unresolved += 1
    return {"edges": edges, "outside": sorted(outside), "unresolved": unresolved,
            "nodes": sorted(set(node.values()))}


def _ref_graph(raw: dict, plan: Plan, utils) -> dict:
    """plan_to_graph edges relabelled onto IR room nodes."""
    mm = scale_mm_per_unit(raw)
    rp = _room_polys(plan)
    cat = {r.id: r.category for r in plan.rooms}
    node = {rid: ("living" if cat.get(rid) == "living" else rid) for rid, _ in rp}
    polys = [p for _, p in rp]
    ids = [rid for rid, _ in rp]
    tree = STRtree(polys) if polys else None

    G = utils.plan_to_graph(dict(raw))

    def to_ir(nid: str) -> str | None:
        g = G.nodes[nid].get("geometry")
        if g is None or getattr(g, "is_empty", True):
            return None
        c = utils.centroid(g) if hasattr(g, "area") else g.centroid
        pt = Point(c.x * mm, c.y * mm)
        if tree is not None:
            for i in tree.query(pt):
                i = int(i)
                if polys[i].covers(pt):
                    return node[ids[i]]
            # nearest fallback: labels can be a hair outside after snapping
            best, bd = None, float("inf")
            for i, p in enumerate(polys):
                d = p.distance(pt)
                if d < bd:
                    best, bd = i, d
            if best is not None and bd < 500.0:
                return node[ids[best]]
        return None

    mapping = {nid: to_ir(nid) for nid in G.nodes}
    edges: dict[tuple[str, str], str] = {}
    outside: set[str] = set()
    for u, v, d in G.edges(data=True):
        et = d.get("type", "?")
        if et == "fallback":
            continue
        tu = G.nodes[u].get("type"); tv = G.nodes[v].get("type")
        if tu == "front_door" or tv == "front_door":
            other = mapping.get(v if tu == "front_door" else u)
            if other:
                outside.add(other)
            continue
        a, b = mapping.get(u), mapping.get(v)
        if a is None or b is None or a == b:
            continue
        key = (a, b) if a < b else (b, a)
        edges.setdefault(key, et)
    unmapped = sum(1 for nid, m in mapping.items()
                   if m is None and G.nodes[nid].get("type") != "front_door")
    return {"edges": edges, "outside": sorted(outside), "unmapped_nodes": unmapped,
            "n_ref_nodes": G.number_of_nodes()}


# A real opening is at least MIN_OPENING_MM wide, so a connector polygon that
# overlaps a party wall by less than this is only clipping its corner.
FN_STRADDLE_MM = 200.0


def attribute_missing_edges(raw: dict, plan: Plan,
                            missing: set[tuple[str, str]]) -> dict[str, int]:
    """Say who is wrong when plan_to_graph has an edge the IR does not.

    For each missing pair, look at the two rooms' actual shared party boundary
    and ask whether any ResPlan door/window polygon straddles it:

    * no shared boundary at all -> the reference edge is geometrically
      impossible (its buffered overlap test fired across a corner);
    * a shared boundary but no connector on it -> the reference asserts a
      walkable link where the source data has no opening;
    * a connector straddling it -> a genuine miss on our side.

    Measured on 1,500 plans: 87.2% / 11.9% / 0.9%. Without this split the raw
    recall reads 0.909 and looks like a converter problem; it is not.
    """
    out = {"reference_no_party_wall": 0, "reference_no_connector": 0,
           "ir_miss": 0, "unclassified": 0}
    if not missing:
        return out
    mm = scale_mm_per_unit(raw)
    merged: dict[str, Polygon] = {}
    cats = {r.id: r.category for r in plan.rooms}
    for rid, poly in _room_polys(plan):
        key = "living" if cats.get(rid) == "living" else rid
        merged[key] = (_union([merged[key], poly]) if key in merged else poly)

    conns = [affinity.scale(g, xfact=mm, yfact=mm, origin=(0, 0))
             for k in ("door", "window", "front_door") for g in geoms(raw.get(k))
             if isinstance(g, Polygon) and g.area > 1e-9]
    cu = _union(conns)

    for a, b in missing:
        pa, pb = merged.get(a), merged.get(b)
        if pa is None or pb is None:
            out["unclassified"] += 1
            continue
        try:
            party = pa.boundary.intersection(pb.boundary)
        except Exception:
            out["unclassified"] += 1
            continue
        if party.is_empty or party.length < 50.0:
            out["reference_no_party_wall"] += 1
            continue
        straddle = 0.0
        if cu is not None:
            try:
                band = party.buffer(30.0)
                straddle = cu.intersection(band).area / 60.0   # ~overlap length
            except Exception:
                straddle = 0.0
        if straddle >= FN_STRADDLE_MM:
            out["ir_miss"] += 1
        else:
            out["reference_no_connector"] += 1
    return out


def adjacency_agreement(raw: dict, plan: Plan, utils=None) -> dict:
    """Agreement between IR-derived room adjacency and ResPlan's plan_to_graph.

    Note on edge types: the real `plan_to_graph` emits `via_door`, `via_window`
    and `via_opening` (plus `direct` to front-door nodes and `fallback` for
    connectivity repair). The `adjacency` / `direct` room-to-room types quoted
    in the ResPlan paper come from a superseded builder and do not appear.

    `via_opening` is a doorless walk-through (open kitchen, archway). The IR has
    no element for it -- ResPlan gives us no polygon there -- so it is reported
    separately and excluded from the headline F1, which would otherwise be
    penalised for a fact the source data never encodes.
    """
    utils = utils or load_resplan_utils()
    got = ir_adjacency(plan)
    ref = _ref_graph(raw, plan, utils)

    ge, re = set(got["edges"]), set(ref["edges"])
    ref_open = {e for e in re if ref["edges"][e] == "via_opening"}
    ref_conn = re - ref_open                      # door/window edges

    tp = len(ge & ref_conn)
    fp = len(ge - re)                             # not in ref at all
    fp_open = len((ge & ref_open))                # we have it, ref calls it open
    fn = len(ref_conn - ge)
    prec = tp / (tp + fp) if (tp + fp) else float("nan")
    rec = tp / (tp + fn) if (tp + fn) else float("nan")
    f1 = (2 * prec * rec / (prec + rec)
          if prec == prec and rec == rec and (prec + rec) > 0 else float("nan"))

    attrib = attribute_missing_edges(raw, plan, ref_conn - ge)
    fn_ir = attrib["ir_miss"] + attrib["unclassified"]
    rec_adj = (tp / (tp + fn_ir)) if (tp + fn_ir) else float("nan")

    by_type: dict[str, list[int]] = {}
    for e, t in ref["edges"].items():
        d = by_type.setdefault(t, [0, 0])
        d[0] += 1
        if e in ge:
            d[1] += 1

    return {
        "ok": True,
        "n_ir_edges": len(ge), "n_ref_edges": len(re),
        "n_ref_walkable": len(ref_conn), "n_ref_via_opening": len(ref_open),
        "tp": tp, "fp": fp, "fn": fn, "fp_matched_via_opening": fp_open,
        "precision": prec, "recall": rec, "f1": f1,
        "fn_attribution": attrib,
        "fn_attributable_to_ir": fn_ir,
        "recall_adjusted": rec_adj,
        "jaccard": len(ge & re) / len(ge | re) if (ge | re) else float("nan"),
        "recall_by_ref_type": {t: {"n": v[0], "hit": v[1]} for t, v in by_type.items()},
        "outside_agreement": (len(set(got["outside"]) & set(ref["outside"])),
                              len(set(got["outside"]) | set(ref["outside"]))),
        "unresolved_openings": got["unresolved"],
        "unmapped_ref_nodes": ref["unmapped_nodes"],
    }
