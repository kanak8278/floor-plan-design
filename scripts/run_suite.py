"""Run the prompt suite and emit a results table.

Track A (default) uses ground truth directly: no LLM, free, isolates engine
failures. Track B runs the full pipeline through extract_spec.
"""
from __future__ import annotations
import argparse, json, sys, time
from pathlib import Path
import _bootstrap  # noqa: F401  (path + cwd)

from fpeval.suite import load_suite
from fpeval.score import run

ap = argparse.ArgumentParser()
ap.add_argument("--track", default="A", choices=["A", "B"])
ap.add_argument("--limit", type=int, default=0)
ap.add_argument("--time-limit", type=float, default=10.0)
ap.add_argument("--only", default="")
ap.add_argument("--set", default="", help="all | paired (50 general + 50 detailed)")
ap.add_argument("--out", default="out/suite")
# Deterministic by default: a suite run is a ruler. `--wall-clock` restores the
# production budget for when the question is "how does it behave when shipped".
ap.add_argument("--wall-clock", action="store_true",
                help="budget by seconds like production, not reproducibly")
args = ap.parse_args()

exs = load_suite()
if args.set == "paired":
    import json as _j
    sel = set(_j.loads(Path("suite/SELECTION.json").read_text())["general_50"])
    exs = [e for e in exs if e.id in sel or e.id.startswith("det-")]
if args.only:
    exs = [e for e in exs if args.only in e.id]
if args.limit:
    exs = exs[: args.limit]

client = None
if args.track == "B":
    from anthropic import Anthropic
    client = Anthropic()

outdir = Path(args.out); outdir.mkdir(parents=True, exist_ok=True)
rows, t0 = [], time.time()
print(f"{'id':<12}{'expect':<11}{'status':<12}{'score':>6}{'rms':>5}{'err':>4}"
      f"{'wrn':>4}{'cov':>7}{'sqft':>6}{'ms':>7}  fails")
print("-" * 104)

for e in exs:
    if args.track not in e.tracks:
        continue
    spec, questions = None, []
    if args.track == "B":
        from fpeval.llm import extract_spec
        try:
            spec, questions = extract_spec(e.prompt, client=client)
        except Exception as ex:
            print(f"{e.id:<12}EXTRACT ERROR {type(ex).__name__}: {str(ex)[:50]}")
            rows.append({"id": e.id, "track": "B", "error": f"extract: {ex}"})
            continue
    r = run(e, track=args.track, client=client, time_limit_s=args.time_limit,
            spec=spec, deterministic=not args.wall_clock)
    fails = "; ".join(f"{c.name}" for c in r.checks if not c.ok)
    print(f"{e.id:<12}{e.expect:<11}{r.status:<12}{r.score:>6.2f}{r.n_rooms:>5}"
          f"{r.n_errors:>4}{r.n_warnings:>4}"
          f"{(f'{r.coverage:.2f}' if r.coverage is not None else '-'):>7}"
          f"{(r.carpet_sqft or '-'):>6}{r.solve_ms:>7}  {fails[:36]}")
    rows.append({
        "id": e.id, "track": args.track, "expect": e.expect, "status": r.status,
        "score": round(r.score, 3), "passed": r.passed, "rooms": r.n_rooms,
        "errors": r.n_errors, "warnings": r.n_warnings, "coverage": r.coverage,
        "carpet_sqft": r.carpet_sqft, "vastu": r.vastu_score, "solve_ms": r.solve_ms,
        "features": e.features, "error": r.error,
        "failed_checks": [{"name": c.name, "detail": c.detail} for c in r.checks if not c.ok],
        "bridge_warnings": r.warnings,
        # `score.Result` has carried these all along and the export dropped
        # them, so a run told you the error COUNT went up and gave you no way
        # to ask which rule. Re-validating outside the run to find out is how
        # an earlier measurement blamed the bye-law family for ten errors on
        # apartment units it correctly skips.
        "error_ids": list(getattr(r, "error_ids", []) or []),
        "warn_ids": list(getattr(r, "warn_ids", []) or []),
        # What track B actually extracted. Without it a track B run says
        # INFEASIBLE and gives you no way to ask what was asked for, so
        # diagnosing one costs another paid extraction -- which is how the
        # first run's `KeyError: 'centre'` had to be chased down.
        "extracted": ({
            "plot": [spec.plot_width_ft, spec.plot_depth_ft],
            "facing": spec.road_facing_side,
            "storeys": spec.storeys,
            "rooms": [{"id": x.id, "category": x.category,
                       "min_sqft": x.min_sqft, "optional": x.optional,
                       "priority": x.priority} for x in spec.rooms],
            "questions": list(questions or []),
        } if spec is not None else None),
    })

el = time.time() - t0
scored = [x for x in rows if x.get("status") not in ("skipped", None)]
passed = [x for x in scored if x.get("passed")]
skipped = [x for x in rows if x.get("status") == "skipped"]
print("-" * 104)
print(f"examples          : {len(rows)}   scored {len(scored)}   skipped {len(skipped)}")
if scored:
    print(f"fully passing     : {len(passed)}/{len(scored)}  ({100*len(passed)/len(scored):.1f}%)")
    print(f"mean check score  : {sum(x['score'] for x in scored)/len(scored):.3f}")
    z = [x for x in scored if x.get("errors") == 0]
    print(f"zero rule errors  : {len(z)}/{len(scored)}  ({100*len(z)/len(scored):.1f}%)")
print(f"wall clock        : {el:.1f}s")

from collections import Counter
fc = Counter(c["name"].split(":")[0] for x in rows for c in x.get("failed_checks", []))
if fc:
    print("\nfailure modes:")
    for k, v in fc.most_common():
        print(f"  {k:<22}{v}")

stamp = time.strftime("%Y%m%d-%H%M%S")
(outdir / f"track{args.track}-{stamp}.json").write_text(json.dumps(rows, indent=1))
print(f"\nwrote {outdir}/track{args.track}-{stamp}.json")
