"""The closed repair loop: solve -> validate -> patch -> re-solve.

This is what makes the system self-correcting rather than one-shot. Three rules
that matter more than the mechanics:

1. **Never return a worse plan than you started with.** Each iteration is scored
   and the best is kept, so a bad patch costs time and nothing else. Without this
   a loop can walk downhill confidently.
2. **Bounded, with non-convergence surfaced.** Five iterations, then report what
   is still broken. Looping until clean either never terminates or launders a
   failure into a timeout.
3. **Spec-level patches are preferred.** They re-enter the solver, which owns
   geometry, so the result is valid by construction. Geometry-level patches edit
   coordinates directly and are therefore the last resort.

Geometry ops are currently PARSED, VALIDATED and REPORTED but not applied: doing
so needs an IR mutation layer that mirrors OpenPlan3D's `project.ts`, which does
not exist yet. Saying so is better than silently dropping them.
"""
from __future__ import annotations
import copy, time
from dataclasses import dataclass, field
from typing import Any

from .bylaws import BENGALURU
from .bridge import spec_to_programme, relational_pairs, resolve_scenario, SQFT_M2
from .envelope import compute_envelope, CityProfileAdapter
from .solver import solve_layout, LayoutSpec
from .rules import validate as validate_plan

PROFILE = CityProfileAdapter(BENGALURU)


# --------------------------------------------------------------- spec patching
def apply_spec_ops(spec: Any, ops: list[Any]) -> tuple[Any, list[str], list[tuple[str, str]]]:
    """Apply the 11 spec-level ops to a copy of `spec`.

    Returns (new_spec, applied_descriptions, rejected[(op, reason)]). Ops are
    applied independently: two bad ops in a batch of six leave four applied, not
    a whole-batch failure and not a half-applied edit.
    """
    s = copy.deepcopy(spec)
    applied: list[str] = []
    rejected: list[tuple[str, str]] = []
    rooms = {r.id: r for r in getattr(s, "rooms", [])}

    for op in ops:
        name = getattr(op, "op", "")
        p = getattr(op, "params", {}) or {}
        desc = getattr(op, "description", "") or name
        try:
            if name == "set_room_area":
                r = rooms.get(p["room_id"])
                if r is None:
                    rejected.append((name, f"no room {p['room_id']!r}")); continue
                r.min_sqft, r.max_sqft = float(p["min_sqft"]), float(p["max_sqft"])
            elif name == "set_room_aspect":
                r = rooms.get(p["room_id"])
                if r is None:
                    rejected.append((name, f"no room {p['room_id']!r}")); continue
                r.max_aspect = float(p["max_aspect"])
            elif name == "set_room_zone":
                r = rooms.get(p["room_id"])
                if r is None:
                    rejected.append((name, f"no room {p['room_id']!r}")); continue
                r.preferred_zone = p["preferred_zone"]
            elif name == "set_room_priority":
                r = rooms.get(p["room_id"])
                if r is None:
                    rejected.append((name, f"no room {p['room_id']!r}")); continue
                r.priority = int(p["priority"])
            elif name == "add_room":
                if p["room_id"] in rooms:
                    rejected.append((name, f"room {p['room_id']!r} already exists")); continue
                from .spec import RoomSpec
                nr = RoomSpec(id=p["room_id"], category=p["category"],
                              name=p.get("name", "") or p["room_id"].title())
                if p.get("min_sqft"): nr.min_sqft = float(p["min_sqft"])
                s.rooms.append(nr); rooms[nr.id] = nr
            elif name == "remove_room":
                if p["room_id"] not in rooms:
                    rejected.append((name, f"no room {p['room_id']!r}")); continue
                s.rooms = [r for r in s.rooms if r.id != p["room_id"]]
                rooms.pop(p["room_id"])
            elif name in ("set_adjacency", "remove_adjacency"):
                from .spec import Adjacency
                a, b = p["a"], p["b"]
                s.adjacency = [x for x in getattr(s, "adjacency", [])
                               if {x.a, x.b} != {a, b}]
                if name == "set_adjacency":
                    s.adjacency.append(Adjacency(a=a, b=b, kind=p.get("kind", "required"),
                                                 relation=p.get("relation", "adjacent")))
            elif name == "set_entrance":
                if p.get("side"): s.entrance.side = p["side"]
                if p.get("zone"): s.entrance.zone = p["zone"]
                if p.get("via_foyer") is not None: s.entrance.via_foyer = bool(p["via_foyer"])
            elif name == "set_wet_grouping":
                s.wet_grouping = p["value"]
            elif name == "set_storeys":
                s.storeys = int(p["value"])
            else:
                rejected.append((name, "not a spec-level op")); continue
            applied.append(desc)
        except Exception as e:
            rejected.append((name, f"{type(e).__name__}: {e}"))
    return s, applied, rejected


# ------------------------------------------------------------------- the loop
@dataclass
class Iteration:
    n: int
    status: str
    n_errors: int
    n_warnings: int
    vastu: float | None
    applied: list[str] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)
    deferred_geometry: list[str] = field(default_factory=list)
    solve_ms: int = 0
    note: str = ""


@dataclass
class RepairResult:
    plan: Any = None
    spec: Any = None
    status: str = "not_run"
    converged: bool = False
    iterations: list[Iteration] = field(default_factory=list)
    findings: list[Any] = field(default_factory=list)
    total_ms: int = 0

    @property
    def summary(self) -> str:
        if not self.iterations:
            return "no iterations"
        a, b = self.iterations[0], self.iterations[-1]
        return (f"{len(self.iterations)} iters: errors {a.n_errors}->{b.n_errors}, "
                f"warnings {a.n_warnings}->{b.n_warnings}, "
                f"{'converged' if self.converged else 'NOT converged'}")


def _score(n_err: int, n_warn: int, vastu: float | None) -> tuple:
    """Lower is better. Errors dominate absolutely; then warnings; then Vastu."""
    return (n_err, n_warn, -(vastu or 0.0))


def _solve(spec, w_ft, d_ft, facing, time_limit_s, plan_id):
    prog, warn = spec_to_programme(spec)
    if not prog:
        return None, "empty programme", warn, 0
    t0 = time.time()
    stmt = compute_envelope(w_ft, d_ft, road_facing=facing, profile=PROFILE, programme=prog)
    budgets = {b.id: b for b in (getattr(stmt, "budgets", None) or [])}
    for r in prog:
        b = budgets.get(r.id)
        if b is not None and getattr(b, "budget_m2", 0):
            r.target_m2 = round(float(b.budget_m2), 2)
    # Relational terms. Without these the loop solved with no relational
    # objective -- the 1e7 required-adjacency penalty multiplied an empty list
    # and `w_soft_adj` weighted nothing -- and then validated the result against
    # TYPO / TOPO / ZONE rules that assume one. Every relational finding the
    # patcher then saw was unreachable by re-solving, so the loop could only
    # ever clear it by luck.
    sc = resolve_scenario(prog, site_kind=getattr(spec, "site_kind", "plot"),
                          plot_sqft=(w_ft * d_ft) if (w_ft and d_ft) else None,
                          storeys=int(getattr(spec, "storeys", 1) or 1))
    req_adj, forb_adj, soft_adj = relational_pairs(prog, sc, spec=spec)
    sr = solve_layout(w_ft, d_ft,
                      LayoutSpec(programme=prog,
                                 entrance_room=next((r.id for r in prog if r.is_entrance),
                                                    prog[0].id),
                                 required_adjacency=req_adj,
                                 forbidden_adjacency=forb_adj,
                                 soft_adjacency=soft_adj,
                                 time_limit_s=time_limit_s),
                      road_facing=facing, profile=PROFILE, plan_id=plan_id)
    return sr, str(getattr(sr, "status", "?")), warn, round(1000 * (time.time() - t0))


def repair(spec: Any, *, max_iters: int = 5, time_limit_s: float = 10.0,
           client=None, propose=None, target_vastu: float | None = 0.78) -> RepairResult:
    """Solve `spec`, then iterate patch/re-solve until good enough or bounded out.

    "Good enough" is deliberately not just "legal". Stopping at zero errors left
    plans at a Vastu score of 0.33 -- six of nine rules firing -- which is legal
    and unsellable in this market. So the loop keeps going on soft objectives
    while they are below `target_vastu`, still keeping only improvements.
    Set `target_vastu=None` to stop at legality.
    """
    from .llm import propose_patch, summarize_plan
    propose = propose or propose_patch

    res = RepairResult(spec=spec)
    t_start = time.time()
    w_ft = getattr(spec, "plot_width_ft", None)
    d_ft = getattr(spec, "plot_depth_ft", None)
    if not (w_ft and d_ft):
        res.status = "no_plot"
        return res
    facing = (getattr(spec, "road_facing_side", None) or "n")[0].upper()

    best = None                      # (score, plan, spec, findings, status)
    cur_spec = spec
    for i in range(max_iters):
        sr, status, bwarn, ms = _solve(cur_spec, w_ft, d_ft, facing, time_limit_s,
                                       f"repair-{i}")
        plan = getattr(sr, "plan", None) if sr else None
        if plan is None:
            res.iterations.append(Iteration(
                n=i, status=status, n_errors=-1, n_warnings=-1, vastu=None,
                solve_ms=ms,
                note=f"no plan: {list(getattr(sr, 'infeasible_groups', []) or [])}"))
            if best is None:
                res.status = status
                res.total_ms = round(1000 * (time.time() - t_start))
                return res
            break

        findings = validate_plan(plan, brief=None, profile=BENGALURU)
        errs = [f for f in findings if f.severity == "error"]
        warns = [f for f in findings if f.severity == "warn"]
        vf = [f for f in findings if f.rule_id.startswith("VASTU")]
        vastu = round(1.0 - len(vf) / 9.0, 3)
        sc = _score(len(errs), len(warns), vastu)
        it = Iteration(n=i, status=status, n_errors=len(errs), n_warnings=len(warns),
                       vastu=vastu, solve_ms=ms)

        if best is None or sc < best[0]:
            best = (sc, plan, cur_spec, findings, status)
        else:
            it.note = "kept the previous plan; this iteration scored worse"

        soft_ok = target_vastu is None or vastu >= target_vastu
        if not errs and soft_ok:
            it.note = it.note or "clean"
            res.iterations.append(it)
            res.converged = True
            break
        if not errs:
            it.note = (it.note or "") + f" legal but vastu {vastu} < {target_vastu}"
            # Nothing left to improve on the last pass -- do not burn a call.
            if i == max_iters - 1:
                res.iterations.append(it)
                res.converged = True
                break

        # ---- ask for a patch -------------------------------------------
        # When there are no errors the actionable findings are the warnings, so
        # send those rather than an empty list.
        actionable = errs if errs else [f for f in warns if f.rule_id.startswith("VASTU")]
        try:
            ops = propose(summarize_plan(plan), actionable, cur_spec, client=client)
        except Exception as e:
            it.note = f"patch proposal failed: {type(e).__name__}: {e}"
            res.iterations.append(it)
            break
        ops = list(getattr(ops, "ops", ops) or [])
        # Furniture ops are now executed rather than deferred: they were declared
        # and dropped, which reported success while changing nothing.
        furn_ops = [o for o in ops if getattr(o, "level", "") == "furniture"]
        spec_ops = [o for o in ops if getattr(o, "level", "") == "spec"]
        geo_ops = [o for o in ops if getattr(o, "level", "") == "geometry"]
        if furn_ops:
            from .apply import apply_furniture_ops
            far = apply_furniture_ops(plan, furn_ops)
            plan = far.plan
            it.applied += far.applied
            it.rejected += far.rejected
        it.deferred_geometry = [f"{getattr(o,'op','?')}: {getattr(o,'description','')}"
                                for o in geo_ops]
        if not spec_ops:
            it.note = (it.note or "") + " no spec-level op proposed; nothing to re-solve"
            res.iterations.append(it)
            break
        cur_spec, applied, rejected = apply_spec_ops(cur_spec, spec_ops)
        it.applied, it.rejected = applied, rejected
        res.iterations.append(it)

    if best is not None:
        _, res.plan, res.spec, res.findings, res.status = best
    res.total_ms = round(1000 * (time.time() - t_start))
    return res
