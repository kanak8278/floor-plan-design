"""Drive the chat agent over real generated plans and record what breaks.

    python tests/probe_agent.py --examples base-01,vastu-01 --out out/probe

This is a *test harness*, not a feature. It generates a plan from a `suite/`
example on track A (ground truth, no LLM, free and deterministic), adopts it as
a document, then runs a scripted conversation that exercises one agent
capability at a time -- create, remove, move, rename, retype, resize, re-solve,
query -- and checks the document afterwards to see whether the effect matches
what the agent said it did.

## Why check the document rather than the reply

An agent that says "I removed the door" and removes nothing is the failure mode
that matters, and it is invisible if you only read the prose. So every probe
declares an `expect` predicate over the document, and the harness reports three
outcomes separately:

  PASS    the document changed the way the probe required
  FAIL    it did not, and the agent claimed otherwise -- a real defect
  REFUSED the agent declined or the applier rejected, with a reason

REFUSED is not a failure. A limitation the agent states plainly is a finding
about the vocabulary, not a bug in the agent, and the two want different fixes.

## Cost

Every probe is a real API turn. `--examples` and `--probes` keep a run small;
the default is deliberately a handful, not the whole suite.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from fpeval.commands import Command                             # noqa: E402
from fpeval.document import Document                            # noqa: E402
from fpeval.ir import Design, Plan                              # noqa: E402
from fpeval.suite import load_suite                             # noqa: E402
from fpeval.score import run as score_run                       # noqa: E402
from fpeval.agent import stream_turn                            # noqa: E402
from fpeval.rules import validate as validate_plan              # noqa: E402
from fpeval.bylaws import BENGALURU                             # noqa: E402


# --------------------------------------------------------------------------
# probe definitions
# --------------------------------------------------------------------------

@dataclass
class Probe:
    """One capability, one message, one predicate over the resulting document."""
    name: str
    ask: Callable[[Design], Optional[str]]
    expect: Callable[[Design, Design], tuple[bool, str]]
    # A probe whose subject does not exist on this plan is skipped, not failed.
    note: str = ""


def _rooms(d: Design) -> list:
    st = d.active
    return list(st.rooms) if st else []


def _by_cat(d: Design, *cats: str) -> list:
    return [r for r in _rooms(d) if r.category in cats]


def _named(d: Design, rid: str):
    st = d.active
    return st.room(rid) if st else None


def _label(r) -> str:
    return r.name or r.category


def _openings(d: Design) -> list:
    st = d.active
    return list(st.openings) if st else []


def _walls(d: Design) -> list:
    st = d.active
    return list(st.walls) if st else []


def _bbox(r) -> tuple[int, int]:
    if len(r.polygon) < 3:
        return (0, 0)
    xs = [p.x for p in r.polygon]
    ys = [p.y for p in r.polygon]
    return (max(xs) - min(xs), max(ys) - min(ys))


# -- create ----------------------------------------------------------------

def probe_add_window() -> Probe:
    def ask(d):
        beds = _by_cat(d, "bedroom", "master_bedroom")
        if not beds:
            return None
        return (f"Add a window to the {_label(beds[0])}. "
                "Put it on whichever of its walls faces outside.")

    def expect(before, after):
        b = len([o for o in _openings(before) if o.kind == "window"])
        a = len([o for o in _openings(after) if o.kind == "window"])
        return (a > b, f"windows {b} -> {a}")
    return Probe("create/window", ask, expect,
                 "needs a bedroom on the plan")


def probe_add_door() -> Probe:
    def ask(d):
        ws = _walls(d)
        if len(ws) < 4:
            return None
        return ("Add a single door at the centre of the longest interior wall "
                "on this floor.")

    def expect(before, after):
        b = len([o for o in _openings(before) if o.kind == "door"])
        a = len([o for o in _openings(after) if o.kind == "door"])
        return (a > b, f"doors {b} -> {a}")
    return Probe("create/door", ask, expect)


def probe_add_room_to_brief() -> Probe:
    def ask(d):
        return ("Add a pooja room to the brief, about 30 sqft, in the "
                "north-east. Do not re-solve yet, just record it.")

    def expect(before, after):
        """A vacuous `True` here is why this probe reported "pass" while the
        whole programme layer was stubbed out. A spec-level op must be
        *accepted*; geometry not changing is not evidence of anything."""
        return (False, "no spec op was accepted (the programme layer is "
                       "not wired to the document)")
    return Probe("create/spec-room", ask, expect,
                 "spec-level: records intent, no geometry until re-solve")


# -- remove ----------------------------------------------------------------

def probe_remove_opening() -> Probe:
    def ask(d):
        wins = [o for o in _openings(d) if o.kind == "window"]
        if not wins:
            return None
        o = wins[0]
        return f"Remove the window {o.id} from wall {o.wall_id}."

    def expect(before, after):
        b = {o.id for o in _openings(before)}
        a = {o.id for o in _openings(after)}
        gone = b - a
        return (bool(gone), f"removed {sorted(gone) or 'nothing'}")
    return Probe("remove/opening", ask, expect, "needs a window on the plan")


def probe_remove_wall() -> Probe:
    def ask(d):
        st = d.active
        if not st or len(st.walls) < 5:
            return None
        return ("Delete one interior wall so two rooms merge into one. "
                "Tell me which rooms merged.")

    def expect(before, after):
        b, a = len(_walls(before)), len(_walls(after))
        rb, ra = len(_rooms(before)), len(_rooms(after))
        return (a < b, f"walls {b} -> {a}, rooms {rb} -> {ra}")
    return Probe("remove/wall", ask, expect, "needs an interior wall")


# -- move ------------------------------------------------------------------

def probe_move_wall() -> Probe:
    def ask(d):
        st = d.active
        if not st or not st.walls:
            return None
        w = max(st.walls, key=lambda x: x.length)
        return (f"Move wall {w.id} 300 mm north. Do not change anything else.")

    def expect(before, after):
        """"Did a wall move" is not enough, and the first version of this
        probe passed while the document was being wrecked.

        A wall move relocates one wall's endpoints and leaves its neighbours
        behind, so the ring can stop being a closed face and every room on the
        storey loses its identity. The probe has to check that the rooms
        survived, or it reports success on the most destructive operation in
        the vocabulary. See `test_KNOWN_BUG_moving_a_wall_outward_destroys_
        every_room`.
        """
        bw = {w.id: (w.start.as_tuple(), w.end.as_tuple())
              for w in _walls(before)}
        moved = [w.id for w in _walls(after)
                 if w.id in bw and (w.start.as_tuple(), w.end.as_tuple()) != bw[w.id]]
        rb, ra = len(_rooms(before)), len(_rooms(after))
        nb = sum(1 for r in _rooms(before) if r.name)
        na = sum(1 for r in _rooms(after) if r.name)
        if not moved:
            return (False, "moved nothing")
        if ra < rb or na < nb:
            return (False, f"moved {moved} BUT rooms {rb} -> {ra}, "
                           f"named {nb} -> {na} — the wall graph tore")
        return (True, f"moved {moved}, rooms and names intact")
    return Probe("move/wall", ask, expect,
                 "checks the graph survived, not just that a wall moved")


def probe_move_opening() -> Probe:
    def ask(d):
        doors = [o for o in _openings(d) if o.kind in ("door", "front_door")]
        if not doors:
            return None
        o = doors[0]
        return (f"Move opening {o.id} to the quarter point of its wall "
                f"instead of where it is now.")

    def expect(before, after):
        bp = {o.id: round(o.position, 4) for o in _openings(before)}
        moved = [o.id for o in _openings(after)
                 if o.id in bp and round(o.position, 4) != bp[o.id]]
        return (bool(moved), f"repositioned {moved or 'nothing'}")
    return Probe("move/opening", ask, expect, "needs a door on the plan")


def probe_move_room_zone() -> Probe:
    def ask(d):
        pooja = _by_cat(d, "pooja")
        target = pooja[0] if pooja else (_by_cat(d, "kitchen") or [None])[0]
        if target is None:
            return None
        return (f"The {_label(target)} should be in the north-east zone. "
                "Record that in the brief.")

    def expect(before, after):
        return (False, "no spec op was accepted (programme layer unwired)")
    return Probe("move/zone", ask, expect, "spec-level")


# -- rename and retype ------------------------------------------------------

def probe_rename() -> Probe:
    def ask(d):
        rs = _rooms(d)
        if not rs:
            return None
        return f"Rename room {rs[0].id} to \"Guest Suite\"."

    def expect(before, after):
        return (any(r.name == "Guest Suite" for r in _rooms(after)),
                "names: " + ", ".join(sorted(
                    r.name or "(unnamed)" for r in _rooms(after))[:6]))
    return Probe("rename/room", ask, expect)


def probe_retype() -> Probe:
    def ask(d):
        rs = [r for r in _rooms(d) if r.category != "study"]
        if not rs:
            return None
        return f"Change room {rs[0].id} to a study."

    def expect(before, after):
        return (any(r.category == "study" for r in _rooms(after)),
                "categories: " + ", ".join(
                    sorted({r.category for r in _rooms(after)})))
    return Probe("retype/room", ask, expect,
                 "the category/room_class distinction is the trap here")


# -- resize ----------------------------------------------------------------

def probe_resize() -> Probe:
    def ask(d):
        beds = _by_cat(d, "master_bedroom", "bedroom")
        if not beds:
            return None
        return (f"The {_label(beds[0])} should be 150 to 190 sqft. "
                "Update the brief.")

    def expect(before, after):
        return (False, "no spec op was accepted (programme layer unwired)")
    return Probe("resize/area", ask, expect, "spec-level")


# -- query -----------------------------------------------------------------

def probe_query_area() -> Probe:
    def ask(d):
        return ("What is the total carpet area of this plan, and which is the "
                "largest room? Do not change anything.")

    def expect(before, after):
        from fpeval.document import state_hash
        same = state_hash(before) == state_hash(after)
        return (same, "document unchanged" if same
                else "MUTATED on a read-only question")
    return Probe("query/read-only", ask, expect,
                 "a question must not edit the document")


def probe_query_findings() -> Probe:
    def ask(d):
        return ("What code problems does this plan have right now, and which "
                "is the most serious? Do not change anything yet.")

    def expect(before, after):
        from fpeval.document import state_hash
        same = state_hash(before) == state_hash(after)
        return (same, "document unchanged" if same else "MUTATED on a question")
    return Probe("query/findings", ask, expect)


# -- multi-step ------------------------------------------------------------

def probe_two_step() -> Probe:
    def ask(d):
        return ("Add a window to the kitchen, then check whether that cleared "
                "the kitchen ventilation finding. Tell me both outcomes.")

    def expect(before, after):
        b = len(_openings(before))
        a = len(_openings(after))
        return (a > b, f"openings {b} -> {a}")
    return Probe("multi-step/act-then-verify", ask, expect,
                 "needs a kitchen; tests read-after-write in one turn")


def probe_ambiguous() -> Probe:
    def ask(d):
        return "Make it bigger."

    def expect(before, after):
        from fpeval.document import state_hash
        # Either it asks, or it picks and says which. Both fine; silently
        # guessing and changing several things is not.
        return (True, "judgement probe -- read the reply")
    return Probe("ambiguity/asks-or-states", ask, expect,
                 "judged by reading the reply, not by the document")


ALL_PROBES: list[Callable[[], Probe]] = [
    probe_query_area,
    probe_query_findings,
    probe_rename,
    probe_retype,
    probe_add_window,
    probe_add_door,
    probe_move_opening,
    probe_move_wall,
    probe_remove_opening,
    probe_remove_wall,
    probe_add_room_to_brief,
    probe_move_room_zone,
    probe_resize,
    probe_two_step,
    probe_ambiguous,
]


# --------------------------------------------------------------------------
# running
# --------------------------------------------------------------------------

@dataclass
class ProbeRun:
    example_id: str
    probe: str
    outcome: str = "not_run"     # pass | fail | refused | skipped | error
    ask: str = ""
    reply: str = ""
    detail: str = ""
    applied: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    steps: int = 0
    seconds: float = 0.0
    thinking: str = ""

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in
                ("example_id", "probe", "outcome", "ask", "reply", "detail",
                 "applied", "refused", "steps", "seconds")}


def generate_plan(example, time_limit_s: float = 12.0) -> tuple[Optional[Plan], str]:
    """Track A: ground truth straight into the solver. No LLM, deterministic."""
    try:
        res = score_run(example, track="A", time_limit_s=time_limit_s)
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"
    if res.plan is None:
        return None, f"{res.status}: {res.error or 'no plan'}"
    return res.plan, res.status


def run_probe(doc: Document, transcript: list[dict], probe: Probe,
              example_id: str) -> ProbeRun:
    import copy
    out = ProbeRun(example_id=example_id, probe=probe.name)
    ask = probe.ask(doc.design)
    if ask is None:
        out.outcome = "skipped"
        out.detail = probe.note or "probe subject absent from this plan"
        return out
    out.ask = ask

    before = copy.deepcopy(doc.design)
    t0 = time.time()
    thinking: list[str] = []
    try:
        for ev in stream_turn(doc, transcript, ask, last_seen_seq=doc.seq,
                              findings_fn=lambda: _findings(doc)):
            kind = ev.get("type")
            if kind == "text":
                out.reply += ev["delta"]
            elif kind == "thinking":
                thinking.append(ev["delta"])
            elif kind == "change":
                out.applied.append(ev["event"]["summary"])
            elif kind == "rejected":
                out.refused.append(f"{ev['op']}: {ev['reason']}")
            elif kind == "done":
                out.steps = ev.get("steps", 0)
            elif kind == "error":
                out.outcome = "error"
                out.detail = ev["message"]
                out.seconds = round(time.time() - t0, 1)
                return out
    except Exception:
        out.outcome = "error"
        out.detail = traceback.format_exc(limit=3)
        out.seconds = round(time.time() - t0, 1)
        return out

    out.seconds = round(time.time() - t0, 1)
    out.thinking = "".join(thinking)
    ok, detail = probe.expect(before, doc.design)
    out.detail = detail
    if ok:
        out.outcome = "pass"
    elif out.refused or _sounds_like_a_refusal(out.reply):
        out.outcome = "refused"
    else:
        out.outcome = "fail"
    return out


REFUSAL_HINTS = (
    "cannot", "can't", "can not", "no command", "not able", "unable",
    "there is no", "not supported", "which ", "would you like", "shall i",
    "do you want", "tell me which", "should i",
)


def _sounds_like_a_refusal(reply: str) -> bool:
    """Distinguish "I declined / I asked" from "I claimed to do it and did not".

    Crude on purpose: the harness marks these for a human to read rather than
    scoring them. Calling a stated limitation a bug would bury the real
    failures, which are the replies that assert an effect the document does not
    show.
    """
    low = reply.lower()[:600]
    return any(h in low for h in REFUSAL_HINTS)


def _findings(doc: Document) -> list[Any]:
    plan = doc.design.active
    if plan is None or not plan.walls:
        return []
    try:
        return list(validate_plan(plan, brief=None, profile=BENGALURU))
    except Exception:
        return []


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--examples", default="base-01,vastu-01,wet-01",
                    help="comma-separated suite example ids")
    ap.add_argument("--probes", default="",
                    help="comma-separated probe names; default is all")
    ap.add_argument("--out", default="out/probe")
    ap.add_argument("--time-limit", type=float, default=12.0)
    ap.add_argument("--fresh-conversation", action="store_true",
                    help="a new transcript per probe, isolating them from "
                         "each other at the cost of losing cache hits")
    ap.add_argument("--fresh-document", action="store_true",
                    help="regenerate the plan before every probe. Without "
                         "this, one destructive probe poisons every later one "
                         "-- which is realistic but makes attribution hard: "
                         "the first run had 8 refusals, most of them the "
                         "agent correctly declining to work on a document "
                         "that an earlier wall move had already wrecked")
    args = ap.parse_args(argv)

    if not (os.environ.get("ANTHROPIC_API_KEY")
            or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        print("No Anthropic credential set.", file=sys.stderr)
        return 2

    wanted = [x.strip() for x in args.examples.split(",") if x.strip()]
    exs = {e.id: e for e in load_suite()}
    missing = [x for x in wanted if x not in exs]
    if missing:
        print(f"unknown example(s): {missing}", file=sys.stderr)
        return 2

    probes = [f() for f in ALL_PROBES]
    if args.probes:
        keep = {x.strip() for x in args.probes.split(",")}
        probes = [p for p in probes if p.name in keep]

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    runs: list[ProbeRun] = []

    for eid in wanted:
        example = exs[eid]
        plan, status = generate_plan(example, args.time_limit)
        print(f"\n=== {eid}  ({status}) "
              f"{'-' * max(0, 50 - len(eid) - len(status))}")
        if plan is None:
            print(f"    could not generate a plan: {status}")
            runs.append(ProbeRun(example_id=eid, probe="(generation)",
                                 outcome="error", detail=status))
            continue
        doc = Document.from_plan(plan, name=eid)
        print(f"    {len(doc.design.active.rooms)} rooms, "
              f"{len(doc.design.active.walls)} walls, "
              f"{len(doc.design.active.openings)} openings, "
              f"{len(_findings(doc))} findings")

        transcript: list[dict] = []
        for probe in probes:
            if args.fresh_conversation:
                transcript = []
            if args.fresh_document:
                fresh, _ = generate_plan(example, args.time_limit)
                if fresh is not None:
                    doc = Document.from_plan(fresh, name=eid)
            r = run_probe(doc, transcript, probe, eid)
            runs.append(r)
            mark = {"pass": "ok  ", "fail": "FAIL", "refused": "refd",
                    "skipped": "skip", "error": "ERR "}.get(r.outcome, "?   ")
            print(f"    {mark} {probe.name:<28} {r.detail[:56]}")
            if r.refused:
                for x in r.refused[:2]:
                    print(f"         refused: {x[:90]}")
            if r.outcome == "fail":
                print(f"         said: {r.reply[:160]}")

    # ---- report ----------------------------------------------------------
    by_outcome: dict[str, int] = {}
    for r in runs:
        by_outcome[r.outcome] = by_outcome.get(r.outcome, 0) + 1
    print("\n" + "=" * 60)
    print("  ".join(f"{k}={v}" for k, v in sorted(by_outcome.items())))

    (outdir / "runs.json").write_text(
        json.dumps([r.to_dict() for r in runs], indent=1))
    print(f"wrote {outdir / 'runs.json'}")

    fails = [r for r in runs if r.outcome in ("fail", "error")]
    if fails:
        print(f"\n{len(fails)} probe(s) failed or errored:")
        for r in fails:
            print(f"  {r.example_id} {r.probe}: {r.detail[:120]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
