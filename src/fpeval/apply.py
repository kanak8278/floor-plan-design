"""Apply agent ops to a plan — the half that was missing.

`llm.py` declared 25 ops; only the 11 spec-level ones had an applier, so the 6
furniture ops and 8 geometry ops were parsed, validated, described to the user
and then silently dropped. An op the agent can emit but the system cannot
execute is worse than no op: the model reports success and nothing changes.

This module applies the FURNITURE ops by reusing `furnish.py`'s placement
machinery, so a symbolic anchor from the agent is resolved against the real room
polygon, door swings and clearances — the same path the rule-based furnisher
takes. Coordinates are still never authored by the model.

Geometry ops remain unapplied and are returned as rejected with a reason. They
need an IR mutation layer mirroring OpenPlan3D's `project.ts`, which does not
exist yet; saying so beats pretending.
"""
from __future__ import annotations
import copy
from dataclasses import dataclass, field
from typing import Any

from .ir import Furniture, P, Plan


@dataclass
class ApplyResult:
    plan: Plan
    applied: list[str] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)
    unsupported: list[tuple[str, str]] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.applied)


def _room_by_id_or_cat(plan: Plan, key: str):
    for r in plan.rooms:
        if r.id == key:
            return r
    for r in plan.rooms:
        if (r.category or "") == key:
            return r
    # "hall" is what a client calls the living room
    alias = {"hall": "living", "master": "master_bedroom"}.get(key)
    if alias:
        for r in plan.rooms:
            if (r.category or "") == alias:
                return r
    return None


def _as_placed(F, plan: Plan, room_id: str) -> list:
    """Existing furniture in a room, as obstacles the placer understands."""
    out = []
    for f in plan.furniture:
        if f.room_id != room_id or f.height == 0:
            continue          # 2D symbols are annotations, not obstacles
        w, d, h = (f.width or 600), (f.depth or 600), (f.height or 600)
        fp = F.footprint(f.position.x, f.position.y, w, d, f.rotation)
        out.append(F._Placed(spec_key=f.catalog_id, catalog_id=f.catalog_id,
                             cx=f.position.x, cy=f.position.y, rot=f.rotation,
                             w=w, d=d, h=h, fp=fp,
                             pad=fp.buffer(F.ITEM_GAP_MM, join_style=2,
                                           mitre_limit=2.0),
                             clearance=None, score=0.0, why="existing"))
    return out


def _spec_for(F, params: dict[str, Any]):
    """Agent params -> a furnish Spec. Only symbolic fields are accepted; the
    coordinate ban in `llm.py` already rejects the rest."""
    d: dict[str, Any] = {
        "key": str(params.get("item")),
        "item": str(params.get("item")),
        "anchor": str(params.get("anchor", "wall")),
    }
    for src, dst in (("of", "of"), ("side", "side"), ("count", "count"),
                     ("align", "align"), ("clear_front_mm", "clear_front"),
                     ("gap_mm", "gap"), ("abut", "abut"),
                     ("avoid_window", "avoid_window")):
        if params.get(src) is not None:
            d[dst] = params[src]
    for src, dst in (("prefer", "prefer"), ("avoid", "avoid")):
        v = params.get(src)
        if v:
            d[dst] = tuple(v) if isinstance(v, (list, tuple)) else (v,)
    return F._spec_from_dict(d)


def apply_furniture_ops(plan: Plan, ops: list[Any]) -> ApplyResult:
    """Apply the furniture ops. Returns a NEW plan; the input is untouched."""
    from . import furnish as F

    out = copy.deepcopy(plan)
    res = ApplyResult(plan=out)
    mass = F.wall_mass(out)
    walls = {w.id: w for w in out.walls}
    nid = [len(out.furniture)]

    def _emit(room, placed) -> Furniture:
        nid[0] += 1
        return Furniture(id=f"ag{nid[0]}", catalog_id=placed.catalog_id,
                         position=P(round(placed.cx), round(placed.cy)),
                         rotation=round(placed.rot, 3), width=int(placed.w),
                         depth=int(placed.d), height=int(placed.h),
                         room_id=room.id)

    def _place(room, params, desc) -> bool:
        ctx = F.build_ctx(out, room, mass, walls)
        if ctx is None:
            res.rejected.append((desc, f"{room.name} has no usable interior"))
            return False
        try:
            spec = _spec_for(F, params)
        except Exception as e:
            res.rejected.append((desc, f"bad params: {type(e).__name__}: {e}"))
            return False
        placed, why = F._try_place(ctx, spec, _as_placed(F, out, room.id))
        if placed is None:
            res.rejected.append((desc, f"nowhere it fits: {why}"))
            return False
        out.furniture.append(_emit(room, placed))
        return True

    for op in ops:
        name = getattr(op, "op", "")
        p = getattr(op, "params", {}) or {}
        desc = getattr(op, "description", "") or name

        if name == "place_item":
            room = _room_by_id_or_cat(out, str(p.get("room_id", "")))
            if room is None:
                res.rejected.append((desc, f"no room {p.get('room_id')!r}")); continue
            n = int(p.get("count") or 1)
            ok = sum(1 for _ in range(n) if _place(room, p, desc))
            if ok:
                res.applied.append(f"{desc} ({ok} placed in {room.name})")

        elif name == "remove_item":
            iid = str(p.get("item_id", ""))
            before = len(out.furniture)
            out.furniture = [f for f in out.furniture
                             if f.id != iid and f.catalog_id != iid]
            gone = before - len(out.furniture)
            (res.applied if gone else res.rejected).append(
                f"{desc} ({gone} removed)" if gone else (desc, f"no item {iid!r}"))

        elif name in ("move_item", "replace_item"):
            iid = str(p.get("item_id", ""))
            item = next((f for f in out.furniture
                         if f.id == iid or f.catalog_id == iid), None)
            if item is None:
                res.rejected.append((desc, f"no item {iid!r}")); continue
            room = next((r for r in out.rooms if r.id == item.room_id), None)
            if room is None:
                res.rejected.append((desc, "the item is not in a room")); continue
            out.furniture = [f for f in out.furniture if f is not item]
            np = dict(p)
            np["item"] = p.get("item") or item.catalog_id
            np.setdefault("anchor", "wall")
            if _place(room, np, desc):
                res.applied.append(f"{desc} (re-placed in {room.name})")
            else:
                out.furniture.append(item)          # put it back, unchanged

        elif name == "furnish_room":
            room = _room_by_id_or_cat(out, str(p.get("room_id", "")))
            if room is None:
                res.rejected.append((desc, f"no room {p.get('room_id')!r}")); continue
            pol: dict[str, Any] = {}
            if p.get("density"):
                pol["density"] = p["density"]
            for k in ("add", "drop", "swap"):
                if p.get(k):
                    pol[k] = p[k]
            try:
                fresh, rep = F.furnish(out, policy=pol or None, seed=0)
            except Exception as e:
                res.rejected.append((desc, f"{type(e).__name__}: {e}")); continue
            keep = [f for f in out.furniture if f.room_id != room.id]
            new = [f for f in fresh.furniture if f.room_id == room.id]
            out.furniture = keep + new
            res.applied.append(f"{desc} ({len(new)} items in {room.name})")

        elif name == "set_kitchen_layout":
            room = _room_by_id_or_cat(out, str(p.get("room_id", "kitchen")))
            if room is None:
                res.rejected.append((desc, "no kitchen")); continue
            pol = {"kitchen": {k: v for k, v in (
                ("counter_run", p.get("run")), ("hob_zone", p.get("hob_zone")),
                ("sink_zone", p.get("sink_zone")),
                ("fridge_zone", p.get("fridge_zone")),
                ("breakfast_counter", p.get("breakfast_counter"))) if v is not None}}
            try:
                fresh, rep = F.furnish(out, policy=pol, seed=0)
            except Exception as e:
                res.rejected.append((desc, f"{type(e).__name__}: {e}")); continue
            keep = [f for f in out.furniture if f.room_id != room.id]
            new = [f for f in fresh.furniture if f.room_id == room.id]
            out.furniture = keep + new
            res.applied.append(f"{desc} ({len(new)} items; run={p.get('run')}, "
                               f"hob={p.get('hob_zone')}, sink={p.get('sink_zone')})")

        elif name in ("update_wall", "move_wall_parallel", "split_wall",
                      "add_wall", "add_door", "update_door", "update_room",
                      "remove_element"):
            res.unsupported.append(
                (name, "geometry ops need an IR mutation layer mirroring "
                       "OpenPlan3D's project.ts; not built yet"))
        else:
            res.rejected.append((desc, f"not a furniture op: {name!r}"))
    return res
