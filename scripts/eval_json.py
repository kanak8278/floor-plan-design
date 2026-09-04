"""Evaluate a Project JSON: door graph, space syntax, bathroom topology, findings.

The service already validates a posted document (`POST /api/validate`), but it
returns a flat findings list and nothing of the GRAPH it derived them from. When
a plan is wrong relationally you need to see the graph, not just the verdict —
"living is not the core" is unactionable without knowing which room is.

Usage:
    uv run python scripts/eval_json.py out/eval/3bhk_30x40.json
    uv run python scripts/eval_json.py plan.json --brief brief.json --json
"""
from __future__ import annotations
import argparse, json, sys, time
from pathlib import Path

import _bootstrap  # noqa: F401  (puts src/ on the path)

from fpeval.bylaws import BENGALURU
from fpeval.project import from_project
from fpeval import rules as R
from fpeval import syntax as SX
from fpeval import topology as TP


def load_plan(path: Path):
    doc = json.loads(path.read_text())
    # A Project may arrive bare or wrapped as {"project": {...}} the way the
    # service takes it. Accept both rather than making the caller unwrap.
    if isinstance(doc, dict) and "project" in doc and "floors" not in doc:
        doc = doc["project"]
    return from_project(doc), doc


def evaluate(plan, brief: dict | None) -> dict:
    brief = dict(brief or {})
    ctx = R._build_ctx(plan, brief, BENGALURU)
    adj = R._adj(ctx)
    meta = {r.id: (r.name, r.category or "") for r in ctx.rooms}
    entry = (ctx.entry_rooms or [None])[0]

    t0 = time.time()
    findings = R.validate(plan, brief=brief, profile=BENGALURU)
    ms = round(1000 * (time.time() - t0))

    st = SX.analyse(adj, meta, entry)
    sc = R._scenario_for(ctx)

    return {
        "rooms": len(ctx.rooms), "walls": len(plan.walls),
        "openings": len(plan.openings), "validate_ms": ms,
        "scenario": {"key": sc.key, "display": sc.display,
                     "size_band": sc.size_band, "max_depth": sc.max_depth,
                     "expects": list(sc.expects)},
        "entry_rooms": list(ctx.entry_rooms or []),
        "graph": {i: sorted(adj.get(i, ())) for i in sorted(meta)},
        "syntax": st,
        "bath_kinds": brief.get("_bath_kinds", {}),
        "meta": meta,
        "findings": findings,
        "ctx": ctx,
    }


def fmt(ev: dict) -> str:
    L: list[str] = []
    meta, st = ev["meta"], ev["syntax"]
    sc = ev["scenario"]
    L.append(f"read back: {ev['rooms']} rooms, {ev['walls']} walls, "
             f"{ev['openings']} openings   validate {ev['validate_ms']} ms")
    L.append(f"scenario: {sc['display']}  ({sc['key']}, band={sc['size_band']}, "
             f"max_depth={sc['max_depth']})")
    missing = [c for c in sc["expects"]
               if not any(cat == c for _, cat in meta.values())]
    L.append(f"programme gaps vs scenario: {', '.join(missing) or 'none'}")
    L.append(f"entry: {', '.join(ev['entry_rooms']) or '(none found)'}")

    L.append("")
    L.append("DOOR GRAPH")
    for rid, ns in ev["graph"].items():
        nm, cat = meta[rid]
        L.append(f"  {nm:<18} [{cat:<15}] -> " +
                 (", ".join(meta[n][0] for n in ns if n in meta) or "(no door)"))

    L.append("")
    L.append("SPACE SYNTAX" + ("" if st.connected else "   *** GRAPH NOT CONNECTED ***"))
    L.append(f"  {'room':<18} {'cat':<15} {'conn':>4} {'depth':>5} "
             f"{'MD':>6} {'integ':>6} {'ctrl':>6}")
    for n in sorted(st.nodes.values(), key=lambda x: -x.integration):
        L.append(f"  {n.name:<18} {n.category:<15} {n.connectivity:>4} "
                 f"{'-' if n.depth_from_entrance is None else n.depth_from_entrance:>5} "
                 f"{n.mean_depth:>6.2f} {n.integration:>6.2f} {n.control:>6.2f}")
    tg = TP.SYNTAX_TARGETS
    L.append(f"  core = {meta.get(st.core, (st.core, ''))[0]} "
             f"[{meta.get(st.core, ('', '?'))[1]}]")
    for label, got, tgt, ok in (
            ("public_score", st.public_score, tg["public_score_min"],
             st.public_score >= tg["public_score_min"]),
            ("living_relative", st.living_relative, tg["living_relative_min"],
             st.living_relative >= tg["living_relative_min"]),
            ("privacy_gradient", st.privacy_gradient, tg["privacy_gradient_max"],
             0 < st.privacy_gradient <= tg["privacy_gradient_max"])):
        L.append(f"  {label:<18} {got:>8.3f}   target {tgt:>6.2f}   "
                 f"{'ok' if ok else 'MISS'}")
    for note in st.notes:
        L.append(f"  note: {note}")

    if ev["bath_kinds"]:
        L.append("")
        L.append("BATHROOM TOPOLOGY")
        for bid, kind in ev["bath_kinds"].items():
            L.append(f"  {meta.get(bid, (bid, ''))[0]:<18} {kind}")

    fs = ev["findings"]
    L.append("")
    L.append(f"FINDINGS  {sum(1 for f in fs if f.severity == 'error')} error, "
             f"{sum(1 for f in fs if f.severity == 'warn')} warn")
    fam: dict[str, list] = {}
    for f in fs:
        fam.setdefault(f.rule_id.split(".", 1)[0], []).append(f)
    for k in sorted(fam, key=lambda k: -len(fam[k])):
        L.append(f"  --- {k} ({len(fam[k])})")
        for f in fam[k]:
            L.append(f"    [{f.severity:<5}] {f.rule_id:<32} {f.detail}")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", type=Path)
    ap.add_argument("--brief", type=Path, default=None,
                    help="JSON brief: typology, scenario, attached_bath, adjacent, ...")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    a = ap.parse_args()

    plan, _ = load_plan(a.path)
    brief = json.loads(a.brief.read_text()) if a.brief else None
    ev = evaluate(plan, brief)

    if a.json:
        st = ev["syntax"]
        print(json.dumps({
            "rooms": ev["rooms"], "scenario": ev["scenario"],
            "graph": ev["graph"], "bath_kinds": ev["bath_kinds"],
            "syntax": {"core": st.core, "connected": st.connected,
                       "public_score": st.public_score,
                       "living_relative": st.living_relative,
                       "privacy_gradient": st.privacy_gradient,
                       "nodes": {i: vars(n) for i, n in st.nodes.items()}},
            "findings": [{"rule_id": f.rule_id, "severity": f.severity,
                          "weight": f.weight, "detail": f.detail,
                          "element_ids": list(f.element_ids or [])} for f in ev["findings"]],
        }, indent=1, default=str))
    else:
        print(fmt(ev))
    return 0


if __name__ == "__main__":
    sys.exit(main())
