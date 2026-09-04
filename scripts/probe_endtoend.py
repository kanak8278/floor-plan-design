"""User prompt -> brief -> solved geometry, over several houses, for real.

    python scripts/probe_endtoend.py                       # all houses
    python scripts/probe_endtoend.py --houses 2bhk,3bhk     # a subset
    python scripts/probe_endtoend.py --turns 1              # first ask only

Every other harness in this directory starts from solver output and tests
editing. This one starts from an empty document and a sentence a person would
actually type, and it checks the document at the end rather than the reply.

## What counts as passing

A house passes when the document holds a solved floor whose room list covers
what was asked for. The reply is checked for one thing only, and it is not
prose quality: if the solver had to invent a plot size, the reply has to say
so. An assumed dimension presented as though the user gave it is the failure
mode this whole layer exists to prevent, so it is asserted rather than
eyeballed.

## Cost

Real API turns, on Opus. Two turns per house by default. `--houses` and
`--turns` keep a run small; the whole set is a few dollars, not cents.
"""
from __future__ import annotations

import argparse, json, os, re, sys, time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from fpeval.document import Document                       # noqa: E402
from fpeval.agent import run_turn                          # noqa: E402
from fpeval.rules import validate as validate_plan         # noqa: E402
from fpeval.bylaws import BENGALURU                        # noqa: E402
from fpeval.render import render                           # noqa: E402
from fpeval.programme import bedroom_count                 # noqa: E402

IN_M, OUT_M, CACHE_R_M = 5.00, 25.00, 0.50


@dataclass
class House:
    key: str
    ask: str
    # Room categories the finished plan must contain, and how many.
    wants: dict[str, int]
    # True when the ask gives no plot, so the reply must own up to assuming one.
    plot_unstated: bool = False
    follow_up: str = ("Check it against the rules and fix what you can "
                      "without making the plan worse. Tell me what is left.")
    notes: str = ""


HOUSES = [
    House("2bhk", "Build a 2bhk standdard sixe",
          {"living": 1, "kitchen": 1, "bedroom": 2, "bathroom": 1},
          plot_unstated=True,
          notes="verbatim from a real session, typos included"),
    House("3bhk-vastu",
          "30x40 north facing site in Bangalore. I want a 3BHK with a pooja "
          "room and covered parking. Vastu matters to us.",
          {"living": 1, "kitchen": 1, "bedroom": 3, "bathroom": 1, "pooja": 1}),
    House("1bhk-small",
          "We have a small 20x30 plot facing east. Just a 1BHK for the two of "
          "us, but I do want a separate utility for the washing machine.",
          {"living": 1, "kitchen": 1, "bedroom": 1, "bathroom": 1}),
    House("4bhk-large",
          "40x60 corner plot, east facing. Four bedrooms, a formal dining, "
          "and a study. Ground floor only.",
          {"living": 1, "kitchen": 1, "bedroom": 4, "bathroom": 1}),
    House("apartment",
          "It's an apartment, not a plot -- 1150 sqft carpet, 3BHK, main "
          "windows face west. Lay out the interior.",
          {"living": 1, "kitchen": 1, "bedroom": 3, "bathroom": 1},
          notes="apartment_unit path: no plot, no setbacks"),
]

# Numbers a reply must not present as the user's when nobody gave them.
_DIMS = re.compile(r"\b(\d{2})\s*[x×]\s*(\d{2})\b")
_OWNS_UP = re.compile(
    r"assum|standard (site|plot|size)|unless you|if that.s (not|wrong)|"
    r"tell me (the|your)|i.ve used|i used|default|say the word|"
    r"let me know (the|if)|which you can change|adjust", re.I)


@dataclass
class Outcome:
    house: str
    passed: bool = False
    reasons: list[str] = field(default_factory=list)
    status: str = ""
    rooms: list[tuple[str, str, float]] = field(default_factory=list)
    walls: int = 0
    openings: int = 0
    n_err: int = 0
    n_warn: int = 0
    errors: list[str] = field(default_factory=list)
    applied: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    replies: list[str] = field(default_factory=list)
    cost: float = 0.0
    secs: float = 0.0
    svg: str = ""


def _cats(st) -> dict[str, int]:
    """Room categories present, counting subtypes as their parent.

    `master_bedroom` is a bedroom and `wc` is a bathroom -- the taxonomy knows
    that, and a check that does not would fail every plan the solver gets
    right.
    """
    from fpeval import roomtypes as rt
    out: dict[str, int] = {}
    for r in st.rooms:
        for key in {r.category, rt.canonical(r.category)}:
            out[key] = out.get(key, 0) + 1
        parent = rt.SUBTYPE_OF.get(r.category)
        if parent:
            out[parent] = out.get(parent, 0) + 1
    return out


def run_house(h: House, turns: int, out_dir: Path) -> Outcome:
    o = Outcome(house=h.key)
    doc = Document.empty(f"e2e-{h.key}", name="Untitled")

    def findings_fn():
        st = doc.design.active
        if not st.rooms:
            return []
        try:
            return validate_plan(st, brief=None, profile=BENGALURU)
        except Exception:
            return []

    transcript: list[dict] = []
    asks = [h.ask] + ([h.follow_up] if turns > 1 else [])
    t0 = time.time()
    for msg in asks:
        seen = doc.seq
        r = run_turn(doc, transcript, msg, last_seen_seq=seen,
                     findings_fn=findings_fn)
        if r.error:
            o.reasons.append(f"api error: {r.error}")
            break
        u = r.usage
        o.cost += (u["input"] * IN_M + u["output"] * OUT_M
                   + u["cache_read"] * CACHE_R_M) / 1e6
        o.applied += [f"{getattr(e,'op','?')}: {getattr(e,'summary','')}"
                      for e in r.events]
        o.refused += [f"{x.command.op}: {x.errors[0]}" for x in r.rejected]
        o.replies.append(r.reply)
    o.secs = time.time() - t0

    st = doc.design.active
    o.walls, o.openings = len(st.walls), len(st.openings)
    o.rooms = [(x.id, x.category, round(x.area_m2, 1)) for x in st.rooms]
    o.status = "solved" if st.rooms else "nothing built"

    # --- did it build the house that was asked for? ------------------------
    if not st.rooms:
        o.reasons.append("no geometry: the document is still empty")
    else:
        have = _cats(st)
        for cat, n in h.wants.items():
            if have.get(cat, 0) < n:
                o.reasons.append(f"wanted {n}x {cat}, plan has "
                                 f"{have.get(cat, 0)}")
        fs = findings_fn()
        errs = [f for f in fs if getattr(f, "severity", "") == "error"]
        o.n_err, o.n_warn = len(errs), len(fs) - len(errs)
        o.errors = [f"{f.rule_id}: {f.detail}" for f in errs]
        try:
            o.svg = render(st, "presentation")
        except Exception as exc:
            o.reasons.append(f"render failed: {type(exc).__name__}: {exc}")

    # --- if we invented the plot, did it say so? ---------------------------
    if h.plot_unstated and o.replies:
        first = o.replies[0]
        if _DIMS.search(first) and not _OWNS_UP.search(first):
            o.reasons.append("quoted a plot size it invented without "
                             "flagging it as an assumption")
        elif not _DIMS.search(first):
            o.reasons.append("never told the user which plot it used")

    o.passed = not o.reasons
    if o.svg:
        (out_dir / f"{h.key}.svg").write_text(o.svg)
    return o


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--houses", default="",
                    help="comma-separated keys; default all")
    ap.add_argument("--turns", type=int, default=2)
    ap.add_argument("--out", default="out/e2e")
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set"); return 2
    want = {k.strip() for k in args.houses.split(",") if k.strip()}
    houses = [h for h in HOUSES if not want or h.key in want]
    out_dir = Path(args.out); out_dir.mkdir(parents=True, exist_ok=True)

    results: list[Outcome] = []
    for h in houses:
        print(f"\n{'='*76}\n{h.key}: {h.ask}")
        if h.notes:
            print(f"  ({h.notes})")
        print("=" * 76)
        o = run_house(h, args.turns, out_dir)
        results.append(o)
        print(f"\n  applied ({len(o.applied)}):")
        for a in o.applied:
            print(f"    + {a}")
        if o.refused:
            print(f"  refused ({len(o.refused)}):")
            for x in o.refused:
                print(f"    - {x}")
        print(f"\n  rooms ({len(o.rooms)}): "
              + ", ".join(f"{i}/{c} {a:g}m2" for i, c, a in o.rooms))
        print(f"  walls {o.walls}, openings {o.openings}, "
              f"{o.n_err} errors, {o.n_warn} warnings")
        for e in o.errors:
            print(f"    ERR {e}")
        print(f"\n  --- first reply ---\n{o.replies[0] if o.replies else '(none)'}")
        if len(o.replies) > 1:
            print(f"\n  --- after self-check ---\n{o.replies[1]}")
        print(f"\n  {'PASS' if o.passed else 'FAIL'} "
              f"({o.secs:.0f}s, ${o.cost:.2f})")
        for why in o.reasons:
            print(f"    ! {why}")

    print(f"\n{'='*76}")
    npass = sum(1 for o in results if o.passed)
    print(f"{npass}/{len(results)} houses built end to end")
    print(f"{'house':<14}{'rooms':>6}{'walls':>7}{'err':>5}{'warn':>6}"
          f"{'secs':>7}{'cost':>8}  result")
    for o in results:
        print(f"{o.house:<14}{len(o.rooms):>6}{o.walls:>7}{o.n_err:>5}"
              f"{o.n_warn:>6}{o.secs:>7.0f}{o.cost:>8.2f}  "
              f"{'PASS' if o.passed else 'FAIL: ' + '; '.join(o.reasons)}")
    total = sum(o.cost for o in results)
    print(f"\ntotal ${total:.2f}")
    (out_dir / "runs.json").write_text(json.dumps(
        [{k: v for k, v in o.__dict__.items() if k != "svg"} for o in results],
        indent=2))
    print(f"wrote {out_dir/'runs.json'} and {len(results)} SVG(s) to {out_dir}")
    return 0 if npass == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
