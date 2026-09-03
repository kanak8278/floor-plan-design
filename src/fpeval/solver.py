"""Slicing-tree topology + CP-SAT dimensioning -> canonical IR `Plan`.

Why this shape:
  * A **slicing tree** (recursive KD subdivision) makes a valid rectilinear
    tiling *by construction* — no overlaps, no gaps, no slivers. Free-floating
    rectangles would need O(n^2) no-overlap disjunctions and would still leave
    voids, which is exactly what breaks face recovery.
  * **CP-SAT dimensions the cuts**, not the topology. Cut positions are integer
    variables, so wall coordinates are exact and joins never drift. That is the
    reason the IR is integer millimetres in the first place.
  * The LLM never sees a coordinate: it supplies a programme, adjacency wishes
    and orientation preferences; this module owns all metric geometry.

Solving happens on a **10 mm grid** (`GRID_MM`). Room area is a product of two
variables and `AddMultiplicationEquality` propagates far better on domains of
~1500 than on ~15000; 10 mm is finer than any buildable tolerance and the
emitted IR is still exact integer millimetres.

Room rectangles are **centreline-to-centreline** (matching `resplan.py`, so
`metrics.face_recovery` and `project.to_project` work unchanged). NBC minimums
are *clear* dimensions, so a wall allowance is subtracted before applying them.
"""
from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field
from typing import Any, Sequence

from ortools.sat.python import cp_model

from .envelope import (HABITABLE, WET, AreaStatement, BylawProfile, RoomReq,
                       compute_envelope, mm2_to_m2, zone_vector)
from .ir import Opening, P, Plan, Room, Site, Wall

GRID_MM = 10                      # solver granularity; IR stays exact mm

# Objective scales chosen so that weight 1.0 on each term makes "1 m^2 of area
# error", "1 m of excess elongation" and "1 m of Vastu drift" cost roughly the
# same. Tune the weights in LayoutSpec, not these.
AREA_SCALE = 1                    # dev is already grid^2 (1 grid^2 = 1e-4 m^2)
ASPECT_SCALE = 10                 # excess is 10x a length in grid units
VASTU_SCALE = 50                  # applied to (x0+x1), i.e. 2x centroid

DOOR_W = 900                      # interior door leaf, mm
FRONT_DOOR_W = 1050
BATH_DOOR_W = 750
JAMB = 100                        # clear either side of an opening in its wall
WIN_SILL, WIN_HEAD = 900, 2100
DOOR_HEAD = 2100
WALL_ID_MIN_OVERLAP = 100.0       # mm of shared centreline to count as bounding

# Pairs NBC forbids outright. These were previously priced at penalty 9, which
# Prim happily paid whenever a bathroom had no other available adjacency -- so the
# solver emitted a door the validator then flagged, on 3 of 89 suite examples.
# A code prohibition is not a cost: exclude the edge, and if that strands a room
# the TOPOLOGY is wrong, so try the next one.
NBC_FORBIDDEN = {frozenset(("kitchen", "bathroom"))}

# Door-graph edge penalties. Prim minimises these, which yields a hub-and-spoke
# plan through the living room / hall instead of a chain of bedrooms opening
# into each other.
_EDGE_PEN = {
    frozenset(("living", "living")): 4, frozenset(("living", "bedroom")): 1,
    frozenset(("living", "kitchen")): 1, frozenset(("living", "bathroom")): 3,
    frozenset(("living", "dining")): 0, frozenset(("living", "pooja")): 0,
    frozenset(("living", "passage")): 0,
    frozenset(("passage", "bedroom")): 0, frozenset(("passage", "bathroom")): 0,
    frozenset(("passage", "kitchen")): 0, frozenset(("passage", "dining")): 0,
    frozenset(("passage", "pooja")): 0, frozenset(("passage", "utility")): 0,
    frozenset(("passage", "storage")): 0, frozenset(("passage", "passage")): 1,
    frozenset(("dining", "kitchen")): 0, frozenset(("dining", "bedroom")): 2,
    frozenset(("bedroom", "bathroom")): 1, frozenset(("bedroom", "bedroom")): 6,
    frozenset(("kitchen", "utility")): 0, frozenset(("kitchen", "bathroom")): 9,
    frozenset(("kitchen", "bedroom")): 7,
    frozenset(("bathroom", "bathroom")): 9,
}
_EDGE_PEN_DEFAULT = 3

CONSTRAINT_GROUPS = ("min_area", "min_clear_width", "max_aspect_hard",
                     "door_width", "pinned")


# ---------------------------------------------------------------- spec / result

@dataclass
class LayoutSpec:
    """Everything the LLM is allowed to say about a layout."""
    programme: list[RoomReq]
    required_adjacency: list[tuple[str, str]] = field(default_factory=list)
    forbidden_adjacency: list[tuple[str, str]] = field(default_factory=list)
    entrance_room: str | None = None          # default: RoomReq.is_entrance
    # objective weights
    w_area: float = 1.0
    w_aspect: float = 0.35
    w_vastu: float = 0.35
    # limits
    max_aspect_hard: float = 4.0
    time_limit_s: float = 8.0
    candidates: int = 4                       # *feasible* topologies to collect
    max_topologies: int = 24                  # ranked topologies CP-SAT may try
    tree_samples: int = 400                   # topologies scored cheaply first
    seed: int = 0
    workers: int = 8
    filler_min_m2: float = 3.0                # slack above this becomes a hall

    def entrance(self) -> str:
        if self.entrance_room:
            return self.entrance_room
        e = next((r.id for r in self.programme if r.is_entrance), None)
        return e or self.programme[0].id


@dataclass
class SolveResult:
    status: str            # OPTIMAL | FEASIBLE | INFEASIBLE | TIMEOUT
    plan: Plan | None
    statement: AreaStatement
    message: str
    solve_time_s: float
    total_time_s: float
    objective: float | None = None
    area_m2: dict[str, float] = field(default_factory=dict)
    target_m2: dict[str, float] = field(default_factory=dict)
    area_dev_m2: dict[str, float] = field(default_factory=dict)
    area_dev_pct: dict[str, float] = field(default_factory=dict)
    clear_wh_mm: dict[str, tuple[int, int]] = field(default_factory=dict)
    unmet_adjacency: list[tuple[str, str]] = field(default_factory=list)
    unreachable: list[str] = field(default_factory=list)
    rooms_without_window: list[str] = field(default_factory=list)
    nbc_violations: list[str] = field(default_factory=list)
    infeasible_groups: list[str] = field(default_factory=list)
    topology: dict | None = None
    pinned_ok: bool = True
    candidates_tried: int = 0
    topology_exhausted: bool = False

    @property
    def ok(self) -> bool:
        return self.plan is not None

    @property
    def max_abs_dev_pct(self) -> float:
        return max((abs(v) for v in self.area_dev_pct.values()), default=0.0)

    @property
    def mean_abs_dev_pct(self) -> float:
        vs = [abs(v) for v in self.area_dev_pct.values()]
        return sum(vs) / len(vs) if vs else 0.0

    def summary(self) -> str:
        lines = [f"{self.status}: {self.message}",
                 f"  solve {self.solve_time_s:.2f}s / total "
                 f"{self.total_time_s:.2f}s  candidates={self.candidates_tried}"]
        if self.plan:
            lines.append(f"  walls={len(self.plan.walls)} "
                         f"rooms={len(self.plan.rooms)} "
                         f"openings={len(self.plan.openings)} "
                         f"obj={self.objective:.0f}")
            for rid in self.area_m2:
                w, h = self.clear_wh_mm[rid]
                lines.append(f"    {rid:<10} {self.area_m2[rid]:>6.2f} m2 "
                             f"clear {w:>5}x{h:<5} mm  "
                             f"(target {self.target_m2[rid]:>6.2f}, "
                             f"{self.area_dev_pct[rid]:+6.1f}%)")
        for label, xs in (("unmet adjacency", self.unmet_adjacency),
                          ("unreachable", self.unreachable),
                          ("no window", self.rooms_without_window),
                          ("NBC violations", self.nbc_violations),
                          ("infeasible groups", self.infeasible_groups)):
            if xs:
                lines.append(f"  {label}: {xs}")
        return "\n".join(lines)


# ---------------------------------------------------------------- slicing tree

@dataclass
class _Node:
    axis: str | None            # 'x' | 'y' for a cut, None for a leaf
    room: int | None
    kids: tuple[int, int] | None
    cut: str | None


def _tree_to_dict(nodes: list[_Node], root: int, order: list[int],
                  room_ids: list[str]) -> dict:
    return {"nodes": [{"axis": n.axis, "room": n.room,
                       "kids": list(n.kids) if n.kids else None,
                       "cut": n.cut} for n in nodes],
            "root": root, "order": list(order), "room_ids": list(room_ids)}


def _tree_from_dict(d: dict) -> tuple[list[_Node], int, list[int]]:
    nodes = [_Node(n["axis"], n["room"],
                   tuple(n["kids"]) if n["kids"] else None, n["cut"])
             for n in d["nodes"]]
    return nodes, int(d.get("root", 0)), list(d["order"])


def _build_tree(order: Sequence[int], w: float, h: float,
                weights: Sequence[float],
                rng: random.Random | None) -> tuple[list[_Node], int]:
    """Cut the longer side; split the room *order* where subtree weight halves.

    Splitting the ordered list (rather than an arbitrary bipartition) is what
    keeps rooms that must be adjacent next to each other, since `_base_order`
    lays the requested-adjacency graph out in BFS order.
    """
    nodes: list[Any] = []
    root = _rec(list(order), w, h, weights, rng, nodes)
    return nodes, root


def _rec(order: list[int], w: float, h: float, weights: Sequence[float],
         rng: random.Random | None, nodes: list) -> int:
    if len(order) == 1:
        nodes.append(_Node(None, order[0], None, None))
        return len(nodes) - 1
    axis = "x" if w >= h else "y"
    if rng is not None and abs(w - h) / max(w, h) < 0.25 and rng.random() < 0.4:
        axis = "y" if axis == "x" else "x"       # near-square: either is fine
    tot = sum(weights[i] for i in order)
    best_k, best_d, cum = 1, float("inf"), 0.0
    for k in range(1, len(order)):
        cum += weights[order[k - 1]]
        d = abs(cum / tot - 0.5)
        if d < best_d:
            best_d, best_k = d, k
    k = best_k
    if rng is not None and len(order) > 2:
        k = min(len(order) - 1, max(1, k + rng.choice((-1, 0, 0, 0, 1))))
    left, right = order[:k], order[k:]
    fl = sum(weights[i] for i in left) / tot
    me = len(nodes)
    nodes.append(None)
    if axis == "x":
        a = _rec(left, w * fl, h, weights, rng, nodes)
        b = _rec(right, w * (1 - fl), h, weights, rng, nodes)
    else:
        a = _rec(left, w, h * fl, weights, rng, nodes)
        b = _rec(right, w, h * (1 - fl), weights, rng, nodes)
    nodes[me] = _Node(axis, None, (a, b), f"c{me}")
    return me


def _subtree_weight(nodes: list[_Node], i: int, weights: Sequence[float],
                    memo: dict[int, float]) -> float:
    if i in memo:
        return memo[i]
    n = nodes[i]
    v = (weights[n.room] if n.room is not None
         else (_subtree_weight(nodes, n.kids[0], weights, memo)
               + _subtree_weight(nodes, n.kids[1], weights, memo)))
    memo[i] = v
    return v


def _label_rects(nodes: list[_Node], root: int
                 ) -> tuple[dict[int, tuple[str, str, str, str]], dict[str, str]]:
    """Each leaf's rectangle as four *symbolic* bounds, plus each cut's axis.

    Symbolic bounds are the whole trick behind structural adjacency: two leaves
    share a wall iff one's upper bound is literally the other's lower bound, and
    that is a string comparison, no geometry needed.
    """
    rects: dict[int, tuple[str, str, str, str]] = {}
    axes: dict[str, str] = {}

    def walk(i: int, x0: str, y0: str, x1: str, y1: str) -> None:
        n = nodes[i]
        if n.room is not None:
            rects[n.room] = (x0, y0, x1, y1)
            return
        a, b = n.kids
        axes[n.cut] = n.axis
        if n.axis == "x":
            walk(a, x0, y0, n.cut, y1)
            walk(b, n.cut, y0, x1, y1)
        else:
            walk(a, x0, y0, x1, n.cut)
            walk(b, x0, n.cut, x1, y1)

    walk(root, "X0", "Y0", "X1", "Y1")
    return rects, axes


def _nominal(nodes: list[_Node], root: int,
             rect: tuple[float, float, float, float],
             weights: Sequence[float]
             ) -> tuple[dict[int, tuple[float, float, float, float]],
                        dict[str, float]]:
    """Proportional-split layout: candidate scoring, CP-SAT hints, door tree."""
    out: dict[int, tuple[float, float, float, float]] = {}
    cutv: dict[str, float] = {}
    memo: dict[int, float] = {}

    def walk(i, x0, y0, x1, y1):
        n = nodes[i]
        if n.room is not None:
            out[n.room] = (x0, y0, x1, y1)
            return
        a, b = n.kids
        f = (_subtree_weight(nodes, a, weights, memo)
             / _subtree_weight(nodes, i, weights, memo))
        if n.axis == "x":
            xm = x0 + (x1 - x0) * f
            cutv[n.cut] = xm
            walk(a, x0, y0, xm, y1)
            walk(b, xm, y0, x1, y1)
        else:
            ym = y0 + (y1 - y0) * f
            cutv[n.cut] = ym
            walk(a, x0, y0, x1, ym)
            walk(b, x0, ym, x1, y1)

    walk(root, *rect)
    return out, cutv


def _structural_pairs(labels: dict[int, tuple[str, str, str, str]]
                      ) -> dict[tuple[int, int], str]:
    out: dict[tuple[int, int], str] = {}
    ks = sorted(labels)
    for a in range(len(ks)):
        for b in range(a + 1, len(ks)):
            i, j = ks[a], ks[b]
            ri, rj = labels[i], labels[j]
            if ri[2] == rj[0] or rj[2] == ri[0]:
                out[(i, j)] = "x"
            elif ri[3] == rj[1] or rj[3] == ri[1]:
                out[(i, j)] = "y"
    return out


# ---------------------------------------------------------------- geometry utils

def _touching(a, b, need: float) -> tuple[str, float, float, float] | None:
    """Shared edge between two rects -> (axis, coord, lo, hi), else None."""
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    if abs(ax1 - bx0) < 1e-6 or abs(bx1 - ax0) < 1e-6:
        lo, hi = max(ay0, by0), min(ay1, by1)
        if hi - lo >= need:
            return ("x", ax1 if abs(ax1 - bx0) < 1e-6 else ax0, lo, hi)
    if abs(ay1 - by0) < 1e-6 or abs(by1 - ay0) < 1e-6:
        lo, hi = max(ax0, bx0), min(ax1, bx1)
        if hi - lo >= need:
            return ("y", ay1 if abs(ay1 - by0) < 1e-6 else ay0, lo, hi)
    return None


def _door_width(a: RoomReq, b: RoomReq) -> int:
    if {"bathroom", "wc"} & {a.category, b.category}:
        return BATH_DOOR_W
    return DOOR_W


def _g(mm: float, up: bool = True) -> int:
    return int(math.ceil(mm / GRID_MM)) if up else int(math.floor(mm / GRID_MM))


# ---------------------------------------------------------------- candidate score

def _score_nominal(rects, reqs: Sequence[RoomReq], targets: Sequence[float],
                   rect: tuple[float, float, float, float], alw: int, ent: int,
                   north_deg: float, spec: LayoutSpec,
                   req_adj: list[tuple[int, int]]) -> float:
    """Cheap surrogate for the CP-SAT objective, used to prefilter topologies.

    Worth the 400 samples: CP-SAT on a hopeless topology burns the whole time
    budget proving infeasibility.
    """
    X0, Y0, X1, Y1 = rect
    pen = 0.0
    for i, r in enumerate(reqs):
        x0, y0, x1, y1 = rects[i]
        cw, ch = x1 - x0 - alw, y1 - y0 - alw
        if cw <= 0 or ch <= 0:
            return 1e12
        mw = r.nbc_min_width()
        pen += 400.0 * (max(0.0, mw - cw) + max(0.0, mw - ch))
        area = cw * ch / 1e6
        pen += 900.0 * max(0.0, r.nbc_min_area_m2() - area)
        pen += 40.0 * abs(area - targets[i])
        ar = max(cw, ch) / max(1.0, min(cw, ch))
        pen += 300.0 * max(0.0, ar - r.max_aspect)
        pen += 3000.0 * max(0.0, ar - spec.max_aspect_hard)
        if not (x0 <= X0 + 1 or x1 >= X1 - 1 or y0 <= Y0 + 1 or y1 >= Y1 - 1):
            # No exterior edge means no window. For a habitable room that is an
            # NBC ventilation failure, not a preference, so it is priced high.
            pen += (6000.0 if r.category in HABITABLE
                    else 1500.0 if r.category in WET else 0.0)
        z = r.zone()
        if z and spec.w_vastu > 0:
            # Scaled to match the CP-SAT objective: there, 1 m^2 of area error
            # costs 10000 and 1 m of centroid shift costs w_vastu*10000*weight;
            # here 1 m^2 costs 40, so 1 m of shift must cost w_vastu*40*weight.
            # Get this wrong and topology selection is Vastu-blind, which is
            # where nearly all the real leverage lives — inside a fixed
            # topology a room can only move by resizing.
            vx, vy = zone_vector(z, north_deg)
            dx = (x0 + x1) / 2 - (X0 + X1) / 2
            dy = (y0 + y1) / 2 - (Y0 + Y1) / 2
            pen -= (spec.w_vastu * 40.0 * max(r.weight, 0.2)
                    * (vx * dx + vy * dy) / 1000.0)
    if rects[ent][1] > Y0 + 1:
        pen += 6000.0                # entrance must reach the road edge
    for i, j in req_adj:
        if _touching(rects[i], rects[j], DOOR_W + 2 * JAMB) is None:
            pen += 2500.0
    return pen


# ---------------------------------------------------------------- CP-SAT model

@dataclass
class _M:
    m: cp_model.CpModel
    bounds: dict[str, Any]                       # symbol -> IntVar
    cuts: dict[str, Any]
    cut_axis: dict[str, str]
    rects: dict[int, tuple[Any, Any, Any, Any]]
    cw: dict[int, Any]
    ch: dict[int, Any]
    carea: dict[int, Any]
    groups: dict[str, list[Any]]


def _build_model(nodes: list[_Node], root: int, reqs: Sequence[RoomReq],
                 targets: Sequence[float], rect_mm: tuple[int, int, int, int],
                 alw_mm: int, spec: LayoutSpec, north_deg: float,
                 doors: list[tuple[int, int, str]],
                 nominal_cuts: dict[str, float],
                 pinned_cuts: dict[str, int], gated: bool) -> _M:
    m = cp_model.CpModel()
    X0, Y0 = _g(rect_mm[0], False), _g(rect_mm[1], False)
    X1, Y1 = _g(rect_mm[2], False), _g(rect_mm[3], False)
    W, H = X1 - X0, Y1 - Y0
    alw = _g(alw_mm)

    labels, cut_axis = _label_rects(nodes, root)
    bounds: dict[str, Any] = {"X0": m.NewConstant(X0), "X1": m.NewConstant(X1),
                              "Y0": m.NewConstant(Y0), "Y1": m.NewConstant(Y1)}
    cuts: dict[str, Any] = {}
    for cid, ax in cut_axis.items():
        lo, hi = (X0, X1) if ax == "x" else (Y0, Y1)
        v = m.NewIntVar(lo, hi, cid)
        cuts[cid] = v
        bounds[cid] = v
    rects = {i: tuple(bounds[s] for s in labels[i]) for i in labels}

    groups: dict[str, list[Any]] = {k: [] for k in CONSTRAINT_GROUPS}

    def gate(group: str):
        """Constraint-group literal, so an INFEASIBLE model can name the set
        that broke instead of just saying 'no'."""
        if not gated:
            return None
        v = m.NewBoolVar(f"g_{group}_{len(groups[group])}")
        groups[group].append(v)
        return v

    def add(group: str, *ctrs):
        lit = gate(group)
        for c in ctrs:
            if lit is not None:
                c.OnlyEnforceIf(lit)

    obj: list[Any] = []
    cwv: dict[int, Any] = {}
    chv: dict[int, Any] = {}
    areav: dict[int, Any] = {}

    for i, r in enumerate(reqs):
        x0, y0, x1, y1 = rects[i]
        cw = m.NewIntVar(1, W, f"cw{i}")
        ch = m.NewIntVar(1, H, f"ch{i}")
        m.Add(cw == x1 - x0 - alw)
        m.Add(ch == y1 - y0 - alw)
        cwv[i], chv[i] = cw, ch

        mw = _g(r.nbc_min_width())
        add("min_clear_width", m.Add(cw >= mw), m.Add(ch >= mw))

        area = m.NewIntVar(1, W * H, f"a{i}")
        m.AddMultiplicationEquality(area, [cw, ch])
        areav[i] = area
        amin = int(math.ceil(r.nbc_min_area_m2() * 1e6 / (GRID_MM ** 2)))
        add("min_area", m.Add(area >= amin))

        # hard aspect cap as a rational, so it stays linear
        num = int(round(spec.max_aspect_hard * 10))
        add("max_aspect_hard",
            m.Add(cw * 10 <= ch * num), m.Add(ch * 10 <= cw * num))

        # soft: area deviation from target
        tgt = int(round(targets[i] * 1e6 / (GRID_MM ** 2)))
        dev = m.NewIntVar(0, W * H, f"dev{i}")
        m.Add(dev >= area - tgt)
        m.Add(dev >= tgt - area)
        ca = int(round(spec.w_area * AREA_SCALE))
        if ca:
            obj.append(ca * dev)

        # soft: elongation past the room's preferred aspect ratio (linear)
        arn = int(round(r.max_aspect * 10))
        ex = m.NewIntVar(0, 10 * max(W, H), f"ex{i}")
        m.Add(ex >= cw * 10 - ch * arn)
        m.Add(ex >= ch * 10 - cw * arn)
        cx = int(round(spec.w_aspect * ASPECT_SCALE))
        if cx:
            obj.append(cx * ex)

        # soft: Vastu pull. The centroid is linear in the cut variables, so the
        # dot product with the zone direction is a plain linear term.
        z = r.zone()
        if z and spec.w_vastu > 0:
            vx, vy = zone_vector(z, north_deg)
            wgt = max(r.weight, 0.2)
            kx = int(round(-spec.w_vastu * VASTU_SCALE * vx * wgt))
            ky = int(round(-spec.w_vastu * VASTU_SCALE * vy * wgt))
            if kx:
                obj.append(kx * (x0 + x1))
            if ky:
                obj.append(ky * (y0 + y1))

    # hard: every door edge must stay wide enough to host a leaf. This is what
    # makes reachability a guarantee rather than a post-hoc hope.
    for (i, j, axis) in doors:
        need = _g(_door_width(reqs[i], reqs[j]) + 2 * JAMB)
        if axis == "x":
            los, his = (rects[i][1], rects[j][1]), (rects[i][3], rects[j][3])
        else:
            los, his = (rects[i][0], rects[j][0]), (rects[i][2], rects[j][2])
        mx = m.NewIntVar(0, max(W, H) + max(X1, Y1), f"omax{i}_{j}")
        mn = m.NewIntVar(0, max(W, H) + max(X1, Y1), f"omin{i}_{j}")
        m.AddMaxEquality(mx, list(los))
        m.AddMinEquality(mn, list(his))
        add("door_width", m.Add(mn - mx >= need))

    # hard: pinned walls. A wall *is* a cut here, so pinning == fixing the var.
    for cid, val in pinned_cuts.items():
        if cid in cuts:
            add("pinned", m.Add(cuts[cid] == val))

    m.Minimize(sum(obj))

    for cid, v in cuts.items():
        nv = nominal_cuts.get(cid)
        if nv is not None:
            lo, hi = (X0, X1) if cut_axis[cid] == "x" else (Y0, Y1)
            m.AddHint(v, max(lo, min(hi, _g(nv, False))))

    if gated:
        m.AddAssumptions([v for vs in groups.values() for v in vs])

    return _M(m, bounds, cuts, cut_axis, rects, cwv, chv, areav, groups)


# ---------------------------------------------------------------- door graph

def _spanning_doors(pairs: dict[tuple[int, int], str], n: int, ent: int,
                    reqs: Sequence[RoomReq], nominal: dict[int, tuple],
                    required: set[frozenset], forbidden: set[frozenset]
                    ) -> list[tuple[int, int, str]] | None:
    """Prim from the entrance over penalised structural edges -> door set.

    Rooted at the entrance, so every room is reachable from the front door by
    construction; the door-width constraints above then keep it true.

    Returns None when the entrance cannot reach every room without an
    NBC-forbidden door. That is a property of the topology, not of the
    dimensions, so the caller should move to the next candidate rather than
    emit an illegal plan.
    """
    adj: dict[int, list[tuple[int, int, str]]] = {i: [] for i in range(n)}
    for (i, j), ax in pairs.items():
        key = frozenset((reqs[i].category, reqs[j].category))
        if key in NBC_FORBIDDEN:
            continue                    # code prohibition, not a penalty
        pen = _EDGE_PEN.get(key, _EDGE_PEN_DEFAULT)
        ids = frozenset((reqs[i].id, reqs[j].id))
        if ids in required:
            pen = -1000
        if ids in forbidden:
            pen += 5000
        if _touching(nominal[i], nominal[j], 1.0) is None:
            pen += 300               # structurally possible but must be forced
        adj[i].append((pen, j, ax))
        adj[j].append((pen, i, ax))
    seen = {ent}
    edges: list[tuple[int, int, str]] = []
    frontier = [(p, ent, j, ax) for p, j, ax in adj[ent]]
    while len(seen) < n and frontier:
        frontier.sort()
        p, u, v, ax = frontier.pop(0)
        if v in seen:
            continue
        seen.add(v)
        edges.append((min(u, v), max(u, v), ax))
        frontier.extend((q, v, k, a2) for q, k, a2 in adj[v] if k not in seen)
    if len(seen) < n:
        return None
    for (i, j), ax in pairs.items():
        if frozenset((reqs[i].id, reqs[j].id)) in required:
            e = (min(i, j), max(i, j), ax)
            if e not in edges:
                edges.append(e)
    return edges


# ---------------------------------------------------------------- IR emission

def _extract_walls(rects: dict[int, tuple[int, int, int, int]],
                   rect_mm: tuple[int, int, int, int],
                   t_ext: int, t_int: int, height: int) -> list[Wall]:
    """Room rectangles -> maximal straight wall centrelines.

    Same inversion as `resplan.py` — union the room boundaries and merge
    collinear intervals so a run may span T-junctions (OpenPlan3D's detectRooms
    splits at tees itself). Because these rectangles are axis-aligned integer
    millimetres by construction, the union is exact 1-D interval arithmetic; no
    snapping, no tolerance, no slivers.
    """
    X0, Y0, X1, Y1 = rect_mm
    vert: dict[int, list[tuple[int, int]]] = {}
    horz: dict[int, list[tuple[int, int]]] = {}
    for (x0, y0, x1, y1) in rects.values():
        vert.setdefault(x0, []).append((y0, y1))
        vert.setdefault(x1, []).append((y0, y1))
        horz.setdefault(y0, []).append((x0, x1))
        horz.setdefault(y1, []).append((x0, x1))

    def merge(ivs: list[tuple[int, int]]) -> list[tuple[int, int]]:
        out: list[list[int]] = []
        for lo, hi in sorted(ivs):
            if out and lo <= out[-1][1]:
                out[-1][1] = max(out[-1][1], hi)
            else:
                out.append([lo, hi])
        return [(a, b) for a, b in out if b > a]

    walls: list[Wall] = []
    for x in sorted(vert):
        ext = x in (X0, X1)
        for lo, hi in merge(vert[x]):
            walls.append(Wall(id=f"w{len(walls)}", start=P(x, lo), end=P(x, hi),
                              thickness=t_ext if ext else t_int, height=height))
    for y in sorted(horz):
        ext = y in (Y0, Y1)
        for lo, hi in merge(horz[y]):
            walls.append(Wall(id=f"w{len(walls)}", start=P(lo, y), end=P(hi, y),
                              thickness=t_ext if ext else t_int, height=height))
    return walls


def _host(walls: list[Wall], axis: str, coord: int, lo: int, hi: int
          ) -> Wall | None:
    """The wall whose centreline carries segment [lo,hi] at `coord`."""
    best, best_len = None, -1
    for w in walls:
        if axis == "x":
            if not (w.start.x == coord and w.end.x == coord):
                continue
            wlo, whi = sorted((w.start.y, w.end.y))
        else:
            if not (w.start.y == coord and w.end.y == coord):
                continue
            wlo, whi = sorted((w.start.x, w.end.x))
        if wlo <= lo and hi <= whi and (whi - wlo) > best_len:
            best, best_len = w, whi - wlo
    return best


def _emit_plan(plan_id: str, rects: dict[int, tuple[int, int, int, int]],
               reqs: Sequence[RoomReq], doors: list[tuple[int, int, str]],
               rect_mm: tuple[int, int, int, int], st: AreaStatement,
               ent: int, storey_height: int, provenance: dict
               ) -> tuple[Plan, list[str], list[str]]:
    X0, Y0, X1, Y1 = rect_mm
    walls = _extract_walls(rects, rect_mm, st.exterior_wall_mm,
                           st.interior_wall_mm, storey_height)
    openings: list[Opening] = []

    def place(w: Wall, centre: int, width: int, kind: str) -> bool:
        L = w.length
        if L < width + 2 * JAMB:
            return False
        vertical = w.start.x == w.end.x
        s = w.start.y if vertical else w.start.x
        e = w.end.y if vertical else w.end.x
        half = (width / 2 + JAMB) / L
        t = min(max((centre - s) / (e - s), half), 1.0 - half)
        is_win = kind == "window"
        openings.append(Opening(
            id=f"o{len(openings)}", kind=kind, wall_id=w.id,
            position=round(t, 6), width=width,
            sill=WIN_SILL if is_win else 0,
            head=WIN_HEAD if is_win else DOOR_HEAD))
        return True

    placed_doors: list[tuple[int, int]] = []
    for (i, j, _ax) in doors:
        t = _touching(rects[i], rects[j], 1.0)
        if t is None:
            continue
        axis, coord, lo, hi = (t[0], int(t[1]), int(t[2]), int(t[3]))
        width = _door_width(reqs[i], reqs[j])
        if hi - lo < width + 2 * JAMB:
            width = max(700, (hi - lo) - 2 * JAMB)
        w = _host(walls, axis, coord, lo, hi)
        if w is not None and place(w, (lo + hi) // 2, width, "door"):
            placed_doors.append((i, j))

    ex0, ey0, ex1, ey1 = rects[ent]
    if ey0 == Y0:
        w = _host(walls, "y", Y0, ex0, ex1)
        if w is not None:
            place(w, (ex0 + ex1) // 2, FRONT_DOOR_W, "front_door")

    no_window: list[str] = []
    for i, r in enumerate(reqs):
        if r.category == "passage":
            continue
        x0, y0, x1, y1 = rects[i]
        cands = []
        if y0 == Y0:
            cands.append(("y", Y0, x0, x1))
        if y1 == Y1:
            cands.append(("y", Y1, x0, x1))
        if x0 == X0:
            cands.append(("x", X0, y0, y1))
        if x1 == X1:
            cands.append(("x", X1, y0, y1))
        placed = False
        for axis, coord, lo, hi in sorted(cands, key=lambda c: c[2] - c[3]):
            w = _host(walls, axis, coord, lo, hi)
            if w is None:
                continue
            wmax = 900 if r.category in ("bathroom", "wc") else 1800
            width = max(600, min(wmax, int((hi - lo) * 0.45) // 50 * 50))
            if hi - lo < width + 2 * JAMB:
                continue
            if place(w, (lo + hi) // 2, width, "window"):
                placed = True
                break
        if not placed:
            no_window.append(r.id)

    rooms: list[Room] = []
    for i, r in enumerate(reqs):
        x0, y0, x1, y1 = rects[i]
        wids = []
        for w in walls:
            if w.start.x == w.end.x:
                if w.start.x not in (x0, x1):
                    continue
                ov = min(max(w.start.y, w.end.y), y1) - max(min(w.start.y, w.end.y), y0)
            else:
                if w.start.y not in (y0, y1):
                    continue
                ov = min(max(w.start.x, w.end.x), x1) - max(min(w.start.x, w.end.x), x0)
            if ov >= WALL_ID_MIN_OVERLAP:
                wids.append(w.id)
        rooms.append(Room(id=r.id, name=r.name, category=r.category,
                          wall_ids=wids,
                          polygon=[P(x0, y0), P(x1, y0), P(x1, y1), P(x0, y1)],
                          area=(x1 - x0) * (y1 - y0)))

    graph: dict[int, set[int]] = {i: set() for i in range(len(reqs))}
    for i, j in placed_doors:
        graph[i].add(j)
        graph[j].add(i)
    seen, stack = {ent}, [ent]
    while stack:
        u = stack.pop()
        for v in graph[u]:
            if v not in seen:
                seen.add(v)
                stack.append(v)
    unreachable = [r.id for i, r in enumerate(reqs) if i not in seen]

    # Record the setbacks the emitted geometry actually achieves (outer wall
    # face to plot boundary), not the bylaw minima: coverage capping and the
    # inward grid snap both push the building further in, and a Site that
    # disagrees with its own walls is worse than no Site at all.
    half = st.exterior_wall_mm // 2
    pw = max(p.x for p in st.plot_polygon)
    pd = max(p.y for p in st.plot_polygon)
    plan = Plan(id=plan_id, walls=walls, openings=openings, rooms=rooms,
                site=Site(plot_polygon=list(st.plot_polygon),
                          north_deg=st.north_deg,
                          setbacks_mm={"front": Y0 - half,
                                       "rear": pd - (Y1 + half),
                                       "left": X0 - half,
                                       "right": pw - (X1 + half)}),
                storey_height=storey_height, provenance=provenance)
    return plan, unreachable, no_window


# ---------------------------------------------------------------- driver

def solve_layout(width_ft: float, depth_ft: float, spec: LayoutSpec, *,
                 road_facing: str = "N", north_deg: float | None = None,
                 profile: BylawProfile | None = None,
                 plan_id: str = "cpsat",
                 pinned_wall_ids: Sequence[str] = (),
                 previous: SolveResult | None = None,
                 storey_height: int = 3000) -> SolveResult:
    """Plot + programme -> Plan. Never raises on timeout or infeasibility."""
    t_start = time.time()
    st = compute_envelope(width_ft, depth_ft, road_facing=road_facing,
                          north_deg=north_deg, profile=profile,
                          programme=list(spec.programme))

    # 1. arithmetic gate — cheaper and far more legible than a CP-SAT proof
    if st.verdict == "INFEASIBLE":
        return SolveResult(
            status="INFEASIBLE", plan=None, statement=st,
            message="area budget: " + "; ".join(st.reasons),
            solve_time_s=0.0, total_time_s=time.time() - t_start,
            infeasible_groups=["area_budget"])

    # 2. absorb leftover area as a hall rather than bloating wet rooms
    reqs = [RoomReq(**{**r.__dict__}) for r in spec.programme]
    targets = [b.budget_m2 for b in st.budgets]
    if st.slack_m2 > spec.filler_min_m2:
        reqs.append(RoomReq("hall", "Hall", "passage",
                            target_m2=st.slack_m2, weight=0.9, max_aspect=3.5))
        targets.append(st.slack_m2)
    elif targets:
        targets[0] += max(0.0, st.slack_m2)

    # rooms tile the centreline rectangle, so targets must sum to the clear area
    # that is actually left once partitions are taken out — otherwise the
    # objective chases a total it can never reach and every room reads "short".
    # Snap the layout rectangle *inward* onto the solver grid, so that every
    # emitted coordinate is a grid multiple and the exterior-edge tests in
    # `_emit_plan` are exact equalities rather than tolerances. Snapping inward
    # can only shrink the footprint, so it cannot breach the coverage cap.
    rect_mm = (-(-st.tiling_polygon[0].x // GRID_MM) * GRID_MM,
               -(-st.tiling_polygon[0].y // GRID_MM) * GRID_MM,
               (st.tiling_polygon[2].x // GRID_MM) * GRID_MM,
               (st.tiling_polygon[2].y // GRID_MM) * GRID_MM)
    rect_f = tuple(float(v) for v in rect_mm)
    TW, TD = rect_mm[2] - rect_mm[0], rect_mm[3] - rect_mm[1]
    alw_mm = st.exterior_wall_mm
    clear_total = _clear_total(mm2_to_m2(TW * TD), targets, alw_mm)
    ts = sum(targets)
    if ts > 0:
        targets = [t * clear_total / ts for t in targets]

    ids = [r.id for r in reqs]
    ent = ids.index(spec.entrance())
    for i, r in enumerate(reqs):
        r.is_entrance = (i == ent)
    weights = [max(t, 0.5) for t in targets]
    required = {frozenset(p) for p in spec.required_adjacency}
    forbidden = {frozenset(p) for p in spec.forbidden_adjacency}
    id_to_i = {r.id: i for i, r in enumerate(reqs)}
    req_adj = [(id_to_i[a], id_to_i[b]) for a, b in spec.required_adjacency
               if a in id_to_i and b in id_to_i]

    # 3. candidate topologies: sample cheaply, keep the best few
    reuse = None
    if previous is not None and previous.topology is not None:
        if previous.topology.get("room_ids") == ids:
            reuse = previous.topology
    rng = random.Random(spec.seed)
    cands: list[tuple[float, list[_Node], int, list[int]]] = []
    if reuse is not None and pinned_wall_ids:
        # A pinned wall only means anything against a fixed topology: walls are
        # not objects here, they are the boundaries the cuts produce.
        nodes, root, order = _tree_from_dict(reuse)
        cands.append((-1e18, nodes, root, order))
    else:
        base = _base_order(reqs, ent, required)
        seen_sig: set[str] = set()
        for k in range(max(1, spec.tree_samples)):
            order = list(base) if k == 0 else _perturb(base, ent, rng)
            nodes, root = _build_tree(order, TW, TD, weights,
                                      None if k == 0 else rng)
            sig = _sig(nodes)
            if sig in seen_sig:
                continue
            seen_sig.add(sig)
            nom, _cv = _nominal(nodes, root, rect_f, weights)
            sc = _score_nominal(nom, reqs, targets, rect_f, alw_mm, ent,
                                st.north_deg, spec, req_adj)
            cands.append((sc, nodes, root, order))
        cands.sort(key=lambda c: c[0])
        cands = cands[:max(1, spec.max_topologies)]

    # 4. CP-SAT down the ranked list. The nominal score is only a surrogate:
    # a topology with a small nominal min-width deficit may be genuinely
    # unsatisfiable while a worse-scoring one solves cleanly, so stopping at
    # the first few would report INFEASIBLE for plans that do fit. Only
    # *feasible* solves count against the quota; infeasible ones are cheap.
    per = max(0.25, spec.time_limit_s / max(1, spec.candidates))
    deadline = t_start + spec.time_limit_s
    best: dict | None = None
    solve_s, tried, n_feasible = 0.0, 0, 0
    all_infeasible, last_status = True, "UNKNOWN"

    for _sc, nodes, root, order in cands:
        if n_feasible >= spec.candidates:
            break
        left = deadline - time.time()
        if tried and left <= 0.05:
            break
        tried += 1
        nom, cutv = _nominal(nodes, root, rect_f, weights)
        labels, _ax = _label_rects(nodes, root)
        pairs = _structural_pairs(labels)
        doors = _spanning_doors(pairs, len(reqs), ent, reqs, nom,
                                required, forbidden)
        if doors is None:
            continue                    # no legal door tree on this topology
        pins = _pinned_cuts(reuse, previous, pinned_wall_ids, nodes)
        mm = _build_model(nodes, root, reqs, targets, rect_mm, alw_mm, spec,
                          st.north_deg, doors, cutv, pins, gated=False)
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = min(per, max(left, 0.25))
        solver.parameters.num_workers = spec.workers
        t0 = time.time()
        status = solver.Solve(mm.m)
        solve_s += time.time() - t0
        last_status = solver.StatusName(status)
        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            all_infeasible = False
            n_feasible += 1
            rects = {i: tuple(int(solver.Value(v)) * GRID_MM
                              for v in mm.rects[i]) for i in mm.rects}
            # Required adjacency and window access are properties of the
            # *topology*, not of the cut positions, so CP-SAT's objective
            # cannot see them. Fold them into the cross-topology comparison
            # instead, or a topology that solves tightly but strands a bedroom
            # with no exterior wall will win.
            got = _adjacent_ids(rects, reqs)
            pen = 1e7 * sum(1 for pr in spec.required_adjacency
                            if frozenset(pr) not in got)
            pen += 1e6 * sum(1 for i, r in enumerate(reqs)
                             if _interior_cell(rects[i], rect_mm)
                             and (r.category in HABITABLE or r.category in WET))
            key = (pen, solver.ObjectiveValue())
            if best is None or key < best["key"]:
                best = {
                    "key": key, "obj": solver.ObjectiveValue(),
                    "status": solver.StatusName(status), "rects": rects,
                    "cuts": {c: int(solver.Value(v)) for c, v in mm.cuts.items()},
                    "cut_axis": dict(mm.cut_axis),
                    "nodes": nodes, "root": root, "order": order, "doors": doors,
                }
        elif status != cp_model.INFEASIBLE:
            all_infeasible = False

    # 5. no solution: separate "cannot fit" from "ran out of time"
    if best is None:
        elapsed = time.time() - t_start
        if all_infeasible:
            groups = _diagnose(cands, reqs, targets, rect_mm, alw_mm, spec, st,
                               required, forbidden, ent, previous,
                               pinned_wall_ids, reuse, weights, rect_f)
            return SolveResult(
                status="INFEASIBLE", plan=None, statement=st,
                message=(f"CP-SAT proved infeasible on all {tried} of "
                         f"{len(cands)} ranked slicing topologies; "
                         f"unsatisfiable constraint set: {groups}"),
                solve_time_s=solve_s, total_time_s=elapsed,
                infeasible_groups=groups, candidates_tried=tried,
                topology_exhausted=True)
        return SolveResult(
            status="TIMEOUT", plan=None, statement=st,
            message=(f"CP-SAT returned {last_status} within "
                     f"{spec.time_limit_s:.1f}s over {tried} topologies; no "
                     "feasible solution found (not proven infeasible)"),
            solve_time_s=solve_s, total_time_s=elapsed, candidates_tried=tried)

    # 6. emit IR
    topo = _tree_to_dict(best["nodes"], best["root"], best["order"], ids)
    topo["cut_values"] = best["cuts"]
    topo["cut_axis"] = best["cut_axis"]
    topo["grid_mm"] = GRID_MM

    areas, tmap, devs, devp, wh = {}, {}, {}, {}, {}
    nbc_bad: list[str] = []
    for i, r in enumerate(reqs):
        x0, y0, x1, y1 = best["rects"][i]
        cw, ch = x1 - x0 - alw_mm, y1 - y0 - alw_mm
        a = mm2_to_m2(cw * ch)
        areas[r.id] = round(a, 3)
        tmap[r.id] = round(targets[i], 3)
        devs[r.id] = round(a - targets[i], 3)
        devp[r.id] = round(100.0 * (a - targets[i]) / max(targets[i], 1e-9), 2)
        wh[r.id] = (cw, ch)
        if min(cw, ch) < r.nbc_min_width():
            nbc_bad.append(f"{r.id}: clear width {min(cw, ch)} mm < "
                           f"{r.nbc_min_width()} mm")
        if a < r.nbc_min_area_m2() - 1e-9:
            nbc_bad.append(f"{r.id}: area {a:.2f} m2 < "
                           f"{r.nbc_min_area_m2():.2f} m2")

    prov = {"source": "cpsat-solver", "grid_mm": GRID_MM,
            "cp_sat_status": best["status"], "objective": best["obj"],
            "topology": topo, "targets_m2": tmap, "clear_areas_m2": areas,
            "wall_allowance_mm": alw_mm,
            "note": ("Room.area is the centreline rectangle so rooms tile the "
                     "footprint exactly; clear_areas_m2 is carpet area"),
            "bylaw_setbacks_mm": dict(st.rules.setbacks_mm),
            "area_statement": st.to_dict()}

    plan, unreachable, no_win = _emit_plan(
        plan_id, best["rects"], reqs, best["doors"], rect_mm, st, ent,
        storey_height, prov)

    got = _adjacent_ids(best["rects"], reqs)
    unmet = [tuple(sorted(p)) for p in spec.required_adjacency
             if frozenset(p) not in got]
    pinned_ok = True
    if pinned_wall_ids and previous is not None and previous.plan is not None:
        pinned_ok = _check_pins(previous.plan, plan, pinned_wall_ids)

    return SolveResult(
        status="OPTIMAL" if best["status"] == "OPTIMAL" else "FEASIBLE",
        plan=plan, statement=st,
        message=(f"{len(reqs)} rooms on {st.plot_area_sqft:.0f} sqft; "
                 f"footprint {mm2_to_m2(st.footprint_mm2):.1f} m2 "
                 f"(coverage cap {mm2_to_m2(st.coverage_cap_mm2):.1f} m2, "
                 f"{st.binding_cap} binding)"),
        solve_time_s=solve_s, total_time_s=time.time() - t_start,
        objective=best["obj"], area_m2=areas, target_m2=tmap, area_dev_m2=devs,
        area_dev_pct=devp, clear_wh_mm=wh, unmet_adjacency=unmet,
        unreachable=unreachable, rooms_without_window=no_win,
        nbc_violations=nbc_bad, topology=topo, pinned_ok=pinned_ok,
        candidates_tried=tried)


# ---------------------------------------------------------------- helpers

def _clear_total(area_m2: float, targets: Sequence[float],
                 alw_mm: int) -> float:
    """Total *clear* area the rooms can actually add up to.

    Rooms tile the centreline rectangle exactly, but each one loses a wall
    allowance on all four sides: a w x h room yields (w-a)(h-a), i.e. it gives
    up a*(w+h) - a^2. Approximating each room as a square of its own area makes
    that a function of the area split alone, and a short fixed-point iteration
    converges. Getting this wrong makes every room report a false shortfall,
    because the objective chases a total that the geometry cannot deliver.
    """
    ts = sum(targets)
    if ts <= 0 or not targets:
        return area_m2
    a = alw_mm / 1000.0                    # metres
    clear = area_m2
    for _ in range(8):
        s = clear / ts
        loss = sum(2.0 * a * math.sqrt(max(t * s, 0.0)) - a * a for t in targets)
        nxt = area_m2 - loss
        if abs(nxt - clear) < 1e-6:
            clear = nxt
            break
        clear = nxt
    return max(clear, 0.1 * area_m2)


def _sig(nodes: list[_Node]) -> str:
    return ";".join(f"{n.axis}:{n.room}:{n.kids}" for n in nodes)


def _base_order(reqs: Sequence[RoomReq], ent: int,
                required: set[frozenset]) -> list[int]:
    """BFS from the entrance over the requested-adjacency graph, so rooms that
    must touch land next to each other in the slicing order."""
    adj: dict[int, list[int]] = {i: [] for i in range(len(reqs))}
    for i in range(len(reqs)):
        for j in range(i + 1, len(reqs)):
            if frozenset((reqs[i].id, reqs[j].id)) in required:
                adj[i].append(j)
                adj[j].append(i)
    order, seen, q = [], {ent}, [ent]
    while q:
        u = q.pop(0)
        order.append(u)
        for v in adj[u]:
            if v not in seen:
                seen.add(v)
                q.append(v)
    rest = sorted((i for i in range(len(reqs)) if i not in seen),
                  key=lambda i: -reqs[i].weight)
    return order + rest


def _perturb(base: Sequence[int], ent: int, rng: random.Random) -> list[int]:
    o = list(base)
    for _ in range(rng.randint(1, max(2, len(o) // 2))):
        i, j = rng.randrange(len(o)), rng.randrange(len(o))
        o[i], o[j] = o[j], o[i]
    if o[0] != ent:                       # keep the entrance at the road edge
        o.remove(ent)
        o.insert(0, ent)
    return o


def _interior_cell(r: tuple, rect_mm: tuple[int, int, int, int]) -> bool:
    """No exterior edge => no window can ever be placed on this room."""
    x0, y0, x1, y1 = r
    X0, Y0, X1, Y1 = rect_mm
    return not (x0 <= X0 or x1 >= X1 or y0 <= Y0 or y1 >= Y1)


def _adjacent_ids(rects: dict[int, tuple], reqs: Sequence[RoomReq]
                  ) -> set[frozenset]:
    got: set[frozenset] = set()
    ks = sorted(rects)
    for a in range(len(ks)):
        for b in range(a + 1, len(ks)):
            i, j = ks[a], ks[b]
            if _touching(rects[i], rects[j], DOOR_W + 2 * JAMB):
                got.add(frozenset((reqs[i].id, reqs[j].id)))
    return got


def _wall_axis_coord(w: Wall) -> tuple[str, int] | None:
    if w.start.x == w.end.x:
        return ("x", w.start.x)
    if w.start.y == w.end.y:
        return ("y", w.start.y)
    return None


def _pinned_cuts(reuse: dict | None, previous: SolveResult | None,
                 pinned_wall_ids: Sequence[str], nodes: list[_Node]
                 ) -> dict[str, int]:
    """Pinned wall ids -> {cut var: fixed value}.

    A wall is not an independent object in this formulation: it is the boundary
    produced by one or more slicing cuts. Pinning therefore means fixing those
    cuts, which is only meaningful when the topology is reused — hence the
    `previous` result carries the topology forward. Retrofitting this later
    would mean re-deriving cut identity from geometry, so it is built in now.
    """
    if not pinned_wall_ids or previous is None or previous.plan is None:
        return {}
    if reuse is None:
        return {}
    vals = reuse.get("cut_values", {})
    axes = reuse.get("cut_axis", {})
    live = {n.cut for n in nodes if n.cut}
    out: dict[str, int] = {}
    for wid in pinned_wall_ids:
        w = previous.plan.wall(wid)
        if w is None:
            continue
        ac = _wall_axis_coord(w)
        if ac is None:
            continue
        axis, coord = ac
        for cid, v in vals.items():
            if cid in live and axes.get(cid) == axis and v * GRID_MM == coord:
                out[cid] = v
    return out


def _check_pins(old: Plan, new: Plan, pinned: Sequence[str]) -> bool:
    """Wall ids are regenerated each solve, so verify geometry, not identity."""
    new_lines = {(w.start.as_tuple(), w.end.as_tuple()) for w in new.walls}
    new_axes = {_wall_axis_coord(w) for w in new.walls}
    for wid in pinned:
        w = old.wall(wid)
        if w is None:
            return False
        if (w.start.as_tuple(), w.end.as_tuple()) in new_lines:
            continue
        if _wall_axis_coord(w) in new_axes:
            continue                   # same centreline, run merged differently
        return False
    return True


def _diagnose(cands, reqs, targets, rect_mm, alw_mm, spec, st, required,
              forbidden, ent, previous, pinned_wall_ids, reuse, weights,
              rect_f) -> list[str]:
    """Which constraint *set* is unsatisfiable — the message the LLM repairs on.

    Three layers, cheapest last-resort first:
      1. the assumption core (a minimal infeasible subset, from CP-SAT);
      2. a single-group relaxation probe, which says which one group would
         restore feasibility on its own — that is the actionable answer;
      3. a closed-form band argument on room widths, so the repair hint carries
         actual numbers rather than a constraint name.
    """
    core_hits: list[str] = []
    binding: list[str] = []
    for _sc, nodes, root, order in cands[:2]:
        nom, cutv = _nominal(nodes, root, rect_f, weights)
        labels, _ax = _label_rects(nodes, root)
        doors = _spanning_doors(_structural_pairs(labels), len(reqs), ent,
                                reqs, nom, required, forbidden)
        if doors is None:
            continue
        pins = _pinned_cuts(reuse, previous, pinned_wall_ids, nodes)
        mm = _build_model(nodes, root, reqs, targets, rect_mm, alw_mm, spec,
                          st.north_deg, doors, cutv, pins, gated=True)
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 2.0
        solver.parameters.num_workers = spec.workers
        if solver.Solve(mm.m) != cp_model.INFEASIBLE:
            continue
        try:
            core = set(solver.SufficientAssumptionsForInfeasibility())
        except Exception:
            core = set()
        for g in CONSTRAINT_GROUPS:
            idxs = {v.Index() for v in mm.groups[g]}
            if core & idxs and g not in core_hits:
                core_hits.append(g)
        for g in list(core_hits):
            if g in binding or not mm.groups[g]:
                continue
            keep = [v for gg in CONSTRAINT_GROUPS if gg != g
                    for v in mm.groups[gg]]
            probe = cp_model.CpSolver()
            probe.parameters.max_time_in_seconds = 1.0
            probe.parameters.num_workers = spec.workers
            m2 = mm.m.Clone()
            m2.ClearAssumptions()
            m2.AddAssumptions(keep)
            if probe.Solve(m2) != cp_model.INFEASIBLE:
                binding.append(g)
        if core_hits:
            break
    hits = binding + [g for g in core_hits if g not in binding]
    hits = hits or _fallback_diagnose(reqs, st)
    band = _band_argument(reqs, rect_mm, alw_mm)
    return ([band] if band else []) + hits


def _band_argument(reqs, rect_mm, alw_mm) -> str | None:
    """Closed-form necessary condition on minimum clear widths.

    A rectangular slicing tiling can only place k rooms of centreline width w
    across a span S if k*w <= S. Rooms too wide to share a band therefore each
    need their own band, and those bands have to fit the other dimension. This
    is the argument that explains, in numbers, why e.g. a 2BHK does not fit
    600 sqft under NBC 2.4 m room widths.
    """
    TW, TD = rect_mm[2] - rect_mm[0], rect_mm[3] - rect_mm[1]
    out = None
    for span, other, sn, on in ((TW, TD, "width", "depth"),
                                (TD, TW, "depth", "width")):
        wide = [r for r in reqs if 2 * (r.nbc_min_width() + alw_mm) > span]
        if len(wide) < 2:
            continue
        need = sum(r.nbc_min_width() + alw_mm for r in wide)
        if need > other:
            out = (f"{len(wide)} rooms need clear width >= "
                   f"{min(r.nbc_min_width() for r in wide)} mm, which the "
                   f"{span} mm layout {sn} admits only one of per band; "
                   f"{len(wide)} bands need {need} mm of {on} but only "
                   f"{other} mm exists")
            break
    return out


def _fallback_diagnose(reqs, st: AreaStatement) -> list[str]:
    hits = []
    if sum(r.nbc_min_area_m2() for r in reqs) > mm2_to_m2(st.tiling_area_mm2):
        hits.append("min_area")
    widest = max((r.nbc_min_width() for r in reqs), default=0)
    if widest > min(st.tiling_w_mm, st.tiling_d_mm) - st.exterior_wall_mm:
        hits.append("min_clear_width")
    return hits or ["min_area+min_clear_width (jointly)"]
