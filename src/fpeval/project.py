"""IR <-> OpenPlan3D `Project` JSON adapter.

OpenPlan3D stores geometry in floating-point **centimetres** (settings.ts
gridSize defaults to 25 cm; furniture dims are cm). The canonical IR is integer
millimetres, so cm is a lossy projection and the IR stays authoritative.
"""
from __future__ import annotations
from typing import Any
from .ir import Plan, Wall, Opening, Room, Site, P

MM_PER_CM = 10.0

CATEGORY_TO_ROOMTYPE = {
    "balcony": "outdoor", "storage": "utility",
    "living": "indoor", "kitchen": "indoor", "bedroom": "indoor", "bathroom": "indoor",
}
FLOOR_TEXTURE = {
    "bathroom": "ceramic-white", "kitchen": "ceramic-gray", "balcony": "slate",
    "living": "light-oak", "bedroom": "light-oak", "storage": "concrete",
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

    rooms = [{
        "id": r.id, "name": r.name, "walls": r.wall_ids,
        "floorTexture": FLOOR_TEXTURE.get(r.category, "light-oak"),
        "area": round(r.area / 1_000_000.0, 2),          # m^2, as detectRooms emits
        "roomType": CATEGORY_TO_ROOMTYPE.get(r.category, "indoor"),
    } for r in plan.rooms]

    return {
        "id": f"proj-{plan.id}",
        "name": name or f"ResPlan {plan.id}",
        "description": f"Converted from {plan.provenance.get('source','?')}",
        "floors": [{
            "id": floor_id, "name": "Ground Floor", "level": 0,
            "walls": walls, "rooms": rooms, "doors": doors, "windows": windows,
            "furniture": [], "stairs": [], "columns": [],
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

    plot = [P(round(p["x"] * MM_PER_CM), round(p["y"] * MM_PER_CM))
            for p in side.get("plot_polygon_cm", [])]

    return Plan(id=str(proj["id"]).replace("proj-", ""), walls=walls, openings=openings,
                rooms=rooms, site=Site(plot_polygon=plot,
                                       north_deg=side.get("north_deg", 0.0)),
                provenance=side.get("provenance", {}))
