"""HTTP service: the only thing the browser talks to for design work.

Why a separate Python process at all: CP-SAT and shapely have no credible
in-browser equivalent (Pyodide runs shapely but not OR-Tools), so the solver must
be server-side. The split is forced; what is NOT forced is it feeling like two
systems. SvelteKit proxies /api/* to here from the same origin, so the browser
sees one app, one deploy, no CORS.

The service is deliberately STATELESS. The document lives in the browser and is
posted in with each call. That removes the whole class of "server thinks X,
client thinks Y" desync bugs, and means the editor's undo stack stays the single
source of truth for history.
"""
from __future__ import annotations
import sys, time, traceback
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from fpeval.bylaws import BENGALURU
from fpeval.envelope import compute_envelope, CityProfileAdapter, RoomReq
from fpeval.solver import solve_layout, LayoutSpec
from fpeval.rules import validate as validate_plan
from fpeval.render import render as render_svg
from fpeval.project import to_project, from_project
from fpeval import roomtypes as rt

PROFILE = CityProfileAdapter(BENGALURU)
app = FastAPI(title="fpeval design service", version="0.1.0")

# The stateful half: documents, the command log, and the chat transcript. Kept
# in its own module because the endpoints below genuinely are pure functions of
# their input, and mixing the two would blur which is which.
from service.documents import router as documents_router  # noqa: E402
app.include_router(documents_router)
# Same-origin in production via the SvelteKit proxy; permissive here so the
# editor's dev server can be pointed straight at the service while iterating.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])


# --------------------------------------------------------------------------- io
class RoomReqIn(BaseModel):
    id: str
    category: str
    name: str = ""
    target_m2: float = 10.0
    weight: float = 1.0
    vastu_zone: str | None = None
    is_entrance: bool = False


class GenerateIn(BaseModel):
    width_ft: float
    depth_ft: float
    programme: list[RoomReqIn]
    road_facing: str = "N"
    north_deg: float | None = None
    entrance_room: str | None = None
    time_limit_s: float = 12.0
    storey_height: int = 3000
    render: bool = True


class ProjectIn(BaseModel):
    project: dict[str, Any]


class RenderIn(ProjectIn):
    mode: str = "presentation"
    with_findings: bool = False


def _findings_json(fs) -> list[dict[str, Any]]:
    out = []
    for f in fs:
        out.append({
            "rule_id": getattr(f, "rule_id", None),
            "severity": getattr(f, "severity", None),
            "weight": getattr(f, "weight", None),
            "detail": getattr(f, "detail", None),
            "element_ids": list(getattr(f, "element_ids", []) or []),
            "measured": getattr(f, "measured", None),
            "required": getattr(f, "required", None),
        })
    return out


# ----------------------------------------------------------------------- routes
@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "room_types": len(rt.KEYS), "profile": "BENGALURU"}


@app.get("/api/roomtypes")
def roomtypes() -> dict[str, Any]:
    """The canonical taxonomy, so the UI never hard-codes a room list."""
    return {k: {
        "display": v.display, "klass": v.klass, "carpet": v.carpet,
        "built_up": v.built_up, "min_area_m2": v.min_area_m2,
        "min_width_mm": v.min_width_mm, "target_m2": v.target_m2,
        "vastu_zone": v.vastu_zone, "op3d_room_type": v.op3d_room_type,
    } for k, v in rt.T.items()}


@app.post("/api/generate")
def generate(body: GenerateIn) -> dict[str, Any]:
    """Programme -> solved plan -> Project JSON + findings + SVG.

    Returns `status` verbatim from the solver, including INFEASIBLE with the
    binding constraint groups. A refusal is a real answer, not an error.
    """
    t0 = time.time()
    prog = [RoomReq(id=r.id, name=r.name or r.id.title(), category=r.category,
                    target_m2=r.target_m2, weight=r.weight,
                    vastu_zone=r.vastu_zone, is_entrance=r.is_entrance)
            for r in body.programme]
    try:
        stmt = compute_envelope(body.width_ft, body.depth_ft,
                                road_facing=body.road_facing, profile=PROFILE,
                                programme=prog)
        res = solve_layout(
            body.width_ft, body.depth_ft,
            LayoutSpec(programme=prog,
                       entrance_room=body.entrance_room or (prog[0].id if prog else None),
                       time_limit_s=body.time_limit_s),
            road_facing=body.road_facing, north_deg=body.north_deg,
            profile=PROFILE, plan_id=f"api-{int(t0)}",
            storey_height=body.storey_height)
    except Exception as e:
        raise HTTPException(500, f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}")

    status = str(getattr(res, "status", "?"))
    plan = getattr(res, "plan", None)
    out: dict[str, Any] = {
        "status": status,
        "solve_ms": round(1000 * (time.time() - t0)),
        "area_statement": getattr(stmt, "to_dict", lambda: None)() if stmt else None,
    }
    if plan is None:
        out["infeasible_groups"] = list(getattr(res, "infeasible_groups", []) or [])
        out["explanation"] = getattr(res, "explanation", None) or getattr(res, "reason", None)
        return out

    findings = validate_plan(plan, brief=None, profile=BENGALURU)
    out["project"] = to_project(plan)
    out["findings"] = _findings_json(findings)
    if body.render:
        out["svg"] = render_svg(plan, "presentation")
    return out


@app.post("/api/validate")
def validate(body: ProjectIn) -> dict[str, Any]:
    """The read-back half: the browser posts its CURRENT document and gets
    exact findings. This is what lets a user edit get checked the same way a
    generated plan does."""
    try:
        plan = from_project(body.project)
    except Exception as e:
        raise HTTPException(400, f"not a readable Project: {type(e).__name__}: {e}")
    t0 = time.time()
    findings = validate_plan(plan, brief=None, profile=BENGALURU)
    return {"findings": _findings_json(findings),
            "n_errors": sum(1 for f in findings if f.severity == "error"),
            "n_warnings": sum(1 for f in findings if f.severity == "warn"),
            "validate_ms": round(1000 * (time.time() - t0)),
            "rooms": len(plan.rooms), "walls": len(plan.walls)}


@app.post("/api/render")
def render(body: RenderIn) -> dict[str, Any]:
    try:
        plan = from_project(body.project)
    except Exception as e:
        raise HTTPException(400, f"not a readable Project: {type(e).__name__}: {e}")
    findings = validate_plan(plan, brief=None, profile=BENGALURU) if body.with_findings else None
    svg = render_svg(plan, body.mode, findings=findings)
    return {"svg": svg, "mode": body.mode, "bytes": len(svg)}
