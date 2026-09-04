"""Run the visual check over solved plans and collect the candidate rules.

    python scripts/review_visual.py                 # 6 plans
    python scripts/review_visual.py --plans 12

The output that matters is the last section: defects the drawing shows and the
rules engine has no rule for. Everything else is there so you can judge how
much to trust that list -- a checker that agrees with the engine on nothing is
not finding new rules, it is hallucinating.

Costs roughly $0.03 per plan (one 640 px image plus the position list).
"""
from __future__ import annotations

import argparse, collections, json, os, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from fpeval.bylaws import BENGALURU                     # noqa: E402
from fpeval.commands import Command                     # noqa: E402
from fpeval.document import Document                    # noqa: E402
from fpeval.generate import build                       # noqa: E402
from fpeval.review import visual_review, cross_reference  # noqa: E402

IN_M, OUT_M = 5.00, 25.00

# A spread of plot sizes and programmes, so the candidate rules are not all
# artefacts of one footprint.
CASES = [
    ("20x30 1BHK",       20, 30, "north", {"bedrooms": 1, "utility": True}),
    ("30x40 2BHK",       30, 40, "north", {"bedrooms": 2}),
    ("30x40 3BHK",       30, 40, "east",  {"bedrooms": 3}),
    ("30x50 3BHK+pooja", 30, 50, "east",  {"bedrooms": 3, "pooja": True,
                                           "utility": True}),
    ("40x60 3BHK+dining",40, 60, "east",  {"bedrooms": 3, "dining": True,
                                           "pooja": True, "utility": True}),
    ("40x60 4BHK",       40, 60, "north", {"bedrooms": 4, "dining": True,
                                           "sit_out": True}),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plans", type=int, default=len(CASES))
    ap.add_argument("--out", default="out/review")
    args = ap.parse_args()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set"); return 2

    rows, cost, cand = [], 0.0, collections.Counter()
    for label, w, d, facing, prog in CASES[:args.plans]:
        doc = Document.empty(label.replace(" ", "-"), name=label)
        doc.apply(Command(op="set_plot", source="agent", description="site",
                          params={"width_ft": w, "depth_ft": d,
                                  "road_facing": facing, "city": "bengaluru"}))
        doc.apply(Command(op="use_standard_programme", source="agent",
                          description="programme", params=prog))
        r = build(doc, time_limit_s=12.0)
        if not r.ok:
            print(f"\n{label}: {r.status} -- skipped"); continue

        t0 = time.time()
        vis, usage = visual_review(doc.design.active)
        cost += (usage.get("input", 0) * IN_M + usage.get("output", 0) * OUT_M) / 1e6
        x = cross_reference(vis, r.findings)

        print(f"\n{'='*74}\n{label}   {r.status}   "
              f"{r.n_errors} rule errors, {len(vis)} visual findings   "
              f"{time.time()-t0:.0f}s\n{'='*74}")
        print(f"  BOTH SAW IT ({len(x['agreed'])}):")
        for v, f, j in x["agreed"]:
            print(f"    {v.category:30} ~ {getattr(f, 'rule_id', '?'):32} "
                  f"(overlap {j})")
        print(f"  RULES ONLY -- the drawing did not show it "
              f"({len(x['rules_only'])}):")
        for f in x["rules_only"][:6]:
            print(f"    {getattr(f, 'rule_id', '?'):34} "
                  f"[{getattr(f, 'severity', '?')}]")
        print(f"  VISION ONLY -- candidate rules ({len(x['vision_only'])}):")
        for v in x["vision_only"]:
            print(f"    [{v.severity:5}] {v.category:28} {v.what[:80]}")
            if v.severity in ("error", "warn"):
                cand[v.category] += 1

        rows.append({"case": label, "status": r.status,
                     "rule_errors": r.n_errors,
                     "visual": [v.to_dict() for v in vis],
                     "agreed": [[v.to_dict(), getattr(f, "rule_id", ""), j]
                                for v, f, j in x["agreed"]],
                     "rules_only": [getattr(f, "rule_id", "")
                                    for f in x["rules_only"]],
                     "vision_only": [v.to_dict() for v in x["vision_only"]]})

    print(f"\n{'='*74}\nCANDIDATE RULES -- seen on more than one plan, no rule id")
    for k, n in cand.most_common():
        if n > 1:
            print(f"  {n:2} plans   {k}")
    print(f"\ntotal ${cost:.2f} over {len(rows)} plan(s)")
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    (out / "runs.json").write_text(json.dumps(rows, indent=2))
    print(f"wrote {out/'runs.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
