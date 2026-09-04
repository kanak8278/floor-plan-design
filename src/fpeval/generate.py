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

from .spec import SQFT_PER_M2 as SQFT_M2
from .bridge import (cap_service_targets, shed_optional, spec_to_programme,
                     resolve_scenario, relational_pairs)
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
    # Scenario defaults plus what this brief actually asked for, through the
    # one helper `loop.py` and `service/app.py` also use. Solving with an empty
    # relational objective and then validating against TYPO/TOPO/ZONE rules
    # that assume one is how a plan comes back with the hall at the far end.
    sc = resolve_scenario(prog, site_kind=("apartment_unit" if unit else "plot"),
                          plot_sqft=(w_ft * d_ft) if (w_ft and d_ft) else None,
                          carpet_sqft=sp.unit_area.resolved_carpet_sqft(),
                          storeys=int(sp.storeys or 1))
    req, forb, soft = relational_pairs(prog, sc, spec=sp)
    # Pairs the typology wants as ONE space. `relational_pairs` folds them into
    # `required`, which only makes them touch; the opening between them still
    # got a 900 mm leaf and `TYPO.NOT_ACTUALLY_OPEN` fired on the result.
    from .topology import open_pairs as _open_pairs
    opens = _open_pairs(prog, sc)

    def budget(programme):
        """Envelope -> per-room target. Returns the area statement."""
        stmt = compute_envelope(w_ft, d_ft, road_facing=facing, profile=profile,
                                programme=programme)
        for r in programme:
            b = next((x for x in (stmt.budgets or []) if x.id == r.id), None)
            if b is not None and b.budget_m2:
                r.target_m2 = round(float(b.budget_m2), 2)
        return stmt

    def attempt(programme, required, forbidden, softs):
        """One solve. Extracted because the retries duplicated all of it."""
        ids = {r.id for r in programme}
        return solve_layout(
            w_ft, d_ft,
            LayoutSpec(programme=programme,
                       required_adjacency=[t for t in required
                                           if t[0] in ids and t[1] in ids],
                       open_adjacency=[t for t in opens
                                       if t[0] in ids and t[1] in ids],
                       forbidden_adjacency=[t for t in forbidden
                                            if t[0] in ids and t[1] in ids],
                       soft_adjacency=[t for t in softs
                                       if t[0] in ids and t[1] in ids],
                       entrance_room=next((r.id for r in programme
                                           if r.is_entrance), programme[0].id),
                       time_limit_s=time_limit_s),
            road_facing=facing, north_deg=sp.north_deg, profile=profile,
            plan_id=f"{design.id}-solved")

    t0 = time.time()
    try:
        stmt = budget(prog)
        # Say so when the client's own room sizes do not fit the area they
        # quoted. Silently scaling every stated room by 0.84 is the behaviour
        # that made "nothing is big enough" the commonest complaint: the brief
        # was answerable, just not at the quoted area, and nobody was told.
        stated = [r for r in prog if getattr(r, "size_stated", False)]
        asked = sum((r.min_sqft or 0) / SQFT_M2 for r in sp.rooms
                    if getattr(r, "size_stated", False))
        offered = sum(r.target_m2 for r in stated)
        if asked and offered and offered < asked * 0.97:
            warn.append(
                f"the room sizes in the brief total {asked * SQFT_M2:.0f} sqft "
                f"but this area only leaves {offered * SQFT_M2:.0f} sqft for "
                f"them, so every stated room was scaled to "
                f"{offered / asked:.0%}. Raise the area or drop a room to get "
                "the sizes you asked for")
        res = attempt(prog, req, forb, soft)
    except Exception as exc:
        return BuildResult(status="error", assumptions=assumptions, warnings=warn,
                           solve_ms=round(1000 * (time.time() - t0)),
                           errors=[f"{type(exc).__name__}: {exc}"])

    # ---- shed the nice-to-haves rather than refuse ----------------------
    # `RoomSpec.optional` was read nowhere on the solve path, so every room in
    # the brief was mandatory and an over-specified brief could only come back
    # INFEASIBLE. Dropping the optional rooms, lowest priority first, and
    # saying which is a better answer than a refusal.
    if res.plan is None and any(getattr(r, "optional", False) for r in sp.rooms):
        kept, shed = shed_optional(prog)
        if kept:
            warn.append("dropped the optional room(s) " + ", ".join(sorted(shed))
                        + " -- the programme did not fit the site with them")
            try:
                budget(kept)
                res, prog = attempt(kept, req, forb, soft), kept
            except Exception as exc:
                warn.append(f"retry without optional rooms failed: "
                            f"{type(exc).__name__}: {exc}")

    # ---- relax the targets rather than refuse ---------------------------
    # A target is a preference; only the NBC minimum is a requirement. But the
    # envelope inflates every target to consume the footprint, and a bigger
    # mandatory room is HARDER to tile inside a fixed rectangle under a hard
    # aspect bound -- so feasibility was not monotone in the envelope. Measured
    # on a real 719 sqft unit: 851 sqft of footprint solved, 857 sqft came back
    # infeasible, 871 sqft solved again. Pulling the targets back toward the
    # minima strictly enlarges the feasible set, so this cannot make things
    # worse, and it turns an "impossible" answer into a slightly smaller house.
    for pull in (0.5, 0.0):
        if res.plan is not None:
            break
        for r in prog:
            floor = r.nbc_min_area_m2()
            r.target_m2 = round(floor + pull * max(0.0, r.target_m2 - floor), 2)
        try:
            res = attempt(prog, req, forb, soft)
        except Exception as exc:
            warn.append(f"relaxed retry failed: {type(exc).__name__}: {exc}")
            break
        if res.plan is not None:
            warn.append(
                "the room sizes were pulled back toward their minimums to make "
                "the plan fit; no arrangement of the requested sizes tiles this "
                "footprint")

    # ---- declare a compact-profile deviation rather than refuse ----------
    # NBC's minima are law, and real Indian compact units are built below them:
    # Godrej Prakriti's kitchen is 1900 x 1900 mm (3.6 m2) against a 5.0 m2
    # floor, and a published 735 sqft unit has a 1.1 m2 bath. Returning
    # INFEASIBLE means we cannot draw a flat somebody already lives in.
    # `bridge` has carried a compact profile and the wording for declaring the
    # deviation since it was written, wired only into `score.py`, so the
    # product refused where the suite relaxed.
    if res.plan is None:
        from .bridge import _apply_relaxed, relaxed_note
        _apply_relaxed(prog)
        try:
            budget(prog)
            res = attempt(prog, req, forb, soft)
        except Exception as exc:
            warn.append(f"compact-profile retry failed: {type(exc).__name__}: {exc}")
        if res.plan is not None:
            warn.append(relaxed_note(prog) or "compact profile applied")

    out = BuildResult(status=str(getattr(res, "status", "?")),
                      assumptions=assumptions, warnings=warn,
                      solve_ms=round(1000 * (time.time() - t0)),
                      tradeoffs=list(getattr(res, "tradeoffs", []) or []))
    plan = getattr(res, "plan", None)
    if plan is None:
        out.infeasible_groups = list(getattr(res, "infeasible_groups", []) or [])
        # `SolveResult` names this field `message`; reading `explanation` meant
        # every infeasibility diagnostic the solver had already computed --
        # which topologies it tried and which constraint groups were jointly
        # unsatisfiable -- was thrown away and replaced by one useless
        # sentence. That is the whole reason an INFEASIBLE brief could not be
        # debugged without attaching a harness to the solver.
        out.errors = [getattr(res, "message", None)
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
        # A count is not actionable. Reporting "4 rule error(s)" and nothing
        # else meant the one moment the model could fix something -- straight
        # after the solve that caused it -- was the moment it was told least.
        from .agent import _finding_line
        bad = [f for f in r.findings if getattr(f, "severity", "") == "error"]
        warns = [f for f in r.findings if getattr(f, "severity", "") == "warn"]
        for f in bad[:12]:
            lines.append("  " + _finding_line(f).lstrip("- "))
        for f in warns[:8]:
            lines.append("  " + _finding_line(f).lstrip("- "))
        if bad:
            lines.append("  Fix the errors or tell the user why you are not "
                         "going to. Do not hand over a plan with errors "
                         "without saying so.")
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
