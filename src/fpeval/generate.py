"""Brief -> geometry, as one command in the log.

This is the step that was missing. `solve_layout` was called in exactly one
place in the tree -- the `/api/generate` endpoint, which writes to no document
-- so an agent could state a programme and never compile it. `replace_storey`
existed for precisely this and was unreachable: its handler needs a solved
`Plan` as an out-of-band payload, and the agent's only write tool cannot carry
one. So the solve happens here, on the server's side of the tool boundary, and
arrives at the document as a payload.

That split is deliberate and is the rule from DECISIONS.md #6: the model emits
a brief, the solver emits coordinates. Nothing in this module reads the model's
opinion about where a wall should go, because it is never asked for one.

## Why a command and not a mutation

Going through `Document.apply` means the solve is in the log with everything
else, so it undoes, it renders in the change feed, and time travel lands on
either side of it. The solved `Plan` rides along as the log entry's payload
rather than being recomputed on replay, because CP-SAT under a wall-clock
limit is not reproducible across machines or load -- re-solving during a
replay would quietly produce a *different* floor plan.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from .bridge import cap_service_targets, shed_optional, spec_to_programme
from .bylaws import BENGALURU
from .commands import Command
from .envelope import CityProfileAdapter, UnitInterior, compute_envelope
from .programme import Assumption, resolve, spec_to_brief, summarise
from .rules import validate as validate_plan
from .solver import LayoutSpec, solve_layout
from .spec import DesignSpec

PROFILE = CityProfileAdapter(BENGALURU)


@dataclass
class BuildResult:
    status: str = "?"
    ok: bool = False
    assumptions: list[Assumption] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    infeasible_groups: list[Any] = field(default_factory=list)
    tradeoffs: list[str] = field(default_factory=list)
    findings: list[Any] = field(default_factory=list)
    solve_ms: int = 0
    event: Any = None
    errors: list[str] = field(default_factory=list)
    # The brief the plan was validated against, so a caller can re-validate
    # the same way after editing rather than falling back to `None`.
    brief: dict = field(default_factory=dict)

    @property
    def n_errors(self) -> int:
        return sum(1 for f in self.findings if getattr(f, "severity", "") == "error")

    @property
    def n_warnings(self) -> int:
        return sum(1 for f in self.findings if getattr(f, "severity", "") == "warn")


def _adjacency(spec: DesignSpec, ids: set[str]) -> tuple[list, list]:
    """Brief adjacencies as solver constraints.

    The brief writes some of these against a category ("no WC opening onto a
    kitchen") and some against a room id, so both are resolved to ids here.
    Without this the agent's `set_adjacency` would write to the brief, report
    success, and change nothing about the solve -- the same shape of failure
    as a command with no handler, only harder to notice.
    """
    by_cat: dict[str, list[str]] = {}
    for r in spec.rooms:
        by_cat.setdefault(r.category, []).append(r.id)

    def expand(token: str) -> list[str]:
        if token in ids:
            return [token]
        return by_cat.get(token, [])

    req: list[tuple[str, str]] = []
    forb: list[tuple[str, str]] = []
    for a in spec.adjacency:
        for x in expand(a.a):
            for y in expand(a.b):
                if x == y:
                    continue
                (req if a.kind == "required" else forb).append((x, y))
    return req, forb


def build(doc: Any, *, time_limit_s: float = 12.0,
          reason: str = "", source: str = "solver") -> BuildResult:
    """Solve the document's brief and replace its active storey with the result.

    Returns a `BuildResult` either way. An INFEASIBLE brief is an answer, not
    an error: it names the constraint groups that could not hold at once, and
    that is more useful to a user than a plan that quietly dropped a room.
    """
    design = doc.design
    # No brief at all is treated as an empty one, not as an error. `resolve`
    # fills an empty brief with the standard 2BHK and says so, and "here is a
    # standard 2BHK, tell me your plot and I will re-solve" is a better answer
    # to "build me a 2bhk" than a refusal. The safety is that every invented
    # value comes back in `assumptions` and the caller is told to relay them.
    sp, assumptions = resolve(getattr(design, "spec", None) or DesignSpec())
    prog, warn = spec_to_programme(sp)
    if not prog:
        return BuildResult(status="empty_programme", assumptions=assumptions,
                           warnings=warn,
                           errors=["the brief has no room the solver can lay "
                                   "out"])
    if sp.storeys > 1:
        # Honest about the limit rather than solving one floor and calling it
        # the house: `solve_layout` lays out a single storey, and splitting a
        # programme across floors is a decision nobody has made yet.
        warn.append(f"the brief asks for {sp.storeys} storeys; only the "
                    "ground floor is laid out, the rest are not solved")

    # Ceilings on service and wet rooms. `cap_service_targets` sets
    # `RoomReq.max_area_m2`, and it was called from exactly one place in the
    # tree -- `score.py`, the suite path -- so the agent path had no ceiling at
    # all. Measured on a 40x60 3BHK solved through the agent: bathrooms at 14.2
    # and 15.6 m2 (against a ~4%-of-carpet norm), a pooja room at 7.9 m2 for a
    # brief that asked 45 sqft, and a utility at 7.6 m2. The target is what the
    # objective aims at, not a bound, so CP-SAT grows a service room into any
    # surplus the envelope leaves.
    #
    # Habitable rooms stay uncapped on purpose: the surplus has to land
    # somewhere, and a bigger living room is the right place for it.
    warn += cap_service_targets(prog)

    # An apartment unit is solved against its own interior, not against a
    # plot: see `envelope.UnitInterior`. Measured before this, a 1150 sqft
    # carpet unit lost a quarter of its area to setbacks it does not have.
    unit = sp.site_kind == "apartment_unit"
    profile = UnitInterior() if unit else PROFILE
    # Only Bengaluru has real bye-law tables. `spec.CITY_PROFILES` lists
    # thirteen cities and says of them that they "are estimates of the same
    # shape and should be replaced by bylaws.py's real tables"; `bylaws
    # .PROFILES` has exactly one key. `set_plot` accepts a city, so saying
    # nothing here would make that parameter a lie.
    if not unit and sp.city_profile not in ("bengaluru", "generic_in"):
        warn.append(f"asked for {sp.city_profile} bye-laws and only Bengaluru "
                    "has a verified table, so Bengaluru's setbacks, FAR and "
                    "coverage were used -- treat the compliance figures as "
                    "indicative for this city")

    facing = (sp.road_facing_side or "north")[0].upper()
    w_ft, d_ft = float(sp.plot_width_ft or 0), float(sp.plot_depth_ft or 0)
    ids = {r.id for r in prog}
    req, forb = _adjacency(sp, ids)

    t0 = time.time()
    try:
        stmt = compute_envelope(w_ft, d_ft, road_facing=facing, profile=profile,
                                programme=prog)
        # The envelope decides how much floor there actually is after setbacks
        # and coverage, and hands back a per-room budget. Solving against the
        # brief's wish list instead is how a programme that wants 1400 sqft on
        # a 900 sqft envelope becomes a plan with one enormous hall.
        budgets = {b.id: b for b in (getattr(stmt, "budgets", None) or [])}
        for r in prog:
            b = budgets.get(r.id)
            if b is not None and getattr(b, "budget_m2", 0):
                r.target_m2 = round(float(b.budget_m2), 2)
        res = solve_layout(
            w_ft, d_ft,
            LayoutSpec(programme=prog,
                       required_adjacency=req, forbidden_adjacency=forb,
                       entrance_room=next((r.id for r in prog if r.is_entrance),
                                          prog[0].id),
                       time_limit_s=time_limit_s),
            road_facing=facing, north_deg=sp.north_deg, profile=profile,
            plan_id=f"{design.id}-solved")
    except Exception as exc:
        return BuildResult(status="error", assumptions=assumptions, warnings=warn,
                           solve_ms=round(1000 * (time.time() - t0)),
                           errors=[f"{type(exc).__name__}: {exc}"])

    # ---- shed the nice-to-haves rather than refuse ----------------------
    # `RoomSpec.optional` is read nowhere on the solve path -- not in
    # `bridge`, not in `envelope`, not in `solver` -- so every room in the
    # brief was mandatory and an over-specified brief could only come back
    # INFEASIBLE. Measured on the first track B run: from "a 30x40 east facing
    # site in Bengaluru, need a 3BHK ground floor house", extraction produced
    # eleven solver rooms including a sitout, a foyer, a dining, a utility and
    # a pooja that nobody asked for. Total target area was LOWER than the
    # ground truth's, so it was not area -- eleven rooms each carry an NBC
    # minimum plus their walls, and that does not fit 1200 sqft after
    # setbacks. Track A never sees this because ground truth asks for what it
    # asks for.
    #
    # Dropping the optional rooms, lowest priority first, and saying which is
    # a better answer than a refusal: the client gets a house and a sentence
    # about what did not fit.
    if getattr(res, "plan", None) is None and any(
            getattr(r, "optional", False) for r in sp.rooms):
        kept, shed = shed_optional(prog)
        keep_ids = {r.id for r in kept}
        if kept:
            warn.append("dropped the optional room(s) "
                        + ", ".join(sorted(shed))
                        + " -- the programme did not fit the site with them")
            req2 = [(a, b) for a, b in req if a in keep_ids and b in keep_ids]
            forb2 = [(a, b) for a, b in forb if a in keep_ids and b in keep_ids]
            try:
                stmt = compute_envelope(w_ft, d_ft, road_facing=facing,
                                        profile=profile, programme=kept)
                budgets = {b.id: b for b in (getattr(stmt, "budgets", None) or [])}
                for r in kept:
                    b = budgets.get(r.id)
                    if b is not None and getattr(b, "budget_m2", 0):
                        r.target_m2 = round(float(b.budget_m2), 2)
                res = solve_layout(
                    w_ft, d_ft,
                    LayoutSpec(programme=kept, required_adjacency=req2,
                               forbidden_adjacency=forb2,
                               entrance_room=next(
                                   (r.id for r in kept if r.is_entrance),
                                   kept[0].id),
                               time_limit_s=time_limit_s),
                    road_facing=facing, north_deg=sp.north_deg, profile=profile,
                    plan_id=f"{design.id}-solved")
                prog = kept
            except Exception as exc:
                warn.append(f"retry without optional rooms failed: "
                            f"{type(exc).__name__}: {exc}")

    out = BuildResult(status=str(getattr(res, "status", "?")),
                      assumptions=assumptions, warnings=warn,
                      solve_ms=round(1000 * (time.time() - t0)),
                      tradeoffs=list(getattr(res, "tradeoffs", []) or []))
    plan = getattr(res, "plan", None)
    if plan is None:
        out.infeasible_groups = list(getattr(res, "infeasible_groups", []) or [])
        out.errors = [getattr(res, "explanation", None)
                      or "the brief cannot be satisfied on this site"]
        return out

    storey = design.active
    if storey is None:
        return BuildResult(status="no_storey", assumptions=assumptions,
                           errors=["the design has no storey to replace"])
    cmd = Command(op="replace_storey",
                  params={"storey_id": storey.id,
                          "reason": reason or "solved from the brief"},
                  source=source,
                  description=reason or "Lay out the floor from the brief")
    res_apply = doc.apply(cmd, payload=plan)
    if not res_apply.ok:
        out.errors = list(res_apply.errors)
        return out

    out.ok = True
    out.event = res_apply.event
    # `doc.apply` REPLACES `doc.design` -- the applier never mutates in place,
    # so the `design` captured at the top of this function is the pre-solve
    # object and its active storey is still empty. Validating that reported
    # GEO.NO_ROOMS and a missing-room error for every room in the brief, on a
    # plan that had just solved OPTIMAL.
    solved = doc.design.active
    # Validated against the brief it was solved from, not against nothing.
    # See `spec_to_brief`: passing None silenced the whole BRIEF family and
    # left `check_bylaws` applying plot rules to apartment units.
    out.findings = validate_plan(solved, brief=spec_to_brief(sp),
                                 profile=BENGALURU)
    out.brief = spec_to_brief(sp)
    return out


def report(r: BuildResult) -> str:
    """A build result as text for the model, and through it for the user."""
    lines: list[str] = []
    if r.ok:
        lines.append(f"Solved: {r.status} in {r.solve_ms} ms. "
                     f"{r.n_errors} rule error(s), {r.n_warnings} warning(s).")
    else:
        lines.append(f"Not solved: {r.status}.")
        for e in r.errors:
            lines.append(f"  {e}")
        if r.infeasible_groups:
            lines.append("  Constraints that cannot hold at once: "
                         + ", ".join(str(g) for g in r.infeasible_groups))
    for w in r.warnings:
        lines.append(f"  note: {w}")
    for t in r.tradeoffs:
        lines.append(f"  trade-off: {t}")
    if r.assumptions:
        lines.append("")
        lines.append(summarise(r.assumptions))
        lines.append("Tell the user what was assumed. They cannot see this list.")
    return "\n".join(lines)
