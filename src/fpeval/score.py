"""Run a suite example and grade it against ground truth.

Two tracks, deliberately separate:

  A  truth -> programme -> envelope -> solver -> validator      (no LLM, free)
  B  prompt -> extract_spec -> programme -> envelope -> solver   (full pipeline)

Running only B conflates engine failures with extraction failures, which is the
fastest way to spend a week tuning a prompt to fix a solver bug.

Grading never compares geometry to a reference plan: there is no single correct
answer to "3BHK on a 30x40", so similarity scoring would penalise good
alternatives. It checks recovery, feasibility, programme fit and compliance.
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from typing import Any

from . import roomtypes as rt
from .bridge import truth_to_programme, spec_to_programme, SQFT_M2
from .bylaws import BENGALURU
from .envelope import compute_envelope, CityProfileAdapter
from .solver import solve_layout, LayoutSpec
from .rules import validate as validate_plan

PROFILE = CityProfileAdapter(BENGALURU)


class _UnitProfile:
    """A profile for an apartment unit: no setbacks, no coverage cap, no FAR.

    A unit is not a site. There is nothing to set back from, no ground to cover,
    and no floor-area ratio to respect -- those all belong to the tower, and its
    developer already satisfied them. Handing the plot profile to a unit would
    manufacture violations out of rules that do not apply.
    """
    name = "apartment_unit"

    def rules_for(self, *, plot_area_sqft: float, width_mm: int,
                  depth_mm: int):
        from .envelope import PlotRules
        return PlotRules(front_mm=0, rear_mm=0, side_left_mm=0, side_right_mm=0,
                         far=99.0, coverage=1.0, max_floors=1,
                         profile="apartment_unit", band="unit", verified=False,
                         rule_text={"note": "unit bounded by its quoted area, not by a site"})


_UNIT_PROFILE = _UnitProfile()


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class Result:
    example_id: str
    track: str
    status: str = "not_run"
    checks: list[Check] = field(default_factory=list)
    n_rooms: int = 0
    n_errors: int = 0
    n_warnings: int = 0
    vastu_score: float | None = None
    coverage: float | None = None
    carpet_sqft: float | None = None
    solve_ms: int = 0
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    plan: Any = None

    @property
    def passed(self) -> bool:
        return all(c.ok for c in self.checks) and self.error is None

    @property
    def score(self) -> float:
        return sum(c.ok for c in self.checks) / len(self.checks) if self.checks else 0.0


# Indian loading factors run ~1.25-1.75 super-built-up over carpet; the internal
# wall footprint alone is ~12-18% of carpet. Only the latter matters here: we need
# the gross rectangle the unit occupies, not the share of lobbies it is sold with.
CARPET_TO_GROSS = 1.15
# Median aspect of the builder unit plans ingested (Brigade, Prestige, Godrej,
# Divyasree) -- units are notably deeper than square because towers are slab-form.
UNIT_ASPECT = 1.45


def unit_envelope_ft(truth) -> tuple[float, float] | None:
    """Apartment unit -> an equivalent rectangle in feet.

    A unit has no plot, so there is nothing to set back from and no FAR to cap.
    What bounds it is the quoted area. Preferring carpet because it is the figure
    that actually describes the enclosed space; super-built-up includes a share of
    corridors and lobbies that are not in this drawing at all.
    """
    q = getattr(truth, "area_quote_sqft", {}) or {}
    carpet = q.get("rera_carpet_sqft") or q.get("carpet_sqft")
    if carpet:
        gross = carpet * CARPET_TO_GROSS
    else:
        gross_q = q.get("builtup_sqft") or q.get("super_builtup_sqft") or q.get("saleable_sqft")
        if not gross_q:
            return None
        # Back out carpet from a gross quote at the default loading factor, then
        # re-inflate for walls. Flagged as approximate wherever it is used.
        gross = gross_q / 1.40 * CARPET_TO_GROSS
    import math
    d = math.sqrt(gross * UNIT_ASPECT)
    w = gross / d
    return (round(w, 1), round(d, 1))


def _rescale_to_budget(prog, stmt) -> None:
    # NOTE: the field is `budgets`. It was `room_budgets` here for three suite
    # passes, which silently disabled rescaling: every plan was solved against
    # nominal room-type midpoints instead. Large plots absorbed it; a 20x30 got a
    # 70.9 m2 programme against a 40.7 m2 envelope and reported INFEASIBLE.
    # getattr with a default hid the typo, so assert instead.
    """Replace nominal targets with the envelope's proportional budget.

    Room-type midpoints are generous by design (a bedroom range of 9-20 m2 gives
    14.5), and six of those exceed a 30x40's 83.6 m2 footprint. The envelope
    already allocates the available carpet area proportionally, so use it.
    """
    assert hasattr(stmt, "budgets"), "AreaStatement lost its `budgets` field"
    budgets = {b.id: b for b in (stmt.budgets or [])}
    for r in prog:
        b = budgets.get(r.id)
        if b is not None and getattr(b, "budget_m2", 0):
            r.target_m2 = round(float(b.budget_m2), 2)


def run(example, *, track: str = "A", client=None, time_limit_s: float = 12.0,
        spec: Any = None, policy=None) -> Result:
    res = Result(example_id=example.id, track=track)
    t = example.truth

    # ---- clarify examples never reach the solver -------------------------
    if example.expect == "clarify":
        if track == "A":
            res.status = "skipped"
            res.checks.append(Check("clarify_needs_llm", True,
                                    "clarification is an extraction behaviour; track A cannot test it"))
            return res

    # ---- apartment units: solve inside the quoted area, no setbacks/FAR ---
    is_unit = (t.site_kind or "plot") == "apartment_unit"
    unit_dims = unit_envelope_ft(t) if is_unit else None
    if is_unit and unit_dims is None:
        res.status = "skipped"
        res.checks.append(Check("unit_area_quoted", example.expect == "clarify",
                                "unit has no quoted area to bound the layout"))
        return res

    if not is_unit and (t.plot_width_ft is None or t.plot_depth_ft is None):
        res.status = "skipped"
        res.checks.append(Check("plot_given", example.expect == "clarify",
                                "no plot dimensions in ground truth"))
        return res

    # Compact plots get the relaxed-width profile: a rectangular tiling cannot
    # fit a 2BHK on 20x30 at NBC's 2400 mm, and those houses exist in reality.
    plot_sqft = (t.plot_width_ft or 0) * (t.plot_depth_ft or 0)
    relaxed = bool(getattr(policy, "relaxed_minima", False)) or (
        0 < plot_sqft <= 800 and not is_unit)
    prog, warn = (spec_to_programme(spec, relaxed=relaxed)
                  if (track == "B" and spec is not None)
                  else truth_to_programme(t, relaxed=relaxed))
    res.warnings = warn
    if not prog:
        res.error = "empty programme"
        res.checks.append(Check("programme_nonempty", False, "no layable rooms"))
        return res

    facing = (t.road_facing or "n")[0].upper()
    w_ft, d_ft = (unit_dims if is_unit else (t.plot_width_ft, t.plot_depth_ft))
    t0 = time.time()
    try:
        # A unit is bounded by its quoted area, so give the envelope a profile with
        # no setbacks and full coverage rather than pretending it sits on a plot.
        prof = _UNIT_PROFILE if is_unit else PROFILE
        stmt = compute_envelope(w_ft, d_ft,
                                road_facing=facing, profile=prof, programme=prog)
        _rescale_to_budget(prog, stmt)
        from .bridge import RELAXED_MAX_ASPECT
        sr = solve_layout(
            w_ft, d_ft,
            LayoutSpec(programme=prog,
                       entrance_room=next((r.id for r in prog if r.is_entrance), prog[0].id),
                       max_aspect_hard=(RELAXED_MAX_ASPECT if relaxed else 2.8),
                       time_limit_s=time_limit_s),
            road_facing=facing, profile=prof,
            plan_id=f"{example.id}-{track}")
    except Exception as e:
        res.error = f"{type(e).__name__}: {e}"
        res.checks.append(Check("engine_ran", False, res.error))
        return res
    res.solve_ms = round(1000 * (time.time() - t0))
    res.status = str(getattr(sr, "status", "?"))
    plan = getattr(sr, "plan", None)

    # ---- feasibility outcome -------------------------------------------
    want_plan = example.expect in ("optimal", "feasible")
    got_plan = plan is not None
    res.checks.append(Check("feasibility", want_plan == got_plan,
                            f"expected {example.expect}, got {res.status}"))
    if not got_plan:
        groups = list(getattr(sr, "infeasible_groups", []) or [])
        if example.expect == "infeasible" and example.expect_reason:
            res.checks.append(Check("infeasible_reason",
                                    example.expect_reason in groups,
                                    f"expected '{example.expect_reason}', got {groups}"))
        return res

    res.plan = plan
    res.n_rooms = len(plan.rooms)
    brief = {"site_kind": "apartment_unit"} if is_unit else None
    findings = validate_plan(plan, brief=brief, profile=BENGALURU)
    errs = [f for f in findings if f.severity == "error"]
    res.n_errors, res.n_warnings = len(errs), len(findings) - len(errs)

    # ---- compliance ------------------------------------------------------
    res.checks.append(Check("no_rule_errors", not errs,
                            ", ".join(f.rule_id for f in errs[:5])))

    # ---- programme fit ---------------------------------------------------
    got: dict[str, int] = {}
    for r in plan.rooms:
        k = r.category if r.category in rt.T else "unknown"
        got[k] = got.get(k, 0) + 1
    # master_bedroom is a bedroom for counting purposes
    got["bedroom"] = got.get("bedroom", 0) + got.get("master_bedroom", 0)
    for key, n in (t.rooms or {}).items():
        res.checks.append(Check(f"count:{key}", got.get(key, 0) == n,
                                f"expected {n}, got {got.get(key, 0)}"))
    for key, n in (t.rooms_min or {}).items():
        if key in ("stair", "parking", "sitout", "balcony", "patio", "landscape", "shaft"):
            continue                                     # deferred by the bridge
        res.checks.append(Check(f"min:{key}", got.get(key, 0) >= n,
                                f"expected >={n}, got {got.get(key, 0)}"))

    # ---- bye-law -------------------------------------------------------
    cov = getattr(stmt, "proposed_coverage", None) or getattr(stmt, "coverage", None)
    if is_unit:
        cov = None                     # no plot, so coverage is meaningless
    if cov is not None:
        res.coverage = round(float(cov), 3)
        if t.coverage_max:
            res.checks.append(Check("coverage_cap", float(cov) <= t.coverage_max + 1e-6,
                                    f"{cov:.3f} vs cap {t.coverage_max}"))
    res.carpet_sqft = round(sum(r.area for r in plan.rooms) / 1e6 * SQFT_M2)

    # ---- vastu ---------------------------------------------------------
    vf = [f for f in findings if f.rule_id.startswith("VASTU")]
    if t.vastu_enabled:
        res.vastu_score = round(1.0 - len(vf) / 9.0, 3)
    return res
