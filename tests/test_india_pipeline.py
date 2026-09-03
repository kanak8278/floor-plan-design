"""End-to-end: classify -> extract -> dual-unit verify -> plausibility.

Includes negative controls: deliberately corrupted schedules that MUST be rejected.
A checker that only ever says yes is worthless.
"""
import copy, glob, json, os, sys
from anthropic import Anthropic
from fpeval.imgclass import classify
from fpeval.imgcorpus import verify, parse_mm
from fpeval.plausible import check, canonical

# The extraction contract lives in the library, not in a script.
from fpeval.extract import TOOL as SCHEMA, PROMPT, MODEL
import base64, mimetypes

client = Anthropic()

def extract(path):
    from fpeval.imgio import encode_for_api
    d, mt = encode_for_api(path)
    r = client.messages.create(model=MODEL, max_tokens=3000, tools=[SCHEMA],
        tool_choice={"type": "tool", "name": "plan_schedule"},
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": mt, "data": d}},
            {"type": "text", "text": PROMPT}]}])
    return next(b.input for b in r.content if b.type == "tool_use")

def to_rooms(ex):
    return [{"name": r.get("name"), "dim_mm": parse_mm(r.get("dim_mm"))}
            for r in ex.get("rooms", [])]

def plot_m2(ex):
    p = ex.get("plot") or {}
    if p.get("width_ft") and p.get("depth_ft"):
        return p["width_ft"] * 0.3048 * p["depth_ft"] * 0.3048
    return None

print("=" * 78)
accepted = []
for f in sorted(glob.glob("corpus/india/raw/*")):
    base = os.path.basename(f)
    c = classify(f, client=client)
    if not c.usable:
        print(f"{base:<32} GATE REJECT   kind={c.kind}")
        continue
    ex = extract(f)
    v = verify(ex)
    pl = check(to_rooms(ex), plot_area_m2=plot_m2(ex))
    print(f"{base:<32} GATE PASS     kind={c.kind}")
    print(f"{'':32} dual-unit : accepted={v.accepted} {v.stats}")
    for rs in v.reasons: print(f"{'':32}   - {rs}")
    print(f"{'':32} plausible : ok={pl.ok} areas={pl.areas}")
    print(f"{'':32}   counts={ {k: n for k, n in sorted(pl.counts.items())} }")
    for i in pl.issues: print(f"{'':32}   [{i.severity}] {i.check}: {i.detail}")
    if pl.ok and v.accepted: accepted.append(base)
    json.dump({"classification": c.as_dict(), "extraction": ex,
               "dual_unit_reasons": v.reasons, "dual_unit_stats": v.stats,
               "plausibility": [i.__dict__ for i in pl.issues], "areas": pl.areas},
              open(f"out/india-{base}.json", "w"), indent=1)

print("=" * 78)
print("NEGATIVE CONTROLS (must be rejected)\n")
GOOD = [{"name": "BEDROOM_1", "dim_mm": (4040, 5260)}, {"name": "BEDROOM_2", "dim_mm": (3300, 3600)},
        {"name": "LIVING", "dim_mm": (3950, 4500)}, {"name": "KITCHEN", "dim_mm": (3650, 2950)},
        {"name": "TOILET", "dim_mm": (1600, 2800)}, {"name": "TOILET", "dim_mm": (1150, 1600)},
        {"name": "UTILITY", "dim_mm": (2650, 1750)}, {"name": "SITOUT", "dim_mm": (3950, 1400)}]
PLOT = 30 * 0.3048 * 40 * 0.3048

base_rep = check(copy.deepcopy(GOOD), plot_area_m2=PLOT)
print(f"clean schedule            -> ok={base_rep.ok}  (expect True)  coverage={base_rep.areas['coverage']}")
for i in base_rep.issues: print(f"    [{i.severity}] {i.check}: {i.detail}")

CASES = {
    "toilet inflated 10x":      lambda r: r.__setitem__(4, {"name": "TOILET", "dim_mm": (16000, 2800)}) or r,
    "bedroom as corridor":      lambda r: r.__setitem__(0, {"name": "BEDROOM_1", "dim_mm": (1200, 8000)}) or r,
    "five kitchens":            lambda r: r + [{"name": f"KITCHEN {i}", "dim_mm": (3000, 2500)} for i in range(4)],
    "digit dropped (bedroom)":  lambda r: r.__setitem__(0, {"name": "BEDROOM_1", "dim_mm": (404, 5260)}) or r,
    "no kitchen, no bedroom":   lambda r: [x for x in r if canonical(x["name"]) not in ("kitchen", "bedroom")],
    "areas exceed plot":        lambda r: [{**x, "dim_mm": (x["dim_mm"][0]*3, x["dim_mm"][1]*3)} for x in r],
    "one room dominates":       lambda r: r + [{"name": "HALL", "dim_mm": (9000, 9000)}],
}
for label, fn in CASES.items():
    rep = check(fn(copy.deepcopy(GOOD)), plot_area_m2=PLOT)
    hits = ", ".join(sorted({i.check for i in rep.errors})) or "(none)"
    print(f"{label:<26}-> ok={str(rep.ok):<6} caught: {hits}")

print(f"\naccepted into corpus: {accepted}")
