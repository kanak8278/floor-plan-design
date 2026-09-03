"""Run the ResPlan -> IR -> Project -> IR pipeline over the corpus and report."""
from __future__ import annotations
import pickle, sys, json, time
import numpy as np
sys.path.insert(0, "src")
from fpeval.resplan import convert
from fpeval.project import to_project, from_project
from fpeval.metrics import ir_identity, face_recovery, source_fidelity

N = int(sys.argv[1]) if len(sys.argv) > 1 else 500
PKL = sys.argv[2] if len(sys.argv) > 2 else "/tmp/resplan/data/ResPlan.pkl"

plans = pickle.load(open(PKL, "rb"))
print(f"corpus: {len(plans)} plans; testing {N}\n")

ok = 0; fails = {}
ident = {"walls": 0, "openings": 0, "rooms": 0, "plot": 0}
faces = {"exact": 0, "within1": 0, "iou": []}
fid = {"iou": [], "area_err": [], "room_count_eq": 0}
host = {"total": 0, "hosted": 0}
counts = {"walls": [], "openings": [], "rooms": []}
t0 = time.time()

for i, raw in enumerate(plans[:N]):
    try:
        ir = convert(raw)
        proj = to_project(ir)
        json.dumps(proj)                       # must be JSON-serialisable
        ir2 = from_project(proj)
    except Exception as e:
        fails[type(e).__name__] = fails.get(type(e).__name__, 0) + 1
        continue
    ok += 1
    idr = ir_identity(ir, ir2)
    ident["walls"] += idr["walls_equal"]; ident["openings"] += idr["openings_equal"]
    ident["rooms"] += idr["rooms_equal"];  ident["plot"] += idr["plot_equal"]
    counts["walls"].append(len(ir.walls)); counts["openings"].append(len(ir.openings))
    counts["rooms"].append(len(ir.rooms))
    fr = face_recovery(ir)
    if fr.get("ok"):
        faces["exact"] += fr["face_count_exact"]; faces["within1"] += fr["face_count_within_1"]
        faces["iou"].append(fr["area_iou"])
    sf = source_fidelity(raw, ir)
    if sf.get("ok"):
        fid["iou"].append(sf["area_iou"]); fid["area_err"].append(sf["total_area_err"])
        fid["room_count_eq"] += sf["room_count_equal"]
    st = ir.provenance["opening_stats"]
    host["total"] += st["total"]; host["hosted"] += st["hosted"]

el = time.time() - t0
def pct(n, d): return f"{100*n/max(d,1):.1f}%"
def q(a, p): return np.percentile(a, p) if len(a) else float("nan")

print(f"=== conversion ({ok}/{N} succeeded, {el:.1f}s, {1000*el/max(N,1):.1f} ms/plan) ===")
if fails: print(f"  failures: {fails}")
print(f"\n=== IR -> Project -> IR identity ===")
for k in ("walls", "openings", "rooms", "plot"):
    print(f"  {k:9s} exactly equal: {ident[k]}/{ok}  {pct(ident[k], ok)}")
print(f"\n=== face recovery from wall graph (== OpenPlan3D detectRooms) ===")
print(f"  face count exact      : {pct(faces['exact'], ok)}")
print(f"  face count within +/-1: {pct(faces['within1'], ok)}")
print(f"  area IoU: p10={q(faces['iou'],10):.4f} median={q(faces['iou'],50):.4f} p90={q(faces['iou'],90):.4f}")
print(f"  IoU > 0.99: {pct(sum(1 for x in faces['iou'] if x>0.99), len(faces['iou']))}")
print(f"\n=== fidelity vs original ResPlan geometry ===")
print(f"  room count equal: {pct(fid['room_count_eq'], ok)}")
print(f"  area IoU: p10={q(fid['iou'],10):.4f} median={q(fid['iou'],50):.4f}")
print(f"  total area error: median={100*q(fid['area_err'],50):.3f}%  p90={100*q(fid['area_err'],90):.3f}%")
print(f"\n=== opening hosting ===")
print(f"  {host['hosted']}/{host['total']} openings hosted on a wall  {pct(host['hosted'], host['total'])}")
print(f"\n=== element counts per plan (median / p90) ===")
for k in ("walls", "openings", "rooms"):
    print(f"  {k:9s} {q(counts[k],50):.0f} / {q(counts[k],90):.0f}")
