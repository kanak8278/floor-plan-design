"""Render every solvable suite example into the editor with a readable name.

Names matter here: `api-1764839201` tells a reviewer nothing. The label encodes
the example id, plot, facing, programme and the feature under test, so the
browser list is self-describing.
"""
from __future__ import annotations
import argparse, json, sys, time
from pathlib import Path
import _bootstrap  # noqa: F401  (path + cwd)

from fpeval.suite import load_suite
from fpeval.score import run
from fpeval.project import to_project
from fpeval.render import render as render_svg

FEATURE_LABEL = {
    "vastu": "Vastu", "parking": "parking", "gate": "gate", "garden": "garden",
    "multifloor": "G+1", "duplex": "duplex", "stairs": "stairs", "pooja": "pooja",
    "study": "study", "servant": "servant", "utility": "utility", "store": "store",
    "attached_bath": "attached baths", "corner_plot": "corner", "sitout": "sitout",
    "balcony": "balcony", "terrace": "terrace", "furniture": "furnished",
    "kitchen_layout": "kitchen", "shaft": "shafts", "dining": "dining",
    "compound_wall": "compound wall", "rwh": "RWH", "porch": "porch",
    "irregular_plot": "irregular", "budget": "budget", "family": "family",
}
FACE = {"north": "N", "south": "S", "east": "E", "west": "W"}


def label(ex, n_rooms: int) -> str:
    t = ex.truth
    bits = [ex.id]
    if t.plot_width_ft and t.plot_depth_ft:
        p = f"{t.plot_width_ft:g}x{t.plot_depth_ft:g}"
        if t.road_facing:
            p += f" {FACE.get(t.road_facing.lower(), t.road_facing[:1].upper())}"
        bits.append(p)
    beds = (t.rooms or {}).get("bedroom") or (t.rooms_min or {}).get("bedroom")
    prog = f"{beds}BHK" if beds else f"{n_rooms} rooms"
    if t.half_bhk:
        prog = f"{beds}.5BHK" if beds else prog
    if t.storeys and t.storeys > 1:
        prog += f" G+{t.storeys - 1}"
    bits.append(prog)
    extras = [FEATURE_LABEL[f] for f in ex.features if f in FEATURE_LABEL]
    # keep the label readable: three feature words at most
    seen, keep = set(), []
    for e in extras:
        if e not in seen:
            seen.add(e); keep.append(e)
    if keep:
        bits.append(", ".join(keep[:3]))
    return " · ".join(bits)


ap = argparse.ArgumentParser()
ap.add_argument("--time-limit", type=float, default=10.0)
ap.add_argument("--svg", action="store_true", help="also write SVGs for review")
ap.add_argument("--set", default="paired")
args = ap.parse_args()

STATIC = Path("vendor/openPlan3D/static/fpeval")
STATIC.mkdir(parents=True, exist_ok=True)
SVGDIR = Path("out/suite_svg"); SVGDIR.mkdir(parents=True, exist_ok=True)

out, skipped, failed = [], [], []
_all = load_suite()
if args.set == "paired":
    import json as _j
    _sel = set(_j.loads(Path("suite/SELECTION.json").read_text())["general_50"])
    _all = [e for e in _all if e.id in _sel or e.id.startswith("det-")]
for ex in _all:
    r = run(ex, track="A", time_limit_s=args.time_limit)
    if r.plan is None:
        (skipped if r.status == "skipped" else failed).append((ex.id, r.status))
        continue
    pj = to_project(r.plan)
    pj["id"] = f"fpeval-sx-{ex.id}"
    pj["name"] = label(ex, r.n_rooms)
    pj["description"] = ex.prompt[:220]
    pj["_meta"] = {
        "example_id": ex.id, "expect": ex.expect, "status": r.status,
        "score": round(r.score, 3), "passed": r.passed,
        "rooms": r.n_rooms, "walls": len(r.plan.walls),
        "openings": len(r.plan.openings),
        "errors": r.n_errors, "warnings": r.n_warnings,
        "coverage": r.coverage, "carpet_sqft": r.carpet_sqft,
        "vastu": r.vastu_score, "features": ex.features,
        "failed_checks": [c.name for c in r.checks if not c.ok],
        "prompt": ex.prompt,
    }
    out.append(pj)
    if args.svg:
        (SVGDIR / f"{ex.id}.svg").write_text(render_svg(r.plan, "presentation"))

(STATIC / "suite.json").write_text(json.dumps(out))
print(f"exported {len(out)} named projects -> {STATIC/'suite.json'}")
print(f"skipped {len(skipped)} (apartment/clarify), failed to solve {len(failed)}")
if failed:
    print("  did not solve:", ", ".join(f"{i}({s})" for i, s in failed[:14]))
print("\nsample names:")
for p in out[:12]:
    m = p["_meta"]
    print(f"  {p['name']}   [{'PASS' if m['passed'] else 'FAIL'} {m['score']:.2f}]")
