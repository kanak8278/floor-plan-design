"""IR <-> OpenPlan3D `Project` JSON adapter.

OpenPlan3D stores geometry in floating-point **centimetres** (settings.ts
gridSize defaults to 25 cm; furniture dims are cm). The canonical IR is integer
millimetres, so cm is a lossy projection and the IR stays authoritative.

## What "lossless" means here

This adapter is two-way for **every field OpenPlan3D can express**, across
**every storey**, including the presentation layer. It is lossless *modulo one
declared quantisation*: centimetre floats are snapped to 1 mm on entry, the
snapped value becomes canonical, and the trip is idempotent from then on.
`design_round_trip_report()` asserts that idempotence.

The earlier version of this module was one-way by construction -- it read only
`activeFloorId` and silently dropped columns, guides, measurements, dimension
and text annotations, groups, entourage, the background image, per-wall
colours and textures, `curvePoint`, and every other storey. That was correct
for what it was used for (converting our own generated plans, one direction,
17,000/17,000 on `metrics.ir_identity`) and wrong as a bridge for live editing:
the first agent edit would have deleted the user's second floor and their
dimension strings. `ir_identity` never noticed because it compares only wall
geometry, opening parameters, room labels, and the plot -- see its docstring.

## Derived defaults, and the one place Project cannot represent the IR

Several Project fields are *derived* from IR semantics rather than stored: a
bathroom's floor texture, a room's OpenPlan3D category, a door's type. The IR
has two states there -- "explicitly marble-white" and "derived, and the
derivation happens to be marble-white" -- and Project has one field, so the
distinction cannot live in the field alone. Storing the derived value on the
way in would give every plan the solver has ever produced an explicit texture
it never asked for, and `IR -> Project -> IR` would stop being an identity.

So the sidecar records *which* values were overrides, and only those. For
documents the editor wrote there is no sidecar entry and `_explicit()` falls
back to comparing against the derived value; the residual ambiguity is an
override equal to its own default, which is invisible because both states emit
the same Project field.
"""
from __future__ import annotations
from typing import Any
from .ir import (
    Plan, Design, Wall, Opening, Room, Site, Stair, Furniture, Column, P,
    Presentation, GuideLine, Measurement, DimAnnotation, TextAnnotation,
    ElementGroup, EntourageItem, EntourageDef, BackgroundImage,
)

MM_PER_CM = 10.0
SIDECAR = "_fpeval"
SIDECAR_VERSION = 2

# Kept as explicit fallbacks for ResPlan's six labels; the canonical source is
# fpeval.roomtypes, consulted first so a new room type needs one edit, not three.
from . import roomtypes as _rt

CATEGORY_TO_ROOMTYPE = {
    "balcony": "outdoor", "storage": "utility",
    "living": "indoor", "kitchen": "indoor", "bedroom": "indoor", "bathroom": "indoor",
    "stair": "indoor",
}
FLOOR_TEXTURE = {
    "bathroom": "ceramic-white", "kitchen": "ceramic-gray", "balcony": "slate",
    "living": "light-oak", "bedroom": "light-oak", "storage": "concrete",
    "stair": "slate",
}


# --------------------------------------------------------------------------
# units
# --------------------------------------------------------------------------

def _cm(mm: int | float) -> float:
    return mm / MM_PER_CM


def _mm(cm: Any) -> int:
    return round(float(cm) * MM_PER_CM)


def _pt(p: P) -> dict[str, float]:
    return {"x": _cm(p.x), "y": _cm(p.y)}


def _p(d: Any) -> P:
    return P(_mm(d["x"]), _mm(d["y"]))


def _opt_p(d: Any) -> P | None:
    return _p(d) if isinstance(d, dict) and "x" in d and "y" in d else None


# --------------------------------------------------------------------------
# derived defaults -- used by BOTH directions so the round trip is an identity
# --------------------------------------------------------------------------

def _derived_floor_texture(category: str) -> str:
    t = _rt.get(category)
    return t.floor_texture if t else FLOOR_TEXTURE.get(category, "light-oak")


def _derived_room_class(category: str) -> str:
    t = _rt.get(category)
    return t.op3d_room_type if t else CATEGORY_TO_ROOMTYPE.get(category, "indoor")


def _derived_floor_name(level: int) -> str:
    return "Ground Floor" if level == 0 else f"Floor {level}"


def _derived_opening_type(kind: str) -> str:
    """What OpenPlan3D `type` an opening gets when the IR names no subtype.

    `front_door -> "opening"` looks wrong and is preserved deliberately: it is
    what every plan in the corpus and the gallery was generated with, and
    changing it here would silently restyle 17,000 verified plans. Fix it in the
    converter that mints front doors, not in the adapter that round-trips them.
    """
    if kind == "window":
        return "standard"
    return "single" if kind == "door" else "opening"


def _norm(value: Any, derived: Any) -> Any:
    """Empty out a value that merely repeats what we would have derived."""
    return "" if value == derived else value


def _explicit(overrides: dict[str, Any], key: str,
              project_value: Any, derived: Any) -> str:
    """Recover whether a Project field was an *override* or just the default.

    `Project` has one field where the IR has two states -- "explicitly
    marble-white" and "derived, which happens to be marble-white" -- so the
    distinction cannot survive in the Project field alone. When we wrote the
    document, the sidecar records which values were overrides and this is exact.

    When the *editor* wrote it there is no sidecar entry, and we fall back to
    comparing against the derived value. The residual ambiguity is an override
    that equals the derived value, which reads back as "derived". That is
    invisible by construction: both states emit the same Project field.
    """
    if key in overrides:
        return str(overrides[key] or "")
    return _norm(project_value or "", derived)


# --------------------------------------------------------------------------
# IR -> Project
# --------------------------------------------------------------------------

def _wall_json(w: Wall) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": w.id,
        "start": _pt(w.start), "end": _pt(w.end),
        "thickness": _cm(w.thickness),
        "height": _cm(w.height),
        "color": w.color,
    }
    if w.curve_point is not None:
        out["curvePoint"] = _pt(w.curve_point)
    for key, val in (("texture", w.texture),
                     ("interiorColor", w.interior_color),
                     ("interiorTexture", w.interior_texture),
                     ("exteriorColor", w.exterior_color),
                     ("exteriorTexture", w.exterior_texture)):
        if val:
            out[key] = val
    return out


def _opening_json(o: Opening) -> tuple[str, dict[str, Any]]:
    if o.kind == "window":
        return "windows", {
            "id": o.id, "wallId": o.wall_id, "position": o.position,
            "width": _cm(o.width),
            "height": _cm(o.head - o.sill),
            "sillHeight": _cm(o.sill),
            "type": o.subtype or _derived_opening_type(o.kind),
        }
    return "doors", {
        "id": o.id, "wallId": o.wall_id, "position": o.position,
        "width": _cm(o.width), "height": _cm(o.head),
        "type": o.subtype or _derived_opening_type(o.kind),
        "swingDirection": o.swing_direction, "flipSide": o.flip_side,
    }


def _room_json(r: Room) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": r.id, "name": r.name, "walls": list(r.wall_ids),
        "floorTexture": r.floor_texture or _derived_floor_texture(r.category),
        # m^2 to 2dp, matching what detectRooms emits. The exact mm^2 lives in
        # the sidecar -- this field is for the editor's label, not for us.
        "area": round(r.area / 1_000_000.0, 2),
        "roomType": r.room_class or _derived_room_class(r.category),
    }
    if r.color:
        out["color"] = r.color
    if r.label_offset is not None:
        out["labelOffset"] = _pt(r.label_offset)
    if r.anchor is not None:
        out["anchor"] = _pt(r.anchor)
    return out


def _stair_json(st: Stair) -> dict[str, Any]:
    return {
        "id": st.id, "position": _pt(st.position), "rotation": st.rotation,
        "width": _cm(st.width), "depth": _cm(st.depth),
        "riserCount": st.riser_count, "direction": st.direction,
        "stairType": st.stair_type,
    }


def _column_json(c: Column) -> dict[str, Any]:
    return {
        "id": c.id, "position": _pt(c.position), "rotation": c.rotation,
        "shape": c.shape, "diameter": _cm(c.size), "height": _cm(c.height),
        "color": c.color,
    }


def _furniture_json(f: Furniture) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": f.id, "catalogId": f.catalog_id, "position": _pt(f.position),
        "rotation": f.rotation,
        "scale": {"x": f.scale_x, "y": f.scale_y, "z": f.scale_z},
    }
    if f.width:
        out["width"] = _cm(f.width)
    if f.depth:
        out["depth"] = _cm(f.depth)
    if f.height:
        out["height"] = _cm(f.height)
    if f.locked:
        out["locked"] = True
    if f.color:
        out["color"] = f.color
    if f.material:
        out["material"] = f.material
    return out


def _presentation_json(pr: Presentation) -> dict[str, Any]:
    out: dict[str, Any] = {
        "guides": [{"id": g.id, "orientation": g.orientation,
                    "position": _cm(g.position)} for g in pr.guides],
        "measurements": [{"id": m.id, "x1": _cm(m.start.x), "y1": _cm(m.start.y),
                          "x2": _cm(m.end.x), "y2": _cm(m.end.y)}
                         for m in pr.measurements],
        "annotations": [
            {"id": d.id, "x1": _cm(d.start.x), "y1": _cm(d.start.y),
             "x2": _cm(d.end.x), "y2": _cm(d.end.y), "offset": _cm(d.offset),
             **({"label": d.label} if d.label else {})}
            for d in pr.dimensions],
        "textAnnotations": [{"id": t.id, "x": _cm(t.position.x),
                             "y": _cm(t.position.y), "text": t.text,
                             "fontSize": t.font_size, "color": t.color,
                             "rotation": t.rotation} for t in pr.texts],
        "groups": [{"id": g.id, "elementIds": list(g.element_ids)}
                   for g in pr.groups],
        "entourage": [
            {"id": e.id, "defId": e.def_id, "position": _pt(e.position),
             "width": _cm(e.width), "rotation": e.rotation,
             **({"opacity": e.opacity} if e.opacity != 1.0 else {}),
             **({"locked": True} if e.locked else {})}
            for e in pr.entourage],
    }
    if pr.background is not None:
        b = pr.background
        out["backgroundImage"] = {
            "dataUrl": b.data_url, "position": _pt(b.position),
            "scale": b.scale, "opacity": b.opacity, "rotation": b.rotation,
            "locked": b.locked,
        }
    return out


def _floor_id_of(st: Plan) -> str:
    """The id this storey carries on the Project side."""
    return st.project_floor_id or f"floor-{st.id}"


def _floor_json(st: Plan, index: int) -> dict[str, Any]:
    doors: list[dict] = []
    windows: list[dict] = []
    for o in st.openings:
        bucket, blob = _opening_json(o)
        (windows if bucket == "windows" else doors).append(blob)

    floor: dict[str, Any] = {
        "id": _floor_id_of(st),
        "name": st.name or _derived_floor_name(st.level),
        "level": st.level,
        "walls": [_wall_json(w) for w in st.walls],
        "rooms": [_room_json(r) for r in st.rooms],
        "doors": doors,
        "windows": windows,
        "furniture": [_furniture_json(f) for f in st.furniture],
        "stairs": [_stair_json(s) for s in st.stairs],
        "columns": [_column_json(c) for c in st.columns],
    }
    floor.update(_presentation_json(st.presentation))
    return floor


def _floor_sidecar(st: Plan) -> dict[str, Any]:
    """Everything about a storey that `Project` has no field for."""
    return {
        "storey_id": st.id,
        "storey_height": st.storey_height,
        **({"storey_name": st.name} if st.name else {}),
        "provenance": st.provenance,
        "site": {
            "plot_polygon_cm": [_pt(p) for p in st.site.plot_polygon],
            "north_deg": st.site.north_deg,
            "setbacks_mm": dict(st.site.setbacks_mm),
        },
        # Project's roomType has 4 values; our taxonomy has 18. The category is
        # what every rule and the solver read, so it cannot be inferred back.
        "room_categories": {r.id: r.category for r in st.rooms},
        # Project rounds area to 2dp m^2 for its label. Keep the exact value.
        "room_areas_mm2": {r.id: r.area for r in st.rooms},
        # Which style values were overrides rather than derived defaults. Only
        # the overrides are listed, so absence means "derive it".
        "room_classes": {r.id: r.room_class for r in st.rooms if r.room_class},
        "room_textures": {r.id: r.floor_texture for r in st.rooms
                          if r.floor_texture},
        "opening_subtypes": {o.id: o.subtype for o in st.openings if o.subtype},
        "room_polygons_cm": {r.id: [_pt(p) for p in r.polygon]
                             for r in st.rooms if r.polygon},
        # door vs front_door is a semantic distinction Project does not carry.
        "opening_kinds": {o.id: o.kind for o in st.openings},
        "opening_heads": {o.id: o.head for o in st.openings},
        "opening_sills": {o.id: o.sill for o in st.openings},
        "stair_rooms": {s.id: s.room_id for s in st.stairs},
        "furniture_rooms": {f.id: f.room_id for f in st.furniture},
    }


def to_project(x: Plan | Design, name: str | None = None) -> dict[str, Any]:
    """Render a plan or a whole design as OpenPlan3D `Project` JSON.

    Accepts a bare `Plan` so every existing caller keeps working; it is wrapped
    as a single-storey `Design`.
    """
    design = x if isinstance(x, Design) else Design.single(x)
    storeys = design.storeys or []
    floors = [_floor_json(st, i) for i, st in enumerate(storeys)]
    active = design.active
    active_floor_id = _floor_id_of(active) if active else ""

    src = (active.provenance.get("source", "?") if active else "?")
    return {
        "id": f"proj-{design.id}",
        "name": name or design.name or f"ResPlan {design.id}",
        "description": design.description or f"Converted from {src}",
        "floors": floors,
        "activeFloorId": active_floor_id,
        "createdAt": design.created_at or "1970-01-01T00:00:00.000Z",
        "updatedAt": design.updated_at or "1970-01-01T00:00:00.000Z",
        "customEntourage": [{"id": d.id, "name": d.name, "dataUrl": d.data_url,
                             "aspect": d.aspect}
                            for d in design.custom_entourage],
        SIDECAR: {
            "version": SIDECAR_VERSION,
            "design_id": design.id,
            "provenance": design.provenance,
            "floors": {_floor_id_of(st): _floor_sidecar(st) for st in storeys},
            # -- legacy flat keys, for readers pinned to sidecar version 1 --
            **_legacy_flat_sidecar(active),
        },
    }


def _legacy_flat_sidecar(active: Plan | None) -> dict[str, Any]:
    """Version-1 sidecar keys for the active storey.

    Project JSON already sits in `localStorage`, in `tests/eval_corpus.json`,
    and in the gallery's `projects.json`. Emitting the flat keys alongside the
    nested ones means a reader from either era works, at the cost of a few
    duplicated bytes on the active floor.
    """
    if active is None:
        return {}
    return {
        "plot_polygon_cm": [_pt(p) for p in active.site.plot_polygon],
        "north_deg": active.site.north_deg,
        "room_categories": {r.id: r.category for r in active.rooms},
        "opening_kinds": {o.id: o.kind for o in active.openings},
        "opening_heads": {o.id: o.head for o in active.openings},
        "stair_rooms": {s.id: s.room_id for s in active.stairs},
        "furniture_rooms": {f.id: f.room_id for f in active.furniture},
    }


# --------------------------------------------------------------------------
# Project -> IR
# --------------------------------------------------------------------------

def _wall_ir(w: dict[str, Any]) -> Wall:
    return Wall(
        id=w["id"], start=_p(w["start"]), end=_p(w["end"]),
        thickness=_mm(w["thickness"]), height=_mm(w.get("height", 300)),
        curve_point=_opt_p(w.get("curvePoint")),
        color=w.get("color") or "#e5e7eb",
        texture=w.get("texture") or "",
        interior_color=w.get("interiorColor") or "",
        interior_texture=w.get("interiorTexture") or "",
        exterior_color=w.get("exteriorColor") or "",
        exterior_texture=w.get("exteriorTexture") or "",
    )


def _room_ir(r: dict[str, Any], side: dict[str, Any]) -> Room:
    cats = side.get("room_categories", {})
    areas = side.get("room_areas_mm2", {})
    polys = side.get("room_polygons_cm", {})
    category = cats.get(r["id"], "indoor")
    rid = r["id"]
    return Room(
        id=rid, name=r["name"], category=category,
        wall_ids=list(r.get("walls", [])),
        polygon=[_p(p) for p in polys.get(rid, [])],
        # Exact mm^2 when we wrote it; otherwise recover from the 2dp label.
        area=int(areas.get(rid, int(round(float(r.get("area", 0)) * 1_000_000)))),
        room_class=_explicit(side.get("room_classes", {}), rid,
                             r.get("roomType"), _derived_room_class(category)),
        floor_texture=_explicit(side.get("room_textures", {}), rid,
                                r.get("floorTexture"),
                                _derived_floor_texture(category)),
        color=r.get("color") or "",
        label_offset=_opt_p(r.get("labelOffset")),
        anchor=_opt_p(r.get("anchor")),
    )


def _openings_ir(fl: dict[str, Any], side: dict[str, Any]) -> list[Opening]:
    kinds = side.get("opening_kinds", {})
    heads = side.get("opening_heads", {})
    sills = side.get("opening_sills", {})
    subs = side.get("opening_subtypes", {})
    out: list[Opening] = []
    for d in fl.get("doors", []):
        kind = kinds.get(d["id"], "door")
        out.append(Opening(
            id=d["id"], kind=kind, wall_id=d["wallId"], position=d["position"],
            width=_mm(d["width"]),
            sill=int(sills.get(d["id"], 0)),
            head=int(heads.get(d["id"], _mm(d.get("height", 210)))),
            subtype=_explicit(subs, d["id"], d.get("type"),
                              _derived_opening_type(kind)),
            swing_direction=d.get("swingDirection") or "left",
            flip_side=bool(d.get("flipSide")),
        ))
    for w in fl.get("windows", []):
        kind = kinds.get(w["id"], "window")
        sill = _mm(w.get("sillHeight", 0))
        out.append(Opening(
            id=w["id"], kind=kind, wall_id=w["wallId"], position=w["position"],
            width=_mm(w["width"]), sill=sill,
            head=int(heads.get(w["id"], sill + _mm(w.get("height", 120)))),
            subtype=_explicit(subs, w["id"], w.get("type"),
                              _derived_opening_type(kind)),
        ))
    # Openings were emitted doors-then-windows but the IR's own order is by id.
    out.sort(key=_opening_sort_key)
    return out


def _opening_sort_key(o: Opening) -> tuple[int, int | float, str]:
    """Sort openings the way the IR mints them: `o0, o1, ... o10`.

    Ids that are not `o<digits>` sort after the numbered ones, by string, so a
    user-drawn door with a random id never reorders the generated ones.
    """
    body = o.id[1:] if o.id[:1] == "o" else o.id
    if body.isdigit():
        return (0, int(body), "")
    return (1, 0, o.id)


def _presentation_ir(fl: dict[str, Any]) -> Presentation:
    bg = fl.get("backgroundImage")
    return Presentation(
        guides=[GuideLine(id=g["id"], orientation=g["orientation"],
                          position=_mm(g["position"]))
                for g in fl.get("guides", [])],
        measurements=[Measurement(id=m["id"],
                                  start=P(_mm(m["x1"]), _mm(m["y1"])),
                                  end=P(_mm(m["x2"]), _mm(m["y2"])))
                      for m in fl.get("measurements", [])],
        dimensions=[DimAnnotation(id=a["id"],
                                  start=P(_mm(a["x1"]), _mm(a["y1"])),
                                  end=P(_mm(a["x2"]), _mm(a["y2"])),
                                  offset=_mm(a.get("offset", 40)),
                                  label=a.get("label") or "")
                    for a in fl.get("annotations", [])],
        texts=[TextAnnotation(id=t["id"], position=P(_mm(t["x"]), _mm(t["y"])),
                              text=t["text"], font_size=int(t.get("fontSize", 16)),
                              color=t.get("color") or "#1e293b",
                              rotation=float(t.get("rotation", 0.0)))
               for t in fl.get("textAnnotations", [])],
        groups=[ElementGroup(id=g["id"], element_ids=list(g.get("elementIds", [])))
                for g in fl.get("groups", [])],
        entourage=[EntourageItem(id=e["id"], def_id=e["defId"],
                                 position=_p(e["position"]),
                                 width=_mm(e["width"]),
                                 rotation=float(e.get("rotation", 0.0)),
                                 opacity=float(e.get("opacity", 1.0)),
                                 locked=bool(e.get("locked")))
                   for e in fl.get("entourage", [])],
        background=(BackgroundImage(
            data_url=bg["dataUrl"], position=_p(bg.get("position", {"x": 0, "y": 0})),
            scale=float(bg.get("scale", 1.0)), opacity=float(bg.get("opacity", 0.5)),
            rotation=float(bg.get("rotation", 0.0)), locked=bool(bg.get("locked")))
            if bg else None),
    )


def _floor_sidecar_for(proj: dict[str, Any], floor_id: str) -> dict[str, Any]:
    """The per-floor sidecar, tolerating both sidecar versions.

    Version 2 nests per floor. Version 1 was flat and described only the active
    floor, so for any other floor of a v1 project there is genuinely nothing to
    recover -- and there could not be, since v1 never wrote it.
    """
    side = proj.get(SIDECAR) or {}
    nested = (side.get("floors") or {}).get(floor_id)
    if nested is not None:
        return nested
    if floor_id == proj.get("activeFloorId"):
        return {
            "storey_id": str(floor_id).replace("floor-", ""),
            "site": {"plot_polygon_cm": side.get("plot_polygon_cm", []),
                     "north_deg": side.get("north_deg", 0.0),
                     "setbacks_mm": {}},
            "provenance": side.get("provenance", {}),
            "room_categories": side.get("room_categories", {}),
            "opening_kinds": side.get("opening_kinds", {}),
            "opening_heads": side.get("opening_heads", {}),
            "stair_rooms": side.get("stair_rooms", {}),
            "furniture_rooms": side.get("furniture_rooms", {}),
        }
    return {}


def _storey_ir(proj: dict[str, Any], fl: dict[str, Any]) -> Plan:
    floor_id = fl["id"]
    side = _floor_sidecar_for(proj, floor_id)
    site_d = side.get("site") or {}
    walls = [_wall_ir(w) for w in fl.get("walls", [])]
    rooms = [_room_ir(r, side) for r in fl.get("rooms", [])]
    st_rooms = side.get("stair_rooms", {})
    f_rooms = side.get("furniture_rooms", {})

    storey_id = side.get("storey_id") or str(floor_id).replace("floor-", "")
    plan = Plan(
        id=storey_id,
        walls=walls,
        openings=_openings_ir(fl, side),
        rooms=rooms,
        site=Site(
            plot_polygon=[_p(p) for p in site_d.get("plot_polygon_cm", [])],
            north_deg=float(site_d.get("north_deg", 0.0)),
            setbacks_mm=dict(site_d.get("setbacks_mm", {})),
        ),
        stairs=[Stair(id=s["id"], position=_p(s["position"]),
                      rotation=float(s.get("rotation", 0.0)),
                      width=_mm(s["width"]), depth=_mm(s["depth"]),
                      riser_count=int(s.get("riserCount", 14)),
                      direction=s.get("direction", "up"),
                      stair_type=s.get("stairType", "straight"),
                      room_id=st_rooms.get(s["id"]))
                for s in fl.get("stairs", [])],
        furniture=[Furniture(id=f["id"], catalog_id=f["catalogId"],
                             position=_p(f["position"]),
                             rotation=float(f.get("rotation", 0.0)),
                             width=_mm(f["width"]) if f.get("width") else 0,
                             depth=_mm(f["depth"]) if f.get("depth") else 0,
                             height=_mm(f["height"]) if f.get("height") else 0,
                             room_id=f_rooms.get(f["id"]),
                             locked=bool(f.get("locked")),
                             color=f.get("color") or "",
                             material=f.get("material") or "",
                             scale_x=float((f.get("scale") or {}).get("x", 1.0)),
                             scale_y=float((f.get("scale") or {}).get("y", 1.0)),
                             scale_z=float((f.get("scale") or {}).get("z", 1.0)))
                   for f in fl.get("furniture", [])],
        storey_height=int(side.get("storey_height", 3000)),
        provenance=dict(side.get("provenance", {})),
        columns=[Column(id=c["id"], position=_p(c["position"]),
                        rotation=float(c.get("rotation", 0.0)),
                        shape=c.get("shape", "round"),
                        size=_mm(c.get("diameter", 30)),
                        height=_mm(c.get("height", 300)),
                        color=c.get("color") or "#6b7280")
                 for c in fl.get("columns", [])],
        presentation=_presentation_ir(fl),
        # "" when the floor id is exactly what we would derive, so a plan
        # built in Python round-trips to itself. Anything else -- an editor
        # uid like "f9k2p1" -- is an override and is kept verbatim.
        project_floor_id=_norm(str(floor_id), f"floor-{storey_id}"),
        name=_explicit(side, "storey_name", fl.get("name"),
                       _derived_floor_name(int(fl.get("level", 0)))),
        level=int(fl.get("level", 0)),
    )
    if any(len(r.polygon) < 3 for r in plan.rooms):
        _derive_room_polygons(plan.walls, plan.rooms)
    return plan


def design_from_project(proj: dict[str, Any]) -> Design:
    """The lossless read: every storey, every field, every layer."""
    side = proj.get(SIDECAR) or {}
    floors = proj.get("floors") or []
    storeys = [_storey_ir(proj, fl) for fl in floors]
    active_floor = proj.get("activeFloorId")
    active_id = ""
    for fl, st in zip(floors, storeys):
        if fl["id"] == active_floor:
            active_id = st.id
            break
    if not active_id and storeys:
        active_id = storeys[0].id

    return Design(
        id=side.get("design_id") or str(proj.get("id", "")).replace("proj-", ""),
        storeys=storeys,
        active_storey_id=active_id,
        name=proj.get("name") or "Untitled",
        description=proj.get("description") or "",
        custom_entourage=[EntourageDef(id=d["id"], name=d["name"],
                                       data_url=d["dataUrl"],
                                       aspect=float(d["aspect"]))
                          for d in (proj.get("customEntourage") or [])],
        created_at=str(proj.get("createdAt") or ""),
        updated_at=str(proj.get("updatedAt") or ""),
        provenance=dict(side.get("provenance", {})),
    )


def from_project(proj: dict[str, Any]) -> Plan:
    """The **active storey only**, for callers that reason about one floor.

    Every rule, solver, and renderer is written against a single `Plan`, so this
    stays the convenient entry point. It is not a document read: a design with
    three floors comes back as one. Use `design_from_project` to round-trip.
    """
    design = design_from_project(proj)
    active = design.active
    return active if active is not None else Plan(id=design.id)


# --------------------------------------------------------------------------
# round-trip verification
# --------------------------------------------------------------------------

def design_round_trip_report(proj: dict[str, Any]) -> dict[str, Any]:
    """Is `Project -> Design -> Project` idempotent for this document?

    The honest statement of losslessness. Quantisation to 1 mm happens on the
    first pass in, so the check is that a *second* pass changes nothing: read,
    write, read, write, and compare the two writes. Anything the adapter drops
    shows up as a difference, including a whole floor.
    """
    once = to_project(design_from_project(proj), name=proj.get("name"))
    twice = to_project(design_from_project(once), name=once.get("name"))
    diffs = _diff(once, twice, "$")
    return {
        "idempotent": not diffs,
        "diffs": diffs[:40],
        "n_diffs": len(diffs),
        "floors_in": len(proj.get("floors") or []),
        "floors_out": len(once.get("floors") or []),
    }


def _diff(a: Any, b: Any, path: str) -> list[str]:
    """Structural diff, reported by path so a failure names the lost field."""
    if type(a) is not type(b) and not (isinstance(a, (int, float))
                                       and isinstance(b, (int, float))):
        return [f"{path}: type {type(a).__name__} != {type(b).__name__}"]
    if isinstance(a, dict):
        out: list[str] = []
        for k in sorted(set(a) | set(b)):
            if k not in a:
                out.append(f"{path}.{k}: missing on the left")
            elif k not in b:
                out.append(f"{path}.{k}: missing on the right")
            else:
                out += _diff(a[k], b[k], f"{path}.{k}")
        return out
    if isinstance(a, list):
        if len(a) != len(b):
            return [f"{path}: length {len(a)} != {len(b)}"]
        out = []
        for i, (x, y) in enumerate(zip(a, b)):
            out += _diff(x, y, f"{path}[{i}]")
        return out
    if isinstance(a, float) or isinstance(b, float):
        return [] if abs(float(a) - float(b)) < 1e-9 else [f"{path}: {a} != {b}"]
    return [] if a == b else [f"{path}: {a!r} != {b!r}"]


def _derive_room_polygons(walls: list[Wall], rooms: list[Room]) -> None:
    """Recover each room's polygon from the wall graph, in place.

    OpenPlan3D's `Project` stores a room as `walls: string[]` and derives the
    outline on the fly (`detectRooms` + `getRoomPolygon`), so a Project carries no
    room polygons at all. Reading one back without this step produced rooms with
    zero vertices, which made the validator report GEO.ROOM_DEGENERATE for every
    room on a plan it had just called clean -- 9 false errors on the read-back
    path. Derive them the same way the editor does.

    Only reached for projects with no `room_polygons_cm` sidecar (anything the
    editor wrote, or a version-1 project); when we wrote the polygons ourselves
    they are read back verbatim.
    """
    from shapely.geometry import LineString
    from shapely.ops import polygonize, unary_union

    segs = {w.id: LineString([w.start.as_tuple(), w.end.as_tuple()])
            for w in walls if w.length > 0}
    if not segs:
        return
    faces = [f for f in polygonize(unary_union(list(segs.values()))) if f.area > 1e4]
    if not faces:
        return

    # Prefer the room's own wall set: the face whose boundary is best covered by
    # those walls. Falls back to centroid containment for rooms whose wall list is
    # incomplete (OpenPlan3D's chaining fails on ~0.4% of long merged walls).
    used: set[int] = set()
    for r in rooms:
        if len(r.polygon) >= 3:
            continue
        own = [segs[wid] for wid in r.wall_ids if wid in segs]
        best, best_score = None, -1.0
        for i, f in enumerate(faces):
            if i in used:
                continue
            if own:
                bnd = f.boundary.buffer(30.0)          # 30 mm tolerance
                covered = sum(l.length for l in own if bnd.covers(l))
                score = covered / max(f.boundary.length, 1.0)
            else:
                score = 0.0
            if score > best_score:
                best, best_score = i, score
        if best is not None and best_score > 0.35:
            used.add(best)
            f = faces[best]
            r.polygon = [P(round(x), round(y)) for x, y in f.exterior.coords[:-1]]
            if not r.area:
                r.area = int(round(f.area))

    # Any room still empty: match by area, largest unclaimed face first.
    leftovers = [i for i in range(len(faces)) if i not in used]
    empty = [r for r in rooms if len(r.polygon) < 3]
    for r in empty:
        if not leftovers:
            break
        j = min(leftovers, key=lambda i: abs(faces[i].area - (r.area or 0)))
        leftovers.remove(j)
        f = faces[j]
        r.polygon = [P(round(x), round(y)) for x, y in f.exterior.coords[:-1]]
