"""Round-trip and fidelity metrics for the ResPlan -> IR -> Project pipeline."""
from __future__ import annotations
from shapely.geometry import Polygon, LineString, MultiLineString
from shapely.ops import unary_union, polygonize
from .ir import Plan
from .resplan import geoms, ROOM_KEYS, scale_mm_per_unit


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


def face_recovery(plan: Plan) -> dict:
    """Can rooms be re-derived as faces of the wall centreline graph?

    This is the same operation OpenPlan3D's detectRooms performs, so it is a
    direct test of whether the converted wall graph is well formed.
    """
    lines = [LineString([w.start.as_tuple(), w.end.as_tuple()]) for w in plan.walls]
    if not lines:
        return {"ok": False, "reason": "no walls"}
    noded = unary_union(lines)
    faces = [f for f in polygonize(noded) if f.area > 1e4]     # > 0.01 m^2
    room_polys = [Polygon([p.as_tuple() for p in r.polygon])
                  for r in plan.rooms if len(r.polygon) >= 3]
    room_polys = [p for p in room_polys if p.is_valid and p.area > 0]
    if not room_polys:
        return {"ok": False, "reason": "no room polygons"}
    tgt = unary_union(room_polys)
    got = unary_union(faces) if faces else None
    iou = 0.0
    if got is not None and not got.is_empty:
        u = unary_union([tgt, got])
        iou = tgt.intersection(got).area / u.area if u.area > 0 else 0.0
    return {"ok": True, "n_faces": len(faces), "n_rooms": len(room_polys),
            "face_count_exact": len(faces) == len(room_polys),
            "face_count_within_1": abs(len(faces) - len(room_polys)) <= 1,
            "area_iou": iou}


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
    src_u = unary_union(src)
    src_area_mm2 = src_u.area * mm * mm
    ir_polys = [Polygon([p.as_tuple() for p in r.polygon])
                for r in plan.rooms if len(r.polygon) >= 3]
    ir_polys = [p for p in ir_polys if p.is_valid and p.area > 0]
    ir_u = unary_union(ir_polys) if ir_polys else None
    if ir_u is None or ir_u.is_empty:
        return {"ok": False}
    # scale source into mm space for an IoU comparison
    from shapely import affinity
    src_mm = affinity.scale(src_u, xfact=mm, yfact=mm, origin=(0, 0))
    u = unary_union([src_mm, ir_u])
    return {
        "ok": True,
        "n_rooms_src": len(src), "n_rooms_ir": len(plan.rooms),
        "room_count_equal": len(src) == len(plan.rooms),
        "area_iou": src_mm.intersection(ir_u).area / u.area if u.area > 0 else 0.0,
        "total_area_err": abs(ir_u.area - src_area_mm2) / src_area_mm2,
    }
