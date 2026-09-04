"""Can the agent build a plan from an empty document?

    python scripts/probe_scratch.py                 # three scripted turns
    python scripts/probe_scratch.py --turns 1       # just the first ask

Every other probe in `probe_agent.py` starts from adopted solver output, so all
of them test *editing*. None tests the case a user actually hits first: an empty
document and "build me a 2BHK". This harness does only that, and it reports what
the document contains afterwards rather than what the agent said about it.

Refusals are the point here, not a failure. Each one is printed with the reason
the applier gave, because the reason is what tells you which layer is missing.
"""
from __future__ import annotations

import argparse, json, os, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from fpeval.document import Document                      # noqa: E402
from fpeval.agent import run_turn                          # noqa: E402
from fpeval.rules import validate as validate_plan         # noqa: E402
from fpeval.bylaws import BENGALURU                        # noqa: E402

# Opus 5 list price, USD per million tokens.
IN_M, OUT_M, CACHE_R_M = 5.00, 25.00, 0.50

TURNS = [
    # Verbatim what the user typed in the editor.
    "Build a 2bhk standdard sixe",
    # The facts `ask.never_assume` says we must not invent, supplied.
    "The plot is 30 x 40 ft with the road on the north. Go ahead and lay it out.",
    # Cheap and diagnostic: the agent reading its own tool results back.
    "You changed almost nothing. Tell me exactly which commands you tried, "
    "what came back, and what you would need in order to do this.",
]


def digest(doc) -> dict:
    st = doc.design.active
    return {"rooms": [(r.id, r.category, r.name) for r in st.rooms],
            "walls": len(st.walls), "openings": len(st.openings),
            "furniture": len(getattr(st, "furniture", []) or []),
            "name": doc.design.name, "seq": doc.seq}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--turns", type=int, default=len(TURNS))
    ap.add_argument("--out", default="out/probe_scratch")
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set"); return 2

    doc = Document.empty("scratch", name="Untitled")

    def findings_fn():
        st = doc.design.active
        if not st.rooms:
            return []
        try:
            return validate_plan(st, brief=None, profile=BENGALURU)
        except Exception:
            return []

    transcript: list[dict] = []
    runs, cost, t0 = [], 0.0, time.time()

    for i, msg in enumerate(TURNS[:args.turns]):
        print(f"\n{'='*74}\nTURN {i+1}: {msg}\n{'='*74}")
        seen = doc.seq
        r = run_turn(doc, transcript, msg, last_seen_seq=seen,
                     findings_fn=findings_fn)
        if r.error:
            print("  ERROR:", r.error); runs.append({"turn": i+1, "error": r.error}); break

        u = r.usage
        c = (u["input"]*IN_M + u["output"]*OUT_M + u["cache_read"]*CACHE_R_M) / 1e6
        cost += c

        print(f"\n-- applied ({len(r.events)})")
        for e in r.events:
            print(f"   + {getattr(e,'op','?'):24} {getattr(e,'summary','')}")
        print(f"\n-- refused ({len(r.rejected)})")
        for x in r.rejected:
            print(f"   - {x}")
        print(f"\n-- reply\n{r.reply}")
        print(f"\n-- state {digest(doc)}")
        print(f"-- steps={r.steps} tokens in/out/cached="
              f"{u['input']}/{u['output']}/{u['cache_read']}  ${c:.3f}")

        runs.append({"turn": i+1, "ask": msg, "reply": r.reply, "steps": r.steps,
                     "applied": [{"op": getattr(e,'op',''),
                                  "summary": getattr(e,'summary','')} for e in r.events],
                     "refused": [str(x) for x in r.rejected],
                     "state": digest(doc), "usage": u, "cost_usd": round(c, 4)})

    st = doc.design.active
    print(f"\n{'='*74}\nFINAL: {len(st.rooms)} rooms, {len(st.walls)} walls, "
          f"{len(st.openings)} openings after {len(runs)} turn(s)")
    print(f"total ${cost:.2f}, {time.time()-t0:.0f}s")

    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    (out / "runs.json").write_text(json.dumps(
        {"runs": runs, "final": digest(doc), "cost_usd": round(cost, 4)}, indent=2))
    print(f"wrote {out/'runs.json'}")
    # A plan built from nothing is the pass condition; anything else is the finding.
    return 0 if st.rooms else 1


if __name__ == "__main__":
    raise SystemExit(main())
