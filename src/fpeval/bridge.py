"""DesignSpec / suite Truth -> solver programme.

The missing adapter. `spec.py` speaks 27 fine-grained categories (it distinguishes
`toilet` from `powder` from `handwash` because builder drawings do); `roomtypes.py`
and the solver speak 18 canonical ones. Mapping many->one here keeps the extra
detail available to the renderer and the area statement without forcing the solver
to reason about it.
"""
from __future__ import annotations
from typing import Any

from . import roomtypes as rt
from .envelope import RoomReq

# spec.py category -> canonical roomtypes key.
SPEC_TO_CANON: dict[str, str] = {
    "living": "living", "dining": "dining", "kitchen": "kitchen",
    "bedroom": "bedroom", "master_bedroom": "master_bedroom",
    "guest_bedroom": "bedroom",
    # A servant room is a bedroom-class space for layout purposes: it needs a
    # window, a door of its own, and habitable minima.
    "servant": "bedroom",
    "study": "study", "office": "study",
    "bathroom": "bathroom", "toilet": "bathroom", "powder": "bathroom",
    "handwash": "bathroom",
    "utility": "utility", "pooja": "pooja",
    "store": "store", "dress": "store",
    "staircase": "stair", "foyer": "foyer", "corridor": "foyer",
    "shaft": "shaft", "balcony": "balcony", "sit_out": "sitout",
    "terrace": "patio", "patio": "patio",
    "parking": "parking", "garage": "parking",
}

SQFT_M2 = 10.7639


def canon(category: str) -> str:
    if category in rt.T:
        return category
    return SPEC_TO_CANON.get(category, "unknown")


def _target_m2(key: str, given: float | None) -> float:
    if given:
        return given
    t = rt.get(key)
    if t and t.target_m2:
        lo, hi = t.target_m2
        return round((lo + hi) / 2.0, 1)
    return 9.0


def spec_to_programme(spec: Any) -> tuple[list[RoomReq], list[str]]:
    """DesignSpec -> solver programme. Returns (programme, warnings).

    Rooms the solver cannot lay out are dropped with a warning rather than
    silently: `landscape` has no walls, `shaft` is sub-minimum, and outdoor
    spaces on a rectangular footprint would eat the envelope.
    """
    prog: list[RoomReq] = []
    warn: list[str] = []
    entrance_set = False
    for r in getattr(spec, "rooms", []) or []:
        key = canon(getattr(r, "category", ""))
        if key == "unknown":
            warn.append(f"dropped room '{getattr(r, 'id', '?')}': "
                        f"unmapped category '{getattr(r, 'category', '')}'")
            continue
        if key in ("landscape", "shaft", "parking", "patio", "sitout", "balcony"):
            warn.append(f"deferred '{getattr(r, 'id', '?')}' ({key}): "
                        "outdoor/service space, not laid out by the rectangular solver")
            continue
        t = rt.get(key)
        given = getattr(r, "min_sqft", None)
        target = _target_m2(key, (given / SQFT_M2) if given else None)
        zone = getattr(r, "preferred_zone", None) or (t.vastu_zone if t else None)
        is_entrance = not entrance_set and key == "living"
        entrance_set = entrance_set or is_entrance
        prog.append(RoomReq(
            id=getattr(r, "id", key), name=getattr(r, "name", "") or (t.display if t else key),
            category=key, target_m2=target,
            weight=1.0 if getattr(r, "priority", 3) <= 2 else 0.7,
            vastu_zone=zone, is_entrance=is_entrance,
        ))
    return prog, warn


def truth_to_programme(truth: Any) -> tuple[list[RoomReq], list[str]]:
    """Suite ground truth -> solver programme, with no LLM in the path.

    Exists so engine failures can be measured separately from extraction
    failures. Running only the end-to-end pipeline conflates the two.
    """
    prog: list[RoomReq] = []
    warn: list[str] = []
    counts: dict[str, int] = {}
    for src in (getattr(truth, "rooms", {}) or {}, getattr(truth, "rooms_min", {}) or {}):
        for key, n in src.items():
            counts[key] = max(counts.get(key, 0), int(n))
    counts.setdefault("living", 1)
    counts.setdefault("kitchen", 1)
    if counts.get("bedroom") and not counts.get("bathroom"):
        counts["bathroom"] = max(1, counts["bedroom"] // 2)

    for key, n in counts.items():
        t = rt.get(key)
        if not t:
            warn.append(f"unknown room type '{key}'")
            continue
        if key in ("landscape", "shaft", "parking", "patio", "sitout", "balcony", "stair"):
            warn.append(f"deferred '{key}': not laid out by the rectangular solver")
            continue
        for i in range(n):
            rid = key if n == 1 else f"{key}{i+1}"
            zone = (getattr(truth, "vastu_zones", {}) or {}).get(key) or t.vastu_zone
            # The first bedroom is the master: bigger, and SW under Vastu.
            if key == "bedroom" and i == 0:
                target = _target_m2("master_bedroom", None)
                zone = (getattr(truth, "vastu_zones", {}) or {}).get("master_bedroom") or "SW"
            else:
                target = _target_m2(key, None)
            prog.append(RoomReq(id=rid, name=f"{t.display} {i+1}" if n > 1 else t.display,
                                category=key, target_m2=target, weight=1.0,
                                vastu_zone=zone, is_entrance=(key == "living" and i == 0)))
    return prog, warn
