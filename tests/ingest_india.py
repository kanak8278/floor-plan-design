"""Ingest the Indian plan corpus: classify -> extract -> 3 independent checks.

Prints a per-image verdict plus an aggregate so we can see the real yield.
"""
import glob, json, os, sys, traceback
sys.path.insert(0, "src")
from anthropic import Anthropic
from fpeval.imgclass import classify
from fpeval.extract import extract
from fpeval.imgcorpus import (check_dual_unit, parse_mm, parse_any,
                              parse_width_mm, verification_tier, MM_PER_FT)
from fpeval.plausible import check, check_area_closure, canonical

client = Anthropic()
ONLY = sys.argv[1] if len(sys.argv) > 1 else None

def norm_rooms(ex):
    """extraction rooms -> the shape the checkers expect."""
    out = []
    for r in ex.get("rooms", []):
        out.append({"name": r.get("name"),
                    "dim_mm": parse_any(r.get("dim_primary")) or parse_any(r.get("dim_secondary")),
                    "dim_primary": r.get("dim_primary"), "dim_secondary": r.get("dim_secondary"),
                    "width_mm": parse_width_mm(r.get("width_only"))})
    return out

def plot_m2(ex):
    p = ex.get("plot") or {}
    if p.get("width_ft") and p.get("depth_ft"):
        return p["width_ft"] * 0.3048 * p["depth_ft"] * 0.3048
    return None

files = sorted(glob.glob("corpus/india/raw/*"))
if ONLY: files = [f for f in files if ONLY in f]
agg = {"n": 0, "gate_pass": 0, "accepted": 0, "dual_rooms": 0, "dual_ok": 0,
       "tok_in": 0, "tok_out": 0}
tiers = {}
results = []

for f in files:
    base = os.path.basename(f); agg["n"] += 1
    try:
        c = classify(f, client=client)
    except Exception as e:
        print(f"{base[:52]:<54} CLASSIFY ERROR {type(e).__name__}: {str(e)[:60]}"); continue
    if not c.usable:
        why = "; ".join(c.raw.get("blocking_problems") or []) or "affordances missing"
        print(f"{base[:52]:<54} REJECT  {c.kind:<26} {why[:52]}")
        results.append({"file": base, "stage": "gate", "kind": c.kind, "accepted": False}); continue
    agg["gate_pass"] += 1
    try:
        ex = extract(f, client=client)
    except Exception as e:
        print(f"{base[:52]:<54} EXTRACT ERROR {type(e).__name__}: {str(e)[:60]}"); continue
    agg["tok_in"] += ex["_usage"]["in"]; agg["tok_out"] += ex["_usage"]["out"]

    rooms = norm_rooms(ex)
    dual = check_dual_unit([{"name": r["name"], "dim_mm": r["dim_primary"],
                             "dim_ft": r["dim_secondary"]} for r in rooms])
    checked = [d for d in dual if d.status in ("ok", "mismatch")]
    bad = [d for d in checked if d.status == "mismatch"]
    agg["dual_rooms"] += len(checked); agg["dual_ok"] += len(checked) - len(bad)

    pl = check(rooms, plot_area_m2=plot_m2(ex))
    ac = check_area_closure(rooms, ex.get("areas") or {})
    errors = pl.errors + [i for i in ac if i.severity == "error"]
    tier = verification_tier(rooms, ex.get("areas"))
    # Tier A/A-: cross-check per room. Tier B: only the area total can vouch for it,
    # so require the closure check to have actually run and passed.
    if tier in ("A", "A-"):
        gate = len(checked) >= 4 and len(bad) / max(len(checked), 1) <= 0.25
    elif tier == "B":
        gate = not any(i.check == "carpet_area_mismatch" for i in ac)
    else:
        gate = False
    ok = gate and not errors
    if ok: agg["accepted"] += 1
    tiers[tier] = tiers.get(tier, 0) + 1

    a = ex.get("areas") or {}
    print(f"{base[:52]:<54} {'ACCEPT' if ok else 'REJECT':<7} {ex.get('unit_label') or '-':<18} "
          f"[{tier}] rooms={len(rooms):<3} dual={len(checked)-len(bad)}/{len(checked)} "
          f"carpet={a.get('carpet_sqft') or '-'} enc={pl.areas['built_up_m2']}m²")
    for d in bad:
        print(f"{'':54}  mm/ft mismatch {d.name}: {d.detail}")
    for i in errors + [i for i in ac if i.severity == "warn"]:
        print(f"{'':54}  [{i.severity}] {i.check}: {i.detail[:96]}")
    results.append({"file": base, "accepted": ok, "tier": tier, "unit": ex.get("unit_label"),
                    "kind": c.kind, "n_rooms": len(rooms),
                    "dual_checked": len(checked), "dual_bad": len(bad),
                    "areas": ex.get("areas"), "counts": pl.counts,
                    "notes": ex.get("notes"), "extraction": ex})

json.dump(results, open("out/india_ingest.json", "w"), indent=1, default=str)
print("\n" + "=" * 96)
print(f"images                 : {agg['n']}")
print(f"passed classifier gate : {agg['gate_pass']}")
print(f"fully accepted         : {agg['accepted']}")
print(f"dual-unit room checks  : {agg['dual_ok']}/{agg['dual_rooms']} agree"
      + (f" ({100*agg['dual_ok']/agg['dual_rooms']:.1f}%)" if agg['dual_rooms'] else ""))
print(f"verification tiers     : {dict(sorted(tiers.items()))}")
print(f"tokens                 : in={agg['tok_in']} out={agg['tok_out']}")
