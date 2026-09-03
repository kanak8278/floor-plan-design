"""IR <-> OpenPlan3D `Project` JSON adapter.

OpenPlan3D stores geometry in floating-point **centimetres** (settings.ts
gridSize defaults to 25 cm; furniture dims are cm). The canonical IR is integer
millimetres, so cm is a lossy projection and the IR stays authoritative.
"""
from __future__ import annotations
from typing import Any
from .ir import Plan, Wall, Opening, Room, Site, Stair, Furniture, P

MM_PER_CM = 10.0

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


def _pt(p: P) -> dict[str, float]:
    return {"x": p.x / MM_PER_CM, "y": p.y / MM_PER_CM}


def to_project(plan: Plan, name: str | None = None) -> dict[str, Any]:
    floor_id = f"floor-{plan.id}"
    walls = [{
        "id": w.id,
        "start": _pt(w.start), "end": _pt(w.end),
        "thickness": w.thickness / MM_PER_CM,
        "height": w.height / MM_PER_CM,
        "color": "#e5e7eb",
    } for w in plan.walls]

    doors, windows = [], []
    for o in plan.openings:
        if o.kind == "window":
            windows.append({
                "id": o.id, "wallId": o.wall_id, "position": o.position,
                "width": o.width / MM_PER_CM,
                "height": (o.head - o.sill) / MM_PER_CM,
                "sillHeight": o.sill / MM_PER_CM,
                "type": "standard",
            })
        else:
            doors.append({
                "id": o.id, "wallId": o.wall_id, "position": o.position,
                "width": o.width / MM_PER_CM, "height": o.head / MM_PER_CM,
                "type": "single" if o.kind == "door" else "opening",
                "swingDirection": "left", "flipSide": False,
            })

    stairs = [{
        "id": st.id,
        "position": _pt(st.position),
        "rotation": st.rotation,
        "width": st.width / MM_PER_CM,
        "depth": st.depth / MM_PER_CM,
        "riserCount": st.riser_count,
        "direction": st.direction,
        "stairType": st.stair_type,
    } for st in plan.stairs]

    furniture = [{
        "id": f.id,
        "catalogId": f.catalog_id,
        "position": _pt(f.position),
        "rotation": f.rotation,
        "scale": {"x": 1, "y": 1, "z": 1},
        **({"width": f.width / MM_PER_CM} if f.width else {}),
        **({"depth": f.depth / MM_PER_CM} if f.depth else {}),
        **({"height": f.height / MM_PER_CM} if f.height else {}),
        **({"locked": True} if f.locked else {}),
    } for f in plan.furniture]

    rooms = [{
        "id": r.id, "name": r.name, "walls": r.wall_ids,
        "floorTexture": (t.floor_texture if (t := _rt.get(r.category))
                         else FLOOR_TEXTURE.get(r.category, "light-oak")),
        "area": round(r.area / 1_000_000.0, 2),          # m^2, as detectRooms emits
        "roomType": (t.op3d_room_type if (t := _rt.get(r.category))
                     else CATEGORY_TO_ROOMTYPE.get(r.category, "indoor")),
    } for r in plan.rooms]

    return {
        "id": f"proj-{plan.id}",
        "name": name or f"ResPlan {plan.id}",
        "description": f"Converted from {plan.provenance.get('source','?')}",
        "floors": [{
            "id": floor_id, "name": "Ground Floor", "level": 0,
            "walls": walls, "rooms": rooms, "doors": doors, "windows": windows,
            "furniture": furniture, "stairs": stairs, "columns": [],
            "guides": [], "measurements": [], "annotations": [],
            "textAnnotations": [], "groups": [], "entourage": [],
        }],
        "activeFloorId": floor_id,
        "createdAt": "1970-01-01T00:00:00.000Z",
        "updatedAt": "1970-01-01T00:00:00.000Z",
        "_fpeval": {                       # our sidecar: Project has no site concept
            "plot_polygon_cm": [_pt(p) for p in plan.site.plot_polygon],
            "north_deg": plan.site.north_deg,
            "provenance": plan.provenance,
            "room_categories": {r.id: r.category for r in plan.rooms},
            "opening_kinds": {o.id: o.kind for o in plan.openings},
            "opening_heads": {o.id: o.head for o in plan.openings},
            "stair_rooms": {st.id: st.room_id for st in plan.stairs},
            "furniture_rooms": {f.id: f.room_id for f in plan.furniture},
        },
    }


def from_project(proj: dict[str, Any]) -> Plan:
    fl = next((f for f in proj["floors"] if f["id"] == proj.get("activeFloorId")),
              proj["floors"][0])
    side = proj.get("_fpeval", {})
    cats = side.get("room_categories", {})
    kinds = side.get("opening_kinds", {})
    heads = side.get("opening_heads", {})

    walls = [Wall(id=w["id"],
                  start=P(round(w["start"]["x"] * MM_PER_CM), round(w["start"]["y"] * MM_PER_CM)),
                  end=P(round(w["end"]["x"] * MM_PER_CM), round(w["end"]["y"] * MM_PER_CM)),
                  thickness=round(w["thickness"] * MM_PER_CM),
                  height=round(w["height"] * MM_PER_CM)) for w in fl["walls"]]

    openings: list[Opening] = []
    for d in fl["doors"]:
        openings.append(Opening(id=d["id"], kind=kinds.get(d["id"], "door"),
                                wall_id=d["wallId"], position=d["position"],
                                width=round(d["width"] * MM_PER_CM), sill=0,
                                head=heads.get(d["id"], round(d["height"] * MM_PER_CM))))
    for w in fl["windows"]:
        sill = round(w["sillHeight"] * MM_PER_CM)
        openings.append(Opening(id=w["id"], kind="window", wall_id=w["wallId"],
                                position=w["position"], width=round(w["width"] * MM_PER_CM),
                                sill=sill, head=sill + round(w["height"] * MM_PER_CM)))
    openings.sort(key=lambda o: int(o.id[1:]))

    rooms = [Room(id=r["id"], name=r["name"], category=cats.get(r["id"], "indoor"),
                  wall_ids=list(r["walls"]),
                  polygon=[], area=int(round(r["area"] * 1_000_000)))
             for r in fl["rooms"]]

    st_rooms = side.get("stair_rooms", {})
    stairs = [Stair(id=st["id"],
                    position=P(round(st["position"]["x"] * MM_PER_CM),
                               round(st["position"]["y"] * MM_PER_CM)),
                    rotation=st.get("rotation", 0.0),
                    width=round(st["width"] * MM_PER_CM),
                    depth=round(st["depth"] * MM_PER_CM),
                    riser_count=st.get("riserCount", 14),
                    direction=st.get("direction", "up"),
                    stair_type=st.get("stairType", "straight"),
                    room_id=st_rooms.get(st["id"]))
              for st in fl.get("stairs", [])]

    f_rooms = side.get("furniture_rooms", {})
    furniture = [Furniture(id=f["id"], catalog_id=f["catalogId"],
                           position=P(round(f["position"]["x"] * MM_PER_CM),
                                      round(f["position"]["y"] * MM_PER_CM)),
                           rotation=f.get("rotation", 0.0),
                           width=round(f["width"] * MM_PER_CM) if f.get("width") else 0,
                           depth=round(f["depth"] * MM_PER_CM) if f.get("depth") else 0,
                           height=round(f["height"] * MM_PER_CM) if f.get("height") else 0,
                           room_id=f_rooms.get(f["id"]),
                           locked=bool(f.get("locked")))
                 for f in fl.get("furniture", [])]

    plot = [P(round(p["x"] * MM_PER_CM), round(p["y"] * MM_PER_CM))
            for p in side.get("plot_polygon_cm", [])]

    return Plan(id=str(proj["id"]).replace("proj-", ""), walls=walls, openings=openings,
                rooms=rooms, stairs=stairs, furniture=furniture, site=Site(plot_polygon=plot,
                                       north_deg=side.get("north_deg", 0.0)),
                provenance=side.get("provenance", {}))
