"""Run the ResPlan -> IR -> Project -> IR pipeline over the corpus and report.

Usage
-----
  uv run --with shapely --with numpy python tests/run_corpus.py [N] [options]

  N                 number of plans (default 500; `all` or 0 = all 17,000)
  --pkl PATH        override the corpus location (default: data/ResPlan.pkl)
  --workers K       fan out across K processes (default: os.cpu_count())
  --ab              A/B the wall-fragmentation fix (linemerge/no-align vs fixed)
  --sweep-align     sweep the axis-alignment tolerance and stop
  --adjacency M     also check adjacency vs ResPlan's plan_to_graph on M plans
                    (needs --with networkx --with opencv-python-headless
                     --with matplotlib)
  --corridor M      quantify unlabelled space on M plans
  --manifest        write out/eval_corpus/ + tests/eval_corpus.json
  --manifest-n K    size of the persisted eval corpus (default 2000)
  --json PATH       dump the full report as JSON
"""
from __future__ import annotations
import json
import os
import pickle
import sys
import time
import warnings
from concurrent.futures import ProcessPoolExecutor

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from fpeval.resplan import convert                                  # noqa: E402
from fpeval.project import to_project, from_project                 # noqa: E402
from fpeval import metrics as M                                     # noqa: E402

# The converter must be warning-free. `oriented_envelope`'s divide-by-zero on
# axis-aligned rectangles was the only source and it is gone, so promote any
# numeric warning to an error rather than hiding it behind a filter.
warnings.simplefilter("error", RuntimeWarning)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(REPO, "out", "eval_corpus")
MANIFEST = os.path.join(REPO, "tests", "eval_corpus.json")
SCHEMA_VERSION = 2


# ---------------------------------------------------------------- worker

def _one(args) -> dict:
    """Convert + round-trip one plan. Returns a flat record (picklable)."""
    raw, opts = args
    pid = raw.get("id")
    rec: dict = {"id": pid, "ok": False}
    t0 = time.perf_counter()
    try:
        ir = convert(raw, merge_collinear=opts["merge"], align_mm=opts["align"])
    except Exception as e:
        rec["fail"] = "convert:" + type(e).__name__
        rec["msg"] = str(e)[:120]
        return rec
    try:
        proj = to_project(ir)
        blob = json.dumps(proj)
        ir2 = from_project(json.loads(blob))
    except Exception as e:
        rec["fail"] = "project:" + type(e).__name__
        rec["msg"] = str(e)[:120]
        return rec
    rec["convert_ms"] = 1000.0 * (time.perf_counter() - t0)
    rec["json_bytes"] = len(blob)

    try:
        idr = M.ir_identity(ir, ir2)
        rec.update({"id_walls": idr["walls_equal"], "id_openings": idr["openings_equal"],
                    "id_rooms": idr["rooms_equal"], "id_plot": idr["plot_equal"]})
        if not all((idr["walls_equal"], idr["openings_equal"],
                    idr["rooms_equal"], idr["plot_equal"])):
            rec["fail"] = "identity"
            return rec

        ws = M.wall_stats(ir)
        rec.update({"n_walls": ws["n_walls"], "n_rooms": ws["n_rooms"],
                    "n_openings": len(ir.openings),
                    "wall_len_median": ws["len_median_mm"],
                    "wall_len_p10": ws["len_p10_mm"],
                    "walls_under_400": ws["n_under_400mm"],
                    "walls_under_1000": ws["n_under_1000mm"]})

        og = M.opening_geometry(ir)
        rec["geom_clean"] = og["clean"]
        rec["geom"] = {k: v for k, v in og.items() if isinstance(v, int) and v}
        if not og["clean"]:
            rec["fail"] = "opening_geometry"
            return rec

        rfm = M.room_face_match(ir)
        rec.update({"all_matched": rfm.get("all_matched"),
                    "n_faces": rfm.get("n_faces"),
                    "matched": rfm.get("matched"),
                    "extra_faces": rfm.get("extra_faces"),
                    "min_room_iou": rfm.get("min_iou"),
                    "unmatched_face_area": rfm.get("unmatched_face_area_m2", 0.0)})

        fr = M.face_recovery(ir)
        if fr.get("ok"):
            rec["face_area_iou"] = fr["area_iou"]
            rec["face_count_exact"] = fr["face_count_exact"]

        sf = M.source_fidelity(raw, ir)
        if sf.get("ok"):
            rec.update({"src_iou": sf["area_iou"], "src_area_err": sf["total_area_err"],
                        "room_count_eq": sf["room_count_equal"]})

        oh = M.opening_hosting(ir)
        rec["host"] = {k: (v["total"], v["hosted"]) for k, v in oh["by_kind"].items()}
        rec["host_total"] = oh["total"]
        rec["host_hosted"] = oh["hosted"]
        rec["via_chord"] = oh["via_chord"]
        rec["via_fallback"] = oh["via_fallback"]
        rec["unhosted"] = oh["unhosted"]
        rec["degenerate"] = {k: v for k, v in M.degenerate_counters(ir).items() if v}
    except Exception as e:
        rec["fail"] = "metrics:" + type(e).__name__
        rec["msg"] = str(e)[:120]
        return rec

    rec["ok"] = True
    return rec


def _corridor(args) -> dict:
    raw, _ = args
    try:
        ir = convert(raw)
        us = M.unlabelled_space(raw, ir)
        rfm = M.room_face_match(ir)
    except Exception as e:
        return {"id": raw.get("id"), "ok": False, "err": type(e).__name__}
    if not us.get("ok"):
        return {"id": raw.get("id"), "ok": False, "err": us.get("reason")}
    return {"id": raw.get("id"), "ok": True,
            "frac": us["unlabelled_frac"], "area": us["unlabelled_area_m2"],
            "ncomp": us["n_components"], "corridor": us["any_corridor_like"],
            "comps": us["components"][:3],
            "extra_faces": rfm.get("extra_faces", 0),
            "unmatched_area": rfm.get("unmatched_face_area_m2", 0.0),
            "n_rooms": rfm.get("n_rooms", 0)}


def _adjacency(args) -> dict:
    raw, _ = args
    try:
        ir = convert(raw)
        return {"id": raw.get("id"), **M.adjacency_agreement(raw, ir)}
    except Exception as e:
        return {"id": raw.get("id"), "ok": False, "err": type(e).__name__ + ":" + str(e)[:80]}


def _fan(fn, plans, opts, workers):
    payload = [(p, opts) for p in plans]
    if workers <= 1:
        return [fn(a) for a in payload]
    chunk = max(1, len(payload) // (workers * 8))
    with ProcessPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(fn, payload, chunksize=chunk))


# ---------------------------------------------------------------- reporting

def pct(n, d):
    return f"{100 * n / max(d, 1):.2f}%"


def q(a, p):
    a = [x for x in a if x is not None and np.isfinite(x)]
    return float(np.percentile(a, p)) if len(a) else float("nan")


def report(recs, elapsed, label, n_req) -> dict:
    ok = [r for r in recs if r.get("ok")]
    n = len(recs)
    fails: dict[str, list] = {}
    for r in recs:
        if not r.get("ok"):
            fails.setdefault(r.get("fail", "unknown"), []).append(r)

    g = lambda k: [r[k] for r in ok if k in r and r[k] is not None]   # noqa: E731
    nw, nr, no = g("n_walls"), g("n_rooms"), g("n_openings")
    print(f"\n{'=' * 74}\n{label}: {n} plans, {len(ok)} clean ({pct(len(ok), n)}), "
          f"{elapsed:.1f}s wall clock, {1000 * elapsed / max(n, 1):.2f} ms/plan\n{'=' * 74}")

    print("\n--- failure taxonomy ---")
    if not fails:
        print("  none")
    for mode, rs in sorted(fails.items(), key=lambda kv: -len(kv[1])):
        ids = [r["id"] for r in rs[:6]]
        msg = rs[0].get("msg", "")
        print(f"  {mode:28s} {len(rs):6d}  {pct(len(rs), n):>7s}  e.g. {ids}"
              + (f"  [{msg}]" if msg else ""))

    print("\n--- IR -> Project -> json -> IR identity ---")
    for k, lbl in (("id_walls", "walls"), ("id_openings", "openings"),
                   ("id_rooms", "rooms"), ("id_plot", "plot")):
        vals = [r[k] for r in recs if k in r]
        print(f"  {lbl:9s} exactly equal: {sum(vals)}/{len(vals)}  {pct(sum(vals), len(vals))}")

    print("\n--- room -> face recovery (primary soundness metric) ---")
    am = g("all_matched")
    mi = g("min_room_iou")
    print(f"  every labelled room maps 1:1 to a face at IoU>0.99: {pct(sum(am), len(am))}")
    print(f"  worst room IoU per plan: p1={q(mi,1):.4f} p10={q(mi,10):.4f} median={q(mi,50):.4f}")
    fai = g("face_area_iou")
    print(f"  aggregate face area IoU: p10={q(fai,10):.4f} median={q(fai,50):.4f} p90={q(fai,90):.4f}")
    print(f"  IoU > 0.99: {pct(sum(1 for x in fai if x > 0.99), len(fai))}")
    ef = g("extra_faces")
    print(f"  extra faces/plan (unlabelled space, NOT an error): "
          f"mean={np.mean(ef) if ef else float('nan'):.3f} zero={pct(sum(1 for x in ef if x == 0), len(ef))} max={max(ef) if ef else 0}")
    fce = g("face_count_exact")
    print(f"  [legacy] face count == room count: {pct(sum(fce), len(fce))}")

    print("\n--- fidelity vs original ResPlan geometry ---")
    si, sa = g("src_iou"), g("src_area_err")
    rce = g("room_count_eq")
    print(f"  room count equal: {pct(sum(rce), len(rce))}")
    print(f"  area IoU: p1={q(si,1):.4f} p10={q(si,10):.4f} median={q(si,50):.4f}")
    print(f"  total area error: median={100*q(sa,50):.3f}%  p90={100*q(sa,90):.3f}%  p99={100*q(sa,99):.3f}%")

    print("\n--- opening hosting ---")
    tot = sum(r.get("host_total", 0) for r in ok)
    hos = sum(r.get("host_hosted", 0) for r in ok)
    print(f"  overall: {hos}/{tot} = {pct(hos, tot)}")
    bk: dict[str, list[int]] = {}
    for r in ok:
        for kind, (t, h) in (r.get("host") or {}).items():
            d = bk.setdefault(kind, [0, 0])
            d[0] += t; d[1] += h
    for kind in sorted(bk):
        t, h = bk[kind]
        print(f"    {kind:12s} {h:7d}/{t:<7d} {pct(h, t):>8s}   unhosted {t-h}")
    vc = sum(r.get("via_chord", 0) for r in ok)
    vf = sum(r.get("via_fallback", 0) for r in ok)
    print(f"  host rule: centreline chord {vc} ({pct(vc, hos)}), proximity fallback {vf} ({pct(vf, hos)})")
    ur: dict[str, list] = {}
    for r in ok:
        for u in r.get("unhosted") or []:
            ur.setdefault(f"{u['kind']}/{u['reason']}", []).append(r["id"])
    for k in sorted(ur, key=lambda k: -len(ur[k])):
        print(f"    unhosted {k:38s} {len(ur[k]):5d}  e.g. {sorted(set(ur[k]))[:8]}")

    print("\n--- wall-count distribution ---")
    print(f"  walls/plan : p10={q(nw,10):.0f} median={q(nw,50):.0f} p90={q(nw,90):.0f} p99={q(nw,99):.0f} max={max(nw) if nw else 0}")
    print(f"  rooms/plan : median={q(nr,50):.0f} p90={q(nr,90):.0f}")
    print(f"  openings/plan: median={q(no,50):.0f} p90={q(no,90):.0f}")
    if nw and nr:
        wpr = [a / b for a, b in zip(nw, nr) if b]
        print(f"  walls per room: median={q(wpr,50):.2f} p90={q(wpr,90):.2f}")
    wlm = g("wall_len_median")
    print(f"  per-plan median wall length: median={q(wlm,50):.0f} mm  p10={q(wlm,10):.0f} mm")
    u4 = g("walls_under_400")
    print(f"  walls under 400 mm: total={sum(u4)} ({pct(sum(u4), sum(nw))} of all walls); "
          f"per plan median={q(u4,50):.0f} p90={q(u4,90):.0f}")

    print("\n--- degenerate-geometry counters (whole run) ---")
    dg: dict[str, int] = {}
    for r in ok:
        for k, v in (r.get("degenerate") or {}).items():
            dg[k] = dg.get(k, 0) + v
    if not dg:
        print("  all zero")
    for k in sorted(dg, key=lambda k: -dg[k]):
        print(f"  {k:26s} {dg[k]}")

    cm = g("convert_ms")
    jb = g("json_bytes")
    print("\n--- throughput ---")
    print(f"  convert+roundtrip CPU: median={q(cm,50):.2f} ms  p90={q(cm,90):.2f} ms  mean={np.mean(cm) if cm else float('nan'):.2f} ms")
    print(f"  Project JSON size: median={q(jb,50)/1024:.1f} KiB  p90={q(jb,90)/1024:.1f} KiB")
    print(f"  end-to-end incl. all metrics: {1000 * elapsed / max(n, 1):.2f} ms/plan ({elapsed:.1f}s for {n})")

    return {
        "label": label, "n_requested": n_req, "n_run": n, "n_ok": len(ok),
        "elapsed_s": elapsed, "ms_per_plan": 1000 * elapsed / max(n, 1),
        "failures": {k: {"count": len(v), "example_ids": [r["id"] for r in v[:10]],
                         "example_msg": v[0].get("msg", "")} for k, v in fails.items()},
        "identity_all_equal": all(
            all(r.get(k, True) for k in ("id_walls", "id_openings", "id_rooms", "id_plot"))
            for r in ok),
        "room_face_all_matched_rate": (sum(am) / len(am)) if am else None,
        "walls_median": q(nw, 50), "walls_p90": q(nw, 90),
        "openings_hosted": hos, "openings_total": tot,
        "hosting_by_kind": {k: {"total": v[0], "hosted": v[1]} for k, v in bk.items()},
        "src_area_err_median": q(sa, 50), "src_area_err_p90": q(sa, 90),
        "degenerate": dg,
        "unhosted_reasons": {k: sorted(set(v)) for k, v in ur.items()},
    }


# ---------------------------------------------------------------- extras

def corridor_report(plans, workers) -> dict:
    print(f"\n{'=' * 74}\nCORRIDOR HYPOTHESIS: is unlabelled circulation the cause of "
          f"extra faces?\n{'=' * 74}")
    t0 = time.time()
    recs = [r for r in _fan(_corridor, plans, {}, workers) if r.get("ok")]
    fr = np.array([r["frac"] for r in recs])
    ef = np.array([r["extra_faces"] for r in recs], dtype=float)
    ua = np.array([r["unmatched_area"] for r in recs])
    nc = np.array([r["ncomp"] for r in recs])
    corr = np.array([r["corridor"] for r in recs])
    n = len(recs)
    print(f"  n={n} plans, {time.time()-t0:.1f}s")
    print(f"  unlabelled fraction of `inner`: median={100*np.median(fr):.4f}% "
          f"mean={100*fr.mean():.4f}% p90={100*q(fr,90):.4f}% p99={100*q(fr,99):.3f}% max={100*fr.max():.2f}%")
    print(f"  plans with ANY unlabelled component >= 1 wall^2: {pct((nc>0).sum(), n)}")
    print(f"  plans with >1% of `inner` unlabelled : {pct((fr>0.01).sum(), n)}")
    print(f"  plans with >5% of `inner` unlabelled : {pct((fr>0.05).sum(), n)}")
    print(f"  plans with a corridor-shaped void (<=2 m wide, touching >=3 rooms): {pct(corr.sum(), n)}")
    print(f"  extra faces/plan: mean={ef.mean():.3f} zero={pct((ef==0).sum(), n)} max={ef.max():.0f}")
    if ef.std() > 0 and fr.std() > 0:
        print(f"  corr(extra_faces, unlabelled_frac)      = {np.corrcoef(ef, fr)[0,1]:+.3f}")
    if ua.std() > 0 and fr.std() > 0:
        print(f"  corr(unmatched_face_area, unlabelled_frac) = {np.corrcoef(ua, fr)[0,1]:+.3f}")
    if (ef > 0).any() and (ef == 0).any():
        print(f"  unlabelled frac | has extra faces (n={int((ef>0).sum())}): median={100*np.median(fr[ef>0]):.4f}%")
        print(f"  unlabelled frac | no extra faces  (n={int((ef==0).sum())}): median={100*np.median(fr[ef==0]):.4f}%")
    big = sorted(recs, key=lambda r: -r["frac"])[:6]
    print("  largest unlabelled voids:")
    for r in big:
        print(f"    plan {r['id']}: {100*r['frac']:.1f}% of inner, {r['area']:.1f} m^2, "
              f"{r['ncomp']} comps, extra_faces={r['extra_faces']}, comps={r['comps']}")
    return {"n": n, "unlabelled_frac_median": float(np.median(fr)),
            "unlabelled_frac_mean": float(fr.mean()),
            "plans_with_void_rate": float((nc > 0).mean()),
            "plans_over_1pct": float((fr > 0.01).mean()),
            "plans_over_5pct": float((fr > 0.05).mean()),
            "corridor_shaped_rate": float(corr.mean()),
            "extra_faces_mean": float(ef.mean()),
            "corr_extra_faces_vs_unlabelled": (float(np.corrcoef(ef, fr)[0, 1])
                                               if ef.std() > 0 and fr.std() > 0 else None)}


def adjacency_report(plans, workers) -> dict:
    print(f"\n{'=' * 74}\nADJACENCY vs ResPlan's own plan_to_graph\n{'=' * 74}")
    t0 = time.time()
    recs = _fan(_adjacency, plans, {}, workers)
    good = [r for r in recs if r.get("ok")]
    errs: dict[str, int] = {}
    for r in recs:
        if not r.get("ok"):
            errs[str(r.get("err"))[:60]] = errs.get(str(r.get("err"))[:60], 0) + 1
    tp = sum(r["tp"] for r in good); fp = sum(r["fp"] for r in good); fn = sum(r["fn"] for r in good)
    prec = tp / max(tp + fp, 1); rec = tp / max(tp + fn, 1)
    f1 = 2 * prec * rec / max(prec + rec, 1e-9)
    print(f"  n={len(good)}/{len(recs)} plans, {time.time()-t0:.1f}s" + (f"  errors={errs}" if errs else ""))
    print(f"  micro over door/window edges: TP={tp} FP={fp} FN={fn}")
    print(f"    precision={prec:.4f} recall={rec:.4f} F1={f1:.4f}")
    exact = sum(1 for r in good if r["fn"] == 0 and r["fp"] == 0)
    print(f"  plans with an exactly identical walkable edge set: {pct(exact, len(good))}")
    jac = [r["jaccard"] for r in good if r["jaccard"] == r["jaccard"]]
    print(f"  per-plan Jaccard (all ref edge types): median={q(jac,50):.4f} p10={q(jac,10):.4f}")
    bt: dict[str, list[int]] = {}
    for r in good:
        for t, v in r["recall_by_ref_type"].items():
            d = bt.setdefault(t, [0, 0]); d[0] += v["n"]; d[1] += v["hit"]
    print("  recall by plan_to_graph edge type (its real types, not the paper's):")
    for t in sorted(bt, key=lambda t: -bt[t][0]):
        nn, hh = bt[t]
        print(f"    {t:14s} {hh:7d}/{nn:<7d} {pct(hh, nn):>8s}")
    oa = [r["outside_agreement"] for r in good]
    hit = sum(a for a, _ in oa); tot = sum(b for _, b in oa)
    print(f"  front-door -> room agreement (rooms reachable from outside): {pct(hit, tot)}")
    print(f"  openings whose two sides could not be resolved: {sum(r['unresolved_openings'] for r in good)}")
    print(f"  plan_to_graph nodes not mappable to an IR room: {sum(r['unmapped_ref_nodes'] for r in good)}")
    return {"n": len(good), "tp": tp, "fp": fp, "fn": fn,
            "precision": prec, "recall": rec, "f1": f1,
            "exact_edge_set_rate": exact / max(len(good), 1),
            "recall_by_type": {t: {"n": v[0], "hit": v[1]} for t, v in bt.items()},
            "errors": errs}


def ab_report(plans, workers) -> dict:
    print(f"\n{'=' * 74}\nA/B: wall fragmentation fix\n{'=' * 74}")
    out = {}
    variants = [
        ("before: linemerge, no axis alignment", {"merge": False, "align": 0.0}),
        ("collinear interval merge only", {"merge": True, "align": 0.0}),
        ("axis alignment only", {"merge": False, "align": 75.0}),
        ("after: both (shipping default)", {"merge": True, "align": 75.0}),
    ]
    print(f"  {'variant':40s} {'walls med':>9} {'p90':>5} {'<400mm':>7} {'roomIoU>.99':>11} "
          f"{'faceIoU med':>11} {'areaErr med':>11} {'p90':>7} {'hosted':>8}")
    for label, opts in variants:
        recs = [r for r in _fan(_one, plans, opts, workers) if r.get("ok")]
        nw = [r["n_walls"] for r in recs]
        sh = sum(r["walls_under_400"] for r in recs)
        am = [r["all_matched"] for r in recs]
        fi = [r["face_area_iou"] for r in recs if "face_area_iou" in r]
        ae = [r["src_area_err"] for r in recs if "src_area_err" in r]
        ht = sum(r["host_total"] for r in recs); hh = sum(r["host_hosted"] for r in recs)
        print(f"  {label:40s} {q(nw,50):9.0f} {q(nw,90):5.0f} {pct(sh, sum(nw)):>7s} "
              f"{pct(sum(am), len(am)):>11s} {q(fi,50):11.4f} {100*q(ae,50):10.3f}% "
              f"{100*q(ae,90):6.3f}% {pct(hh, ht):>8s}")
        out[label] = {"walls_median": q(nw, 50), "walls_p90": q(nw, 90),
                      "walls_under_400_rate": sh / max(sum(nw), 1),
                      "room_face_matched_rate": sum(am) / max(len(am), 1),
                      "face_iou_median": q(fi, 50),
                      "src_area_err_median": q(ae, 50), "src_area_err_p90": q(ae, 90),
                      "hosting_rate": hh / max(ht, 1)}
    return out


def sweep_align(plans, workers) -> None:
    print(f"\n{'=' * 74}\nSWEEP: axis-alignment tolerance\n{'=' * 74}")
    print(f"  {'mm':>5} {'walls med':>9} {'p90':>5} {'<400mm':>7} {'roomIoU>.99':>11} "
          f"{'extraF=0':>9} {'areaErr med':>11} {'p90':>7} {'p99':>7} {'hosted':>8}")
    for al in (0.0, 30.0, 45.0, 60.0, 75.0, 90.0, 113.0, 150.0, 226.0):
        recs = [r for r in _fan(_one, plans, {"merge": True, "align": al}, workers)
                if r.get("ok")]
        nw = [r["n_walls"] for r in recs]
        sh = sum(r["walls_under_400"] for r in recs)
        am = [r["all_matched"] for r in recs]
        ef = [r["extra_faces"] for r in recs]
        ae = [r["src_area_err"] for r in recs if "src_area_err" in r]
        ht = sum(r["host_total"] for r in recs); hh = sum(r["host_hosted"] for r in recs)
        print(f"  {al:5.0f} {q(nw,50):9.0f} {q(nw,90):5.0f} {pct(sh, sum(nw)):>7s} "
              f"{pct(sum(am), len(am)):>11s} {pct(sum(1 for x in ef if x==0), len(ef)):>9s} "
              f"{100*q(ae,50):10.3f}% {100*q(ae,90):6.3f}% {100*q(ae,99):6.3f}% {pct(hh, ht):>8s}")


# ---------------------------------------------------------------- manifest

def write_manifest(plans, recs, n_keep: int, source_pkl: str) -> dict:
    """Persist a fixed, versioned eval corpus for downstream agents.

    Size rationale: the full 17,000 Projects are ~1.1 GiB of JSON, which is not
    something four concurrent agents should each hold in memory or re-read. A
    2,000-plan slice keeps every measured rate inside +/-1.1% at 95% confidence
    (worst case p=0.5), which is finer than any decision we make off these
    numbers, and lands at ~130 MiB. The slice is the first `n_keep` clean plans
    in file order -- deterministic, and file order is already unrelated to plan
    content, so no extra shuffling is needed to avoid bias.
    """
    import hashlib
    os.makedirs(OUT_DIR, exist_ok=True)
    byid = {p.get("id"): p for p in plans}
    keep = [r for r in recs if r.get("ok")][:n_keep]

    projects = []
    entries = []
    for r in keep:
        raw = byid[r["id"]]
        ir = convert(raw)
        proj = to_project(ir)
        proj["_expect"] = {
            "n_rooms": len(ir.rooms),
            "n_walls": len(ir.walls),
            "n_openings": len(ir.openings),
            "areas_m2": sorted(round(rm.area / 1e6, 2) for rm in ir.rooms),
            "room_categories": sorted(rm.category for rm in ir.rooms),
            "n_faces": r.get("n_faces"),
            "extra_faces": r.get("extra_faces"),
        }
        projects.append(proj)
        entries.append({
            "resplan_id": r["id"], "project_id": proj["id"],
            "n_walls": r["n_walls"], "n_rooms": r["n_rooms"],
            "n_openings": r["n_openings"], "n_faces": r.get("n_faces"),
            "all_rooms_matched": r.get("all_matched"),
            "min_room_iou": round(r.get("min_room_iou") or 0.0, 6),
            "src_area_err": round(r.get("src_area_err") or 0.0, 8),
            "openings_hosted": r.get("host_hosted"),
            "openings_total": r.get("host_total"),
        })

    proj_path = os.path.join(OUT_DIR, "projects.json")
    with open(proj_path, "w") as f:
        json.dump(projects, f)
    ir_path = os.path.join(OUT_DIR, "ir.json")
    with open(ir_path, "w") as f:
        json.dump([convert(byid[e["resplan_id"]]).to_dict() for e in entries], f)

    def sha(p):
        h = hashlib.sha256()
        with open(p, "rb") as fh:
            for b in iter(lambda: fh.read(1 << 20), b""):
                h.update(b)
        return h.hexdigest()

    from fpeval import resplan as R
    man = {
        "schema_version": SCHEMA_VERSION,
        "name": "resplan-eval-2k",
        "description": "Fixed ResPlan -> IR -> OpenPlan3D Project evaluation corpus.",
        "source_pkl": os.path.relpath(source_pkl, REPO),
        "source_plans_total": len(plans),
        "n_plans": len(entries),
        "selection": f"first {len(entries)} plans in file order that convert cleanly",
        "converter": {
            "assumed_wall_mm": R.ASSUMED_WALL_MM,
            "snap_units": R.SNAP_UNITS,
            "axis_cluster_mm": R.AXIS_CLUSTER_MM,
            "align_overlap_slack": R.ALIGN_OVERLAP_SLACK,
            "min_opening_mm": R.MIN_OPENING_MM,
            "wall_merge": "collinear_interval",
        },
        "artefacts": {
            "projects_json": {"path": "out/eval_corpus/projects.json",
                              "bytes": os.path.getsize(proj_path), "sha256": sha(proj_path),
                              "content": "OpenPlan3D Project dicts + _expect block"},
            "ir_json": {"path": "out/eval_corpus/ir.json",
                        "bytes": os.path.getsize(ir_path), "sha256": sha(ir_path),
                        "content": "canonical IR Plan dicts, same order"},
        },
        "aggregate": {
            "walls_median": q([e["n_walls"] for e in entries], 50),
            "walls_p90": q([e["n_walls"] for e in entries], 90),
            "rooms_median": q([e["n_rooms"] for e in entries], 50),
            "all_rooms_matched_rate": sum(1 for e in entries if e["all_rooms_matched"]) / max(len(entries), 1),
            "openings_hosted": sum(e["openings_total"] for e in entries) and
                               sum(e["openings_hosted"] for e in entries) / sum(e["openings_total"] for e in entries),
        },
        "plans": entries,
    }
    with open(MANIFEST, "w") as f:
        json.dump(man, f, indent=1)
    print(f"\n{'=' * 74}\nEVAL CORPUS PERSISTED\n{'=' * 74}")
    print(f"  manifest : {os.path.relpath(MANIFEST, REPO)}  ({len(entries)} plans)")
    print(f"  projects : out/eval_corpus/projects.json  {os.path.getsize(proj_path)/2**20:.1f} MiB")
    print(f"  ir       : out/eval_corpus/ir.json        {os.path.getsize(ir_path)/2**20:.1f} MiB")
    return man


# ---------------------------------------------------------------- main

def main() -> None:
    argv = sys.argv[1:]
    def opt(name, default=None, cast=str):
        if name in argv:
            return cast(argv[argv.index(name) + 1])
        return default
    flag = lambda name: name in argv                                  # noqa: E731

    pos = [a for a in argv if not a.startswith("--")
           and (not argv or argv.index(a) == 0 or not argv[argv.index(a) - 1].startswith("--"))]
    n_arg = pos[0] if pos else "500"
    pkl = opt("--pkl", M.default_pkl_path())
    workers = int(opt("--workers", os.cpu_count() or 4))

    plans = pickle.load(open(pkl, "rb"))
    N = len(plans) if n_arg in ("all", "0") else min(int(n_arg), len(plans))
    subset = plans[:N]
    print(f"corpus: {len(plans)} plans at {pkl}; running {N} on {workers} workers")

    out: dict = {"corpus": {"path": os.path.relpath(pkl, REPO) if pkl.startswith(REPO) else pkl,
                            "total": len(plans), "n_run": N},
                 "schema_version": SCHEMA_VERSION}

    if flag("--sweep-align"):
        sweep_align(subset, workers)
        return

    t0 = time.time()
    recs = _fan(_one, subset, {"merge": True, "align": None}, workers)
    out["main"] = report(recs, time.time() - t0, "FULL PIPELINE", N)

    if flag("--ab"):
        out["ab"] = ab_report(subset[:min(N, 4000)], workers)
    m = int(opt("--corridor", 0))
    if m:
        out["corridor"] = corridor_report(plans[:min(m, len(plans))], workers)
    m = int(opt("--adjacency", 0))
    if m:
        out["adjacency"] = adjacency_report(plans[:min(m, len(plans))], workers)
    if flag("--manifest"):
        man = write_manifest(subset, recs, int(opt("--manifest-n", 2000)), pkl)
        out["manifest"] = {k: v for k, v in man.items() if k != "plans"}

    jp = opt("--json")
    if jp:
        os.makedirs(os.path.dirname(os.path.abspath(jp)) or ".", exist_ok=True)
        with open(jp, "w") as f:
            json.dump(out, f, indent=1, default=str)
        print(f"\nreport JSON -> {jp}")


if __name__ == "__main__":
    main()
