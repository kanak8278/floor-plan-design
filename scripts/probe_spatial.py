"""Does a spatial view change how the agent reasons about a plan?

    python scripts/probe_spatial.py            # A/B, both arms
    python scripts/probe_spatial.py --arm on   # one arm only

Same plan, same question, twice: once with the plan shown as a rasterised
drawing plus the compass/adjacency text, and once with both suppressed. Anything else identical, including the
seed the plan was solved with.

The plan is chosen because its defects are known and independently measured,
so the arms can be scored against a fixed list rather than against taste:

  * the utility is at the opposite end of the house from the kitchen
  * one bathroom has no exterior wall at all, so it can never be ventilated
  * the common toilet opens off a bedroom instead of off circulation

An arm "sees" a defect when its reply names the rooms involved. That is a
keyword check and a weak instrument, so the replies are printed in full -- the
numbers are a summary of something you should read, not a substitute for it.
"""
from __future__ import annotations

import argparse, json, os, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from fpeval import agent as A                              # noqa: E402
from fpeval.commands import Command                        # noqa: E402
from fpeval.document import Document                       # noqa: E402
from fpeval.generate import build                          # noqa: E402
from fpeval.rules import validate as validate_plan        # noqa: E402
from fpeval.bylaws import BENGALURU                        # noqa: E402

IN_M, OUT_M, CACHE_R_M = 5.00, 25.00, 0.50

ASK = ("Look at this plan and tell me what is wrong with the way the rooms are "
       "arranged -- not the sizes, the arrangement. Then fix what you can "
       "without re-solving the whole floor.")

# (label, rooms whose names must appear together for the defect to count)
DEFECTS = [
    ("utility far from kitchen", ("utility", "kitchen")),
    ("bathroom with no exterior wall", ("bath2",)),
    ("common toilet opens off a bedroom", ("toilet1", "bed3")),
]


def make_doc() -> Document:
    doc = Document.empty("spatial", name="40x60 3BHK")
    for c in (Command(op="set_plot", source="agent", description="site",
                      params={"width_ft": 40, "depth_ft": 60,
                              "road_facing": "east", "city": "bengaluru"}),
              Command(op="use_standard_programme", source="agent",
                      description="3BHK",
                      params={"bedrooms": 3, "pooja": True, "utility": True,
                              "dining": True})):
        assert doc.apply(c).ok
    r = build(doc, time_limit_s=12.0)
    assert r.ok, r.errors
    return doc


def run_arm(spatial: bool) -> dict:
    doc = make_doc()
    before = {(x.id) for x in doc.design.active.rooms}
    real_png, real_txt = A.plan_image_message, A.spatial_block
    if not spatial:
        # Suppress both halves of the spatial view and change nothing else:
        # same plan, same question, same seed.
        A.plan_image_message = lambda *a, **k: None
        A.spatial_block = lambda *a, **k: ""
    try:
        def findings_fn():
            st = doc.design.active
            return validate_plan(st, brief=None, profile=BENGALURU) if st.rooms else []
        t0 = time.time()
        res = A.run_turn(doc, [], ASK, last_seen_seq=0, findings_fn=findings_fn)
    finally:
        A.plan_image_message, A.spatial_block = real_png, real_txt
    u = res.usage
    cost = (u["input"] * IN_M + u["output"] * OUT_M + u["cache_read"] * CACHE_R_M) / 1e6
    low = res.reply.lower()
    seen = [label for label, keys in DEFECTS
            if all(k.lower() in low for k in keys)]
    return {"spatial": spatial, "reply": res.reply,
            "applied": [f"{getattr(e,'op','?')}: {getattr(e,'summary','')}"
                        for e in res.events],
            "refused": [f"{x.command.op}: {x.errors[0]}" for x in res.rejected],
            "defects_named": seen, "steps": res.steps,
            "error": res.error,
            "secs": round(time.time() - t0), "cost": round(cost, 3),
            "rooms_unchanged": before == {x.id for x in doc.design.active.rooms}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", choices=["on", "off", "both"], default="both")
    ap.add_argument("--out", default="out/spatial")
    args = ap.parse_args()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set"); return 2

    arms = {"on": [True], "off": [False], "both": [False, True]}[args.arm]
    results = []
    for spatial in arms:
        tag = "WITH spatial view" if spatial else "WITHOUT spatial view"
        print(f"\n{'='*76}\n{tag}\n{'='*76}")
        r = run_arm(spatial)
        results.append(r)
        print(f"\napplied ({len(r['applied'])}):")
        for a in r["applied"]:
            print(f"  + {a}")
        if r["refused"]:
            print(f"refused ({len(r['refused'])}):")
            for x in r["refused"]:
                print(f"  - {x}")
        print(f"\n--- reply ---\n{r['reply']}")
        print(f"\ndefects named: {r['defects_named'] or 'none'}  "
              f"({len(r['defects_named'])}/{len(DEFECTS)})")
        print(f"steps={r['steps']} {r['secs']}s ${r['cost']}")

    print(f"\n{'='*76}")
    for r in results:
        print(f"{'with' if r['spatial'] else 'without':>8} spatial: "
              f"{len(r['defects_named'])}/{len(DEFECTS)} defects named, "
              f"{len(r['applied'])} changes applied, "
              f"{len(r['refused'])} refused, ${r['cost']}")
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    (out / "runs.json").write_text(json.dumps(results, indent=2))
    print(f"wrote {out/'runs.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
