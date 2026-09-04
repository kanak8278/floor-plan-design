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
from .ir import Opening, P, Plan, Room, Site, Stair, Wall

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
# Outdoor rooms that are tiled with the plan (a balcony is a room in the
# tiling; a lawn is a site element). Each one must reach the perimeter.
OUTDOOR_ROOMS = ("balcony", "sitout", "patio", "terrace")

JAMB = 100                        # clear either side of an opening in its wall
MIN_WIN_W = 600                   # below this it is a vent, not a window
PIER_MM = 600                     # masonry between two windows on one wall
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

# How far past its measured ceiling a room may be stretched, when stretching is
# what buys a required adjacency. See the hard aspect cap below.
ASPECT_HARD_SLACK = 1.25

CONSTRAINT_GROUPS = (
    "max_area","min_area", "min_clear_width", "max_aspect_hard",
                     "door_width", "pinned")


# ---------------------------------------------------------------- spec / result

@dataclass
class LayoutSpec:
    """Everything the LLM is allowed to say about a layout."""
    programme: list[RoomReq]
    required_adjacency: list[tuple[str, str]] = field(default_factory=list)
    forbidden_adjacency: list[tuple[str, str]] = field(default_factory=list)
    # Signed preferences from the topology matrix: +1 required ... -1 forbidden.
    # Binary pairs cannot say "mildly discouraged", which is most of the table.
    soft_adjacency: list[tuple[str, str, float]] = field(default_factory=list)
    entrance_room: str | None = None          # default: RoomReq.is_entrance
    # objective weights
    w_area: float = 1.0
    w_aspect: float = 0.35
    w_vastu: float = 0.35
    # Spatial hierarchy. Measured on 400 real ResPlan plans, the living room
    # is the most integrated space in 97% and carries 2.53x the plan's average
    # integration; our own output managed 3.3% and 1.01x, i.e. no hierarchy at
    # all. The topology surrogate had no relational term whatsoever -- only
    # per-room area, width, aspect, window and Vastu -- so nothing ever
    # preferred a hub over a chain.
    w_hub: float = 1.0        # reward the public core touching many rooms
    w_private: float = 1.0    # penalise a private room acting as a corridor
    w_soft_adj: float = 1.0   # weighted topology preferences
    w_circ: float = 1.0       # charge a room with no contact to circulation
    # limits
    max_aspect_hard: float = 4.0
    time_limit_s: float = 8.0
    # 48/96 measured best over the ten-case plot matrix (20x30 1BHK .. 50x80
    # 5BHK): mean area deviation 8.8% against 11.9% at 4/24 and 10.4% at
    # 48/200, median 1.73 s of an 8 s budget. Deeper is not monotonically
    # better -- the per-candidate CP-SAT budget is time_limit/candidates, and a
    # deeper pool also holds worse topologies.
    candidates: int = 48                      # *feasible* topologies to collect
    max_topologies: int = 96                  # ranked topologies CP-SAT may try
    tree_samples: int = 400                   # topologies scored cheaply first
    seed: int = 0
    workers: int = 8
    # Wall-clock budgets make the RESULT depend on machine speed and load: with
    # 8 workers racing a `max_time_in_seconds` cutoff, the same code on the
    # same input returns different plans. That is fine for an interactive
    # solve and fatal for a ruler -- a pure refactor moved the paired suite
    # from 42/100 to 43/100, which is noise reading as progress. Set this for
    # measurement and every run is reproducible; leave it off in production,
    # where finishing on time matters more than finishing identically.
    deterministic: bool = False
    filler_min_m2: float = 3.0                # slack above this becomes a passage
    # Ceiling on the filler passage. Without one, ALL leftover area went into
    # circulation: a 40x60 3BHK produced a 30 m2 passage beside a 10 m2 living
    # room, and `DESIGN.CIRCULATION_OVERSIZED` fired on 66 of 100 suite plans.
    # A passage needs enough width for two people to pass and enough length to
    # reach the rooms; beyond that it is unassigned floor. Surplus past this
    # goes to the social space instead, which is where a client would put it.
    filler_max_m2: float = 9.0

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
    # Why a room is short of its target when the shortfall was a CHOICE. The
    # cross-topology key prefers structure over area on purpose, so on a 30x40
    # 3BHK a 23 m2 living room is reachable but only on topologies that break
    # the required kitchen-living adjacency or leave a room off circulation.
    # Without this the answer looked like a solver failure; it is a trade-off,
    # and an agent can act on knowing which constraint bought the loss.
    tradeoffs: list[str] = field(default_factory=list)

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
                   req_adj: list[tuple[int, int]],
                   soft_adj: list[tuple[int, int, float]] | None = None) -> float:
    """Cheap surrogate for the CP-SAT objective, used to prefilter topologies.

    Worth the 400 samples: CP-SAT on a hopeless topology burns the whole time
    budget proving infeasibility.
    """
    X0, Y0, X1, Y1 = rect
    pen = 0.0
    w_mean = (sum(max(r.weight, 0.2) for r in reqs)
              / max(len(reqs), 1)) or 1.0
    for i, r in enumerate(reqs):
        x0, y0, x1, y1 = rects[i]
        cw, ch = x1 - x0 - alw, y1 - y0 - alw
        if cw <= 0 or ch <= 0:
            return 1e12
        mw = r.nbc_min_width()
        pen += 400.0 * (max(0.0, mw - cw) + max(0.0, mw - ch))
        area = cw * ch / 1e6
        pen += 900.0 * max(0.0, r.nbc_min_area_m2() - area)
        # Weighted by the room's own weight, like the model's area term. Flat
        # here meant the ranking could not tell a topology that gives the
        # living room its 23 m2 from one that gives a bathroom 7 m2 and leaves
        # the living room at 16, so neither could survive selection: with the
        # default 24-topology pool the answer was living 15.8 m2 at 30.5% mean
        # deviation, and with a 120-topology pool 22.8 m2 at 9.6%. The pool was
        # not too small, it was filled with the wrong topologies.
        pen += 40.0 * abs(area - targets[i]) * max(r.weight, 0.2) / w_mean
        ar = max(cw, ch) / max(1.0, min(cw, ch))
        pen += 300.0 * max(0.0, ar - r.max_aspect)
        pen += 3000.0 * max(0.0, ar - spec.max_aspect_hard)
        if not (x0 <= X0 + 1 or x1 >= X1 - 1 or y0 <= Y0 + 1 or y1 >= Y1 - 1):
            # No exterior edge means no window. For a habitable room that is an
            # NBC ventilation failure, not a preference, so it is priced high.
            # A balcony, sitout or patio with no edge on the perimeter is not
            # a balcony at all -- it is an internal void with nothing to open
            # onto, which is what DESIGN.BALCONY_ENCLOSED says. Priced with
            # the habitable rooms, not below the wet ones, because unlike a
            # windowless bathroom (which an exhaust fan can rescue) there is
            # no version of an interior balcony that works.
            pen += (6000.0 if r.category in HABITABLE
                    else 6000.0 if r.category in OUTDOOR_ROOMS
                    else 1500.0 if r.category in WET else 0.0)
        else:
            # Has an exterior wall, but perhaps not enough of one. 2000 per m2
            # of shortfall puts a room 2.6 m2 short at 5200, next to the 6000
            # charged for having no wall at all -- the same defect, priced
            # continuously instead of as a cliff.
            pen += 2000.0 * _glazing_deficit_m2(rects[i], rect, r.category)
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

    # ---- spatial hierarchy ------------------------------------------------
    # Degree in the *structural* adjacency graph is a cheap surrogate for
    # integration: a room that physically touches many others can be doored to
    # many others, which is what makes it a configurational core. Computing real
    # integration here would need the door graph, and the door graph is chosen
    # after this ranking.
    n = len(reqs)
    door_gap = DOOR_W + 2 * JAMB
    deg = [0] * n
    for i in range(n):
        for j in range(i + 1, n):
            if _touching(rects[i], rects[j], door_gap) is not None:
                deg[i] += 1
                deg[j] += 1

    # `passage` belongs here. It was missing, and it is the solver's own
    # circulation room -- `_programme` emits the "Hall" with category
    # "passage" -- so the one room whose entire job is to touch everything got
    # no reward for touching anything. On a 30x40 3BHK that produced a hall
    # in contact with two rooms, a kitchen reachable only through a bedroom
    # and a bathroom opening off another bathroom.
    CIRC_CORE = ("living", "dining", "foyer", "passage", "stair")
    for i, r in enumerate(reqs):
        if r.category in CIRC_CORE:
            # Reward the public core for being reachable from many rooms.
            pen -= spec.w_hub * 900.0 * deg[i] * (1.6 if r.category == "living" else 1.0)
        elif r.category in ("bedroom", "master_bedroom", "study"):
            # A private room touching more than two others will end up carrying
            # traffic. Two is enough: one to circulation, one to its own bath.
            pen += spec.w_private * 1400.0 * max(0, deg[i] - 2)

    # A room that touches no circulation room at all can only be doored to
    # something private or service, which is the through-traffic defect before
    # the door graph is even chosen. Nothing charged for it, so the ranking
    # happily returned layouts where it was unavoidable. A bathroom is exempt:
    # hanging off its bedroom is the normal arrangement, not a fault.
    circ = [i for i, r in enumerate(reqs) if r.category in CIRC_CORE]
    if circ:
        for i, r in enumerate(reqs):
            if i in circ or r.category in ("bathroom", "wc", "balcony"):
                continue
            if not any(_touching(rects[i], rects[c], door_gap) for c in circ):
                # Deliberately modest here, and decisive later. This score
                # also decides which topologies survive truncation to
                # `max_topologies`, so a charge big enough to dominate pushed
                # feasible-but-imperfect topologies out of the pool entirely
                # and a 20x30 1BHK went INFEASIBLE. The real work is done by
                # `_circ_isolated` in the cross-topology key, which only ever
                # compares topologies that already solved and so cannot cost
                # us a solution.
                pen += spec.w_circ * 2200.0

    for i, j, w in (soft_adj or ()):
        touching = _touching(rects[i], rects[j], door_gap) is not None
        # A satisfied positive preference is rewarded; an unsatisfied one is
        # charged half as much, so a preference nudges without dominating.
        if w > 0:
            pen -= spec.w_soft_adj * 800.0 * w * (1.0 if touching else -0.5)
        elif touching:
            pen -= spec.w_soft_adj * 800.0 * w        # w < 0 -> a charge
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
    w_mean = (sum(max(r.weight, 0.2) for r in reqs) / max(len(reqs), 1)) or 1.0
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
    over_terms: list[Any] = []

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
        # Service-room ceiling as a SOFT term, not a constraint.
        #
        # A hard `area <= amax` made wet-01, wet-02 and base-04 infeasible: on a
        # tight tiling the surplus has to go somewhere, and refusing to let it
        # go into a bathroom can leave no solution at all. A bathroom at 6.5 m2
        # instead of 6.0 is a wart, not an impossibility. So overshoot is
        # charged heavily in the objective and feasibility is preserved.
        amax_m2 = getattr(r, "max_area_m2", None)
        over_terms.append(None)
        if amax_m2:
            amax = int(math.floor(amax_m2 * 1e6 / (GRID_MM ** 2)))
            if amax > amin:
                over = m.NewIntVar(0, W * H, f"over{i}")
                m.Add(over >= area - amax)
                m.Add(over >= 0)
                over_terms[-1] = over

        # Hard aspect cap as a rational, so it stays linear.
        #
        # Per room, not one global 4.0. `r.max_aspect` carries the measured
        # ceiling from `standards.MAX_ASPECT` and used to be SOFT -- a
        # preference the objective could buy its way out of, and it did: a
        # bedroom capped at 1.55 came back at 2.36, which is 2630 x 6200 mm.
        # The area was ample (15.2 m2) and the shape was a corridor, so the
        # furnisher could not place a bed in a master bedroom. A shape bound
        # that the objective may violate is not a bound.
        # A margin above the soft preference. Clamping hard AT the measured
        # ceiling left the solver no room to trade shape for a required
        # adjacency and a 3BHK on 30x40 came back with kitchen and living
        # unconnected; 1.25x still excludes the 2.36-aspect "bedroom" that a
        # flat 4.0 licensed, which is the case that mattered.
        soft = r.max_aspect or spec.max_aspect_hard
        hard = min(spec.max_aspect_hard, soft * ASPECT_HARD_SLACK)
        num = int(round(hard * 10))
        add("max_aspect_hard",
            m.Add(cw * 10 <= ch * num), m.Add(ch * 10 <= cw * num))

        # soft: area deviation from target
        tgt = int(round(targets[i] * 1e6 / (GRID_MM ** 2)))
        dev = m.NewIntVar(0, W * H, f"dev{i}")
        m.Add(dev >= area - tgt)
        m.Add(dev >= tgt - area)
        # Weighted by the room's own weight, normalised so total pressure on
        # area is unchanged. A flat coefficient is nearly degenerate here:
        # rooms tile a fixed rectangle, so signed deviations sum to a constant
        # and the solver can take the whole shortfall out of one room. It did
        # -- on a 30x40 3BHK, asking for a bigger hall got a smaller one (ask
        # 18 -> 20.6 m2, ask 26 -> 15.8), every solve honestly OPTIMAL because
        # the objective could not tell those layouts apart.
        ca = int(round(spec.w_area * AREA_SCALE * 100.0
                       * max(r.weight, 0.2) / w_mean))
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

        # Service-room overshoot: priced at 3x the area-deviation weight, so it
    # bites without ever making a plan impossible.
    _over = sum(o for o in over_terms if o is not None)
    m.Minimize(sum(obj)
               + 3 * int(round(spec.w_area * AREA_SCALE * 100.0)) * _over)

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
    """Hub assignment, not a minimum spanning tree.

    A minimum spanning tree over edge costs was the wrong objective and it is
    worth recording why. Prim minimises the TOTAL cost, so on base-01 it chose
    living -> bedroom1 -> hall -> bedroom3 -> bathroom at a cost of 2, which is
    cheaper than any hub -- and which makes you walk through two bedrooms to
    reach the only toilet. Circulation wants a STAR: everything hangs off a
    public spine, and private rooms are leaves.

    Measured consequence of the MST version, against 400 real ResPlan plans:
    the living room was the most integrated space in 3.3% of our plans versus
    97% of real ones.

    Algorithm:
      1. spine  -- connect the circulation rooms (living / dining / foyer /
         stair) into a tree among themselves;
      2. leaves -- attach every other room to an adjacent circulation room,
         cheapest first;
      3. baths  -- a bathroom with no circulation neighbour may attach to one
         bedroom (an en-suite), but never become a through-route;
      4. give up -- if a room can reach neither circulation nor a legal host,
         the TOPOLOGY is wrong; return None so the caller tries the next.

    Returns None rather than emitting a plan that routes traffic through a
    bedroom.
    """
    CIRC = {"living", "dining", "foyer", "stair", "passage"}
    PRIV = {"bedroom", "master_bedroom", "study"}
    # A wet room is always a leaf, and so is `pooja`. Hosting makes the host a
    # two-door room, and two door swings in a 3.4 m2 bathroom leave nowhere for
    # the WC -- the furnisher rejects it and ships a bathroom with no fixtures.
    # A store or utility may still host: reaching a store through the utility
    # is a real arrangement, not a fault.
    LEAF_ONLY = {"bathroom", "wc", "balcony", "shaft", "pooja"}

    adj: dict[int, list[tuple[int, int, str]]] = {i: [] for i in range(n)}
    for (i, j), ax in pairs.items():
        key = frozenset((reqs[i].category, reqs[j].category))
        if key in NBC_FORBIDDEN:
            continue
        ids = frozenset((reqs[i].id, reqs[j].id))
        if ids in forbidden:
            continue                     # a hard prohibition, not a cost
        pen = _EDGE_PEN.get(key, _EDGE_PEN_DEFAULT)
        if ids in required:
            pen = -1000
        if _touching(nominal[i], nominal[j], 1.0) is None:
            pen += 300
        adj[i].append((pen, j, ax))
        adj[j].append((pen, i, ax))

    circ = [i for i in range(n) if reqs[i].category in CIRC]
    if ent not in circ:
        circ.append(ent)                 # the entrance is circulation by role
    circ_set = set(circ)
    edges: list[tuple[int, int, str]] = []

    # 1. spine over circulation, grown from the entrance
    seen = {ent}
    frontier = [(p, ent, j, ax) for p, j, ax in adj[ent] if j in circ_set]
    while frontier:
        frontier.sort()
        p, u, v, ax = frontier.pop(0)
        if v in seen:
            continue
        seen.add(v)
        edges.append((min(u, v), max(u, v), ax))
        frontier.extend((q, v, k, a2) for q, k, a2 in adj[v]
                        if k in circ_set and k not in seen)
    if len(seen) < len(circ_set):
        return None                      # circulation itself is disconnected

    # 2. every remaining room hangs off circulation
    for i in range(n):
        if i in seen:
            continue
        hosts = [(p, j, ax) for p, j, ax in adj[i] if j in seen and j in circ_set]
        if hosts:
            hosts.sort()
            p, j, ax = hosts[0]
            seen.add(i)
            edges.append((min(i, j), max(i, j), ax))

    # 3. an en-suite bathroom may hang off exactly one bedroom -- and a bedroom
    #    may carry exactly one en-suite. Without the second half of that
    #    sentence two baths attached to the same bedroom, which is
    #    DESIGN.MULTIPLE_ATTACHED_BATHS: the second bath has no independent
    #    access, so nobody outside that bedroom can use it.
    ensuite_of: dict[int, int] = {}
    n_baths = sum(1 for r in reqs if r.category == "bathroom")
    for i in range(n):
        if i in seen or reqs[i].category != "bathroom":
            continue
        # The only bathroom in the plan must never be an en-suite: everyone
        # else would reach the toilet through someone's bedroom, which is
        # DESIGN.SOLE_BATH_VIA_BEDROOM. Leave it to the later tiers, which
        # prefer a non-private host.
        if n_baths == 1:
            continue
        beds = [(p, j, ax) for p, j, ax in adj[i]
                if reqs[j].category in PRIV and j in seen
                and j not in ensuite_of]
        if beds:
            # A bedroom the brief asked to have an attached bath wins over one
            # that merely happens to be next to the bathroom. Before this the
            # tier was purely opportunistic and `attached_bath` was read
            # nowhere, so a stated requirement was satisfied by luck.
            beds.sort(key=lambda t: (not reqs[t[1]].attached_bath, t[0]))
            p, j, ax = beds[0]
            seen.add(i)
            ensuite_of[j] = i
            edges.append((min(i, j), max(i, j), ax))

    # 4. fallback: a SERVICE room with no circulation neighbour may hang off any
    #    already-connected room, provided that host does not thereby become a
    #    through-route. Without this tier the stricter rule simply rejected
    #    topologies the MST version had accepted (vastu-01 and base-05 both went
    #    infeasible), which trades one failure for another.
    SERVICE = {"bathroom", "store", "utility", "shaft", "pooja"}
    degree: dict[int, int] = {}
    for a_, b_, _ax in edges:
        degree[a_] = degree.get(a_, 0) + 1
        degree[b_] = degree.get(b_, 0) + 1
    for i in range(n):
        if i in seen or reqs[i].category not in SERVICE:
            continue
        hosts = [(p, j, ax) for p, j, ax in adj[i] if j in seen
                 and reqs[j].category not in LEAF_ONLY
                 and not (reqs[i].category == "bathroom" and j in ensuite_of)
                 and not (reqs[j].category in PRIV and degree.get(j, 0) >= 2)]
        if hosts:
            hosts.sort()
            p, j, ax = hosts[0]
            seen.add(i)
            degree[j] = degree.get(j, 0) + 1
            edges.append((min(i, j), max(i, j), ax))

    # 5. last resort: attach anything still stranded to whatever is reachable,
    #    preferring a non-private host.
    #
    # Returning None here was too strict and cost real coverage: infeasible
    # cases went from 3 to 20 across the suite (base-04, base-05, wet-01,
    # wet-05/06/10, spec-03/09/10, apt-02 and more). Refusing to produce a plan
    # is worse than producing one with a flagged circulation fault -- the
    # validator already reports DESIGN.BEDROOM_THROUGH_TRAFFIC, so the fault is
    # visible either way, and a visible fault beats no answer.
    for i in range(n):
        if i in seen:
            continue
        hosts = [(p + (0 if reqs[j].category not in PRIV else 4000), j, ax)
                 for p, j, ax in adj[i]
                 if j in seen and reqs[j].category not in LEAF_ONLY
                 and not (reqs[i].category == "bathroom" and j in ensuite_of)]
        if not hosts:
            return None                  # genuinely unreachable: no shared wall
        hosts.sort()
        _p, j, ax = hosts[0]
        seen.add(i)
        edges.append((min(i, j), max(i, j), ax))

    if len(seen) < n:
        return None

    # required pairs the assignment did not already create
    for (i, j), ax in pairs.items():
        if frozenset((reqs[i].id, reqs[j].id)) in required:
            e = (min(i, j), max(i, j), ax)
            if e not in edges:
                edges.append(e)
    return edges


# ---------------------------------------------------------------- IR emission

def _emit_stairs(reqs: Sequence[RoomReq], rects: dict[int, tuple],
                 storey_mm: int) -> list[Stair]:
    """A flight inside every room categorised `stair`.

    The riser count comes from the storey height and NBC's 190 mm cap, and the
    flight shape from whether the room is long enough for a straight run: a
    3000 mm storey needs 17 treads at 250 mm = 4250 mm of going, which almost
    no stairwell has in one line, so anything shorter folds into an L. That is
    what real plans do and what `standards.developed_going_mm` measures.
    """
    from . import standards as SD

    out: list[Stair] = []
    std = SD.STAIRS["residential"]
    for i, r in enumerate(reqs):
        if r.category != "stair":
            continue
        x0, y0, x1, y1 = rects[i]
        w, d = x1 - x0, y1 - y0
        if w > d:                      # run along the longer side
            w, d = d, w
            rot = 90.0
        else:
            rot = 0.0
        risers = max(2, math.ceil(storey_mm / std.riser_max_mm))
        need = SD.required_going_mm(risers, std.tread_min_mm)
        # Choose the shape from the FLIGHT's dimensions, not the room's. Both
        # were in scope and picking on the room's said an L-shape had 4660 mm
        # of going while the flight actually emitted had 3460 -- a 231 mm
        # tread against the 250 mm minimum, and NBC.STAIR_TREAD on 8 plans.
        # The validator measures the flight, so the flight is what decides.
        flight_w = max(std.width_min_mm, min(w, 1200))
        flight_d = max(1200, d - 200)
        needs_landing = risers > std.risers_per_flight_max
        kind = "u-shaped"
        for cand in ("straight", "l-shaped", "u-shaped"):
            if cand == "straight" and needs_landing:
                continue
            if SD.developed_going_mm(flight_d, flight_w, cand) >= need:
                kind = cand
                break
        out.append(Stair(
            id=f"st{len(out)}", position=P((x0 + x1) // 2, (y0 + y1) // 2),
            rotation=rot, width=flight_w, depth=flight_d,
            riser_count=risers, direction="up", stair_type=kind))
    return out


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


def _wants_window(category: str) -> bool:
    """Does this room type want glazing even where NBC does not demand it?"""
    from . import roomtypes as rt
    t = rt.get(category)
    return bool(t and t.needs_window)


def _vent_need_m2(category: str, floor_m2: float) -> float:
    """Glazed area NBC asks of one room, from `standards.VENTILATION`.

    Read from the same table the validator reads, so the solver cannot drift
    away from the rule it is being judged against.
    """
    from . import standards as SD
    if category in ("bathroom", "wc"):
        key = "bathroom"
    elif category == "kitchen":
        key = "kitchen"
    elif category in SD.HABITABLE_VENT:
        key = "habitable"
    else:
        return 0.0
    std = SD.VENTILATION[key]
    return max(std.window_frac_of_floor * floor_m2, std.min_window_m2)


def _glazing_runs(lo: int, hi: int, need_mm: int, cap_mm: int
                  ) -> list[tuple[int, int]]:
    """(centre, width) for the windows one wall run should carry.

    One window is centred on the run; several are spread evenly with a masonry
    pier between them, which is what an elevation actually shows.

    The cap is per OPENING, not per wall, and treating it as per wall is what
    broke: `room_cap = 3000` with one window per edge means a 46 m2 living room
    -- needing ~4.2 m of glazing under NBC's 1 m2 per 10 m2 -- cannot be
    satisfied on a wall with only one exterior face, however long that wall is.
    Redistributing surplus floor into habitable rooms made rooms that big
    common, and NBC.VENTILATION_HABITABLE went from 9 plans to 14 as a direct
    result. The sizer's own comment already said "two windows, not one
    impossible one"; it only ever did that across edges, never along one.
    """
    usable = (hi - lo) - 2 * JAMB
    if usable < MIN_WIN_W:
        return []
    # How many openings the need calls for, bounded by what the run can hold.
    by_need = -(-need_mm // cap_mm)                      # ceil
    by_room = (usable + PIER_MM) // (MIN_WIN_W + PIER_MM)
    n = max(1, int(min(by_need, by_room)))
    span_avail = usable - (n - 1) * PIER_MM
    if span_avail < MIN_WIN_W:
        n, span_avail = 1, usable
    width = int(min(cap_mm, max(MIN_WIN_W, min(need_mm, span_avail) // n)))
    width -= width % 50
    if width < MIN_WIN_W:
        return []
    # Do not place an opening the need does not call for.
    while n > 1 and (n - 1) * width >= need_mm:
        n -= 1
    span = n * width + (n - 1) * PIER_MM
    if span > usable:
        return []
    start = lo + JAMB + (usable - span) // 2
    return [(int(start + k * (width + PIER_MM) + width // 2), width)
            for k in range(n)]


def _emit_plan(plan_id: str, rects: dict[int, tuple[int, int, int, int]],
               reqs: Sequence[RoomReq], doors: list[tuple[int, int, str]],
               rect_mm: tuple[int, int, int, int], st: AreaStatement,
               ent: int, storey_height: int, provenance: dict
               ) -> tuple[Plan, list[str], list[str]]:
    X0, Y0, X1, Y1 = rect_mm
    walls = _extract_walls(rects, rect_mm, st.exterior_wall_mm,
                           st.interior_wall_mm, storey_height)
    openings: list[Opening] = []
    # Along-wall intervals already spoken for, including jambs, per wall id.
    used: dict[str, list[tuple[float, float]]] = {}

    def place(w: Wall, centre: int, width: int, kind: str) -> bool:
        L = w.length
        if L < width + 2 * JAMB:
            return False
        vertical = w.start.x == w.end.x
        s = w.start.y if vertical else w.start.x
        e = w.end.y if vertical else w.end.x
        half = (width / 2 + JAMB) / L
        t = min(max((centre - s) / (e - s), half), 1.0 - half)
        # Two openings must not share masonry. There was no such check: `place`
        # clamped an opening inside its wall and then trusted the caller not to
        # ask twice. That held only because every room got at most one window
        # per wall, and it stops holding the moment a large room needs two.
        c = s + t * (e - s)
        reach = width / 2 + JAMB
        lo_i, hi_i = sorted((c - reach, c + reach))
        for a, b in used.get(w.id, ()):
            if lo_i < b and a < hi_i:
                return False
        used.setdefault(w.id, []).append((lo_i, hi_i))
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

    # Glazing is SIZED, not stamped. This used to place one window per room at
    # 45% of the wall run capped at 1800 mm, which is under NBC's 1 m2 per
    # 10 m2 of floor for any room over ~21 m2 -- so every plan with a decent
    # living room shipped with NBC.VENTILATION_HABITABLE as an error, and the
    # solver had no idea. Compute the width the rule demands, then keep opening
    # exterior edges until it is met, which is what a real plan does with a big
    # room: two windows, not one impossible one.
    no_window: list[str] = []
    win_h_mm = max(WIN_HEAD - WIN_SILL, 1)
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

        floor_m2 = (x1 - x0) * (y1 - y0) / 1e6
        need_m2 = _vent_need_m2(r.category, floor_m2)
        if need_m2 <= 0 and not _wants_window(r.category):
            continue        # a pooja niche or a store is better off without one
        # A 10% margin: the validator measures glazing off the emitted opening
        # geometry, and a width rounded down to the nearest 50 mm must not land
        # a hair under the requirement.
        need_mm = int(need_m2 * 1.10 * 1e6 / win_h_mm) if need_m2 > 0 else 0

        got_mm = 0
        target_mm = max(need_mm, MIN_WIN_W)
        # Per-OPENING cap: a bathroom gets a small high window, everything else
        # up to a 3 m sash. More glazing than that on one wall becomes a second
        # window, not a wider one.
        room_cap = 900 if r.category in ("bathroom", "wc") else 3000
        for axis, coord, lo, hi in sorted(cands, key=lambda c: -(c[3] - c[2])):
            if got_mm >= target_mm:
                break
            w = _host(walls, axis, coord, lo, hi)
            if w is None:
                continue
            for centre, width in _glazing_runs(
                    lo, hi, max(MIN_WIN_W, need_mm - got_mm), room_cap):
                if place(w, centre, width, "window"):
                    got_mm += width
                if got_mm >= target_mm:
                    break
        if got_mm == 0:
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
    stairs = _emit_stairs(reqs, rects, storey_height)
    plan = Plan(id=plan_id, walls=walls, openings=openings, rooms=rooms,
                stairs=stairs,
                site=Site(plot_polygon=list(st.plot_polygon),
                          north_deg=st.north_deg,
                          setbacks_mm={"front": Y0 - half,
                                       "rear": pd - (Y1 + half),
                                       "left": X0 - half,
                                       "right": pw - (X1 + half)}),
                storey_height=storey_height, provenance=provenance)
    return plan, unreachable, no_window


# ---------------------------------------------------------------- driver

def _budget(solver, spec, seconds: float) -> None:
    """Give a CP-SAT solve its time budget, wall-clock or deterministic.

    `max_deterministic_time` counts work units rather than seconds, so it stops
    at the same point on a fast machine and a loaded one. It only buys
    reproducibility with a single worker: eight workers race, and whichever
    finds a solution first wins.

    The conversion is not a physical constant -- deterministic units are not
    seconds. 1.0 unit per second is calibrated to land in the same order of
    magnitude as the wall-clock budget it replaces, which is all that is
    needed for the suite to finish in a comparable time.
    """
    solver.parameters.random_seed = spec.seed
    if spec.deterministic:
        solver.parameters.max_deterministic_time = seconds
        solver.parameters.num_workers = 1
    else:
        solver.parameters.max_time_in_seconds = seconds
        solver.parameters.num_workers = spec.workers


def _measure_rooms(reqs, rects, targets, alw_mm):
    """Per-room clear area, deviation from target, and NBC shortfalls.

    `Room.area` is the centreline rectangle so rooms tile the footprint
    exactly; what a client is sold is the clear area inside the walls, which is
    the rectangle less one wall thickness on each axis. Both are reported and
    the provenance note says which is which.
    """
    areas, tmap, devs, devp, wh = {}, {}, {}, {}, {}
    nbc_bad: list[str] = []
    for i, r in enumerate(reqs):
        x0, y0, x1, y1 = rects[i]
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
    return areas, tmap, devs, devp, wh, nbc_bad


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

    # 2. absorb leftover area -- capped circulation first, social space after
    #
    # This used to hand the whole slack to the passage. That is why every plan
    # had a corridor bigger than its living room: the objective aims at the
    # target, so a passage told to be 30 m2 became 30 m2. The surplus belongs
    # in the living room ("living and hall should be one place, that will make
    # them big together"), and the passage should be sized for walking.
    reqs = [RoomReq(**{**r.__dict__}) for r in spec.programme]
    targets = [b.budget_m2 for b in st.budgets]

    # Habitable rooms share the surplus in proportion to what they already
    # asked for, with the social rooms weighted up.
    #
    # Handing it all to the living room instead produced a 637 sqft living on a
    # 2400 sqft plot -- 2.3x its own 120-280 sqft band. That is the same defect
    # as the 337 sqft passage with a different victim: one room absorbing
    # everything. Proportional growth is also what `envelope` already does when
    # it allocates the budget, so this keeps one rule for "who gets area".
    _SURPLUS_SHARE = {"living": 2.0, "dining": 1.5, "master_bedroom": 1.2,
                      "bedroom": 1.0, "study": 0.8, "kitchen": 0.6}

    def _spread(spare: float) -> None:
        """Add `spare` m2 across habitable rooms, proportional to target x share."""
        w = [(i, targets[i] * _SURPLUS_SHARE.get(r.category, 0.0))
             for i, r in enumerate(reqs) if _SURPLUS_SHARE.get(r.category)]
        tot = sum(x for _, x in w)
        if not w or tot <= 0:
            if targets:
                targets[0] += spare
                reqs[0].target_m2 = targets[0]
            return
        for i, x in w:
            targets[i] += spare * x / tot
            reqs[i].target_m2 = targets[i]

    if st.slack_m2 > spec.filler_min_m2:
        # Named "Passage", not "Hall". `roomtypes` lists "hall" as an ALIAS OF
        # LIVING -- in Indian usage the hall IS the living room -- so labelling
        # leftover circulation "Hall" put the word for the main social space on
        # the one room nobody asked for. A reader looking at the drawing sees
        # "HALL 211 SQ FT" in a corner and concludes the living room is in the
        # wrong place, which is a reasonable reading of a mislabelled plan.
        circ = min(st.slack_m2, spec.filler_max_m2)
        reqs.append(RoomReq("passage", "Passage", "passage",
                            target_m2=circ, weight=0.9, max_aspect=3.5,
                            max_area_m2=spec.filler_max_m2))
        targets.append(circ)
        spare = st.slack_m2 - circ
        if spare > 0:
            _spread(spare)
    elif targets:
        _spread(max(0.0, st.slack_m2))

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
    soft_adj = [(id_to_i[a], id_to_i[b], w) for a, b, w in spec.soft_adjacency
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
        ranked_all = list(cands)
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
                                st.north_deg, spec, req_adj, soft_adj)
            cands.append((sc, nodes, root, order))
        cands.sort(key=lambda c: c[0])
        ranked_all = list(cands)
        cands = cands[:max(1, spec.max_topologies)]

    # 4. CP-SAT down the ranked list. The nominal score is only a surrogate:
    # a topology with a small nominal min-width deficit may be genuinely
    # unsatisfiable while a worse-scoring one solves cleanly, so stopping at
    # the first few would report INFEASIBLE for plans that do fit. Only
    # *feasible* solves count against the quota; infeasible ones are cheap.
    per = max(0.25, spec.time_limit_s / max(1, spec.candidates))
    deadline = t_start + spec.time_limit_s
    # Deterministic mode budgets by topology count instead of by clock, so the
    # number of attempts -- and therefore the answer -- is the same everywhere.
    # 4x the candidate pool matches what a wall-clock run reaches in practice
    # while still bounding the tranche expansion below.
    _det_cap = 4 * max(1, spec.candidates)
    best: dict | None = None
    passed: list[tuple[float, float, dict]] = []
    solve_s, tried, n_feasible = 0.0, 0, 0
    all_infeasible, last_status = True, "UNKNOWN"

    def _attempt(nodes, root, order) -> str:
        """Solve one topology and fold it into `best`. Returns the status name.

        A closure because the ranked pool is tried in tranches (see 4b) and the
        two passes must stay identical -- they were a copy-paste for one commit
        and that is exactly how the cross-topology key drifts out of step with
        itself.
        """
        nonlocal best, solve_s, tried, n_feasible, all_infeasible, last_status
        left = (per if spec.deterministic else deadline - time.time())
        if tried and left <= 0.05:
            return "OUT_OF_TIME"
        if spec.deterministic and tried >= _det_cap:
            return "OUT_OF_TIME"        # a topology count, not a clock
        tried += 1
        nom, cutv = _nominal(nodes, root, rect_f, weights)
        labels, _ax = _label_rects(nodes, root)
        pairs = _structural_pairs(labels)
        doors = _spanning_doors(pairs, len(reqs), ent, reqs, nom,
                                required, forbidden)
        if doors is None:
            return "NO_DOOR_TREE"       # no legal door tree on this topology
        pins = _pinned_cuts(reuse, previous, pinned_wall_ids, nodes)
        mm = _build_model(nodes, root, reqs, targets, rect_mm, alw_mm, spec,
                          st.north_deg, doors, cutv, pins, gated=False)
        solver = cp_model.CpSolver()
        _budget(solver, spec, min(per, max(left, 0.25)))
        t0 = time.time()
        status = solver.Solve(mm.m)
        solve_s += time.time() - t0
        last_status = solver.StatusName(status)
        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            all_infeasible = False
            n_feasible += 1
            rects = {i: tuple(int(solver.Value(v)) * GRID_MM
                              for v in mm.rects[i]) for i in mm.rects}
            # Required adjacency, window access and circulation contact are
            # properties of the *topology*, not of the cut positions, so
            # CP-SAT's objective cannot see them. They belong in the
            # cross-topology comparison, or a topology that solves tightly but
            # strands a bedroom with no exterior wall wins. The order is a
            # design decision, so it is stated rather than implied:
            #
            #   1. a habitable room with no exterior wall     2e7
            #   2. a required adjacency the client asked for  1e7
            #   3. a room with no door to circulation         5e6
            #
            # (1) outranks (2) because it is law, not preference: a bedroom
            # with no window fails NBC 2016 Part 3 Cl 8.2.5 and cannot be
            # built, whereas a kitchen that does not touch the living room is a
            # brief miss the client can be asked about.
            got = _adjacent_ids(rects, reqs)
            pen = 2e7 * sum(1 for i, r in enumerate(reqs)
                            if _interior_cell(rects[i], rect_mm)
                            and (r.category in HABITABLE
                                 or r.category in OUTDOOR_ROOMS))
            pen += 1e7 * sum(1 for pr in spec.required_adjacency
                             if frozenset(pr) not in got)
            pen += 1e6 * sum(1 for i, r in enumerate(reqs)
                             if _interior_cell(rects[i], rect_mm)
                             and r.category in WET)
            pen += 5e6 * _circ_isolated(rects, reqs)
            # 4e6: worse than a room off circulation is not the claim -- this
            # is a specific, very visible fault (the household's only toilet
            # is inside a bedroom) and the last-resort door tier will produce
            # it if nothing outranks it. Priced in the KEY rather than
            # rejected, so it can never cost us a plan.
            pen += 4e6 * _sole_bath_private(doors, reqs, ent)
            # 8e5, below everything above it. An unmet en-suite belongs in
            # the key -- door tiers can only connect what the tiling made
            # adjacent, and no door logic rescues a topology that put every
            # bathroom away from the master -- but it is a preference, and the
            # Indian ground truth is largely silent: 127 of the 148
            # bedroom-bearing examples in `suite/` say nothing about
            # `attached_bath`. Weighting it like law would distort layouts for
            # a norm this market does not strongly hold.
            # 3e6: NBC.VENTILATION_HABITABLE is an error and the top suite
            # defect, so above the interior-wet-room term -- but under
            # circulation isolation, because a short facade can also be
            # answered by shrinking the room, whereas no door to circulation
            # has no answer but a different layout.
            pen += 3e6 * sum(1 for i, r in enumerate(reqs)
                             if _glazing_deficit_m2(rects[i], rect_mm,
                                                    r.category) > 0.05)
            pen += 8e5 * _ensuite_unmet(doors, reqs)
            key = (pen, solver.ObjectiveValue())
            passed.append((pen, solver.ObjectiveValue(),
                           {i: rects[i] for i in rects}))
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
        return last_status

    for _sc, nodes, root, order in cands:
        if n_feasible >= spec.candidates:
            break
        if _attempt(nodes, root, order) == "OUT_OF_TIME":
            break

    # 4b. The pool is a SAMPLE. Declaring INFEASIBLE after 24 of 393 scored
    # topologies is an overclaim, and it was wrong in practice: a 1BHK on
    # 20x30 has 37.8 m2 of layout area against a 22.8 m2 programme minimum and
    # came back "proved infeasible". On a tight plot the ranking's own
    # preferences (hierarchy, Vastu, adjacency) push the few topologies that
    # actually fit down the list. So while time remains, keep taking tranches.
    while (best is None and all_infeasible and len(ranked_all) > len(cands)
           and (tried < _det_cap if spec.deterministic
                else time.time() < deadline - 0.1)):
        nxt = ranked_all[len(cands):len(cands) + max(1, spec.max_topologies)]
        cands = cands + nxt
        for _sc, nodes, root, order in nxt:
            st_name = _attempt(nodes, root, order)
            if st_name == "OUT_OF_TIME":
                break
            if best is not None:
                break

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

    areas, tmap, devs, devp, wh, nbc_bad = _measure_rooms(
        reqs, best["rects"], targets, alw_mm)

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

    tradeoffs = _explain_tradeoffs(passed, best, reqs, targets, rect_mm, spec,
                                   alw_mm)

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
        candidates_tried=tried, tradeoffs=tradeoffs)


# ---------------------------------------------------------------- helpers

def _explain_tradeoffs(passed: list[tuple[float, float, dict]],
                       best: dict, reqs: Sequence[RoomReq],
                       targets: Sequence[float],
                       rect_mm: tuple[int, int, int, int],
                       spec: LayoutSpec, alw_mm: int,
                       min_gap_m2: float = 2.0) -> list[str]:
    """Name the constraint that cost a room its area, when one did.

    The cross-topology key puts structure ahead of area on purpose: a plan
    whose kitchen does not touch the living room is wrong in a way a slightly
    small living room is not. But that makes a large shortfall look like a
    solver failure to whoever reads the output. So: find the feasible topology
    that would have fitted the room best, and if it was rejected, say which
    structural fault it carried. An agent can then decide whether to drop the
    adjacency or accept the smaller room -- which is the actual design
    decision, and not one the solver should be making silently.
    """
    if not passed or best is None:
        return []
    chosen = best["rects"]

    def area(rects, i):
        # CLEAR area, the same convention `area_m2` reports. Comparing gross
        # rect area against a clear-area target understated every shortfall by
        # about the wall allowance -- roughly 2 m2 on a 20 m2 room, which is
        # the whole size of the effect being explained.
        x0, y0, x1, y1 = rects[i]
        return mm2_to_m2((x1 - x0 - alw_mm) * (y1 - y0 - alw_mm))

    out: list[str] = []
    for i, r in enumerate(reqs):
        want = targets[i]
        short = want - area(chosen, i)
        if short < min_gap_m2:
            continue
        alt = max(passed, key=lambda t: area(t[2], i))
        gain = area(alt[2], i) - area(chosen, i)
        if gain < min_gap_m2 or alt[0] <= best["key"][0]:
            continue
        why = []
        got = _adjacent_ids(alt[2], reqs)
        miss = [tuple(sorted(p)) for p in spec.required_adjacency
                if frozenset(p) not in got]
        if miss:
            why.append("breaks " + ", ".join("~".join(m) for m in miss))
        iso = _circ_isolated_ids(alt[2], reqs)
        if iso:
            why.append("leaves " + ", ".join(iso[:3]) + " with no door to "
                       "circulation")
        inner = [reqs[j].id for j in range(len(reqs))
                 if _interior_cell(alt[2][j], rect_mm)
                 and (reqs[j].category in HABITABLE or reqs[j].category in WET)]
        if inner:
            why.append("no exterior wall for " + ", ".join(inner[:3]))
        if why:
            out.append(f"{r.id} is {short:.1f} m2 under its {want:.1f} m2 "
                       f"target; a layout giving it {area(alt[2], i):.1f} m2 "
                       f"exists but " + " and ".join(why))
    return out


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


def _ensuite_unmet(doors: list[tuple[int, int, str]],
                   reqs: Sequence[RoomReq]) -> int:
    """Bedrooms that asked for an attached bath and have no bathroom door."""
    baths = {i for i, r in enumerate(reqs) if r.category in ("bathroom", "wc")}
    linked: set[int] = set()
    for a, b, _ax in doors:
        if a in baths:
            linked.add(b)
        if b in baths:
            linked.add(a)
    return sum(1 for i, r in enumerate(reqs)
               if r.attached_bath and i not in linked)


def _sole_bath_private(doors: list[tuple[int, int, str]],
                       reqs: Sequence[RoomReq], ent: int) -> int:
    """1 if the plan's only bathroom can be reached only through a bedroom."""
    baths = [i for i, r in enumerate(reqs) if r.category == "bathroom"]
    if len(baths) != 1:
        return 0
    priv = {i for i, r in enumerate(reqs)
            if r.category in ("bedroom", "master_bedroom", "study")}
    adj: dict[int, set[int]] = {}
    for a, b, _ax in doors:
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    seen, stack = {ent}, [ent]
    while stack:
        u = stack.pop()
        for v in adj.get(u, ()):
            if v in seen or v in priv:
                continue
            seen.add(v)
            stack.append(v)
    return 0 if baths[0] in seen else 1


def _circ_isolated_ids(rects: dict[int, tuple],
                       reqs: Sequence[RoomReq]) -> list[str]:
    """Rooms that touch no circulation room at all, by id.

    Baths and balconies are exempt: hanging off a bedroom is what they do.
    """
    circ = [i for i, r in enumerate(reqs)
            if r.category in ("living", "dining", "foyer", "passage", "stair")]
    if not circ:
        return []
    door_gap = DOOR_W + 2 * JAMB
    out: list[str] = []
    for i, r in enumerate(reqs):
        if i in circ or r.category in ("bathroom", "wc", "balcony"):
            continue
        if not any(_touching(rects[i], rects[c], door_gap) for c in circ):
            out.append(r.id)
    return out


def _circ_isolated(rects: dict[int, tuple], reqs: Sequence[RoomReq]) -> int:
    return len(_circ_isolated_ids(rects, reqs))


def _exterior_run_mm(r: tuple, rect_mm: tuple[int, int, int, int]) -> int:
    """Total length of this room's edges that lie on the building perimeter."""
    x0, y0, x1, y1 = r
    X0, Y0, X1, Y1 = rect_mm
    # The +/-1 slack matches the guard in `_score_nominal`, which asks the same
    # question. Without it the two could disagree about a room sitting half a
    # millimetre off the boundary: one would call it exterior and the other
    # would compute no run at all.
    run = 0
    if y0 <= Y0 + 1:
        run += x1 - x0
    if y1 >= Y1 - 1:
        run += x1 - x0
    if x0 <= X0 + 1:
        run += y1 - y0
    if x1 >= X1 - 1:
        run += y1 - y0
    return run


# What fraction of an exterior run can actually become glass. `_glazing_runs`
# places openings up to 3000 mm with a 600 mm pier between and a 100 mm jamb at
# each end, so a long wall tops out near 3000/3600 of its length.
GLAZED_FRACTION = 0.80


def _glazing_deficit_m2(r: tuple, rect_mm: tuple[int, int, int, int],
                        category: str) -> float:
    """Glazing NBC demands, minus what this room's outside walls can carry.

    The ranking already charged a room with NO exterior edge, and charged
    nothing at all for a room whose exterior edge is simply too SHORT. Those
    are the same defect at two magnitudes: NBC wants 1 m2 of glass per 10 m2
    of floor, so a 46 m2 living room needs ~4.6 m2, which is about 4.2 m of
    sash at a 1200 mm window height -- and no wall shorter than that can
    provide it however the openings are subdivided.

    That gap became the top defect the day surplus floor started going to
    habitable rooms instead of the passage: NBC.VENTILATION_HABITABLE went
    from 9 plans to 14 and NBC.VENTILATION_KITCHEN from 4 to 9. Widening the
    sizer to place several windows per wall recovered 2 of those 5, and the
    rest are not a sizer problem -- the solver put a big room where there is
    not enough facade to light it, and only the layout can fix that.

    Returns 0.0 for a room with no exterior edge at all: that case is already
    priced, heavily, and double-charging it would change a tuned behaviour
    while pretending to add a new one.
    """
    run = _exterior_run_mm(r, rect_mm)
    if run <= 0:
        return 0.0
    floor_m2 = (r[2] - r[0]) * (r[3] - r[1]) / 1e6
    need = _vent_need_m2(category, floor_m2)
    if need <= 0:
        return 0.0
    win_h = max(WIN_HEAD - WIN_SILL, 1)
    capacity = max(0, run - 2 * JAMB) * GLAZED_FRACTION * win_h / 1e6
    return max(0.0, need - capacity)


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
        _budget(solver, spec, 2.0)
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
            _budget(probe, spec, 1.0)
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
