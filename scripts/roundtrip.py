"""Image -> prompt -> plan -> compare. The reconstruction loop from ANNOTATION.md.

Track A builds the spec from the hand truth: free, and isolates engine failures
from extraction failures. Track B extracts the spec from the hand-written prompt
and costs money.

Reports the four rungs separately. One image-vs-image verdict says "no" on every
example and names nothing; a rung says which capability is missing.
"""
from __future__ import annotations
import argparse, json, os, sys, time, traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from fpeval.spec import DesignSpec, RoomSpec, AreaQuote, EntranceSpec, VastuSpec
from fpeval.document import Document
from fpeval.generate import build
from fpeval.bridge import spec_to_programme, canon
from fpeval.spatial import plan_png


# ---------------------------------------------------------------- truth -> spec

def truth_to_spec(t: dict, *, ground_only: bool = False) -> DesignSpec:
    """The hand truth as a DesignSpec: the brief a perfect extractor would emit.

    `ground_only` counts rooms from the annotated per-room `storey` instead of
    the whole-dwelling totals. It exists because `generate.build` lays out one
    storey, so a 2-storey brief otherwise asks for the whole house on the
    ground floor and fails on area budget before reaching CP-SAT.
    """
    st = t["spec_truth"]
    counts = dict(st.get("rooms") or {})
    if ground_only:
        counts = {}
        for r in t["rooms"]:
            if (r.get("storey") or 0) != 0 or not r.get("category"):
                continue
            counts[r["category"]] = counts.get(r["category"], 0) + 1
    # Real areas off the drawing, largest first, so a brief that names sizes is
    # tested the way a client's is. Without them the round trip only ever
    # measured whether the room COUNT survived.
    by_cat: dict[str, list[float]] = {}
    for r in t["rooms"]:
        if r.get("category") and r.get("area_sqft") and (
                not ground_only or (r.get("storey") or 0) == 0):
            by_cat.setdefault(r["category"], []).append(float(r["area_sqft"]))
    for v in by_cat.values():
        v.sort(reverse=True)
    rooms: list[RoomSpec] = []
    for cat, n in counts.items():
        sizes = by_cat.get(cat, [])
        for i in range(int(n)):
            rid = cat if n == 1 else f"{cat}{i+1}"
            rooms.append(RoomSpec(id=rid, category=cat, priority=1,
                                  min_sqft=sizes[i] if i < len(sizes) else None))
    ua = st.get("unit_area") or {}
    ent = st.get("entrance") or {}
    va = st.get("vastu") or {}
    return DesignSpec(
        site_kind=st.get("site_kind") or "plot",
        plot_width_ft=st.get("plot_width_ft"),
        plot_depth_ft=st.get("plot_depth_ft"),
        road_facing_side=st.get("road_facing_side"),
        north_deg=st.get("north_deg") or 0.0,
        storeys=int(st.get("storeys") or 1),
        half_bhk=bool(st.get("half_bhk")),
        unit_label=st.get("unit_label") or "",
        unit_area=AreaQuote(**{k: v for k, v in ua.items()
                               if k in AreaQuote.__dataclass_fields__}),
        rooms=rooms,
        entrance=EntranceSpec(**{k: v for k, v in ent.items()
                                 if k in EntranceSpec.__dataclass_fields__}),
        vastu=VastuSpec(**{k: v for k, v in va.items()
                           if k in VastuSpec.__dataclass_fields__}),
        provenance={"source": "hand-truth", "example": t["id"]},
    )


# ------------------------------------------------------------------- the rungs

def rung1(spec: DesignSpec, t: dict) -> dict:
    """Can the brief be said: extracted spec vs hand truth."""
    st = t["spec_truth"]
    want = {k: int(v) for k, v in (st.get("rooms") or {}).items()}
    got: dict[str, int] = {}
    for r in spec.rooms:
        got[r.category] = got.get(r.category, 0) + 1
    miss = {k: want[k] - got.get(k, 0) for k in want if want[k] != got.get(k, 0)}
    extra = {k: v for k, v in got.items() if k not in want}
    return {
        "site_kind": [st.get("site_kind"), spec.site_kind],
        "plot": [[st.get("plot_width_ft"), st.get("plot_depth_ft")],
                 [spec.plot_width_ft, spec.plot_depth_ft]],
        "facing": [st.get("road_facing_side"), spec.road_facing_side],
        "storeys": [st.get("storeys"), spec.storeys],
        "rooms_want": want, "rooms_got": got,
        "rooms_wrong_count": miss, "rooms_not_asked_for": extra,
        "ok": not miss and not extra,
    }


def rung2(spec: DesignSpec) -> dict:
    """Can the programme be held: what the bridge drops on the way to the solver."""
    prog, warn = spec_to_programme(spec)
    asked = {r.category for r in spec.rooms}
    arrived = {r.category for r in prog}
    # The collapse is the interesting part: two brief categories landing on one
    # solver category means the plan cannot distinguish them again.
    collapse: dict[str, list[str]] = {}
    for c in sorted(asked):
        k = canon(c)
        if k != c:
            collapse.setdefault(k, []).append(c)
    return {
        "asked": sorted(asked), "arrived": sorted(arrived),
        "dropped": sorted(asked - {c for c in asked if canon(c) in arrived}),
        "warnings": warn,
        "renamed_by_canon": {k: v for k, v in collapse.items()},
        "lossy_collapse": {k: v for k, v in collapse.items() if len(v) > 1},
        "ok": not warn,
    }


def _pairs(xs) -> set[frozenset]:
    return {frozenset(p) for p in (xs or []) if len(p) == 2}


def rung3(plan, t: dict) -> dict | None:
    """Can the topology be reproduced: room set, door graph, adjacency.

    Returns None when the sheet's door graph has not been annotated yet. A
    faked graph would score as a pass and hide the gap.
    """
    if not t.get("graph"):
        return None
    from fpeval.spatial import _door_pairs
    cats_want: dict[str, int] = {}
    for r in t["rooms"]:
        c = r.get("category")
        if c:
            cats_want[c] = cats_want.get(c, 0) + 1
    cats_got: dict[str, int] = {}
    for r in plan.rooms:
        c = r.category or "?"
        cats_got[c] = cats_got.get(c, 0) + 1

    # Truth pairs are by truth room id; the solved plan uses its own ids. Compare
    # by CATEGORY pair, which is the only thing the two share.
    idcat = {r["id"]: r.get("category") for r in t["rooms"]}
    want_doors = {frozenset((idcat.get(a), idcat.get(b)))
                  for a, b in (t["graph"].get("doors") or [])
                  if idcat.get(a) and idcat.get(b)}
    got_doors = {frozenset((_c(plan, a), _c(plan, b)))
                 for a, b, _k in _door_pairs(plan)}
    want_touch = {frozenset((idcat.get(a), idcat.get(b)))
                  for a, b in (t["graph"].get("touching") or [])
                  if idcat.get(a) and idcat.get(b)}
    return {
        "categories_want": cats_want, "categories_got": cats_got,
        "door_pairs_want": len(want_doors), "door_pairs_got": len(got_doors),
        "door_pairs_matched": sorted("|".join(sorted(str(x) for x in p))
                                     for p in (want_doors & got_doors)),
        "door_pairs_missing": sorted("|".join(sorted(str(x) for x in p))
                                     for p in (want_doors - got_doors)),
        "touch_pairs_want": len(want_touch),
        "front_door_want": (t["graph"].get("front_door") or {}).get("into"),
    }


def _c(plan, rid: str) -> str:
    for r in plan.rooms:
        if r.id == rid:
            return r.category or "?"
    return "?"


def rung4(plan, t: dict) -> dict:
    """Can the geometry be reproduced: areas and envelope shape."""
    want = {}
    for r in t["rooms"]:
        if r.get("area_sqft") and r.get("category"):
            want.setdefault(r["category"], []).append(round(r["area_sqft"]))
    got: dict[str, list[int]] = {}
    for r in plan.rooms:
        got.setdefault(r.category or "?", []).append(round((r.area or 0) / 92903.04))
    return {
        "area_sqft_want": {k: sorted(v, reverse=True) for k, v in want.items()},
        "area_sqft_got": {k: sorted(v, reverse=True) for k, v in got.items()},
        "envelope_want": t["envelope"].get("shape"),
        "envelope_got": "rectangular (slicing tree)",
        "expressible": False if "re-entrant" in (t["envelope"].get("shape") or "")
                       else None,
    }


def match_score(row: dict) -> dict:
    """One number per example, plus the parts it is made of.

    Deliberately not a weighted sum dressed as a priority order -- that mistake
    is already documented in DECISIONS.md. Each part is reported separately and
    `score` is their mean, so a regression in one cannot be hidden by a gain in
    another.

      solved     did a plan come out at all
      programme  fraction of the brief's rooms that reached the plan
      size       1 - median relative area error over categories present in both
      clean      1 if no rule errors, else 1/(1+errors)
    """
    if row.get("skipped"):
        return {}
    status = (row.get("build") or {}).get("status")
    solved = 1.0 if status in ("OPTIMAL", "FEASIBLE") else 0.0
    r2 = row.get("rung2_programme") or {}
    asked = len(r2.get("asked") or [])
    programme = (len(r2.get("arrived") or []) / asked) if asked else 0.0
    g4 = row.get("rung4_geometry") or {}
    want, got = g4.get("area_sqft_want") or {}, g4.get("area_sqft_got") or {}
    errs = []
    for k in set(want) & set(got):
        for a, b in zip(want[k], got[k]):
            if a:
                errs.append(abs(b - a) / a)
    errs.sort()
    size = max(0.0, 1.0 - errs[len(errs) // 2]) if errs else 0.0
    # A plan that does not exist has no findings, so scoring cleanliness on
    # the finding count alone handed a perfect 1.00 to every INFEASIBLE
    # example and rewarded refusing to draw.
    n_err = row.get("n_errors") or 0
    clean = (1.0 / (1.0 + n_err)) if solved else 0.0
    parts = {"solved": solved, "programme": round(programme, 3),
             "size": round(size, 3), "clean": round(clean, 3),
             "n_size_pairs": len(errs)}
    parts["score"] = round((solved + programme + size + clean) / 4.0, 3)
    return parts


# ------------------------------------------------------------------------ main

def one(path: Path, track: str, client, time_limit: float, outdir: Path,
        *, ground_only: bool = False) -> dict:
    t = json.loads(path.read_text())
    row: dict = {"id": t["id"], "track": track, "image": t["source"]["image"]}
    if not t.get("usable", True):
        # A calibration case is deliberately not a reconstruction target.
        row["skipped"] = t.get("not_a_reconstruction_target", "marked unusable")
        return row
    t0 = time.time()

    if track == "B":
        from fpeval.llm import extract_spec
        spec, questions = extract_spec(t["prompt"], client=client)
        row["questions"] = questions
    else:
        spec = truth_to_spec(t, ground_only=ground_only)

    row["ground_only"] = ground_only
    row["rung1_brief"] = rung1(spec, t)
    row["rung2_programme"] = rung2(spec)

    doc = Document.empty(t["id"], name=t["id"])
    doc.design.spec = spec
    br = build(doc, time_limit_s=time_limit, reason="roundtrip")
    row["build"] = {"status": br.status, "warnings": br.warnings,
                    "errors": br.errors, "assumptions": br.assumptions,
                    "infeasible_groups": list(br.infeasible_groups or []),
                    "tradeoffs": list(br.tradeoffs or []),
                    "solve_ms": br.solve_ms}

    plan = doc.design.active
    if plan is None or not plan.rooms:
        row["fatal"] = "no plan produced"
        row["ms"] = int((time.time() - t0) * 1000)
        return row

    # `build` already validated with `spec_to_brief(sp)`. Re-validating here
    # with `brief=None` made `check_bylaws` treat every apartment unit as a
    # plot and report setback and coverage errors on flats that have no plot --
    # 8 of 18 errors across the corpus were this harness's own fault.
    fnd = br.findings
    row["findings"] = [{"rule": f.rule_id, "sev": f.severity, "detail": f.detail}
                       for f in fnd]
    row["n_errors"] = sum(1 for f in fnd if f.severity == "error")
    row["n_warns"] = sum(1 for f in fnd if f.severity == "warn")
    r3 = rung3(plan, t)
    row["rung3_topology"] = r3 if r3 is not None else "not annotated"
    row["rung4_geometry"] = rung4(plan, t)

    png = plan_png(plan, width=1400)
    if png:
        (outdir / f"{t['id']}.gen.png").write_bytes(png)
        row["png"] = f"{t['id']}.gen.png"
    row["ms"] = int((time.time() - t0) * 1000)
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--track", default="A", choices=["A", "B"])
    ap.add_argument("--only", default="")
    ap.add_argument("--time-limit", type=float, default=20.0)
    ap.add_argument("--carpet-factor", type=float, default=0.0,
                    help="override programme.CARPET_TO_FOOTPRINT; the shipped "
                         "1.04 lands inside a measured infeasibility band")
    ap.add_argument("--ground-only", action="store_true",
                    help="brief the ground storey only (build lays out one)")
    ap.add_argument("--out", default="out/roundtrip")
    a = ap.parse_args()

    client = None
    if a.track == "B":
        from anthropic import Anthropic
        client = Anthropic()

    if a.carpet_factor:
        from fpeval import programme as _P
        _P.CARPET_TO_FOOTPRINT = a.carpet_factor
    outdir = Path(a.out); outdir.mkdir(parents=True, exist_ok=True)
    files = sorted(Path("corpus/india/truth").glob("*.json"))
    if a.only:
        files = [f for f in files if a.only in f.name]

    rows = []
    for f in files:
        try:
            r = one(f, a.track, client, a.time_limit, outdir,
                    ground_only=a.ground_only)
        except Exception as e:
            r = {"id": f.stem, "track": a.track, "fatal": f"{type(e).__name__}: {e}",
                 "trace": traceback.format_exc()[-900:]}
        rows.append(r)
        if r.get("skipped"):
            print(f"{r['id']:<30} SKIPPED (calibration case)")
            continue
        b = r.get("build") or {}
        print(f"{r['id']:<30} {b.get('status') or r.get('fatal','?'):<16} "
              f"rung1={'ok' if (r.get('rung1_brief') or {}).get('ok') else 'GAP':<3} "
              f"rung2={'ok' if (r.get('rung2_programme') or {}).get('ok') else 'GAP':<3} "
              f"err={r.get('n_errors','-')} warn={r.get('n_warns','-')} "
              f"{r.get('ms','?')}ms")

    for r in rows:
        r["match"] = match_score(r)
    (outdir / f"track{a.track}.json").write_text(json.dumps(rows, indent=1, default=str))
    scored = [r for r in rows if r.get("match")]
    if scored:
        print(f"\n{'example':<32}{'solved':>7}{'prog':>6}{'size':>6}"
              f"{'clean':>7}{'score':>7}")
        print("-" * 65)
        for r in sorted(scored, key=lambda x: -x["match"]["score"]):
            m = r["match"]
            print(f"{r['id']:<32}{m['solved']:>7.0f}{m['programme']:>6.2f}"
                  f"{m['size']:>6.2f}{m['clean']:>7.2f}{m['score']:>7.3f}")
        print("-" * 65)
        n = len(scored)
        print(f"{'MEAN of ' + str(n):<32}"
              f"{sum(r['match']['solved'] for r in scored)/n:>7.2f}"
              f"{sum(r['match']['programme'] for r in scored)/n:>6.2f}"
              f"{sum(r['match']['size'] for r in scored)/n:>6.2f}"
              f"{sum(r['match']['clean'] for r in scored)/n:>7.2f}"
              f"{sum(r['match']['score'] for r in scored)/n:>7.3f}")
    print(f"\nwrote {outdir}/track{a.track}.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
